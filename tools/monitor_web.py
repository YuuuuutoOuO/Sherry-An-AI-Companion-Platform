"""
tools/monitor_web.py
====================
Web UI 伺服器存取與狀態即時終端監控器 (分頁 2)。

持續追蹤 data/web_ui.log，對 Uvicorn / Quart 的 HTTP 請求與 API 狀態碼
進行彩色終端高亮渲染。日誌檔被輪替時會自動重新追蹤新檔。
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

# 自動確保專案根目錄在 Python 搜尋路徑中
BASE_DIR: Path = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# 監控共用邏輯（FileTailer / tail helpers）
from tools.common import FileTailer, should_exit, tail_lines  # noqa: E402


# 確保輸出支援 Windows UTF-8 與 ANSI 色彩
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

if sys.platform == "win32":
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass
    os.system("title 🌐 Web UI 伺服器狀態監控")


# ANSI 色彩定義
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAGENTA = "\033[35m"
BLUE = "\033[34m"

LOG_PATH = BASE_DIR / "data" / "web_ui.log"

# 方法與狀態碼正規表示法（對應 uvicorn.access 輸出：`127.0.0.1 - GET /api/state 200 OK`）
_METHOD_RE = re.compile(r"(?<![A-Za-z])(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)(?![A-Za-z])")
_STATUS_RE = re.compile(r"(?<=\s)(\d{3})(?![\d.:])")
_METHOD_COLORS = {
    "GET": CYAN,
    "POST": GREEN,
    "DELETE": RED,
}


def _status_color(match: re.Match[str]) -> str:
    """依狀態碼區間上色。"""
    code = match.group(1)
    if code.startswith("2"):
        color = GREEN
    elif code.startswith("3"):
        color = CYAN
    elif code.startswith("4"):
        color = YELLOW
    elif code.startswith("5"):
        color = RED
    else:
        color = DIM
    return f"{BOLD}{color}{code}{RESET}"


def _format_http_line(line: str) -> str:
    """為 HTTP 存取日誌上色。

    Args:
        line: 原始日誌文字行。

    Returns:
        含 ANSI 彩色碼的格式化字串；無內容時回傳空字串。
    """
    # 忽略 SSE/WebSocket 客戶端離線無害通知
    if "ASGI callable returned without completing response" in line:
        return ""

    colored = _METHOD_RE.sub(
        lambda m: f"{BOLD}{_METHOD_COLORS.get(m.group(1), YELLOW)}{m.group(1)}{RESET}",
        line,
    )
    return _STATUS_RE.sub(_status_color, colored)


def main() -> None:
    """即時監聽並輸出 Web UI 日誌。"""
    print(f"{BOLD}{CYAN}{'=' * 70}{RESET}")
    print(f"{BOLD}{CYAN}🌐 雪莉 Web UI & API 伺服器即時串流監控 (Tab 2){RESET}")
    print(f"{BOLD}{CYAN}{'=' * 70}{RESET}")
    print(f"{DIM}• 監聽日誌：{LOG_PATH}{RESET}")
    print(f"{DIM}• 狀態：🟢 等待伺服器連線與請求注入... (按 Ctrl+C 可停止){RESET}")
    print(f"{DIM}{'-' * 70}{RESET}\n")

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not LOG_PATH.exists():
        LOG_PATH.touch()

    # 顯示歷史最後幾筆
    for line in tail_lines(LOG_PATH, 15):
        formatted = _format_http_line(line)
        if formatted:
            sys.stdout.write(formatted + "\n")
    sys.stdout.flush()

    start_time = time.time()
    tailer = FileTailer(LOG_PATH)
    try:
        while True:
            if should_exit(start_time):
                break
            chunk = tailer.read()
            if chunk:
                for line in chunk.splitlines():
                    formatted = _format_http_line(line)
                    if formatted:
                        sys.stdout.write(formatted + "\n")
                sys.stdout.flush()
            else:
                time.sleep(tailer.poll_seconds)
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Web UI 監控已停止。{RESET}")


if __name__ == "__main__":
    main()