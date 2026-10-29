"""Managed stdio client for the pinned upstream @xzxzzx/bilibili-mcp."""
import asyncio
import json
import os
from pathlib import Path
import re


READ_TOOLS = {'check_bilibili_credentials', 'search_bilibili_videos', 'get_video_transcript', 'get_video_metadata'}


def video_id(value):
    value = str(value).strip()
    if re.fullmatch(r'BV[0-9A-Za-z]{10}', value):
        return value
    match = re.fullmatch(r'https://(?:www\.)?bilibili\.com/video/(BV[0-9A-Za-z]{10})/?(?:\?[^#\s]*)?', value)
    if not match:
        raise ValueError('请提供 BV 号或 https://www.bilibili.com/video/BV... 链接')
    return match[1]


class BilibiliClient:
    def __init__(self, root, config=None):
        self.root = Path(root)
        self.config = config or {}
        self.entry = self.root / 'deploy/bilibili/node_modules/@xzxzzx/bilibili-mcp/dist/cli.js'
        self.process = None
        self.lock = asyncio.Lock()
        self.sequence = 0

    async def _request(self, method, params):
        self.sequence += 1
        ident = self.sequence
        payload = {'jsonrpc':'2.0','id':ident,'method':method,'params':params}
        self.process.stdin.write((json.dumps(payload, ensure_ascii=False)+'\n').encode())
        await self.process.stdin.drain()
        for _ in range(64):
            line = await self.process.stdout.readline()
            if not line:
                raise RuntimeError('bilibili_process_exited')
            reply = json.loads(line)
            if reply.get('id') != ident:
                continue
            if 'error' in reply:
                raise RuntimeError('bilibili_protocol_error')
            return reply.get('result', {})
        raise RuntimeError('bilibili_protocol_limit')

    async def _start(self):
        if self.process and self.process.returncode is None:
            return
        if not self.entry.is_file():
            raise RuntimeError('bilibili_runtime_missing')
        node = os.environ.get('NODE_BIN') or self.config.get('node', 'node')
        self.process = await asyncio.create_subprocess_exec(node, str(self.entry), cwd=self.root,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=4 * 1024 * 1024)
        await self._request('initialize', {'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'oii-learning','version':'1.0'}})
        self.process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        await self.process.stdin.drain()

    async def call(self, tool, arguments=None):
        if tool not in READ_TOOLS:
            raise ValueError('only_read_only_bilibili_tools_allowed')
        async with self.lock:
            try:
                async def invoke():
                    await self._start()
                    result = await self._request('tools/call', {'name':tool,'arguments':arguments or {}})
                    data = result.get('structuredContent')
                    if not isinstance(data, dict):
                        content = '\n'.join(item.get('text','') for item in result.get('content',[]) if item.get('type') == 'text')
                        try:
                            data = json.loads(content)
                        except (ValueError, TypeError):
                            raise RuntimeError('bilibili_response_not_structured') from None
                    if not isinstance(data,dict):
                        raise RuntimeError('bilibili_response_not_structured')
                    if result.get('isError') or data.get('error') or data.get('success') is False:
                        error = data.get('error', {})
                        code = error.get('code') if isinstance(error, dict) else data.get('code')
                        known = {'VALIDATION_ERROR','COOKIE_EXPIRED','SUBTITLE_UNAVAILABLE','API_RATE_LIMITED','BILIBILI_API_ERROR','UNKNOWN_ERROR','NETWORK_ERROR','AUTH_REQUIRED','CREDENTIALS_REQUIRED'}
                        raise RuntimeError('bilibili_' + (code if isinstance(code,str) and code in known else 'upstream_error'))
                    return data
                return await asyncio.wait_for(invoke(), timeout=float(self.config.get('timeout_seconds',20)))
            except BaseException:
                await self.close()
                raise

    async def close(self):
        process, self.process = self.process, None
        if process and process.returncode is None:
            try: process.terminate()
            except ProcessLookupError: pass
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except asyncio.TimeoutError:
                try: process.kill()
                except ProcessLookupError: pass
                await process.wait()
