@echo off
:: Hollow AgentOS — live monitor launcher
:: Double-click to open the TUI. The AgentOS stack must already be running.
:: Run install.bat first if you haven't set up yet.

title Hollow AgentOS

cd /d "%~dp0"
set HOLLOW_DIR=%~dp0

:: Strip trailing backslash from HOLLOW_DIR
if "%HOLLOW_DIR:~-1%"=="\" set HOLLOW_DIR=%HOLLOW_DIR:~0,-1%

:: Check Docker is running
docker info >nul 2>&1
if %errorlevel% neq 0 (
    echo [!!] Docker is not running. Starting Docker Desktop...
    start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
    echo [..] Waiting for Docker to start...
    timeout /t 15 /nobreak >nul
)

:: Start containers if not already running
docker compose ps --services --filter status=running 2>nul | findstr "api" >nul
if %errorlevel% neq 0 (
    echo [..] Starting AgentOS containers...
    docker compose up -d --pull missing
    timeout /t 5 /nobreak >nul
)

:: Find Python
set PYTHON=
for %%P in (python python3) do (
    if "!PYTHON!"=="" (
        %%P --version >nul 2>&1 && set PYTHON=%%P
    )
)
setlocal enabledelayedexpansion
set PYTHON=
for %%P in (python python3) do (
    if "!PYTHON!"=="" (
        %%P --version >nul 2>&1
        if !errorlevel!==0 set PYTHON=%%P
    )
)

if "!PYTHON!"=="" (
    echo [XX] Python not found. Install Python 3.12+ from https://python.org
    pause
    exit /b 1
)

:: Launch monitor (no extra dependencies required)
!PYTHON! "%~dp0thoughts.py"
endlocal
