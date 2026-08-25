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
    --name "xiaozhi-message-listener" `
    --distpath $output `
    --workpath "$env:TEMP\xiaozhi-listener-build" `
    --specpath "$env:TEMP\xiaozhi-listener-build" `
    "$PSScriptRoot\app.py"

Write-Host "已生成: $output\xiaozhi-message-listener.exe"
