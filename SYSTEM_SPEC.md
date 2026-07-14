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
│  ├─ /digest/<file>   GET   抽某篇「## 乾貨摘要」段              │
│  ├─ /logic/<file>    GET   抽某篇「## 邏輯拆解」段              │
│  ├─ /synthesis/<f>/generate POST 產「## 融會貫通」段          │
│  ├─ /challenge POST {filename, answer}  Active Recall        │
│  ├─ /delete/<file>   POST  移到 outputs/_trash（可復原）        │
│  ├─ /rename/<file>   POST {title}  寫 frontmatter display_title │
│  ├─ /api/summaries   全部摘要 metadata                         │
│  ├─ /catchup         GET   乾貨快讀頁（標題+乾貨，時間戳連結）   │
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

### `GET /digest/<filename>`

```json
// Response (200)
{
  "filename": "...",
  "digest": "30 秒 catch-up 純文字（💡一句定位 / ⏱跟著影片走（• [mm:ss] 從X→Y…）/ 📌so-what，含 \\n 換行）",
  "missing": false  // true 表示該篇還沒產生乾貨摘要
}

// Errors
400 { "error": "filename 不合法" }   // 含 / \ ..
404 { "error": "找不到摘要：..." }
```

`filename` 不含副檔名。從 `.md` body 抽 `## 乾貨摘要.*\n` 標題行之後到下個 `## ...` 標題前的內容。

### `POST /digest/<filename>/generate`

**讀對應的 `outputs/transcripts/<filename>.srt` 逐字稿**（轉成 `[mm:ss] 文字` 餵 LLM 拿時間戳），產 catch-up 乾貨、插入 `.md`（在導讀／精選摘要／完整拆解最先者之前）並寫回，回 `{ "digest": "...", "filename": "..." }`。受 `_DEEPSEEK_SEM` 併發控制（與多工 task / `/challenge` 共用）。前端在 `GET /digest` 回 `missing:true` 時自動觸發；讀全文較慢（~30–60 秒）。

```json
// Errors
400 { "error": "filename 不合法" / "找不到逐字稿 X.srt，無法產帶時間戳乾貨" / "逐字稿解析為空，無法產乾貨" }
404 { "error": "找不到摘要：..." }
500 { "error": "<exception>" / "LLM 回傳過短（N 字），疑似失敗" }
```

`/intro/<filename>/generate` 結構對稱（導讀版，讀 `## 完整拆解`）。

### `GET /catchup`

乾貨快讀頁（server-rendered，`templates/catchup.html`）：每篇列「標題 + 🧩 融會貫通（結構化重點整理）+ 乾貨摘要」，無心智圖/展開/學習工具。融會貫通用 `_extract_section(md, _SYNTHESIS_HEADING_RE)` 抽，經 `_synthesis_to_html()` 把 Markdown（`###` 小標 / 清單 / `**粗體**`）轉成安全 HTML 顯示（`.synth-h`/`ul`/`li` 樣式見 catchup.html）；乾貨用 `_extract_section(md, _DIGEST_HEADING_RE)`，`_digest_to_html()` 把 `[mm:ss]` 換成 `https://www.youtube.com/watch?v=<id>&t=<秒>s` 連結（`_youtube_id()` 從 frontmatter `source` 抽 11 碼 id）。兩者皆「缺則顯示 ▶ 生成 按鈕」即時補（`genSynthesis` / `genDigest` → POST generate → reload）。前端純標題/講者/tag 文字搜尋。

### `GET /logic/<filename>` ＋ `POST /logic/<filename>/generate`

與 `/digest` 完全同構，差別在段落標題（`## 邏輯拆解`）、prompt（`LOGIC_*`）與插入位置。

```json
// GET 200
{ "filename": "...", "logic": "純文字邏輯骨架（四段：拆到地基/推導鏈/多視角壓力測試/崩潰條件，含 \\n 換行、• bullet、→ 推導、🟢🔴⚖️ 視角）", "missing": false }
```

`generate` 即時產出後插入 `.md`（在 `## 完整拆解` 之前，作為原 8 段的深度伴隨版）並寫回。受 `_DEEPSEEK_SEM` 控併發。前端在 `missing:true` 時自動觸發；產出耗時較長（~30–45 秒）。錯誤碼同 `/digest/generate`（過短門檻 150 字）。

### `POST /synthesis/<filename>/generate`

讀 `## 完整拆解`（`extract_breakdown`）**＋對應 `.srt` 逐字稿**（`srt_to_timestamped_text`）餵 `SYNTHESIS_*` prompt，產「結構隨內容走」的結構化重點整理「融會貫通」（排名→排名清單、流程→步驟、論證→主張+證據+反例…；Markdown，標題用 `###`、禁 `##`/表格；含 ASR 同音錯字校正），用 `insert_synthesis()` 插在 `## 精選摘要` 之前並寫回，回 `{ "synthesis": "...", "filename": "..." }`。受 `_DEEPSEEK_SEM` 控併發；過短門檻 80 字。catchup 頁在缺此段時顯示「▶ 生成融會貫通」按鈕觸發。批次補產：`python enrich_synthesis.py --apply`（dry-run 預設、`--force` 重產、`--file` 單篇；`--file … --apply` 可只寫單篇）。

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

