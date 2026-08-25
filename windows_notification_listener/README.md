# Stage 1: Windows Notification Listener

This stage only reads Windows Toast notifications and displays normalized records. It does not connect to the server, call TTS, or touch the ESP32 protocol.

## Requirements

- Windows 11
- .NET 8 SDK
- Windows 10/11 SDK 10.0.19041.0 or newer (for `signtool.exe`)

## Build (no Visual Studio required)

```powershell
# 1. Install the .NET 8 SDK locally (already at D:\software\xiaozhi-notification-listener\dotnet)
dotnet restore .\windows_notification_listener\WindowsNotificationListener.csproj -r win-x64

# 2. Build and generate the MSIX
dotnet build .\windows_notification_listener\WindowsNotificationListener.csproj -c Release -r win-x64 `
  -p:GenerateAppxPackageOnBuild=true -p:AppxPackageDir=D:\software\xiaozhi-notification-listener\msix\

# 3. Sign with a self-signed cert whose subject matches the manifest Publisher
signtool sign /fd SHA256 /a /sha1 <cert-thumbprint> <path-to>.msix
```

Signed output is copied to:

```text
D:\software\xiaozhi-notification-listener\release\WindowsNotificationListener_1.0.0.0_x64.msix
D:\software\xiaozhi-notification-listener\xiaozhi-notification-listener.cer
```

## Install

1. Import the certificate: double-click `xiaozhi-notification-listener.cer` → install to `Local Machine` → `Trusted People`.
2. Double-click the `.msix` and install. The MSIX already bundles the required Windows App SDK runtime in `Dependencies`.

On first launch click `Request notification access`. Windows must report `Allowed` before listening starts.

Open `WindowsNotificationListener.sln` in Visual Studio, select `x64`, then run the packaged MSIX project. On first launch click `Request notification access`. Windows must report `Allowed` before listening starts.

The app writes normalized notification records to the UI and to `%LOCALAPPDATA%\XiaozhiNotificationListener\listener.log`.

## Current scope

- Uses `UserNotificationListener`, not OCR, screenshots, Notification Center scraping, or UI Automation.
- Parses all `ToastGeneric` text nodes.
- Captures app name, app id, title, content, notification id, and creation time.
- Supports allow and deny app filters.
- Deduplicates by notification id and a fallback composite key for 30 minutes.

## Expected log

```text
[WINDOWS][INFO] Notification listener permission: Allowed
[WINDOWS][INFO] Notification received App=微信 Title=张三 Content=晚上一起吃饭吗？ Id=123 Time=2026-08-25T21:00:00+08:00
```
