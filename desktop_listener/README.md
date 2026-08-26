
# 小智 Windows 桌面插件

这个客户端读取 Windows 系统通知并监听服务器控制指令。它不读取或修改 QQ/微信数据库，不执行自动回复，也不执行任意命令行。服务器识别语音意图后，可让它打开允许的软件或网页。

## 安装

```powershell
python -m pip install -r desktop_listener/requirements-windows.txt
```

首次运行时，请允许 Windows 通知读取权限。

## 服务器配置

服务器会把通知发送到当前在线的开发板。桌面控制端点为 `ws://服务器地址:8003/api/desktop`，令牌默认使用 `server.auth_key`，也可以在配置中设置 `server.desktop_token`。不要把令牌提交到公开仓库。

## 运行

直接打开安装后的“小智消息监听”，填写服务器地址，点击“启动监听”。MSIX 版本的配置保存在 `%LOCALAPPDATA%\XiaozhiMessageListener`。

如果普通 exe 报 `0x80004001 尚未实现`，请使用 MSIX 版本。MSIX 需要 Windows SDK 的 `makeappx.exe` 和 `signtool.exe`，在项目目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\desktop_listener\build-msix.ps1
```

先安装生成的 `.cer` 证书，再安装 `.msix`。安装后在 Windows 设置中允许“小智消息监听”访问通知。

## 链路验证

1. 启动服务器，确认日志显示 WebSocket 地址和 HTTP 地址。
2. 让 ESP32 连接服务器并完成绑定。
3. 在 Windows 客户端填写通知 HTTP 地址、控制 WS 地址和桌面令牌，点击“启动监听”。
4. 给微信或 QQ 发送一条消息；服务器日志应显示通知下发，ESP32 应通过 TTS 播报。
5. 对 ESP32 说“打开记事本”或“打开这个网页 https://example.com”，服务器调用 `open_desktop_app` 后，电脑插件会执行打开操作。

也可以直接测试 HTTP 接口：

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8003/api/notify `
  -ContentType 'application/json' -Body '{"text":"通知测试"}'
```
