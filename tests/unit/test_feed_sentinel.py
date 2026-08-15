"""
tests/unit/test_feed_sentinel.py
================================
AI 社群情報哨兵模組 (mod.feed_sentinel) 單元測試。
"""

import asyncio
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from discord.ext import commands

# 確保專案根目錄在 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from mod.feed_sentinel import config
from mod.feed_sentinel.models import FeedItem
from mod.feed_sentinel.cog import FeedSentinelCog, _load_seen_ids, _save_seen_ids
from mod.feed_sentinel.fetcher import (
    fetch_huggingface_trending,
    fetch_arxiv_ai_papers,
    fetch_github_ai_trending,
)

TAIWAN_TZ = timezone(timedelta(hours=8))


def test_feed_sentinel_config_defaults():
    """測試情報哨兵模組預設設定值。"""
    assert config.ENABLED is True
    assert config.START_HOUR == 8
    assert config.END_HOUR == 23
    assert config.MIN_GAP_SECONDS == 3600
    assert config.TARGET_CHANNEL_ID == 1519967448982487201
    assert config.DEFAULT_TIMEOUT_SEC == 8


def test_feed_item_model():
    """測試 FeedItem 資料類別與字典轉換。"""
    item = FeedItem(
        item_id="hf_test_123",
        source="HuggingFace",
        title="Test Model",
        url="https://huggingface.co/test/model",
        summary="This is a test AI model summary.",
        author_or_repo="TestAuthor",
        tags=["NLP", "PyTorch"],
        published_at="2026-08-15",
    )

    assert item.item_id == "hf_test_123"
    assert item.source == "HuggingFace"
    assert len(item.tags) == 2

    d = item.to_dict()
    assert d["item_id"] == "hf_test_123"
    assert d["author_or_repo"] == "TestAuthor"
    assert "NLP" in d["tags"]


def test_seen_ids_persistence(tmp_path: Path):
    """測試已推播情報 ID 快取的持久化讀寫。"""
    test_seen_file = tmp_path / "test_seen.json"
    with patch.object(config, "SEEN_PATH", test_seen_file):
        # 初始應為空集合
        seen = _load_seen_ids()
        assert len(seen) == 0

        # 寫入測試資料
        test_ids = {"hf_1", "arxiv_2", "github_3"}
        _save_seen_ids(test_ids)

        # 重新讀取
        loaded = _load_seen_ids()
        assert loaded == test_ids


def test_fetch_huggingface_trending_mock():
    """測試 HuggingFace 熱門模型與論文 mock 爬取解析。"""
    async def _run():
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value=[
                {
                    "paper": {
                        "id": "2608.12345",
                        "title": "Sherry Attention: Efficient Reasoning",
                        "summary": "Novel architecture for low-latency reasoning.",
                    }
                }
            ]
        )
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_resp
        mock_session = MagicMock()
        mock_session.get.return_value = mock_cm

        items = await fetch_huggingface_trending(mock_session)
        assert len(items) >= 1
        assert items[0].item_id == "hf_paper_2608.12345"
        assert "Sherry Attention" in items[0].title
        assert "2608.12345" in items[0].url

    asyncio.run(_run())


