"""
test_market_cog.py  (tests/unit)
================================
Unit tests for MarketCog scheduler and channel fetching utilities (AAA pattern).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord.ext import commands

from mod.market_plugin.cog import TAIWAN_TZ, MarketCog
from mod.market_plugin.utils import get_or_fetch_channel


def test_get_or_fetch_channel_cache_hit() -> None:
    """Test get_or_fetch_channel returns channel from local cache when present."""
    # Arrange
    bot = MagicMock(spec=commands.Bot)
    mock_channel = MagicMock(spec=discord.TextChannel)
    bot.get_channel.return_value = mock_channel
    bot.fetch_channel = AsyncMock()

    # Act
    result = asyncio.run(get_or_fetch_channel(bot, 123456))

    # Assert
    assert result is mock_channel
    bot.get_channel.assert_called_once_with(123456)
    bot.fetch_channel.assert_not_called()


def test_get_or_fetch_channel_cache_miss_fetch_success() -> None:
    """Test get_or_fetch_channel falls back to fetch_channel when get_channel is None."""
    # Arrange
    bot = MagicMock(spec=commands.Bot)
    bot.get_channel.return_value = None
    mock_channel = MagicMock(spec=discord.TextChannel)
    bot.fetch_channel = AsyncMock(return_value=mock_channel)

    # Act
    result = asyncio.run(get_or_fetch_channel(bot, 654321))

    # Assert
    assert result is mock_channel
    bot.get_channel.assert_called_once_with(654321)
    bot.fetch_channel.assert_called_once_with(654321)


def test_get_or_fetch_channel_fetch_error_returns_none() -> None:
    """Test get_or_fetch_channel returns None when fetch_channel raises Exception."""
    # Arrange
    bot = MagicMock(spec=commands.Bot)
    bot.get_channel.return_value = None
    bot.fetch_channel = AsyncMock(side_effect=Exception("Discord API error"))

    # Act
    result = asyncio.run(get_or_fetch_channel(bot, 999999))

    # Assert
    assert result is None
    bot.get_channel.assert_called_once_with(999999)
    bot.fetch_channel.assert_called_once_with(999999)


def test_market_cog_init() -> None:
    """Test MarketCog initializes its dependencies and binds watchlist loader to bot."""
    # Arrange
    bot = MagicMock(spec=commands.Bot)

    # Act
    cog = MarketCog(bot)

    # Assert
    assert cog.bot is bot
    assert hasattr(bot, "_market_watchlists")
    assert cog._last_triggered == {}
    assert cog._last_etf_trigger_minute is None


def test_market_cog_schedule_morning_verify_trigger() -> None:
    """Test _market_schedule_loop triggers morning verify at 07:00 and prevents duplicates."""
    # Arrange
    bot = MagicMock(spec=commands.Bot)
    bot.wait_until_ready = AsyncMock()
    bot.loop = MagicMock(spec=asyncio.AbstractEventLoop)
    cog = MarketCog(bot)

    test_dt = datetime(2026, 8, 1, 7, 0, 0, tzinfo=TAIWAN_TZ)

    # Act 1: First trigger at 07:00
    with (
        patch.object(cog, "_task_morning_verify", new_callable=AsyncMock),
        patch("mod.market_plugin.cog.datetime") as mock_dt,
    ):
        mock_dt.now.return_value = test_dt
        asyncio.run(cog._market_schedule_loop.coro(cog))

    # Assert 1
    assert cog._last_triggered.get("morning_verify") == "2026-08-01"
    assert bot.loop.create_task.call_count == 1

    # Act 2: Second run on the same day at 07:00
    bot.loop.create_task.reset_mock()
    with (
        patch.object(cog, "_task_morning_verify", new_callable=AsyncMock),
        patch("mod.market_plugin.cog.datetime") as mock_dt,
    ):
        mock_dt.now.return_value = test_dt
        asyncio.run(cog._market_schedule_loop.coro(cog))

    # Assert 2: Should not trigger again
    assert bot.loop.create_task.call_count == 0


def test_market_cog_schedule_etf_monitor_trigger() -> None:
    """Test _market_schedule_loop triggers ETF monitor during Taiwan stock market open."""
    # Arrange
    bot = MagicMock(spec=commands.Bot)
    bot.wait_until_ready = AsyncMock()
    bot.loop = MagicMock(spec=asyncio.AbstractEventLoop)
    cog = MarketCog(bot)

    test_dt = datetime(2026, 8, 1, 10, 20, 0, tzinfo=TAIWAN_TZ)

    # Act 1: First check at 10:20 (divisible by 10)
    with (
        patch.object(cog, "_task_etf_monitor", new_callable=AsyncMock),
        patch("mod.market_plugin.cog.datetime") as mock_dt,
    ):
        mock_dt.now.return_value = test_dt
        asyncio.run(cog._market_schedule_loop.coro(cog))

    # Assert 1
    assert cog._last_etf_trigger_minute == 20
    assert bot.loop.create_task.call_count == 1

    # Act 2: Second check in the same minute (10:20)
    bot.loop.create_task.reset_mock()
    with (
        patch.object(cog, "_task_etf_monitor", new_callable=AsyncMock),
        patch("mod.market_plugin.cog.datetime") as mock_dt,
    ):
        mock_dt.now.return_value = test_dt
        asyncio.run(cog._market_schedule_loop.coro(cog))

    # Assert 2: Should not trigger again in the same minute
    assert bot.loop.create_task.call_count == 0
