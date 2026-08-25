# QQ / 微信消息监听器

这个客户端只读取 Windows 系统通知，不读取或修改 QQ/微信数据库，也不执行自动回复。收到微信或 QQ 桌面通知后，会把发送人和消息摘要转发到小智服务器，由开发板播报。

## 安装

在 Windows 桌面电脑执行：

```powershell
python -m pip install -r desktop_listener/requirements-windows.txt
```

首次运行时，Windows 会请求通知读取权限，需要允许。

## 服务器配置

在服务器的 `data/.config.yaml` 增加一个只在本地保存的令牌：

```yaml
server:
  notification_token: "换成一串随机长令牌"
```

服务器和开发板连接后，桌面端使用开发板的 `device-id`。

## 启动

```powershell
python desktop_listener/listener.py `
  --server-url http://36.212.7.43:8003/api/notify `
  --token "你的notification_token" `
  --device-id "9c:13:9e:8a:0a:b0"
```

桌面电脑必须能访问服务器的 `8003` 端口，服务器防火墙也要允许该端口。需要开机运行时，可以将此命令加入 Windows 任务计划程序。
