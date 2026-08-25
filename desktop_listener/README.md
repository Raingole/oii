
# QQ / 微信消息监听器

这个客户端只读取 Windows 系统通知，不读取或修改 QQ/微信数据库，也不执行自动回复。收到微信或 QQ 桌面通知后，会把消息转发到小智服务器，由开发板播报。

## 安装

```powershell
python -m pip install -r desktop_listener/requirements-windows.txt
```

首次运行时，请允许 Windows 通知读取权限。

## 服务器配置

在服务器的 `data/.config.yaml` 增加令牌：

```yaml
server:
  notification_token: "换成一串随机长令牌"
```

## 运行

直接双击打包后的 `xiaozhi-message-listener.exe`，填写服务器地址、令牌和开发板 ID，点击“启动监听”。
