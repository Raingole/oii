$ErrorActionPreference = "Stop"
$dependencyRoot = "D:\software\xiaozhi-desktop-control\deps"
if (-not (Test-Path -LiteralPath "D:\software")) {
    throw "D:\software does not exist"
}
New-Item -ItemType Directory -Path $dependencyRoot -Force | Out-Null
python -m pip install --upgrade --target $dependencyRoot -r (Join-Path $PSScriptRoot "requirements.txt")
Write-Host "Dependencies installed to $dependencyRoot"
