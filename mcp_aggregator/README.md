# MCP 聚合服务

小智当前只配置一个 `mcp_endpoint`，本服务把多个 WebSocket MCP 后端聚合成一个地址，并按工具名转发调用。

默认后端：

```text
restaurant=ws://127.0.0.1:8766/mcp/
```

后续新增 MCP 时，通过 `MCP_BACKENDS` 配置，格式为逗号分隔的 `名称=WebSocket地址`：

```bash
export MCP_BACKENDS="restaurant=ws://127.0.0.1:8766/mcp/,weather=ws://127.0.0.1:8767/mcp/"
```

如果工具名称冲突，聚合器会自动使用 `名称__工具名`，避免模型调用错服务。

小智配置保持为：

```yaml
mcp_endpoint: "ws://36.212.7.43:8765/mcp/"
```
