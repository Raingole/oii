# OII 中控

本项目是小智中控服务，统一提供 QQ 对话、长期记忆、MCP、设备控制和桌面控制能力。

## 启动

```bash
./setup-and-start.sh
```

服务器运行前会读取：

```text
data/.config.yaml
```

该文件包含本机密钥和部署配置，不提交到 Git。

## QQ 接入

### NapCat / OneBot

NapCat 连接中控的反向 WebSocket：

```text
ws://服务器地址:8082/onebot
```

中控接收 OneBot 事件，并通过 NapCat HTTP API 发送消息：

```text
http://127.0.0.1:3000
```

配置示例：

```yaml
qq:
  enabled: true
  onebot_ws_host: 0.0.0.0
  onebot_ws_port: 8082
  onebot_ws_path: /onebot
  onebot_ws_token: "设置一个安全Token"
  napcat_http_url: http://127.0.0.1:3000
  napcat_http_token: "NapCat API Token"
  owner_qq: "你的QQ号"
```

### QQ 官方机器人

中控可以直接连接 QQ 官方机器人 Gateway，不需要 AstrBot，也不需要 NapCat：

```text
中控 → QQ 官方 Gateway WebSocket：接收事件
中控 → QQ 官方 OpenAPI：发送回复
```

配置示例：

```yaml
qq_official:
  enabled: true
  app_id: "QQ开放平台AppID"
  app_secret: "QQ开放平台AppSecret"
  memory_identity: "你的NapCat QQ号"
  intents: 33554432
```

`memory_identity` 用于让 QQ 官方机器人和 NapCat 共用同一个记忆会话。建议把 AppSecret 放入环境变量：

```bash
export QQ_OFFICIAL_APP_ID="你的AppID"
export QQ_OFFICIAL_APP_SECRET="你的AppSecret"
```

QQ 官方机器人需要在 QQ 开放平台开启消息列表单聊权限。官方机器人返回的是用户 `openid`，中控通过 `memory_identity` 将其映射到统一记忆身份。

## 端口

| 端口 | 用途 |
| --- | --- |
| 8000 | 小智设备 WebSocket |
| 8005 | OTA、HTTP 接口、桌面控制 WebSocket |
| 8082 | NapCat OneBot 反向 WebSocket |
| 3000 | NapCat 本地 HTTP API，由 NapCat 提供 |

桌面控制地址：

```text
ws://服务器地址:8005/api/desktop
```

桌面控制客户端需要使用服务器 `server.desktop_token` 作为 Token。客户端本地配置通常位于：

```text
C:\Users\你的用户名\AppData\Local\XiaozhiDesktopControl\config.json
```

## 桌面控制 Debug

测试消息：

```text
请调用电脑控制功能，打开计算器
```

日志链路：

```text
QQ pipeline dispatch
QQ agent start
Pipeline route=desktop_direct
Tool execution start: name=open_desktop_app
desktop command sent: clients=1
桌面插件执行结果: ok
Tool execution finish
QQ agent finish
QQ pipeline completed
```

根据缺失的日志阶段，可以判断问题发生在消息接收、工具调用、桌面命令发送或 Windows 客户端执行环节。

## 更新

```bash
git pull origin main
./setup-and-start.sh
```

不要把 `data/.config.yaml`、AppSecret、API Key、邮箱授权码或其他 Token 提交到 Git。
