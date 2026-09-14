from __future__ import annotations

from aiohttp import web
from .contracts import CognitiveEvent
from .runtime import CognitiveCore
import uuid

TRACE_ID = web.AppKey("trace_id", str)


def create_app(core: CognitiveCore, dispatcher=None, approval=None) -> web.Application:
    token = str(core.config.get("api_token", ""))
    if not token and str(core.config.get("mode", core.config.get("environment", "development"))).lower() in {"production", "prod"}:
        raise RuntimeError("COGNITIVE_CORE_API_TOKEN is required in production")
    max_body = int(core.config.get("max_body_bytes", 262144))
    app = web.Application(client_max_size=max_body)

    def authorized(request: web.Request) -> bool:
        return not token or request.headers.get("Authorization", "") == f"Bearer {token}"

    def reject(request: web.Request) -> web.Response | None:
        if authorized(request): return None
        return web.json_response({"ok": False, "error": "unauthorized", "trace_id": request.get(TRACE_ID, "")}, status=401)

    async def health(_: web.Request) -> web.Response:
        memory_ok = await core.memory.health()
        deps = {"sqlite": core.store.path.exists(), "memory": memory_ok, "controller_bridge": core.config.get("controller_bridge", True), "action_outbox": core.config.get("action_outbox", True)}
        return web.json_response({"ok": all(deps.values()), "service": "cognitive-core", "dependencies": deps}, status=200 if all(deps.values()) else 503)
    async def event(request: web.Request) -> web.Response:
        denied = reject(request)
        if denied: return denied
        try:
            event = CognitiveEvent(**await request.json())
            if event.source == "system" and event.actor_id != "self": raise ValueError("invalid system actor")
            if event.source == "esp32" and not event.actor_id: raise ValueError("invalid esp32 actor")
            result = await core.process_event(event)
        except (ValueError, TypeError) as exc: return web.json_response({"ok": False, "error": str(exc)}, status=400)
        return web.json_response(result)
    async def state(request: web.Request) -> web.Response:
        denied = reject(request)
        return denied or web.json_response(core.snapshot())
    async def heartbeat(request: web.Request) -> web.Response:
        denied = reject(request)
        return denied or web.json_response(await core.run_once())
    async def create_approval(request: web.Request) -> web.Response:
        denied = reject(request)
        if denied: return denied
        if approval is None: return web.json_response({"ok": False, "error": "approval service unavailable"}, status=503)
        try:
            body = await request.json(); action = body.get("action") or body
            required = {"action_id", "event_id", "actor_id", "session_id"}
            if not required.issubset(action): raise ValueError("action_id/event_id/actor_id/session_id required")
            record = approval.create(action, action["event_id"], action["actor_id"], action["session_id"], float(body.get("ttl", 300)))
            return web.json_response({"ok": True, "approval": record}, status=201)
        except (ValueError, TypeError, KeyError) as exc: return web.json_response({"ok": False, "error": str(exc)}, status=400)
    async def get_approval(request: web.Request) -> web.Response:
        denied = reject(request)
        if denied: return denied
        record = approval.get(request.match_info["approval_id"]) if approval else None
        if not record: return web.json_response({"ok": False, "error": "approval not found"}, status=404)
        record = dict(record); record.pop("approval_token", None)
        return web.json_response({"ok": True, "approval": record})
    async def approve_approval(request: web.Request) -> web.Response:
        denied=reject(request)
        if denied:return denied
        if approval is None:return web.json_response({"ok":False,"error":"approval service unavailable"},status=503)
        try:
            body=await request.json(); token=str(body.get("approval_token") or body.get("token") or ""); approved_by=str(body.get("approved_by") or "").strip(); actor_id=body.get("actor_id")
            if not approved_by or not token:return web.json_response({"ok":False,"error":"approval_token and approved_by required"},status=400)
            precheck=approval.get(request.match_info["approval_id"])
            if dispatcher is not None and precheck:
                current=dispatcher.outbox.get(precheck["action_id"])
                if not current or dispatcher.approval._hash(current["payload"]) != precheck["payload_hash"]:
                    return web.json_response({"ok":False,"error":"action payload changed"},status=409)
            ok=approval.approve_request(request.match_info["approval_id"],token,approved_by,actor_id)
            if ok and dispatcher is not None:
                record=approval.get(request.match_info["approval_id"]); action_record=dispatcher.outbox.get(record["action_id"])
                if not action_record or dispatcher.approval._hash(action_record["payload"]) != record["payload_hash"]:
                    return web.json_response({"ok":False,"error":"action payload changed"},status=409)
                if action_record["status"] in {"cancelled","pending","retrying","failed"}: dispatcher.outbox.transition(record["action_id"],"approved")
                outcome=await dispatcher.dispatch(record["event_id"],action_record["payload"],approved=True)
                return web.json_response({"ok":outcome.success,"action_result":outcome.to_dict()},status=200 if outcome.success else 409)
            return web.json_response({"ok":ok},status=200 if ok else 403)
        except (ValueError,TypeError):return web.json_response({"ok":False,"error":"invalid approval request"},status=400)
    @web.middleware
    async def trace(request: web.Request, handler):
        request._state[TRACE_ID] = request.headers.get("X-Trace-Id") or str(uuid.uuid4())
        response = await handler(request); response.headers["X-Trace-Id"] = request._state[TRACE_ID]; return response
    app.middlewares.append(trace)
    app.router.add_get("/health", health); app.router.add_post("/events", event); app.router.add_get("/state", state); app.router.add_post("/heartbeat/run-once", heartbeat)
    app.router.add_post("/controller/approvals", create_approval); app.router.add_get("/controller/approvals/{approval_id}", get_approval)
    app.router.add_post("/controller/approvals/{approval_id}/approve", approve_approval)
    return app
