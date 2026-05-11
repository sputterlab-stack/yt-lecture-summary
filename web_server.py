# Flask web server for YT 演講摘要 dashboard
# Endpoints:
#   GET  /                  -> dashboard HTML (server-side rendered)
#   POST /convert           -> {url} 或 {urls: [...]} -> 起 N 個 task；回 {task_ids: [...]}
#   GET  /status/<task_id>  -> 單一 task 狀態
#   GET  /tasks             -> 所有 task 狀態（多工 polling 用）
#   GET  /api/summaries     -> JSON list of all summaries (post-batch reload)

import json
import os
import re
import sys
import subprocess
import threading
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml
from flask import Flask, jsonify, render_template, request
from openai import OpenAI

import config  # noqa: F401（為了 .env 載入副作用）
import summarizer
import transcriber
from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, OUTPUT_ROOT, WHISPER_MODEL
from enrich_intro import (
    extract_breakdown,
    generate_intro as _generate_intro_text,
    insert_intro,
)
from prompts import RECALL_CHALLENGE_SYSTEM, RECALL_CHALLENGE_USER_TEMPLATE
from recategorize import extract_summary_top, split_frontmatter

# ---------------------------------------------------------------------------
# Paths / taxonomy
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
SUMMARIES_DIR = BASE_DIR / "outputs" / "summaries"
MARKMAP_DIR = BASE_DIR / "outputs" / "markmap"
TAXONOMY_PATH = BASE_DIR / "category_taxonomy.yaml"


def _load_taxonomy_cfg() -> dict:
    if not TAXONOMY_PATH.exists():
        return {"taxonomy": {}, "aliases": {}, "skip_files": []}
    return yaml.safe_load(TAXONOMY_PATH.read_text(encoding="utf-8")) or {}


_TAXONOMY_CFG = _load_taxonomy_cfg()
_TAXONOMY = _TAXONOMY_CFG.get("taxonomy") or {}
_ALIASES = _TAXONOMY_CFG.get("aliases") or {}
EXCLUDE = {"INDEX.md", *(_TAXONOMY_CFG.get("skip_files") or [])}
CATEGORY_ORDER = list(_TAXONOMY.keys())

# ---------------------------------------------------------------------------
# Concurrency control
# ---------------------------------------------------------------------------
# Hybrid 並行：
# - PARALLEL_LIMIT 個 task 同時進入「執行」階段（network/CPU/API 各種混合）
# - Whisper 階段全域 lock 在 transcriber.transcribe() 內部（GPU OOM 防護）
# - DeepSeek 階段 semaphore 限併發（API rate limit 防護）
PARALLEL_LIMIT = int(os.environ.get("PARALLEL_LIMIT", "3"))
DEEPSEEK_PARALLEL = int(os.environ.get("DEEPSEEK_PARALLEL", "3"))

_PARALLEL_SEM = threading.Semaphore(PARALLEL_LIMIT)
_DEEPSEEK_SEM = threading.Semaphore(DEEPSEEK_PARALLEL)

# Batch debounce：所有 task 個別階段跑完，最後一個觸發批次後處理
_PENDING = 0
_PENDING_LOCK = threading.Lock()
_BATCH_LOCK = threading.Lock()
_BATCH_STATE = {"running": False, "last_finished_at": None}

# ---------------------------------------------------------------------------
# Task state
# ---------------------------------------------------------------------------
_TASKS: dict[str, dict] = {}
_TASKS_LOCK = threading.Lock()

# Per-task 5 步：1-4 個別跑、5 等批次完成後標 done
TASK_STEP_NAMES = [
    "下載 YouTube 音訊",
    "Whisper 轉逐字稿",
    "DeepSeek 第一性原理摘要",
    "寫入 .md / .srt",
    "完成",
]
TOTAL_STEPS = len(TASK_STEP_NAMES)

# ---------------------------------------------------------------------------
# Frontmatter / content helpers
# ---------------------------------------------------------------------------

def parse_frontmatter(path: Path) -> dict:
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
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return ""
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
    entries = []
    for md in sorted(SUMMARIES_DIR.glob("*.md")):
        if md.name in EXCLUDE:
            continue
        fm = parse_frontmatter(md)
        if fm.get("_skip_index"):
            continue
        cat = fm.get("category") or "未分類"
        cat = _ALIASES.get(cat, cat)
        markmap_html = MARKMAP_DIR / (md.stem + ".html")
        entries.append({
            "filename": md.stem,
            "path": md.name,
            "category": cat,
            "subcategory": fm.get("subcategory") or "",
            "tags": fm.get("tags") or [],
            "speaker": fm.get("speaker") or "未知",
            "duration": fm.get("duration") or "-",
            "generated_at": str(fm.get("generated_at") or ""),
            "source": fm.get("source") or "",
            "yt_title": fm.get("yt_title") or md.stem,
            "thesis": fm.get("thesis") or "",
            "weekly_action": fm.get("weekly_action") or "",
            "elevator_pitch": extract_elevator_pitch(md),
            "markmap_url": f"/outputs/markmap/{md.stem}.html" if markmap_html.exists() else None,
        })
    return entries


