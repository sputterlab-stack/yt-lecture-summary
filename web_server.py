# Flask web server for YT 演講摘要 dashboard
# Endpoints:
#   GET  /                  -> dashboard HTML (server-side rendered)
#   POST /convert           -> {url} 或 {urls: [...]} -> 起 N 個 task；回 {task_ids: [...]}
#   GET  /status/<task_id>  -> 單一 task 狀態
#   GET  /tasks             -> 所有 task 狀態（多工 polling 用）
#   GET  /api/summaries     -> JSON list of all summaries (post-batch reload)

from __future__ import annotations  # 型別註解不在載入時求值（OpenAI 改成延後 import）

import html
import json
import os
import re
import shutil
import sys
import subprocess
import threading
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openai import OpenAI

import yaml
from flask import Flask, jsonify, render_template, request

import config  # noqa: F401（為了 .env 載入副作用）
import summarizer
import transcriber
from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, OUTPUT_ROOT, WHISPER_MODEL
from enrich_intro import (
    extract_breakdown,
    generate_intro as _generate_intro_text,
    insert_intro,
)
from enrich_digest import (
    generate_digest as _generate_digest_text,
    insert_digest,
    srt_to_timestamped_text,
    TRANSCRIPTS_DIR,
)
from enrich_logic import (
    generate_logic as _generate_logic_text,
    insert_logic,
)
from enrich_synthesis import (
    generate_synthesis as _generate_synthesis_text,
    insert_synthesis,
)
from prompts import RECALL_CHALLENGE_SYSTEM, RECALL_CHALLENGE_USER_TEMPLATE
from recategorize import extract_summary_top, split_frontmatter, update_frontmatter_keys

# ---------------------------------------------------------------------------
# Paths / taxonomy
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
SUMMARIES_DIR = BASE_DIR / "outputs" / "summaries"
MARKMAP_DIR = BASE_DIR / "outputs" / "markmap"
TRASH_DIR = BASE_DIR / "outputs" / "_trash"
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
            "tags": [str(t) for t in (fm.get("tags") or [])],
            "speaker": fm.get("speaker") or "未知",
            "duration": fm.get("duration") or "-",
            "generated_at": str(fm.get("generated_at") or ""),
            "source": fm.get("source") or "",
            "yt_title": fm.get("yt_title") or md.stem,
            "display_title": fm.get("display_title") or md.stem,
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


@app.route("/delete/<path:filename>", methods=["POST"])
def delete_summary(filename: str):
    """把某篇的 .md/.mmd/.srt/.html 移到 outputs/_trash/（可復原），不硬刪。"""
    if "/" in filename or "\\" in filename or ".." in filename:
        return jsonify({"error": "filename 不合法"}), 400

    md_path = SUMMARIES_DIR / f"{filename}.md"
    if not md_path.exists():
        return jsonify({"error": f"找不到摘要：{filename}"}), 404

    targets = [
        (SUMMARIES_DIR / f"{filename}.md", TRASH_DIR / "summaries" / f"{filename}.md"),
        (SUMMARIES_DIR / f"{filename}.mmd", TRASH_DIR / "summaries" / f"{filename}.mmd"),
        (TRANSCRIPTS_DIR / f"{filename}.srt", TRASH_DIR / "transcripts" / f"{filename}.srt"),
        (MARKMAP_DIR / f"{filename}.html", TRASH_DIR / "markmap" / f"{filename}.html"),
    ]
    moved = []
    for src, dst in targets:
        if not src.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            dst.unlink()
        shutil.move(str(src), str(dst))
        moved.append(src.name)
    return jsonify({"ok": True, "moved": moved})


