@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "DISPLAY_API_KEY=change-this-before-deploy"
if exist ".env" for /f "tokens=1,* delims==" %%A in ('findstr /b "HIGHGROUND_API_KEY=" ".env"') do set "DISPLAY_API_KEY=%%B"

echo [HighGround] 正在检查 Docker...
docker info >nul 2>nul
if errorlevel 1 (
  echo [错误] Docker Desktop 尚未启动。请先打开 Docker Desktop，再双击本文件。
  pause
  exit /b 1
)

echo [HighGround] 正在启动后端、数据库和网页...
docker compose up -d --build
if errorlevel 1 (
  echo [错误] 启动失败，请保留此窗口中的错误信息。
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "$deadline=(Get-Date).AddSeconds(30); do { try { $response=Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8000/healthz' -TimeoutSec 2; if ($response.StatusCode -eq 200) { exit 0 } } catch {}; Start-Sleep -Milliseconds 500 } while ((Get-Date) -lt $deadline); exit 1"
if errorlevel 1 (
  echo [错误] 服务已启动，但健康检查未在 30 秒内通过。
  docker compose logs --tail 40
  pause
  exit /b 1
)

echo.
echo [成功] 高地 AI 已启动。
echo 地址: http://127.0.0.1:8000/
echo 本地演示 X-API-Key: %DISPLAY_API_KEY%
echo.
if /i "%~1"=="--no-open" exit /b 0
start "" "http://127.0.0.1:8000/"
pause
