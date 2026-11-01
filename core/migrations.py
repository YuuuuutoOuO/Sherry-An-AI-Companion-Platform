"""版本化資料庫遷移框架（R18）。

每個 migration 為一個檔案、一個版本、單向套用：
- 版本記錄於 SQLite ``PRAGMA user_version``；
- 套用前可選擇備份（CLI），失敗自動 rollback（版本不前進）；
- ``dry_run`` 只列出待套用步驟，不開寫入交易。
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

MigrationFunc = Callable[[sqlite3.Connection], None]


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: MigrationFunc
    #: 空 tuple 表示套用於所有 DB；否則只套用於檔名符合的 DB。
    applies_to: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class MigrationResult:
    version: int
    name: str
    applied: bool
    dry_run: bool
    error: str = ""


class MigrationRunner:
    def __init__(
        self,
        db_path: Path | str,
        migrations: list[Migration],
        *,
        db_filename: str | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_filename = db_filename or self.db_path.name
        selected = [
            migration
            for migration in migrations
            if not migration.applies_to or self.db_filename in migration.applies_to
        ]
        self.migrations = sorted(selected, key=lambda migration: migration.version)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        return conn

    def current_version(self) -> int:
        if not self.db_path.exists():
            return 0
        # 唯讀連線：status／dry-run 不應因 PRAGMA journal_mode 而建立 WAL 側檔。
        conn = sqlite3.connect(f"file:{self.db_path.as_posix()}?mode=ro", uri=True)
        try:
            conn.execute("PRAGMA busy_timeout = 5000;")
            return int(conn.execute("PRAGMA user_version;").fetchone()[0])
        finally:
            conn.close()

    def pending(self) -> list[Migration]:
        current = self.current_version()
        return [migration for migration in self.migrations if migration.version > current]

    def status(self) -> dict[str, object]:
        current = self.current_version()
        return {
            "db": str(self.db_path),
            "user_version": current,
            "pending": [migration.version for migration in self.pending()],
            "known": [migration.version for migration in self.migrations],
        }

    def backup(self, backup_dir: Path | str | None = None, *, keep: int = 10) -> Path | None:
        """以 SQLite backup API 建立一致性快照（WAL 下亦安全），並校驗、輪替。"""
        if not self.db_path.exists():
            return None
        target_dir = Path(backup_dir) if backup_dir else self.db_path.parent / "backups"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{self.db_path.stem}.v{self.current_version()}.{int(time.time())}.bak"
        self._copy_database(self.db_path, target)
        if not self._integrity_ok(target):
            raise RuntimeError(f"備份校驗失敗：{target}")
        self._rotate_backups(target_dir, keep=keep)
        return target

    @staticmethod
    def _copy_database(source: Path, target: Path) -> None:
        """使用 sqlite3 backup API 複製，避免 WAL 未 checkpoint 造成資料遺失。"""
        src_conn = sqlite3.connect(str(source))
        dst_conn = sqlite3.connect(str(target))
        try:
            src_conn.backup(dst_conn)
            dst_conn.commit()
        finally:
            dst_conn.close()
            src_conn.close()

    @staticmethod
    def _integrity_ok(path: Path) -> bool:
        conn = sqlite3.connect(str(path))
        try:
            return str(conn.execute("PRAGMA integrity_check;").fetchone()[0]).lower() == "ok"
        finally:
            conn.close()

    @staticmethod
    def _rotate_backups(backup_dir: Path, *, keep: int) -> None:
        if keep <= 0:
            return
        backups = sorted(
            backup_dir.glob("*.v*.bak"),
            key=lambda path: path.stat().st_mtime,
        )
        for stale in backups[:-keep]:
            try:
                stale.unlink()
            except OSError:
                logger.warning("[migrations] 無法刪除舊備份：%s", stale)

    def restore(self, backup_path: Path | str) -> Path:
        """還原指定備份；先為現況建立 pre_rollback 安全備份，再校驗還原結果。"""
        backup = Path(backup_path)
        if not backup.exists():
            raise FileNotFoundError(f"找不到備份：{backup}")
        if not self._integrity_ok(backup):
            raise RuntimeError(f"備份校驗失敗：{backup}")

        safety: Path | None = None
        if self.db_path.exists():
            target_dir = backup.parent
            safety = target_dir / (
                f"{self.db_path.stem}.pre_rollback.v{self.current_version()}."
                f"{int(time.time())}.bak"
            )
            self._copy_database(self.db_path, safety)

        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(self.db_path) + suffix)
            if sidecar.exists():
                try:
                    sidecar.unlink()
                except PermissionError as exc:
                    raise RuntimeError(
                        f"{sidecar.name} 正被其他行程使用；請先停止服務再 rollback"
                    ) from exc
        shutil.copy2(backup, self.db_path)
        if not self._integrity_ok(self.db_path):
            raise RuntimeError(f"還原後校驗失敗：{self.db_path}")
        return safety or self.db_path


    def run(self, *, dry_run: bool = False) -> list[MigrationResult]:
        results: list[MigrationResult] = []
        for migration in self.pending():
            if dry_run:
                results.append(
                    MigrationResult(migration.version, migration.name, applied=False, dry_run=True)
                )
                continue
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                migration.apply(conn)
                # PRAGMA 不支援 parameter binding，version 已由 Migration 型別約束為 int。
                conn.execute(f"PRAGMA user_version = {int(migration.version)};")
                conn.commit()
                results.append(
                    MigrationResult(migration.version, migration.name, applied=True, dry_run=False)
                )
                logger.info("[migrations] applied v%d %s", migration.version, migration.name)
            except Exception as exc:
                conn.rollback()
                results.append(
                    MigrationResult(
                        migration.version,
                        migration.name,
                        applied=False,
                        dry_run=False,
                        error=str(exc),
                    )
                )
                logger.error("[migrations] v%d %s failed: %s", migration.version, migration.name, exc)
                break
            finally:
                conn.close()
        return results
