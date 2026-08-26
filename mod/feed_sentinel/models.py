"""
models.py  (mod/feed_sentinel)
==============================
AI 社群情報哨兵之資料模型與多維正規化特徵提取定義。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, TypedDict
from urllib.parse import urlparse


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


class SharedPostDict(TypedDict, total=False):
    """已推播情報文章字典序列化結構。"""

    post_id: str
    source: str
    title: str
    url: str
    original_summary: str
    sherry_analysis: str
    author_or_repo: str
    tags: list[str]
    published_at: str
    posted_at: str
    canonical_keys: list[str]


@dataclass(frozen=True)
class SharedPost:
    """已成功推播至 Discord 並持久化封存之情報文章模型。"""

    post_id: str
    source: str
    title: str
    url: str
    original_summary: str
    sherry_analysis: str
    author_or_repo: str = ""
    tags: list[str] = field(default_factory=list)
    published_at: str = ""
    posted_at: str = ""
    canonical_keys: list[str] = field(default_factory=list)

    def to_dict(self) -> SharedPostDict:
        """轉換為字典格式供 JSON 序列化與 Web API 輸出。"""
        return {
            "post_id": self.post_id,
            "source": self.source,
            "title": self.title,
            "url": self.url,
            "original_summary": self.original_summary,
            "sherry_analysis": self.sherry_analysis,
            "author_or_repo": self.author_or_repo,
            "tags": list(self.tags),
            "published_at": self.published_at,
            "posted_at": self.posted_at,
            "canonical_keys": list(self.canonical_keys),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SharedPost:
        """從字典反序列化為 SharedPost 實例。"""
        return cls(
            post_id=str(data.get("post_id") or data.get("item_id") or ""),
            source=str(data.get("source") or ""),
            title=str(data.get("title") or ""),
            url=str(data.get("url") or ""),
            original_summary=str(data.get("original_summary") or data.get("summary") or ""),
            sherry_analysis=str(data.get("sherry_analysis") or ""),
            author_or_repo=str(data.get("author_or_repo") or ""),
            tags=list(data.get("tags") or []),
            published_at=str(data.get("published_at") or ""),
            posted_at=str(data.get("posted_at") or ""),
            canonical_keys=list(data.get("canonical_keys") or []),
        )


# ---------------------------------------------------------------------------
# 多維特徵正規化與指紋提取工具函式 (Multi-Key Canonical Deduplication)
# ---------------------------------------------------------------------------

_ARXIV_PATTERN = re.compile(r"(\d{4}\.\d{4,5})(?:v\d+)?", re.IGNORECASE)
_PUNCT_WHITESPACE_PATTERN = re.compile(r"[\s\W_]+", re.UNICODE)


def normalize_url(url: str) -> str:
    """將 URL 去除協議、查詢參數、锚點與結尾斜線，轉為標準化路徑。

    Args:
        url: 原始 URL 字串。

    Returns:
        正規化後的 URL 指紋字串。
    """
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip())
        netloc = parsed.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        path = parsed.path.rstrip("/")
        return f"{netloc}{path}".lower()
    except Exception:
        return url.strip().lower().rstrip("/")


def normalize_title_fingerprint(title: str) -> str:
    """將文章標題去除標點符號、所有空白與特殊字元並小寫化，生成標題指紋。

    Args:
        title: 文章標題字串。

    Returns:
        純字母數字與 CJK 字元的緊湊指紋字串。
    """
    if not title:
        return ""
    clean = _PUNCT_WHITESPACE_PATTERN.sub("", title).lower()
    return clean


def extract_arxiv_id(text_or_url: str) -> str | None:
    """從 URL 或文字中提取 arXiv 基礎識別碼 (忽略 v1, v2 等版本號)。

    Args:
        text_or_url: 包含 arXiv 識別碼的 URL 或文字。

    Returns:
        標準 arXiv ID (例如 '2408.12345') 或 None。
    """
    match = _ARXIV_PATTERN.search(text_or_url)
    if match:
        return match.group(1)
    return None


def extract_canonical_keys(item: FeedItem | SharedPost | dict[str, Any]) -> list[str]:
    """為情報項目生成全方位多維唯一指紋鍵值集合。

    包含：
    1. 原始識別碼 (`raw:{id}`)
    2. 正規化 URL 指紋 (`url:{normalized_url}`)
    3. 標題語義指紋 (`title:{normalized_title}`)
    4. arXiv 識別碼 (`arxiv:{arxiv_id}`) (若可解析)
    5. GitHub 倉庫標識 (`github:{owner/repo}`) (若適用)
    6. HuggingFace 模型標識 (`hf:{model_id}`) (若適用)

    Args:
        item: FeedItem 或 SharedPost 實例或字典。

    Returns:
        不重複的指紋鍵值清單。
    """
    keys: set[str] = set()

    if isinstance(item, dict):
        item_id = str(item.get("post_id") or item.get("item_id") or "")
        title = str(item.get("title") or "")
        url = str(item.get("url") or "")
        source = str(item.get("source") or "")
    elif isinstance(item, (FeedItem, SharedPost)):
        item_id = item.post_id if isinstance(item, SharedPost) else item.item_id
        title = item.title
        url = item.url
        source = item.source
    else:
        return []

    # 1. 原始 ID
    if item_id:
        keys.add(f"raw:{item_id.strip()}")

    # 2. 正規化 URL
    norm_url = normalize_url(url)
    if norm_url:
        keys.add(f"url:{norm_url}")

    # 3. 標題指紋
    title_fp = normalize_title_fingerprint(title)
    if title_fp and len(title_fp) >= 4:
        keys.add(f"title:{title_fp}")

    # 4. arXiv ID (從 URL 或 ID 或標題解析)
    arxiv_id = extract_arxiv_id(url) or extract_arxiv_id(item_id)
    if arxiv_id:
        keys.add(f"arxiv:{arxiv_id}")

    # 5. GitHub 倉庫解析
    if "github.com" in url.lower():
        norm_gh = norm_url.replace("github.com/", "")
        parts = norm_gh.split("/")
        if len(parts) >= 2:
            repo_slug = f"{parts[0]}/{parts[1]}".lower()
            keys.add(f"github:{repo_slug}")

    # 6. HuggingFace 解析
    if "huggingface.co" in url.lower():
        if "/papers/" in url.lower():
            paper_id = extract_arxiv_id(url)
            if paper_id:
                keys.add(f"arxiv:{paper_id}")
        else:
            norm_hf = norm_url.replace("huggingface.co/", "")
            if norm_hf:
                keys.add(f"hf:{norm_hf}")

    return sorted(keys)
