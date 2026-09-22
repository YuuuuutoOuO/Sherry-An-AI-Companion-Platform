"""tests/unit/test_dnd_cog.py
===========================
D&D 統一指令 (/dnd) 與 Action 分派行為單元測試。
"""

from __future__ import annotations

import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord import app_commands
import pytest

from mod.dnd.cog import DndCog
from mod.dnd.models import Character, CharacterStats, Inventory, WorldState


@pytest.fixture
def mock_bot() -> MagicMock:
    """提供 Mock Discord Bot。"""
    bot = MagicMock(spec=discord.Client)
    bot.user = MagicMock()
    bot.user.id = 12345
    return bot


def _make_interaction(user_id: int = 999, channel_id: int = 888) -> MagicMock:
    """建立 Mock Interaction。"""
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(spec=discord.User)
    interaction.user.id = user_id
    interaction.user.display_name = "冒險者小明"
    interaction.channel_id = channel_id
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.send_modal = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def test_dnd_command_default_panel(mock_bot: MagicMock) -> None:
    """測試未提供 action 時預設開啟 panel。"""
    async def _run() -> None:
        cog = DndCog(mock_bot)
        interaction = _make_interaction()

        callback = cast(Any, cog.dnd.callback)
        await callback(cog, interaction, action=None, context=None)

        interaction.response.send_message.assert_awaited_once()
        call_kwargs = interaction.response.send_message.call_args.kwargs
        assert "embed" in call_kwargs
        assert "view" in call_kwargs

    asyncio.run(_run())


def test_dnd_command_action_without_context(mock_bot: MagicMock) -> None:
    """測試 action 動作未給 context 時彈出 CustomActionModal。"""
    async def _run() -> None:
        cog = DndCog(mock_bot)
        interaction = _make_interaction()

        action = app_commands.Choice(name="冒險行動", value="action")
        callback = cast(Any, cog.dnd.callback)
        await callback(cog, interaction, action=action, context="")

        interaction.response.send_modal.assert_awaited_once()

    asyncio.run(_run())


def test_dnd_command_action_with_context(mock_bot: MagicMock) -> None:
    """測試 action 動作帶有 context 時推播至 BotBrain。"""
    async def _run() -> None:
        brain = MagicMock()
        brain.enqueue_task = AsyncMock()
        mock_bot.get_cog = MagicMock(side_effect=lambda name: brain if name == "BotBrain" else None)

        cog = DndCog(mock_bot)
        interaction = _make_interaction()

        action = app_commands.Choice(name="冒險行動", value="action")
        callback = cast(Any, cog.dnd.callback)
        await callback(cog, interaction, action=action, context="我拔出長劍環顧四周")

        interaction.response.defer.assert_awaited_once()
        brain.enqueue_task.assert_awaited_once()

    asyncio.run(_run())


def test_dnd_command_join_without_context(mock_bot: MagicMock) -> None:
    """測試 join 動作未給 context 時彈出 DndCreateModal。"""
    async def _run() -> None:
        cog = DndCog(mock_bot)
        interaction = _make_interaction()

        action = app_commands.Choice(name="創建角色", value="join")
        callback = cast(Any, cog.dnd.callback)
        await callback(cog, interaction, action=action, context="")

        interaction.response.send_modal.assert_awaited_once()

    asyncio.run(_run())


def test_dnd_command_join_with_context(mock_bot: MagicMock) -> None:
    """測試 join 動作帶 context 時自動解析並建立角色。"""
    async def _run() -> None:
        cog = DndCog(mock_bot)
        interaction = _make_interaction(user_id=1001)

        action = app_commands.Choice(name="創建角色", value="join")
        callback = cast(Any, cog.dnd.callback)
        await callback(cog, interaction, action=action, context="莉莉絲 精靈 法師")

        interaction.response.defer.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args.kwargs
        assert "embed" in call_kwargs
        assert "莉莉絲" in call_kwargs["embed"].title

    asyncio.run(_run())


def test_dnd_command_roll_without_context(mock_bot: MagicMock) -> None:
    """測試 roll 動作未給 context 時彈出 DndRollModal。"""
    async def _run() -> None:
        cog = DndCog(mock_bot)
        interaction = _make_interaction()

        action = app_commands.Choice(name="擲骰判定", value="roll")
        callback = cast(Any, cog.dnd.callback)
        await callback(cog, interaction, action=action, context="")

        interaction.response.send_modal.assert_awaited_once()

    asyncio.run(_run())


