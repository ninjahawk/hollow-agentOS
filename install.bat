@echo off
:: Hollow AgentOS - Windows installer
:: Double-click this file to set everything up.
:: Requires: internet connection, ~15 GB free disk space, Windows 10 2004+ or Windows 11

title Hollow AgentOS Setup

:: Request elevation (admin rights needed for Docker/Ollama install)
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -Command "Start-Process cmd -ArgumentList '/c \"%~f0\"' -Verb RunAs"
    exit /b
)

:: Run the PowerShell installer from the same directory as this .bat
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"

:: Keep window open if setup failed so user can read the error
if %errorlevel% neq 0 (
    echo.
    echo Setup encountered an issue. See messages above.
    pause
)
