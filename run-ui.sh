#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

[ -f ./venv/bin/python ] || { echo "[錯誤] 找不到 venv，請先跑 ./setup.sh"; exit 1; }

echo "Starting web server (Ctrl+C to stop)..."
# 等 port 通了才開瀏覽器（原本是 sleep 2 猜時間，啟動一變慢就開到還沒起來的 server）
./venv/bin/python -u wait_and_open.py &
./venv/bin/python -u web_server.py
