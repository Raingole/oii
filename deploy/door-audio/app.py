import hmac
import os
import secrets
import sqlite3
import subprocess
import tempfile
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from flask import Flask, Response, abort, redirect, render_template, request, send_file, url_for

MAX_UPLOAD = 16 * 1024 * 1024
SAMPLE_RATE = 32000
MAX_PCM = 30 * SAMPLE_RATE * 2
FORMATS = {".wav": "wav", ".mp3": "mp3", ".m4a": "mov", ".flac": "flac", ".ogg": "ogg"}


def create_app():
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=MAX_UPLOAD + 65536, MAX_FORM_PARTS=8)
    username = os.environ["ADMIN_USERNAME"]
    password = os.environ["ADMIN_PASSWORD"]
    token = os.environ["DEVICE_TOKEN"]
    if not username or not token or len(password) < 12 or password == token:
        raise ValueError("Set ADMIN_USERNAME, a separate ADMIN_PASSWORD (12+ characters), and DEVICE_TOKEN")
    csrf_token = secrets.token_hex(32)
    ffmpeg = os.getenv("FFMPEG", "ffmpeg")
    data = Path(os.getenv("DOOR_AUDIO_DATA", "data")).resolve()
    data.mkdir(parents=True, exist_ok=True)

    def connect():
        db = sqlite3.connect(data / "audio.sqlite3", timeout=10)
        db.row_factory = sqlite3.Row
        return db

    with closing(connect()) as db, db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS audio (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, size INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY CHECK (id = 1), selected TEXT
            );
            INSERT OR IGNORE INTO settings (id, selected) VALUES (1, NULL);
        """)

    def equal(actual, expected):
        return hmac.compare_digest(actual.encode("utf-8"), expected.encode("utf-8"))

    @app.after_request
    def response_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
        return response

    @app.before_request
    def authorize():
        if request.path == "/api/device/door-audio":
            if not equal(request.headers.get("Authorization", ""), "Bearer " + token):
                return Response(status=401, headers={"WWW-Authenticate": "Bearer"})
            return
        auth = request.authorization
        if not (auth and auth.type == "basic" and
                equal(auth.username or "", username) and equal(auth.password or "", password)):
            return Response(status=401, headers={"WWW-Authenticate": 'Basic realm="Door audio", charset="UTF-8"'})
        if request.method == "POST" and not equal(request.form.get("csrf", ""), csrf_token):
            abort(403)

    def admin_page(error=None, status=200):
        with closing(connect()) as db:
            tracks = db.execute("SELECT id, name, size FROM audio ORDER BY rowid DESC").fetchall()
            selected = db.execute("SELECT selected FROM settings WHERE id = 1").fetchone()[0]
        return render_template("admin.html", tracks=tracks, selected=selected,
                               csrf=csrf_token, error=error), status

    @app.errorhandler(413)
    def too_large(_error):
        return admin_page("文件大小不能超过 16 MiB。", 413)

    @app.get("/")
    def index():
        return admin_page()

    @app.post("/upload")
    def upload():
        source = request.files.get("audio")
        if not source or not source.filename:
            return admin_page("请选择音频文件。", 400)
        name = source.filename.replace(chr(92), "/").rsplit("/", 1)[-1]
        audio_format = FORMATS.get(Path(name).suffix.lower())
        if not audio_format:
            return admin_page("支持 WAV、MP3、M4A、FLAC 和 OGG。", 400)

        source.stream.seek(0, os.SEEK_END)
        if source.stream.tell() > MAX_UPLOAD:
            return too_large(None)
        source.stream.seek(0)

        with tempfile.TemporaryDirectory(dir=data) as temp:
            input_path = Path(temp) / "input"
            output_path = Path(temp) / "output.pcm"
            source.save(input_path)
            try:
                # Decode just past the limit to detect overlong input without processing it all.
                subprocess.run([
                    ffmpeg, "-nostdin", "-v", "error", "-xerror", "-protocol_whitelist", "file",
                    "-f", audio_format, "-i", str(input_path), "-map", "0:a:0",
                    "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le",
                    "-t", "30.1", "-f", "s16le", str(output_path),
                ], check=True, timeout=45, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except FileNotFoundError:
                app.logger.exception("FFmpeg executable missing")
                return admin_page("服务器未配置 FFmpeg。", 503)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                return admin_page("无法转换此音频，请检查文件。", 422)
            size = output_path.stat().st_size
            if size > MAX_PCM:
                return admin_page("音频时长不能超过 30 秒。", 422)
            if size == 0:
                return admin_page("音频没有可播放的内容。", 422)

            audio_id = uuid4().hex
            target = data / (audio_id + ".pcm")
            output_path.replace(target)
            with closing(connect()) as db, db:
                db.execute("INSERT INTO audio VALUES (?, ?, ?)", (audio_id, name, size))
        return redirect(url_for("index"), code=303)

    @app.post("/select")
    def select():
        audio_id = request.form.get("audio_id") or None
        with closing(connect()) as db, db:
            if audio_id and not db.execute("SELECT 1 FROM audio WHERE id = ?", (audio_id,)).fetchone():
                abort(404)
            db.execute("UPDATE settings SET selected = ? WHERE id = 1", (audio_id,))
        return redirect(url_for("index"), code=303)

    @app.get("/api/device/door-audio")
    def door_audio():
        with closing(connect()) as db:
            selected = db.execute("SELECT selected FROM settings WHERE id = 1").fetchone()[0]
        if selected is None:
            return Response(status=204)
        response = send_file(data / (selected + ".pcm"), mimetype="application/octet-stream",
                             conditional=False, etag=False, max_age=0)
        response.headers.update({
            "X-Audio-Format": "pcm_s16le",
            "X-Audio-Sample-Rate": str(SAMPLE_RATE),
            "X-Audio-Channels": "1",
            "X-Accel-Buffering": "no",
        })
        return response

    return app
