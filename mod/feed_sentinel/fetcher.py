"""
fetcher.py  (mod/feed_sentinel)
===============================
多來源情報爬取與解析器 (HuggingFace / arXiv / GitHub)。
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any

import aiohttp

from . import config
from .models import FeedItem

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Sherry-AI-Companion-Platform/1.5 (FeedSentinel; contact: discord-bot)",
    "Accept": "application/json, application/xml, text/xml, */*",
}


async def fetch_huggingface_trending(session: aiohttp.ClientSession) -> list[FeedItem]:
    """獲取 HuggingFace 最新熱門模型與 Daily Papers。"""
    items: list[FeedItem] = []

    # 1. 嘗試抓取 Daily Papers API
    url_papers = "https://huggingface.co/api/daily_papers"
    try:
        async with session.get(url_papers, headers=_HEADERS) as resp:
            if resp.status == 200:
                data = await resp.json()
                if isinstance(data, list):
                    for paper in data[:5]:
                        paper_info = paper.get("paper", {})
                        paper_id = paper_info.get("id") or paper.get("id", "")
                        title = paper_info.get("title") or paper.get("title", "")
                        summary = paper_info.get("summary") or paper.get("summary", "")
                        if paper_id and title:
                            items.append(
                                FeedItem(
                                    item_id=f"hf_paper_{paper_id}",
                                    source="HuggingFace Daily Papers",
                                    title=title.strip(),
                                    url=f"https://huggingface.co/papers/{paper_id}",
                                    summary=summary.strip()[:1000],
                                    author_or_repo="HuggingFace Community",
                                    tags=["AI Paper", "HuggingFace"],
                                )
                            )
    except Exception as exc:
        logger.warning("[FeedSentinel] HuggingFace Daily Papers 抓取失敗: %s", exc)

    # 2. 嘗試抓取 Trending Models
    url_models = "https://huggingface.co/api/models?sort=likes7d&direction=-1&limit=5"
    try:
        async with session.get(url_models, headers=_HEADERS) as resp:
            if resp.status == 200:
                models = await resp.json()
                if isinstance(models, list):
                    for m in models[:5]:
                        model_id = m.get("id", "")
                        pipeline_tag = m.get("pipeline_tag", "AI Model")
                        likes = m.get("likes", 0)
                        downloads = m.get("downloads", 0)
                        if model_id:
                            items.append(
                                FeedItem(
                                    item_id=f"hf_model_{model_id}",
                                    source="HuggingFace Trending Model",
                                    title=f"{model_id} ({pipeline_tag})",
                                    url=f"https://huggingface.co/{model_id}",
                                    summary=f"熱門開源模型，近7日獲得 {likes} 個讚，下載量 {downloads} 次。主要任務類型：{pipeline_tag}。",
                                    author_or_repo=model_id.split("/")[0] if "/" in model_id else "Community",
                                    tags=[pipeline_tag, "HuggingFace Model"],
                                )
                            )
    except Exception as exc:
        logger.warning("[FeedSentinel] HuggingFace Trending Models 抓取失敗: %s", exc)

    return items


async def fetch_arxiv_ai_papers(session: aiohttp.ClientSession) -> list[FeedItem]:
    """獲取 arXiv 最新 AI / Machine Learning (cs.AI, cs.CL, cs.CV) 論文。"""
    items: list[FeedItem] = []
    url = (
        "https://export.arxiv.org/api/query?"
        "search_query=cat:cs.AI+OR+cat:cs.CL+OR+cat:cs.CV"
        "&sortBy=submittedDate&sortOrder=descending&max_results=5"
    )

    try:
        async with session.get(url, headers=_HEADERS) as resp:
            if resp.status == 200:
                xml_text = await resp.text()
                root = ET.fromstring(xml_text)
                # Atom namespace
                ns = {"atom": "http://www.w3.org/2005/Atom"}
                for entry in root.findall("atom:entry", ns):
                    entry_id = entry.findtext("atom:id", default="", namespaces=ns).strip()
                    title = entry.findtext("atom:title", default="", namespaces=ns).strip()
                    summary = entry.findtext("atom:summary", default="", namespaces=ns).strip()
                    published = entry.findtext("atom:published", default="", namespaces=ns).strip()

                    # Authors
                    authors: list[str] = []
                    for author_elem in entry.findall("atom:author", ns):
                        name = author_elem.findtext("atom:name", default="", namespaces=ns).strip()
                        if name:
                            authors.append(name)
                    author_str = ", ".join(authors[:3]) if authors else "arXiv"

                    # Clean whitespace
                    title = " ".join(title.split())
                    summary = " ".join(summary.split())

                    arxiv_id = entry_id.split("/abs/")[-1] if "/abs/" in entry_id else entry_id

                    if title and entry_id:
                        items.append(
                            FeedItem(
                                item_id=f"arxiv_{arxiv_id}",
                                source="arXiv AI/CS Research",
                                title=title,
                                url=f"https://arxiv.org/abs/{arxiv_id}",
                                summary=summary[:1000],
                                author_or_repo=author_str,
                                tags=["arXiv", "Research Paper"],
                                published_at=published[:10],
                            )
                        )
    except Exception as exc:
        logger.warning("[FeedSentinel] arXiv 論文抓取失敗: %s", exc)

    return items


async def fetch_github_ai_trending(session: aiohttp.ClientSession) -> list[FeedItem]:
    """獲取 GitHub 最新熱門 AI / LLM 開源專案。"""
    items: list[FeedItem] = []
    url = (
        "https://api.github.com/search/repositories?"
        "q=topic:llm+OR+topic:generative-ai+stars:>100"
        "&sort=updated&order=desc&per_page=5"
    )

    try:
        async with session.get(url, headers=_HEADERS) as resp:
            if resp.status == 200:
                data = await resp.json()
                for repo in data.get("items", [])[:5]:
                    full_name = repo.get("full_name", "")
                    html_url = repo.get("html_url", "")
                    description = repo.get("description", "") or "No description provided."
                    stars = repo.get("stargazers_count", 0)
                    language = repo.get("language", "Unknown")
                    topics = repo.get("topics", [])

                    if full_name and html_url:
                        items.append(
                            FeedItem(
                                item_id=f"github_{full_name}",
                                source="GitHub AI Trending",
                                title=f"{full_name} (⭐ {stars:,})",
                                url=html_url,
                                summary=f"{description} (語言: {language}, Topics: {', '.join(topics[:4])})",
                                author_or_repo=full_name.split("/")[0] if "/" in full_name else "GitHub",
                                tags=["GitHub", language] + topics[:2],
                            )
                        )
    except Exception as exc:
        logger.warning("[FeedSentinel] GitHub AI Trending 抓取失敗: %s", exc)

    return items


async def fetch_all_feeds() -> list[FeedItem]:
    """並行抓取所有情報源並彙整結果清單。"""
    timeout = aiohttp.ClientTimeout(total=config.DEFAULT_TIMEOUT_SEC)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        import asyncio

        results = await asyncio.gather(
            fetch_huggingface_trending(session),
            fetch_arxiv_ai_papers(session),
            fetch_github_ai_trending(session),
            return_exceptions=True,
        )

    all_items: list[FeedItem] = []
    for res in results:
        if isinstance(res, list):
            all_items.extend(res)
        elif isinstance(res, Exception):
            logger.warning("[FeedSentinel] 情報源抓取異常: %s", res)

    return all_items
