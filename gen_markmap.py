# 掃 outputs/summaries/*.md 產對應 markmap 互動式 HTML
# 用法：
#   python gen_markmap.py            # 只產缺的 .html
#   python gen_markmap.py --force    # 重新產所有 .html（覆蓋既有）
import argparse
import subprocess
import sys
from pathlib import Path

# Windows 終端可能是 cp950，強制 stdout/stderr 改為 utf-8 以支援所有中文字
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import yaml

SUMMARIES_DIR = Path(__file__).parent / "outputs" / "summaries"
MARKMAP_DIR = Path(__file__).parent / "outputs" / "markmap"

EXCLUDE = {"INDEX.md", "心智圖總覽.md"}

CATEGORY_ORDER = [
    "投資/經濟",
    "AI/科技",
    "演講/溝通",
    "思想/個人成長",
    "健康/科學",
]


def parse_frontmatter(path: Path) -> dict:
    """Return frontmatter dict from a .md file, or empty dict on failure."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end == -1:
        return {}
    try:
        return yaml.safe_load(text[3:end]) or {}
    except yaml.YAMLError:
        return {}


def find_targets(force: bool) -> list[Path]:
    targets = []
    for md in sorted(SUMMARIES_DIR.glob("*.md")):
        if md.name in EXCLUDE:
            continue
        html = MARKMAP_DIR / (md.stem + ".html")
        if force or not html.exists():
            targets.append(md)
    return targets


def run_markmap(md_path: Path, html_path: Path) -> tuple[bool, str]:
    """Run markmap CLI to convert md to html. Returns (success, error_msg)."""
    cmd = f'markmap "{md_path}" --no-open -o "{html_path}"'
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        err = result.stderr.strip() or result.stdout.strip() or "未知錯誤"
        return False, err
    return True, ""


def build_index_html():
    """產出 outputs/markmap/index.html，列出所有 .html 並按 category 分群。"""
    htmls = sorted(MARKMAP_DIR.glob("*.html"), key=lambda p: p.name)
    htmls = [h for h in htmls if h.name != "index.html"]

    # 讀各 .md 的 frontmatter，建立 stem → category 對照
    cat_map: dict[str, str] = {}
    for h in htmls:
        md_path = SUMMARIES_DIR / (h.stem + ".md")
        if md_path.exists():
            fm = parse_frontmatter(md_path)
            cat_map[h.stem] = fm.get("category") or "未分類"
        else:
            cat_map[h.stem] = "未分類"

    # 分群
    grouped: dict[str, list[Path]] = {}
    for h in htmls:
        cat = cat_map[h.stem]
        grouped.setdefault(cat, []).append(h)

    known = [c for c in CATEGORY_ORDER if c in grouped]
    extra = sorted(c for c in grouped if c not in CATEGORY_ORDER and c != "未分類")
    uncategorised = ["未分類"] if "未分類" in grouped else []
    order = known + extra + uncategorised

    # 產 HTML
    sections = []
    for cat in order:
        items = grouped[cat]
        links = "\n".join(
            f'    <li><a href="./{h.name}">{h.stem}</a></li>'
            for h in items
        )
        sections.append(f"  <h2>{cat}</h2>\n  <ul>\n{links}\n  </ul>")

    sections_html = "\n".join(sections)
    total = len(htmls)

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="UTF-8">
  <title>YT 演講心智圖</title>
  <style>
    body {{ font-family: sans-serif; max-width: 700px; margin: 40px auto; padding: 0 20px; }}
    h1 {{ font-size: 1.6em; margin-bottom: 4px; }}
    p.meta {{ color: #666; font-size: 0.9em; margin-top: 0; }}
    h2 {{ font-size: 1.1em; margin-top: 1.4em; margin-bottom: 6px; color: #444; }}
    ul {{ list-style: disc; padding-left: 1.4em; }}
    li {{ margin: 4px 0; }}
    a {{ color: #1a6bc4; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
  <h1>YT 演講心智圖</h1>
  <p class="meta">共 {total} 篇，由 gen_markmap.py 自動產出</p>
{sections_html}
</body>
</html>
"""

    index_path = MARKMAP_DIR / "index.html"
    index_path.write_text(html, encoding="utf-8")
    print(f"index.html 已更新：{total} 篇")


def main():
    parser = argparse.ArgumentParser(description="掃 .md 產 markmap 互動式 HTML")
    parser.add_argument(
        "--force", action="store_true", help="重新產生所有 .html（覆蓋既有）"
    )
    args = parser.parse_args()

    MARKMAP_DIR.mkdir(parents=True, exist_ok=True)

    targets = find_targets(force=args.force)
    if not targets:
        print("沒有需要產生的 .html（全部已存在；用 --force 強制重新）")
        build_index_html()
        return

    total = len(targets)
    print(f"準備產生 {total} 個 markmap HTML")

    success = 0
    fail = 0
    for i, md in enumerate(targets, 1):
        html_path = MARKMAP_DIR / (md.stem + ".html")
        print(f"  [{i}/{total}] {md.stem} ", end="", flush=True)
        ok, err = run_markmap(md, html_path)
        if ok:
            print("OK")
            success += 1
        else:
            print(f"FAIL（{err}）")
            fail += 1

    print(f"\n完成：成功 {success} / 失敗 {fail}")
    build_index_html()


if __name__ == "__main__":
    main()
