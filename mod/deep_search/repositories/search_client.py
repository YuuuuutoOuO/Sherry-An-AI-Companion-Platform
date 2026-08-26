"""
search_client.py (mod/deep_search/repositories)
===============================================
DuckDuckGo 網路檢索實作。
遵守非同步 I/O 與逾時規範。
"""

from __future__ import annotations

import logging
import re
import urllib.parse

import aiohttp

from mod.deep_search import config
from mod.deep_search.domain.interfaces import ISearchClient
from mod.deep_search.domain.models import SearchResultItem

logger = logging.getLogger(__name__)


class DuckDuckGoSearchClient(ISearchClient):
    """基於 DuckDuckGo HTML 介面之非同步搜尋客戶端。"""

    def __init__(
        self,
        base_url: str | None = None,
        timeout_seconds: int | None = None,
        user_agent: str | None = None,
    ) -> None:
        self._base_url = base_url or config.DUCKDUCKGO_SEARCH_URL
        self._timeout_seconds = timeout_seconds or config.DEFAULT_HTTP_TIMEOUT
        self._user_agent = user_agent or config.USER_AGENT

    async def search(self, query: str, max_results: int = 4) -> list[SearchResultItem]:
        """對 DuckDuckGo 進行搜尋並解析出項目清單。

        Args:
            query: 搜尋關鍵字。
            max_results: 最大返回筆數。

        Returns:
            list[SearchResultItem]: 搜尋結果列表。
        """
        clean_query = query.strip()
        if not clean_query:
            return []

        search_url = f"{self._base_url}{urllib.parse.quote(clean_query)}"
        headers = {
            "User-Agent": self._user_agent,
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        items: list[SearchResultItem] = []

        try:
            async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
                async with session.get(search_url) as resp:
                    if resp.status != 200:
                        logger.warning(
                            "[deep_search.search_client] DuckDuckGo HTTP 錯誤: %d (query: %s)",
                            resp.status,
                            clean_query,
                        )
                        return []

                    html = await resp.text()
                    items = self._parse_html(html, max_results=max_results)

        except Exception as exc:
            logger.warning(
                "[deep_search.search_client] 檢索失敗 (query: %s): %s",
                clean_query,
                exc,
            )
            return []

        logger.info(
            "[deep_search.search_client] 關鍵字 '%s' 成功檢索到 %d 筆文獻來源",
            clean_query,
            len(items),
        )
        return items

    def _parse_html(self, html: str, max_results: int = 4) -> list[SearchResultItem]:
        """從 DuckDuckGo HTML 原始碼中萃取標題、連結與摘要。"""
        if "No results" in html:
            return []

        results: list[SearchResultItem] = []

        # 匹配結果區塊
        # 尋找 <a class="result__url" ... href="..."> 或 <a class="result__snippet" ...>
        # DuckDuckGo HTML 典型格式：<a class="result__snippet ... href="...">
        snippet_matches = re.findall(
            r'<a\s+class="result__snippet[^"]*"\s+href="([^"]+)"[^>]*>(.*?)</a>',
            html,
            re.DOTALL | re.IGNORECASE,
        )

        # 尋找標題 <a class="result__a" href="...">Title</a>
        title_matches = re.findall(
            r'<a\s+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            html,
            re.DOTALL | re.IGNORECASE,
        )

        for idx, (raw_href, raw_snippet) in enumerate(snippet_matches):
            if len(results) >= max_results:
                break

            url = self._extract_clean_url(raw_href)
            if not url or not url.startswith("http"):
                continue

            # 取得對應標題
            title = ""
            if idx < len(title_matches):
                title = re.sub(r"<[^>]+>", "", title_matches[idx][1]).strip()
            if not title:
                title = url

            snippet = re.sub(r"<[^>]+>", "", raw_snippet).strip()
            # 解碼 HTML entities
            title = urllib.parse.unquote(title)
            snippet = urllib.parse.unquote(snippet)

            results.append(
                SearchResultItem(
                    title=title,
                    url=url,
                    snippet=snippet,
                )
            )

        return results

    @staticmethod
    def _extract_clean_url(raw_href: str) -> str:
        """解析 DuckDuckGo 重定向 URL (uddg 參數)。"""
        u_match = re.search(r"uddg=(https?[^&]+)", raw_href)
        if u_match:
            return urllib.parse.unquote(u_match.group(1))
        if raw_href.startswith("http"):
            return raw_href
        return ""
