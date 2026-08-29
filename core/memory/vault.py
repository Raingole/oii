"""Small AES-256-GCM vault for secrets referenced by unified memory."""

from __future__ import annotations

import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class SecretVault:
    def __init__(self, key_path: Path):
        self.key_path = Path(key_path)
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        if self.key_path.exists():
            self.key = base64.urlsafe_b64decode(self.key_path.read_bytes())
            if len(self.key) != 32:
                raise ValueError("无效的 Secret Vault 密钥")
        else:
            self.key = os.urandom(32)
            self.key_path.write_bytes(base64.urlsafe_b64encode(self.key))
            try:
                os.chmod(self.key_path, 0o600)
            except OSError:
                pass

    def encrypt(self, value: str) -> str:
        nonce = os.urandom(12)
        ciphertext = AESGCM(self.key).encrypt(nonce, value.encode("utf-8"), None)
        return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")

    def decrypt(self, value: str) -> str:
        raw = base64.urlsafe_b64decode(value.encode("ascii"))
        return AESGCM(self.key).decrypt(raw[:12], raw[12:], None).decode("utf-8")
