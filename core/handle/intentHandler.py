import json
import uuid
import asyncio
import re
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler
from core.utils.dialogue import Message
from core.providers.tts.dto.dto import ContentType
from plugins_func.register import Action, ActionResponse
from core.handle.sendAudioHandle import send_stt_message
from core.handle.reportHandle import enqueue_tool_report
from core.utils.util import remove_punctuation_and_length
from core.providers.tts.dto.dto import TTSMessageDTO, SentenceType

TAG = __name__


def detect_meal_request(text: str):
    """识别餐饮请求，返回用餐时段、菜品关键词和目标地点。"""
    food_request = re.search(
        r"吃什么|吃啥|吃点什么|吃哪家|推荐.*[吃喝]|想吃|想喝|要吃|要喝|好饿|饿了|"
        r"换一家|换一张饭|换个吃的|换个饭|换个喝的|来点吃的|来点喝的",
        text,
    )
    if not food_request:
        return None
    if re.search(r"早上|早餐|早饭", text):
        period = "早餐"
    elif re.search(r"中午|午餐|午饭", text):
        period = "午餐"
    elif re.search(r"晚上|晚餐|晚饭", text):
        period = "晚餐"
    else:
        hour = datetime.now().hour
        period = "早餐" if hour < 10 else "午餐" if hour < 16 else "晚餐"

    keyword = ""
    match = re.search(
        r"(?:想吃|想喝|要吃|要喝|换一家|换一张饭|换个吃的|换个饭|换个喝的|来点吃的|来点喝的)"
        r"([\u4e00-\u9fffA-Za-z0-9·]{1,12})",
        text,
    )
    if match:
        keyword = re.sub(r"[了吧呢呀啊哦]$", "", match.group(1))
        if keyword in {"什么", "点什么", "饭", "东西", "吃的", "喝的", "一家"}:
            keyword = ""

    nearby_place = "易美购" if "易美购" in text else ""
    return period, keyword, nearby_place


def detect_route_request(text: str):
    """识别目的地距离或耗时查询。"""
    match = re.search(
        r"(?:去|到)([\u4e00-\u9fffA-Za-z0-9·]{1,20}?)(?:大概要|大概|要|需要)?"
        r"(?:多远|多久|几分钟|多少时间)",
        text,
    )
    if not match:
        return None
    destination = match.group(1).strip()
    if not destination:
        return None
    mode = "walking" if re.search(r"步行|走路|走过去", text) else "driving"
    return destination, mode


async def handle_user_intent(conn: "ConnectionHandler", text):
    # 预处理输入文本，处理可能的JSON格式
    try:
        if text.strip().startswith("{") and text.strip().endswith("}"):
            parsed_data = json.loads(text)
            if isinstance(parsed_data, dict) and "content" in parsed_data:
                text = parsed_data["content"]  # 提取content用于意图分析
                conn.current_speaker = parsed_data.get("speaker")  # 保留说话人信息
    except (json.JSONDecodeError, TypeError):
        pass

    # 检查是否有明确的退出命令
    _, filtered_text = remove_punctuation_and_length(text)
    if await check_direct_exit(conn, filtered_text):
        return True

    if conn.intent_type == "function_call":
        route_request = detect_route_request(text)
        if route_request and getattr(conn, "func_handler", None):
            return await estimate_route_directly(conn, text, *route_request)
        meal_request = detect_meal_request(text)
        if meal_request and getattr(conn, "func_handler", None):
            return await recommend_meal_directly(conn, text, *meal_request)
        # 使用支持function calling的聊天方法,不再进行意图分析
        return False
    # 使用LLM进行意图分析
    intent_result = await analyze_intent_with_llm(conn, text)
    if not intent_result:
        return False
    # 会话开始时生成sentence_id
    conn.sentence_id = str(uuid.uuid4().hex)
    # 处理各种意图
    return await process_intent_result(conn, intent_result, text)


async def check_direct_exit(conn: "ConnectionHandler", text):
    """检查是否有明确的退出命令"""
    _, text = remove_punctuation_and_length(text)
    cmd_exit = conn.cmd_exit
    for cmd in cmd_exit:
        if text == cmd:
            conn.logger.bind(tag=TAG).info(f"识别到明确的退出命令: {text}")
            conn.close_after_chat = False
            conn.reset_context_after_chat = True
            conn.client_abort = False
            await send_stt_message(conn, text)
            speak_txt(conn, "好的，当前对话结束了。")
            return True
    return False


