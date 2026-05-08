# YT 演講摘要 — 系統規格書

> 開發者規格（API / schema / 並行架構）。使用面文件見 `README.md`。

## 1. 架構總覽

```
┌─────────────────────────────────────────────────────────────┐
│  Browser (templates/index.html — vanilla HTML/CSS/JS)       │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼ HTTP
┌─────────────────────────────────────────────────────────────┐
│  Flask web_server.py                                         │
│  ├─ /convert   POST {urls: [...]}    起 N 個 task             │
│  ├─ /tasks     GET   全部 task 狀態                            │
│  ├─ /status/<id>     單一 task                                │
│  ├─ /intro/<file>    GET   抽某篇「## 導讀」段                  │
│  ├─ /challenge POST {filename, answer}  Active Recall        │
│  ├─ /api/summaries   全部摘要 metadata                         │
│  └─ /                Server-rendered dashboard                │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼ in-process call
┌─────────────────────────────────────────────────────────────┐
│  transcriber.download_audio(url, prefix)  yt-dlp             │
│  transcriber.transcribe(mp3, model_size)  Whisper            │
│  summarizer.first_principles_summary(...)  DeepSeek          │
│  summarizer.write_summary / write_srt                        │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼ subprocess (batch debounce)
┌─────────────────────────────────────────────────────────────┐
│  gen_mindmap.py  (DeepSeek → mermaid mindmap .mmd)           │
│  gen_index.py    (套 taxonomy 重建 INDEX.md 二層結構)         │
│  gen_overview.py (拼 .mmd → 心智圖總覽.md)                     │
│  gen_markmap.py  (markmap-cli → outputs/markmap/*.html)      │
└─────────────────────────────────────────────────────────────┘
```

## 2. Frontmatter Schema

每篇摘要 `outputs/summaries/{標題}.md` 開頭 YAML frontmatter：

| 欄位 | 型別 | 必要 | 說明 |
|---|---|---|---|
| `source` | str | ✓ | YouTube URL |
| `yt_title` | str | ✓ | YT 原標題 |
| `speaker` | str | ✓ | 講者；無法識別填 `未知` |
| `language` | str | ✓ | 偵測語言（中文 / 英文 / 日文等） |
| `duration` | str | ✓ | `HH:MM:SS` |
| `generated_at` | str (ISO 8601) | ✓ | UTC 生成時間 |
| `model` | str | ✓ | 模型 id（例 `deepseek-v4-pro`） |
| `category` | str | ✓ | 主類；必須在 taxonomy 內 |
| `subcategory` | str | optional | 子類；taxonomy 列出的優先 |
| `thesis` | str (yaml-quoted) | optional | 30 字內核心主張（立場句） |
| `weekly_action` | str (yaml-quoted) | optional | 50 字內可執行動作 |
| `tags` | list[str] (flow style) | ✓ | 3-6 個自由標籤 |
| `_skip_index` | bool | optional | gen_index 跳過旗標（工具產物用） |

`thesis` / `weekly_action` 用 JSON-encoded 雙引號 scalar 防 `:` 與 `"` 撞 YAML parser（`summarizer._yaml_quote` / `enrich_summary._yaml_quote`）。

## 3. category_taxonomy.yaml Schema

```yaml
taxonomy:
  <主類>:
    - <子類1>
    - <子類2>
    - ...
  <主類無子類則空 list>: []

aliases:
  <過時主類>: <canonical 主類>

skip_files:
  - <檔名.md>
```

**規則**：
- 主類順序 = yaml dict 插入順序（Python 3.7+ 保證）
- LLM 必須選現有主類，**不可新建**（prompt enforced）
- 子類允許新建（4-8 字中文），會出現在 INDEX 的「extra」分群
- `aliases` 只折主類，不擅自指定子類（子類由 LLM 重判）
- `skip_files` + frontmatter `_skip_index: true` 雙重黑名單機制

