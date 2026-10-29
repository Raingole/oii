"""Private, persistent access key for the read-only memory observer."""
import os
from pathlib import Path
import secrets


def observer_token(config, root):
    mmtr = config.get("mmtr") or {}
    cognitive = config.get("cognitive_core") or config
    configured = os.environ.get("MMTR_WEB_TOKEN") or mmtr.get("web_token") or cognitive.get("api_token")
    if configured:
        return str(configured)
    path = Path(root) / "data/deploy/mmtr-web.token"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        value = path.read_text(encoding="utf-8").strip()
        if not value:
            raise RuntimeError("Memory observer access key file is empty")
        return value
    value = secrets.token_urlsafe(32)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(value + "\n")
    return value
