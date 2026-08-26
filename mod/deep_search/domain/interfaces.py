"""
interfaces.py (mod/deep_search/domain)
======================================
抽象契約定義 (遵守 AGENTS.md 介面隔離與依賴注入原則)。
Service 層與業務邏輯僅依賴這些抽象合約。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from mod.deep_search.domain.models import (
    ResearchProgress,
    ResearchSession,
    SearchResultItem,
    SourceDoc,
)


class ISearchClient(ABC):
    """網路搜尋引擎抽象介面。"""

    @abstractmethod
    async def search(self, query: str, max_results: int = 4) -> list[SearchResultItem]:
        """對搜尋引擎進行關鍵字檢索。

        Args:
            query: 搜尋關鍵字。
            max_results: 最多返回之結果筆數。

        Returns:
            list[SearchResultItem]: 搜尋結果清單。
        """
        pass


class IScraperClient(ABC):
    """網頁正文解析與擷取抽象介面。"""

    @abstractmethod
    async def scrape(self, url: str, snippet: str = "") -> SourceDoc | None:
        """擷取指定 URL 之純淨內文。

        Args:
            url: 網頁 URL。
            snippet: 原始搜尋引擎摘要。

        Returns:
            SourceDoc | None: 擷取成功返回 SourceDoc，失敗或超時返回 None。
        """
        pass


class ILLMClient(ABC):
    """LLM 推論抽象介面。"""

    @abstractmethod
    async def generate(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> str:
        """調用 LLM 生成回應。

        Args:
            model: 模型識別字串 (例如: lms:... 或 ollama:...)。
            system_prompt: 系統提示詞。
            user_prompt: 使用者提示詞。
            temperature: 採樣溫度 (深度研究預設較低，維持嚴謹)。
            max_tokens: 最大生成 Token 上限 override。
            timeout: 推論逾時秒數 override。

        Returns:
            str: 模型生成之文字內容。
        """
        pass


class ISessionRepository(ABC):
    """研究專案狀態與歷史資料倉儲抽象介面。"""

    @abstractmethod
    def save_session(self, session: ResearchSession) -> bool:
        """儲存或更新 ResearchSession。

        Args:
            session: 研究專案資料物件。

        Returns:
            bool: 儲存是否成功。
        """
        pass

    @abstractmethod
    def load_session(self, session_id: str) -> ResearchSession | None:
        """依 session_id 載入特定研究專案。

        Args:
            session_id: 唯一識別碼。

        Returns:
            ResearchSession | None: 存在則返回物件，否則返回 None。
        """
        pass

    @abstractmethod
    def list_sessions(self, limit: int = 50) -> list[ResearchSession]:
        """列出所有研究專案歷史 (依時間由新到舊排序)。

        Args:
            limit: 獲取上限筆數。

        Returns:
            list[ResearchSession]: 研究專案清單。
        """
        pass

    @abstractmethod
    def delete_session(self, session_id: str) -> bool:
        """刪除特定研究專案。

        Args:
            session_id: 唯一識別碼。

        Returns:
            bool: 刪除是否成功。
        """
        pass


class IDeepSearchEngine(ABC):
    """多輪深度研究推演引擎抽象介面。"""

    @abstractmethod
    async def run_research(
        self,
        topic: str,
        model: str,
        max_rounds: int = 4,
        session_id: str | None = None,
        progress_callback: Callable[[ResearchProgress], Any] | None = None,
    ) -> ResearchSession:
        """啟動並執行完整的多輪自主深度研究迴圈。

        Args:
            topic: 研究主題。
            model: 使用之 LLM 模型。
            max_rounds: 研究子專題探討輪數。
            session_id: 自訂 session_id (可選，預設自動生成)。
            progress_callback: 進度變更即時回呼函式。

        Returns:
            ResearchSession: 最終完成之研究專案狀態。
        """
        pass
