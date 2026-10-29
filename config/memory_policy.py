"""One memory-backend policy shared by the controller and deployment tools."""


def mmtr_preferred(config):
    value = (config.get("mmtr") or {}).get("preferred", True)
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def selected_memory_backend(config):
    if mmtr_preferred(config):
        return "mmtr"
    selected = str(config.get("memory_backend", "legacy")).strip().lower()
    if selected == "mmtr":
        return "mmtr"
    enabled = str(config.get("tencent_memory_enabled", False)).strip().lower() in {"1", "true", "yes", "on"}
    return "tencent" if enabled else selected


def apply_memory_policy(config):
    if mmtr_preferred(config):
        config["memory_backend"] = "mmtr"
        config["tencent_memory_enabled"] = False
        config.setdefault("cognitive_core", {})["share_legacy_memory"] = True
    return config
