"""handlers/emotion/service.py
==========================
動態情感狀態機服務與 SQLite 持久化層。
維護 channel/user/guild 各層級之 PAD 情感狀態、歷史事件日誌與衰減推移。
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
import sqlite3
import threading
from typing import Mapping

from handlers.emotion.decay import decay_affect_state
from handlers.emotion.state import AffectState, clamp
import settings

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = getattr(settings, "DATA_DIR", Path("data")) / "sherry_memory.db"
_SERVICE_LOCK = threading.Lock()
_GLOBAL_EMOTION_SERVICE: EmotionService | None = None


class EmotionService:
    """動態情感狀態機服務。"""

    def __init__(self, db_path: Path | str | None = None) -> None:
        if db_path is None:
            self.db_path = Path(_DEFAULT_DB_PATH)
        else:
            self.db_path = Path(db_path)

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS affect_state (
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    pleasure REAL NOT NULL DEFAULT 0.0,
                    arousal REAL NOT NULL DEFAULT 0.0,
                    dominance REAL NOT NULL DEFAULT 0.0,
                    last_event TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (entity_type, entity_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS affect_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    event_name TEXT NOT NULL,
                    delta_p REAL NOT NULL,
                    delta_a REAL NOT NULL,
                    delta_d REAL NOT NULL,
                    note TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def get_state(
        self,
        entity_type: str,
        entity_id: str,
        *,
        auto_decay: bool = True,
        now: datetime | None = None,
        half_life_hours: float | Mapping[str, float] | None = None,
    ) -> AffectState:
        """取得指定實體之情感狀態，預設自動套用衰減。"""
        if now is None:
            now = datetime.now(timezone.utc)

        with self._lock, self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT entity_type, entity_id, pleasure, arousal, dominance, last_event, updated_at
                FROM affect_state
                WHERE entity_type = ? AND entity_id = ?
                """,
                (entity_type, entity_id),
            )
            row = cur.fetchone()

            if not row:
                return AffectState(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    pleasure=0.0,
                    arousal=0.0,
                    dominance=0.0,
                    last_event=None,
                    updated_at=now.isoformat(),
                )

            current_state = AffectState(
                entity_type=row["entity_type"],
                entity_id=row["entity_id"],
                pleasure=float(row["pleasure"]),
                arousal=float(row["arousal"]),
                dominance=float(row["dominance"]),
                last_event=row["last_event"],
                updated_at=row["updated_at"],
            )

            if not auto_decay:
                return current_state

            decayed = decay_affect_state(
                current_state, now=now, half_life_hours=half_life_hours
            )

            # 若數值因衰減產生實質變化，寫回資料庫保持最新
            if (
                abs(decayed.pleasure - current_state.pleasure) > 0.001
                or abs(decayed.arousal - current_state.arousal) > 0.001
                or abs(decayed.dominance - current_state.dominance) > 0.001
            ):
                cur.execute(
                    """
                    UPDATE affect_state
                    SET pleasure = ?, arousal = ?, dominance = ?, updated_at = ?
                    WHERE entity_type = ? AND entity_id = ?
                    """,
                    (
                        decayed.pleasure,
                        decayed.arousal,
                        decayed.dominance,
                        decayed.updated_at,
                        entity_type,
                        entity_id,
                    ),
                )
                conn.commit()

            return decayed

    def apply_event(
        self,
        entity_type: str,
        entity_id: str,
        event_name: str,
        delta_p: float,
        delta_a: float,
        delta_d: float,
        *,
        note: str = "",
        now: datetime | None = None,
        half_life_hours: float | Mapping[str, float] | None = None,
    ) -> AffectState:
        """對指定實體套用情緒事件（先計算衰減，再疊加有界增量，並記錄事件）。"""
        if now is None:
            now = datetime.now(timezone.utc)

        with self._lock, self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT entity_type, entity_id, pleasure, arousal, dominance, last_event, updated_at
                FROM affect_state
                WHERE entity_type = ? AND entity_id = ?
                """,
                (entity_type, entity_id),
            )
            row = cur.fetchone()

            if row:
                base_state = AffectState(
                    entity_type=row["entity_type"],
                    entity_id=row["entity_id"],
                    pleasure=float(row["pleasure"]),
                    arousal=float(row["arousal"]),
                    dominance=float(row["dominance"]),
                    last_event=row["last_event"],
                    updated_at=row["updated_at"],
                )
                decayed = decay_affect_state(
                    base_state, now=now, half_life_hours=half_life_hours
                )
            else:
                decayed = AffectState(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    pleasure=0.0,
                    arousal=0.0,
                    dominance=0.0,
                    last_event=None,
                    updated_at=now.isoformat(),
                )

            new_p = clamp(decayed.pleasure + float(delta_p))
            new_a = clamp(decayed.arousal + float(delta_a))
            new_d = clamp(decayed.dominance + float(delta_d))

            new_state = AffectState(
                entity_type=entity_type,
                entity_id=entity_id,
                pleasure=new_p,
                arousal=new_a,
                dominance=new_d,
                last_event=event_name,
                updated_at=now.isoformat(),
            )

            cur.execute(
                """
                INSERT INTO affect_state (entity_type, entity_id, pleasure, arousal, dominance, last_event, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_type, entity_id) DO UPDATE SET
                    pleasure = excluded.pleasure,
                    arousal = excluded.arousal,
                    dominance = excluded.dominance,
                    last_event = excluded.last_event,
                    updated_at = excluded.updated_at
                """,
                (
                    new_state.entity_type,
                    new_state.entity_id,
                    new_state.pleasure,
                    new_state.arousal,
                    new_state.dominance,
                    new_state.last_event,
                    new_state.updated_at,
                ),
            )

            cur.execute(
                """
                INSERT INTO affect_events (entity_type, entity_id, event_name, delta_p, delta_a, delta_d, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entity_type,
                    entity_id,
                    event_name,
                    delta_p,
                    delta_a,
                    delta_d,
                    note,
                    now.isoformat(),
                ),
            )

            # 保持事件表上限 500 條
            cur.execute(
                """
                DELETE FROM affect_events
                WHERE id NOT IN (
                    SELECT id FROM affect_events ORDER BY id DESC LIMIT 500
                )
                """
            )
            conn.commit()

            return new_state

    def reset_state(self, entity_type: str, entity_id: str) -> AffectState:
        """重置指定實體的情感狀態至中性零點。"""
        now = datetime.now(timezone.utc)
        with self._lock, self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE affect_state
                SET pleasure = 0.0, arousal = 0.0, dominance = 0.0, last_event = 'reset', updated_at = ?
                WHERE entity_type = ? AND entity_id = ?
                """,
                (now.isoformat(), entity_type, entity_id),
            )
            conn.commit()

        return AffectState(
            entity_type=entity_type,
            entity_id=entity_id,
            pleasure=0.0,
            arousal=0.0,
            dominance=0.0,
            last_event="reset",
            updated_at=now.isoformat(),
        )

    def get_recent_events(
        self,
        entity_type: str | None = None,
        entity_id: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """查詢近期發生的情感事件清單。"""
        with self._lock, self._get_connection() as conn:
            cur = conn.cursor()
            if entity_type and entity_id:
                cur.execute(
                    """
                    SELECT id, entity_type, entity_id, event_name, delta_p, delta_a, delta_d, note, created_at
                    FROM affect_events
                    WHERE entity_type = ? AND entity_id = ?
                    ORDER BY id DESC LIMIT ?
                    """,
                    (entity_type, entity_id, limit),
                )
            else:
                cur.execute(
                    """
                    SELECT id, entity_type, entity_id, event_name, delta_p, delta_a, delta_d, note, created_at
                    FROM affect_events
                    ORDER BY id DESC LIMIT ?
                    """,
                    (limit,),
                )
            rows = cur.fetchall()
            return [dict(r) for r in rows]

    def get_all_states(self, limit: int = 50) -> list[dict]:
        """取得所有實體的情感狀態列表（供 WebUI 展示）。"""
        with self._lock, self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT entity_type, entity_id, pleasure, arousal, dominance, last_event, updated_at
                FROM affect_state
                ORDER BY updated_at DESC LIMIT ?
                """,
                (limit,),
            )
            rows = cur.fetchall()
            results = []
            now = datetime.now(timezone.utc)
            for r in rows:
                state = AffectState(
                    entity_type=r["entity_type"],
                    entity_id=r["entity_id"],
                    pleasure=float(r["pleasure"]),
                    arousal=float(r["arousal"]),
                    dominance=float(r["dominance"]),
                    last_event=r["last_event"],
                    updated_at=r["updated_at"],
                )
                decayed = decay_affect_state(state, now=now)
                results.append(decayed.to_dict())
            return results


def get_emotion_service(db_path: Path | str | None = None) -> EmotionService:
    """取得或建立全域單例 EmotionService。"""
    global _GLOBAL_EMOTION_SERVICE
    with _SERVICE_LOCK:
        if _GLOBAL_EMOTION_SERVICE is None:
            _GLOBAL_EMOTION_SERVICE = EmotionService(db_path=db_path)
        return _GLOBAL_EMOTION_SERVICE
