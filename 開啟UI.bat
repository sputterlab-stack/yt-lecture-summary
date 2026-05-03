@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
title YT Lecture Summary - Web UI
cd /d "%~dp0"

echo Starting web server (close this window to stop)...
start "" http://localhost:5000
python -u web_server.py

pause
