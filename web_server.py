# Flask web server for YT 演講摘要 dashboard
# Endpoints:
#   GET  /                  -> dashboard HTML (server-side rendered)
#   POST /convert           -> {url} -> starts background chain, returns {task_id}
#   GET  /status/<task_id>  -> chain status JSON
#   GET  /api/summaries     -> JSON list of all summaries (for post-chain reload)

import re
import sys
import threading
import uuid
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml
from flask import Flask, jsonify, render_template, request

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
SUMMARIES_DIR = BASE_DIR / "outputs" / "summaries"
MARKMAP_DIR = BASE_DIR / "outputs" / "markmap"
EXCLUDE = {"INDEX.md", "心智圖總覽.md"}

CATEGORY_ORDER = [
    "投資/經濟",
    "AI/科技",
    "演講/溝通",
    "思想/個人成長",
    "健康/科學",
]

# ---------------------------------------------------------------------------
# Task state
# ---------------------------------------------------------------------------
_TASKS: dict[str, dict] = {}
_CHAIN_LOCK = threading.Lock()

TOTAL_STEPS = 9
STEP_NAMES = [
    "下載 YouTube 音訊",
    "Whisper 轉逐字稿",
    "DeepSeek 第一性原理摘要",
    "寫入 .md / .srt",
    "產 mermaid 心智圖（.mmd）",
    "更新 INDEX.md",
    "拼接心智圖總覽",
    "產 Markmap 互動式 HTML",
    "完成",
]

# yt_summary.py emits "步驟 N/4: title" — map its 4 steps to our 9-step model
_YT_STEP_MAP = {
    1: 1,  # 下載 YouTube 音訊   → step 1
    2: 2,  # Whisper 轉文字      → step 2
    3: 3,  # DeepSeek 摘要       → step 3
    4: 4,  # 寫入檔案            → step 4
}

# ---------------------------------------------------------------------------
# Frontmatter / content helpers
# ---------------------------------------------------------------------------

def parse_frontmatter(path: Path) -> dict:
    """Return frontmatter dict from a .md file, or empty dict on failure."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end == -1:
        return {}
    try:
        return yaml.safe_load(text[3:end]) or {}
    except yaml.YAMLError:
        return {}


def extract_elevator_pitch(path: Path) -> str:
    """Return the first > blockquote line after the # title, or ''."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return ""
    # Strip frontmatter
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            text = text[end + 3:]
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(">"):
            return stripped.lstrip("> ").strip()
    return ""


def collect_summaries() -> list[dict]:
    """Collect all .md summaries with frontmatter + elevator pitch."""
    entries = []
    for md in sorted(SUMMARIES_DIR.glob("*.md")):
        if md.name in EXCLUDE:
            continue
        fm = parse_frontmatter(md)
        markmap_html = MARKMAP_DIR / (md.stem + ".html")
        entries.append({
            "filename": md.stem,
            "path": md.name,
            "category": fm.get("category") or "未分類",
            "tags": fm.get("tags") or [],
            "speaker": fm.get("speaker") or "未知",
            "duration": fm.get("duration") or "-",
            "generated_at": str(fm.get("generated_at") or ""),
            "source": fm.get("source") or "",
            "yt_title": fm.get("yt_title") or md.stem,
            "elevator_pitch": extract_elevator_pitch(md),
            "markmap_url": f"/outputs/markmap/{md.stem}.html" if markmap_html.exists() else None,
        })
    return entries


def group_summaries(entries: list[dict]) -> list[dict]:
    """Return entries grouped in CATEGORY_ORDER for template use."""
    grouped: dict[str, list[dict]] = {}
    for e in entries:
        grouped.setdefault(e["category"], []).append(e)

    known = [c for c in CATEGORY_ORDER if c in grouped]
    extra = sorted(c for c in grouped if c not in CATEGORY_ORDER and c != "未分類")
    uncategorised = ["未分類"] if "未分類" in grouped else []
    order = known + extra + uncategorised

    result = []
    for cat in order:
        items = grouped[cat]
        items.sort(key=lambda x: x["generated_at"], reverse=True)
        result.append({"category": cat, "entries": items})
    return result


# ---------------------------------------------------------------------------
# Background chain runner
# ---------------------------------------------------------------------------

_STEP_RE = re.compile(r"步驟\s*(\d+)/\d+[:：]\s*(.*)")


