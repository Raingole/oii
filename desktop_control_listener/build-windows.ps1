$ErrorActionPreference = "Stop"
$outputRoot = "D:\software\xiaozhi-desktop-control"
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
python -m pip install -r "$PSScriptRoot\requirements.txt"
python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name "xiaozhi-desktop-control" `
    --distpath $outputRoot `
    --workpath "$env:TEMP\xiaozhi-desktop-control-build" `
    --specpath "$env:TEMP\xiaozhi-desktop-control-build" `
    "$PSScriptRoot\app.py"
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE. Close the running EXE and retry."
}
Write-Host "EXE created: $outputRoot\xiaozhi-desktop-control.exe"
