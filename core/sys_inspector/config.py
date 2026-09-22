"""core.sys_inspector.config
=============================
微核心系統監控配置與資料庫路徑管理。
將原散落在 data/self-monitoring/ 與 data/sys_monitor/ 的資料庫集中於 data/sys_inspector/，
並在啟動時自動進行安全無損遷移，確保過往歷史紀錄 100% 繼承。
"""

from __future__ import annotations

import logging
from pathlib import Path
import shutil

import settings

logger = logging.getLogger(__name__)

# 集中化儲存目錄：./data/sys_inspector/
DATA_DIR: Path = settings.DATA_DIR / "sys_inspector"
AUDIT_DB_PATH: Path = DATA_DIR / "audit.db"
SYS_MONITOR_DB_PATH: Path = DATA_DIR / "sys_monitor.db"

# 舊版目錄路徑（相容自動遷移）
_LEGACY_SELF_MONI_DIR: Path = settings.DATA_DIR / "self-monitoring"
_LEGACY_AUDIT_DB: Path = _LEGACY_SELF_MONI_DIR / "audit.db"

_LEGACY_SYS_MONI_DIR: Path = settings.DATA_DIR / "sys_monitor"
_LEGACY_SYS_DB: Path = _LEGACY_SYS_MONI_DIR / "sys_monitor.db"


def _copy_with_wal_safely(src_db: Path, dst_db: Path) -> bool:
    """安全拷貝 SQLite 資料庫檔案及其 WAL/SHM 附屬檔。"""
    try:
        if not src_db.exists():
            return False
        dst_db.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_db, dst_db)

        # 拷貝可能存在的 WAL 與 SHM 臨時檔
        for ext in ("-wal", "-shm"):
            src_wal = src_db.with_name(src_db.name + ext)
            dst_wal = dst_db.with_name(dst_db.name + ext)
            if src_wal.exists():
                shutil.copy2(src_wal, dst_wal)

        logger.info(
            "[SysInspectorConfig] 資料庫已成功自動遷移集中: %s -> %s (大小: %d bytes)",
            src_db,
            dst_db,
            dst_db.stat().st_size,
        )
        return True
    except Exception as exc:
        logger.error("[SysInspectorConfig] 資料庫自動遷移失敗: %s -> %s: %s", src_db, dst_db, exc)
        return False


def ensure_migrated_databases() -> None:
    """確保所有監控資料庫集中於 ./data/sys_inspector/ 目錄下。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 1. 遷移與繼承 audit.db
    if not AUDIT_DB_PATH.exists() and _LEGACY_AUDIT_DB.exists():
        _copy_with_wal_safely(_LEGACY_AUDIT_DB, AUDIT_DB_PATH)

    # 2. 遷移與繼承 sys_monitor.db
    if not SYS_MONITOR_DB_PATH.exists() and _LEGACY_SYS_DB.exists():
        _copy_with_wal_safely(_LEGACY_SYS_DB, SYS_MONITOR_DB_PATH)


def get_audit_db_path() -> Path:
    """取得集中化審計資料庫路徑。

    Returns:
        Path: ./data/sys_inspector/audit.db 路徑物件。
    """
    return AUDIT_DB_PATH


def get_sys_monitor_db_path() -> Path:
    """取得集中化系統監控資料庫路徑。

    Returns:
        Path: ./data/sys_inspector/sys_monitor.db 路徑物件。
    """
    return SYS_MONITOR_DB_PATH

