@echo off
:: Hollow AgentOS — Stop & VRAM clear
:: Stops all containers and unloads models from VRAM.
:: Run launch.bat or open the monitor to resume later.

title Hollow AgentOS — Stopping

cd /d "%~dp0"

echo [..] Stopping Hollow AgentOS...
docker compose stop
if %errorlevel% neq 0 (
    echo [!!] Docker stop failed — is Docker Desktop running?
    pause
    exit /b 1
)
echo [OK] Containers stopped.

echo [..] Unloading models from VRAM...
curl -s -X POST http://localhost:11434/api/generate ^
  -H "Content-Type: application/json" ^
  -d "{\"model\":\"qwen3.5:9b\",\"keep_alive\":0,\"prompt\":\"\"}" >nul 2>&1
curl -s -X POST http://localhost:11434/api/generate ^
  -H "Content-Type: application/json" ^
  -d "{\"model\":\"nomic-embed-text\",\"keep_alive\":0,\"prompt\":\"\"}" >nul 2>&1
echo [OK] VRAM cleared.

echo.
echo Hollow AgentOS is stopped. Agent memory and state are preserved.
echo Run launch.bat (or open the monitor) to resume where you left off.
echo.
pause
