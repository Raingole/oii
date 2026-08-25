$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$output = Split-Path -Parent $root
$python = "python"

if (-not (Test-Path -LiteralPath "$PSScriptRoot\app.py")) {
    throw "找不到 desktop_listener/app.py"
}

& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --hidden-import "winrt.windows.foundation" `
    --hidden-import "winrt.windows.foundation.collections" `
    --hidden-import "winrt.windows.system" `
    --hidden-import "winrt.windows.system.remotesystems" `
    --hidden-import "winrt.windows.system.diagnostics" `
    --hidden-import "winrt.windows.storage" `
    --hidden-import "winrt.windows.data.xml.dom" `
    --hidden-import "winrt.windows.applicationmodel" `
    --hidden-import "winrt.windows.applicationmodel.core" `
    --hidden-import "winrt.windows.applicationmodel.activation" `
    --hidden-import "winrt.windows.ui.notifications" `
    --hidden-import "winrt.windows.ui.notifications.management" `
    --name "xiaozhi-message-listener" `
    --distpath $output `
    --workpath "$env:TEMP\xiaozhi-listener-build" `
    --specpath "$env:TEMP\xiaozhi-listener-build" `
    "$PSScriptRoot\app.py"

Write-Host "已生成: $output\xiaozhi-message-listener.exe"
