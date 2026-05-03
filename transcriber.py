# YT 音訊下載（yt-dlp）與 Whisper 語音轉文字
import os
import atexit
import glob
import time

from config import FFMPEG_DIR

if FFMPEG_DIR:
    os.environ["PATH"] = FFMPEG_DIR + os.pathsep + os.environ.get("PATH", "")

import re
import math
import torch
import whisper
import yt_dlp


_TEMP_PREFIX = f"temp_audio_download_{os.getpid()}"


def _cleanup_my_temp():
    for ext in ["", ".mp3", ".part"]:
        try:
            os.remove(_TEMP_PREFIX + ext)
        except FileNotFoundError:
            pass


atexit.register(_cleanup_my_temp)

# 啟動時掃孤兒：mtime > 1 小時的舊 temp 檔（前次強殺 / 視窗 X 關掉留下的）
_now = time.time()
for _f in glob.glob("temp_audio_download_*"):
    try:
        if _now - os.path.getmtime(_f) > 3600:
            os.remove(_f)
            print(f"(清理孤兒暫存: {_f})")
    except (OSError, FileNotFoundError):
        pass


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()


def format_srt_timestamp(seconds: float) -> str:
    hours = math.floor(seconds / 3600)
    seconds %= 3600
    minutes = math.floor(seconds / 60)
    seconds %= 60
    milliseconds = round((seconds - math.floor(seconds)) * 1000)
    seconds = math.floor(seconds)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def download_audio(url: str) -> tuple[str, str, float]:
    ydl_opts = {
        "format": "bestaudio/best",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
        "outtmpl": _TEMP_PREFIX,
        "quiet": True,
        "no_warnings": True,
        "overwrites": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            video_title = info.get("title", "video_transcription")
            safe_title = sanitize_filename(video_title)
            duration = float(info.get("duration") or 0.0)
            print(f"(下載完成：{safe_title}，時長 {duration:.0f} 秒)")
            return f"{_TEMP_PREFIX}.mp3", safe_title, duration
    except Exception as e:
        raise RuntimeError(f"音訊下載失敗：{e}") from e


def transcribe(mp3_path: str, model_size: str = "base") -> dict:
    if torch.cuda.is_available():
        device = "cuda"
        print(f"(偵測到 NVIDIA GPU：{torch.cuda.get_device_name(0)}，啟用 CUDA 加速)")
    else:
        device = "cpu"
        print("(未偵測到 GPU，使用 CPU 轉文字)")

    print(f"(載入 Whisper {model_size} 模型中...)")
    model = whisper.load_model(model_size, device=device)

    print("(開始轉文字...)")
    result = model.transcribe(mp3_path, verbose=False)

    segments = [
        {"start": seg["start"], "end": seg["end"], "text": seg["text"]}
        for seg in result["segments"]
    ]
    return {
        "segments": segments,
        "language": result.get("language", "unknown"),
        "text": result.get("text", ""),
    }
