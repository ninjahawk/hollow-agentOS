@echo off
:: HollowAgentOS — one-click launcher
:: Builds and starts the full AgentOS stack from local source.
:: Requirements: Docker Desktop (running), Ollama with qwen3.5:9b-gpu
:: After setup: Dashboard at http://localhost:7778

title HollowAgentOS

setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo   ╔══════════════════════════════════════════════════════╗
echo   ║              HollowAgentOS  Launcher                 ║
echo   ╚══════════════════════════════════════════════════════╝
echo.

:: ── 1. Check Docker Desktop is running ────────────────────────────────────────
echo   [..] Checking Docker...
docker info >nul 2>&1
if %errorlevel% neq 0 (
    echo   [!!] Docker Desktop is not running.
    echo        Please start Docker Desktop, wait for it to fully load,
    echo        then run this bat file again.
    echo.
    pause
    exit /b 1
)
echo   [ok] Docker is running.

:: ── 2. Copy config if first run ───────────────────────────────────────────────
if not exist "%~dp0config.json" (
    if exist "%~dp0config.example.json" (
        copy "%~dp0config.example.json" "%~dp0config.json" >nul
        echo   [ok] Created config.json from config.example.json
    )
)

:: ── 3. Build image from local source and start all services ───────────────────
echo   [..] Building HollowAgentOS from source (first run takes 2-5 min)...
docker compose up -d --build
if %errorlevel% neq 0 (
    echo   [XX] docker compose failed. Check output above.
    pause
    exit /b 1
)
echo   [ok] All containers started.

:: ── 4. Check Ollama and required model ───────────────────────────────────────
echo   [..] Checking Ollama (required for agent intelligence)...
curl -s http://localhost:11434/api/tags >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo   [!!] Ollama is not running on localhost:11434.
    echo        Agents will not be able to think until Ollama is running.
    echo.
    echo        Install: https://ollama.ai
    echo        Then run: ollama pull qwen3.5:9b-gpu
    echo.
) else (
    echo   [ok] Ollama is running.
    echo   [..] Checking for qwen3.5:9b-gpu model...
    ollama list 2>nul | findstr "qwen3.5" >nul
    if %errorlevel% neq 0 (
        echo   [..] Pulling qwen3.5:9b-gpu (this may take a while on first run)...
        ollama pull qwen3.5:9b-gpu
    ) else (
        echo   [ok] qwen3.5:9b-gpu model is ready.
    )
)

:: ── 5. Done ───────────────────────────────────────────────────────────────────
echo.
echo   ╔══════════════════════════════════════════════════════╗
echo   ║   HollowAgentOS is running!                         ║
echo   ║                                                      ║
echo   ║   Dashboard:  http://localhost:7778                  ║
echo   ║   API:        http://localhost:7777                  ║
echo   ║   API docs:   http://localhost:7777/docs             ║
echo   ╚══════════════════════════════════════════════════════╝
echo.
echo   Press any key to open the live monitor (requires Python)...
pause >nul

:: Launch live monitor if Python is available
python --version >nul 2>&1
if %errorlevel% equ 0 (
    python -c "import textual" >nul 2>&1
    if %errorlevel% neq 0 (
        python -m pip install -q -r "%~dp0requirements-monitor.txt"
    )
    python "%~dp0monitor.py"
) else (
    echo   [!!] Python not found -- skipping live monitor.
    echo        Install Python 3.12+ to use the TUI monitor.
    pause
)

endlocal
