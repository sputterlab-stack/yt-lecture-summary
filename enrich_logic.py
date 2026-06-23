"""為既有摘要補「## 邏輯拆解」段：第一性原理推導鏈 × 多視角壓力測試。
讀現有「## 完整拆解」當輸入跑 LLM，插入在「## 完整拆解」之前（深度版伴隨原 8 段）。
預設跳過已有「## 邏輯拆解」的篇。

用法：
  python enrich_logic.py                     # dry-run（預設）
  python enrich_logic.py --apply             # 寫入
  python enrich_logic.py --file <檔名.md>    # 單篇 dry-run
  python enrich_logic.py --apply --force     # 強制重產（覆蓋既有）
"""
import argparse
import re
import sys
from pathlib import Path

from openai import OpenAI

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, require_api_key
from enrich_intro import extract_breakdown  # 共用 8 段拆解抽取邏輯
from prompts import LOGIC_SYSTEM, LOGIC_USER_TEMPLATE
from recategorize import SUMMARIES_DIR, load_taxonomy, split_frontmatter

LOGIC_HEADING = "## 邏輯拆解"
LOGIC_HEADING_RE = re.compile(r"^##\s*邏輯拆解", re.MULTILINE)
# 插入點：完整拆解之前（深度版緊鄰原 8 段）；fallback 精選摘要前；都無則 body 開頭
BREAKDOWN_HEADING_RE = re.compile(r"^##\s*完整拆解", re.MULTILINE)
BREAKDOWN_FALLBACK_RE = re.compile(r"^##\s*一[、，,．.\s]", re.MULTILINE)
SUMMARY_HEADING_RE = re.compile(r"^##\s*精選摘要", re.MULTILINE)


def has_logic(body: str) -> bool:
    return bool(LOGIC_HEADING_RE.search(body))


def _insert_pos(body: str) -> int:
    for rx in (BREAKDOWN_HEADING_RE, BREAKDOWN_FALLBACK_RE, SUMMARY_HEADING_RE):
        m = rx.search(body)
        if m:
            return m.start()
    return 0


def insert_logic(body: str, logic_text: str) -> str:
    """把邏輯拆解段插入「## 完整拆解」之前。若已有舊段，先移除再插。"""
    logic_block = f"\n\n{LOGIC_HEADING}\n\n{logic_text.strip()}\n"

    if has_logic(body):
        m_old = LOGIC_HEADING_RE.search(body)
        next_h2 = re.search(r"^##\s", body[m_old.end():], re.MULTILINE)
        end = m_old.end() + next_h2.start() if next_h2 else len(body)
        body = body[:m_old.start()] + body[end:]

    pos = _insert_pos(body)
    return body[:pos].rstrip() + logic_block + "\n" + body[pos:].lstrip("\n")


def generate_logic(client: OpenAI, title: str, breakdown: str) -> str:
    user_content = LOGIC_USER_TEMPLATE.format(title=title, full_breakdown=breakdown)
    resp = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": LOGIC_SYSTEM},
            {"role": "user", "content": user_content},
        ],
        temperature=0.3,
    )
    text = resp.choices[0].message.content.strip()
    # 防 LLM 偶爾加 code fence
    if text.startswith("```"):
        text = re.sub(r"^```[\w]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def process_file(path: Path, client: OpenAI, dry_run: bool, force: bool) -> dict:
    text = path.read_text(encoding="utf-8")
    fm_block, body, fm = split_frontmatter(text)

    if fm.get("_skip_index"):
        return {"skipped": True, "reason": "_skip_index flag"}

    if has_logic(body) and not force:
        return {"skipped": True, "reason": "已有邏輯拆解（用 --force 重產）"}

    breakdown = extract_breakdown(body)
    if not breakdown:
        return {"error": "找不到「## 完整拆解」段，無法產邏輯拆解"}

    title = path.stem
    logic = generate_logic(client, title, breakdown)

    if not logic or len(logic) < 150:
        return {"error": f"LLM 回傳過短（{len(logic)} 字），疑似失敗"}

    if not dry_run:
        new_body = insert_logic(body, logic)
        new_text = fm_block + "\n\n" + new_body.lstrip("\n")
        path.write_text(new_text, encoding="utf-8")

    return {
        "logic_preview": logic[:200] + ("..." if len(logic) > 200 else ""),
        "logic_full": logic,
        "char_count": len(logic),
        "applied": not dry_run,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="實際寫入；預設 dry-run")
    parser.add_argument("--file", type=str, help="只處理單一檔名（強制 dry-run）")
    parser.add_argument("--force", action="store_true", help="強制重產既有邏輯拆解")
    parser.add_argument("--show-full", action="store_true", help="dry-run 印出完整邏輯內文")
    args = parser.parse_args()

    # Windows 主控台/重導向預設 cp950，遇到非 cp950 漢字 print 會 UnicodeEncodeError 中斷整批；強制 utf-8 輸出
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

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

    print(f"模式：{'DRY RUN' if dry_run else 'APPLY'} | force={args.force} | 共 {len(files)} 篇")
    print()

    done_n = skipped_n = error_n = 0
    for i, path in enumerate(files, 1):
        try:
            r = process_file(path, client, dry_run, args.force)
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

        print(f"[{i}/{len(files)}] {path.stem}  ({r['char_count']} 字)")
        if args.show_full or len(files) == 1:
            print()
            print(r["logic_full"])
            print()
        else:
            print(f"    {r['logic_preview']}")
        done_n += 1

    print()
    print("=== 總結 ===")
    print(f"產生：{done_n} | 跳過：{skipped_n} | 錯誤：{error_n}")
    if dry_run and done_n > 0:
        print("\n(dry-run。確認 OK 跑 `python enrich_logic.py --apply`)")


if __name__ == "__main__":
    main()
