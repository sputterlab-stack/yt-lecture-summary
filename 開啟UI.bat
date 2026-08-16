@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
title YT Lecture Summary - Web UI
cd /d "%~dp0"

if not exist "%~dp0venv\Scripts\python.exe" (
    echo [ERROR] venv not found. Please double-click setup.bat first.
    pause
    exit /b 1
)

echo Starting web server (close this window to stop)...
rem Open the browser only after the port answers - see wait_and_open.py
start "" /b "%~dp0venv\Scripts\python.exe" -u wait_and_open.py
"%~dp0venv\Scripts\python.exe" -u web_server.py

pause
