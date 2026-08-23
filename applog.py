# 持久化紀錄檔：outputs/logs/app.log（2 MB 輪替、留 5 份），同時仍印到 console。
#
# 為什麼要有：本專案原本只有 print，訊息只活在那個黑視窗裡，關掉就沒了。
# 2026-08-23 下載被 YouTube 擋掉時，使用者手上沒有任何可回溯的紀錄，
# 只能靠事後重現才找到原因 —— 這個模組就是為了讓下一次不必重現。
from __future__ import annotations

import logging
import threading
from logging.handlers import RotatingFileHandler

from config import OUTPUT_ROOT

_LOGGER_NAME = "yt"
_INIT_LOCK = threading.Lock()


def get_logger() -> logging.Logger:
    """全 process 共用的 logger。重複呼叫不會重複裝 handler（多 thread 安全）。"""
    log = logging.getLogger(_LOGGER_NAME)
    if log.handlers:
        return log

    with _INIT_LOCK:
        if log.handlers:  # 另一個 thread 剛裝好
            return log

        log_dir = OUTPUT_ROOT / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        fmt = logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
        # encoding 必須明講：Windows 預設 cp950，中文標題會炸
        fh = RotatingFileHandler(
            log_dir / "app.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        sh = logging.StreamHandler()  # 保留原本「看視窗」的習慣
        sh.setFormatter(fmt)

        log.addHandler(fh)
        log.addHandler(sh)
        log.setLevel(logging.INFO)
        log.propagate = False
        return log
