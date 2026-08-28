from plugins_func.register import register_function, ToolType, ActionResponse, Action
from config.logger import setup_logging
from typing import TYPE_CHECKING
import re

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()


def normalize_goodbye(text: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff]", "", str(text))
    return text[:15] or "再见下次再聊"

handle_exit_intent_function_desc = {
    "type": "function",
    "function": {
        "name": "handle_exit_intent",
        "description": "当用户想结束对话或需要退出系统时调用",
        "parameters": {
            "type": "object",
            "properties": {
                "say_goodbye": {
                    "type": "string",
                    "description": "和用户友好结束对话的告别语",
                }
            },
            "required": ["say_goodbye"],
        },
    },
}


@register_function(
    "handle_exit_intent", handle_exit_intent_function_desc, ToolType.SYSTEM_CTL
)
def handle_exit_intent(conn: "ConnectionHandler", say_goodbye: str | None = None):
    # 处理退出意图
    try:
        if say_goodbye is None:
            say_goodbye = "再见，祝您生活愉快！"
        say_goodbye = normalize_goodbye(say_goodbye)
        conn.close_after_chat = False
        conn.reset_context_after_chat = True
        conn.conversation_active = False
        logger.bind(tag=TAG).info(f"退出意图已处理:{say_goodbye}")
        return ActionResponse(
            action=Action.RESPONSE, result="退出意图已处理", response=say_goodbye
        )
    except Exception as e:
        logger.bind(tag=TAG).error(f"处理退出意图错误: {e}")
        return ActionResponse(
            action=Action.NONE, result="退出意图处理失败", response=""
        )
