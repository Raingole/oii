import sys
import uuid
import signal
import asyncio
import json
import hashlib
try:
    from aioconsole import ainput
except ModuleNotFoundError:
    async def ainput(prompt: str = ""):
        """Optional dependency fallback; stdin monitoring is non-critical."""
        return await asyncio.to_thread(input, prompt)
from config.settings import load_config
from config.logger import setup_logging
from core.utils.util import get_local_ip, validate_mcp_endpoint
from core.http_server import SimpleHttpServer
from core.websocket_server import WebSocketServer
from core.utils.util import check_ffmpeg_installed
from core.utils.gc_manager import get_gc_manager
from qq.gateway import QQGateway
from qq.official_gateway import QQOfficialGateway
from controller.tool_catalog import ToolCatalog
from controller.tool_executor import ToolExecutor

TAG = __name__
logger = setup_logging()


async def wait_for_exit() -> None:
    """
    阻塞直到收到 Ctrl‑C / SIGTERM。
    - Unix: 使用 add_signal_handler
    - Windows: 依赖 KeyboardInterrupt
    """
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    if sys.platform != "win32":  # Unix / macOS
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_event.set)
        await stop_event.wait()
    else:
        # Windows：await一个永远pending的fut，
        # 让 KeyboardInterrupt 冒泡到 asyncio.run，以此消除遗留普通线程导致进程退出阻塞的问题
        try:
            await asyncio.Future()
        except KeyboardInterrupt:  # Ctrl‑C
            pass


async def monitor_stdin():
    """监控标准输入，消费回车键"""
    while True:
        await ainput()  # 异步等待输入，消费回车