@app.route("/rename/<path:filename>", methods=["POST"])
def rename_summary(filename: str):
    """改顯示標題：寫入 .md frontmatter 的 display_title，不改檔名。"""
    if "/" in filename or "\\" in filename or ".." in filename:
        return jsonify({"error": "filename 不合法"}), 400

    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "標題不可為空"}), 400

    md_path = SUMMARIES_DIR / f"{filename}.md"
    if not md_path.exists():
        return jsonify({"error": f"找不到摘要：{filename}"}), 404

    text = md_path.read_text(encoding="utf-8")
    fm_block, body, _fm = split_frontmatter(text)
    if not fm_block:
        return jsonify({"error": "此檔無 frontmatter，無法寫入標題"}), 400

    new_fm = update_frontmatter_keys(
        fm_block, {"display_title": json.dumps(title, ensure_ascii=False)}
    )
    md_path.write_text(new_fm + "\n" + body, encoding="utf-8")
    return jsonify({"ok": True, "display_title": title})


# 共用 OpenAI client（thread-safe；DeepSeek base url）
_DEEPSEEK_CLIENT = None
_DEEPSEEK_CLIENT_LOCK = threading.Lock()


def _get_deepseek_client() -> OpenAI:
    global _DEEPSEEK_CLIENT
    if _DEEPSEEK_CLIENT is None:
        with _DEEPSEEK_CLIENT_LOCK:
            if _DEEPSEEK_CLIENT is None:
                from openai import OpenAI  # 只在真的要呼叫 API 時載入

                _DEEPSEEK_CLIENT = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    return _DEEPSEEK_CLIENT


_INTRO_HEADING_RE = re.compile(r"^##\s*導讀.*\n", re.MULTILINE)  # 含整行標題（避免「（線性帶入）」殘留進 body）
_DIGEST_HEADING_RE = re.compile(r"^##\s*乾貨摘要.*\n", re.MULTILINE)  # 含整行標題
_LOGIC_HEADING_RE = re.compile(r"^##\s*邏輯拆解.*\n", re.MULTILINE)  # 含整行標題
_SYNTHESIS_HEADING_RE = re.compile(r"^##\s*融會貫通.*\n", re.MULTILINE)  # 含整行標題


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


@app.route("/digest/<path:filename>")
def digest(filename: str):
    """抽某篇 .md 中的「## 乾貨摘要」段純文字回給 dashboard。"""
    if "/" in filename or "\\" in filename or ".." in filename:
        return jsonify({"error": "filename 不合法"}), 400

    md_path = SUMMARIES_DIR / f"{filename}.md"
    if not md_path.exists():
        return jsonify({"error": f"找不到摘要：{filename}"}), 404

    text = md_path.read_text(encoding="utf-8")
    _, body, _ = split_frontmatter(text)

    m = _DIGEST_HEADING_RE.search(body)
    if not m:
        return jsonify({"digest": "", "missing": True, "filename": filename})

    after = body[m.end():]
    next_h2 = re.search(r"^##\s", after, re.MULTILINE)
    end_pos = next_h2.start() if next_h2 else len(after)
    digest_text = after[:end_pos].strip()

    return jsonify({"digest": digest_text, "missing": False, "filename": filename})


@app.route("/digest/<path:filename>/generate", methods=["POST"])
def digest_generate(filename: str):
    """即時為單篇 .md 產乾貨摘要並寫回檔案。回傳 {digest: 純文字}。"""
    if "/" in filename or "\\" in filename or ".." in filename:
        return jsonify({"error": "filename 不合法"}), 400

    md_path = SUMMARIES_DIR / f"{filename}.md"
    if not md_path.exists():
        return jsonify({"error": f"找不到摘要：{filename}"}), 404

    text = md_path.read_text(encoding="utf-8")
    fm_block, body, _fm = split_frontmatter(text)

    srt_path = TRANSCRIPTS_DIR / f"{filename}.srt"
    if not srt_path.exists():
        return jsonify({"error": f"找不到逐字稿 {filename}.srt，無法產帶時間戳乾貨"}), 400
    transcript = srt_to_timestamped_text(srt_path)
    if not transcript:
        return jsonify({"error": "逐字稿解析為空，無法產乾貨"}), 400

    try:
        client = _get_deepseek_client()
        with _DEEPSEEK_SEM:
            digest_text = _generate_digest_text(client, filename, transcript)
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500

    if not digest_text or len(digest_text) < 80:
        return jsonify({"error": f"LLM 回傳過短（{len(digest_text)} 字），疑似失敗"}), 500

    new_body = insert_digest(body, digest_text)
    new_text = fm_block + "\n\n" + new_body.lstrip("\n")
    md_path.write_text(new_text, encoding="utf-8")

    return jsonify({"digest": digest_text, "filename": filename})