def group_summaries(entries: list[dict]) -> list[dict]:
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
# Task helpers
# ---------------------------------------------------------------------------

def _seconds_to_hhmmss(total_seconds: float) -> str:
    total_seconds = int(total_seconds)
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def _set_step(task_id: str, idx: int) -> None:
    with _TASKS_LOCK:
        t = _TASKS.get(task_id)
        if t:
            t["step_index"] = idx
            t["current_step"] = TASK_STEP_NAMES[idx - 1] if 1 <= idx <= TOTAL_STEPS else TASK_STEP_NAMES[-1]


def _set_status(task_id: str, status: str, error: str | None = None) -> None:
    with _TASKS_LOCK:
        t = _TASKS.get(task_id)
        if t:
            t["status"] = status
            if error:
                t["error"] = error
            if status in ("done", "error"):
                t["finished_at"] = datetime.now(timezone.utc).isoformat()


def _task_snapshot(task_id: str) -> dict | None:
    with _TASKS_LOCK:
        t = _TASKS.get(task_id)
        return dict(t) if t else None


# ---------------------------------------------------------------------------
# Batch post-processing (gen_mindmap → gen_index → gen_overview → gen_markmap)
# ---------------------------------------------------------------------------

def _run_batch_postprocess() -> tuple[bool, str]:
    """跑批次。串行 subprocess（避免 LLM 並行撞 rate limit）。"""
    _BATCH_STATE["running"] = True
    try:
        for script in ("gen_mindmap.py", "gen_index.py", "gen_overview.py", "gen_markmap.py"):
            r = subprocess.run(
                [sys.executable, "-u", str(BASE_DIR / script)],
                cwd=str(BASE_DIR),
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if r.returncode != 0:
                return False, f"{script} 失敗: {r.stderr[-400:]}"
        return True, ""
    finally:
        _BATCH_STATE["running"] = False
        _BATCH_STATE["last_finished_at"] = datetime.now(timezone.utc).isoformat()


def _finalise_waiting_tasks(ok: bool, err: str) -> None:
    """把所有 status='waiting_batch' 的 task 結尾標 done / error。"""
    now = datetime.now(timezone.utc).isoformat()
    with _TASKS_LOCK:
        for t in _TASKS.values():
            if t["status"] == "waiting_batch":
                if ok:
                    t["status"] = "done"
                    t["step_index"] = TOTAL_STEPS
                    t["current_step"] = TASK_STEP_NAMES[-1]
                else:
                    t["status"] = "error"
                    t["error"] = f"批次後處理失敗：{err}"
                t["finished_at"] = now


# ---------------------------------------------------------------------------
# Per-task chain (in-process)
# ---------------------------------------------------------------------------

def _run_one_task(task_id: str, url: str) -> None:
    """單一 task 全程：下載 → Whisper → DeepSeek → 寫檔 → 標等批次。
    用 _PARALLEL_SEM 控併發；transcribe 內部用 WHISPER_TRANSCRIBE_LOCK 串行；
    DeepSeek 階段用 _DEEPSEEK_SEM。
    """
    global _PENDING
    individual_ok = False
    try:
        with _PARALLEL_SEM:
            try:
                _set_status(task_id, "running")

                # Step 1: 下載
                _set_step(task_id, 1)
                prefix = transcriber.make_temp_prefix(task_id)
                mp3_path, yt_title, duration_sec = transcriber.download_audio(url, prefix=prefix)

                # Step 2: Whisper（內部 lock 串行）
                _set_step(task_id, 2)
                transcript = transcriber.transcribe(mp3_path, model_size=WHISPER_MODEL)

                # Step 3: DeepSeek（限併發）
                _set_step(task_id, 3)
                duration_str = _seconds_to_hhmmss(duration_sec)
                source_meta = {
                    "yt_title": yt_title,
                    "source_url": url,
                    "duration": duration_str,
                }
                with _DEEPSEEK_SEM:
                    result = summarizer.first_principles_summary(
                        transcript["text"], transcript["language"], source_meta
                    )

                # Step 4: 寫檔
                _set_step(task_id, 4)
                summarizer.write_srt(transcript["segments"], result["filename"], OUTPUT_ROOT)
                summarizer.write_summary(result, source_meta, OUTPUT_ROOT)

                # 清 mp3
                try:
                    if os.path.exists(mp3_path):
                        os.remove(mp3_path)
                except Exception:
                    pass

                # 紀錄結果到 task（前端可用）
                with _TASKS_LOCK:
                    t = _TASKS.get(task_id)
                    if t:
                        t["filename"] = result["filename"]
                        t["yt_title"] = yt_title

                _set_status(task_id, "waiting_batch")
                individual_ok = True

            except Exception as e:
                traceback.print_exc()
                _set_status(task_id, "error", f"{type(e).__name__}: {e}")
                return  # finally 仍會跑 _PENDING -= 1
    finally:
        # 不論成功失敗都遞減；最後一個觸發批次
        with _PENDING_LOCK:
            _PENDING -= 1
            is_last = (_PENDING == 0)

        if is_last:
            # 至少有一個個別階段成功，才需要跑批次（gen_mindmap 等會掃 .md 補處理）
            # 即使全部失敗也跑一次無害（gen_index 會列出舊狀態）
            with _BATCH_LOCK:
                ok, err = _run_batch_postprocess()
            _finalise_waiting_tasks(ok, err)


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__)

