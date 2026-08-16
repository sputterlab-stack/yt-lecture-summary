"""等 web_server 真的能連線了，才開瀏覽器。

為什麼要有這支：`開啟UI.bat` 原本是先 `start "" http://localhost:5000`、下一行才啟
server，所以瀏覽器一定比 server 早到，第一眼看到的是 ERR_CONNECTION_REFUSED，
使用者得自己按重新整理。`run-ui.sh` 則是 `sleep 2` 猜一個時間——啟動變慢就會失準。
兩者都改成呼叫本檔：輪詢 port，通了才開，最多等 60 秒。
"""
import socket
import sys
import time
import webbrowser

HOST = "127.0.0.1"
PORT = 5000
URL = f"http://localhost:{PORT}"
TIMEOUT_S = 60


def main() -> int:
    deadline = time.time() + TIMEOUT_S
    while time.time() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.3)
            try:
                sock.connect((HOST, PORT))
            except OSError:
                time.sleep(0.2)
                continue
        webbrowser.open(URL)
        return 0
    print(f"[wait_and_open] server did not answer on {HOST}:{PORT} within {TIMEOUT_S}s")
    return 1


if __name__ == "__main__":
    sys.exit(main())
