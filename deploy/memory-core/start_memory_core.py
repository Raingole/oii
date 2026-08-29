"""Start the pinned TencentDB MemoryCore from the controller repository."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
MEMORY_CORE = ROOT / "TencentDB-Agent-Memory" / "MemoryCore"
CONTROLLER_CONFIG = ROOT / "data" / ".config.yaml"
UPSTREAM_CONFIG = MEMORY_CORE / "tdai-gateway.standalone.yaml"


def main() -> int:
    if not MEMORY_CORE.is_dir():
        raise SystemExit(f"MemoryCore source not found: {MEMORY_CORE}")
    config = yaml.safe_load(CONTROLLER_CONFIG.read_text(encoding="utf-8")) or {}
    llm_section = config.get("LLM", {})
    selected_llm = config.get("selected_module", {}).get("LLM", "")
    llm = llm_section.get(selected_llm, {}) if isinstance(llm_section, dict) else {}
    llm = llm if isinstance(llm, dict) else {}
    endpoint = str(config.get("tencent_memory_base_url", "http://127.0.0.1:8420"))
    port = endpoint.rsplit(":", 1)[-1].rstrip("/") if ":" in endpoint else "8420"
    env = os.environ.copy()
    env.update({
        "TDAI_GATEWAY_CONFIG": str(UPSTREAM_CONFIG),
        "TDAI_GATEWAY_HOST": str(config.get("tencent_memory_host", "127.0.0.1")),
        "TDAI_GATEWAY_PORT": port,
        "TDAI_DATA_DIR": str(ROOT / "data" / "memory" / "tencentdb"),
        "TDAI_LLM_API_KEY": str(llm.get("api_key", "")),
        "TDAI_LLM_BASE_URL": str(llm.get("base_url", "https://api.openai.com/v1")),
        "TDAI_LLM_MODEL": str(llm.get("model_name", "gpt-4o")),
    })
    gateway_key = str(config.get("tencent_memory_api_key", ""))
    if gateway_key:
        env["TDAI_GATEWAY_API_KEY"] = gateway_key
    print(f"[Memory] Starting pinned MemoryCore from {MEMORY_CORE}", flush=True)
    print(f"[Memory] Config source: {CONTROLLER_CONFIG}", flush=True)
    print(f"[Memory] Data directory: {env['TDAI_DATA_DIR']}", flush=True)
    return subprocess.call(["node", "--import", "tsx", "src/gateway/server.ts"], cwd=MEMORY_CORE, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
