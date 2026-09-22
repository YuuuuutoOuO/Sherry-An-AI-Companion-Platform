"""
core/db.py
==========
雪莉核心共用 SQLite 資料庫連線與交易管理模組。

提供 WAL 模式、外鍵約束、忙碌重試 (busy_timeout) 及非同步執行封裝，
確保在 Discord Bot 與 Web UI 高並發環境下資料庫連線的穩定性與安全性。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
import sqlite3
from typing import Any, Callable, Generator, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


_CONFIGURED_DBS: set[str] = set()


def get_db_connection(
    db_path: Path | str,
    wal_mode: bool = True,
    row_factory: bool = True,
    timeout: float = 10.0,
) -> sqlite3.Connection:
    """建立並設定 SQLite 資料庫連線。

    Args:
        db_path: 資料庫檔案路徑。
        wal_mode: 是否啟用 WAL (Write-Ahead Logging) 模式。
        row_factory: 是否使用 sqlite3.Row 以支援欄位名稱字典式存取。
        timeout: 等待鎖定的逾時秒數 (預設 10 秒)。

    Returns:
        設定完成的 sqlite3.Connection 實例。
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    canonical_path = str(path.resolve())

    conn = sqlite3.connect(canonical_path, timeout=timeout)

    if row_factory:
        conn.row_factory = sqlite3.Row

    # WAL 模式屬檔案等級持久設定，每個資料庫在行程內僅需設定一次，大幅降低高頻讀寫 PRAGMA 往返耗時
    if wal_mode and canonical_path not in _CONFIGURED_DBS:
        try:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            _CONFIGURED_DBS.add(canonical_path)
        except Exception as exc:
            logger.debug("[CoreDB] 設定 WAL PRAGMA 失敗 (%s): %s", canonical_path, exc)

    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("PRAGMA foreign_keys = ON;")

    return conn


@contextlib.contextmanager
def db_transaction(
    db_path: Path | str,
    wal_mode: bool = True,
    row_factory: bool = True,
    timeout: float = 10.0,
) -> Generator[sqlite3.Cursor, None, None]:
    """資料庫交易上下文管理器，提供自動 Commit 與異常 Rollback。

    Args:
        db_path: 資料庫檔案路徑。
        wal_mode: 是否啟用 WAL 模式。
        row_factory: 是否啟用 sqlite3.Row。
        timeout: 等待鎖定逾時秒數。

    Yields:
        sqlite3.Cursor 物件供執行 SQL 操作。
    """
    conn = get_db_connection(
        db_path=db_path,
        wal_mode=wal_mode,
        row_factory=row_factory,
        timeout=timeout,
    )
    cursor = conn.cursor()
    try:
        yield cursor
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error("[CoreDB] 交易失敗，已執行 Rollback: %s", e)
        raise
    finally:
        conn.close()


async def async_db_run(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """在背景執行緒中執行同步 SQLite 函式，避免阻塞非同步事件迴圈。

    Args:
        func: 欲執行的同步函式。
        *args: 位置參數。
        **kwargs: 關鍵字參數。

    Returns:
        函式執行回傳結果。
    """
    return await asyncio.to_thread(func, *args, **kwargs)


def checkpoint_all_dbs() -> None:
    """對全數已註冊之 WAL SQLite 資料庫執行 TRUNCATE checkpoint。

    確保在系統重啟、熱更新或關機前，所有 WAL 日誌中的交易均完整落盤至磁碟主檔，
    避免未持久化資料遺失或進程立即被 OS 終止時出現資料損毀。
    """
    for canonical_path in list(_CONFIGURED_DBS):
        try:
            with sqlite3.connect(canonical_path, timeout=5.0) as conn:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            logger.info("[CoreDB] 資料庫 WAL checkpoint 完成: %s", canonical_path)
        except Exception as exc:
            logger.warning("[CoreDB] 資料庫 WAL checkpoint 失敗 (%s): %s", canonical_path, exc)

