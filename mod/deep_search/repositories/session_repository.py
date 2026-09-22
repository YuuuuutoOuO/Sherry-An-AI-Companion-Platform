"""
session_repository.py (mod/deep_search/repositories)
===================================================
深度研究專案歷程與狀態的 SQLite 資料庫儲存倉儲。
將每份 ResearchSession 持久化儲存於 data/deep_search/deep_search.db。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from core.db import get_db_connection, db_transaction
from mod.deep_search import config
from mod.deep_search.domain.interfaces import ISessionRepository
from mod.deep_search.domain.models import ResearchSession

logger = logging.getLogger(__name__)


class SqliteSessionRepository(ISessionRepository):
    """基於 SQLite 之深度研究專案資料倉儲。"""

    def __init__(self, db_path: Path | None = None, storage_dir: Path | None = None) -> None:
        if db_path is not None:
            self.db_path = db_path
        elif storage_dir is not None:
            if storage_dir.suffix == ".db":
                self.db_path = storage_dir
            else:
                self.db_path = storage_dir / "deep_search.db"
        else:
            sub_db = config.DEEP_SEARCH_DIR / "deep_search.db"
            root_db = config.DATA_DIR / "deep_search.db"
            if sub_db.exists():
                self.db_path = sub_db
            elif root_db.exists():
                self.db_path = root_db
            else:
                self.db_path = sub_db

        self._init_db()

    def _init_db(self) -> None:
        """初始化 SQLite 資料表與索引。"""
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with db_transaction(self.db_path) as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS research_sessions (
                        session_id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        topic TEXT NOT NULL,
                        data_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_ds_updated ON research_sessions(updated_at DESC);")
        except Exception as e:
            logger.error("[deep_search.repository] 初始化 SQLite 資料庫失敗: %s", e)

    def save_session(self, session: ResearchSession) -> bool:
        """儲存或更新 ResearchSession。

        Args:
            session: 研究專案資料。

        Returns:
            bool: 成功寫入返回 True，否則返回 False。
        """
        try:
            data = session.to_dict()
            title = str(session.topic or session.session_id)
            topic = str(session.topic or "")
            updated_at = str(
                getattr(session, "finished_at", "")
                or getattr(session, "started_at", "")
                or datetime.now(timezone.utc).isoformat()
            )
            data_json = json.dumps(data, ensure_ascii=False)

            with db_transaction(self.db_path) as cur:
                cur.execute("""
                    INSERT OR REPLACE INTO research_sessions (session_id, title, topic, data_json, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (session.session_id, title, topic, data_json, updated_at))
            return True
        except Exception as exc:
            logger.error(
                "[deep_search.repository] 儲存 Session 失敗 (id=%s): %s",
                session.session_id,
                exc,
            )
            return False

    def load_session(self, session_id: str) -> ResearchSession | None:
        """依 session_id 載入特定研究專案。

        Args:
            session_id: 唯一識別碼。

        Returns:
            ResearchSession | None: 成功則返回物件，否則返回 None。
        """
        try:
            conn = get_db_connection(self.db_path)
            cur = conn.cursor()
            cur.execute("SELECT data_json FROM research_sessions WHERE session_id = ?", (session_id,))
            row = cur.fetchone()
            conn.close()

            if row:
                data = json.loads(row["data_json"])
                return ResearchSession.from_dict(data)
        except Exception as exc:
            logger.error(
                "[deep_search.repository] 讀取 SQLite Session 失敗 (id=%s): %s",
                session_id,
                exc,
            )
        return None

    def list_sessions(self, limit: int = 50) -> list[ResearchSession]:
        """列出所有研究專案歷史 (依照更新時間由新到舊排序)。

        Args:
            limit: 獲取上限筆數。

        Returns:
            list[ResearchSession]: 專案清單。
        """
        sessions: list[ResearchSession] = []
        try:
            conn = get_db_connection(self.db_path)
            cur = conn.cursor()
            cur.execute("""
                SELECT data_json FROM research_sessions
                ORDER BY updated_at DESC
                LIMIT ?
            """, (limit,))
            rows = cur.fetchall()
            conn.close()

            for r in rows:
                try:
                    data = json.loads(r["data_json"])
                    sessions.append(ResearchSession.from_dict(data))
                except Exception:
                    pass
            return sessions
        except Exception as exc:
            logger.error("[deep_search.repository] 列出 Sessions 失敗: %s", exc)
            return []

    def delete_session(self, session_id: str) -> bool:
        """刪除特定研究專案。

        Args:
            session_id: 唯一識別碼。

        Returns:
            bool: 成功刪除返回 True。
        """
        try:
            with db_transaction(self.db_path) as cur:
                cur.execute("DELETE FROM research_sessions WHERE session_id = ?", (session_id,))
            return True
        except Exception as exc:
            logger.error(
                "[deep_search.repository] 刪除 Session 失敗 (id=%s): %s",
                session_id,
                exc,
            )
            return False


# 向下相容別名
FileSessionRepository = SqliteSessionRepository
