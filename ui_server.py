from __future__ import annotations

import argparse
import urllib.error
import urllib.request
from http import HTTPStatus
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class QuietStaticHandler(SimpleHTTPRequestHandler):
    backend_port = 8003

    def log_message(self, format: str, *args: object) -> None:
        return

    def _proxy_api(self):
        if not self.path.startswith("/api/sim-qq/"):
            return False
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else None
        target = f"http://127.0.0.1:{self.backend_port}{self.path}"
        request = urllib.request.Request(target, data=body, method=self.command)
        for name in ("Content-Type", "Cookie"):
            if self.headers.get(name):
                request.add_header(name, self.headers[name])
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read()
                self.send_response(response.status)
                for name in ("Content-Type", "Set-Cookie"):
                    value = response.headers.get(name)
                    if value:
                        self.send_header(name, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        except urllib.error.HTTPError as error:
            payload = error.read()
            self.send_response(error.code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except urllib.error.URLError:
            payload = '{"ok":false,"error":"中控 HTTP 服务未连接，请确认 8003 端口已启动"}'.encode("utf-8")
            self.send_response(HTTPStatus.SERVICE_UNAVAILABLE)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        return True

    def do_GET(self):
        if not self._proxy_api():
            super().do_GET()

    def do_POST(self):
        if not self._proxy_api():
            self.send_error(HTTPStatus.NOT_FOUND)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the oii voice guide UI")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--backend-port", type=int, default=8003)
    args = parser.parse_args()

    ui_dir = Path(__file__).resolve().parent / "ui"
    QuietStaticHandler.backend_port = args.backend_port
    handler = partial(QuietStaticHandler, directory=str(ui_dir))
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"oii voice guide listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
