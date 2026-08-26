"""
session_repository.py (mod/deep_search/repositories)
===================================================
深度研究專案歷程與狀態的 JSON 檔案儲存倉儲。
將每份 ResearchSession 持久化儲存於 data/deep_search/sessions/。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from mod.deep_search import config
from mod.deep_search.domain.interfaces import ISessionRepository
from mod.deep_search.domain.models import ResearchSession

logger = logging.getLogger(__name__)


class FileSessionRepository(ISessionRepository):
    """基於本地檔案系統之 JSON 專案資料倉儲。"""

    def __init__(self, storage_dir: Path | None = None) -> None:
        self._dir = storage_dir or config.SESSIONS_DIR
        self._dir.mkdir(parents=True, exist_ok=True)

    def _get_path(self, session_id: str) -> Path:
        """取得單一 session_id 對應之 JSON 檔案路徑。"""
        # 過濾非法檔名字元
        safe_id = "".join(c for c in session_id if c.isalnum() or c in ("-", "_"))
        return self._dir / f"{safe_id}.json"

    def save_session(self, session: ResearchSession) -> bool:
        """儲存或更新 ResearchSession。

        Args:
            session: 研究專案資料。

        Returns:
            bool: 成功寫入返回 True，否則返回 False。
        """
        try:
            path = self._get_path(session.session_id)
            data = session.to_dict()
            tmp_path = path.with_suffix(".tmp")
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp_path.replace(path)
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
        path = self._get_path(session_id)
        if not path.exists():
            return None

        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return ResearchSession.from_dict(data)
        except Exception as exc:
            logger.error(
                "[deep_search.repository] 讀取 Session 失敗 (path=%s): %s",
                path,
                exc,
            )
        return None

    def list_sessions(self, limit: int = 50) -> list[ResearchSession]:
        """列出所有研究專案歷史 (依照修改時間由新到舊排序)。

        Args:
            limit: 獲取上限筆數。

        Returns:
            list[ResearchSession]: 專案清單。
        """
        sessions: list[ResearchSession] = []
        if not self._dir.exists():
            return sessions

        files = sorted(
            self._dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        for path in files[:limit]:
            try:
                with path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    sessions.append(ResearchSession.from_dict(data))
            except Exception as exc:
                logger.debug(
                    "[deep_search.repository] 解析 Session 檔案略過 (%s): %s",
                    path.name,
                    exc,
                )

        return sessions

    def delete_session(self, session_id: str) -> bool:
        """刪除特定研究專案。

        Args:
            session_id: 唯一識別碼。

        Returns:
            bool: 成功刪除返回 True。
        """
        path = self._get_path(session_id)
        if path.exists():
            try:
                path.unlink()
                return True
            except Exception as exc:
                logger.error(
                    "[deep_search.repository] 刪除 Session 失敗 (id=%s): %s",
                    session_id,
                    exc,
                )
                return False
        return False
