# TencentDB Agent MemoryCore

The controller uses the official MemoryCore HTTP v2 API. The upstream source
was inspected at commit `5299c00aaf65481703c180fd69df066d11254eb7`.

## Build the pinned image

```bash
git clone https://github.com/TencentCloud/TencentDB-Agent-Memory.git
cd TencentDB-Agent-Memory
git checkout 5299c00aaf65481703c180fd69df066d11254eb7
docker build -t tencentdb-agent-memory:5299c00aaf65481703c180fd69df066d11254eb7 MemoryCore
```

Copy `.env.example` to `.env`, set `TDAI_LLM_API_KEY`, and start:

```bash
docker compose --env-file .env -f deploy/memory-core/docker-compose.yml up -d
curl http://127.0.0.1:8420/health
```

The SQLite/local files are persisted in the `memory-core-data` Docker volume.
The controller remains usable in degraded mode if this service is stopped.

## Controller configuration

Set `MEMORY_BACKEND=tencent` and the `TENCENT_MEMORY_*` variables from the
repository `.env.example`. Use `MEMORY_BACKEND=legacy` for an emergency rollback.

The adapter calls `/v2/atomic/search` once before each LLM turn,
`/v2/conversation/add` asynchronously after each completed turn, and
`/v2/offload/ingest` asynchronously for tool pairs. Memory failures are logged
and do not fail the ASR/LLM/MCP/TTS request.