@app.route("/logic/<path:filename>")
def logic(filename: str):
    """抽某篇 .md 中的「## 邏輯拆解」段純文字回給 dashboard。"""
    if "/" in filename or "\\" in filename or ".." in filename:
        return jsonify({"error": "filename 不合法"}), 400

    md_path = SUMMARIES_DIR / f"{filename}.md"
    if not md_path.exists():
        return jsonify({"error": f"找不到摘要：{filename}"}), 404

    text = md_path.read_text(encoding="utf-8")
    _, body, _ = split_frontmatter(text)

    m = _LOGIC_HEADING_RE.search(body)
    if not m:
        return jsonify({"logic": "", "missing": True, "filename": filename})

    after = body[m.end():]
    next_h2 = re.search(r"^##\s", after, re.MULTILINE)
    end_pos = next_h2.start() if next_h2 else len(after)
    logic_text = after[:end_pos].strip()

    return jsonify({"logic": logic_text, "missing": False, "filename": filename})


@app.route("/logic/<path:filename>/generate", methods=["POST"])
def logic_generate(filename: str):
    """即時為單篇 .md 產邏輯拆解並寫回檔案。回傳 {logic: 純文字}。"""
    if "/" in filename or "\\" in filename or ".." in filename:
        return jsonify({"error": "filename 不合法"}), 400

    md_path = SUMMARIES_DIR / f"{filename}.md"
    if not md_path.exists():
        return jsonify({"error": f"找不到摘要：{filename}"}), 404

    text = md_path.read_text(encoding="utf-8")
    fm_block, body, _fm = split_frontmatter(text)

    breakdown = extract_breakdown(body)
    if not breakdown:
        return jsonify({"error": "找不到「## 完整拆解」段，無法產邏輯拆解"}), 400

    try:
        client = _get_deepseek_client()
        with _DEEPSEEK_SEM:
            logic_text = _generate_logic_text(client, filename, breakdown)
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500

    if not logic_text or len(logic_text) < 150:
        return jsonify({"error": f"LLM 回傳過短（{len(logic_text)} 字），疑似失敗"}), 500

    new_body = insert_logic(body, logic_text)
    new_text = fm_block + "\n\n" + new_body.lstrip("\n")
    md_path.write_text(new_text, encoding="utf-8")

    return jsonify({"logic": logic_text, "filename": filename})


@app.route("/synthesis/<path:filename>/generate", methods=["POST"])
def synthesis_generate(filename: str):
    """即時為單篇 .md 產融會貫通（一段式 elevator pitch）並寫回檔案。回 {synthesis: 純文字}。"""
    if "/" in filename or "\\" in filename or ".." in filename:
        return jsonify({"error": "filename 不合法"}), 400

    md_path = SUMMARIES_DIR / f"{filename}.md"
    if not md_path.exists():
        return jsonify({"error": f"找不到摘要：{filename}"}), 404

    text = md_path.read_text(encoding="utf-8")
    fm_block, body, _fm = split_frontmatter(text)

    breakdown = extract_breakdown(body)
    if not breakdown:
        return jsonify({"error": "找不到「## 完整拆解」段，無法產融會貫通"}), 400

    srt_path = TRANSCRIPTS_DIR / f"{filename}.srt"
    transcript = srt_to_timestamped_text(srt_path) if srt_path.exists() else ""

    try:
        client = _get_deepseek_client()
        with _DEEPSEEK_SEM:
            synthesis_text = _generate_synthesis_text(client, filename, breakdown, transcript)
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500

    if not synthesis_text or len(synthesis_text) < 80:
        return jsonify({"error": f"LLM 回傳過短（{len(synthesis_text)} 字），疑似失敗"}), 500

    new_body = insert_synthesis(body, synthesis_text)
    new_text = fm_block + "\n\n" + new_body.lstrip("\n")
    md_path.write_text(new_text, encoding="utf-8")

    return jsonify({"synthesis": synthesis_text, "filename": filename})


