"""mod/spark/clients/rss_client.py - RSS / Atom / YouTube 訂閱客戶端。

負責透過 aiohttp 抓取 XML 資料並解析為標準 RssItem 結構。
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET

import aiohttp

from ..config import DEFAULT_TIMEOUT_SEC
from ..models import RssItem

logger = logging.getLogger(__name__)

_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Sherry-Spark/1.0",
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
}


class RssClient:
    """RSS 與 Atom Feed 訂閱抓取客戶端。"""

    def __init__(self, timeout_sec: int = DEFAULT_TIMEOUT_SEC) -> None:
        """初始化 RSS 客戶端。

        Args:
            timeout_sec: 網路請求超時秒數。
        """
        self._timeout = aiohttp.ClientTimeout(total=timeout_sec)

    async def fetch_latest_items(self, feed_url: str, limit: int = 5) -> list[RssItem]:
        """抓取並解析指定 Feed 的最新文章或影片項目。

        Args:
            feed_url: RSS / Atom / YouTube Feed 網址。
            limit: 最大回傳筆數。

        Returns:
            list[RssItem]: 解析後的文章項目清單。
        """
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.get(feed_url, headers=_DEFAULT_HEADERS) as resp:
                    if resp.status != 200:
                        logger.warning(
                            "[RssClient] 抓取失敗 HTTP %d: %s", resp.status, feed_url
                        )
                        return []
                    xml_text = await resp.text(encoding="utf-8", errors="replace")
                    return self.parse_feed(xml_text, limit=limit)
        except Exception as exc:
            logger.warning("[RssClient] 抓取 RSS 發生異常 (%s): %s", feed_url, exc)
            return []

    def parse_feed(self, xml_content: str, limit: int = 5) -> list[RssItem]:
        """同步解析 RSS/Atom XML 字串。

        Args:
            xml_content: 原始 XML 字串。
            limit: 最大回傳筆數。

        Returns:
            list[RssItem]: 解析出的項目列表。
        """
        items: list[RssItem] = []
        if not xml_content.strip():
            return items

        try:
            root = ET.fromstring(xml_content)
        except Exception as exc:
            logger.debug("[RssClient] XML 解析失敗: %s", exc)
            return items

        # 1. 檢查是否為 RSS 2.0 (<rss><channel><item>...</item></channel></rss>)
        channel = root.find("channel")
        if channel is not None:
            for elem in channel.findall("item"):
                title = (elem.findtext("title") or "").strip()
                link = (elem.findtext("link") or "").strip()
                guid = (elem.findtext("guid") or link).strip()
                pub_date = (elem.findtext("pubDate") or "").strip()
                desc = (elem.findtext("description") or "").strip()
                if title or link:
                    items.append(
                        RssItem(
                            item_id=guid or link or title,
                            title=title,
                            url=link,
                            published_at=pub_date,
                            summary=desc[:300],
                        )
                    )
                if len(items) >= limit:
                    break
            return items

        # 2. 檢查是否為 Atom 或 YouTube Feed (<feed xmlns="http://www.w3.org/2005/Atom"><entry>...)
        # 移除 XML Namespace 前綴以簡化比對
        for elem in root.iter():
            if "}" in elem.tag:
                elem.tag = elem.tag.split("}", 1)[1]

        for entry in root.findall("entry"):
            title = (entry.findtext("title") or "").strip()
            link_elem = entry.find("link")
            link = ""
            if link_elem is not None:
                link = link_elem.attrib.get("href", "") or (link_elem.text or "").strip()
            
            entry_id = (entry.findtext("id") or "").strip()
            video_id = (entry.findtext("videoId") or "").strip()
            published = (entry.findtext("published") or entry.findtext("updated") or "").strip()
            summary = (entry.findtext("summary") or entry.findtext("content") or "").strip()

            final_id = video_id or entry_id or link or title
            if title or link:
                items.append(
                    RssItem(
                        item_id=final_id,
                        title=title,
                        url=link,
                        published_at=published,
                        summary=summary[:300],
                    )
                )
            if len(items) >= limit:
                break

        return items
