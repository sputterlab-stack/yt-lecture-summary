# 掃 outputs/summaries/*.md，套 taxonomy + aliases 生成 INDEX.md（二層：主類 > 子類）
from datetime import date
from pathlib import Path

import yaml

SUMMARIES_DIR = Path(__file__).parent / "outputs" / "summaries"
INDEX_PATH = SUMMARIES_DIR / "INDEX.md"
TAXONOMY_PATH = Path(__file__).parent / "category_taxonomy.yaml"


def load_taxonomy() -> dict:
    if not TAXONOMY_PATH.exists():
        return {"taxonomy": {}, "aliases": {}, "skip_files": []}
    return yaml.safe_load(TAXONOMY_PATH.read_text(encoding="utf-8")) or {}


def parse_frontmatter(path: Path) -> dict:
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


def collect_entries(taxonomy_cfg: dict) -> list[dict]:
    aliases = taxonomy_cfg.get("aliases") or {}
    skip = set(taxonomy_cfg.get("skip_files") or [])

    entries = []
    for md in SUMMARIES_DIR.glob("*.md"):
        if md.name == "INDEX.md" or md.name in skip:
            continue
        fm = parse_frontmatter(md)
        if fm.get("_skip_index"):
            continue
        cat = fm.get("category") or "未分類"
        cat = aliases.get(cat, cat)  # 折舊類別名到 canonical
        entries.append({
            "filename": md.stem,
            "path": md.name,
            "category": cat,
            "subcategory": fm.get("subcategory") or "",
            "tags": fm.get("tags") or [],
            "speaker": fm.get("speaker") or "未知",
            "duration": fm.get("duration") or "-",
            "generated_at": str(fm.get("generated_at") or ""),
        })
    return entries


def build_index(entries: list[dict], taxonomy_cfg: dict) -> str:
    today = date.today().isoformat()
    total = len(entries)
    taxonomy = taxonomy_cfg.get("taxonomy") or {}

    grouped: dict[str, list[dict]] = {}
    for e in entries:
        grouped.setdefault(e["category"], []).append(e)

    known_order = [c for c in taxonomy.keys() if c in grouped]
    extra = sorted(c for c in grouped if c not in taxonomy and c != "未分類")
    uncategorised = ["未分類"] if "未分類" in grouped else []
    order = known_order + extra + uncategorised

    lines = [
        "# 演講摘要索引",
        "",
        f"更新：{today} | 共 {total} 篇",
        "",
    ]

    for cat in order:
        items = grouped[cat]
        sub_groups: dict[str, list[dict]] = {}
        for e in items:
            sub_groups.setdefault(e["subcategory"] or "(未細分)", []).append(e)

        cat_subs = taxonomy.get(cat) or []
        sub_known = [s for s in cat_subs if s in sub_groups]
        sub_extra = sorted(s for s in sub_groups if s not in cat_subs and s != "(未細分)")
        sub_uncat = ["(未細分)"] if "(未細分)" in sub_groups else []
        sub_order = sub_known + sub_extra + sub_uncat

        lines.append(f"## {cat}（{len(items)}）")
        lines.append("")

        for sub in sub_order:
            sub_items = sub_groups[sub]
            sub_items.sort(key=lambda x: x["generated_at"], reverse=True)

            lines.append(f"### {sub}（{len(sub_items)}）")
            lines.append("")
            for e in sub_items:
                date_str = e["generated_at"][:10] if len(e["generated_at"]) >= 10 else "-"
                tags_display = ", ".join(e["tags"]) if e["tags"] else "-"
                lines.append(
                    f"- [{e['filename']}](./{e['path']}) — {e['speaker']} | {e['duration']} | {date_str}"
                )
                lines.append(f"  tags: {tags_display}")
                lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main():
    taxonomy_cfg = load_taxonomy()
    entries = collect_entries(taxonomy_cfg)
    content = build_index(entries, taxonomy_cfg)
    INDEX_PATH.write_text(content, encoding="utf-8")
    print(f"INDEX.md 已更新：{len(entries)} 篇")


if __name__ == "__main__":
    main()
