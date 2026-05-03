# 主入口：YouTube URL -> 第一性原理摘要 .md + 逐字稿 .srt
import argparse
import math
import os
import sys

import config
from config import OUTPUT_ROOT, WHISPER_MODEL
import transcriber
import summarizer


def step_banner(num: int, total: int, title: str) -> None:
    bar = "=" * 60
    print(f"\n{bar}")
    print(f"  步驟 {num}/{total}: {title}")
    print(bar)


def seconds_to_hhmmss(total_seconds: float) -> str:
    total_seconds = int(total_seconds)
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def main():
    parser = argparse.ArgumentParser(
        description="YouTube 演講 -> 第一性原理摘要 .md + 逐字稿 .srt"
    )
    parser.add_argument("url", nargs="?", help="YouTube URL")
    args = parser.parse_args()

    url = args.url
    if not url:
        url = input("請貼 YouTube URL: ").strip()
    if not url:
        print("錯誤：未輸入 URL")
        sys.exit(1)

    # 步驟 0：先檢查 API key（fail-fast，避免下載+轉錄完才發現 key 沒設）
    config.require_api_key()

    step_banner(1, 4, "下載 YouTube 音訊")
    # 步驟 1：下載音訊
    try:
        mp3_path, yt_title, duration_sec = transcriber.download_audio(url)
    except RuntimeError as e:
        print(f"錯誤：{e}")
        sys.exit(1)

    step_banner(2, 4, "Whisper 轉文字（CUDA 加速）")
    # 步驟 2：Whisper 轉文字
    try:
        transcript = transcriber.transcribe(mp3_path, model_size=WHISPER_MODEL)
    except Exception as e:
        print(f"錯誤：Whisper 轉文字失敗 — {e}")
        sys.exit(1)

    word_count = len(transcript["text"])
    language = transcript["language"]
    segments = transcript["segments"]

    step_banner(3, 4, "DeepSeek V4-Pro 第一性原理摘要")
    print(f"  (逐字稿 {word_count} 字，開始呼叫)")

    duration_str = seconds_to_hhmmss(duration_sec)
    source_meta = {
        "yt_title": yt_title,
        "source_url": url,
        "duration": duration_str,
    }

    # 步驟 4：DeepSeek 摘要
    try:
        result = summarizer.first_principles_summary(
            transcript["text"], language, source_meta
        )
    except RuntimeError as e:
        print(f"錯誤：摘要生成失敗 — {e}")
        sys.exit(1)
    except Exception as e:
        print(f"錯誤：呼叫 DeepSeek API 時發生問題 — {type(e).__name__}: {e}")
        sys.exit(1)

    step_banner(4, 4, "寫入檔案")
    # 步驟 5：寫 SRT
    try:
        srt_path = summarizer.write_srt(segments, result["filename"], OUTPUT_ROOT)
    except Exception as e:
        print(f"錯誤：寫入 .srt 失敗 — {e}")
        sys.exit(1)

    # 步驟 6：寫摘要 .md
    try:
        md_path = summarizer.write_summary(result, source_meta, OUTPUT_ROOT)
    except Exception as e:
        print(f"錯誤：寫入 .md 失敗 — {e}")
        sys.exit(1)

    # 步驟 7：清理暫存 mp3
    try:
        if os.path.exists(mp3_path):
            os.remove(mp3_path)
    except Exception:
        pass

    print("\n完成！產出檔案：")
    print(f"  摘要：{md_path.resolve()}")
    print(f"  逐字稿：{srt_path.resolve()}")


if __name__ == "__main__":
    main()
