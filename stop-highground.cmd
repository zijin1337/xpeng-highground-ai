@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo [HighGround] 正在停止服务...
docker compose down
if errorlevel 1 (
  echo [错误] 停止失败，请确认 Docker Desktop 正在运行。
  pause
  exit /b 1
)

echo [成功] 高地 AI 已停止。
pause
