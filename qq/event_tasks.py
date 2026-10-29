"""Keep OneBot echo processing live while messages wait for model/tool I/O."""
import asyncio
import json


class EventTasks:
    def __init__(self, on_error=None, limit=32):
        self.tasks=set()
        self.on_error=on_error
        self.limit=limit

    async def feed(self, raw, handler):
        try:
            value=json.loads(raw)
        except (ValueError,TypeError):
            return
        if not isinstance(value,dict):
            return
        if value.get('echo') is not None:
            await handler(raw)
            return
        if len(self.tasks)>=self.limit:
            if self.on_error: self.on_error('OneBot event queue full')
            return
        async def process():
            try:
                await handler(raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self.on_error: self.on_error('OneBot event failed: '+type(exc).__name__)
        task=asyncio.create_task(process())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def close(self):
        tasks=list(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks,return_exceptions=True)
        self.tasks.clear()