載入：`prompts._load_taxonomy()`、`gen_index.load_taxonomy()`、`web_server._load_taxonomy_cfg()`、`recategorize.load_taxonomy()`。**修改後須重啟 web_server**。

## 4. HTTP API

### `POST /convert`

```json
// Request
{ "urls": ["https://youtu.be/...", "..."] }
// 或向後相容單一 url：{ "url": "..." }

// Response (202)
{ "task_ids": ["uuid1", "uuid2", ...], "task_id": "uuid1" }

// Error (400)
{ "error": "url 不可為空" }
```

### `GET /tasks`

```json
{
  "tasks": [
    {
      "task_id": "uuid",
      "url": "https://...",
      "status": "queued|running|waiting_batch|done|error",
      "current_step": "下載 YouTube 音訊",
      "step_index": 1,
      "total_steps": 5,
      "filename": "...",       // 寫檔後填
      "yt_title": "...",
      "error": null | "錯誤訊息",
      "submitted_at": "ISO 8601",
      "finished_at": null | "ISO 8601"
    }
  ],
  "batch": {
    "running": false,
    "last_finished_at": null | "ISO 8601"
  },
  "limits": { "parallel": 3, "deepseek": 3 }
}
```

### `GET /status/<task_id>`

單筆 task snapshot（同上 `tasks[i]` 結構）。404 if not found。

### `GET /intro/<filename>`

```json
// Response (200)
{
  "filename": "...",
  "intro": "純散文導讀全文（含段落 \\n\\n 分隔）",
  "missing": false  // true 表示該篇還沒產生導讀
}

// Errors
400 { "error": "filename 不合法" }   // 含 / \ ..
404 { "error": "找不到摘要：..." }
```

`filename` 不含副檔名。從 `.md` body 抽 `## 導讀.*\n` 標題行之後到下個 `## ...` 標題前的內容。

### `POST /challenge`

```json
// Request
{ "filename": "巴菲特...", "answer": "用自己話寫的核心論點" }

// Response (200)
{
  "got_right": ["你抓對的點1", "..."],
  "missed":    ["忽略了 X...", "..."],
  "coaching":  "下次注意 ..."
}

// Errors
400 { "error": "需 filename" / "請寫下你對這篇的核心論點" / "filename 不合法" }
404 { "error": "找不到摘要：..." }
500 { "error": "<exception type>: <message>" }
```

`filename` 不含副檔名。安全：拒含 `/`、`\`、`..` 的 filename（防 path traversal）。

### `GET /api/summaries`

回傳全部 `collect_summaries()` 結果（列表，每筆含 frontmatter 全欄 + `elevator_pitch` + `markmap_url`）。

## 5. Per-task 5 步狀態機

```
queued (進 _PARALLEL_SEM 前)
  │
  ▼ 取得 sem
running (step 1: 下載) → step 2: Whisper → step 3: DeepSeek → step 4: 寫檔
  │
  ▼ 釋出 sem
waiting_batch (step 4 done，等 _PENDING == 0 觸發批次)
  │
  ▼ 批次成功
done (step 5)
  │
  └─ 失敗：error (帶 message)
