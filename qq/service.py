"""NapCat OneBot HTTP API client."""

import asyncio
import re
from typing import Any, Optional

import aiohttp
from config.logger import setup_logging

TAG = __name__


class QQService:
    def __init__(self, config: dict):
        qq_config = config.get("qq", {})
        self.base_url = str(qq_config.get("napcat_http_url", "http://127.0.0.1:3000")).rstrip("/")
        self.token = str(qq_config.get("napcat_http_token", ""))
        self.timeout = float(qq_config.get("napcat_http_timeout", 10))
        self.max_message_length = int(qq_config.get("max_message_length", 4000))
        self.logger = setup_logging(config)
        self._session: Optional[aiohttp.ClientSession] = None

    async def start(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def send_private_message(self, user_id: str, message: str) -> bool:
        return await self._call("send_private_msg", {"user_id": str(user_id), "message": message})

    async def send_group_message(self, group_id: str, message: str) -> bool:
        return await self._call("send_group_msg", {"group_id": str(group_id), "message": message})

    async def _call(self, action: str, params: dict[str, Any]) -> bool:
        await self.start()
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        url = f"{self.base_url}/{action}"
        for attempt in range(2):
            try:
                async with self._session.post(
                    url, json=params, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                ) as response:
                    payload = await response.json(content_type=None)
                    ok = response.status < 400 and payload.get("status", "ok") == "ok" and payload.get("retcode", 0) == 0
                    if not ok:
                        self.logger.bind(tag=TAG).error(
                            f"NapCat Action failed: action={action}, status={response.status}, retcode={payload.get('retcode')}"
                        )
                    return ok
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                if attempt == 1:
                    self.logger.bind(tag=TAG).error(f"NapCat HTTP request failed: action={action}, error={exc}")
                    return False
                await asyncio.sleep(0.25)
        return False

    async def send_message(self, target: dict[str, Any], message: str) -> bool:
        clean = _clean_message(message)
        if not clean:
            return False
        chunks = [clean[i:i + self.max_message_length] for i in range(0, len(clean), self.max_message_length)]
        results = []
        for chunk in chunks:
            if target.get("message_type") == "group" and target.get("group_id"):
                results.append(await self.send_group_message(target["group_id"], chunk))
            else:
                results.append(await self.send_private_message(target["user_id"], chunk))
        return all(results)


def _clean_message(message: str) -> str:
    # Keep QQ replies plain text; strip internal ESP/TTS control markers.
    message = re.sub(r"<\/?think>|<tool_call>|</tool_call>", "", str(message or ""))
    message = re.sub(r"\x00", "", message)
    return message.strip()

