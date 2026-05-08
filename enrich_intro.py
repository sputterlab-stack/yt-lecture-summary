"""為既有摘要補「## 導讀（線性帶入）」段，插入在 elevator pitch 後、精選摘要前。
讀現有「## 完整拆解」當輸入跑 LLM 產 600-900 字散文。
預設跳過已有「## 導讀」的篇。

用法：
  python enrich_intro.py                     # dry-run（預設）
  python enrich_intro.py --apply             # 寫入
  python enrich_intro.py --file <檔名.md>    # 單篇 dry-run
  python enrich_intro.py --apply --force     # 強制重產（覆蓋既有導讀）
"""
import argparse
import re
from pathlib import Path

from openai import OpenAI

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, require_api_key
from prompts import INTRO_SYSTEM, INTRO_USER_TEMPLATE
from recategorize import SUMMARIES_DIR, load_taxonomy, split_frontmatter

INTRO_HEADING = "## 導讀（線性帶入）"
SUMMARY_HEADING_RE = re.compile(r"^##\s*精選摘要", re.MULTILINE)
BREAKDOWN_HEADING_RE = re.compile(r"^##\s*完整拆解", re.MULTILINE)
# 早期版本沒「## 完整拆解」父標題，8 段直接用 ## 一、…，fallback 找 H2 一、
BREAKDOWN_FALLBACK_RE = re.compile(r"^##\s*一[、，,．.\s]", re.MULTILINE)
INTRO_HEADING_RE = re.compile(r"^##\s*導讀", re.MULTILINE)


def extract_breakdown(body: str) -> str:
    """抽 8 段拆解內容。優先找「## 完整拆解」父標題，fallback 找「## 一、...」(早期版本)。"""
    m = BREAKDOWN_HEADING_RE.search(body)
    if m:
        return body[m.start():].strip()
    m = BREAKDOWN_FALLBACK_RE.search(body)
    if m:
        return body[m.start():].strip()
    return ""


def has_intro(body: str) -> bool:
    return bool(INTRO_HEADING_RE.search(body))


def insert_intro(body: str, intro_text: str) -> str:
    """把導讀段插入 body：放在「## 精選摘要」前面（自動找位置）。
    若 body 已有「## 導讀」段，先移除舊的再插。"""
    intro_block = f"\n\n{INTRO_HEADING}\n\n{intro_text.strip()}\n"

    # 移除既有導讀段（從「## 導讀」到下個 H2 標題前）
    if has_intro(body):
        m_intro = INTRO_HEADING_RE.search(body)
        # 找下個 ## 標題作為終點
        next_h2 = re.search(r"^##\s", body[m_intro.end():], re.MULTILINE)
        if next_h2:
            end = m_intro.end() + next_h2.start()
        else:
            end = len(body)
        body = body[:m_intro.start()] + body[end:]

    # 找「## 精選摘要」位置插入導讀
    m_sum = SUMMARY_HEADING_RE.search(body)
    if not m_sum:
        # 沒精選摘要 — 插在「## 完整拆解」前；若也沒就放在 body 開頭
        m_break = BREAKDOWN_HEADING_RE.search(body)
        insertion_pos = m_break.start() if m_break else 0
    else:
        insertion_pos = m_sum.start()

    return body[:insertion_pos].rstrip() + intro_block + "\n" + body[insertion_pos:].lstrip("\n")


def generate_intro(client: OpenAI, title: str, breakdown: str) -> str:
    user_content = INTRO_USER_TEMPLATE.format(title=title, full_breakdown=breakdown)
    resp = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": INTRO_SYSTEM},
            {"role": "user", "content": user_content},
        ],
        temperature=0.4,
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

    if has_intro(body) and not force:
        return {"skipped": True, "reason": "已有導讀（用 --force 重產）"}

    breakdown = extract_breakdown(body)
    if not breakdown:
        return {"error": "找不到「## 完整拆解」段，無法產導讀"}

    title = path.stem
    intro = generate_intro(client, title, breakdown)

    if not intro or len(intro) < 200:
        return {"error": f"LLM 回傳過短（{len(intro)} 字），疑似失敗"}

    if not dry_run:
        new_body = insert_intro(body, intro)
        new_text = fm_block + "\n\n" + new_body.lstrip("\n")
        path.write_text(new_text, encoding="utf-8")

    return {
        "intro_preview": intro[:200] + ("..." if len(intro) > 200 else ""),
        "intro_full": intro,
        "char_count": len(intro),
        "applied": not dry_run,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="實際寫入；預設 dry-run")
    parser.add_argument("--file", type=str, help="只處理單一檔名（強制 dry-run）")
    parser.add_argument("--force", action="store_true", help="強制重產既有導讀")
    parser.add_argument("--show-full", action="store_true", help="dry-run 印出完整導讀內文")
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
            print(r["intro_full"])
            print()
        else:
            print(f"    {r['intro_preview']}")
        done_n += 1

    print()
    print("=== 總結 ===")
    print(f"產生：{done_n} | 跳過：{skipped_n} | 錯誤：{error_n}")
    if dry_run and done_n > 0:
        print("\n(dry-run。確認 OK 跑 `python enrich_intro.py --apply`)")


if __name__ == "__main__":
    main()
