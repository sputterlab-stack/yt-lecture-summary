@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
title YT Lecture Summary - Web UI
cd /d "%~dp0"

if not exist "%~dp0venv\Scripts\python.exe" (
    echo [錯誤] 找不到 venv，請先雙擊 setup.bat
    pause
    exit /b 1
)

echo Starting web server (close this window to stop)...
start "" http://localhost:5000
"%~dp0venv\Scripts\python.exe" -u web_server.py

pause
