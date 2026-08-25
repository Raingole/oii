
# QQ / 微信消息监听器

这个客户端只读取 Windows 系统通知，不读取或修改 QQ/微信数据库，也不执行自动回复。收到微信或 QQ 桌面通知后，会把消息转发到小智服务器，由开发板播报。

## 安装

```powershell
python -m pip install -r desktop_listener/requirements-windows.txt
```

首次运行时，请允许 Windows 通知读取权限。

## 服务器配置

服务器会把通知发送到当前唯一在线的开发板，不需要令牌或设备 ID。

## 运行

直接打开安装后的“小智消息监听”，填写服务器地址，点击“启动监听”。MSIX 版本的配置保存在 `%LOCALAPPDATA%\XiaozhiMessageListener`。

如果普通 exe 报 `0x80004001 尚未实现`，请使用 MSIX 版本。MSIX 需要 Windows SDK 的 `makeappx.exe` 和 `signtool.exe`，在项目目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\desktop_listener\build-msix.ps1
```

先安装生成的 `.cer` 证书，再安装 `.msix`。安装后在 Windows 设置中允许“小智消息监听”访问通知。
