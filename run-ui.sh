#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

[ -f ./venv/bin/python ] || { echo "[錯誤] 找不到 venv，請先跑 ./setup.sh"; exit 1; }

echo "Starting web server (Ctrl+C to stop)..."
(sleep 2 && python3 -m webbrowser http://localhost:5000 2>/dev/null \
    || open http://localhost:5000 2>/dev/null \
    || xdg-open http://localhost:5000 2>/dev/null \
    || true) &
./venv/bin/python -u web_server.py