import flask  # noqa: E402


@app.route("/outputs/markmap/<path:filename>")
def serve_markmap(filename: str):
    return flask.send_from_directory(str(MARKMAP_DIR), filename)


@app.route("/")
def index():
    entries = collect_summaries()
    groups = group_summaries(entries)
    all_tags: list[str] = sorted({t for e in entries for t in e["tags"]})
    return render_template(
        "index.html",
        groups=groups,
        all_tags=all_tags,
        total=len(entries),
        category_order=CATEGORY_ORDER,
        parallel_limit=PARALLEL_LIMIT,
    )


@app.route("/convert", methods=["POST"])
def convert():
    """接收 {url} 或 {urls: [...]}；回 {task_ids: [...]}。
    向後相容：單一 url 仍可用，會包成一筆。"""
    global _PENDING
    data = request.get_json(silent=True) or {}

    urls: list[str] = []
    if "urls" in data and isinstance(data["urls"], list):
        urls = [u.strip() for u in data["urls"] if isinstance(u, str) and u.strip()]
    elif "url" in data and isinstance(data["url"], str):
        u = data["url"].strip()
        if u:
            urls = [u]

    if not urls:
        return jsonify({"error": "url 不可為空"}), 400

    task_ids: list[str] = []
    now = datetime.now(timezone.utc).isoformat()
    with _PENDING_LOCK:
        _PENDING += len(urls)

    for url in urls:
        task_id = str(uuid.uuid4())
        with _TASKS_LOCK:
            _TASKS[task_id] = {
                "task_id": task_id,
                "url": url,
                "status": "queued",
                "current_step": TASK_STEP_NAMES[0],
                "step_index": 0,
                "total_steps": TOTAL_STEPS,
                "error": None,
                "finished_at": None,
                "submitted_at": now,
                "filename": None,
                "yt_title": None,
            }
        task_ids.append(task_id)
        threading.Thread(target=_run_one_task, args=(task_id, url), daemon=True).start()

    return jsonify({"task_ids": task_ids, "task_id": task_ids[0]}), 202  # task_id 留向後相容


@app.route("/status/<task_id>")
def status(task_id: str):
    snap = _task_snapshot(task_id)
    if snap is None:
        return jsonify({"error": "task not found"}), 404
    return jsonify(snap)


@app.route("/tasks")
def tasks():
    """所有 task 狀態 + 全域批次狀態。前端 polling 用。"""
    with _TASKS_LOCK:
        all_tasks = [dict(t) for t in _TASKS.values()]
    return jsonify({
        "tasks": all_tasks,
        "batch": {
            "running": _BATCH_STATE["running"],
            "last_finished_at": _BATCH_STATE["last_finished_at"],
        },
        "limits": {
            "parallel": PARALLEL_LIMIT,
            "deepseek": DEEPSEEK_PARALLEL,
        },
    })


@app.route("/api/summaries")
def api_summaries():
    return jsonify(collect_summaries())


# 共用 OpenAI client（thread-safe；DeepSeek base url）
_DEEPSEEK_CLIENT = None
_DEEPSEEK_CLIENT_LOCK = threading.Lock()


