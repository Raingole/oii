@echo off
setlocal EnableExtensions

set "ROOT=%~dp0"
set "PYTHON=D:\software\miniconda3\envs\xiaozhi-esp32-server\python.exe"

if not exist "%PYTHON%" (
    echo [ERROR] Python environment was not found:
    echo         %PYTHON%
    pause
    exit /b 1
)

if not exist "%ROOT%data\.config.yaml" (
    echo [ERROR] Configuration file was not found:
    echo         %ROOT%data\.config.yaml
    pause
    exit /b 1
)

for %%P in (8000 8003) do (
    netstat -ano | findstr /R /C:":%%P .*LISTENING" >nul
    if not errorlevel 1 (
        echo [ERROR] Port %%P is already in use. Stop the existing server first.
        pause
        exit /b 1
    )
)

set "PATH=D:\software\miniconda3\envs\xiaozhi-esp32-server;D:\software\miniconda3\envs\xiaozhi-esp32-server\Scripts;%PATH%"

start "xiaozhi-server test" /D "%ROOT%" cmd /k "echo Starting xiaozhi-server test... && python.exe app.py"

echo xiaozhi-server test has been started in a separate window.
echo WebSocket: ws://36.212.7.43:8000/xiaozhi/v1/
echo OTA:       http://36.212.7.43:8003/xiaozhi/ota/
echo Vision:    http://36.212.7.43:8003/mcp/vision/explain
exit /b 0
