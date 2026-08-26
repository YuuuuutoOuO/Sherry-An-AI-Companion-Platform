"""
repositories (mod/deep_search)
==============================
外部 I/O 實作層 (唯一的 I/O 依賴模組)。
封裝網路檢索、網頁內文解析、LLM 呼叫與檔案倉儲。
"""

from __future__ import annotations

from mod.deep_search.repositories.llm_client import OllamaResearchClient
from mod.deep_search.repositories.scraper_client import TrafilaturaScraperClient
from mod.deep_search.repositories.search_client import DuckDuckGoSearchClient
from mod.deep_search.repositories.session_repository import FileSessionRepository

__all__ = [
    "DuckDuckGoSearchClient",
    "FileSessionRepository",
    "OllamaResearchClient",
    "TrafilaturaScraperClient",
]
