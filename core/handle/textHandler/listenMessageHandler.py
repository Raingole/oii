import time
import uuid
import asyncio
from typing import Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

from core.utils.dialogue import Message
from core.utils.util import audio_to_data
from core.providers.asr.dto.dto import InterfaceType
from core.handle.receiveAudioHandle import startToChat
from core.handle.reportHandle import enqueue_asr_report
from core.handle.sendAudioHandle import sendAudioMessage, send_stt_message, send_tts_message
from core.handle.textMessageHandler import TextMessageHandler
from core.handle.textMessageType import TextMessageType
from core.utils.util import remove_punctuation_and_length
from core.providers.tts.dto.dto import ContentType, TTSMessageDTO, SentenceType


TAG = __name__


async def _send_wake_ack(conn: "ConnectionHandler", turn_id: int, sentence_id: str):
    """Send the wake acknowledgement from a local file, independent of cloud TTS."""
    if not conn.is_current_turn(turn_id) or conn.sentence_id != sentence_id:
        return
    try:
        opus_packets = await audio_to_data(
            "config/assets/wakeup_words_short.wav", use_cache=True
        )
        if not opus_packets or not conn.is_current_turn(turn_id):
            return
        # Keep the same control-message order as normal TTS responses.
        # ESP expects tts/start before sentence_start and audio frames.
        await send_tts_message(conn, "start", turn_id=turn_id)
        await sendAudioMessage(conn, SentenceType.FIRST, opus_packets, "我在", sentence_id)
        await sendAudioMessage(conn, SentenceType.LAST, [], None, sentence_id)
        conn.logger.bind(tag=TAG).info(
            f"[session={conn.session_id} turn={turn_id}] wake acknowledgement sent"
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        conn.logger.bind(tag=TAG).warning(
            f"[session={conn.session_id} turn={turn_id}] wake acknowledgement failed: {exc}"
        )

class ListenTextMessageHandler(TextMessageHandler):
    """Listen消息处理器"""

    @property
    def message_type(self) -> TextMessageType:
        return TextMessageType.LISTEN

    async def handle(self, conn: "ConnectionHandler", msg_json: Dict[str, Any]) -> None:
        state = msg_json.get("state")
        if state == "start":
            if conn.conversation_state.name == "WAIT_WAKE_WORD":
                conn.logger.bind(tag=TAG).info("Ignore listen/start: waiting for wake word")
                return
            if conn.active_conversation is None or not conn.active_conversation.active:
                conn.logger.bind(tag=TAG).info("Ignore listen/start: no active conversation")
                return
            if "mode" in msg_json:
                conn.client_listen_mode = msg_json["mode"]
            turn, duplicate = await conn.start_turn(
                msg_json.get("client_event_id", ""), msg_json.get("sample_rate")
            )
            await conn.send_listen_ready(turn.turn_id)
            await conn.send_conversation_state("active")
            # The wake word is detected locally by the ESP.  Queue the
            # acknowledgement only after the formal turn has been created;
            # start_turn() clears the previous turn's queues and would
            # otherwise race with an immediately queued "我在" response.
            if getattr(conn, "wake_ack_pending", False):
                conn.wake_ack_pending = False
                conn.sentence_turn_ids[conn.sentence_id] = turn.turn_id
                ack_task = asyncio.create_task(
                    _send_wake_ack(conn, turn.turn_id, conn.sentence_id)
                )
                turn.track(ack_task, "tts")
                conn.logger.bind(tag=TAG).info(
                    f"[session={conn.session_id} turn={turn.turn_id}] wake acknowledgement queued"
                )
            return
        if state == "detect":
            if conn.conversation_state.name == "ENDING":
                conn.logger.bind(tag=TAG).info("Ignore wake event while conversation is ending")
                return
            if conn.conversation_state.name == "WAIT_WAKE_WORD":
                session = await conn.start_conversation_after_wake()
                if session is None:
                    return
                conn.sentence_id = uuid.uuid4().hex
                conn.just_woken_up = True
                conn.wake_ack_pending = True
                conn.logger.bind(tag=TAG).info(
                    f"Wake word accepted; ConversationSession active: {session.conversation_id}; acknowledgement pending"
                )
                return
        if state == "stop" and conn.conversation_state.name != "ACTIVE":
            conn.logger.bind(tag=TAG).info("Ignore listen/stop: no active conversation")
            return
        if "mode" in msg_json:
            conn.client_listen_mode = msg_json["mode"]
            conn.logger.bind(tag=TAG).debug(
                f"客户端拾音模式：{conn.client_listen_mode}"
            )
        if msg_json["state"] == "stop":
            if conn.active_conversation is None:
                return
            requested_turn_id = msg_json.get("turn_id")
            if requested_turn_id is not None and not conn.is_current_turn(requested_turn_id):
                conn.logger.bind(tag=TAG).info(
                    f"Ignore stale listen/stop: turn_id={requested_turn_id}, active={conn.active_turn_id}"
                )
                return
            # 收到stop但asr未初始化，跳过处理
            if conn.asr is None:
                return

            conn.client_voice_stop = True
            if conn.active_turn is not None:
                from core.turn import AudioInputState, TurnState
                conn.audio_input_state = AudioInputState.IDLE
                conn.active_turn.state = TurnState.ASR
            if conn.asr.interface_type == InterfaceType.STREAM:
                # 流式模式下，发送结束请求
                asyncio.create_task(conn.asr._send_stop_request())
            else:
                # 非流式模式：直接触发ASR识别
                if len(conn.asr_audio) > 0:
                    asr_audio_task = conn.asr_audio.copy()
                    conn.reset_audio_states()

                    if len(asr_audio_task) > 0:
                        asyncio.create_task(conn.asr.handle_voice_stop(conn, asr_audio_task))
        elif msg_json["state"] == "detect":
            if conn.active_conversation is None:
                return
            conn.client_have_voice = False
            conn.reset_audio_states()
            if "text" in msg_json:
                conn.last_activity_time = time.time() * 1000
                original_text = msg_json["text"]  # 保留原始文本
                filtered_len, filtered_text = remove_punctuation_and_length(
                    original_text
                )

                # 检查是否是设备呼叫指令 [device_call]
                if original_text.startswith("[device_call]"):
                    # 提取 tag 后的文本
                    call_text = original_text[len("[device_call]"):].strip()
                    conn.logger.bind(tag=TAG).info(f"收到设备呼叫指令: {call_text}")

                    # 标记为来电接听模式
                    conn.incoming_call = True

                    # 准备开始新会话
                    conn.sentence_id = uuid.uuid4().hex

                    await send_stt_message(conn, call_text)

                    # 等待tts初始化，最多等待3秒
                    start_time = time.time()
                    while time.time() - start_time < 3:
                        if conn.tts:
                            break
                        await asyncio.sleep(0.1)

                    if conn.tts:
                        conn.tts.store_tts_text(conn.sentence_id, call_text)
                        conn.tts.tts_text_queue.put(TTSMessageDTO(sentence_id=conn.sentence_id, sentence_type=SentenceType.FIRST, content_type=ContentType.ACTION))
                        conn.tts.tts_one_sentence(conn, ContentType.TEXT, content_detail=call_text)
                        conn.tts.tts_text_queue.put(TTSMessageDTO(sentence_id=conn.sentence_id, sentence_type=SentenceType.LAST, content_type=ContentType.ACTION))

                    # 添加到对话历史，让模型理解上下文
                    conn.dialogue.put(Message(role="assistant", content=call_text))
                    return

                # 唤醒词由 ESP 本地检测；detect 到达这里时已经是用户实际语音。
                conn.just_woken_up = True
                enqueue_asr_report(conn, original_text, [])
                # Keep the WebSocket reader responsive while LLM/MCP/TTS run.
                asyncio.create_task(startToChat(conn, original_text))
