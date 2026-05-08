# YT 演講摘要

> ⚠️ **跨平台支援**：Windows 用 `.bat`、Mac/Linux 用 `.sh`，邏輯一致。
> 主要在 Windows 11 開發測試；Mac/Linux 腳本歡迎回饋（PR welcome）。

## Features

- 🎙️ YouTube URL → Whisper 轉逐字稿（CUDA 加速）
- 🧠 DeepSeek V4-Pro 用「第一性原理」拆解演講者思考骨架（8 段結構分析 + 精選摘要）
- 🗺️ 自動產 mermaid 心智圖（`.mmd`）+ Markmap 互動式 HTML
- 🌐 Flask web dashboard：**多 URL 一次貼批次**轉換（Hybrid 並行：Whisper 排隊、下載+DeepSeek 並行）+ 多 task 進度卡 + tag 搜尋過濾
- 🎯 每篇自動濃縮 **核心主張一句話 + 這週能做的一個動作**（壓縮入口頻寬、觸發應用）
- 📖 **「導讀」按鈕**：800-2000 字線性敘事帶入（先有 narrative scaffolding 再去看心智圖網路）
- 📝 **「考自己」按鈕**（Active Recall）：合上摘要、用自己的話講，LLM 對齊原內容指出你漏掉/抓錯的點
- 🗂️ 兩層 taxonomy 治理（`category_taxonomy.yaml` 主類+子類 + alias 折疊舊類別）
- 📚 frontmatter 自動分類 + INDEX 二層索引

---

輸入 YouTube 演講 URL，自動：

1. 下載音訊 → Whisper 轉逐字稿
2. DeepSeek V4-Pro 用第一性原理拆解 → 產 `.md` 摘要 + `.srt` 字幕
3. 產 mermaid 心智圖 `.mmd`（可在 VS Code 預覽）
4. 更新 `INDEX.md` 分類索引

---

## 一、安裝

### Quick Setup（跨平台一鍵）

**Windows**：雙擊 `setup.bat`

**Mac / Linux**：
```bash
chmod +x setup.sh
./setup.sh
```

setup script 會：偵測你的 Python → 建 `./venv` 隔離環境 → 裝所有依賴。

完成後**永遠用 venv 內的 python**（launcher 已寫死路徑），不依賴系統 PATH。

### GPU 加速（可選，預設 CPU torch）

預設安裝 CPU 版 torch（Whisper 轉文字會慢一點）。NVIDIA CUDA 加速：

**Windows**:
```
venv\Scripts\python.exe -m pip install --upgrade torch --index-url https://download.pytorch.org/whl/cu121
```

**Mac / Linux**:
```bash
./venv/bin/python -m pip install --upgrade torch --index-url https://download.pytorch.org/whl/cu121
```

（CUDA 版本依你的 NVIDIA driver；上述 cu121 是 CUDA 12.1，可換成 cu118 等）

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

### Web Dashboard（推薦）

- Windows：雙擊 `開啟UI.bat`
- Mac/Linux：`./run-ui.sh`

啟 Flask server → 自動開瀏覽器 `localhost:5000` → URL 輸入框轉新影片 + 卡片網格瀏覽既有摘要。

- **上方 textarea（多行 URL）**：一行一個，可貼一批 → 點「轉換」→ 立刻可繼續貼下一批；下方 task 卡片網格累積顯示每筆 5 步進度（藍=跑、黃=等批次、綠=完成、紅=失敗）
- **Hybrid 並行**：Whisper 階段全域 lock 串行（防 GPU OOM），下載 + DeepSeek 階段並行；上限以 `PARALLEL_LIMIT` env 控制（預設 3）
- **下方卡片網格**：所有摘要按主類分群；卡片正面顯示**核心主張**（thesis）+ **💡 這週能做的動作**（黃色 box），點展開看 elevator pitch / 講者 / tags
- **「📖 導讀」按鈕**（卡片右上書本 icon）：跳 modal 顯示 800-2000 字線性敘事，建議先看完導讀再看心智圖（網狀結構）
- **「考自己」按鈕**（卡片右上問號 icon）：跳 modal、合上摘要寫核心論點、LLM 對齊評估「你抓對的 / 漏掉的 / 教練引導」（Esc 關閉）
- **即時過濾**：搜尋框（標題 / 講者 / tag）+ 多選 tag chips（OR 邏輯）+ 清除按鈕

視窗保持開啟即 server 運作，關掉視窗即停止。

### 命令列 Chain（fallback）

- Windows：雙擊 `一鍵啟動.bat`
- Mac/Linux：`./run-cli.sh`

整套 chain 跑（yt_summary → gen_mindmap → gen_index → gen_overview → gen_markmap）。

---

## 三、輸出檔案

| 檔案 | 路徑 | 用途 |
|---|---|---|
| 摘要 | `outputs/summaries/{標題}.md` | 第一性原理 8 段拆解 + 精選摘要 |
| 心智圖 | `outputs/summaries/{標題}.mmd` | mermaid mindmap，視覺化吸收 |
| 字幕 | `outputs/transcripts/{標題}.srt` | 帶時間軸逐字稿 |
| 索引 | `outputs/summaries/INDEX.md` | 按 category 分群的總表 |

`.md` 開頭含 YAML frontmatter：

