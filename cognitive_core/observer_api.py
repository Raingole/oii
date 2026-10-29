"""Authenticated read-only observer routes, shared by both HTTP entrypoints."""
import asyncio
import secrets
from pathlib import Path
import re
from aiohttp import web
from config.observer_access import observer_token


ROOT = Path(__file__).resolve().parents[1]


def register_observer(app, core_provider, config, *, root=None):
    key = observer_token(config, root or ROOT)

    def valid_key(request):
        expected = "Bearer " + key
        return secrets.compare_digest(request.headers.get("Authorization", "").encode("utf-8"), expected.encode("utf-8"))

    def knowledge_scope(core):
        owner=str(((config.get('qq') or {}).get('owner_qq') or '')).strip()
        if re.fullmatch(r'\d{5,20}',owner):
            return 'qq:private:'+owner
        return ''

    def asset(name):
        async def handler(_request):
            response = web.FileResponse(ROOT / "ui" / name)
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            return response
        return handler

    async def activity(request):
        if not valid_key(request):
            return web.json_response({"ok": False, "error": "observer_key_required"}, status=401, headers={"Cache-Control": "no-store"})
        core = core_provider()
        if core is None:
            return web.json_response({"ok": False, "error": "cognitive_runtime_disabled"}, status=503)
        session = request.query.get("session_id", "")
        event = request.query.get("event_id", "")
        if len(session) > 500 or len(event) > 500:
            return web.json_response({"ok": False, "error": "invalid_selection"}, status=400)
        result = core.store.memory_activity(session, event)
        if event and result["selected"] is None:
            return web.json_response({"ok": False, "error": "event_not_found"}, status=404)
        manager = getattr(core.memory, "manager", None)
        result.update({"ok": True, "backend": getattr(manager, "backend_name", "adapter"),
                       "memory_degraded": bool(getattr(core.memory, "degraded", False))})
        return web.json_response(result, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    async def knowledge_library(request):
        if not valid_key(request):
            return web.json_response({"ok":False,"error":"observer_key_required"},status=401,headers={"Cache-Control":"no-store"})
        core=core_provider()
        learning=getattr(core,'learning',None) if core else None
        if learning is None:
            return web.json_response({"ok":False,"error":"knowledge_store_unavailable"},status=503,headers={"Cache-Control":"no-store"})
        scope=knowledge_scope(core)
        if not scope:
            return web.json_response({"ok":True,"scope":"","counts":{"sources":0,"meme_entries":0,"video_notes":0,"images":0},"sources":[],"images":[]},headers={"Cache-Control":"no-store"})
        result=await asyncio.to_thread(learning.store.library,scope,200)
        last=learning.store.state('last_run')
        schedule=learning.store.state('meme_schedule')
        result['learning_status']={
            'last_run': last if last.get('scope')==scope else {},
            'meme_schedule': {key:schedule.get(key) for key in ('day','count','next_at') if key in schedule},
            'enabled': bool(learning.settings.get('enabled',False)),
            'meme_enabled': bool(learning.meme_settings.get('enabled',False)),
        }
        result['ok']=True
        return web.json_response(result,headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff","Referrer-Policy":"no-referrer"})

    async def knowledge_image(request):
        if not valid_key(request):
            return web.json_response({"ok":False,"error":"observer_key_required"},status=401,headers={"Cache-Control":"no-store"})
        digest=request.match_info.get('digest','')
        if not re.fullmatch(r'[0-9a-f]{64}',digest):
            raise web.HTTPNotFound()
        core=core_provider()
        learning=getattr(core,'learning',None) if core else None
        scope=knowledge_scope(core) if learning else ''
        item=await asyncio.to_thread(learning.store.image_asset,scope,digest) if scope else None
        if not item or len(item['image'])>5*1024*1024:
            raise web.HTTPNotFound()
        return web.Response(body=item['image'],content_type='image/jpeg',headers={
            "Cache-Control":"private, no-store","X-Content-Type-Options":"nosniff",
            "Referrer-Policy":"no-referrer"})

    async def dsh_requests(request):
        if not valid_key(request):
            return web.json_response({"ok":False,"error":"observer_key_required"},status=401,headers={"Cache-Control":"no-store"})
        core=core_provider()
        learning=getattr(core,'learning',None) if core else None
        dsh=getattr(learning,'dsh',None) if learning else None
        if dsh is None:
            return web.json_response({"ok":False,"error":"dsh_service_unavailable"},status=503,headers={"Cache-Control":"no-store"})
        scope=knowledge_scope(core)
        if not scope:
            return web.json_response({"ok":False,"error":"owner_scope_unavailable"},status=503,headers={"Cache-Control":"no-store"})
        try:
            limit=max(1,min(100,int(request.query.get('limit','50'))))
        except ValueError:
            return web.json_response({"ok":False,"error":"invalid_limit"},status=400)
        snapshot=await asyncio.to_thread(dsh.observer_snapshot,scope,limit)
        snapshot['ok']=True
        return web.json_response(snapshot,headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff","Referrer-Policy":"no-referrer"})

    app.router.add_get("/memory", asset("memory.html"))
    app.router.add_get("/memory/", asset("memory.html"))
    app.router.add_get("/memory.html", asset("memory.html"))
    app.router.add_get("/memory.css", asset("memory.css"))
    app.router.add_get("/memory.js", asset("memory.js"))
    app.router.add_get("/api/memory/activity", activity)
    app.router.add_get("/knowledge", asset("knowledge.html"))
    app.router.add_get("/knowledge/", asset("knowledge.html"))
    app.router.add_get("/knowledge.html", asset("knowledge.html"))
    app.router.add_get("/knowledge.css", asset("knowledge.css"))
    app.router.add_get("/knowledge.js", asset("knowledge.js"))
    app.router.add_get("/dsh", asset("dsh-monitor.html"))
    app.router.add_get("/dsh/", asset("dsh-monitor.html"))
    app.router.add_get("/dsh-monitor.html", asset("dsh-monitor.html"))
    app.router.add_get("/dsh-monitor.css", asset("dsh-monitor.css"))
    app.router.add_get("/dsh-monitor.js", asset("dsh-monitor.js"))
    app.router.add_get("/api/knowledge/library", knowledge_library)
    app.router.add_get("/api/knowledge/images/{digest}", knowledge_image)
    app.router.add_get("/api/dsh/requests", dsh_requests)