def test_dnd_command_roll_with_context(mock_bot: MagicMock) -> None:
    """測試 roll 動作帶 context 擲骰計算（一般、優勢、劣勢）。"""
    async def _run() -> None:
        cog = DndCog(mock_bot)

        # 1. 一般擲骰 2d6+2
        interaction = _make_interaction()
        action = app_commands.Choice(name="擲骰判定", value="roll")
        callback = cast(Any, cog.dnd.callback)
        await callback(cog, interaction, action=action, context="2d6+2")
        interaction.response.send_message.assert_awaited_once()
        embed = interaction.response.send_message.call_args.kwargs["embed"]
        assert "2d6" in embed.description

        # 2. 優勢擲骰
        interaction_adv = _make_interaction()
        await callback(cog, interaction_adv, action=action, context="d20 adv +3")
        embed_adv = interaction_adv.response.send_message.call_args.kwargs["embed"]
        assert "優勢" in embed_adv.description

        # 3. 劣勢擲骰
        interaction_dis = _make_interaction()
        await callback(cog, interaction_dis, action=action, context="d20 dis -1")
        embed_dis = interaction_dis.response.send_message.call_args.kwargs["embed"]
        assert "劣勢" in embed_dis.description

    asyncio.run(_run())


def test_dnd_command_status(mock_bot: MagicMock) -> None:
    """測試 status 角色卡查看。"""
    async def _run() -> None:
        cog = DndCog(mock_bot)
        interaction = _make_interaction(user_id=2001)

        # 尚未創角時
        action = app_commands.Choice(name="角色狀態", value="status")
        callback = cast(Any, cog.dnd.callback)
        await callback(cog, interaction, action=action, context="")
        call_kwargs = interaction.response.send_message.call_args.kwargs
        call_args = interaction.response.send_message.call_args.args
        msg = call_args[0] if call_args else call_kwargs.get("content", "")
        assert "你還沒有創建角色" in msg

        # 已創角後
        with patch("mod.dnd.cog.load_characters") as mock_load_chars:
            fake_char = Character(
                user_id=2001,
                name="亞瑟",
                race="人類",
                character_class="戰士",
                stats=CharacterStats(),
                inventory=Inventory(),
            )
            mock_load_chars.return_value = {2001: fake_char}

            interaction2 = _make_interaction(user_id=2001)
            await callback(cog, interaction2, action=action, context="")
            embed = interaction2.response.send_message.call_args.kwargs["embed"]
            assert "亞瑟" in embed.title

    asyncio.run(_run())


def test_dnd_command_inventory(mock_bot: MagicMock) -> None:
    """測試 inventory 背包查看。"""
    async def _run() -> None:
        cog = DndCog(mock_bot)
        action = app_commands.Choice(name="查看背包", value="inventory")
        callback = cast(Any, cog.dnd.callback)

        with patch("mod.dnd.cog.load_characters") as mock_load_chars:
            fake_char = Character(
                user_id=2002,
                name="卡特",
                race="人類",
                character_class="盜賊",
                stats=CharacterStats(),
                inventory=Inventory(gold=50, silver=20),
            )
            mock_load_chars.return_value = {2002: fake_char}

            interaction = _make_interaction(user_id=2002)
            await callback(cog, interaction, action=action, context="")
            embed = interaction.response.send_message.call_args.kwargs["embed"]
            assert "卡特 的冒險背包" in embed.title
            assert "背包空空如也" in embed.description

    asyncio.run(_run())


def test_dnd_command_session(mock_bot: MagicMock) -> None:
    """測試 session 跑團模式開關與 toggle。"""
    async def _run() -> None:
        cog = DndCog(mock_bot)
        action = app_commands.Choice(name="跑團模式", value="session")
        callback = cast(Any, cog.dnd.callback)

        fake_world = WorldState()
        with patch("mod.dnd.cog.load_world_state", return_value=fake_world), \
             patch("mod.dnd.cog.save_world_state") as mock_save:

            # 1. 啟動 session
            interaction_start = _make_interaction(channel_id=9999)
            await callback(cog, interaction_start, action=action, context="start")
            assert 9999 in fake_world.active_channels
            mock_save.assert_called()

            # 2. 關閉 session
            interaction_stop = _make_interaction(channel_id=9999)
            await callback(cog, interaction_stop, action=action, context="stop")
            assert 9999 not in fake_world.active_channels

            # 3. Toggle 切換
            interaction_toggle = _make_interaction(channel_id=9999)
            await callback(cog, interaction_toggle, action=action, context="")
            assert 9999 in fake_world.active_channels

    asyncio.run(_run())


def test_dnd_command_world(mock_bot: MagicMock) -> None:
    """測試 world 查看世界狀態。"""
    async def _run() -> None:
        cog = DndCog(mock_bot)
        action = app_commands.Choice(name="世界情報", value="world")
        callback = cast(Any, cog.dnd.callback)

        interaction = _make_interaction()
        await callback(cog, interaction, action=action, context="")
        embed = interaction.response.send_message.call_args.kwargs["embed"]
        assert "世界狀態" in embed.title

    asyncio.run(_run())