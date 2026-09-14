from __future__ import annotations

from typing import Any


def reflect(event: Any, result: dict[str, Any]) -> dict[str, Any] | None:
    appraisal = result.get("appraisal", {})
    if appraisal.get("emotional_salience", 0) < 0.7 and event.type != "action_result": return None
    failed = appraisal.get("pleasantness", 1) < 0.4
    return {"lesson": "工具失败后需要验证结果" if failed else "重要事件应保留证据并复盘", "self_implication": "我应保持谨慎并区分事实与推测", "future_strategy": "对关键工具结果执行一次观察或重试", "confidence": 0.75}
