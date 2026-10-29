"""Bounded records of actual recall inputs and visible cognitive outputs.

This is provenance for observable application state, never model attention or
private chain-of-thought. No extra recall is performed to construct the view.
"""
from difflib import SequenceMatcher
import re


REASONS = {"vector": "向量入口", "calendar": "日历唤醒", "prospective": "约定临近",
           "graph:entity": "共享人物或物件", "graph:temporal": "时间相近",
           "graph:calendar": "共同日历锚点", "graph:theme": "共同主题"}


def terms(query, content, explicit=()):
    result = [str(x)[:24] for x in explicit if str(x).strip()][:8]
    for match in SequenceMatcher(None, query, content, autojunk=False).get_matching_blocks():
        fragment = query[match.a:match.a + match.size].strip('，。！？,!? ;；\n\t“”"')
        if 2 <= len(fragment) <= 16 and fragment not in {"我们", "你们", "他们", "记得", "什么", "这个", "那个"}:
            result.append(fragment)
    result.extend(word for word in re.findall(r"[A-Za-z0-9_]{3,}", query) if word.lower() in content.lower())
    return list(dict.fromkeys(result))[:8]


def build_activity(event, memories, meaning, goals, actions, decision, llm_used, backend):
    query = str(event.content.get("text", event.type))
    details = next(((item.metadata or {}).get("mmtr") for item in memories if (item.metadata or {}).get("mmtr")), {})
    memory_nodes = []
    for item in details.get("items", [])[:8]:
        content = str(item.get("content", ""))[:4000]
        explicit = [*item.get("entities", []), *item.get("themes", [])]
        channels = [{"channel": c["channel"], "strength": round(float(c["strength"]), 4), "present": bool(c.get("content")),
                     "content": str(c.get("content", ""))[:1000] if c.get("content") and c["content"] in content else ""}
                    for c in item.get("channels", [])]
        memory_nodes.append({"id": item["id"], "kind": "memory", "memory_kind": item.get("kind", "episode"),
            "label": "约定" if item.get("kind") == "promise" else "记忆",
            "text": content, "terms": terms(query, content, explicit), "channels": channels,
            "reasons": [REASONS.get(value, value) for value in item.get("reasons", [])],
            "source": "本轮实际召回", "provided_to_model": llm_used})
    for item in details.get("core", [])[:16]:
        content = str(item.get("content", ""))[:4000]
        memory_nodes.append({"id": f"core:{item['key']}", "kind": "memory", "memory_kind": "core",
            "label": "常驻记忆", "text": content, "terms": terms(query, content), "channels": [],
            "reasons": ["每轮常驻"], "source": "本轮实际上下文", "provided_to_model": llm_used})
    # Other memory adapters also expose only what was actually returned.
    if not details:
        for index, item in enumerate(memories[:8]):
            if item.text:
                memory_nodes.append({"id": f"returned:{index}", "kind": "memory", "memory_kind": "returned",
                    "label": "召回内容", "text": item.text[:4000], "terms": terms(query, item.text),
                    "channels": [], "reasons": ["记忆接口返回"], "source": "本轮实际召回", "provided_to_model": llm_used})
    cognition = []
    for goal in goals:
        if goal.get("status", "active") == "active":
            content = str(goal.get("title", "")) + ("：" + str(goal["description"]) if goal.get("description") else "")
            cognition.append({"id": f"goal:{goal.get('id', len(cognition))}", "kind": "cognition", "label": "当前目标",
                "text": content[:1000], "terms": terms(query, content), "source": "思维核心目标状态",
                "provided_to_model": llm_used, "reasons": ["作为目标上下文提供" if llm_used else "规则规划可见目标"]})
            if len(cognition) >= 3:
                break
    content = str(meaning.get("meaning", ""))[:300]
    if content:
        cognition.append({"id": "meaning", "kind": "cognition", "label": "事件理解", "text": content,
            "terms": terms(query, content), "source": "思维核心的规则评估", "provided_to_model": False,
            "reasons": ["实际事件评估结果"]})
    explanation = str((decision or {}).get("reason") or next((action.reason for action in actions), ""))[:300]
    if explanation:
        cognition.append({"id": "decision", "kind": "cognition", "label": "决策摘要", "text": explanation,
            "terms": terms(query, explanation), "source": "模型结构化输出" if llm_used else "规则规划结果",
            "provided_to_model": False, "reasons": ["可见的简短决策说明"]})
    return {"event_id": event.event_id, "session_id": event.session_id or "default", "user_id": event.metadata.get("user_id", event.actor_id),
        "source": event.source, "timestamp": event.timestamp, "input_text": str(event.content.get("text", ""))[:4000],
        "query": query[:4000], "backend": backend, "mode": "llm" if llm_used else "rules",
        "parent_event_id": str(event.metadata.get("parent_event_id", "")),
        "memory_nodes": memory_nodes, "cognition_nodes": cognition,
        "connections": [{"a": edge["a"], "b": edge["b"], "kind": edge["kind"]} for edge in details.get("connections", [])[:64]],
        "context": "\n".join(item.text for item in memories)[:8000],
        "reply": next((str(action.payload.get("text", ""))[:4000] for action in actions if action.type in {"speak", "send_message"}), ""),
        "action_types": list(dict.fromkeys(action.type for action in actions)),
        "omitted_reminders": len(details.get("omittedReminders", []))}
