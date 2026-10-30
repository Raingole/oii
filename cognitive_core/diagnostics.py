"""Safe, user-shareable diagnostics for private troubleshooting replies."""
from __future__ import annotations

import re


_SECRET_ASSIGNMENT = re.compile(
    r"(?i)([\"']?(?:cookie|set-cookie|authorization|api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|secret)[\"']?"
    r"\s*[:=]\s*)[\"']?[^\"',\s;}]+[\"']?"
)
_URL = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_LONG_CREDENTIAL = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_~+/=-]{40,}(?![A-Za-z0-9])")
_PREFIXED_CREDENTIAL = re.compile(r"(?i)\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]{16,}|hf_[A-Za-z0-9]{16,})\b")


def safe_debug_detail(value: object, limit: int = 240) -> str:
    """Keep useful error codes while removing common credential-bearing values."""
    text = str(value or "")
    text = re.sub(r"[\r\n\t\x00-\x1f]+", " ", text).strip()
    text = re.sub(r"(?i)\bBearer\s+\S+", "Bearer <redacted>", text)
    text = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}<redacted>", text)

    def strip_url_query(match: re.Match[str]) -> str:
        url = match.group(0)
        url = re.sub(r"(https?://)[^/@\s]+:[^/@\s]+@", r"\1<redacted>@", url, flags=re.IGNORECASE)
        return re.split(r"[?#]", url, maxsplit=1)[0] + "?<query-redacted>"

    text = _URL.sub(strip_url_query, text)
    text = _PREFIXED_CREDENTIAL.sub("<credential-redacted>", text)
    text = _LONG_CREDENTIAL.sub("<long-token-redacted>", text)
    return text[:max(32, min(400, int(limit)))].strip()
