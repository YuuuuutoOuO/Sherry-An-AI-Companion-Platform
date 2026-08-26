"""
scraper_client.py (mod/deep_search/repositories)
================================================
網頁正文解析與深度內容萃取實作。
優先使用 trafilatura 或 BeautifulSoup 進行智慧主體內容提煉，去除廣告、側邊欄與無關導覽列。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import re
import urllib.request

from mod.deep_search import config
from mod.deep_search.domain.interfaces import IScraperClient
from mod.deep_search.domain.models import SourceDoc

logger = logging.getLogger(__name__)


class TrafilaturaScraperClient(IScraperClient):
    """基於 trafilatura / BeautifulSoup 的高品質非同步網頁內文解析客戶端。"""

    def __init__(
        self,
        max_chars: int | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        self._max_chars = max_chars or config.MAX_CHARS_PER_PAGE
        self._timeout_seconds = timeout_seconds or config.DEFAULT_HTTP_TIMEOUT

    async def scrape(self, url: str, snippet: str = "") -> SourceDoc | None:
        """擷取網頁主體內容。

        Args:
            url: 目標網頁 URL。
            snippet: 原始搜尋引擎摘要。

        Returns:
            SourceDoc | None: 擷取成果。
        """
        if not url or not url.startswith("http"):
            return None

        # 略過二進位檔案或影片串流等格式
        if any(url.lower().endswith(ext) for ext in (".pdf", ".zip", ".mp4", ".mp3", ".exe", ".png", ".jpg")):
            return None

        try:
            doc = await asyncio.wait_for(
                asyncio.to_thread(self._fetch_and_extract_sync, url, snippet),
                timeout=float(self._timeout_seconds),
            )
            return doc
        except asyncio.TimeoutError:
            logger.warning("[deep_search.scraper] 擷取逾時 (%ds): %s", self._timeout_seconds, url)
            return None
        except Exception as exc:
            logger.debug("[deep_search.scraper] 擷取例外 (%s): %s", url, exc)
            return None

    def _fetch_and_extract_sync(self, url: str, snippet: str) -> SourceDoc | None:
        """同步執行網頁擷取與正文清洗。"""
        title = ""
        content = ""

        # 1. 優先嘗試 trafilatura (若已安裝)
        try:
            import trafilatura  # type: ignore[import-untyped]

            downloaded = trafilatura.fetch_url(url)
            if downloaded:
                extracted = trafilatura.extract(
                    downloaded,
                    include_links=False,
                    include_comments=False,
                    include_tables=True,
                    output_format="txt",
                )
                if extracted:
                    content = extracted.strip()

                metadata = trafilatura.extract_metadata(downloaded)
                if metadata and metadata.title:
                    title = metadata.title.strip()
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("[deep_search.scraper] trafilatura 萃取略過 (%s): %s", url, exc)

        # 2. 若無內容，嘗試 BeautifulSoup 萃取
        if not content:
            try:
                headers = {"User-Agent": config.USER_AGENT}
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=self._timeout_seconds) as response:
                    raw_html = response.read().decode("utf-8", errors="ignore")

                from bs4 import BeautifulSoup  # type: ignore[import-untyped]

                soup = BeautifulSoup(raw_html, "html.parser")

                # 移除 script, style 等標籤
                for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                    tag.decompose()

                if soup.title and soup.title.string:
                    title = soup.title.string.strip()

                text = soup.get_text(separator="\n")
                lines = [line.strip() for line in text.splitlines() if line.strip()]
                extracted_text = "\n".join(lines)
                if len(extracted_text) > 100:
                    content = extracted_text
            except Exception as exc:
                logger.debug("[deep_search.scraper] BeautifulSoup 備援萃取略過: %s", exc)

        # 3. 若仍無內容，使用搜尋摘要回退 (Fallback)
        if not content and snippet:
            content = f"[文獻摘要情報]：{snippet}"

        if not content:
            return None

        # 截斷字數以符合地端 SLM 上下文限制
        if len(content) > self._max_chars:
            content = content[: self._max_chars] + "\n...(篇幅過長，已智慧截斷)"

        # 若無明確標題，自動從前文截取或使用 URL
        if not title:
            first_line = content.split("\n", 1)[0].strip()
            title = first_line[:60] if first_line else url

        # 移除多餘空行
        content = re.sub(r"\n{3,}", "\n\n", content)

        now_iso = datetime.now(timezone.utc).isoformat()
        return SourceDoc(
            url=url,
            title=title,
            content=content,
            snippet=snippet,
            scraped_at=now_iso,
            char_count=len(content),
        )
