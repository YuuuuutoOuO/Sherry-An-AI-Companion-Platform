"""mod/spark/checkers/rss_checker.py - RSS / 內容更新監控策略。

純業務邏輯層：透過 RssClientProtocol 協定注入抓取客戶端。
"""

from __future__ import annotations

import logging
from typing import Protocol

from ..models import RssItem, Trigger, TriggerResult

logger = logging.getLogger(__name__)


class RssClientProtocol(Protocol):
    """RSS 訂閱客戶端協定介面。"""

    async def fetch_latest_items(self, feed_url: str, limit: int = 5) -> list[RssItem]:
        """抓取並解析 Feed 最新項目清單。"""
        ...


class RssChecker:
    """RSS / Atom / YouTube 訂閱更新檢查器。"""

    trigger_type: str = "rss"

    def __init__(self, rss_client: RssClientProtocol) -> None:
        """初始化 RSS 檢查器。

        Args:
            rss_client: 實作 RssClientProtocol 的訂閱客戶端。
        """
        self._client = rss_client

    async def should_fire(self, trigger: Trigger) -> TriggerResult:
        """評估是否有最新符合條件的文章或影片。

        Args:
            trigger: 欲評估的 Trigger 實例。

        Returns:
            TriggerResult: 判定結果與最新內容摘要。
        """
        cond = trigger.condition
        feed_url = str(cond.get("feed_url", "")).strip()
        if not feed_url:
            return TriggerResult(should_fire=False, current_value="未指定訂閱網址 (feed_url)")

        keyword = str(cond.get("keyword", "")).strip().lower()
        last_item_id = str(cond.get("last_item_id", "")).strip()

        items = await self._client.fetch_latest_items(feed_url, limit=5)
        if not items:
            return TriggerResult(
                should_fire=False,
                current_value=f"無法獲取或解析訂閱內容: {feed_url}",
            )

        # 若有指定關鍵字，先進行過濾
        matched_items: list[RssItem] = []
        for it in items:
            if not keyword or (keyword in it.title.lower() or keyword in it.summary.lower()):
                matched_items.append(it)

        if not matched_items:
            return TriggerResult(
                should_fire=False,
                current_value=f"近期無包含關鍵字 '{keyword}' 的新文章",
            )

        latest_item = matched_items[0]

        # 首次建立或初次運行
        if not last_item_id:
            return TriggerResult(
                should_fire=True,
                current_value=f"【{latest_item.title}】\n{latest_item.url}",
                detail_data={
                    "item_id": latest_item.item_id,
                    "title": latest_item.title,
                    "url": latest_item.url,
                    "published_at": latest_item.published_at,
                    "last_item_id": latest_item.item_id,
                },
            )

        # 比對最新項目 ID
        if latest_item.item_id != last_item_id:
            return TriggerResult(
                should_fire=True,
                current_value=f"📡 發現新內容！\n【{latest_item.title}】\n{latest_item.url}",
                detail_data={
                    "item_id": latest_item.item_id,
                    "title": latest_item.title,
                    "url": latest_item.url,
                    "published_at": latest_item.published_at,
                    "last_item_id": latest_item.item_id,
                },
            )

        return TriggerResult(
            should_fire=False,
            current_value=f"尚無新內容發布（最新：{latest_item.title[:20]}...）",
        )
