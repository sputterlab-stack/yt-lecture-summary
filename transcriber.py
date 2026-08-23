# YT 音訊下載（yt-dlp）與 Whisper 語音轉文字
# Thread-safe：download_audio 用 per-task prefix；Whisper model 全域 cache + lock 保護
import atexit
import glob
import math
import os
import re
import sys
import threading
import time
from datetime import datetime

from applog import get_logger
from config import FFMPEG_DIR, OUTPUT_ROOT

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


_VERSION_WARNED = False

# 90 天不是「YouTube 會擋」的臨界點，是 yt-dlp 官方自己的門檻：
# 它保證至少每 90 天一個 stable release，逾期就代表這份安裝落後了一個發布週期。
STALE_DAYS = 90


class DownloadBlocked(RuntimeError):
    """下載被擋（403／需登入／疑似機器人）。

    帶結構化欄位，讓上層**不必比對錯誤訊息的字串**——文案一改，字串比對就會靜默失效。
    """

    failure_kind = "download_blocked"
    suggested_action = "update_ytdlp"


def ytdlp_status() -> dict:
    """回 `{version, age_days, stale, problem}`，給畫面顯示用。

    刻意用 `importlib.metadata` 查版本，**不 import yt_dlp 本體**——
    commit 1ab3c09 花力氣把冷啟動從 3.25 秒壓到 0.38 秒，不能為了顯示一行字吐回去。
    查不到或看不懂版本時一律當成 stale（要出聲），**不默默當作沒事**。
    """
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as pkg_version

    try:
        ver = pkg_version("yt-dlp")
    except PackageNotFoundError:
        return {"version": None, "age_days": None, "stale": True, "problem": "not_installed"}
    except Exception as e:  # metadata 損壞 / 權限問題
        return {"version": None, "age_days": None, "stale": True, "problem": f"lookup_failed: {e}"}

    try:
        released = datetime.strptime(ver[:10], "%Y.%m.%d")
    except ValueError:
        return {"version": ver, "age_days": None, "stale": True, "problem": "unparseable_version"}

    age_days = (datetime.now() - released).days
    return {"version": ver, "age_days": age_days, "stale": age_days > STALE_DAYS, "problem": None}


def _warn_if_stale_ytdlp(log) -> None:
    """版本落後就寫一筆警告（每個 process 只寫一次）。

    YouTube 是持續變動的目標，舊版下載器遲早會被擋 —— 2026-08-23 的故障就是
    5 個月沒更新的版本被回 403。這裡不自動更新（不在使用者不知情下改環境），
    只讓這件事在被擋之前就留下紀錄。
    """
    global _VERSION_WARNED
    if _VERSION_WARNED:
        return
    _VERSION_WARNED = True

    st = ytdlp_status()
    if not st["stale"]:
        return
    if st["problem"]:
        log.warning("yt-dlp 版本查不出來（%s），無法判斷是否過舊", st["problem"])
        return
    log.warning(
        'yt-dlp 版本 %s 已 %d 天未更新，YouTube 很可能會擋下載。更新指令："%s" -m pip install -U yt-dlp',
        st["version"],
        st["age_days"],
        sys.executable,
    )


_BLOCKED_SIGNS = ("403", "Forbidden", "Sign in to confirm", "not a bot", "age")


def _download_hint(err_text: str) -> str:
    """被擋型的錯誤才補一段「下一步做什麼」。其他錯誤保留原文，不亂猜原因。"""
    if not any(s.lower() in err_text.lower() for s in _BLOCKED_SIGNS):
        return ""
    return (
        "\n這類錯誤通常是下載器版本落後被 YouTube 擋。請用本程式的 Python 更新後重試：\n"
        f'  "{sys.executable}" -m pip install -U yt-dlp\n'
        "更新後仍失敗的話，可能是影片需要登入、有地區或權限限制、或網路暫時被擋。\n"
        f"完整紀錄：{OUTPUT_ROOT / 'logs' / 'app.log'}"
    )


def download_audio(
    url: str, prefix: str | None = None
) -> tuple[str, str, float]:
    """下載 YT 音訊轉 mp3。
    prefix: 暫存檔前綴（不含副檔名）。多 thread 並行時必須傳唯一值避免互蓋。
            None 時用 process-level prefix（單一 CLI 模式）。
    """
    import yt_dlp

    log = get_logger()
    _warn_if_stale_ytdlp(log)

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
        # quiet 只壓進度條；no_warnings 會連「你的版本太舊」都摀住，所以刻意不設。
        # 2026-08-23：下載被 403 擋了整整一週，而 yt-dlp 早就想說這句話。
        "quiet": True,
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
        log.error("下載失敗 url=%s：%s", url, e)
        hint = _download_hint(str(e))
        if hint:  # 被擋型：丟帶欄位的例外，上層據此決定要不要提示更新
            raise DownloadBlocked(f"音訊下載失敗：{e}{hint}") from e
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
