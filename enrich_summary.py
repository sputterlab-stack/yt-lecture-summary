"""一次性批次工具：為既有摘要補 thesis + weekly_action 兩個 frontmatter 欄位。
不重產 summary、不動 tags / category / 其餘欄位。

用法：
  python enrich_summary.py                     # dry-run（預設）
  python enrich_summary.py --apply             # 實際寫入
  python enrich_summary.py --file <檔名.md>    # 單篇強制 dry-run
"""
import argparse
import json
from pathlib import Path

from openai import OpenAI

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, require_api_key
from recategorize import (
    SUMMARIES_DIR,
    extract_summary_top,
    load_taxonomy,
    split_frontmatter,
    update_frontmatter_keys,
)

ENRICH_SYSTEM = "你是內容濃縮大師。只輸出 JSON，不輸出任何其他文字。"

ENRICH_USER_TEMPLATE = """為這篇演講摘要產出兩個濃縮欄位。

【thesis】演講者最核心的主張
- 30 字內
- **立場句**不是描述句（不是「這篇講 X」，是「演講者主張 X」）
- 要能單獨拿出來引用
- 避免「這篇演講...」「演講者認為...」這類描述性開頭，直接給結論本身

【weekly_action】這週能立刻嘗試的具體小動作
- 不超過 50 字
- 從演講內容濃縮一個**可執行單一動作**
- 5 分鐘內能開始做
- **不是**抽象建議（如「減少滑手機」），**是**具體動作（如「今天記錄一次刷手機的衝動以及當時情緒」）
- 若演講內容偏理論（如歷史分析、宏觀預測）難轉應用，就濃縮成「需要思考的一個關鍵問題」

【演講標題】{title}

【演講精選摘要】
{summary_top}

【輸出格式】嚴格 JSON：
{{"thesis": "...", "weekly_action": "..."}}"""


def _yaml_quote(s: str) -> str:
    """安全編碼成 YAML double-quoted scalar。"""
    return json.dumps(str(s), ensure_ascii=False)


def enrich(client: OpenAI, title: str, summary_top: str) -> dict:
    user_content = ENRICH_USER_TEMPLATE.format(title=title, summary_top=summary_top)
    resp = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": ENRICH_SYSTEM},
            {"role": "user", "content": user_content},
        ],
        response_format={"type": "json_object"},
        temperature=0.3,
    )
    return json.loads(resp.choices[0].message.content)


def process_file(path: Path, client: OpenAI, dry_run: bool) -> dict:
    text = path.read_text(encoding="utf-8")
    fm_block, body, fm = split_frontmatter(text)

    if fm.get("_skip_index"):
        return {"skipped": True, "reason": "_skip_index flag"}

    title = path.stem
    summary_top = extract_summary_top(body)

    result = enrich(client, title, summary_top)
    new_thesis = (result.get("thesis") or "").strip()
    new_action = (result.get("weekly_action") or "").strip()

    old_thesis = (fm.get("thesis") or "").strip()
    old_action = (fm.get("weekly_action") or "").strip()
    changed = (old_thesis, old_action) != (new_thesis, new_action)

    if not dry_run and changed:
        updates = {
            "thesis": _yaml_quote(new_thesis) if new_thesis else "",
            "weekly_action": _yaml_quote(new_action) if new_action else "",
        }
        new_fm_block = update_frontmatter_keys(fm_block, updates)
        new_text = new_fm_block + "\n\n" + body.lstrip("\n")
        path.write_text(new_text, encoding="utf-8")

    return {
        "old": (old_thesis, old_action),
        "new": (new_thesis, new_action),
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
    skip_files = set(taxonomy_cfg.get("skip_files") or [])
    dry_run = not args.apply or bool(args.file)

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
            r = process_file(path, client, dry_run)
        except Exception as e:
            print(f"[{i}/{len(files)}] {path.stem} -- ERROR: {type(e).__name__}: {e}")
            error_n += 1
            continue

        if r.get("skipped"):
            print(f"[{i}/{len(files)}] {path.stem} -- 跳過（{r['reason']}）")
            skipped_n += 1
            continue

        print(f"[{i}/{len(files)}] {path.stem}")
        print(f"    thesis : {r['new'][0]}")
        print(f"    action : {r['new'][1]}")
        if r["changed"]:
            changed_n += 1
        else:
            unchanged_n += 1

    print()
    print("=== 總結 ===")
    print(f"變動：{changed_n} | 不變：{unchanged_n} | 錯誤：{error_n} | 跳過：{skipped_n}")
    if dry_run and changed_n > 0:
        print("\n(dry-run。確認 OK 跑 `python enrich_summary.py --apply`)")


if __name__ == "__main__":
    main()
