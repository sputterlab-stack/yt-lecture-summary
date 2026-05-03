# 掃 outputs/summaries/*.md，用 frontmatter 生成 INDEX.md
from datetime import date
from pathlib import Path

import yaml

SUMMARIES_DIR = Path(__file__).parent / "outputs" / "summaries"
INDEX_PATH = SUMMARIES_DIR / "INDEX.md"

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


def collect_entries() -> list[dict]:
    entries = []
    for md in SUMMARIES_DIR.glob("*.md"):
        if md.name == "INDEX.md":
            continue
        fm = parse_frontmatter(md)
        entries.append({
            "filename": md.stem,
            "path": md.name,
            "category": fm.get("category") or "未分類",
            "tags": fm.get("tags") or [],
            "speaker": fm.get("speaker") or "未知",
            "duration": fm.get("duration") or "-",
            "generated_at": str(fm.get("generated_at") or ""),
        })
    return entries


def sort_key(entry: dict) -> str:
    return entry["generated_at"]


def build_index(entries: list[dict]) -> str:
    today = date.today().isoformat()
    total = len(entries)

    grouped: dict[str, list[dict]] = {}
    for e in entries:
        grouped.setdefault(e["category"], []).append(e)

    # Sort each group by generated_at descending
    for cat in grouped:
        grouped[cat].sort(key=sort_key, reverse=True)

    # Build category display order
    known = [c for c in CATEGORY_ORDER if c in grouped]
    extra = sorted(c for c in grouped if c not in CATEGORY_ORDER and c != "未分類")
    uncategorised = ["未分類"] if "未分類" in grouped else []
    order = known + extra + uncategorised

    lines = [
        "# 演講摘要索引",
        "",
        f"更新：{today} | 共 {total} 篇",
        "",
    ]

    for cat in order:
        items = grouped[cat]
        lines.append(f"## {cat}（{len(items)}）")
        lines.append("")
        for e in items:
            date_str = e["generated_at"][:10] if len(e["generated_at"]) >= 10 else "-"
            tags_display = ", ".join(e["tags"]) if e["tags"] else "-"
            lines.append(
                f"- [{e['filename']}](./{e['path']}) — {e['speaker']} | {e['duration']} | {date_str}"
            )
            lines.append(f"  tags: {tags_display}")
            lines.append("")

    # Ensure no trailing empty line issue; end with single newline
    return "\n".join(lines).rstrip() + "\n"


def main():
    entries = collect_entries()
    content = build_index(entries)
    INDEX_PATH.write_text(content, encoding="utf-8")
    print(f"INDEX.md 已更新：{len(entries)} 篇")


if __name__ == "__main__":
    main()
