"""一次性批次工具：用 category_taxonomy.yaml 重判既有摘要的 category + subcategory。
只改 frontmatter 兩個 key，不動 summary / tags / generated_at / 其餘欄位。

用法：
  python recategorize.py                  # dry-run（預設）：印出每篇預測結果，不寫檔
  python recategorize.py --apply          # 實際寫入
  python recategorize.py --file <檔名.md> # 只跑單篇（強制 dry-run）
"""
import argparse
import json
import re
from pathlib import Path

import yaml
from openai import OpenAI

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, require_api_key
from prompts import TAXONOMY_TEXT

SUMMARIES_DIR = Path(__file__).parent / "outputs" / "summaries"
TAXONOMY_PATH = Path(__file__).parent / "category_taxonomy.yaml"

CLASSIFY_SYSTEM = "你是嚴謹的內容分類器。只輸出 JSON，不輸出任何其他文字。"

CLASSIFY_USER_TEMPLATE = """從下列 taxonomy 為這篇演講摘要選一組主類+子類。

【taxonomy】
{taxonomy_text}

【規則】
- 主類**必須**從上面選一個現有的，**不可新建**，**字串必須與 taxonomy 完全字面一致（含繁簡體與符號）**
- 子類優先選列出的；確實都不適合可新建簡短中文（4-8 字）

【演講標題】{title}

【精選摘要】
{summary_top}

【輸出格式】嚴格 JSON：
{{"category": "主類", "subcategory": "子類", "reason": "30字內為何選這個"}}"""


def load_taxonomy() -> dict:
    return yaml.safe_load(TAXONOMY_PATH.read_text(encoding="utf-8")) or {}


def split_frontmatter(text: str) -> tuple[str, str, dict]:
    """Return (fm_block, body, fm_dict). fm_block 含開頭 '---' 與結尾 '---'。"""
    if not text.startswith("---"):
        return "", text, {}
    end = text.find("\n---", 3)
    if end == -1:
        return "", text, {}
    fm_block = text[: end + 4]  # 含結尾 ---
    body = text[end + 4 :]
    if body.startswith("\n"):
        body = body[1:]
    try:
        fm_dict = yaml.safe_load(text[3:end]) or {}
    except yaml.YAMLError:
        fm_dict = {}
    return fm_block, body, fm_dict


def extract_summary_top(body: str) -> str:
    """抓 elevator pitch + 精選摘要段（到 `---` 分隔線或下個 ## 為止）。"""
    pitch_match = re.search(r"^>\s*(.+)$", body, re.MULTILINE)
    pitch = pitch_match.group(1).strip() if pitch_match else ""

    m = re.search(r"##\s*精選摘要[\s\S]*?(?=\n---\n|\n##\s)", body)
    if m:
        return (pitch + "\n\n" + m.group(0)).strip()
    return body[:1500]


_KEY_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$")


def update_frontmatter_keys(fm_block: str, updates: dict) -> str:
    """精準替換 fm_block 中 updates 涉及的 key。
    - 既有 key：值替換成新值；新值 == "" 則刪除整行
    - 新增 key：插在 category 行之後（若無 category 則插在最後一個 --- 前）
    其餘行原樣保留（含 datetime / list / 註解格式）。
    """
    lines = fm_block.split("\n")
    handled = set()
    out = []
    for line in lines:
        m = _KEY_LINE_RE.match(line)
        if m and m.group(1) in updates:
            key = m.group(1)
            new_val = updates[key]
            handled.add(key)
            if new_val == "" or new_val is None:
                continue  # 刪行
            out.append(f"{key}: {new_val}")
        else:
            out.append(line)

    pending = {k: v for k, v in updates.items() if k not in handled and v}
    if not pending:
        return "\n".join(out)

    result = []
    inserted = False
    for line in out:
        result.append(line)
        if not inserted and line.startswith("category:"):
            for k, v in pending.items():
                result.append(f"{k}: {v}")
            inserted = True
    if not inserted:
        # 找最後一個 --- 之前插
        for i in range(len(result) - 1, -1, -1):
            if result[i].strip() == "---":
                for k, v in pending.items():
                    result.insert(i, f"{k}: {v}")
                inserted = True
                break
    return "\n".join(result)


