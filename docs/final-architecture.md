# Final architecture status

The controller owns ingress, Action Outbox, approvals and execution. Cognitive Core owns Self, emotion, memory retrieval, appraisal, meaning, planning, reflection, timeline, body state and heartbeat decisions. The boundary is:

`External Event -> EventRouter -> CognitiveCore -> ActionOutbox -> ActionDispatcher -> Controller executor -> action_result Event`.

Action Outbox now uses SQLite WAL, atomic claim, persisted attempts/max_attempts, retry_at, lease_until and last_error. Startup recovery runs before heartbeat. QQ uses request-local `ReplyResult`; ESP32 suppresses the legacy pipeline only after confirmed device output. ToolCatalog fronts server MCP, device MCP, plugins and desktop providers. Approval records are persistent and payload-bound.

Status: core/controller behavior is unit and mock verified. Real external services are not verified in this environment. The legacy Agent Pipeline remains the fallback path.