def test_fetch_arxiv_mock():
    """測試 arXiv XML 回應解析。"""
    async def _run():
        sample_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <id>http://arxiv.org/abs/2608.99999v1</id>
            <title>Deep Reasoning in Multi-Agent Systems</title>
            <summary>We propose a new framework for agentic workflows.</summary>
            <published>2026-08-15T00:00:00Z</published>
            <author><name>Alice</name></author>
            <author><name>Bob</name></author>
          </entry>
        </feed>
        """
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value=sample_xml)
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_resp
        mock_session = MagicMock()
        mock_session.get.return_value = mock_cm

        items = await fetch_arxiv_ai_papers(mock_session)
        assert len(items) == 1
        assert items[0].item_id == "arxiv_2608.99999v1"
        assert items[0].title == "Deep Reasoning in Multi-Agent Systems"
        assert "Alice, Bob" in items[0].author_or_repo

    asyncio.run(_run())


def test_feed_sentinel_cog_schedule_window_and_cooldown():
    """測試 Sentinel 背景輪詢的時段檢查與冷卻邏輯。"""
    async def _run():
        bot = MagicMock(spec=commands.Bot)
        bot.wait_until_ready = AsyncMock()
        cog = FeedSentinelCog(bot)

        # 1. 測試時段外（如凌晨 03:00）：應略過
        out_of_hours_dt = datetime(2026, 8, 15, 3, 0, 0, tzinfo=TAIWAN_TZ)
        with (
            patch("mod.feed_sentinel.cog.datetime") as mock_dt,
            patch.object(cog, "_process_feed_and_post", new_callable=AsyncMock) as mock_process,
        ):
            mock_dt.now.return_value = out_of_hours_dt
            await cog._sentinel_loop.coro(cog)
            mock_process.assert_not_called()

        # 2. 測試時段內（如上午 10:00），且距離上次發送 < 3600 秒：應略過
        in_hours_dt = datetime(2026, 8, 15, 10, 0, 0, tzinfo=TAIWAN_TZ)
        cog._last_post_time = 10000.0
        with (
            patch("mod.feed_sentinel.cog.datetime") as mock_dt,
            patch("mod.feed_sentinel.cog.time.time", return_value=10000.0 + 1800.0),  # 僅經過 30 分鐘
            patch.object(cog, "_process_feed_and_post", new_callable=AsyncMock) as mock_process,
        ):
            mock_dt.now.return_value = in_hours_dt
            await cog._sentinel_loop.coro(cog)
            mock_process.assert_not_called()

        # 3. 測試時段內（上午 10:00），且距離上次發送 >= 3600 秒：應觸發處理
        with (
            patch("mod.feed_sentinel.cog.datetime") as mock_dt,
            patch("mod.feed_sentinel.cog.time.time", return_value=10000.0 + 3650.0),  # 經過 > 1 小時
            patch.object(cog, "_process_feed_and_post", new_callable=AsyncMock) as mock_process,
        ):
            mock_dt.now.return_value = in_hours_dt
            await cog._sentinel_loop.coro(cog)
            mock_process.assert_called_once()

    asyncio.run(_run())


def test_feed_sentinel_process_and_post(tmp_path: Path):
    """測試情報抓取、AI 銳評生成與 Discord Embed 發送閉環。"""
    async def _run():
        bot = MagicMock(spec=commands.Bot)
        mock_channel = MagicMock(spec=discord.TextChannel)
        mock_channel.send = AsyncMock()
        bot.get_channel.return_value = mock_channel

        test_item = FeedItem(
            item_id="test_post_001",
            source="HuggingFace",
            title="Gemma 3 Open Weights",
            url="https://huggingface.co/google/gemma-3",
            summary="New SOTA open weights model released.",
        )

        cog = FeedSentinelCog(bot)
        cog._seen_ids = set()

        with (
            patch("mod.feed_sentinel.cog.fetch_all_feeds", new_callable=AsyncMock, return_value=[test_item]),
            patch.object(cog, "_generate_sherry_briefing", new_callable=AsyncMock, return_value="💡 **【重點】**\n1. 效能大幅提升\n\n🕵️‍♀️ **【雪莉銳評】**\n這模型確實有點料！"),
            patch.object(config, "SEEN_PATH", tmp_path / "seen.json"),
        ):
            success = await cog._process_feed_and_post()
            assert success is True
            assert "test_post_001" in cog._seen_ids
            mock_channel.send.assert_called_once()
            sent_embed = mock_channel.send.call_args.kwargs.get("embed")
            assert sent_embed is not None
            assert "Gemma 3" in sent_embed.title
            assert "雪莉銳評" in sent_embed.description

    asyncio.run(_run())
