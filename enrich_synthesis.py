"""為既有摘要補「## 融會貫通」段：把整篇整理成一段式的 elevator pitch（融會貫通）。
讀現有「## 完整拆解」當輸入跑 LLM，插入在「## 精選摘要」之前（全篇最上方的總覽）。
預設跳過已有「## 融會貫通」的篇。

用法：
  python enrich_synthesis.py                     # dry-run（預設）
  python enrich_synthesis.py --apply             # 寫入
  python enrich_synthesis.py --file <檔名.md>    # 單篇 dry-run
  python enrich_synthesis.py --apply --force     # 強制重產（覆蓋既有）
"""
import argparse
import re
import sys
from pathlib import Path

from openai import OpenAI

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, require_api_key
from enrich_intro import extract_breakdown  # 共用 8 段拆解抽取邏輯
from prompts import SYNTHESIS_SYSTEM, SYNTHESIS_USER_TEMPLATE
from recategorize import SUMMARIES_DIR, load_taxonomy, split_frontmatter

SYNTHESIS_HEADING = "## 融會貫通"
SYNTHESIS_HEADING_RE = re.compile(r"^##\s*融會貫通", re.MULTILINE)
# 插入點：精選摘要之前（全篇最上方的總覽）；fallback 完整拆解前；都無則 body 開頭
SUMMARY_HEADING_RE = re.compile(r"^##\s*精選摘要", re.MULTILINE)
BREAKDOWN_HEADING_RE = re.compile(r"^##\s*完整拆解", re.MULTILINE)
BREAKDOWN_FALLBACK_RE = re.compile(r"^##\s*一[、，,．.\s]", re.MULTILINE)


def has_synthesis(body: str) -> bool:
    return bool(SYNTHESIS_HEADING_RE.search(body))


def _insert_pos(body: str) -> int:
    for rx in (SUMMARY_HEADING_RE, BREAKDOWN_HEADING_RE, BREAKDOWN_FALLBACK_RE):
        m = rx.search(body)
        if m:
            return m.start()
    return 0


def insert_synthesis(body: str, synthesis_text: str) -> str:
    """把融會貫通段插入「## 精選摘要」之前。若已有舊段，先移除再插。"""
    block = f"\n\n{SYNTHESIS_HEADING}\n\n{synthesis_text.strip()}\n"

    if has_synthesis(body):
        m_old = SYNTHESIS_HEADING_RE.search(body)
        next_h2 = re.search(r"^##\s", body[m_old.end():], re.MULTILINE)
        end = m_old.end() + next_h2.start() if next_h2 else len(body)
        body = body[:m_old.start()] + body[end:]

    pos = _insert_pos(body)
    return body[:pos].rstrip() + block + "\n" + body[pos:].lstrip("\n")


def generate_synthesis(client: OpenAI, title: str, breakdown: str) -> str:
    user_content = SYNTHESIS_USER_TEMPLATE.format(title=title, full_breakdown=breakdown)
    resp = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": SYNTHESIS_SYSTEM},
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

    if has_synthesis(body) and not force:
        return {"skipped": True, "reason": "已有融會貫通（用 --force 重產）"}

    breakdown = extract_breakdown(body)
    if not breakdown:
        return {"error": "找不到「## 完整拆解」段，無法產融會貫通"}

    title = path.stem
    synthesis = generate_synthesis(client, title, breakdown)

    if not synthesis or len(synthesis) < 80:
        return {"error": f"LLM 回傳過短（{len(synthesis)} 字），疑似失敗"}

    if not dry_run:
        new_body = insert_synthesis(body, synthesis)
        new_text = fm_block + "\n\n" + new_body.lstrip("\n")
        path.write_text(new_text, encoding="utf-8")

    return {
        "synthesis_preview": synthesis[:200] + ("..." if len(synthesis) > 200 else ""),
        "synthesis_full": synthesis,
        "char_count": len(synthesis),
        "applied": not dry_run,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="實際寫入；預設 dry-run")
    parser.add_argument("--file", type=str, help="只處理單一檔名（強制 dry-run）")
    parser.add_argument("--force", action="store_true", help="強制重產既有融會貫通")
    parser.add_argument("--show-full", action="store_true", help="dry-run 印出完整內文")
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
            print(r["synthesis_full"])
            print()
        else:
            print(f"    {r['synthesis_preview']}")
        done_n += 1

    print()
    print("=== 總結 ===")
    print(f"產生：{done_n} | 跳過：{skipped_n} | 錯誤：{error_n}")
    if dry_run and done_n > 0:
        print("\n(dry-run。確認 OK 跑 `python enrich_synthesis.py --apply`)")


if __name__ == "__main__":
    main()
