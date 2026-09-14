# Current architecture baseline

The repository is an asyncio Python controller. `app.py` is the main process entry point; `core/websocket_server.py` owns ESP32 connections and `core/http_server.py` owns HTTP/OTA integrations. `qq/gateway.py` and `qq/official_gateway.py` receive QQ traffic. The legacy `core/agent_pipeline.py` QQ fallback has been removed; Cognitive Core is authoritative.

## Actual call chains

- QQ: gateway -> `QQAgent.reply_result` -> EventRouter -> CognitiveCore -> ActionOutbox/Dispatcher -> QQ executor. No legacy AgentPipeline fallback is used.
- ESP32: ASR text -> `startToChat` -> EventRouter -> speak/display Action -> connection TTS output; fallback continues through the original turn/session/wake-word pipeline unless output is confirmed.
- Memory: CognitiveCore -> `MemoryPort`; shared legacy manager uses its configured Tencent commit hook, with local adapter available for tests/fallback.
- MCP/tools: Controller Action -> ToolCatalog -> server MCP/device MCP/plugins/desktop provider -> standard result -> action_result event.
- Heartbeat: scheduler -> Core decision -> Action Dispatcher; scheduler never executes a device directly.

## Compatibility and known limits

The new path is isolated in `cognitive_core/`, `controller/` and `contracts/`. NapCat/ESP32 protocol, session, turn and TTS code remain in `core/` and `qq/`. Real external service integration is not verified here. Full `app.py` startup is blocked by missing `numpy` in the current interpreter.发现疑似凭据，已脱敏处理。
