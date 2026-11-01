"""core/concurrency.py
=======================
專用執行器（Dedicated Executors）與背景 Future 回收。

本平台的 Discord Bot 與 WebUI 共用同一個 event loop；任何同步阻塞若丟進
asyncio 預設 executor，會與 WebUI 的 ``to_thread``／其他工具互相排擠。
因此把兩類工作負載分離：

- ``heavy_io_pool``：網路抓取、圖表繪製、影音處理等長耗時／CPU+I/O 工作。
- ``db_pool``：SQLite 寫入與 JSON 落盤等短交易，避免被長 I/O 餓死。
- ``web_extract_pool``：trafilatura 等可能卡死的第三方網頁擷取器，獨立隔離避免佔滿
  ``heavy_io_pool``；搭配呼叫端的 bounded semaphore 與逾時結果丟棄策略。

另外提供 ``schedule_in_pool``：讓同步 API 能以 fire-and-forget 方式落盤時，
仍保留 Future 強引用與完成回呼（失敗留痕、不再靜默消失）。
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    """讀取整數環境變數並套用下限，避免設定成 0 造成 executor 卡死。"""
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


HEAVY_IO_POOL_WORKERS = _env_int("HEAVY_IO_POOL_WORKERS", 6)
DB_POOL_WORKERS = _env_int("DB_POOL_WORKERS", 2)
WEB_EXTRACT_POOL_WORKERS = _env_int("WEB_EXTRACT_POOL_WORKERS", 2)

heavy_io_pool = ThreadPoolExecutor(
    max_workers=HEAVY_IO_POOL_WORKERS,
    thread_name_prefix="sherry-heavy-io",
)
db_pool = ThreadPoolExecutor(
    max_workers=DB_POOL_WORKERS,
    thread_name_prefix="sherry-db",
)
web_extract_pool = ThreadPoolExecutor(
    max_workers=WEB_EXTRACT_POOL_WORKERS,
    thread_name_prefix="sherry-web-extract",
)

# 未完成的背景 Future 強引用集合：避免 fire-and-forget 的 Future 被 GC。
_PENDING_FUTURES: set[asyncio.Future[Any]] = set()


def schedule_in_pool(
    executor: ThreadPoolExecutor,
    func: Callable[..., Any],
    *args: Any,
    on_error: Callable[[BaseException], None] | None = None,
) -> asyncio.Future[Any] | None:
    """在指定 executor 排程同步函式，並保存 Future 強引用直到完成。

    若呼叫端沒有執行中的 event loop（例如 CLI／測試同步路徑），直接同步執行，
    與既有 ``try: get_running_loop()/run_in_executor`` 的降級語意一致。

    Args:
        executor: ``heavy_io_pool`` 或 ``db_pool``。
        func: 要執行的同步函式。
        *args: 傳給函式的參數。
        on_error: 例外時的回呼（預設記錄錯誤）。

    Returns:
        asyncio.Future | None: 非同步路徑回傳 Future；同步降級路徑回傳 None。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        func(*args)
        return None

    future = loop.run_in_executor(executor, func, *args)
    future = asyncio.ensure_future(future)
    _PENDING_FUTURES.add(future)

    def _on_done(done: asyncio.Future[Any]) -> None:
        _PENDING_FUTURES.discard(done)
        if done.cancelled():
            return
        exc = done.exception()
        if exc is None:
            return
        if on_error is not None:
            try:
                on_error(exc)
            except Exception:
                logger.debug("concurrency: on_error 回呼失敗", exc_info=True)
        else:
            logger.error("concurrency: 背景工作失敗 (%s): %s", getattr(func, "__name__", func), exc)

    future.add_done_callback(_on_done)
    return future


def pending_background_writes() -> int:
    """回傳尚未完成的背景 Future 數量（供測試與關機檢查）。"""
    return len(_PENDING_FUTURES)


def shutdown_executors(wait: bool = True) -> None:
    """關閉專用執行器；可重複呼叫（atexit 與正常關機流程共用）。"""
    for pool in (db_pool, heavy_io_pool, web_extract_pool):
        try:
            pool.shutdown(wait=wait, cancel_futures=not wait)
        except TypeError:  # Python < 3.9 相容
            pool.shutdown(wait=wait)
        except Exception:
            logger.debug("concurrency: 關閉 executor 失敗", exc_info=True)


atexit.register(shutdown_executors, False)
