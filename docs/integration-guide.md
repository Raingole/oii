# Cognitive Core integration guide

## Main path

Ingress adapters convert QQ/NapCat, QQ Official, ESP32 ASR, SMS, MailPilot, MCP and heartbeat input into `CognitiveEvent`. `controller.event_router.EventRouter` invokes the core. Core output is an Action only; `ActionOutbox` persists it and `ActionDispatcher` executes it through controller-owned executors. Results are fed back as `controller/action_result` events.

## Lifecycle

The controller constructs WebSocket and runtime, registers QQ/ESP32/MCP/plugin/desktop executors, recovers the Outbox, then starts Heartbeat and network services. Shutdown stops accepting events, stops Heartbeat, waits for dispatch work, then closes gateways and memory.

## Reliability and safety

Action IDs and source event IDs are idempotent. Attempts and leases survive process restart. High-risk Actions require a persisted, payload-bound approval. Cognitive Core cannot access NapCat, ESP32 sockets or desktop execution directly. ESP32 legacy fallback is used unless a speak/display Action reports confirmed device output.

## Configuration

Use `data/.config.yaml` for project configuration and environment variables for overrides. Never commit credentials. `COGNITIVE_MEMORY_BACKEND=local` enables local test fallback; remote Tencent Memory uses the configured URL, API key and `x-tdai-*` scope headers.

## Verification status

Unit/mock: verified. Real NapCat, QQ Official, ESP32 hardware, Tencent Memory, network MCP and full `app.py` startup: not verified in the current interpreter because the original `numpy` dependency is missing.
