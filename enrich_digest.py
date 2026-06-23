"""為既有摘要補/更新「## 乾貨摘要」段：30 秒 catch-up（帶時間戳 beats）。
讀對應的 .srt 逐字稿（含時間戳）跑 LLM，插入在最上方（pitch 後、導讀／精選摘要前）。
預設跳過已有「## 乾貨摘要」的篇；改版重產請加 --force。

用法：
  python enrich_digest.py                     # dry-run（預設）
  python enrich_digest.py --apply             # 寫入
  python enrich_digest.py --file <檔名.md>    # 單篇 dry-run
  python enrich_digest.py --apply --force     # 強制重產（改版／覆蓋既有）
"""
import argparse
import re
import sys
from pathlib import Path

from openai import OpenAI

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, require_api_key
from prompts import DIGEST_SYSTEM, DIGEST_USER_TEMPLATE
from recategorize import SUMMARIES_DIR, load_taxonomy, split_frontmatter

TRANSCRIPTS_DIR = Path(__file__).parent / "outputs" / "transcripts"

DIGEST_HEADING = "## 乾貨摘要"
DIGEST_HEADING_RE = re.compile(r"^##\s*乾貨摘要", re.MULTILINE)
# 乾貨放最上方：插在這三段中最先出現的那段之前
INTRO_HEADING_RE = re.compile(r"^##\s*導讀", re.MULTILINE)
SUMMARY_HEADING_RE = re.compile(r"^##\s*精選摘要", re.MULTILINE)
BREAKDOWN_HEADING_RE = re.compile(r"^##\s*完整拆解", re.MULTILINE)
BREAKDOWN_FALLBACK_RE = re.compile(r"^##\s*一[、，,．.\s]", re.MULTILINE)

_SRT_TS_RE = re.compile(r"(\d\d):(\d\d):(\d\d)")


def srt_to_timestamped_text(srt_path: Path) -> str:
    """把 .srt 轉成「[mm:ss] 文字」逐行，供 LLM 取時間戳用。"""
    raw = srt_path.read_text(encoding="utf-8")
    blocks = re.split(r"\n\s*\n", raw.strip())
    lines = []
    for b in blocks:
        bl = [x for x in b.strip().splitlines() if x.strip()]
        if len(bl) < 3:
            continue
        m = _SRT_TS_RE.match(bl[1])  # bl[1] = 時間軸行
        if not m:
            continue
        h, mm, ss = int(m.group(1)), int(m.group(2)), int(m.group(3))
        stamp = f"{h * 60 + mm:02d}:{ss:02d}"
        text = " ".join(x.strip() for x in bl[2:]).strip()
        if text:
            lines.append(f"[{stamp}] {text}")
    return "\n".join(lines)


def has_digest(body: str) -> bool:
    return bool(DIGEST_HEADING_RE.search(body))


def _first_section_pos(body: str) -> int:
    """乾貨插入點：導讀 / 精選摘要 / 完整拆解（含 fallback）中最先出現者的起點。
    都找不到則回 0（放 body 開頭）。"""
    candidates = []
    for rx in (INTRO_HEADING_RE, SUMMARY_HEADING_RE, BREAKDOWN_HEADING_RE, BREAKDOWN_FALLBACK_RE):
        m = rx.search(body)
        if m:
            candidates.append(m.start())
    return min(candidates) if candidates else 0


def insert_digest(body: str, digest_text: str) -> str:
    """把乾貨段插入 body 最上方（導讀／精選摘要／完整拆解最先者之前）。
    若已有「## 乾貨摘要」段，先移除舊的再插。"""
    digest_block = f"\n\n{DIGEST_HEADING}\n\n{digest_text.strip()}\n"

    # 移除既有乾貨段（從「## 乾貨摘要」到下個 H2 標題前）
    if has_digest(body):
        m_old = DIGEST_HEADING_RE.search(body)
        next_h2 = re.search(r"^##\s", body[m_old.end():], re.MULTILINE)
        end = m_old.end() + next_h2.start() if next_h2 else len(body)
        body = body[:m_old.start()] + body[end:]

    pos = _first_section_pos(body)
    return body[:pos].rstrip() + digest_block + "\n" + body[pos:].lstrip("\n")


def generate_digest(client: OpenAI, title: str, transcript: str) -> str:
    user_content = DIGEST_USER_TEMPLATE.format(title=title, transcript=transcript)
    resp = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": DIGEST_SYSTEM},
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

    if has_digest(body) and not force:
        return {"skipped": True, "reason": "已有乾貨摘要（用 --force 重產）"}

    srt_path = TRANSCRIPTS_DIR / f"{path.stem}.srt"
    if not srt_path.exists():
        return {"error": f"找不到逐字稿 {srt_path.name}，無法產帶時間戳乾貨"}
    transcript = srt_to_timestamped_text(srt_path)
    if not transcript:
        return {"error": "逐字稿解析為空，無法產乾貨"}

    title = path.stem
    digest = generate_digest(client, title, transcript)

    if not digest or len(digest) < 80:
        return {"error": f"LLM 回傳過短（{len(digest)} 字），疑似失敗"}

    if not dry_run:
        new_body = insert_digest(body, digest)
        new_text = fm_block + "\n\n" + new_body.lstrip("\n")
        path.write_text(new_text, encoding="utf-8")

    return {
        "digest_preview": digest[:200] + ("..." if len(digest) > 200 else ""),
        "digest_full": digest,
        "char_count": len(digest),
        "applied": not dry_run,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="實際寫入；預設 dry-run")
    parser.add_argument("--file", type=str, help="只處理單一檔名（強制 dry-run）")
    parser.add_argument("--force", action="store_true", help="強制重產既有乾貨摘要")
    parser.add_argument("--show-full", action="store_true", help="dry-run 印出完整乾貨內文")
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
            print(r["digest_full"])
            print()
        else:
            print(f"    {r['digest_preview']}")
        done_n += 1

    print()
    print("=== 總結 ===")
    print(f"產生：{done_n} | 跳過：{skipped_n} | 錯誤：{error_n}")
    if dry_run and done_n > 0:
        print("\n(dry-run。確認 OK 跑 `python enrich_digest.py --apply`)")


if __name__ == "__main__":
    main()
