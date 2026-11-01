"""
core/recovery/storage.py
========================
雪莉狀態快照 SQLite WAL 儲存庫。
提供執行緒安全、非同步包裝與高可用性快照持久化管理。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from core.db import get_db_connection
from core.recovery.base import SnapshotMetadata
import settings

logger = logging.getLogger(__name__)


class RecoveryStorage:
    """狀態快照 SQLite 持久化儲存庫。"""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path or getattr(settings, "STATE_RECOVERY_DB_PATH", settings.DATA_DIR / "sherry_recovery.db"))
        self._init_db()

    def _init_db(self) -> None:
        """初始化快照表與索引。"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with get_db_connection(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS system_recovery_snapshots (
                    provider_id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL DEFAULT 1,
                    snapshot_json TEXT NOT NULL,
                    saved_at REAL NOT NULL,
                    ttl_seconds REAL NOT NULL,
                    is_restored INTEGER NOT NULL DEFAULT 0,
                    restore_attempts INTEGER NOT NULL DEFAULT 0,
                    quarantined INTEGER NOT NULL DEFAULT 0,
                    last_attempt_at REAL NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_recovery_ttl 
                ON system_recovery_snapshots(saved_at, ttl_seconds);
                """
            )
            conn.commit()

    def save_snapshot(
        self,
        provider_id: str,
        snapshot_data: dict[str, Any],
        ttl_seconds: float,
        version: int = 1,
    ) -> None:
        """同步儲存或覆寫模組狀態快照。"""
        now = time.time()
        serialized = json.dumps(snapshot_data, ensure_ascii=False)
        with get_db_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO system_recovery_snapshots (
                    provider_id, version, snapshot_json, saved_at, ttl_seconds,
                    is_restored, restore_attempts, quarantined, last_attempt_at
                ) VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0)
                ON CONFLICT(provider_id) DO UPDATE SET
                    version = excluded.version,
                    snapshot_json = excluded.snapshot_json,
                    saved_at = excluded.saved_at,
                    ttl_seconds = excluded.ttl_seconds,
                    is_restored = 0,
                    restore_attempts = 0,
                    quarantined = 0,
                    last_attempt_at = 0;
                """,
                (provider_id, version, serialized, now, float(ttl_seconds)),
            )
            conn.commit()
        logger.debug("[RecoveryStorage] 快照已寫入: provider=%s, ttl=%.1fs", provider_id, ttl_seconds)

    def get_snapshot(
        self, provider_id: str
    ) -> tuple[dict[str, Any] | None, SnapshotMetadata | None]:
        """同步查詢單一模組的最新快照與詮釋資料。"""
        with get_db_connection(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT version, snapshot_json, saved_at, ttl_seconds, is_restored
                FROM system_recovery_snapshots
                WHERE provider_id = ?;
                """,
                (provider_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None, None
            try:
                data = json.loads(row["snapshot_json"])
            except Exception as exc:
                logger.warning("[RecoveryStorage] 快照 JSON 解析失敗 (%s): %s", provider_id, exc)
                return None, None
            metadata = SnapshotMetadata(
                provider_name=provider_id,
                snapshot_time=float(row["saved_at"]),
                ttl_seconds=float(row["ttl_seconds"]),
                version=int(row["version"]),
            )
            return data, metadata

    def get_active_snapshots(
        self,
    ) -> list[tuple[str, dict[str, Any], SnapshotMetadata, int, int]]:
        """同步查詢所有尚未恢復且未隔離的活躍快照。
        
        Returns:
            list of (provider_id, data, metadata, restore_attempts, quarantined)
        """
        results = []
        with get_db_connection(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT provider_id, version, snapshot_json, saved_at, ttl_seconds,
                       restore_attempts, quarantined, last_attempt_at
                FROM system_recovery_snapshots
                WHERE is_restored = 0 AND quarantined = 0;
                """
            )
            rows = cursor.fetchall()
            for row in rows:
                p_id = row["provider_id"]
                try:
                    data = json.loads(row["snapshot_json"])
                except Exception as exc:
                    logger.warning("[RecoveryStorage] 忽略損壞的快照 JSON (%s): %s", p_id, exc)
                    continue
                meta = SnapshotMetadata(
                    provider_name=p_id,
                    snapshot_time=float(row["saved_at"]),
                    ttl_seconds=float(row["ttl_seconds"]),
                    version=int(row["version"]),
                )
                results.append((
                    p_id,
                    data,
                    meta,
                    int(row["restore_attempts"]),
                    int(row["quarantined"]),
                ))
        return results

    def mark_restored(self, provider_id: str) -> None:
        """同步標記指定快照為已恢復完成。"""
        with get_db_connection(self.db_path) as conn:
            conn.execute(
                "UPDATE system_recovery_snapshots SET is_restored = 1 WHERE provider_id = ?;",
                (provider_id,),
            )
            conn.commit()
        logger.debug("[RecoveryStorage] 標記快照為已恢復: %s", provider_id)

    def mark_quarantined(self, provider_id: str) -> None:
        """同步標記指定快照為隔離狀態（防止連續崩潰）。"""
        with get_db_connection(self.db_path) as conn:
            conn.execute(
                "UPDATE system_recovery_snapshots SET quarantined = 1 WHERE provider_id = ?;",
                (provider_id,),
            )
            conn.commit()
        logger.warning("[RecoveryStorage] 快照已隔離防護: %s", provider_id)

    def record_restore_attempt(self, provider_id: str, attempt_time: float | None = None) -> int:
        """記錄一次恢復嘗試，累加次數並回傳新計數。"""
        t = attempt_time or time.time()
        with get_db_connection(self.db_path) as conn:
            conn.execute(
                """
                UPDATE system_recovery_snapshots 
                SET restore_attempts = restore_attempts + 1,
                    last_attempt_at = ?
                WHERE provider_id = ?;
                """,
                (t, provider_id),
            )
            conn.commit()
            cursor = conn.execute(
                "SELECT restore_attempts FROM system_recovery_snapshots WHERE provider_id = ?;",
                (provider_id,),
            )
            row = cursor.fetchone()
            return int(row["restore_attempts"]) if row else 1

    def archive_all_unhandled(self) -> int:
        """將所有未處理快照標記為已歸檔 (is_restored = 1)。"""
        with get_db_connection(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE system_recovery_snapshots SET is_restored = 1 WHERE is_restored = 0;"
            )
            count = cursor.rowcount
            conn.commit()
        logger.info("[RecoveryStorage] 已歸檔 %d 筆未處理快照", count)
        return count

    def clear_snapshot(self, provider_id: str) -> None:
        """刪除指定快照記錄（例如模組已無活躍任務）。"""
        with get_db_connection(self.db_path) as conn:
            conn.execute(
                "DELETE FROM system_recovery_snapshots WHERE provider_id = ?;",
                (provider_id,),
            )
            conn.commit()
        logger.debug("[RecoveryStorage] 已清除快照: %s", provider_id)

    def clear_all(self) -> None:
        """清空全數快照記錄。"""
        with get_db_connection(self.db_path) as conn:
            conn.execute("DELETE FROM system_recovery_snapshots;")
            conn.commit()

    # --- Async Wrappers ---

    async def save_snapshot_async(
        self,
        provider_id: str,
        snapshot_data: dict[str, Any],
        ttl_seconds: float,
        version: int = 1,
    ) -> None:
        await asyncio.to_thread(self.save_snapshot, provider_id, snapshot_data, ttl_seconds, version)

    async def get_snapshot_async(
        self, provider_id: str
    ) -> tuple[dict[str, Any] | None, SnapshotMetadata | None]:
        return await asyncio.to_thread(self.get_snapshot, provider_id)

    async def get_active_snapshots_async(
        self,
    ) -> list[tuple[str, dict[str, Any], SnapshotMetadata, int, int]]:
        return await asyncio.to_thread(self.get_active_snapshots)

    async def mark_restored_async(self, provider_id: str) -> None:
        await asyncio.to_thread(self.mark_restored, provider_id)

    async def mark_quarantined_async(self, provider_id: str) -> None:
        await asyncio.to_thread(self.mark_quarantined, provider_id)

    async def record_restore_attempt_async(
        self, provider_id: str, attempt_time: float | None = None
    ) -> int:
        return await asyncio.to_thread(self.record_restore_attempt, provider_id, attempt_time)

    async def archive_all_unhandled_async(self) -> int:
        return await asyncio.to_thread(self.archive_all_unhandled)

    async def clear_snapshot_async(self, provider_id: str) -> None:
        await asyncio.to_thread(self.clear_snapshot, provider_id)

    async def clear_all_async(self) -> None:
        await asyncio.to_thread(self.clear_all)
