@echo off
:: Hollow AgentOS — Snapshot Restore
:: Loads the bundled Cedar/Helix/Titan agent state into a running container.
:: Run AFTER install.bat has completed and the container is up.

title Hollow AgentOS — Restore Snapshot

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0restore.ps1"

if %errorlevel% neq 0 (
    echo.
    echo Restore encountered an issue. See messages above.
    pause
)
