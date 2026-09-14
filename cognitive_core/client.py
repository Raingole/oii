from __future__ import annotations

import asyncio
import json
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from typing import Any


class CognitiveClient:
    def __init__(self, base_url: str, timeout: float = 3.0) -> None: self.base_url, self.timeout = base_url.rstrip("/"), timeout
    async def process(self, event: dict[str, Any]) -> dict[str, Any]:
        def call() -> dict[str, Any]:
            request = Request(self.base_url + "/events", data=json.dumps(event, ensure_ascii=False).encode(), headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(request, timeout=self.timeout) as response: return json.loads(response.read().decode())
        return await asyncio.to_thread(call)
