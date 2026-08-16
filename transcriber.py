# YT 音訊下載（yt-dlp）與 Whisper 語音轉文字
# Thread-safe：download_audio 用 per-task prefix；Whisper model 全域 cache + lock 保護
import atexit
import glob
import math
import os
import re
import threading
import time

from config import FFMPEG_DIR

if FFMPEG_DIR:
    os.environ["PATH"] = FFMPEG_DIR + os.pathsep + os.environ.get("PATH", "")

# torch / whisper / yt_dlp 刻意「不」在模組頂層 import。
# 它們只在轉檔時用得到，但 import torch 本身要 2.5 秒以上；而 web_server 只是
# 要顯示既有摘要清單也會被迫付這筆錢（summarizer.py 也 import 本模組，所以
# 光延後 web_server 的 import 沒有用 —— 邊界要畫在唯一真正用到它們的地方）。
# 載入點：torch/whisper → get_whisper_model()；yt_dlp → download_audio()。


_TEMP_PREFIX_BASE = f"temp_audio_download_{os.getpid()}"


def _cleanup_my_temp():
    """Process exit 時清自己 PID 開頭的所有暫存（含子 task prefix）。"""
    for f in glob.glob(f"{_TEMP_PREFIX_BASE}*"):
        try:
            os.remove(f)
        except (OSError, FileNotFoundError):
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


# === Whisper model 全域 cache（避免每 task 重新 load）===
_WHISPER_MODEL = None
_WHISPER_MODEL_KEY = None  # (model_size, device) — 切換時重 load
_WHISPER_LOAD_LOCK = threading.Lock()
WHISPER_TRANSCRIBE_LOCK = threading.Lock()  # transcribe 階段全域串行（GPU OOM 防護）


def get_whisper_model(model_size: str = "base"):
    """Lazy load + cache。多 thread 安全。"""
    global _WHISPER_MODEL, _WHISPER_MODEL_KEY
    import torch
    import whisper

    device = "cuda" if torch.cuda.is_available() else "cpu"
    key = (model_size, device)

    if _WHISPER_MODEL is not None and _WHISPER_MODEL_KEY == key:
        return _WHISPER_MODEL

    with _WHISPER_LOAD_LOCK:
        if _WHISPER_MODEL is not None and _WHISPER_MODEL_KEY == key:
            return _WHISPER_MODEL
        if device == "cuda":
            print(
                f"(偵測到 NVIDIA GPU：{torch.cuda.get_device_name(0)}，啟用 CUDA 加速)"
            )
        else:
            print("(未偵測到 GPU，使用 CPU 轉文字)")
        print(f"(載入 Whisper {model_size} 模型中...)")
        _WHISPER_MODEL = whisper.load_model(model_size, device=device)
        _WHISPER_MODEL_KEY = key
        return _WHISPER_MODEL


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


def make_temp_prefix(task_id: str | None = None) -> str:
    """每個 task 自己的 temp prefix。task_id None 時退回 process-level prefix（向後相容單一 CLI）。"""
    if task_id:
        # task_id 可能含 '-'，留著無妨；只去除路徑分隔字元
        safe = re.sub(r"[\\/]", "", str(task_id))
        return f"{_TEMP_PREFIX_BASE}_{safe}"
    return _TEMP_PREFIX_BASE


def download_audio(
    url: str, prefix: str | None = None
) -> tuple[str, str, float]:
    """下載 YT 音訊轉 mp3。
    prefix: 暫存檔前綴（不含副檔名）。多 thread 並行時必須傳唯一值避免互蓋。
            None 時用 process-level prefix（單一 CLI 模式）。
    """
    import yt_dlp

    use_prefix = prefix if prefix is not None else _TEMP_PREFIX_BASE
    ydl_opts = {
        "format": "bestaudio/best",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
        "outtmpl": use_prefix,
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
            return f"{use_prefix}.mp3", safe_title, duration
    except Exception as e:
        raise RuntimeError(f"音訊下載失敗：{e}") from e


def transcribe(mp3_path: str, model_size: str = "base") -> dict:
    """Whisper 轉文字。內部用全域 cache model + WHISPER_TRANSCRIBE_LOCK 串行。
    呼叫端若已自行 acquire WHISPER_TRANSCRIBE_LOCK 則此處 lock 是 reentrant？
    threading.Lock 不是 reentrant — 呼叫端不要外層 lock。
    """
    model = get_whisper_model(model_size)
    print("(開始轉文字...)")
    with WHISPER_TRANSCRIBE_LOCK:
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
