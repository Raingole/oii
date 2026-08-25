$ErrorActionPreference = "Stop"

$sourceRoot = Split-Path -Parent $PSScriptRoot
$outputRoot = Split-Path -Parent $sourceRoot
$exe = Join-Path $outputRoot "xiaozhi-message-listener.exe"
$staging = Join-Path $env:TEMP "xiaozhi-message-listener-msix"
$msix = Join-Path $outputRoot "xiaozhi-message-listener.msix"
$certFile = Join-Path $outputRoot "xiaozhi-message-listener.cer"

if (-not (Test-Path -LiteralPath $exe)) {
    throw "找不到 $exe，请先运行 build-windows.ps1 生成 exe"
}

$makeAppx = Get-ChildItem -Path "C:\Program Files (x86)\Windows Kits\10\bin" -Filter makeappx.exe -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.FullName -match "\\x64\\makeappx\.exe$" } | Sort-Object FullName -Descending | Select-Object -First 1
$signTool = Get-ChildItem -Path "C:\Program Files (x86)\Windows Kits\10\bin" -Filter signtool.exe -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.FullName -match "\\x64\\signtool\.exe$" } | Sort-Object FullName -Descending | Select-Object -First 1
if (-not $makeAppx -or -not $signTool) {
    throw "未找到 Windows SDK 的 makeappx.exe 或 signtool.exe"
}

Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path "$staging\Assets" -Force | Out-Null
Copy-Item -LiteralPath $exe -Destination $staging
Copy-Item -LiteralPath "$PSScriptRoot\package\AppxManifest.xml" -Destination $staging
Copy-Item -LiteralPath "$PSScriptRoot\package\Assets\logo.svg" -Destination "$staging\Assets\logo.svg"
Remove-Item -LiteralPath $msix -Force -ErrorAction SilentlyContinue

& $makeAppx.FullName pack /d $staging /p $msix /o

$certificate = Get-ChildItem Cert:\CurrentUser\My | Where-Object { $_.Subject -eq "CN=XiaozhiMessageListener" } | Select-Object -First 1
if (-not $certificate) {
    $certificate = New-SelfSignedCertificate -Type Custom -Subject "CN=XiaozhiMessageListener" -KeyUsage DigitalSignature -FriendlyName "小智消息监听开发证书" -CertStoreLocation Cert:\CurrentUser\My
}
Export-Certificate -Cert $certificate -FilePath $certFile -Type CERT | Out-Null
Import-Certificate -FilePath $certFile -CertStoreLocation Cert:\CurrentUser\TrustedPeople | Out-Null
& $signTool.FullName sign /fd SHA256 /a /sha1 $certificate.Thumbprint $msix

Write-Host "已生成并签名: $msix"
Write-Host "安装前请双击导入证书: $certFile"