async def main():
    check_ffmpeg_installed()
    config = await load_config()

    # auth_key优先级：配置文件server.auth_key > manager-api.secret > 自动生成
    # auth_key用于jwt认证，比如视觉分析接口的jwt认证、ota接口的token生成与websocket认证
    # 获取配置文件中的auth_key
    auth_key = config["server"].get("auth_key", "")
    
    # 验证auth_key，无效则尝试使用manager-api.secret
    if not auth_key or len(auth_key) == 0 or "你" in auth_key:
        auth_key = config.get("manager-api", {}).get("secret", "")
        # 验证secret，无效则生成随机密钥
        if not auth_key or len(auth_key) == 0 or "你" in auth_key:
            auth_key = str(uuid.uuid4().hex)
    
    config["server"]["auth_key"] = auth_key

    # 添加 stdin 监控任务
    stdin_task = asyncio.create_task(monitor_stdin())

    # 启动全局GC管理器（5分钟清理一次）
    gc_manager = get_gc_manager(interval_seconds=300)
    await gc_manager.start()

    # 启动 WebSocket 服务器
    ws_server = WebSocketServer(config)
    logger.bind(tag=TAG).info(
        "Cognitive Core status: enabled={} event_router={} action_dispatcher={}",
        bool(ws_server.cognitive_runtime),
        bool(ws_server.event_router),
        bool(ws_server.action_dispatcher),
    )
    ws_task = None
    # 启动 Simple http 服务器
    ota_server = SimpleHttpServer(config, ws_server)
    ws_server.http_server = ota_server
    ws_server.desktop_control = ota_server.desktop_control
    ota_task = None
    qq_gateway = QQGateway(config, ws_server._llm, controller=ws_server)
    ws_server.qq_gateway = qq_gateway
    if ws_server.action_dispatcher is not None:
        async def _qq_executor(action):
            target = str(action.get("target") or action.get("payload", {}).get("target") or "")
            user_id = target.split(":", 1)[-1] if target.startswith("qq:") else target
            if action.get("payload", {}).get("platform") == "official":
                await qq_official_gateway._send_c2c_message(user_id, str(action.get("payload", {}).get("text", "")), str(action.get("payload", {}).get("source_message_id", "")))
                return {"status": "completed", "output_sent": True, "channel": "qq_official"}
            sent = await qq_gateway.send_private_message(user_id, str(action.get("payload", {}).get("text", "")))
            if not sent:
                raise RuntimeError("NapCat message delivery failed")
            return {"status": "completed", "output_sent": True, "channel": "napcat"}
    qq_official_gateway = QQOfficialGateway(config, qq_gateway.agent, logger)
    if ws_server.action_dispatcher is not None:
        class _DynamicProvider:
            def __init__(self, getter, conn_getter=lambda: None): self.getter, self.conn_getter = getter, conn_getter
            async def discover(self):
                obj = self.getter()
                if obj is None: return []
                if hasattr(obj, "get_tools"): return obj.get_tools()
                if hasattr(obj, "get_all_tools"): return obj.get_all_tools()
                if hasattr(obj, "get_available_tools"): return obj.get_available_tools()
                return []
            async def execute(self, name, arguments, context):
                obj = self.getter()
                if obj is None: raise RuntimeError("tool provider offline")
                if hasattr(obj, "execute"):
                    return await obj.execute(self.conn_getter(), name, arguments)
                if hasattr(obj, "execute_tool"): return await obj.execute_tool(name, arguments)
                return await obj.call_tool(name, arguments)
        catalog = ToolCatalog()
        _context = lambda: getattr(qq_gateway.agent, "context", None)
        catalog.register("server_mcp", _DynamicProvider(lambda: getattr(getattr(_context(), "func_handler", None), "server_mcp_executor", None), _context))
        catalog.register("device_mcp", _DynamicProvider(lambda: next((getattr(c, "mcp_client", None) for c in ws_server.connections.values() if getattr(c, "mcp_client", None) is not None), None)))
        catalog.register("plugins", _DynamicProvider(lambda: getattr(getattr(_context(), "func_handler", None), "tool_manager", None), _context))
        catalog.register("desktop", _DynamicProvider(lambda: getattr(ws_server, "desktop_control", None)))
        tool_executor = ToolExecutor(catalog, timeout=float(config.get("cognitive_core", {}).get("action_timeout", 15)))
        async def _esp_executor(action):
            target = str(action.get("target") or action.get("payload", {}).get("device_id") or "")
            conn = ws_server.connections.get(target) or (next(iter(ws_server.connections.values())) if ws_server.connections else None)
            if conn is None: raise RuntimeError("ESP32 body is offline")
            await conn.notify_text(str(action.get("payload", {}).get("text", "")), action_id=str(action.get("action_id", "")))
            # notify_text only queues data in the ESP32 transport. Physical
            # completion must arrive later with the matching action_id.
            payload = action.get("payload", {})
            payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
            return {"device_id": str(conn.device_id), "status": "queued", "output_sent": False, "action_id": action.get("action_id"), "payload_hash": payload_hash}
        ws_server.action_dispatcher.register("send_message", _qq_executor)
        ws_server.action_dispatcher.register("speak", _esp_executor)
        ws_server.action_dispatcher.register("display", _esp_executor)
        async def _mcp_executor(action):
            payload = action.get("payload", {})
            tool_name = str(payload.get("tool", "")); arguments = payload.get("arguments", {})
            result = await tool_executor.execute(tool_name, arguments, {"action": action, "event_id": action.get("event_id", ""), "approval_validated": bool(action.get("_approval_validated"))})
            if not result.get("success"): raise RuntimeError(result.get("error", "tool failed"))
            return result
        ws_server.action_dispatcher.register("call_mcp", _mcp_executor)
    if ws_server.cognitive_runtime:
        await ws_server.cognitive_runtime.recover()
        await ws_server.cognitive_runtime.start_heartbeat()
    ws_task = asyncio.create_task(ws_server.start())
    ota_task = asyncio.create_task(ota_server.start())
    qq_task = asyncio.create_task(qq_gateway.start())
    qq_official_task = asyncio.create_task(qq_official_gateway.start())

    read_config_from_api = config.get("read_config_from_api", False)
    port = int(config["server"].get("http_port", 8003))
    logger.bind(tag=TAG).info(
        "桌面控制WebSocket地址是\tws://{}:{}/api/desktop",
        get_local_ip(),
        port,
    )
    if not read_config_from_api:
        logger.bind(tag=TAG).info(
            "OTA接口是\t\thttp://{}:{}/xiaozhi/ota/",
            get_local_ip(),
            port,
        )
    logger.bind(tag=TAG).info(
        "视觉分析接口是\thttp://{}:{}/mcp/vision/explain",
        get_local_ip(),
        port,
    )
    mcp_endpoint = config.get("mcp_endpoint", None)
    if mcp_endpoint is not None and "你" not in mcp_endpoint:
        # 校验MCP接入点格式
        if validate_mcp_endpoint(mcp_endpoint):
            logger.bind(tag=TAG).info("mcp接入点是\t{}", mcp_endpoint)
            # 将mcp计入点地址转成调用点
            mcp_endpoint = mcp_endpoint.replace("/mcp/", "/call/")
            config["mcp_endpoint"] = mcp_endpoint
        else:
            logger.bind(tag=TAG).error("mcp接入点不符合规范")
            config["mcp_endpoint"] = "你的接入点 websocket地址"

    # 获取WebSocket配置，使用安全的默认值
    websocket_port = 8000
    server_config = config.get("server", {})
    if isinstance(server_config, dict):
        websocket_port = int(server_config.get("port", 8000))

    logger.bind(tag=TAG).info(
        "Websocket地址是\tws://{}:{}/xiaozhi/v1/",
        get_local_ip(),
        websocket_port,
    )

    logger.bind(tag=TAG).info(
        "=======上面的地址是websocket协议地址，请勿用浏览器访问======="
    )
    logger.bind(tag=TAG).info(
        "如想测试websocket请启动digital-human模块，打开浏览器交互测试"
    )
    logger.bind(tag=TAG).info(
        "=============================================================\n"
    )

    try:
        await wait_for_exit()  # 阻塞直到收到退出信号
    except asyncio.CancelledError:
        print("任务被取消，清理资源中...")
    finally:
        # 停止全局GC管理器
        await gc_manager.stop()
        if ws_server.cognitive_runtime:
            await ws_server.cognitive_runtime.shutdown()

        # 取消所有任务（关键修复点）
        stdin_task.cancel()
        ws_task.cancel()
        if ota_task:
            ota_task.cancel()
        qq_task.cancel()
        qq_official_task.cancel()
        await qq_gateway.close()
        await qq_official_gateway.close()

        # 等待任务终止（必须加超时）
        await asyncio.wait(
            [stdin_task, ws_task, ota_task, qq_task, qq_official_task] if ota_task else [stdin_task, ws_task, qq_task, qq_official_task],
            timeout=3.0,
            return_when=asyncio.ALL_COMPLETED,
        )
        ws_server.memory_manager.close()
        print("服务器已关闭，程序退出。")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("手动中断，程序终止。")
