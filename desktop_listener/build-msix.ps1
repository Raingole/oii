$ErrorActionPreference = "Stop"

$sourceRoot = Split-Path -Parent $PSScriptRoot
$outputRoot = Split-Path -Parent $sourceRoot
$exe = Join-Path $outputRoot "xiaozhi-message-listener.exe"
$staging = Join-Path $env:TEMP "xiaozhi-message-listener-msix"
$msix = Join-Path $outputRoot "xiaozhi-message-listener.msix"
$certFile = Join-Path $outputRoot "xiaozhi-message-listener.cer"

if (-not (Test-Path -LiteralPath $exe)) {
    throw "desktop_listener/app.py exe was not found"
}

$makeAppx = Get-ChildItem -Path "C:\Program Files (x86)\Windows Kits\10\bin" -Filter makeappx.exe -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.FullName -match "\\x64\\makeappx\.exe$" } | Sort-Object FullName -Descending | Select-Object -First 1
$signTool = Get-ChildItem -Path "C:\Program Files (x86)\Windows Kits\10\bin" -Filter signtool.exe -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.FullName -match "\\x64\\signtool\.exe$" } | Sort-Object FullName -Descending | Select-Object -First 1
if (-not $makeAppx -or -not $signTool) {
    throw "Windows SDK makeappx.exe or signtool.exe was not found"
}

Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path "$staging\Assets" -Force | Out-Null
Copy-Item -LiteralPath $exe -Destination $staging
Copy-Item -LiteralPath "$PSScriptRoot\package\AppxManifest.xml" -Destination $staging
Copy-Item -LiteralPath "$PSScriptRoot\package\Assets\logo.png" -Destination "$staging\Assets\logo.png"
Remove-Item -LiteralPath $msix -Force -ErrorAction SilentlyContinue

& $makeAppx.FullName pack /d $staging /p $msix /o
if ($LASTEXITCODE -ne 0) {
    throw "makeappx failed with exit code $LASTEXITCODE"
}

$certificate = Get-ChildItem Cert:\CurrentUser\My | Where-Object { $_.Subject -eq "CN=XiaozhiMessageListenerSigner" } | Select-Object -First 1
if (-not $certificate) {
    $certificate = New-SelfSignedCertificate -Type CodeSigningCert -Subject "CN=XiaozhiMessageListenerSigner" -FriendlyName "Xiaozhi Message Listener Development Certificate" -CertStoreLocation Cert:\CurrentUser\My
}
Export-Certificate -Cert $certificate -FilePath $certFile -Type CERT | Out-Null
Import-Certificate -FilePath $certFile -CertStoreLocation Cert:\CurrentUser\TrustedPeople | Out-Null
certutil -user -addstore Root $certFile | Out-Null
& $signTool.FullName sign /fd SHA256 /a /sha1 $certificate.Thumbprint $msix
if ($LASTEXITCODE -ne 0) {
    throw "signtool failed with exit code $LASTEXITCODE"
}

Write-Host "Created and signed: $msix"
Write-Host "Import this certificate before installing: $certFile"
