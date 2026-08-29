# TencentDB Agent MemoryCore

The controller uses the official MemoryCore HTTP v2 API. The upstream source
was inspected at commit `5299c00aaf65481703c180fd69df066d11254eb7`.

The pinned upstream package references a missing optional `seed-v2` build
directory. The vendored `build:scripts` command omits that optional helper;
the MemoryCore Gateway itself is unaffected.

## Direct standalone install (no Docker)

```bash
cd TencentDB-Agent-Memory/MemoryCore
npm install
npm run build
cd ../..
python deploy/memory-core/start_memory_core.py
curl http://127.0.0.1:8420/health
```

The launcher reads the LLM and TencentDB settings from `data/.config.yaml`.
MemoryCore data is persisted in `data/memory/tencentdb/`. Use systemd,
Supervisor, or another process manager to keep the standalone Node process up.
The controller remains usable in degraded mode if this service is stopped.

仓库根目录的 `setup-and-start.sh` 是一键启动脚本。首次运行会安装并构建
MemoryCore，然后启动 MemoryCore、MCP、QQ、Web UI 和中控。

## Controller configuration

Set `memory_backend: tencent` and `tencent_memory_enabled: true` in
`data/.config.yaml`. No Tencent memory environment variables are required.
Use `memory_backend: legacy` and `tencent_memory_enabled: false` for rollback.

The adapter calls `/v2/atomic/search` once before each LLM turn,
`/v2/conversation/add` asynchronously after each completed turn, and
`/v2/offload/ingest` asynchronously for tool pairs. Memory failures are logged
and do not fail the ASR/LLM/MCP/TTS request.

统一用户 ID 配置为 `gu`，QQ 和 ESP 共用该身份。
当前映射为 QQ `2496303940 -> gu`，唯一 ESP 设备 `* -> gu`；外部账号和设备 ID
只用于渠道识别，不会作为 TencentDB 的 `user_id`。
