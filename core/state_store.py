"""跨行程狀態儲存（R17）。

提供 ``SessionStore`` / ``RateLimitStore`` / ``LoginLockout`` 的 SQLite 實作。
所有實作皆為 ``MutableMapping``，可直接替換既有 in-process dict，讓 WebUI
session、登入鎖定與限流狀態在重啟後仍可延續，亦可供多實例共用（同一 DB）。

介面刻意保持最小：正式部署可換成 Redis 實作，呼叫端不需改動。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator, MutableMapping
from pathlib import Path
from typing import Any, Generic, TypeVar

import settings

K = TypeVar("K")
V = TypeVar("V")


def get_state_db_path() -> Path:
    """回傳 runtime state DB 路徑（可用環境變數覆寫）。"""

    import os

    override = os.getenv("SHERRY_RUNTIME_STATE_DB", "").strip()
    if override:
        return Path(override)
    base = Path(getattr(settings, "DATA_DIR", Path("data")))
    return base / "runtime_state.db"


class SQLiteJSONMap(MutableMapping[K, V], Generic[K, V]):
    """以 SQLite 為後端的 JSON key-value mapping（支援任意可 JSON 化 key）。"""

    def __init__(self, scope: str, db_path: Path | str | None = None) -> None:
        self.scope = scope
        self.db_path = Path(db_path) if db_path is not None else get_state_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), timeout=5.0, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode = WAL;")
        self._conn.execute("PRAGMA busy_timeout = 5000;")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS state_kv (
                scope TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (scope, key)
            )
            """
        )
        self._conn.commit()

    @staticmethod
    def _encode_key(key: Any) -> str:
        return json.dumps(key, ensure_ascii=False, sort_keys=True, default=str)

    @staticmethod
    def _decode_key(raw: str) -> Any:
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return raw

    def __getitem__(self, key: K) -> V:
        import time

        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM state_kv WHERE scope = ? AND key = ?",
                (self.scope, self._encode_key(key)),
            ).fetchone()
        if row is None:
            raise KeyError(key)
        return json.loads(row[0])

    def __setitem__(self, key: K, value: V) -> None:
        import time

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO state_kv(scope, key, value, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(scope, key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (self.scope, self._encode_key(key), json.dumps(value, ensure_ascii=False), time.time()),
            )
            self._conn.commit()

    def __delitem__(self, key: K) -> None:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM state_kv WHERE scope = ? AND key = ?",
                (self.scope, self._encode_key(key)),
            )
            self._conn.commit()
        if cursor.rowcount == 0:
            raise KeyError(key)

    def __iter__(self) -> Iterator[K]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT key FROM state_kv WHERE scope = ?", (self.scope,)
            ).fetchall()
        return iter([self._decode_key(row[0]) for row in rows])

    def __len__(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM state_kv WHERE scope = ?", (self.scope,)
            ).fetchone()
        return int(row[0]) if row else 0

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM state_kv WHERE scope = ?", (self.scope,))
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class InMemoryJSONMap(dict[K, V]):
    """測試／降級用；介面與 ``SQLiteJSONMap`` 相同。"""


class SessionStore(SQLiteJSONMap[str, float]):
    """管理員 session：token -> last_active_ts。"""


class RateLimitStore(SQLiteJSONMap[Any, list[float]]):
    """滑動窗口限流狀態：bucket -> timestamps。"""


class LoginLockout:
    """登入失敗計數與 IP 鎖定政策（狀態由外部 mapping 提供）。"""

    def __init__(
        self,
        attempts: MutableMapping[str, list[float]],
        locked: MutableMapping[str, float],
        *,
        window_sec: float = 60.0,
    ) -> None:
        self.attempts = attempts
        self.locked = locked
        self.window_sec = float(window_sec)

    def is_locked(self, ip: str, now: float | None = None) -> tuple[bool, int]:
        import time

        now = time.time() if now is None else now
        unlock_at = self.locked.get(ip)
        if unlock_at is None:
            return False, 0
        if now < float(unlock_at):
            return True, max(1, int(float(unlock_at) - now))
        self.attempts.pop(ip, None)
        self.locked.pop(ip, None)
        return False, 0

    def record_failure(
        self,
        ip: str,
        *,
        max_attempts: int,
        lockout_sec: float,
        now: float | None = None,
    ) -> tuple[int, int]:
        """記錄一次失敗；回傳（窗口內失敗數, 鎖定剩餘秒數）。"""

        import time

        now = time.time() if now is None else now
        history = [t for t in self.attempts.get(ip, []) if now - float(t) <= self.window_sec]
        history.append(now)
        self.attempts[ip] = history
        if len(history) >= max(1, int(max_attempts)):
            self.locked[ip] = now + float(lockout_sec)
            return len(history), int(lockout_sec)
        return len(history), 0

    def clear(self, ip: str) -> None:
        self.attempts.pop(ip, None)
        self.locked.pop(ip, None)

    def sweep(self, *, max_tracked: int = 1000, now: float | None = None) -> None:
        """清理過期鎖定與失敗紀錄，並限制追蹤表大小。"""

        import time

        now = time.time() if now is None else now
        for ip in [ip for ip, unlock_at in self.locked.items() if now >= float(unlock_at)]:
            self.locked.pop(ip, None)
        for ip in [ip for ip, stamps in self.attempts.items() if not stamps or now - float(stamps[-1]) > self.window_sec]:
            self.attempts.pop(ip, None)
        while len(self.locked) > max_tracked:
            oldest = min(self.locked, key=lambda key: float(self.locked[key]))
            self.locked.pop(oldest, None)
        while len(self.attempts) > max_tracked:
            oldest = min(
                self.attempts,
                key=lambda key: float(self.attempts[key][-1]) if self.attempts[key] else 0.0,
            )
            self.attempts.pop(oldest, None)
