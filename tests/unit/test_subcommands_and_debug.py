"""
test_subcommands_and_debug.py  (tests/unit)
===========================================
Unit tests verifying Discord Subcommand Groups and Debug Detail card rendering.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord.ext import commands
import pytest

import settings
from handlers.admin.cog import AdminCog
from handlers.help_cog import HelpCog
from handlers.social.utils import simulate_typing_and_send
from mod.dnd.cog import DndCog
from mod.market_plugin.cog import MarketCog


def test_choice_commands_registration() -> None:
    """Verify that unified Choice commands are properly defined on Cogs."""
    # Arrange & Act
    bot = MagicMock(spec=commands.Bot)
    admin_cog = AdminCog(bot)
    market_cog = MarketCog(bot)
    dnd_cog = DndCog(bot)
    help_cog = HelpCog(bot)

    # Assert AdminCog Choice Commands
    assert hasattr(admin_cog, "vibe_command")
    assert admin_cog.vibe_command.name == "vibe"
    assert hasattr(admin_cog, "memory_command")
    assert admin_cog.memory_command.name == "memory"
    assert hasattr(admin_cog, "admin_command")
    assert admin_cog.admin_command.name == "admin"
    assert hasattr(admin_cog, "debug_command")
    assert admin_cog.debug_command.name == "debug"

    # Assert MarketCog Choice Commands
    assert hasattr(market_cog, "slash_market")
    assert market_cog.slash_market.name == "market"
    assert hasattr(market_cog, "slash_card")
    assert market_cog.slash_card.name == "card"

    # Assert DndCog Group
    assert hasattr(dnd_cog, "dnd_group")
    assert dnd_cog.dnd_group.name == "dnd"

    # Assert HelpCog exists
    assert help_cog.bot is bot


def test_debug_detail_card_rendering_enabled() -> None:
    """Verify simulate_typing_and_send formats as a card when DEBUG_DETAIL is True."""
    # Arrange
    target_msg = MagicMock(spec=discord.Message)
    target_msg.reply = AsyncMock()
    target_msg.channel = MagicMock(spec=discord.TextChannel)
    target_msg.channel.typing = MagicMock()
    target_msg.channel.typing.return_value.__aenter__ = AsyncMock()
    target_msg.channel.typing.return_value.__aexit__ = AsyncMock()
    target_msg.add_reaction = AsyncMock()

    # Act
    async def _run() -> None:
        with patch.object(settings, "DEBUG_DETAIL", True):
            with patch.object(settings, "TYPING_BASE_DELAY", 0.0):
                with patch.object(settings, "TYPING_DELAY_PER_50_CHARS", 0.0):
                    await simulate_typing_and_send(
                        target=target_msg,
                        reply="<thought>正在分析案情...</thought>這是一宗不尋常的案件！",
                        intent_name="CHAT",
                        prompt="這案子妳怎麼看？",
                        model="lms:gemma4",
                        latency_ms=1234,
                    )

    asyncio.run(_run())

    # Assert
    target_msg.reply.assert_called_once()
    call_kwargs = target_msg.reply.call_args.kwargs
    assert "embed" in call_kwargs
    sent_embed = call_kwargs["embed"]
    assert isinstance(sent_embed, discord.Embed)
    assert "[CHAT]" in sent_embed.title
    assert sent_embed.color.value == 0xE74C3C
    # Check fields
    field_names = [f.name for f in sent_embed.fields]
    assert "👤 使用者提問 / 上下文" in field_names
    assert "⚡ 執行資訊" in field_names
    assert "🧠 思考過程 (CoT)" in field_names
    assert "🐱 雪莉回覆" in field_names


def test_debug_detail_card_rendering_disabled() -> None:
    """Verify simulate_typing_and_send outputs clean text when DEBUG_DETAIL is False."""
    # Arrange
    target_msg = MagicMock(spec=discord.Message)
    target_msg.reply = AsyncMock()
    target_msg.channel = MagicMock(spec=discord.TextChannel)
    target_msg.channel.typing = MagicMock()
    target_msg.channel.typing.return_value.__aenter__ = AsyncMock()
    target_msg.channel.typing.return_value.__aexit__ = AsyncMock()
    target_msg.add_reaction = AsyncMock()

    # Act
    async def _run() -> None:
        with patch.object(settings, "DEBUG_DETAIL", False):
            with patch.object(settings, "TYPING_BASE_DELAY", 0.0):
                with patch.object(settings, "TYPING_DELAY_PER_50_CHARS", 0.0):
                    await simulate_typing_and_send(
                        target=target_msg,
                        reply="<thought>內部思考</thought>你好呀！",
                        intent_name="CHAT",
                    )

    asyncio.run(_run())

    # Assert
    target_msg.reply.assert_called_once()
    sent_text = target_msg.reply.call_args[0][0]
    assert sent_text == "你好呀！"
    assert "[CHAT]" not in sent_text


def test_admin_sync_command() -> None:
    """Verify that /admin action:sync triggers bot.tree.sync()."""
    # Arrange
    bot = MagicMock(spec=commands.Bot)
    bot.tree = MagicMock()
    bot.tree.sync = AsyncMock(return_value=[MagicMock(), MagicMock()])
    admin_cog = AdminCog(bot)

    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    interaction.user = MagicMock()
    interaction.user.id = 999999

    sync_action = MagicMock()
    sync_action.value = "sync"

    # Act
    with patch.object(admin_cog, "_is_super_admin", return_value=True):
        asyncio.run(admin_cog.admin_command.callback(admin_cog, interaction, action=sync_action))

    # Assert
    bot.tree.sync.assert_called_once()
    interaction.followup.send.assert_called_once()
    msg = interaction.followup.send.call_args[0][0]
    assert "成功同步 `2` 個 Slash 指令" in msg