# ---------------------------------------------------------------------------
# 乾貨快讀頁（standalone catch-up reading page）
#   只顯示「標題 + 乾貨摘要」，無心智圖 / 學習工具；[mm:ss] 連到 YouTube 該時間點
# ---------------------------------------------------------------------------
_TS_LINK_RE = re.compile(r"\[(\d{1,3}):(\d{2})\]")


def _extract_section(md_path: Path, heading_re) -> str:
    try:
        text = md_path.read_text(encoding="utf-8")
    except Exception:
        return ""
    _, body, _ = split_frontmatter(text)
    m = heading_re.search(body)
    if not m:
        return ""
    after = body[m.end():]
    nh = re.search(r"^##\s", after, re.MULTILINE)
    return (after[:nh.start()] if nh else after).strip()


def _youtube_id(url: str) -> str | None:
    if not url:
        return None
    m = re.search(r"(?:youtu\.be/|[?&]v=|/embed/|/shorts/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else None


def _digest_to_html(digest: str, source: str) -> str:
    """HTML-escape 乾貨內文；把 [mm:ss] 變成可跳到 YouTube 該時間點的連結。"""
    if not digest:
        return ""
    esc = html.escape(digest)
    vid = _youtube_id(source or "")
    if vid:
        def repl(m):
            secs = int(m.group(1)) * 60 + int(m.group(2))
            return (f'<a href="https://www.youtube.com/watch?v={vid}&t={secs}s" '
                    f'target="_blank" rel="noopener" class="ts-link">{m.group(0)}</a>')
        esc = _TS_LINK_RE.sub(repl, esc)
    return esc


_SYN_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _syn_inline(s: str) -> str:
    return _SYN_BOLD_RE.sub(r"<strong>\1</strong>", html.escape(s))


def _synthesis_to_html(md: str) -> str:
    """把結構化融會貫通 markdown 轉成安全 HTML（### 小標 / 清單 / **粗體**）；供 catchup 顯示。"""
    if not md:
        return ""
    out, in_ul = [], False

    def close_ul():
        nonlocal in_ul
        if in_ul:
            out.append("</ul>")
            in_ul = False

    for raw in md.splitlines():
        line = raw.rstrip()
        if not line.strip():
            close_ul()
            continue
        mh = re.match(r"^#{3,6}\s+(.*)$", line)
        mb = re.match(r"^\s*[-*]\s+(.*)$", line)
        if mh:
            close_ul()
            out.append(f'<div class="synth-h">{_syn_inline(mh.group(1))}</div>')
        elif mb:
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{_syn_inline(mb.group(1))}</li>")
        elif re.match(r"^\s*-{3,}\s*$", line):
            close_ul()
        else:
            close_ul()
            out.append(f"<p>{_syn_inline(line)}</p>")
    close_ul()
    return "\n".join(out)


@app.route("/catchup")
def catchup_page():
    entries = collect_summaries()
    for e in entries:
        md = SUMMARIES_DIR / e["path"]
        e["digest_html"] = _digest_to_html(
            _extract_section(md, _DIGEST_HEADING_RE), e.get("source")
        )
        e["synthesis_html"] = _synthesis_to_html(_extract_section(md, _SYNTHESIS_HEADING_RE))
    groups = group_summaries(entries)
    return render_template("catchup.html", groups=groups, total=len(entries))


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
