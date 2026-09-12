"""Deterministic desktop command detection for text channels."""

import re


_OPEN_RE = re.compile(r"^\s*(?:请)?(?:调用电脑控制功能[，,、 ]*)?打开(?:一下)?\s*(.+?)\s*[。.!！?？]*\s*$")
_IGNORE = {"桌面", "电脑", "电脑控制", "桌面控制", "应用"}


def detect_desktop_open(text: str) -> str | None:
    """Return an application name for an explicit '打开应用' request."""
    if not isinstance(text, str):
        return None
    match = _OPEN_RE.match(text)
    if not match:
        return None
    name = match.group(1).strip()
    if not name or name in _IGNORE or "网页" in name or "网址" in name:
        return None
    return name
