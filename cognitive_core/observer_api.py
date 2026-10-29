"""Authenticated read-only observer routes, shared by both HTTP entrypoints."""
import secrets
from pathlib import Path
from aiohttp import web
from config.observer_access import observer_token


ROOT = Path(__file__).resolve().parents[1]


def register_observer(app, core_provider, config, *, root=None):
    key = observer_token(config, root or ROOT)

    def asset(name):
        async def handler(_request):
            response = web.FileResponse(ROOT / "ui" / name)
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            return response
        return handler

    async def activity(request):
        expected = "Bearer " + key
        if not secrets.compare_digest(request.headers.get("Authorization", "").encode("utf-8"), expected.encode("utf-8")):
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

    app.router.add_get("/memory", asset("memory.html"))
    app.router.add_get("/memory/", asset("memory.html"))
    app.router.add_get("/memory.html", asset("memory.html"))
    app.router.add_get("/memory.css", asset("memory.css"))
    app.router.add_get("/memory.js", asset("memory.js"))
    app.router.add_get("/api/memory/activity", activity)