def _run_chain(task_id: str, url: str) -> None:
    task = _TASKS[task_id]

    def set_step(idx: int) -> None:
        """idx is 1-based."""
        task["step_index"] = idx
        task["current_step"] = STEP_NAMES[idx - 1] if idx <= TOTAL_STEPS else STEP_NAMES[-1]

    def fail(msg: str) -> None:
        task["status"] = "error"
        task["error"] = msg
        task["finished_at"] = datetime.now(timezone.utc).isoformat()

    try:
        set_step(1)
        task["status"] = "running"

        # ------------------------------------------------------------------
        # Step 1–4: yt_summary.py
        # ------------------------------------------------------------------
        proc = subprocess.Popen(
            [sys.executable, "-u", str(BASE_DIR / "yt_summary.py"), url],
            cwd=str(BASE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            text=True,
        )

        stderr_lines: list[str] = []

        # Read stderr in a separate thread so it doesn't block stdout reading
        def _drain_stderr() -> None:
            for line in proc.stderr:
                stderr_lines.append(line)

        stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
        stderr_thread.start()

        for line in proc.stdout:
            m = _STEP_RE.search(line)
            if m:
                yt_step = int(m.group(1))
                mapped = _YT_STEP_MAP.get(yt_step, yt_step)
                set_step(mapped)

        proc.wait()
        stderr_thread.join(timeout=5)

        if proc.returncode != 0:
            err_tail = "".join(stderr_lines[-20:]).strip()
            fail(f"yt_summary.py 失敗（returncode={proc.returncode}）\n{err_tail}")
            return

        # ------------------------------------------------------------------
        # Step 5: gen_mindmap.py
        # ------------------------------------------------------------------
        set_step(5)
        r = subprocess.run(
            [sys.executable, "-u", str(BASE_DIR / "gen_mindmap.py")],
            cwd=str(BASE_DIR),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if r.returncode != 0:
            fail(f"gen_mindmap.py 失敗\n{r.stderr[-800:]}")
            return

        # ------------------------------------------------------------------
        # Step 6: gen_index.py
        # ------------------------------------------------------------------
        set_step(6)
        r = subprocess.run(
            [sys.executable, "-u", str(BASE_DIR / "gen_index.py")],
            cwd=str(BASE_DIR),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if r.returncode != 0:
            fail(f"gen_index.py 失敗\n{r.stderr[-800:]}")
            return

        # ------------------------------------------------------------------
        # Step 7: gen_overview.py
        # ------------------------------------------------------------------
        set_step(7)
        r = subprocess.run(
            [sys.executable, "-u", str(BASE_DIR / "gen_overview.py")],
            cwd=str(BASE_DIR),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if r.returncode != 0:
            fail(f"gen_overview.py 失敗\n{r.stderr[-800:]}")
            return

        # ------------------------------------------------------------------
        # Step 8: gen_markmap.py
        # ------------------------------------------------------------------
        set_step(8)
        r = subprocess.run(
            [sys.executable, "-u", str(BASE_DIR / "gen_markmap.py")],
            cwd=str(BASE_DIR),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if r.returncode != 0:
            fail(f"gen_markmap.py 失敗\n{r.stderr[-800:]}")
            return

        # ------------------------------------------------------------------
        # Done
        # ------------------------------------------------------------------
        set_step(9)
        task["status"] = "done"
        task["finished_at"] = datetime.now(timezone.utc).isoformat()

    except Exception as exc:
        fail(f"未預期錯誤：{exc}")
    finally:
        _CHAIN_LOCK.release()


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__)

# Serve outputs/ as static files (for markmap HTML links)
import flask
import os

@app.route("/outputs/markmap/<path:filename>")
def serve_markmap(filename: str):
    return flask.send_from_directory(str(MARKMAP_DIR), filename)


@app.route("/")
def index():
    entries = collect_summaries()
    groups = group_summaries(entries)
    all_tags: list[str] = sorted({t for e in entries for t in e["tags"]})
    return render_template("index.html", groups=groups, all_tags=all_tags, total=len(entries))


@app.route("/convert", methods=["POST"])
def convert():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url 不可為空"}), 400

    acquired = _CHAIN_LOCK.acquire(blocking=False)
    if not acquired:
        return jsonify({"error": "已有 chain 在執行中，請等候完成後再試"}), 409

    task_id = str(uuid.uuid4())
    _TASKS[task_id] = {
        "task_id": task_id,
        "url": url,
        "status": "queued",
        "current_step": STEP_NAMES[0],
        "step_index": 1,
        "total_steps": TOTAL_STEPS,
        "error": None,
        "finished_at": None,
    }

    t = threading.Thread(target=_run_chain, args=(task_id, url), daemon=True)
    t.start()

    return jsonify({"task_id": task_id}), 202


@app.route("/status/<task_id>")
def status(task_id: str):
    task = _TASKS.get(task_id)
    if task is None:
        return jsonify({"error": "task not found"}), 404
    return jsonify({
        "task_id": task["task_id"],
        "status": task["status"],
        "current_step": task["current_step"],
        "step_index": task["step_index"],
        "total_steps": task["total_steps"],
        "error": task["error"],
        "finished_at": task["finished_at"],
    })


@app.route("/api/summaries")
def api_summaries():
    return jsonify(collect_summaries())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("Server running on http://localhost:5000")
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