async def recommend_meal_directly(
    conn: "ConnectionHandler", text: str, meal_period: str, keyword: str = "", nearby_place: str = ""
):
    """餐馆推荐不交给模型判断，确保只使用地图真实结果。"""
    function_call_data = {
        "name": "recommend_meal",
        "id": str(uuid.uuid4().hex),
        "arguments": json.dumps({
            "meal_period": meal_period,
            "keyword": keyword,
            "nearby_place": nearby_place,
        }, ensure_ascii=False),
    }
    await send_stt_message(conn, text)
    conn.client_abort = False
    enqueue_tool_report(conn, "recommend_meal", {
        "meal_period": meal_period,
        "keyword": keyword,
        "nearby_place": nearby_place,
    })

    def process_function_call():
        conn.dialogue.put(Message(role="user", content=text))
        try:
            result = asyncio.run_coroutine_threadsafe(
                conn.func_handler.handle_llm_function_call(conn, function_call_data),
                conn.loop,
            ).result(timeout=int(conn.config.get("tool_call_timeout", 30)))
            if result and result.action == Action.RESPONSE and result.response:
                speak_txt(conn, result.response)
            elif result and result.action in {Action.ERROR, Action.NOTFOUND}:
                speak_txt(conn, result.response or result.result or "餐馆查询失败")
        except Exception as exc:
            conn.logger.bind(tag=TAG).error(f"确定性餐馆推荐失败: {exc}")
            speak_txt(conn, "餐馆查询失败，请稍后再试。")

    conn.executor.submit(process_function_call)
    return True


async def estimate_route_directly(
    conn: "ConnectionHandler", text: str, destination: str, mode: str
):
    """路线查询直接使用地图结果，避免模型自行估算距离和时间。"""
    function_call_data = {
        "name": "estimate_route",
        "id": str(uuid.uuid4().hex),
        "arguments": json.dumps({"destination": destination, "mode": mode}, ensure_ascii=False),
    }
    await send_stt_message(conn, text)
    conn.client_abort = False
    enqueue_tool_report(conn, "estimate_route", {"destination": destination, "mode": mode})

    def process_function_call():
        conn.dialogue.put(Message(role="user", content=text))
        try:
            result = asyncio.run_coroutine_threadsafe(
                conn.func_handler.handle_llm_function_call(conn, function_call_data),
                conn.loop,
            ).result(timeout=int(conn.config.get("tool_call_timeout", 30)))
            if result and result.action == Action.RESPONSE and result.response:
                speak_txt(conn, result.response)
            elif result and result.action in {Action.ERROR, Action.NOTFOUND}:
                speak_txt(conn, result.response or result.result or "路线查询失败")
        except Exception as exc:
            conn.logger.bind(tag=TAG).error(f"确定性路线查询失败: {exc}")
            speak_txt(conn, "路线查询失败，请稍后再试。")

    conn.executor.submit(process_function_call)
    return True


async def analyze_intent_with_llm(conn: "ConnectionHandler", text):
    """使用LLM分析用户意图"""
    if not hasattr(conn, "intent") or not conn.intent:
        conn.logger.bind(tag=TAG).warning("意图识别服务未初始化")
        return None

    # 对话历史记录
    dialogue = conn.dialogue
    try:
        intent_result = await conn.intent.detect_intent(conn, dialogue.dialogue, text)
        return intent_result
    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"意图识别失败: {str(e)}")

    return None


