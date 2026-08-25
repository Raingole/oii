$ErrorActionPreference = "Stop"
$dependencyRoot = "D:\software\xiaozhi-desktop-control\deps"
$outputRoot = "D:\software\xiaozhi-desktop-control"
if (-not (Test-Path -LiteralPath $dependencyRoot)) {
    throw "Dependency directory does not exist; run install-deps.ps1 first"
}
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --paths $dependencyRoot `
    --name "xiaozhi-desktop-control" `
    --distpath $outputRoot `
    --workpath "$env:TEMP\xiaozhi-desktop-control-build" `
    --specpath "$env:TEMP\xiaozhi-desktop-control-build" `
    "$PSScriptRoot\app.py"
Write-Host "EXE created: $outputRoot\xiaozhi-desktop-control.exe"
