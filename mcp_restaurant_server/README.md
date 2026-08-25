# IP 餐馆 MCP 服务

这个服务为小智提供三个 MCP 工具：`find_nearby_restaurants`、`recommend_meal` 和 `estimate_route`。前两个用于查询和随机推荐餐馆，后者用于查询从固定位置到目标地点的距离和预计时间。高德 Key 从 `data/.config.yaml` 读取。

## 需要提供

- 高德地图 Web 服务 API Key，申请地址：<https://console.amap.com/dev/key/app>，填写到 `data/.config.yaml` 的 `mcp.restaurant.amap_key`
- 如果要从另一台机器连接，需要让 MCP 服务端口可以被小智服务器访问
- 可选的 MCP Token，用于保护 WebSocket 接口

默认固定使用重庆理工大学两江校区学生公寓坐标，不使用公网 IP 定位。也可以在 `data/.config.yaml` 配置 `mcp.restaurant.location` 覆盖默认坐标。

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
