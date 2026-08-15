"""
models.py  (mod/feed_sentinel)
==============================
AI 社群情報哨兵之資料模型定義。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypedDict


class FeedItemDict(TypedDict, total=False):
    """FeedItem 字典序列化結構。"""

    item_id: str
    source: str
    title: str
    url: str
    summary: str
    author_or_repo: str
    tags: list[str]
    published_at: str


@dataclass(frozen=True)
class FeedItem:
    """單一情報項目不可變資料類別。"""

    item_id: str
    source: str
    title: str
    url: str
    summary: str
    author_or_repo: str = ""
    tags: list[str] = field(default_factory=list)
    published_at: str = ""

    def to_dict(self) -> FeedItemDict:
        """轉換為字典格式。"""
        return {
            "item_id": self.item_id,
            "source": self.source,
            "title": self.title,
            "url": self.url,
            "summary": self.summary,
            "author_or_repo": self.author_or_repo,
            "tags": list(self.tags),
            "published_at": self.published_at,
        }
