"""
core/db_migration.py
====================
雪莉核心 SQLite 資料庫架構遷移 (Schema Migration) 與備份回滾框架。

提供基於 `PRAGMA user_version` 的版本控制、自動升級 (Migrate)、
交易隔離、備份保護及回滾 (Rollback) 機制，確保資料庫升級過程安全可靠。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from pathlib import Path
import sqlite3
from typing import Callable

from core.db import get_db_connection

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Migration:
    """單一資料庫遷移定義。

    Attributes:
        version: 目標版本號 (必須為大於 0 的正整數且嚴格遞增)。
        name: 遷移描述名稱。
        up: 升級執行函式，接收 sqlite3.Connection 進行結構修改與資料遷移。
        down: 降級/回滾執行函式 (可選)。
    """

    version: int
    name: str
    up: Callable[[sqlite3.Connection], None]
    down: Callable[[sqlite3.Connection], None] | None = None


def get_schema_version(db_path: Path | str) -> int:
    """取得目標 SQLite 資料庫當前的 schema 版本號 (`PRAGMA user_version`)。

    Args:
        db_path: 資料庫檔案路徑。

    Returns:
        當前版本號 (若新資料庫未設定則回傳 0)。
    """
    path = Path(db_path)
    if not path.exists():
        return 0

    conn = get_db_connection(db_path, row_factory=False)
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA user_version;")
        row = cursor.fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    """設定資料庫版本號 (`PRAGMA user_version`)。

    Args:
        conn: 進行中的資料庫連線。
        version: 目標版本號。
    """
    conn.execute(f"PRAGMA user_version = {int(version)};")


def create_db_backup(db_path: Path | str, backup_dir: Path | str | None = None) -> Path:
    """在執行結構變更前建立時間戳備份複本。

    Args:
        db_path: 原資料庫路徑。
        backup_dir: 備份目錄 (預設為原資料庫目錄下之 `backups/`)。

    Returns:
        建立的備份檔案路徑。

    Raises:
        FileNotFoundError: 若來源資料庫不存在。
    """
    src_path = Path(db_path)
    if not src_path.exists():
        raise FileNotFoundError(f"來源資料庫不存在，無法建立備份: {src_path}")

    target_dir = Path(backup_dir) if backup_dir else src_path.parent / "backups"
    target_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_file = target_dir / f"{src_path.stem}_backup_{timestamp}{src_path.suffix}"

    # 使用 SQLite 線上備份 API 確保 WAL 模式下連線一致性
    src_conn = get_db_connection(src_path, row_factory=False)
    try:
        with sqlite3.connect(str(backup_file)) as dst_conn:
            src_conn.backup(dst_conn)
    finally:
        src_conn.close()

    logger.info("[DBMigration] 已建立資料庫備份: %s -> %s", src_path, backup_file)
    return backup_file


class MigrationRunner:
    """資料庫遷移協調器。"""

    def __init__(self, db_path: Path | str, backup_on_migrate: bool = True) -> None:
        """初始化遷移協調器。

        Args:
            db_path: 目標資料庫路徑。
            backup_on_migrate: 升級前是否自動建立備份。
        """
        self.db_path = Path(db_path)
        self.backup_on_migrate = backup_on_migrate
        self._migrations: dict[int, Migration] = {}

    def register(self, migration: Migration) -> None:
        """註冊一項遷移操作。

        Args:
            migration: 遷移物件。

        Raises:
            ValueError: 若版本號重複或小於 1。
        """
        if migration.version <= 0:
            raise ValueError(f"遷移版本號必須大於 0，收到: {migration.version}")
        if migration.version in self._migrations:
            raise ValueError(f"重複註冊遷移版本: {migration.version} ({migration.name})")
        self._migrations[migration.version] = migration

    def get_registered_versions(self) -> list[int]:
        """取得所有已註冊的遷移版本清單 (由小到大排序)。"""
        return sorted(self._migrations.keys())

    def migrate(self, target_version: int | None = None) -> int:
        """執行資料庫結構升級至目標版本。

        Args:
            target_version: 目標版本 (若為 None 則升級至最新已註冊版本)。

        Returns:
            遷移完成後的最新版本號。

        Raises:
            Exception: 遷移過程中若發生異常，將自動 Rollback 並拋出例外。
        """
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        current_version = get_schema_version(self.db_path)
        available_versions = self.get_registered_versions()

        if not available_versions:
            logger.info("[DBMigration] 未註冊任何遷移操作，保持當前版本: %d", current_version)
            return current_version

        max_version = available_versions[-1]
        final_target = max_version if target_version is None else target_version

        if current_version >= final_target:
            logger.info("[DBMigration] 資料庫當前版本 (%d) 已達或高於目標版本 (%d)，無需升級。", current_version, final_target)
            return current_version

        # 執行備份
        if self.backup_on_migrate and self.db_path.exists():
            try:
                create_db_backup(self.db_path)
            except Exception as exc:
                logger.warning("[DBMigration] 自動備份失敗，但繼續嘗試遷移: %s", exc)

        pending_versions = [v for v in available_versions if current_version < v <= final_target]

        conn = get_db_connection(self.db_path, row_factory=False)
        try:
            for v in pending_versions:
                migration = self._migrations[v]
                logger.info("[DBMigration] 正在執行升級: v%d - %s", migration.version, migration.name)
                try:
                    conn.execute("BEGIN IMMEDIATE;")
                    migration.up(conn)
                    set_schema_version(conn, migration.version)
                    conn.commit()
                    logger.info("[DBMigration] 升級成功: v%d - %s", migration.version, migration.name)
                except Exception as e:
                    conn.rollback()
                    logger.error("[DBMigration] 遷移至 v%d 失敗，已回復交易: %s", migration.version, e)
                    raise
        finally:
            conn.close()

        return get_schema_version(self.db_path)

    def rollback(self, target_version: int) -> int:
        """將資料庫降級回滾至指定版本。

        Args:
            target_version: 欲回滾到的目標版本號。

        Returns:
            回滾完成後的版本號。

        Raises:
            ValueError: 若降級目標大於等於當前版本，或某個版本未實作 down 降級函式。
        """
        current_version = get_schema_version(self.db_path)
        if target_version >= current_version:
            raise ValueError(f"回滾目標版本 ({target_version}) 必須小於當前版本 ({current_version})")

        available_versions = self.get_registered_versions()
        rollback_versions = sorted([v for v in available_versions if target_version < v <= current_version], reverse=True)

        for v in rollback_versions:
            migration = self._migrations.get(v)
            if not migration or not migration.down:
                raise ValueError(f"遷移版本 v{v} 未提供 down 降級操作，無法安全回滾")

        conn = get_db_connection(self.db_path, row_factory=False)
        try:
            for v in rollback_versions:
                migration = self._migrations[v]
                logger.info("[DBMigration] 正在執行回滾: v%d - %s", migration.version, migration.name)
                try:
                    conn.execute("BEGIN IMMEDIATE;")
                    if migration.down:
                        migration.down(conn)
                    # 精確查找小於當前 v 的最大已註冊版本
                    prior_versions = [ver for ver in available_versions if ver < v]
                    prev_version = prior_versions[-1] if prior_versions else 0
                    if prev_version < target_version:
                        prev_version = target_version
                    set_schema_version(conn, prev_version)
                    conn.commit()
                    logger.info("[DBMigration] 回滾成功: 當前版本降為 v%d", prev_version)
                except Exception as e:
                    conn.rollback()
                    logger.error("[DBMigration] 回滾 v%d 失敗，已回復交易: %s", migration.version, e)
                    raise
        finally:
            conn.close()

        return get_schema_version(self.db_path)
