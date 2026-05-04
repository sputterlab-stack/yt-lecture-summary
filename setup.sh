#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

echo ""
echo "=== YT 演講摘要 Setup ==="
echo ""

# 偵測 Python
PY=""

if command -v python3 &>/dev/null; then
    PY="python3"
    echo "[找到] python3"
elif command -v python &>/dev/null; then
    VER=$(python --version 2>&1 | grep -oP '(?<=Python )\d')
    if [ "$VER" = "3" ]; then
        PY="python"
        echo "[找到] python (Python 3)"
    else
        echo "[錯誤] 'python' 指向 Python 2，請安裝 Python 3.9+"
        exit 1
    fi
else
    echo "[錯誤] 找不到 Python。請安裝 Python 3.9+："
    echo "  Mac:   brew install python3"
    echo "  Linux: sudo apt install python3 python3-venv"
    exit 1
fi

echo ""
echo "建立 venv..."
$PY -m venv venv

echo ""
echo "升級 pip..."
./venv/bin/python -m pip install --upgrade pip

echo ""
echo "安裝依賴（首次較慢，torch CPU 版約 200MB）..."
./venv/bin/python -m pip install -r requirements.txt

echo ""
echo "==========================================="
echo "✓ Setup 完成"
echo ""
echo "日常使用："
echo "  ./run-ui.sh   啟動 web dashboard"
echo "  ./run-cli.sh  命令列模式"
echo ""
echo "GPU 加速（NVIDIA CUDA）："
echo "  ./venv/bin/python -m pip install --upgrade torch --index-url https://download.pytorch.org/whl/cu121"
echo "==========================================="
echo ""
