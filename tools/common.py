"""
tools/common.py
================
監控工具共用模組：檔案 Tail 讀取與 rotation 偵測。

提供 FileTailer（持續追蹤一個附加式日誌檔，自動處理 rotation/truncation）
與尾部歷史區塊的讀取 helpers，供三個監控分頁 (monitor_web / monitor_brain /
monitor_sentry) 共用，避免三份重複的 tail 邏輯。

背景說明：
  runtime_log.jsonl 超過 50MB 時，主程式會把它 rename 成 .jsonl.1 再開新檔。
  若監控分頁一直握著舊 file handle，rotation 後就會「假死」——看似正常但再也
  讀不到新內容。FileTailer 每輪詢偵測「目前位置 > 檔案大小」或「檔案消失」，
  自動關閉舊 handle、改追蹤新檔，解決這個問題。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import IO

BASE_DIR: Path = Path(__file__).resolve().parent.parent

DEFAULT_ENCODING = "utf-8"
_ROTATION_POLL_SECONDS = 0.3


def _open_for_follow(path: Path) -> IO[str] | None:
    """以「允許其他程序 rename/delete」的方式開啟日誌檔。

    重點：只用一般的 ``open()`` 讀檔時，Windows 上的共用模式 (share mode) 不含
    FILE_SHARE_DELETE，會讓主程式的 rotation (rename) 失敗（WinError 32），
    反而導致監控分頁把 50MB 輪替機制弄壞。這裡改用 CreateFileW 明確要求
    FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE。
    """
    try:
        if sys.platform == "win32":
            import ctypes
            import msvcrt

            GENERIC_READ = 0x80000000
            FILE_SHARE_READ = 0x00000001
            FILE_SHARE_WRITE = 0x00000002
            FILE_SHARE_DELETE = 0x00000004
            OPEN_EXISTING = 3
            FILE_ATTRIBUTE_NORMAL = 0x80

            handle = ctypes.windll.kernel32.CreateFileW(
                str(path),
                GENERIC_READ,
                FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                None,
                OPEN_EXISTING,
                FILE_ATTRIBUTE_NORMAL,
                None,
            )
            if handle in (0, ctypes.c_void_p(-1).value):
                return None
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY)
            return os.fdopen(fd, "r", encoding=DEFAULT_ENCODING, errors="replace")
        return path.open("r", encoding=DEFAULT_ENCODING, errors="replace")
    except Exception:
        return None


class FileTailer:
    """追蹤一個附加式 (append-only) 日誌檔。

    行為：
      - 第一次讀取時移到檔尾，只讀出「之後新增」的內容。
      - 每次 read() 偵測目標檔被改名/重建 (rotation) 或截斷 (truncate)：
        自動改用新檔（從檔頭開始，才不會漏掉已寫入的新內容），
        並把 ``detected_rotation`` 設為 True（供呼叫端清掉殘塊）。

    使用方式::

        tailer = FileTailer(path)
        while True:
            chunk = tailer.read()
            if tailer.detected_rotation:
                buffer = ""   # 檔被輪替，丟棄未完成的殘塊
            if not chunk:
                time.sleep(tailer.poll_seconds)
                continue
            # ... 處理 chunk ...
    """

    def __init__(
        self,
        path: Path,
        poll_seconds: float = _ROTATION_POLL_SECONDS,
        *,
        start_at_end: bool = True,
    ) -> None:
        self.path = Path(path)
        self.poll_seconds = poll_seconds
        self._start_at_end = start_at_end
        self._fh: IO[str] | None = None
        self.detected_rotation = False
        self._restart_from_beginning = False

    def open(self, start_at_end: bool | None = None) -> None:
        """關閉舊 handle（若有）並開啟目前存在的目標檔；不存在就保持關閉。

        Args:
            start_at_end: True 移到檔尾（只讀未來內容）；False 從檔頭讀。
                預設使用建構時的值；rotation 後重新開啟時會用 False，
                以免漏掉新檔已寫入的內容。
        """
        if start_at_end is None:
            start_at_end = self._start_at_end
        self.close()
        if self.path.exists():
            self._fh = _open_for_follow(self.path)
            if self._fh is not None and start_at_end:
                self._fh.seek(0, os.SEEK_END)

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except Exception:
                pass
            self._fh = None

    def _file_was_rotated(self) -> bool:
        try:
            st = self.path.stat()
        except FileNotFoundError:
            return True  # 目標檔被搬走，尚未重新建立
        if self._fh is None:
            return False
        if self._fh.tell() > st.st_size:
            return True  # 被截斷/清空
        # Windows 上 st_ctime = creation time：rename 後重建會改變 → 視同 rotation
        prev_ctime = getattr(self, "_prev_ctime_ns", None)
        if prev_ctime is not None and st.st_ctime_ns != prev_ctime:
            self._prev_ctime_ns = st.st_ctime_ns
            return True
        self._prev_ctime_ns = st.st_ctime_ns
        return False

    def read(self) -> str:
        """回傳自上次讀取以來的追加內容；rotation 發生時清空並回傳空字串。"""
        self.detected_rotation = False
        if self._fh is None:
            start_at_end = self._start_at_end and not self._restart_from_beginning
            self._restart_from_beginning = False
            self.open(start_at_end=start_at_end)
        elif self._file_was_rotated():
            self.detected_rotation = True
            self._restart_from_beginning = True
            self.open(start_at_end=False)
        if self._fh is None:
            return ""
        try:
            return self._fh.read()
        except Exception:
            self.close()
            return ""


def tail_bytes(path: Path, max_bytes: int = 131072) -> str:
    """回傳檔案最末 ``max_bytes`` 的原始文字（供啟動時顯示歷史，避免解析整份大檔）。"""
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            start = max(0, size - max_bytes)
            f.seek(start)
            return f.read().decode(DEFAULT_ENCODING, errors="replace")
    except FileNotFoundError:
        return ""
    except OSError:
        return ""


def tail_lines(path: Path, n: int = 15, max_bytes: int = 131072) -> list[str]:
    """回傳檔案最末 ``n`` 行文字（忽略尾隨的空行）。"""
    text = tail_bytes(path, max_bytes)
    if not text:
        return []
    lines = text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    return lines[-n:]


SIGNAL_FILE: Path = BASE_DIR / "data" / "shutdown.signal"
PID_FILE: Path = BASE_DIR / "data" / "sherry.pid"


def is_pid_running(pid: int) -> bool:
    """檢查指定 PID 之進程是否仍在系統中活躍執行。

    Args:
        pid: 目標進程識別碼。

    Returns:
        若仍在執行回傳 True，否則回傳 False。
    """
    if pid <= 0:
        return False
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    SYNCHRONIZE = 0x00100000
    handle = ctypes.windll.kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid
    )
    if not handle:
        return False
    exit_code = ctypes.c_ulong()
    ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
    ctypes.windll.kernel32.CloseHandle(handle)
    return exit_code.value == 259  # 259 代表 STILL_ACTIVE


def get_main_bot_pid() -> int | None:
    """讀取當前雪莉主核心 (main.py) 的 PID。

    Returns:
        成功取得回傳 int，檔案不存在或格式不符回傳 None。
    """
    if not PID_FILE.exists():
        return None
    try:
        content = PID_FILE.read_text(encoding="utf-8").strip()
        if content.isdigit():
            return int(content)
    except Exception:
        pass
    return None


_main_bot_dead_since: float | None = None
_has_seen_bot_alive: bool = False
_last_restart_notice: float = 0.0


def should_exit(start_time: float) -> bool:
    """檢查監控器是否應當退出。

    規則：
      1. 若收到 shutdown.signal 且前綴為 stop，表示系統停機，回傳 True。
      2. 若收到 shutdown.signal 為 restart 或 update，表示主核心在同分頁原地重啟，
         監控器維持待命狀態，回傳 False。
      3. 若主核心曾活躍執行但已消失超過 15 秒（未觸發重啟或進程異常崩潰），判定主核心已關閉，回傳 True。

    Args:
        start_time: 監控器啟動時間戳記。

    Returns:
        若監控器應當結束，回傳 True；否則回傳 False。
    """
    global _main_bot_dead_since, _has_seen_bot_alive, _last_restart_notice

    if SIGNAL_FILE.exists():
        try:
            if SIGNAL_FILE.stat().st_mtime > start_time:
                content = SIGNAL_FILE.read_text(encoding="utf-8").strip().lower()
                if content.startswith("stop"):
                    sys.stdout.write("\n\033[33m[INFO] 收到關閉指令，監控分頁即將結束。\033[0m\n")
                    sys.stdout.flush()
                    return True
                if content.startswith("restart") or content.startswith("update"):
                    # 正在重啟中，重設看門狗計時器，避免重啟間隔期間誤殺監控
                    _main_bot_dead_since = None
                    now = time.time()
                    if now - _last_restart_notice > 6.0:
                        _last_restart_notice = now
                        sys.stdout.write("\n\033[33m[INFO] 偵測到雪莉主核心正在重新啟動，監控分頁持續待命中...\033[0m\n")
                        sys.stdout.flush()
                    return False
        except Exception:
            pass

    # 檢查主核心 PID 存活看門狗
    pid = get_main_bot_pid()
    if pid is not None and is_pid_running(pid):
        _has_seen_bot_alive = True
        _main_bot_dead_since = None
        return False

    # 若主核心 PID 目前不在運行
    if _has_seen_bot_alive:
        now = time.time()
        if _main_bot_dead_since is None:
            _main_bot_dead_since = now
        elif now - _main_bot_dead_since > 15.0:
            # 主核心已斷開超過 15 秒且無重啟，自動結束監控分頁
            return True

    return False