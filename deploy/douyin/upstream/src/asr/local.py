"""Local CPU Whisper provider backed by OII's existing faster-whisper runtime."""
import asyncio
import json
import os
from pathlib import Path
import subprocess

from ..config import ASR_LOCAL_MODEL, ASR_LOCAL_MODEL_DIR, ASR_LOCAL_PYTHON
from .base import ASRProvider, ASRResult


class LocalProvider(ASRProvider):
    @property
    def name(self) -> str:
        return f"Local faster-whisper ({ASR_LOCAL_MODEL})"

    def _python(self) -> Path:
        return Path(ASR_LOCAL_PYTHON).expanduser()

    def _model_dir(self) -> Path | None:
        if ASR_LOCAL_MODEL_DIR:
            path = Path(ASR_LOCAL_MODEL_DIR).expanduser()
            return path if path.is_dir() else None
        home = Path.home()
        candidates = [
            home / '.bilibili-mcp' / 'asr' / 'models' / ASR_LOCAL_MODEL,
            home / '.bilibili-mcp' / 'asr' / ASR_LOCAL_MODEL,
        ]
        cache = home / '.cache' / 'huggingface' / 'hub' / f'models--Systran--faster-whisper-{ASR_LOCAL_MODEL}' / 'snapshots'
        if cache.is_dir():
            candidates.extend(sorted((item for item in cache.iterdir() if item.is_dir()),
                                     key=lambda item: item.stat().st_mtime, reverse=True))
        return next((path for path in candidates if path.is_dir()), None)

    def is_configured(self) -> bool:
        return self._python().is_file() and self._model_dir() is not None

    def get_config_hint(self) -> str:
        return ('本地 ASR 未就绪。请先安装 OII 的 Bilibili faster-whisper 模型；若模型路径无法自动发现，'
                '设置 ASR_LOCAL_MODEL_DIR，或设置 ASR_LOCAL_PYTHON 指向该 ASR 虚拟环境的 Python。')

    async def transcribe(self, audio_path: str) -> ASRResult:
        audio = Path(audio_path)
        model_dir = self._model_dir()
        python = self._python()
        if not audio.is_file() or model_dir is None or not python.is_file():
            raise RuntimeError(self.get_config_hint())
        script = (
            'import json,sys; from faster_whisper import WhisperModel; '
            'm=WhisperModel(sys.argv[1],device="cpu",compute_type="int8",cpu_threads=2); '
            'segments,info=m.transcribe(sys.argv[2],beam_size=1); '
            'print(json.dumps({"text":"".join(s.text for s in segments),"duration":info.duration},ensure_ascii=False))'
        )
        env = {
            'PATH': os.environ.get('PATH', ''),
            'HOME': str(Path.home()),
            'HF_HUB_OFFLINE': '1',
            'TRANSFORMERS_OFFLINE': '1',
            'OMP_NUM_THREADS': '2',
            'OPENBLAS_NUM_THREADS': '2',
            'TOKENIZERS_PARALLELISM': 'false',
        }

        def run():
            return subprocess.run([str(python), '-I', '-c', script, str(model_dir), str(audio)],
                                  capture_output=True, text=True, encoding='utf-8', errors='replace',
                                  timeout=1200, env=env, check=False)

        result = await asyncio.to_thread(run)
        if result.returncode != 0:
            raise RuntimeError('local_asr_failed')
        try:
            payload = json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise RuntimeError('local_asr_invalid_output') from None
        return ASRResult(text=str(payload.get('text') or ''),
                         duration=float(payload.get('duration') or 0), provider=self.name)
