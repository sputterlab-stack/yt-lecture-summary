# 掃 outputs/summaries/*.md 產對應 .mmd（mermaid mindmap 心智圖）
# 用法：
#   python gen_mindmap.py            # 只產缺的 .mmd
#   python gen_mindmap.py --force    # 重新產所有 .mmd（覆蓋既有）
import argparse
import sys
from pathlib import Path

from openai import OpenAI

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
from prompts import MINDMAP_SYSTEM_PROMPT, MINDMAP_USER_PROMPT_TEMPLATE

SUMMARIES_DIR = Path(__file__).parent / "outputs" / "summaries"


def find_targets(force: bool) -> list[Path]:
    targets = []
    for md in sorted(SUMMARIES_DIR.glob("*.md")):
        if md.name == "INDEX.md":
            continue
        mmd = md.with_suffix(".mmd")
        if force or not mmd.exists():
            targets.append(md)
    return targets


def strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def gen_mindmap(md_path: Path, client: OpenAI) -> str:
    md_content = md_path.read_text(encoding="utf-8")
    user_content = MINDMAP_USER_PROMPT_TEMPLATE.format(markdown=md_content)

    resp = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": MINDMAP_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        extra_body={"thinking": {"type": "enabled", "reasoning_effort": "medium"}},
    )

    text = strip_code_fence(resp.choices[0].message.content)
    if not text.lstrip().startswith("mindmap"):
        raise RuntimeError(f"輸出不是 mindmap 語法（首 80 字：{text[:80]}）")
    return text


def main():
    parser = argparse.ArgumentParser(description="掃 .md 產對應 .mmd 心智圖")
    parser.add_argument(
        "--force", action="store_true", help="重新產生所有 .mmd（覆蓋既有）"
    )
    args = parser.parse_args()

    if not DEEPSEEK_API_KEY:
        print("錯誤：DEEPSEEK_API_KEY 未設定")
        sys.exit(1)

    targets = find_targets(force=args.force)
    if not targets:
        print("沒有需要產生的 .mmd（全部已存在；用 --force 強制重新）")
        return

    print(f"準備產生 {len(targets)} 個 .mmd")
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

    success = 0
    fail = 0
    for i, md in enumerate(targets, 1):
        print(f"  [{i}/{len(targets)}] {md.stem} ", end="", flush=True)
        try:
            mmd_text = gen_mindmap(md, client)
            mmd_path = md.with_suffix(".mmd")
            mmd_path.write_text(mmd_text + "\n", encoding="utf-8")
            print("OK")
            success += 1
        except Exception as e:
            print(f"FAIL ({type(e).__name__}: {e})")
            fail += 1

    print(f"\n完成：{success} 成功 / {fail} 失敗")


if __name__ == "__main__":
    main()
