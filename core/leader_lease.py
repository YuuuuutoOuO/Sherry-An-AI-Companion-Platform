"""SQLite 租約式 leader 選舉（R17）。

用途：多實例部署時只允許一個實例執行排程巡檢（SPARK），避免重複推播。
租約以 ``BEGIN IMMEDIATE`` 原子更新；持有者需定期 renew，過期即可被接手。
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from core.state_store import get_state_db_path


class LeaderLease:
    """具 TTL 的單一 leader 租約。"""

    def __init__(
        self,
        name: str,
        owner: str,
        db_path: Path | str | None = None,
    ) -> None:
        self.name = name
        self.owner = owner
        self.db_path = Path(db_path) if db_path is not None else get_state_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS leader_leases (
                    name TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        return conn

    def acquire(self, ttl_sec: float = 90.0, now: float | None = None) -> bool:
        """嘗試取得或續約；回傳是否持有租約。"""

        now = time.time() if now is None else float(now)
        expires_at = now + max(1.0, float(ttl_sec))
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT owner, expires_at FROM leader_leases WHERE name = ?",
                (self.name,),
            ).fetchone()
            if row is not None and row[0] != self.owner and float(row[1]) > now:
                conn.rollback()
                return False
            conn.execute(
                """
                INSERT INTO leader_leases(name, owner, expires_at) VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    owner = excluded.owner,
                    expires_at = excluded.expires_at
                """,
                (self.name, self.owner, expires_at),
            )
            conn.commit()
            return True
        except sqlite3.OperationalError:
            conn.rollback()
            return False
        finally:
            conn.close()

    def renew(self, ttl_sec: float = 90.0, now: float | None = None) -> bool:
        now = time.time() if now is None else float(now)
        conn = self._connect()
        try:
            cursor = conn.execute(
                "UPDATE leader_leases SET expires_at = ? WHERE name = ? AND owner = ?",
                (now + max(1.0, float(ttl_sec)), self.name, self.owner),
            )
            conn.commit()
            return cursor.rowcount == 1
        finally:
            conn.close()

    def is_leader(self, now: float | None = None) -> bool:
        now = time.time() if now is None else float(now)
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT owner, expires_at FROM leader_leases WHERE name = ?",
                (self.name,),
            ).fetchone()
            return bool(row and row[0] == self.owner and float(row[1]) > now)
        finally:
            conn.close()

    def release(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "DELETE FROM leader_leases WHERE name = ? AND owner = ?",
                (self.name, self.owner),
            )
            conn.commit()
        finally:
            conn.close()
