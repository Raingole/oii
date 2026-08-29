"""Send a message to the single configured owner through QQ."""

from __future__ import annotations

import json
from typing import Any

from plugins_func.register import Action, ActionResponse, ToolType, register_function


DESCRIPTION = {
    "type": "function",
    "function": {
        "name": "qq.send_to_owner",
        "description": (
            "将文本发送到用户本人的QQ。当用户明确说“发到我的QQ”“发给我QQ”“把这个发到QQ” "
            "“把刚才推荐的餐厅、地址、链接或结果发到我的QQ”时，必须调用此工具，"
            "不要回答没有发送能力，也不要让用户手动复制。message填写要发送的完整文本；"
            "如果用户说“这个”“刚才的结果”或“刚才的地址”，优先使用当前会话上一轮工具返回的结构化结果。"
            "目标QQ由服务器配置决定，禁止自行生成或询问QQ号。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "要发送的完整文本；引用上一轮结果时可填写“这个”或“刚才的结果”。",
                }
            },
            "required": ["message"],
        },
    },
}


def _parse(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def _is_reference(text: str) -> bool:
    return any(word in text for word in ("这个", "刚才", "上一条", "上述", "它", "地址", "结果"))

def _format_artifact(value: Any, address_only: bool = False) -> str:
    value = _parse(value)
    if isinstance(value, dict):
        recommendation = value.get("recommendation")
        if isinstance(recommendation, dict):
            lines = []
            name = recommendation.get("name") or value.get("name")
            address = recommendation.get("address") or value.get("address")
            distance = recommendation.get("distance_m") or recommendation.get("distance") or value.get("distance")
            rating = recommendation.get("rating") or value.get("rating")
            if address_only and address:
                return f"地址：{address}"
            if name:
                lines.append(str(name))
            if address:
                lines.append(f"地址：{address}")
            if distance:
                lines.append(f"距离：{distance}米")
            if rating:
                lines.append(f"评分：{rating}")
            if lines:
                return "\n".join(lines)
        if any(value.get(key) for key in ("name", "address", "distance", "distance_m")):
            lines = []
            if address_only and value.get("address"):
                return f"地址：{value['address']}"
            if value.get("name"):
                lines.append(str(value["name"]))
            if value.get("address"):
                lines.append(f"地址：{value['address']}")
            distance = value.get("distance_m") or value.get("distance")
            if distance:
                lines.append(f"距离：{distance}米")
            if lines:
                return "\n".join(lines)
        for key in ("response", "content", "text", "result"):
            if value.get(key):
                nested = _format_artifact(value[key], address_only=address_only)
                if nested:
                    return nested
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "\n".join(_format_artifact(item, address_only=address_only) for item in value if item)
    return str(value or "").strip()


@register_function("qq.send_to_owner", DESCRIPTION, type=ToolType.SYSTEM_CTL)
async def send_to_owner(conn, message: str) -> ActionResponse:
    requested = str(message or "").strip()
    if not requested:
        return ActionResponse(Action.ERROR, response="要发送的QQ消息不能为空")

    if _is_reference(requested):
        artifact = getattr(conn, "last_tool_result", None)
        if not artifact:
            manager = getattr(getattr(conn, "server", None), "memory_manager", None)
            recent = manager.get_recent_artifact() if manager is not None else None
            artifact = recent.get("data") if recent else None
        if artifact:
            requested = _format_artifact(
                artifact,
                address_only=("地址" in requested and "这个" not in requested),
            )
        elif requested in {"这个", "刚才的结果", "上一条结果", "地址", "刚才的地址"}:
            return ActionResponse(Action.ERROR, response="没有找到可发送的上一条工具结果")

    server = getattr(conn, "server", None)
    service = getattr(server, "qq_service", None)
    if service is None:
        return ActionResponse(Action.ERROR, response="QQ发送服务未初始化")

    result = await service.send_text_to_owner(requested)
    if not result.get("success"):
        return ActionResponse(
            Action.ERROR,
            result=json.dumps(result, ensure_ascii=False),
            response="发送到QQ失败，请稍后重试",
        )
    return ActionResponse(
        Action.RESPONSE,
        result=json.dumps(result, ensure_ascii=False),
        response="已发送到你的QQ",
    )
