# YT 演講摘要

> ⚠️ **目前僅在 Windows 11 測試過。** Python 程式（`yt_summary.py` / `web_server.py` / `gen_*.py`）跨平台應該能跑；但 `.bat` 啟動腳本是 Windows 限定，Mac / Linux 使用者需要對應寫 `.sh` 替代（PR welcome）。

## Features

- 🎙️ YouTube URL → Whisper 轉逐字稿（CUDA 加速）
- 🧠 DeepSeek V4-Pro 用「第一性原理」拆解演講者思考骨架（8 段結構分析 + 精選摘要）
- 🗺️ 自動產 mermaid 心智圖（`.mmd`）+ Markmap 互動式 HTML
- 🌐 Flask web dashboard：URL 觸發整套 chain、9 步進度條、卡片網格、tag 搜尋過濾
- 📚 frontmatter 自動分類 + INDEX 索引

---

輸入 YouTube 演講 URL，自動：

1. 下載音訊 → Whisper 轉逐字稿
2. DeepSeek V4-Pro 用第一性原理拆解 → 產 `.md` 摘要 + `.srt` 字幕
3. 產 mermaid 心智圖 `.mmd`（可在 VS Code 預覽）
4. 更新 `INDEX.md` 分類索引

---

## 一、安裝

### Python 環境

本專案應在 **conda base 環境**（`python`）執行（與既有 YT 轉文字 ipynb 同環境，已備齊 yt-dlp / whisper / torch+CUDA / dotenv，僅需補裝 `openai`；Flask 已在 requirements.txt）。

```bash
python -m pip install -r requirements.txt
```

### ffmpeg

`yt-dlp` 與 `whisper` 都依賴 `ffmpeg`。

- 預設：`ffmpeg` 應在系統 PATH 中（建議方法，跨平台）
- 替代：若 ffmpeg 不在 PATH，可設環境變數 `FFMPEG_DIR=<ffmpeg 所在目錄>` — `transcriber.py` 啟動時會把該路徑加進 PATH

### API Key

```bash
cp .env.example .env
# 編輯 .env 填入 DeepSeek API Key
# DEEPSEEK_API_KEY=sk-你的金鑰
```

### Markmap CLI（看互動心智圖必裝）

```bash
npm install -g markmap-cli
```

需要先裝 Node.js（[官網](https://nodejs.org/)）。

### VS Code 心智圖預覽（看 `.mmd` / `.md` 內 mermaid 圖必裝）

```bash
code --install-extension bierner.markdown-mermaid
code --install-extension tomoyukim.vscode-mermaid-editor
```

---

## 二、日常使用

### 主要入口 — 開啟UI.bat（Web Dashboard）

雙擊 `開啟UI.bat` → 自動啟動 Flask server + 開瀏覽器 `localhost:5000`：

- **上方輸入框**：貼 YouTube URL → 點「轉換」→ 進度條即時顯示 9 步 → 完成自動重新載入卡片
- **下方卡片網格**：所有摘要按 category 分群，含 elevator pitch、speaker、tag chips
- **即時過濾**：搜尋框（標題 / 講者 / tag）+ 多選 tag chips（OR 邏輯）+ 清除按鈕

視窗保持開啟即 server 運作，關掉視窗即停止。

### Fallback — 一鍵啟動.bat（純命令列）

雙擊 → 貼 URL → 全套自動跑（yt_summary → gen_mindmap → gen_index → gen_overview → gen_markmap）。

### 純命令列

```bash
python yt_summary.py "https://youtube.com/watch?v=XXX"
python gen_mindmap.py    # 為新 .md 產對應 .mmd
python gen_index.py      # 重建 INDEX.md
```

無參數時 `yt_summary.py` 會互動式問 URL。

---

## 三、輸出檔案

| 檔案 | 路徑 | 用途 |
|---|---|---|
| 摘要 | `outputs/summaries/{標題}.md` | 第一性原理 8 段拆解 + 精選摘要 |
| 心智圖 | `outputs/summaries/{標題}.mmd` | mermaid mindmap，視覺化吸收 |
| 字幕 | `outputs/transcripts/{標題}.srt` | 帶時間軸逐字稿 |
| 索引 | `outputs/summaries/INDEX.md` | 按 category 分群的總表 |

`.md` 開頭含 YAML frontmatter（source / yt_title / speaker / language / duration / generated_at / model / category / tags）。

---

## 四、看心智圖

### 最推薦 — Web Dashboard（`開啟UI.bat`）

雙擊 `開啟UI.bat` → 瀏覽器開 `localhost:5000` → 點卡片標題 → 新分頁開互動式 markmap HTML。

### 次選 — 直接開舊 index.html

`outputs/markmap/index.html` 仍可直接雙擊開啟（靜態連結列表）。

### VS Code 開「心智圖總覽.md」

```
code "outputs/summaries/心智圖總覽.md"
```

開檔後按 `Ctrl+Shift+V` → 滾動看所有心智圖。此檔由 `gen_overview.py` 自動拼接所有 `.mmd`，每次跑 `一鍵啟動.bat` 會自動更新。

### 進階 — 只看單篇 `.mmd` 純檔

需要 `tomoyukim.vscode-mermaid-editor` 套件（已在第一節安裝）：

1. 打開 `.mmd` 檔
2. `Ctrl+Shift+P` → **輸入 `Mermaid`** → 選 `Mermaid Editor: Preview`

若找不到 Mermaid 指令，重啟 VS Code 一次。

### 其他 — Obsidian / GitHub

- Obsidian：把 `outputs/summaries/` 開成 vault，安裝 Mermaid 外掛即可看 `.mmd`
- GitHub：`.mmd` 內容包進 ` ```mermaid ``` ` 區塊貼到 README，GitHub 原生渲染

---

## 五、工具一覽

| 檔案 | 角色 | 何時跑 |
|---|---|---|
| `yt_summary.py` | 主流程：URL → 摘要 .md + 字幕 .srt | 每次新影片 |
| `gen_mindmap.py` | 後處理：掃 .md 補對應 .mmd | 新摘要產生後 |
| `gen_index.py` | 後處理：重建 INDEX.md | 新摘要產生後 |
| `gen_overview.py` | 後處理：拼所有 .mmd 成「心智圖總覽.md」 | 新心智圖產生後 |
| `gen_markmap.py` | 後處理：用 markmap-cli 把 .md 轉互動式 HTML | 新摘要產生後 |
| `web_server.py` | Flask web 後端：URL 觸發 chain + 提供卡片網格 dashboard | 雙擊「開啟UI.bat」啟動 |
| `一鍵啟動.bat` | 把上面五個串起來（純命令列 fallback） | 不需 web UI 時用 |
| `prompts.py` | 集中 LLM prompt（摘要 + 心智圖兩組） | 想調 prompt 時改 |
| `summarizer.py` / `transcriber.py` / `config.py` | 主流程內部模組 | 不直接呼叫 |

### `gen_mindmap.py` 旗標

```bash
python gen_mindmap.py            # 只產缺的（已有 .mmd 跳過）
python gen_mindmap.py --force    # 全部重新產（覆蓋既有）
```

---

## 六、切換模型

編輯 `config.py` 的 `DEEPSEEK_MODEL`。摘要與心智圖共用同一個模型（兩組 prompt 都走 DeepSeek）。