def _get_deepseek_client() -> OpenAI:
    global _DEEPSEEK_CLIENT
    if _DEEPSEEK_CLIENT is None:
        with _DEEPSEEK_CLIENT_LOCK:
            if _DEEPSEEK_CLIENT is None:
                _DEEPSEEK_CLIENT = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    return _DEEPSEEK_CLIENT


_INTRO_HEADING_RE = re.compile(r"^##\s*導讀.*\n", re.MULTILINE)  # 含整行標題（避免「（線性帶入）」殘留進 body）


@app.route("/intro/<path:filename>")
def intro(filename: str):
    """抽某篇 .md 中的「## 導讀」段純文字回給 dashboard。"""
    if "/" in filename or "\\" in filename or ".." in filename:
        return jsonify({"error": "filename 不合法"}), 400

    md_path = SUMMARIES_DIR / f"{filename}.md"
    if not md_path.exists():
        return jsonify({"error": f"找不到摘要：{filename}"}), 404

    text = md_path.read_text(encoding="utf-8")
    _, body, _ = split_frontmatter(text)

    m = _INTRO_HEADING_RE.search(body)
    if not m:
        return jsonify({"intro": "", "missing": True, "filename": filename})

    after = body[m.end():]
    next_h2 = re.search(r"^##\s", after, re.MULTILINE)
    end_pos = next_h2.start() if next_h2 else len(after)
    intro_text = after[:end_pos].strip()

    return jsonify({"intro": intro_text, "missing": False, "filename": filename})


@app.route("/intro/<path:filename>/generate", methods=["POST"])
def intro_generate(filename: str):
    """即時為單篇 .md 產導讀並寫回檔案。回傳 {intro: 純文字}。"""
    if "/" in filename or "\\" in filename or ".." in filename:
        return jsonify({"error": "filename 不合法"}), 400

    md_path = SUMMARIES_DIR / f"{filename}.md"
    if not md_path.exists():
        return jsonify({"error": f"找不到摘要：{filename}"}), 404

    text = md_path.read_text(encoding="utf-8")
    fm_block, body, _fm = split_frontmatter(text)

    breakdown = extract_breakdown(body)
    if not breakdown:
        return jsonify({"error": "找不到「## 完整拆解」段，無法產導讀"}), 400

    try:
        client = _get_deepseek_client()
        with _DEEPSEEK_SEM:
            intro_text = _generate_intro_text(client, filename, breakdown)
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500

    if not intro_text or len(intro_text) < 200:
        return jsonify({"error": f"LLM 回傳過短（{len(intro_text)} 字），疑似失敗"}), 500

    new_body = insert_intro(body, intro_text)
    new_text = fm_block + "\n\n" + new_body.lstrip("\n")
    md_path.write_text(new_text, encoding="utf-8")

    return jsonify({"intro": intro_text, "filename": filename})


@app.route("/challenge", methods=["POST"])
def challenge():
    """Active Recall：使用者用自己的話講一篇核心論點，LLM 對比原摘要評估。"""
    data = request.get_json(silent=True) or {}
    filename = (data.get("filename") or "").strip()
    answer = (data.get("answer") or "").strip()

    if not filename:
        return jsonify({"error": "需 filename"}), 400
    if not answer:
        return jsonify({"error": "請寫下你對這篇的核心論點"}), 400

    # 安全：filename 應為純檔名（防 path traversal）
    if "/" in filename or "\\" in filename or ".." in filename:
        return jsonify({"error": "filename 不合法"}), 400

    md_path = SUMMARIES_DIR / f"{filename}.md"
    if not md_path.exists():
        return jsonify({"error": f"找不到摘要：{filename}"}), 404

    text = md_path.read_text(encoding="utf-8")
    _, body, _ = split_frontmatter(text)
    summary_top = extract_summary_top(body)

    user_content = RECALL_CHALLENGE_USER_TEMPLATE.format(
        summary_top=summary_top, user_answer=answer
    )

    try:
        client = _get_deepseek_client()
        with _DEEPSEEK_SEM:  # 受全域 DeepSeek 併發控制（與多工 task 共用）
            resp = client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[
                    {"role": "system", "content": RECALL_CHALLENGE_SYSTEM},
                    {"role": "user", "content": user_content},
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
            )
        result = json.loads(resp.choices[0].message.content)
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500

    return jsonify({
        "got_right": result.get("got_right") or [],
        "missed": result.get("missed") or [],
        "coaching": result.get("coaching") or "",
    })


if __name__ == "__main__":
    print(f"[web_server] PARALLEL_LIMIT={PARALLEL_LIMIT}, DEEPSEEK_PARALLEL={DEEPSEEK_PARALLEL}")
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
