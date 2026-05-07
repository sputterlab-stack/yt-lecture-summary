# 集中管理 LLM prompt 常數
from pathlib import Path

import yaml

_TAXONOMY_PATH = Path(__file__).parent / "category_taxonomy.yaml"


def _load_taxonomy() -> dict:
    if not _TAXONOMY_PATH.exists():
        return {"taxonomy": {}, "aliases": {}, "skip_files": []}
    return yaml.safe_load(_TAXONOMY_PATH.read_text(encoding="utf-8")) or {}


def _build_taxonomy_text(taxonomy: dict) -> str:
    """二層樹序列化成 prompt 可讀文字。"""
    lines = []
    for main, subs in taxonomy.items():
        if subs:
            sub_str = " / ".join(subs)
            lines.append(f"- **{main}**：{sub_str}")
        else:
            lines.append(f"- **{main}**（子類自由命名）")
    return "\n".join(lines)


_TAXONOMY = _load_taxonomy()
TAXONOMY_TEXT = _build_taxonomy_text(_TAXONOMY.get("taxonomy", {}))


SYSTEM_PROMPT = """你是學術級演講內容拆解專家。你的任務是用第一性原理（First Principles）拆解演講逐字稿，產出「上下兩層」結構：上層精選摘要（沒時間就看這段），下層 8 段完整拆解（深入學習用）。"""

# 注意：USER_PROMPT_TEMPLATE 用 .format(yt_title=..., source_url=..., ...) 被呼叫，
# {TAXONOMY_TEXT} 必須在這層用 string replace 預先填入（不走 .format()，避免被當成 placeholder）
_RAW_USER_PROMPT = """【分類提示】
從以下二層 taxonomy 選一組「主類 + 子類」：

{TAXONOMY_TEXT}

規則：
- **主類必須**從上面選一個現有的，**不可新建**
- 子類優先選列出的現有子類；確實都不適合可新建簡短中文（4-8 字）
- 若主類後標「子類自由命名」，則子類自訂

tags 是 3-6 個自由標籤，描述細分主題、關鍵概念、講者特色，方便未來搜尋。

【拆解原則】
1. 不是流水帳重述，是還原演講者的「思考鏈」
2. 找出底層假設（演講者預設了什麼前提才能推出結論）
3. 區分「事實聲明」「推論」「個人立場」「實踐建議」
4. 反例與對立觀點必須保留（演講者如何回應 X 反對意見）
5. 任何外語原文，摘要全部用繁體中文表達；金句段落保留原文 + 中譯
6. 上層精選摘要約 300-500 字
7. 下層 8 段完整拆解約 1500-2500 字

【元資料】
- YT 原標題：{yt_title}
- 來源 URL：{source_url}
- 影片時長：{duration}
- 偵測語言：{language}

【逐字稿】
{transcript}

【輸出格式】嚴格 JSON（無 markdown code fence），格式如下：
{{
  "filename": "10-20字精準繁體中文標題（不含副檔名與標點 \\\\ / : * ? \\" < > |）",
  "speaker": "若可從內容識別則填講者姓名，否則填 '未知'",
  "language": "原語言（中文/英文/日文等）",
  "category": "從 taxonomy 選的主類（必須是上面列出的）",
  "subcategory": "從 taxonomy 該主類下的子類（或合理新命名 4-8 字）",
  "tags": ["細分標籤", "跨主題標籤"],
  "thesis": "演講者最核心的主張（30 字內，立場句不是描述句；要能單獨拿出來引用，例如『美聯儲必須立刻降息以避免長債失控』）",
  "weekly_action": "這週能立刻嘗試的一個具體小動作（不超過 50 字，要能在 5 分鐘內開始做；不是抽象建議，是可執行的單一動作，例如『今天記錄一次自己刷手機的衝動以及當時的情緒』）",
  "summary_md": "完整 Markdown 字串，依下列結構生成"
}}

【summary_md 結構規範】

summary_md **不可**包含 H1 標題（不要寫「# 標題」，外層程式會自動加）。
summary_md 從以下內容開始，依序生成：

第一行：30 字內的 elevator pitch，使用 > blockquote 格式

## 精選摘要（沒時間先看這段）

### 核心重點
3-5 條編號清單，每條格式為：
1. **{{重點標題}}**：{{1-2 行說明}}

### 核心概念
3-6 個 bullet points，每個格式為：
- **{{概念名}}**：{{一行內定義 10-30 字}}

---

## 完整拆解（深入學習）

### 一、核心問題（演講要回答的根本問題）
### 二、底層假設（推論前提）
### 三、核心概念定義
### 四、論證鏈（從假設推到結論的路徑）
### 五、關鍵證據與案例
### 六、反例與對立觀點的處理
### 七、可操作的洞察（讀者聽完能做什麼）
### 八、金句精華（原文 + 中譯，3-5 句）

8 段每段一個 H3 標題，標題文字完全一致，順序不可調換。"""

