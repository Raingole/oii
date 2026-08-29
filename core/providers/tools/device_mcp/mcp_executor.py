"""设备端MCP工具执行器"""

import json
from typing import Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler
from ..base import ToolType, ToolDefinition, ToolExecutor
from plugins_func.register import Action, ActionResponse
from core.utils.util import sanitize_tool_name
from .mcp_handler import call_mcp_tool


AIR_CONDITIONER_SET_TEMPERATURE = sanitize_tool_name(
    "self.air_conditioner.set_temperature"
)


class DeviceMCPExecutor(ToolExecutor):
    """设备端MCP工具执行器"""

    def __init__(self, conn):
        self.conn = conn

    async def execute(
        self, conn: "ConnectionHandler", tool_name: str, arguments: Dict[str, Any]
    ) -> ActionResponse:
        """执行设备端MCP工具"""
        if not hasattr(conn, "mcp_client") or not conn.mcp_client:
            return ActionResponse(
                action=Action.ERROR,
                response=self._failure_response(
                    tool_name, "设备端MCP客户端未初始化"
                ),
            )

        if not await conn.mcp_client.is_ready():
            return ActionResponse(
                action=Action.ERROR,
                response=self._failure_response(
                    tool_name, "设备端MCP客户端未准备就绪"
                ),
            )

        try:
            # 在工具边界校验并规范化空调温度；无效参数不得发到ESP。
            if tool_name == AIR_CONDITIONER_SET_TEMPERATURE:
                validation_result = self._validate_air_conditioner_temperature(arguments)
                if isinstance(validation_result, str):
                    return ActionResponse(action=Action.ERROR, response=validation_result)
                arguments = validation_result

            args_str = json.dumps(arguments) if arguments else "{}"

            # 调用设备端MCP工具
            result = await call_mcp_tool(conn, conn.mcp_client, tool_name, args_str)

            resultJson = None
            if isinstance(result, str):
                try:
                    resultJson = json.loads(result)
                except Exception as e:
                    pass

            if isinstance(resultJson, dict):
                # 兼容设备返回的标准 action 结果。
                if "action" in resultJson:
                    return ActionResponse(
                        action=Action[resultJson["action"]],
                        response=resultJson.get("response", ""),
                    )

                # 设备可能以 success=false 表示红外发送失败，而不是 MCP
                # 的 isError=true；此时必须阻断成功话术。
                if resultJson.get("success") is False:
                    if tool_name == AIR_CONDITIONER_SET_TEMPERATURE:
                        return ActionResponse(
                            action=Action.ERROR,
                            response="没有成功发送空调红外指令，请检查设备在线状态和红外模块。",
                        )
                    return ActionResponse(
                        action=Action.ERROR,
                        response="设备工具执行失败，请检查设备在线状态。",
                    )

            return ActionResponse(action=Action.REQLLM, result=str(result))

        except ValueError as e:
            return ActionResponse(action=Action.NOTFOUND, response=str(e))
        except Exception as e:
            return ActionResponse(
                action=Action.ERROR,
                response=self._failure_response(tool_name, str(e)),
            )

    @staticmethod
    def _failure_response(tool_name: str, fallback: str) -> str:
        if tool_name == AIR_CONDITIONER_SET_TEMPERATURE:
            return "没有成功发送空调红外指令，请检查设备在线状态和红外模块。"
        return fallback

    @staticmethod
    def _validate_air_conditioner_temperature(
        arguments: Dict[str, Any]
    ) -> Dict[str, Any] | str:
        """Validate the MCP tool argument without parsing natural language."""
        if not isinstance(arguments, dict) or "temperature" not in arguments:
            return "可以，请告诉我目标温度，支持16到30度的制冷设定。"

        raw_temperature = arguments["temperature"]
        if isinstance(raw_temperature, bool):
            return "当前空调只支持制冷模式下16到30度的设温。"

        try:
            numeric_temperature = float(raw_temperature)
        except (TypeError, ValueError):
            return "当前空调只支持制冷模式下16到30度的设温。"

        if not numeric_temperature.is_integer():
            return "当前空调只支持制冷模式下16到30度的设温。"

        temperature = int(numeric_temperature)
        if not 16 <= temperature <= 30:
            return "当前空调只支持制冷模式下16到30度的设温。"

        normalized = dict(arguments)
        normalized["temperature"] = temperature
        return normalized

    def get_tools(self) -> Dict[str, ToolDefinition]:
        """获取所有设备端MCP工具"""
        if not hasattr(self.conn, "mcp_client") or not self.conn.mcp_client:
            return {}

        tools = {}
        mcp_tools = self.conn.mcp_client.get_available_tools()

        for tool in mcp_tools:
            func_def = tool.get("function", {})
            tool_name = func_def.get("name", "")

            if tool_name:
                tools[tool_name] = ToolDefinition(
                    name=tool_name, description=tool, tool_type=ToolType.DEVICE_MCP
                )

        return tools

    def has_tool(self, tool_name: str) -> bool:
        """检查是否有指定的设备端MCP工具"""
        if not hasattr(self.conn, "mcp_client") or not self.conn.mcp_client:
            return False

        return self.conn.mcp_client.has_tool(tool_name)