| 欄位 | 說明 |
|---|---|
| `source` / `yt_title` / `speaker` / `language` / `duration` / `generated_at` / `model` | 元資料 |
| `category` | 主類（必須是 `category_taxonomy.yaml` 列出的） |
| `subcategory` | 子類（taxonomy 列出的，或合理新建 4-8 字） |
| `thesis` | 一句話核心主張（30 字內，立場句） |
| `weekly_action` | 這週能做的具體小動作（50 字內，5 分鐘可開始） |
| `tags` | 3-6 個自由標籤（細分主題、概念、講者特色） |

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
| `yt_summary.py` | 主流程 CLI：URL → 摘要 .md + 字幕 .srt | 命令列單一影片 |
| `web_server.py` | Flask web 後端：多 URL 批次、Hybrid 並行、`/challenge` endpoint | 雙擊「開啟UI.bat」啟動 |
| `gen_mindmap.py` | 掃 .md 補對應 .mmd（支援 `--force`） | 新摘要產生後 |
| `gen_index.py` | 套 `category_taxonomy.yaml` 重建 INDEX.md（二層） | 新摘要產生後 |
| `gen_overview.py` | 拼所有 .mmd 成「心智圖總覽.md」 | 新心智圖產生後 |
| `gen_markmap.py` | 用 markmap-cli 把 .md 轉互動式 HTML | 新摘要產生後 |
| `recategorize.py` | 一次性批次：用 taxonomy 重判既有摘要的 category + subcategory | 改 taxonomy 後 |
| `enrich_summary.py` | 一次性批次：補既有摘要的 thesis + weekly_action | 加新欄位後 |
| `enrich_intro.py` | 一次性批次：補既有摘要的「## 導讀（線性帶入）」段（800-2000 字散文） | 加新欄位後 |
| `category_taxonomy.yaml` | 主類+子類二層樹 + alias 折疊 + skip_files 黑名單 | 想調分類時改 |
| `prompts.py` | 集中所有 LLM prompts（摘要、心智圖、考自己） | 想調 prompt 時改 |
| `一鍵啟動.bat` / `run-cli.sh` | 命令列 chain（不用 web UI） | fallback |
| `setup.bat` / `setup.sh` | 一次性建 venv + 裝依賴 | 第一次 clone 後跑 |
| `summarizer.py` / `transcriber.py` / `config.py` | 主流程內部模組 | 不直接呼叫 |

### 批次工具旗標

```bash
# 重新分類（讀取 category_taxonomy.yaml + 跑 LLM 重判）
python recategorize.py            # dry-run 預覽
python recategorize.py --apply    # 寫入 frontmatter
python recategorize.py --file 標題.md   # 單篇 dry-run

# 補 thesis + weekly_action（既有摘要欠缺時）
python enrich_summary.py          # dry-run
python enrich_summary.py --apply  # 寫入

# 補導讀段（800-2000 字線性敘事）
python enrich_intro.py            # dry-run（預設跳過已有導讀）
python enrich_intro.py --apply    # 寫入
python enrich_intro.py --apply --force  # 強制重產所有篇

# 心智圖
python gen_mindmap.py             # 只產缺的
python gen_mindmap.py --force     # 全部重產
```

---

## 六、分類治理（taxonomy）

`category_taxonomy.yaml` 定義主類 + 子類二層樹：

```yaml
taxonomy:
  投資/經濟:
    - 個股拆解
    - 宏觀經濟
    - ...
  AI/科技:
    - AI 應用/Agent
    - ...
  # 其他主類

aliases:           # 過時主類折疊到 canonical
  政治/歷史: 思想/個人成長
  社會/政治: 思想/個人成長

skip_files:        # gen_index 跳過（工具產物）
  - 心智圖總覽.md
```

**新增主類**：直接編 yaml → 重啟 web_server。**新增子類**：edit yaml 即可（gen_index 用 yaml 順序排）。**改 taxonomy 後**：跑 `python recategorize.py --apply` 重判所有舊摘要。

---

## 七、Hybrid 並行（多工）

Web dashboard 接收多 URL 時三層並行控制：

| 階段 | 控制 |
|---|---|
| 整體 task 數 | `PARALLEL_LIMIT` env（預設 3） |
| 下載音訊 | 並行（網路 IO） |
| Whisper 轉文字 | 全域 lock 串行（防 GPU OOM） |
| DeepSeek 摘要 | `DEEPSEEK_PARALLEL` semaphore（預設 3，撞 rate limit 時降至 1） |
| 寫檔 + 心智圖 | 並行 |
| 批次後處理（gen_index/overview/markmap） | 全部 task 完成後 debounce 跑一次 |

```bash
# 自訂並行數
PARALLEL_LIMIT=2 DEEPSEEK_PARALLEL=2 python web_server.py
```

---

## 八、導讀 + Active Recall（學習迴路）

兩個學習通道是互補的：

| 通道 | 形式 | 角色 |
|---|---|---|
| 📖 **導讀** | 800-2000 字線性敘事 | 入口前的 narrative scaffolding（先建立 mental model） |
| 🗺️ **心智圖** | 網狀結構（mermaid mindmap） | 概念之間的連結（建立思維網路） |
| 📝 **考自己** | 合上摘要寫 / LLM 對齊 | 看完後 active recall（把辨識升級為回憶） |

建議使用順序：**讀 thesis → 點 📖 導讀讀完 → 看心智圖 → 點考自己驗證自己抓到什麼漏掉什麼**。

## 九、Active Recall（考自己）— 詳細

每張摘要卡右上「？」按鈕點下去：跳 modal、合上摘要寫核心論點、LLM 對齊評估。

回傳：
- ✓ **抓對的點**（你寫對的核心）
- ✗ **漏掉的點**（你以為懂但沒抓到）
- 💬 **教練引導**（下次該注意什麼）

API：`POST /challenge` body `{filename, answer}` → `{got_right, missed, coaching}`。

---

## 十、切換模型

編輯 `config.py` 的 `DEEPSEEK_MODEL`。摘要 / 心智圖 / 考自己 / 重分類 / enrich 共用同一模型。
