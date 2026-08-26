"""mod/clients/booru_client.py
==============================
External Client for Anime Booru image providers (Safebooru and Danbooru).

Encapsulates all external HTTP communications, JSON deserialization,
timeouts, retries, and multi-booru fallback routing.
"""

from __future__ import annotations

import asyncio
import logging
import random
import urllib.parse
from dataclasses import dataclass
from typing import Final

import aiohttp

import settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SEC: Final[int] = 10
MAX_RETRIES: Final[int] = 2


def _safe_int(val: object, default: int = 0) -> int:
    """Safely cast a value to integer, handling None and invalid types.

    Args:
        val: The value to convert.
        default: Default integer fallback.

    Returns:
        Converted integer or default.
    """
    if val is None:
        return default
    try:
        return int(str(val))
    except (ValueError, TypeError):
        return default


@dataclass(frozen=True)
class BooruSearchResult:
    """Immutable result object from a Booru image query.

    Attributes:
        image_url: Direct URL pointing to the full/large resolution image.
        source_site: Name of the Booru engine ("safebooru" | "danbooru").
        post_id: Unique integer ID of the image post.
        score: Image rating score on the platform.
        tags_used: Tuple of query tags actually sent to the API.
        rating: Content rating string ("g", "s", "q", "e").
    """

    image_url: str
    source_site: str
    post_id: int = 0
    score: int = 0
    tags_used: tuple[str, ...] = ()
    rating: str = "s"


