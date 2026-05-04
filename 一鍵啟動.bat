@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
title YT Lecture First Principles Summary
cd /d "%~dp0"

if not exist "%~dp0venv\Scripts\python.exe" (
    echo [錯誤] 找不到 venv，請先雙擊 setup.bat
    pause
    exit /b 1
)

echo.
echo === YT Lecture First Principles Summary ===
echo.
"%~dp0venv\Scripts\python.exe" -u yt_summary.py
if errorlevel 1 goto :end

echo.
echo === Generating mermaid mindmap ===
"%~dp0venv\Scripts\python.exe" -u gen_mindmap.py

echo.
echo === Updating INDEX.md ===
"%~dp0venv\Scripts\python.exe" -u gen_index.py

echo.
echo === Updating 心智圖總覽.md ===
"%~dp0venv\Scripts\python.exe" -u gen_overview.py

echo.
echo === Generating Markmap HTML ===
"%~dp0venv\Scripts\python.exe" -u gen_markmap.py

:end
echo.
echo =================================
echo Done. Press any key to close...
pause > nul