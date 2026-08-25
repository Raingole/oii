# 独立电脑控制插件

这是与 QQ/微信通知监听器完全分离的 Windows 程序，只监听服务器 WebSocket 指令并打开白名单目标，不申请 Windows 通知权限。

## 安装依赖

```powershell
powershell -ExecutionPolicy Bypass -File .\desktop_control_listener\install-deps.ps1
```

依赖会安装到 `D:\software\xiaozhi-desktop-control\deps`。

## 构建 EXE

需要当前 Python 环境已安装 PyInstaller：

```powershell
powershell -ExecutionPolicy Bypass -File .\desktop_control_listener\build-windows.ps1
```

生成 `D:\software\xiaozhi-desktop-control\xiaozhi-desktop-control.exe`。

启动后填写 `ws://服务器地址:8003/api/desktop` 和服务器的 `server.desktop_token`，然后点击“启动监听”。
