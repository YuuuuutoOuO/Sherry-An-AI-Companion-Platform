"""
tools/monitor_sentry.py
=======================
雪莉異常與錯誤即時警戒雷達 (分頁 4)。

即時監聽兩處：
  - data/error_log.jsonl —— 主程式的 log_error() 事件（結構化 JSONL 卡片）
  - data/bot.log        —— 標準 logging 輸出的錯誤/警告/Traceback 行

平時維持靜默雷達狀態，一旦有異常或降級即時高亮示警。
日誌檔輪替時會自動重新追蹤新檔。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# 自動確保專案根目錄在 Python 搜尋路徑中
BASE_DIR: Path = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# 監控共用邏輯（FileTailer / tail helpers）
from tools.common import FileTailer, should_exit, tail_bytes, tail_lines  # noqa: E402


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
    os.system("title 🚨 錯誤警戒雷達 (Error Sentry)")


# ANSI 色彩定義
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAGENTA = "\033[35m"
WHITE = "\033[37m"

ERROR_LOG_PATH = BASE_DIR / "data" / "error_log.jsonl"
BOT_LOG_PATH = BASE_DIR / "data" / "bot.log"

# 啟動時讀取的歷史檔尾部上限
_HISTORY_MAX_BYTES = 131072


def _render_error_card(entry: dict[str, Any]) -> None:
    """輸出美化錯誤警報卡片。

    Args:
        entry: 包含錯誤內容的字典。
    """
    ts = entry.get("ts", "")
    event = entry.get("event", "ERROR")
    task_id = entry.get("task_id", "")
    error_type = entry.get("error_type", "UnknownError")
    message = entry.get("message", "").strip()
    extra = entry.get("extra", {})

    width = 76
    print(f"\n{RED}╭{'─' * (width - 2)}╮{RESET}")
    print(
        f"{RED}│{RESET} {BOLD}{RED}🚨 [{event.upper()} DETECTED]{RESET} "
        f"{DIM}{ts}{RESET}" + (f" | Task: {CYAN}{task_id}{RESET}" if task_id else "")
    )
    print(f"{RED}│{RESET} {BOLD}⚠️  錯誤類型:{RESET} {YELLOW}{error_type}{RESET}")

    if message:
        print(f"{RED}│{RESET} {BOLD}📄 異常訊息:{RESET}")
        for line in message.splitlines():
            print(f"{RED}│{RESET}    {RED}{line}{RESET}")

    if extra:
        print(f"{RED}│{RESET} {DIM}🔍 附加資訊: {extra}{RESET}")

    print(f"{RED}╰{'─' * (width - 2)}╯{RESET}")


def _parse_error_blocks(text: str) -> tuple[list[dict[str, Any]], str]:
    """解析以空行或 JSON 區塊分隔的錯誤日誌文字。

    Args:
        text: 待解析的錯誤文字。

    Returns:
        (解析出的錯誤事件列表, 未完整文字殘餘)
    """
    entries: list[dict[str, Any]] = []
    blocks = text.split("\n\n")
    remainder = blocks[-1] if text and not text.endswith("\n\n") else ""
    parseable_blocks = blocks[:-1] if (text and not text.endswith("\n\n")) else blocks

    for block in parseable_blocks:
        b = block.strip()
        if not b:
            continue
        try:
            data = json.loads(b)
            if isinstance(data, dict):
                entries.append(data)
        except json.JSONDecodeError:
            for line in b.splitlines():
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        data = json.loads(line)
                        if isinstance(data, dict):
                            entries.append(data)
                    except Exception:
                        pass
    return entries, remainder


def _is_noteworthy_bot_line(line: str) -> bool:
    """判斷 bot.log 的一行是否值得示警（錯誤/警告/例外訊息）。"""
    stripped = line.strip()
    if not stripped:
        return False
    upper = stripped.upper()
    if (
        "[ERROR]" in upper
        or "[CRITICAL]" in upper
        or upper.startswith("ERROR")
        or "TRACEBACK (MOST RECENT CALL LAST)" in upper
        or upper.startswith("Traceback")
    ):
        return True
    if "[WARNING]" in upper or upper.startswith("WARNING"):
        return True
    # 例外堆疊內的檔案/拋出行（strip 後只剩 "File ..." / "raise ..."）給灰底上下文
    if stripped.startswith(("File ", "raise ")):
        return True
    return False


def _render_bot_line(line: str) -> None:
    """依嚴重度輸出 bot.log 的警示行（錯誤紅、警告黃、堆疊上下文灰）。"""
    stripped = line.strip()
    upper = stripped.upper()
    if (
        "[ERROR]" in upper
        or "[CRITICAL]" in upper
        or upper.startswith("ERROR")
        or "TRACEBACK (MOST RECENT CALL LAST)" in upper
        or upper.startswith("Traceback")
    ):
        print(f"{BOLD}{RED}{stripped}{RESET}")
    elif "[WARNING]" in upper or upper.startswith("WARNING"):
        print(f"{YELLOW}{stripped}{RESET}")
    else:
        print(f"{DIM}{stripped}{RESET}")


def main() -> None:
    """即時監聽並輸出錯誤警報。"""
    print(f"{BOLD}{RED}{'=' * 76}{RESET}")
    print(f"{BOLD}{RED}🚨 雪莉異常與錯誤即時警戒雷達 (Tab 4 - Error Sentry){RESET}")
    print(f"{BOLD}{RED}{'=' * 76}{RESET}")
    print(f"{DIM}• 監聽目標：{ERROR_LOG_PATH} 與 {BOT_LOG_PATH}{RESET}")
    print(f"{DIM}• 雷達狀態：🟢 雷達掃描中... (按 Ctrl+C 可停止){RESET}")
    print(f"{DIM}{'-' * 76}{RESET}\n")

    ERROR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not ERROR_LOG_PATH.exists():
        ERROR_LOG_PATH.touch()

    # 讀取歷史錯誤紀錄（只讀檔尾）
    history_text = tail_bytes(ERROR_LOG_PATH, _HISTORY_MAX_BYTES)
    if history_text:
        initial_entries, _ = _parse_error_blocks(history_text)
        if initial_entries:
            print(f"{YELLOW}--- 📜 最近歷史錯誤紀錄 ({min(3, len(initial_entries))} 筆) ---{RESET}")
            for entry in initial_entries[-3:]:
                _render_error_card(entry)
        else:
            print(f"{GREEN}✨ 目前無歷史未清除之嚴重錯誤。系統維持健康運作中。{RESET}\n")

    # 歷史 bot.log 中值得注意的最後幾筆
    noteworthy = [ln for ln in tail_lines(BOT_LOG_PATH, 30, _HISTORY_MAX_BYTES) if _is_noteworthy_bot_line(ln)]
    recent = noteworthy[-5:]
    if recent:
        print(f"{YELLOW}--- 📜 bot.log 最近警示 ({min(5, len(recent))} 筆) ---{RESET}")
        for ln in recent:
            _render_bot_line(ln)
        print()

    start_time = time.time()
    error_tailer = FileTailer(ERROR_LOG_PATH)
    bot_tailer = FileTailer(BOT_LOG_PATH)
    buffer = ""
    try:
        while True:
            if should_exit(start_time):
                break
            chunk = error_tailer.read()
            if error_tailer.detected_rotation:
                buffer = ""  # 檔被輪替，丟棄未完成的殘塊
            if chunk:
                buffer += chunk
                if "\n\n" in buffer:
                    entries, buffer = _parse_error_blocks(buffer)
                    for entry in entries:
                        _render_error_card(entry)
                sys.stdout.flush()

            for line in bot_tailer.read().splitlines():
                if _is_noteworthy_bot_line(line):
                    _render_bot_line(line)
                    sys.stdout.flush()

            time.sleep(error_tailer.poll_seconds)
    except KeyboardInterrupt:
        print(f"\n{YELLOW}錯誤警戒雷達已停止。{RESET}")


if __name__ == "__main__":
    main()