# DeepSeek 第一性原理摘要生成，寫出 .md 與 .srt
import json
from datetime import datetime, timezone
from pathlib import Path


def _yaml_quote(s: str) -> str:
    """Safely encode a string as a YAML double-quoted scalar (handles ':, '\"', \\n)."""
    return json.dumps(str(s), ensure_ascii=False)

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
from prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from transcriber import format_srt_timestamp


def first_principles_summary(transcript_text, language, source_meta):
    from openai import OpenAI  # 只在真的要呼叫 API 時載入（見 transcriber.py 的邊界說明）

    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    user_content = USER_PROMPT_TEMPLATE.format(
        yt_title=source_meta["yt_title"],
        source_url=source_meta["source_url"],
        duration=source_meta["duration"],
        language=language,
        transcript=transcript_text,
    )

    stream = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        extra_body={"thinking": {"type": "enabled", "reasoning_effort": "high"}},
        response_format={"type": "json_object"},
        stream=True,
    )

    reasoning_chars = 0
    content_chars = 0
    content_buf = []
    in_content_phase = False
    reasoning_dots_at = 0
    content_dots_at = 0

    print("  (思考中)", end="", flush=True)

    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        rc = getattr(delta, "reasoning_content", None)
        c = delta.content

        if rc:
            reasoning_chars += len(rc)
            while reasoning_chars - reasoning_dots_at >= 200:
                print(".", end="", flush=True)
                reasoning_dots_at += 200

        if c:
            if not in_content_phase:
                print(
                    f"\n  (思考完成 {reasoning_chars} 字，輸出摘要中)",
                    end="",
                    flush=True,
                )
                in_content_phase = True
            content_buf.append(c)
            content_chars += len(c)
            while content_chars - content_dots_at >= 200:
                print(".", end="", flush=True)
                content_dots_at += 200

    print(f"\n  (摘要輸出完成 {content_chars} 字)")

    raw = "".join(content_buf)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"JSON 解析失敗，原始回應前 500 字：\n{raw[:500]}")
        raise RuntimeError(f"DeepSeek 回傳非合法 JSON：{e}") from e

    return {
        "filename": data["filename"],
        "speaker": data.get("speaker", "未知"),
        "language": data.get("language", language),
        "category": data.get("category", "未分類"),
        "subcategory": data.get("subcategory", ""),
        "tags": data.get("tags", []),
        "thesis": data.get("thesis", ""),
        "weekly_action": data.get("weekly_action", ""),
        "summary_md": data["summary_md"],
    }


def write_summary(result: dict, source_meta: dict, output_root: Path) -> Path:
    summaries_dir = output_root / "summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)

    base_name = result["filename"]
    out_path = summaries_dir / f"{base_name}.md"

    if out_path.exists():
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_path = summaries_dir / f"{base_name}_{ts}.md"

    generated_at = datetime.now(timezone.utc).isoformat()
    tags_str = ", ".join(result["tags"]) if result["tags"] else ""

    subcategory_line = f"subcategory: {result['subcategory']}\n" if result.get("subcategory") else ""
    thesis_line = f"thesis: {_yaml_quote(result['thesis'])}\n" if result.get("thesis") else ""
    action_line = f"weekly_action: {_yaml_quote(result['weekly_action'])}\n" if result.get("weekly_action") else ""
    frontmatter = f"""---
source: {source_meta['source_url']}
yt_title: {source_meta['yt_title']}
speaker: {result['speaker']}
language: {result['language']}
duration: {source_meta['duration']}
generated_at: {generated_at}
model: {DEEPSEEK_MODEL}
category: {result['category']}
{subcategory_line}{thesis_line}{action_line}tags: [{tags_str}]
---

# {result['filename']}

{result['summary_md']}
"""

    out_path.write_text(frontmatter, encoding="utf-8")
    return out_path


def write_srt(segments: list, filename: str, output_root: Path) -> Path:
    transcripts_dir = output_root / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    out_path = transcripts_dir / f"{filename}.srt"

    lines = []
    for i, seg in enumerate(segments, start=1):
        start = format_srt_timestamp(seg["start"])
        end = format_srt_timestamp(seg["end"])
        text = seg["text"].strip()
        lines.append(f"{i}\n{start} --> {end}\n{text}\n")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
