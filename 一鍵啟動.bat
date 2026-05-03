@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
title YT Lecture First Principles Summary
echo.
echo === YT Lecture First Principles Summary ===
echo.
cd /d "%~dp0"
python -u yt_summary.py
if errorlevel 1 goto :end

echo.
echo === Generating mermaid mindmap ===
python -u gen_mindmap.py

echo.
echo === Updating INDEX.md ===
python -u gen_index.py

echo.
echo === Updating 心智圖總覽.md ===
python -u gen_overview.py

echo.
echo === Generating Markmap HTML ===
python -u gen_markmap.py

:end
echo.
echo =================================
echo Done. Press any key to close...
pause > nul