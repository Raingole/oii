from __future__ import annotations

import os
import asyncio
from aiohttp import web
from .api import create_app
from .runtime import CognitiveCore
from .memory import TencentMemoryAdapter, FallbackMemoryPort
from controller.approval_service import ApprovalService
from config.config_loader import load_config
from config.logger import setup_logging
from core.utils.modules_initialize import initialize_modules
from .llm import ExistingProviderLLM


def main() -> None:
    config = asyncio.run(load_config())
    cc = config.get("cognitive_core", {}) if isinstance(config.get("cognitive_core", {}), dict) else {}
    use_remote_memory = os.getenv("COGNITIVE_MEMORY_BACKEND", "remote" if config.get("tencent_memory_enabled", False) else "local").lower() != "local"
    base = (os.getenv("TENCENT_MEMORY_URL") or config.get("tencent_memory_base_url", "")) if use_remote_memory else ""
    remote_memory = TencentMemoryAdapter(
        base, os.getenv("TENCENT_MEMORY_API_KEY") or str(config.get("tencent_memory_api_key", "")),
        timeout=float(config.get("tencent_memory_timeout", 3)), service_id=str(config.get("tencent_memory_service_id", "default")),
        team_id=str(config.get("tencent_memory_team_id", "personal")), agent_id=str(config.get("tencent_memory_agent_id", "central-controller")),
        user_id=str(config.get("tencent_memory_user_id") or config.get("owner_id", "gu")), api_version=str(config.get("tencent_memory_api_version", "v2")),
    ) if base else None
    memory = FallbackMemoryPort(remote_memory) if remote_memory else None
    core = CognitiveCore(db_path=os.getenv("COGNITIVE_DB_PATH") or str(cc.get("db_path", "data/cognitive/cognitive.db")), config={**cc, "api_token": os.getenv("COGNITIVE_CORE_API_TOKEN") or cc.get("api_token", "")}, memory=memory)
    if cc.get("llm_enabled", False):
        logger = setup_logging(config)
        try:
            modules = initialize_modules(logger, config, init_llm=True)
            provider = modules.get("llm")
            if provider is None:
                raise RuntimeError("selected LLM provider was not initialized")
            core.llm = ExistingProviderLLM(provider, timeout=float(cc.get("llm_timeout", 60) or 60))
            logger.bind(tag="cognitive_core").info("cognitive LLM provider connected")
        except Exception as exc:
            logger.bind(tag="cognitive_core").error(f"cognitive LLM provider unavailable; fallback planner remains active: {exc}")
    approval = ApprovalService(core.store.path)
    web.run_app(create_app(core, approval=approval), host=os.getenv("COGNITIVE_CORE_HOST", "127.0.0.1"), port=int(os.getenv("COGNITIVE_CORE_PORT", "8010")))


if __name__ == "__main__": main()
