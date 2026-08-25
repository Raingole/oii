# IP 餐馆 MCP 服务

这个服务为小智提供两个 MCP 工具：`find_nearby_restaurants` 和 `recommend_meal`。它会检测服务所在机器的公网 IP，通过 IP 定位获取大致位置，再调用高德地图查询周边餐馆；`recommend_meal` 会从搜索结果中随机推荐一家。高德 Key 从 `data/.config.yaml` 读取。

## 需要提供

- 高德地图 Web 服务 API Key，申请地址：<https://console.amap.com/dev/key/app>，填写到 `data/.config.yaml` 的 `mcp.restaurant.amap_key`
- 如果要从另一台机器连接，需要让 MCP 服务端口可以被小智服务器访问
- 可选的 MCP Token，用于保护 WebSocket 接口

IP 定位使用 ipify 和 ipinfo，默认不需要额外 Key。IP 定位一般只能到城市或区域，不能替代 GPS。

## 启动

在项目根目录执行：

```powershell
python mcp_restaurant_server/server.py
```

默认监听 `8766` 端口。通常由 MCP 聚合器转发，不需要直接对外开放这个端口。

也可以在项目根目录使用统一启动脚本：

```bash
chmod +x start-all.sh
./start-all.sh
```

脚本会先启动本 MCP 服务，再启动小智主服务。后续新增 MCP 时，在 `start-all.sh` 中增加一行 `start_mcp`，每个服务使用不同端口。

## 小智配置

在 `data/.config.yaml` 添加：

```yaml
mcp_endpoint: "ws://MCP服务器IP:8765/mcp/?token=自定义长Token"

selected_module:
  LLM: AliLLM
  TTS: AliBLTTS
  Intent: function_call
```

启动后查看日志，应该能看到：

```text
MCP接入点工具获取完成，共 1 个工具
```
