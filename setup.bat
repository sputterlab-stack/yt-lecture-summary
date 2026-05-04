@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
title YT 演講摘要 - Setup
cd /d "%~dp0"

echo.
echo === YT 演講摘要 Setup ===
echo.

:: 偵測 Python（按優先序）
set PY=

if exist "%USERPROFILE%\anaconda3\python.exe" (
    set PY="%USERPROFILE%\anaconda3\python.exe"
    echo [找到] anaconda3 Python: %USERPROFILE%\anaconda3\python.exe
    goto :found
)

if exist "%USERPROFILE%\miniconda3\python.exe" (
    set PY="%USERPROFILE%\miniconda3\python.exe"
    echo [找到] miniconda3 Python: %USERPROFILE%\miniconda3\python.exe
    goto :found
)

where python >nul 2>&1
if %errorlevel% == 0 (
    set PY=python
    echo [找到] PATH Python: python
    goto :found
)

where python3 >nul 2>&1
if %errorlevel% == 0 (
    set PY=python3
    echo [找到] PATH Python: python3
    goto :found
)

py -3 --version >nul 2>&1
if %errorlevel% == 0 (
    set PY=py -3
    echo [找到] py launcher: py -3
    goto :found
)

echo.
echo [錯誤] 找不到 Python。
echo 請安裝 Python 3.9 以上版本（建議 Anaconda）：
echo   https://www.anaconda.com/download
echo 安裝後重開此視窗重試。
echo.
pause
exit /b 1

:found
echo.
echo 建立 venv...
%PY% -m venv venv
if errorlevel 1 (
    echo [錯誤] 建立 venv 失敗，請確認 Python 版本 ^>= 3.9
    pause
    exit /b 1
)

echo.
echo 升級 pip...
venv\Scripts\python.exe -m pip install --upgrade pip

echo.
echo 安裝依賴（首次較慢，torch CPU 版約 200MB）...
venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
    echo [錯誤] pip install 失敗，請查看上方錯誤訊息
    pause
    exit /b 1
)

echo.
echo ===========================================
echo ^✓ Setup 完成
echo.
echo 日常使用：
echo   雙擊 開啟UI.bat 啟動 web dashboard
echo   雙擊 一鍵啟動.bat 命令列模式
echo.
echo GPU 加速（NVIDIA CUDA）：
echo   venv\Scripts\python.exe -m pip install --upgrade torch --index-url https://download.pytorch.org/whl/cu121
echo ===========================================
echo.
pause
