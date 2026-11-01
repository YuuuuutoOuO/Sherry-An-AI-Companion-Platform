"""
core/db.py
==========
雪莉核心共用 SQLite 資料庫連線與交易管理模組。

提供 WAL 模式、外鍵約束、忙碌重試 (busy_timeout) 及非同步執行封裝，
確保在 Discord Bot 與 Web UI 高並發環境下資料庫連線的穩定性與安全性。
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
import sqlite3
from typing import Generator

logger = logging.getLogger(__name__)


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


def backup_database(src_db_path: Path | str, backup_db_path: Path | str) -> bool:
    """使用 SQLite Online Backup API 安全備份資料庫（支援 WAL 模式高並發讀寫）。

    Args:
        src_db_path: 來源 SQLite 資料庫檔案路徑。
        backup_db_path: 目的備份檔案路徑。

    Returns:
        bool: 備份是否成功。
    """
    src_p = Path(src_db_path)
    dst_p = Path(backup_db_path)
    if not src_p.exists():
        logger.error("[CoreDB] 來源資料庫不存在: %s", src_p)
        return False
    dst_p.parent.mkdir(parents=True, exist_ok=True)
    src_conn = None
    dst_conn = None
    try:
        src_conn = sqlite3.connect(str(src_p.resolve()), timeout=10.0)
        dst_conn = sqlite3.connect(str(dst_p.resolve()), timeout=10.0)
        src_conn.backup(dst_conn)
        logger.info("[CoreDB] 資料庫備份完成: %s -> %s", src_p, dst_p)
        return True
    except Exception as exc:
        logger.error("[CoreDB] 資料庫備份失敗 (%s -> %s): %s", src_p, dst_p, exc)
        return False
    finally:
        if dst_conn is not None:
            dst_conn.close()
        if src_conn is not None:
            src_conn.close()


def verify_database_integrity(db_path: Path | str) -> tuple[bool, str]:
    """檢驗 SQLite 資料庫檔案完整性 (PRAGMA integrity_check)。

    Args:
        db_path: 欲檢查的 SQLite 資料庫檔案路徑。

    Returns:
        tuple[bool, str]: (是否完整無損, 檢查結果文字)。
    """
    p = Path(db_path)
    if not p.exists():
        return False, f"檔案不存在: {p}"
    conn = None
    try:
        conn = sqlite3.connect(str(p.resolve()), timeout=5.0)
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check;")
        rows = cursor.fetchall()
        if rows and rows[0][0] == "ok":
            return True, "ok"
        errs = "; ".join(str(r[0]) for r in rows)
        return False, f"資料庫損毀: {errs}"
    except Exception as exc:
        return False, f"完整性檢查異常: {exc}"
    finally:
        if conn is not None:
            conn.close()


def restore_database(backup_db_path: Path | str, dest_db_path: Path | str) -> bool:
    """從備份檔案安全還原 SQLite 資料庫，並在還原前後執行完整性查驗。

    Args:
        backup_db_path: 來源備份檔案路徑。
        dest_db_path: 目標資料庫檔案路徑。

    Returns:
        bool: 還原是否成功且完整性無損。
    """
    import shutil

    src_p = Path(backup_db_path)
    dst_p = Path(dest_db_path)
    if not src_p.exists():
        logger.error("[CoreDB] 備份檔案不存在: %s", src_p)
        return False
    ok, msg = verify_database_integrity(src_p)
    if not ok:
        logger.error("[CoreDB] 備份檔案完整性異常，無法還原: %s", msg)
        return False

    dst_p.parent.mkdir(parents=True, exist_ok=True)
    try:
        # 清除目標可能殘留的已損毀 WAL/SHM 臨時檔案
        wal_file = dst_p.with_name(dst_p.name + "-wal")
        shm_file = dst_p.with_name(dst_p.name + "-shm")
        if wal_file.exists():
            try:
                wal_file.unlink()
            except Exception:
                pass
        if shm_file.exists():
            try:
                shm_file.unlink()
            except Exception:
                pass

        shutil.copyfile(src_p, dst_p)

        valid, check_msg = verify_database_integrity(dst_p)
        if not valid:
            logger.error("[CoreDB] 還原後資料庫完整性異常: %s", check_msg)
            return False

        logger.info("[CoreDB] 資料庫還原成功並通過完整性校驗: %s -> %s", src_p, dst_p)
        return True
    except Exception as exc:
        logger.error("[CoreDB] 資料庫還原失敗: %s", exc)
        return False