```

`_PENDING` counter 在 submit 時 += N，每 task `finally` 區塊 -= 1；歸零者觸發 `_run_batch_postprocess()`，跑完 `_finalise_waiting_tasks()` 把所有 `waiting_batch` 標 done / error。

## 6. 並行控制

| 鎖 / Semaphore | 位置 | 用途 |
|---|---|---|
| `_PARALLEL_SEM` (Semaphore, 預設 3) | `web_server.py` | 整體 task 同時跑數量上限 |
| `_DEEPSEEK_SEM` (Semaphore, 預設 3) | `web_server.py` | DeepSeek API 併發限制（也守護 `/challenge`） |
| `WHISPER_TRANSCRIBE_LOCK` (Lock) | `transcriber.py` | Whisper transcribe 全域串行（GPU OOM） |
| `_WHISPER_LOAD_LOCK` (Lock) | `transcriber.py` | Whisper model lazy load 防雙重載入 |
| `_BATCH_LOCK` (Lock) | `web_server.py` | 批次後處理單 instance |
| `_TASKS_LOCK` (Lock) | `web_server.py` | `_TASKS` dict 讀寫保護 |
| `_PENDING_LOCK` (Lock) | `web_server.py` | `_PENDING` counter 原子操作 |

環境變數：

| Env | 預設 | 說明 |
|---|---|---|
| `PARALLEL_LIMIT` | `3` | 整體並行 task 上限 |
| `DEEPSEEK_PARALLEL` | `3` | DeepSeek API 併發上限 |
| `FFMPEG_DIR` | `""` | ffmpeg.exe 所在目錄（空 → 用系統 PATH） |
| `DEEPSEEK_API_KEY` | (必填) | `.env` |

## 7. 暫存檔隔離

每 task `transcriber.make_temp_prefix(task_id)` 產出獨立 prefix：

- 命令列單一 CLI：`temp_audio_download_{PID}`
- Web in-process 多 task：`temp_audio_download_{PID}_{task_id}`

`atexit` 自動清自己 PID 開頭的所有暫存。模組載入時掃 `mtime > 1hr` 孤兒檔（前次強殺 / X 關視窗殘留）。

## 8. Prompts（`prompts.py`）

| 名稱 | 用途 | 呼叫者 |
|---|---|---|
| `SYSTEM_PROMPT` + `USER_PROMPT_TEMPLATE` | 主摘要（含 thesis / weekly_action / category / subcategory） | `summarizer.first_principles_summary` |
| `MINDMAP_SYSTEM_PROMPT` + `MINDMAP_USER_PROMPT_TEMPLATE` | Mermaid 心智圖 | `gen_mindmap.py` |
| `INTRO_SYSTEM` + `INTRO_USER_TEMPLATE` | 線性導讀 | `enrich_intro.py` |
| `RECALL_CHALLENGE_SYSTEM` + `RECALL_CHALLENGE_USER_TEMPLATE` | Active Recall 評估 | `web_server /challenge` |

`USER_PROMPT_TEMPLATE` 在 module load 時用 `_RAW_USER_PROMPT.replace("{TAXONOMY_TEXT}", TAXONOMY_TEXT)` 動態注入 taxonomy；其餘 `{yt_title}` `{transcript}` 等仍由 `summarizer` `.format()` 處理。

## 9. 一次性批次工具

| 工具 | 觸發時機 | 改動範圍 |
|---|---|---|
| `recategorize.py` | 改 taxonomy 後重判 | frontmatter `category` + `subcategory`，不動 summary / tags |
| `enrich_summary.py` | 加 thesis/action 欄後補舊資料 | frontmatter `thesis` + `weekly_action`，不動 summary / tags |
| `enrich_intro.py` | 加導讀章節後補舊資料 | body 插入「## 導讀（線性帶入）」段（在 `## 精選摘要` 前）；不動 frontmatter 與其他段；預設跳過已有導讀者，`--force` 強制重產 |

兩者共用 `recategorize.split_frontmatter` / `extract_summary_top` / `update_frontmatter_keys`。`update_frontmatter_keys` 精準 in-place 替換指定 key（保留其他行原樣含 datetime / list / 註解）。

## 10. 已知小債

| 項目 | 影響 | 修法 |
|---|---|---|
| `_TASKS` dict 無 GC | 長跑 server 記憶體增長；重啟清 | 30 分鐘 timer GC done/error 老 task |
| 多 task 同時 `gen_mindmap.py` | 已用批次 debounce 規避 | — |
| Flask 預設 `debug=False` 不熱重載 templates | 改檔需重啟 | 由開發者 awareness |