class BooruClient:
    """Async HTTP Client for querying Safebooru and Danbooru endpoints."""

    def __init__(
        self,
        safebooru_api_url: str | None = None,
        safebooru_image_base: str | None = None,
        danbooru_posts_api_url: str | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self._safebooru_api_url = (
            safebooru_api_url
            or getattr(settings, "SAFEBOORU_API_URL", "https://safebooru.org/index.php")
        )
        self._safebooru_image_base = (
            safebooru_image_base
            or getattr(settings, "SAFEBOORU_IMAGE_BASE_URL", "https://safebooru.org/images/")
        )
        self._danbooru_posts_api_url = (
            danbooru_posts_api_url
            or getattr(settings, "DANBOORU_API_URL", "https://danbooru.donmai.us/posts.json")
        )
        self._timeout_seconds = timeout_seconds
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """取得或惰性初始化共用的 aiohttp.ClientSession 連線池。"""
        if self._session is None or self._session.closed:
            headers = {"User-Agent": "SherryCompanion/3.0 (Anime Bot)"}
            self._session = aiohttp.ClientSession(
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self._timeout_seconds),
            )
        return self._session

    async def close(self) -> None:
        """關閉底層 ClientSession 連線池。"""
        if self._session and not self._session.closed:
            await self._session.close()

    async def search_safebooru(
        self,
        tags: list[str],
        limit: int = 40,
        page_offsets: list[int] | None = None,
    ) -> BooruSearchResult | None:
        """Search Safebooru with given tags and multi-page jitter.

        Args:
            tags: List of tag strings to search.
            limit: Number of items to query per page.
            page_offsets: List of page PID offsets to try.

        Returns:
            BooruSearchResult if a valid image is found, None otherwise.
        """
        if not tags:
            return None

        clean_tags = [t.replace(" ", "_") for t in tags if t.strip()]
        encoded_tags = urllib.parse.quote_plus(" ".join(clean_tags), safe="+")
        pids = page_offsets if page_offsets is not None else [random.randint(0, 3), 0]
        session = await self._get_session()

        for pid in pids:
            api_url = (
                f"{self._safebooru_api_url}?page=dapi&s=post&q=index&json=1"
                f"&limit={limit}&pid={pid}&tags={encoded_tags}"
            )
            for attempt in range(MAX_RETRIES):
                try:
                    async with session.get(api_url) as resp:
                        if resp.status == 200:
                            posts = await resp.json(content_type=None)
                            if posts and isinstance(posts, list):
                                shuffled = list(posts)
                                random.shuffle(shuffled)
                                for post in shuffled:
                                    if (
                                        isinstance(post, dict)
                                        and "directory" in post
                                        and "image" in post
                                    ):
                                        img_url = (
                                            f"{self._safebooru_image_base}"
                                            f"{post['directory']}/{post['image']}"
                                        )
                                        post_id = _safe_int(post.get("id"))
                                        score = _safe_int(post.get("score"))
                                        return BooruSearchResult(
                                            image_url=img_url,
                                            source_site="safebooru",
                                            post_id=post_id,
                                            score=score,
                                            tags_used=tuple(tags),
                                            rating="s",
                                        )
                        elif resp.status in (429, 500, 502, 503):
                            await asyncio.sleep(0.5 * (attempt + 1))
                            continue
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    logger.debug("Safebooru request failed (attempt %d): %s", attempt + 1, exc)
                    if attempt < MAX_RETRIES - 1:
                        await asyncio.sleep(0.5)

        return None

    async def search_danbooru(
        self,
        tags: list[str],
        is_nsfw: bool = False,
        limit: int = 50,
        page_offsets: list[int] | None = None,
    ) -> BooruSearchResult | None:
        """Search Danbooru with given tags and SFW safety filters.

        Args:
            tags: List of Danbooru tag strings.
            is_nsfw: Whether NSFW content is permitted. If False, rating:general is enforced.
            limit: Number of items to query per page.
            page_offsets: List of page numbers to search (1-indexed).

        Returns:
            BooruSearchResult if a valid image is found, None otherwise.
        """
        if not tags:
            return None

        # Build query tags: enforce SFW rating if not explicitly nsfw
        query_tags = list(tags)
        if not is_nsfw:
            if not any(t.startswith("rating:") for t in query_tags):
                query_tags.insert(0, "rating:g")

        encoded_tags = urllib.parse.quote_plus(" ".join(query_tags), safe="+")
        pages = page_offsets if page_offsets is not None else [random.randint(1, 3), 1]
        session = await self._get_session()

        for page in pages:
            api_url = (
                f"{self._danbooru_posts_api_url}?"
                f"limit={limit}&page={page}&tags={encoded_tags}"
            )
            for attempt in range(MAX_RETRIES):
                try:
                    async with session.get(api_url) as resp:
                        if resp.status == 200:
                            posts = await resp.json(content_type=None)
                            if posts and isinstance(posts, list):
                                shuffled = list(posts)
                                random.shuffle(shuffled)
                                for post in shuffled:
                                    if not isinstance(post, dict):
                                        continue
                                    # Prefer large_file_url or file_url
                                    img_url = post.get("large_file_url") or post.get("file_url") or ""
                                    if img_url and isinstance(img_url, str) and img_url.startswith("http"):
                                        post_id = _safe_int(post.get("id"))
                                        score = _safe_int(post.get("score"))
                                        rating = str(post.get("rating", "s"))
                                        return BooruSearchResult(
                                            image_url=img_url,
                                            source_site="danbooru",
                                            post_id=post_id,
                                            score=score,
                                            tags_used=tuple(tags),
                                            rating=rating,
                                        )
                        elif resp.status in (429, 500, 502, 503):
                            await asyncio.sleep(0.5 * (attempt + 1))
                            continue
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    logger.debug("Danbooru request failed (attempt %d): %s", attempt + 1, exc)
                    if attempt < MAX_RETRIES - 1:
                        await asyncio.sleep(0.5)

        return None

    async def search_federated(
        self,
        tags: list[str],
        is_nsfw: bool = False,
    ) -> tuple[str, str]:
        """Multi-booru federated search with Safebooru primary and Danbooru fallback.

        Args:
            tags: List of query tags.
            is_nsfw: Whether NSFW content is allowed.

        Returns:
            Tuple of (image_url, source_site). If not found, returns ("", "").
        """
        # Primary: Safebooru (Fast & Safe)
        res = await self.search_safebooru(tags)
        if res and res.image_url:
            return res.image_url, res.source_site

        # Fallback: Danbooru (SFW or full based on is_nsfw)
        res_dan = await self.search_danbooru(tags, is_nsfw=is_nsfw)
        if res_dan and res_dan.image_url:
            return res_dan.image_url, res_dan.source_site

        return "", ""