def classify(client: OpenAI, title: str, summary_top: str) -> dict:
    user_content = CLASSIFY_USER_TEMPLATE.format(
        taxonomy_text=TAXONOMY_TEXT,
        title=title,
        summary_top=summary_top,
    )
    resp = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": CLASSIFY_SYSTEM},
            {"role": "user", "content": user_content},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    raw = resp.choices[0].message.content
    return json.loads(raw)


def process_file(
    path: Path, client: OpenAI, taxonomy_cfg: dict, dry_run: bool
) -> dict:
    text = path.read_text(encoding="utf-8")
    fm_block, body, fm = split_frontmatter(text)

    if fm.get("_skip_index"):
        return {"skipped": True, "reason": "_skip_index flag"}

    title = path.stem
    summary_top = extract_summary_top(body)

    result = classify(client, title, summary_top)
    new_cat = (result.get("category") or "").strip()
    new_sub = (result.get("subcategory") or "").strip()
    reason = (result.get("reason") or "").strip()

    taxonomy = taxonomy_cfg.get("taxonomy") or {}
    if new_cat not in taxonomy:
        return {"error": f"LLM 出未知主類「{new_cat}」（不在 taxonomy）", "raw": result}

    old_cat = fm.get("category", "") or ""
    old_sub = fm.get("subcategory", "") or ""
    changed = (old_cat, old_sub) != (new_cat, new_sub)

    if not dry_run and changed:
        new_fm_block = update_frontmatter_keys(
            fm_block, {"category": new_cat, "subcategory": new_sub}
        )
        new_text = new_fm_block + "\n\n" + body.lstrip("\n")
        path.write_text(new_text, encoding="utf-8")

    return {
        "old": (old_cat, old_sub),
        "new": (new_cat, new_sub),
        "reason": reason,
        "changed": changed,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="實際寫入；預設只 dry-run")
    parser.add_argument("--file", type=str, help="只處理單一檔名（強制 dry-run）")
    args = parser.parse_args()

    require_api_key()
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    taxonomy_cfg = load_taxonomy()
    dry_run = not args.apply or bool(args.file)

    skip_files = set(taxonomy_cfg.get("skip_files") or [])

    if args.file:
        files = [SUMMARIES_DIR / args.file]
    else:
        files = sorted(
            p for p in SUMMARIES_DIR.glob("*.md")
            if p.name != "INDEX.md" and p.name not in skip_files
        )

    print(f"模式：{'DRY RUN' if dry_run else 'APPLY'} | 共 {len(files)} 篇")
    print()

    changed_n = unchanged_n = error_n = skipped_n = 0

    for i, path in enumerate(files, 1):
        try:
            r = process_file(path, client, taxonomy_cfg, dry_run)
        except Exception as e:
            print(f"[{i}/{len(files)}] {path.stem} -- ERROR: {type(e).__name__}: {e}")
            error_n += 1
            continue

        if r.get("skipped"):
            print(f"[{i}/{len(files)}] {path.stem} -- 跳過（{r['reason']}）")
            skipped_n += 1
            continue
        if r.get("error"):
            print(f"[{i}/{len(files)}] {path.stem} -- {r['error']}")
            error_n += 1
            continue

        old = " / ".join(x for x in r["old"] if x) or "(無)"
        new = " / ".join(x for x in r["new"] if x) or "(無)"
        mark = "→" if r["changed"] else "="
        print(f"[{i}/{len(files)}] {path.stem}")
        print(f"    {old}  {mark}  {new}    | {r['reason']}")
        if r["changed"]:
            changed_n += 1
        else:
            unchanged_n += 1

    print()
    print("=== 總結 ===")
    print(f"變動：{changed_n} | 不變：{unchanged_n} | 錯誤：{error_n} | 跳過：{skipped_n}")
    if dry_run and changed_n > 0:
        print("\n(dry-run。確認要套用，跑 `python recategorize.py --apply`)")


if __name__ == "__main__":
    main()
