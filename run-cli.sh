#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

[ -f ./venv/bin/python ] || { echo "[錯誤] 找不到 venv，請先跑 ./setup.sh"; exit 1; }

echo ""
echo "=== YT Lecture First Principles Summary ==="
echo ""
./venv/bin/python -u yt_summary.py
echo ""
echo "=== Generating mermaid mindmap ==="
./venv/bin/python -u gen_mindmap.py
echo ""
echo "=== Updating INDEX.md ==="
./venv/bin/python -u gen_index.py
echo ""
echo "=== Updating 心智圖總覽.md ==="
./venv/bin/python -u gen_overview.py
echo ""
echo "=== Generating Markmap HTML ==="
./venv/bin/python -u gen_markmap.py
echo ""
echo "================================="
echo "Done."
