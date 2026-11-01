"""mod/spark/clients/rss_client.py - RSS / Atom / YouTube 訂閱客戶端。

負責透過 aiohttp 抓取 XML 資料並解析為標準 RssItem 結構。
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any

import aiohttp

from ..config import DEFAULT_TIMEOUT_SEC
from ..models import RssItem

logger = logging.getLogger(__name__)

_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Sherry-Spark/1.0",
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
}


_BLOCKED_FEED_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1"})


def _resolve_feed_hostname(url: str) -> str | None:
    """驗證 URL 結構並回傳可解析的主機名稱；不合格時回傳 None。"""
    if not url or not isinstance(url, str):
        return None
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        return None

    hostname = parsed.hostname
    if not hostname:
        return None

    # 阻擋特殊主機名與 localhost
    if hostname.lower() in _BLOCKED_FEED_HOSTNAMES:
        return None
    return hostname


def _is_safe_feed_addr_info(addr_info: list[tuple[Any, ...]]) -> bool:
    """檢查 DNS 解析結果中的所有 IP 是否皆為公網位址。"""
    for entry in addr_info:
        ip_str = entry[4][0]
        ip = ipaddress.ip_address(ip_str)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
            or not ip.is_global
        ):
            return False
    return True


def is_safe_feed_url(url: str) -> bool:
    """檢查目標 Feed URL 是否為合法且安全的公開網路位址（阻擋 SSRF、內網 IP 與 Localhost）。

    此同步版本保留給非同步事件循環以外的呼叫端（CLI／測試）；事件循環內請使用
    ``is_safe_feed_url_async``，避免 DNS 解析阻塞其他任務。

    Args:
        url: 待檢查的目標網址。

    Returns:
        bool: True 表示為安全的外部公網網址，False 表示為潛在危險位址。
    """
    try:
        hostname = _resolve_feed_hostname(url)
        if hostname is None:
            return False

        # 解析 IP 並判定是否為私有/環回/保留網段
        return _is_safe_feed_addr_info(socket.getaddrinfo(hostname, None))
    except Exception as exc:
        logger.debug("[RssClient] URL 安全檢查判定不通過: %s (%s)", url, exc)
        return False


async def is_safe_feed_url_async(url: str) -> bool:
    """``is_safe_feed_url`` 的非同步版本：DNS 解析交由 event loop 執行。

    使用 ``loop.getaddrinfo`` 讓 DNS 查詢在預設 executor 中進行，避免同步
    ``socket.getaddrinfo`` 卡住整個事件循環。IP 安全判定邏輯與同步版本共用，
    確保 SSRF 防護行為一致。

    Args:
        url: 待檢查的目標網址。

    Returns:
        bool: True 表示為安全的外部公網網址，False 表示為潛在危險位址。
    """
    try:
        hostname = _resolve_feed_hostname(url)
        if hostname is None:
            return False

        loop = asyncio.get_running_loop()
        addr_info = await loop.getaddrinfo(hostname, None)
        return _is_safe_feed_addr_info(addr_info)
    except Exception as exc:
        logger.debug("[RssClient] URL 安全檢查判定不通過: %s (%s)", url, exc)
        return False


class RssClient:
    """RSS 與 Atom Feed 訂閱抓取客戶端。"""

    def __init__(
        self,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """初始化 RSS 客戶端。

        Args:
            timeout_sec: 網路請求超時秒數。
            session: 可選的外部共用 aiohttp.ClientSession。
        """
        self._timeout = aiohttp.ClientTimeout(total=timeout_sec)
        self._session = session
        self._own_session = session is None

    async def _get_session(self) -> aiohttp.ClientSession:
        """取得或建立 persistent ClientSession。"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
            self._own_session = True
        return self._session

    async def close(self) -> None:
        """關閉底層自行維護之 ClientSession。"""
        if self._own_session and self._session and not self._session.closed:
            await self._session.close()

    async def fetch_latest_items(self, feed_url: str, limit: int = 5) -> list[RssItem]:
        """抓取並解析指定 Feed 的最新文章或影片項目。

        Args:
            feed_url: RSS / Atom / YouTube Feed 網址。
            limit: 最大回傳筆數。

        Returns:
            list[RssItem]: 解析後的文章項目清單。
        """
        if not await is_safe_feed_url_async(feed_url):
            logger.warning("[RssClient] 拒絕不安全的目標 Feed URL (SSRF 防禦): %s", feed_url)
            return []

        try:
            session = await self._get_session()
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
