"""handlers/spark/clients/web_monitor_client.py - Spark 網頁巡檢檢索客戶端。

負責執行定時網頁巡檢與即時新聞情報檢索。
實作 WebMonitorClientProtocol。
"""

from __future__ import annotations

import logging
from typing import Any

from mod.deep_search.repositories.search_client import DuckDuckGoSearchClient

logger = logging.getLogger(__name__)


class WebMonitorClient:
    """基於 DuckDuckGo 的 Spark 智慧網頁巡檢客戶端。"""

    def __init__(self, search_client: Any | None = None) -> None:
        """初始化 WebMonitorClient。

        Args:
            search_client: 底層檢索客戶端實例（預設為 DuckDuckGoSearchClient）。
        """
        self._search_client = search_client or DuckDuckGoSearchClient()

    async def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """執行即時網路檢索並回傳條目字典清單。

        Args:
            query: 檢索關鍵字。
            limit: 最大回傳筆數。

        Returns:
            list[dict[str, Any]]: 包含 title, snippet, url 之字典清單。
        """
        clean_query = query.strip()
        if not clean_query:
            return []

        try:
            results = await self._search_client.search(clean_query, max_results=limit)
            items: list[dict[str, Any]] = []
            for r in results:
                items.append({
                    "title": getattr(r, "title", "") or "",
                    "snippet": getattr(r, "snippet", "") or "",
                    "url": getattr(r, "url", "") or "",
                })
            return items
        except Exception as exc:
            logger.warning("[WebMonitorClient] 檢索失敗 (%s): %s", clean_query, exc)
            return []