async def process_intent_result(
    conn: "ConnectionHandler", intent_result, original_text
):
    """处理意图识别结果"""
    try:
        # 尝试将结果解析为JSON
        intent_data = json.loads(intent_result)

        # 检查是否有function_call
        if "function_call" in intent_data:
            # 直接从意图识别获取了function_call
            conn.logger.bind(tag=TAG).debug(
                f"检测到function_call格式的意图结果: {intent_data['function_call']['name']}"
            )
            function_name = intent_data["function_call"]["name"]
            if function_name == "continue_chat":
                return False

            if function_name == "result_for_context":
                await send_stt_message(conn, original_text)
                conn.client_abort = False

                def process_context_result():
                    conn.dialogue.put(Message(role="user", content=original_text))

                    from core.utils.current_time import get_current_time_info

                    current_time, today_date, today_weekday, lunar_date = (
                        get_current_time_info()
                    )

                    # 构建带上下文的基础提示
                    context_prompt = f"""当前时间：{current_time}
                                        今天日期：{today_date} ({today_weekday})
                                        今天农历：{lunar_date}

                                        请根据以上信息回答用户的问题：{original_text}"""

                    # 使用异步调用避免阻塞事件循环，影响其他设备的音频播放
                    try:
                        response = asyncio.run_coroutine_threadsafe(
                            conn.intent.replyResult(context_prompt, original_text),
                            conn.loop,
                        ).result()
                    except Exception as e:
                        conn.logger.bind(tag=TAG).error(f"LLM生成回复失败: {e}")
                        response = None
                    if response:
                        speak_txt(conn, response)

                conn.executor.submit(process_context_result)
                return True

            function_args = {}
            if "arguments" in intent_data["function_call"]:
                function_args = intent_data["function_call"]["arguments"]
                if function_args is None:
                    function_args = {}
            # 确保参数是字符串格式的JSON
            if isinstance(function_args, dict):
                function_args = json.dumps(function_args)

            function_call_data = {
                "name": function_name,
                "id": str(uuid.uuid4().hex),
                "arguments": function_args,
            }

            await send_stt_message(conn, original_text)
            conn.client_abort = False

            # 准备工具调用参数
            tool_input = {}
            if function_args:
                if isinstance(function_args, str):
                    tool_input = json.loads(function_args) if function_args else {}
                elif isinstance(function_args, dict):
                    tool_input = function_args

            # 上报工具调用
            enqueue_tool_report(conn, function_name, tool_input)

            # 使用executor执行函数调用和结果处理
            def process_function_call():
                conn.dialogue.put(Message(role="user", content=original_text))
                
                # 工具调用超时时间
                tool_call_timeout = int(conn.config.get("tool_call_timeout", 30))
                # 使用统一工具处理器处理所有工具调用
                try:
                    result = asyncio.run_coroutine_threadsafe(
                        conn.func_handler.handle_llm_function_call(
                            conn, function_call_data
                        ),
                        conn.loop,
                    ).result(timeout=tool_call_timeout)
                except Exception as e:
                    conn.logger.bind(tag=TAG).error(f"工具调用失败: {e}")
                    result = ActionResponse(
                        action=Action.ERROR, result="工具调用超时，请一会再试下哈", response="工具调用超时，请一会再试下哈"
                    )

                # 上报工具调用结果
                if result:
                    enqueue_tool_report(conn, function_name, tool_input, str(result.result) if result.result else None, report_tool_call=False)

                    if result.action == Action.RESPONSE:  # 直接回复前端
                        text = result.response
                        if text is not None:
                            speak_txt(conn, text)
                    elif result.action == Action.REQLLM:  # 调用函数后再请求llm生成回复
                        text = result.result
                        conn.dialogue.put(Message(role="tool", content=text))
                        # 使用异步调用避免阻塞事件循环，影响其他设备的音频播放
                        try:
                            llm_result = asyncio.run_coroutine_threadsafe(
                                conn.intent.replyResult(text, original_text),
                                conn.loop,
                            ).result()
                        except Exception as e:
                            conn.logger.bind(tag=TAG).error(f"LLM生成回复失败: {e}")
                            llm_result = text
                        if llm_result is None:
                            llm_result = text
                        speak_txt(conn, llm_result)
                    elif (
                        result.action == Action.NOTFOUND
                        or result.action == Action.ERROR
                    ):
                        text = result.response if result.response else result.result
                        if text is not None:
                            speak_txt(conn, text)
                    elif function_name != "play_music":
                        # For backward compatibility with original code
                        # 获取当前最新的文本索引
                        text = result.response
                        if text is None:
                            text = result.result
                        if text is not None:
                            speak_txt(conn, text)

            # 将函数执行放在线程池中
            conn.executor.submit(process_function_call)
            return True
        return False
    except json.JSONDecodeError as e:
        conn.logger.bind(tag=TAG).error(f"处理意图结果时出错: {e}")
        return False


def speak_txt(conn: "ConnectionHandler", text):
    # 记录文本到 sentence_id 映射
    conn.tts.store_tts_text(conn.sentence_id, text)

    conn.tts.tts_text_queue.put(
        TTSMessageDTO(
            sentence_id=conn.sentence_id,
            sentence_type=SentenceType.FIRST,
            content_type=ContentType.ACTION,
        )
    )
    conn.tts.tts_one_sentence(conn, ContentType.TEXT, content_detail=text)
    conn.tts.tts_text_queue.put(
        TTSMessageDTO(
            sentence_id=conn.sentence_id,
            sentence_type=SentenceType.LAST,
            content_type=ContentType.ACTION,
        )
    )
    conn.dialogue.put(Message(role="assistant", content=text))
