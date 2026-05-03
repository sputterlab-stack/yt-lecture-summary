# 設定載入：從 .env 讀取 API key 與各模組常數
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = "deepseek-v4-pro"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
WHISPER_MODEL = "base"
OUTPUT_ROOT = Path(__file__).parent / "outputs"

# ffmpeg.exe 所在目錄。yt-dlp 與 whisper 都需要 ffmpeg；若已在系統 PATH 設為 ""
FFMPEG_DIR = os.environ.get("FFMPEG_DIR", "")


def require_api_key():
    if not DEEPSEEK_API_KEY:
        raise SystemExit("錯誤：未設定 DEEPSEEK_API_KEY，請在專案根目錄的 .env 中設定")
