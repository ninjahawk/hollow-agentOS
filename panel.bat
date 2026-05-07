@echo off
REM Hollow AgentOS Control Panel launcher.
REM Double-click to open. Requires pywebview (auto-installs on first run).
cd /d "%~dp0"
python -c "import webview" 2>nul
if errorlevel 1 (
    echo Installing pywebview...
    pip install pywebview
)
start "" pythonw panel.py