回傳全部 `collect_summaries()` 結果（列表，每筆含 frontmatter 全欄 + `display_title` + `elevator_pitch` + `markmap_url`）。`display_title` = frontmatter 的 `display_title`，無則回退 `md.stem`（檔名）。

### `POST /delete/<filename>`

把該篇的 `.md` / `.mmd`（summaries）、`.srt`（transcripts）、`.html`（markmap）**移到 `outputs/_trash/`** 對應子夾（可手動復原，非硬刪）。回 `{ "ok": true, "moved": [...] }`；`.md` 不存在回 404。安全：拒含 `/`、`\`、`..` 的 filename。

### `POST /rename/<filename>` body `{title}`

把 `title` 以 `update_frontmatter_keys()` 寫入該篇 frontmatter 的 `display_title`（值用 `json.dumps` 包成合法 YAML 雙引號字串，避免冒號/引號破壞 YAML），**不改檔名**。回 `{ "ok": true, "display_title": title }`；空標題 400、檔案不存在 404。前端僅改顯示與搜尋用標題，`yt_title`（原始 YouTube 標題）保留不動。

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
| `INTRO_SYSTEM` + `INTRO_USER_TEMPLATE` | 線性導讀 | `enrich_intro.py`、`web_server /intro/<f>/generate` |
| `DIGEST_SYSTEM` + `DIGEST_USER_TEMPLATE` | 乾貨摘要（30 秒 catch-up，帶時間戳 beats；輸入為 `.srt` 逐字稿，placeholder `{transcript}`） | `enrich_digest.py`、`web_server /digest/<f>/generate` |
| `LOGIC_SYSTEM` + `LOGIC_USER_TEMPLATE` | 邏輯拆解（第一性原理推導鏈 × 多視角） | `enrich_logic.py`、`web_server /logic/<f>/generate` |
| `SYNTHESIS_SYSTEM` + `SYNTHESIS_USER_TEMPLATE` | 融會貫通（「結構隨內容走」結構化重點整理，排名/流程/論證/敘事依內容選結構；輸入為 `## 完整拆解` ＋ `.srt` 逐字稿） | `enrich_synthesis.py`、`web_server /synthesis/<f>/generate` |
| `RECALL_CHALLENGE_SYSTEM` + `RECALL_CHALLENGE_USER_TEMPLATE` | Active Recall 評估 | `web_server /challenge` |

`USER_PROMPT_TEMPLATE` 在 module load 時用 `_RAW_USER_PROMPT.replace("{TAXONOMY_TEXT}", TAXONOMY_TEXT)` 動態注入 taxonomy；其餘 `{yt_title}` `{transcript}` 等仍由 `summarizer` `.format()` 處理。

## 9. 一次性批次工具

| 工具 | 觸發時機 | 改動範圍 |
|---|---|---|
| `recategorize.py` | 改 taxonomy 後重判 | frontmatter `category` + `subcategory`，不動 summary / tags |
| `enrich_summary.py` | 加 thesis/action 欄後補舊資料 | frontmatter `thesis` + `weekly_action`，不動 summary / tags |
| `enrich_intro.py` | 加導讀章節後補舊資料 | body 插入「## 導讀（線性帶入）」段（在 `## 精選摘要` 前）；不動 frontmatter 與其他段；預設跳過已有導讀者，`--force` 強制重產 |
| `enrich_digest.py` | 加/改版乾貨段 | **讀 `outputs/transcripts/<stem>.srt`**（`srt_to_timestamped_text()` → `[mm:ss] 文字`）產帶時間戳 catch-up，插入「## 乾貨摘要」段（在導讀／精選摘要／完整拆解最先者之前，即最上方）；不動 frontmatter 與其他段；預設跳過已有者，改版用 `--apply --force` 全量重產 |
| `enrich_logic.py` | 加邏輯拆解章節後補舊資料 | body 插入「## 邏輯拆解」段（在 `## 完整拆解` 之前）；不動 frontmatter 與其他段；預設跳過已有者，`--force` 強制重產；複用 `enrich_intro.extract_breakdown`；過短門檻 150 字 |

各工具共用 `recategorize.split_frontmatter` / `extract_summary_top` / `update_frontmatter_keys`。`update_frontmatter_keys` 精準 in-place 替換指定 key（保留其他行原樣含 datetime / list / 註解）。

## 10. 已知小債

| 項目 | 影響 | 修法 |
|---|---|---|
| `_TASKS` dict 無 GC | 長跑 server 記憶體增長；重啟清 | 30 分鐘 timer GC done/error 老 task |
| 多 task 同時 `gen_mindmap.py` | 已用批次 debounce 規避 | — |
| Flask 預設 `debug=False` 不熱重載 templates | 改檔需重啟 | 由開發者 awareness |
