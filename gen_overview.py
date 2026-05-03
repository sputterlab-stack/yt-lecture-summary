# 把 outputs/summaries/*.mmd 拼接成 心智圖總覽.md
# 用法：python gen_overview.py
from pathlib import Path

SUMMARIES_DIR = Path(__file__).parent / "outputs" / "summaries"
OVERVIEW_PATH = SUMMARIES_DIR / "心智圖總覽.md"


def main():
    mmds = sorted(SUMMARIES_DIR.glob("*.mmd"))
    if not mmds:
        print("沒有 .mmd 檔，跳過總覽生成")
        return

    lines = [
        "# 心智圖總覽",
        "",
        "> 由所有 .mmd 自動拼接而成。在 VS Code 開此檔，按 Ctrl+Shift+V 即可一次看全部心智圖。",
        "",
        f"共 {len(mmds)} 篇。每次跑 `yt_summary.py` 後一鍵啟動會自動更新。",
        "",
    ]

    for m in mmds:
        lines.append(f"## {m.stem}")
        lines.append("")
        lines.append("```mermaid")
        lines.append(m.read_text(encoding="utf-8").rstrip())
        lines.append("```")
        lines.append("")

    OVERVIEW_PATH.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"心智圖總覽.md 已更新：{len(mmds)} 篇")


if __name__ == "__main__":
    main()