# 把 TAXONOMY_TEXT 預先填入；其餘 {yt_title} {transcript} 等仍由 summarizer.py 的 .format() 處理
USER_PROMPT_TEMPLATE = _RAW_USER_PROMPT.replace("{TAXONOMY_TEXT}", TAXONOMY_TEXT)


# === Active Recall 挑戰 prompt（web_server /challenge endpoint 用）===

RECALL_CHALLENGE_SYSTEM = """你是嚴格但有同理心的學習教練。使用者剛看完一篇演講摘要，現在以「合上摘要、用自己的話講」的方式接受 active recall 挑戰。你的工作是把使用者的回答跟原摘要對齊，找出他抓到什麼、漏什麼。只輸出 JSON，不輸出任何其他文字。"""

RECALL_CHALLENGE_USER_TEMPLATE = """對照原摘要評估這位使用者的「合上書」回答。

【任務】
1. **got_right**：使用者抓對的核心點（list[str]，每項 1 句、20 字內）。只列「真的抓到」的點，不要太寬鬆。
2. **missed**：使用者**漏掉但很重要**的核心點（list[str]，每項 1-2 句、40 字內）。只列「會影響理解」的關鍵漏失，雞毛蒜皮不用列。每項用「忽略了 X」開頭。
3. **coaching**：一句話引導下次該注意的方向（30 字內，鼓勵性而非批評，例如「下次注意分辨主張與證據的差異」）。

【特殊情況】
- 使用者根本沒寫東西或寫亂碼 → got_right=[], missed 列 3 個最關鍵點, coaching="建議先閱讀後再嘗試挑戰"
- 使用者寫得很完整 → missed 可能 1 個甚至 0 個（不要硬挑毛病）
- 使用者偏離主題 → got_right=[], missed 列原摘要核心點, coaching 提醒回到主題

【演講原摘要（精選段）】
{summary_top}

【使用者的回答】
{user_answer}

【輸出格式】嚴格 JSON：
{{"got_right": ["...", "..."], "missed": ["...", "..."], "coaching": "..."}}"""


# === Mermaid 心智圖 prompt（gen_mindmap.py 用）===

MINDMAP_SYSTEM_PROMPT = """你是 mermaid mindmap 心智圖專家。將演講摘要 .md 轉成 mermaid mindmap 語法，供本地 VS Code / Obsidian / GitHub 渲染。"""

MINDMAP_USER_PROMPT_TEMPLATE = """將以下演講摘要轉換為 mermaid mindmap 心智圖。

【規格】
- 用 mermaid `mindmap` 語法（不是 graph TD）
- 根節點：演講標題（精簡至 < 15 字），用 `((標題))` 形狀，可用 `<br/>` 換行
- 第一層：5-7 個主要章節（依該篇實際結構，常見有：核心問題 / 底層假設 / 核心概念 / 論證 / 證據 / 反例 / 洞察 / 金句）
- 第二層以下：每章節 2-4 個子節點
- 節點文字 < 25 字（太長會擠）
- 總節點數 30-50（少了空，多了亂）
- 善用 mindmap 形狀：`((中心))` / `(分支)` / `[葉節點]`
- 縮排：兩格一層

【輸出格式】
**只輸出 mermaid mindmap 語法本身**，不要有任何前後文字、不要 markdown code fence（不要寫 ```mermaid）、不要解釋。第一行必須是 `mindmap`。

【演講摘要】
{markdown}
"""
