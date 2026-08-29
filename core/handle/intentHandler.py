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
from core.utils.util import remove_punctuation_and_length, sanitize_tool_name
from core.providers.tts.dto.dto import TTSMessageDTO, SentenceType

TAG = __name__

AIR_CONDITIONER_SET_TEMPERATURE = sanitize_tool_name(
    "self.air_conditioner.set_temperature"
)
AIR_CONDITIONER_POWER_OFF = sanitize_tool_name("self.air_conditioner.power_off")
AIR_CONDITIONER_GET_LAST_COMMAND = sanitize_tool_name(
    "self.air_conditioner.get_last_command"
)


def detect_air_conditioner_request(text: str):
    """Return a safe direct MCP call for explicit air-conditioner requests."""
    normalized = re.sub(r"[，。！？、,.!?]", "", text).strip()
    if "空调" not in normalized:
        return None

    if re.search(r"(?:关闭|关掉|关上|关了|停止|停掉).{0,4}空调|空调.{0,4}(?:关闭|关掉|关上|停止|停掉)", normalized):
        return AIR_CONDITIONER_POWER_OFF, {}

    if re.search(r"(?:最后|上次|上一个).{0,5}(?:空调|指令)|(?:空调|指令).{0,5}(?:最后|上次|上一个)", normalized):
        return AIR_CONDITIONER_GET_LAST_COMMAND, {}

    match = re.search(r"(\d{1,2})\s*(?:度|℃)?", normalized)
    chinese_temperatures = {
        "十六": 16, "十七": 17, "十八": 18, "十九": 19,
        "二十": 20, "二十一": 21, "二十二": 22, "二十三": 23,
        "二十四": 24, "二十五": 25, "二十六": 26, "二十七": 27,
        "二十八": 28, "二十九": 29, "三十": 30,
    }

    # “空调25度”是常见的省略式设温请求，即使没有出现“设置/调到”也应直调。
    if match and ("度" in normalized or "℃" in normalized):
        return AIR_CONDITIONER_SET_TEMPERATURE, {"temperature": int(match.group(1))}

    if "度" in normalized or "℃" in normalized:
        for phrase, temperature in sorted(
            chinese_temperatures.items(), key=lambda item: len(item[0]), reverse=True
        ):
            if phrase in normalized:
                return AIR_CONDITIONER_SET_TEMPERATURE, {"temperature": temperature}

    if re.search(r"(?:设置|调到|调成|调为|改到|改成|设为|设到|温度)", normalized):
        for phrase, temperature in sorted(
            chinese_temperatures.items(), key=lambda item: len(item[0]), reverse=True
        ):
            if phrase in normalized:
                return AIR_CONDITIONER_SET_TEMPERATURE, {"temperature": temperature}

        if match:
            return AIR_CONDITIONER_SET_TEMPERATURE, {"temperature": int(match.group(1))}

    if re.search(r"(?:设置|调到|调成|调为|改到|改成|设为|设到)\s*(?:多少|几度)?$", normalized):
        return AIR_CONDITIONER_SET_TEMPERATURE, None
    return None


async def execute_air_conditioner_directly(conn, text: str, tool_name: str, arguments):
    """Execute an explicit air-conditioner request without relying on LLM tool choice."""
    if not conn.func_handler.has_tool(tool_name):
        conn.logger.bind(tag=TAG).warning(f"空调工具未注册，无法执行: {tool_name}")
        await send_stt_message(conn, text)
        conn.client_abort = False
        speak_txt(conn, "当前设备未提供对应的空调控制接口。")
        return True

    if arguments is None:
        await send_stt_message(conn, text)
        conn.client_abort = False
        speak_txt(conn, "可以，请告诉我目标温度，支持16到30度的制冷设定。")
        return True

    await send_stt_message(conn, text)
    conn.client_abort = False
    enqueue_tool_report(conn, tool_name, arguments)
    function_call_data = {
        "name": tool_name,
        "id": str(uuid.uuid4().hex),
        "arguments": json.dumps(arguments, ensure_ascii=False),
    }

    def process_function_call():
        conn.dialogue.put(Message(role="user", content=text))
        try:
            result = asyncio.run_coroutine_threadsafe(
                conn.func_handler.handle_llm_function_call(conn, function_call_data),
                conn.loop,
            ).result(timeout=int(conn.config.get("tool_call_timeout", 30)))
            if not result:
                speak_txt(conn, "空调指令执行失败，请稍后再试。")
            elif result.action in {Action.ERROR, Action.NOTFOUND}:
                speak_txt(conn, result.response or result.result or "空调指令执行失败，请稍后再试。")
            elif tool_name == AIR_CONDITIONER_SET_TEMPERATURE:
                speak_txt(conn, f"红外指令已发送，已为你设置制冷{arguments['temperature']}度。")
            elif tool_name == AIR_CONDITIONER_POWER_OFF:
                speak_txt(conn, "空调关机红外指令已发送。")
            else:
                result_text = result.result or result.response or "未能获取最后一次空调指令。"
                speak_txt(conn, result_text)
        except Exception as exc:
            conn.logger.bind(tag=TAG).error(f"空调MCP直调用失败: {exc}")
            speak_txt(conn, "空调红外指令未成功发送，请检查设备连接。")

    conn.executor.submit(process_function_call)
    return True


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

    # 空调属于有实际副作用的设备控制：明确请求时直接调用设备 MCP，
    # 避免模型选择普通文本回复而跳过红外下发。
    if getattr(conn, "func_handler", None):
        air_request = detect_air_conditioner_request(text)
        if air_request:
            tool_name, arguments = air_request
            if await execute_air_conditioner_directly(
                conn, text, tool_name, arguments
            ):
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
            await conn._mark_conversation_ending()
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
    if getattr(conn, "sentence_id", None) is not None:
        conn.sentence_turn_ids[conn.sentence_id] = getattr(conn, "active_turn_id", None)
    conn.tts.store_tts_text(conn.sentence_id, text)

    conn.tts.tts_text_queue.put(
        TTSMessageDTO(
            sentence_id=conn.sentence_id,
            sentence_type=SentenceType.FIRST,
            content_type=ContentType.ACTION,
            turn_id=getattr(conn, "active_turn_id", None),
        )
    )
    conn.tts.tts_one_sentence(conn, ContentType.TEXT, content_detail=text)
    conn.tts.tts_text_queue.put(
        TTSMessageDTO(
            sentence_id=conn.sentence_id,
            sentence_type=SentenceType.LAST,
            content_type=ContentType.ACTION,
            turn_id=getattr(conn, "active_turn_id", None),
        )
    )
    conn.dialogue.put(Message(role="assistant", content=text))
