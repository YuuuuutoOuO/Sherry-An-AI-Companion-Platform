"""
tests/unit/test_admin_cog.py
============================
管理員指令、記憶管理視圖 (MemoryControlView) 與設定面板單元測試。
"""

from __future__ import annotations

import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
import discord
from discord import app_commands

import settings
from handlers.admin.cog import AdminCog, ConfigModal, ConfigSelect, ConfigView, MemoryControlView


def test_memory_control_view_init() -> None:
    """測試 MemoryControlView 初始化是否正常讀取 settings 且未發生 NameError。"""
    view = MemoryControlView(target_user_id=123456789)
    assert view.target_user_id == 123456789
    assert view.timeout == settings.DISCORD_UI_VIEW_TIMEOUT


def test_config_view_and_modal() -> None:
    """測試 ConfigView 與 ConfigModal 正常初始化。"""
    modal = ConfigModal(key="PRIMARY_MODEL", current_value="test-model")
    assert modal.key == "PRIMARY_MODEL"
    assert modal.new_value.default == "test-model"

    view = ConfigView()
    assert len(view.children) == 1
    assert isinstance(view.children[0], ConfigSelect)


def test_admin_cog_memory_command_show() -> None:
    """測試 /memory show 指令正常執行並附帶 MemoryControlView。"""
    async def _run() -> None:
        bot = MagicMock()
        mem_mgr = MagicMock()
        mem_mgr.get_full_user_memory_snippet = MagicMock(return_value="喜歡喝咖啡，常在深夜寫 code。")
        bot.get_cog = MagicMock(side_effect=lambda name: mem_mgr if name == "MemoryManager" else None)

        cog = AdminCog(bot)

        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        user = MagicMock(spec=discord.Member)
        user.id = 999888
        user.display_name = "TestTester"
        user.mention = "<@999888>"

        action = app_commands.Choice(name="檢視記憶", value="show")

        callback = cast(Any, cog.memory_command.callback)
        await callback(cog, interaction, action=action, user=user)

        assert interaction.response.send_message.called
        kwargs = interaction.response.send_message.call_args.kwargs
        assert "view" in kwargs
        assert isinstance(kwargs["view"], MemoryControlView)
        assert kwargs["view"].target_user_id == 999888
        assert kwargs["ephemeral"] is True

    asyncio.run(_run())


def test_admin_cog_memory_command_world() -> None:
    """測試 /memory world 指令正常取得當前伺服器的世界記憶。"""
    async def _run() -> None:
        bot = MagicMock()
        cog = AdminCog(bot)

        interaction = MagicMock(spec=discord.Interaction)
        guild = MagicMock(spec=discord.Guild)
        guild.id = 777888
        guild.name = "測試魔法工坊"
        interaction.guild = guild
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        action = app_commands.Choice(name="世界記憶", value="world")

        callback = cast(Any, cog.memory_command.callback)
        with patch("handlers.memory.world_memory.get_world_memory_snippet", return_value="🌍【世界近期大事】\n- 2026-09-02 工坊成立"):
            await callback(cog, interaction, action=action)

        assert interaction.response.send_message.called
        args, kwargs = interaction.response.send_message.call_args
        assert "測試魔法工坊 世界記憶" in args[0]
        assert "工坊成立" in args[0]
        assert kwargs["ephemeral"] is True

    asyncio.run(_run())


def test_admin_cog_memory_command_self() -> None:
    """測試 /memory self 指令正常取得雪莉的自我記憶庫 (Embed)。"""
    async def _run() -> None:
        bot = MagicMock()
        cog = AdminCog(bot)

        interaction = MagicMock(spec=discord.Interaction)
        interaction.channel_id = 12345
        interaction.response = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup = MagicMock()
        interaction.followup.send = AsyncMock()

        action = app_commands.Choice(name="雪莉自我記憶", value="self")
        mock_episodes = [{"topic": "測試想法", "content": "今天在群組摸魚很開心", "emotional_tone": "得意"}]
        mock_actions = [{"verb": "賽博群友", "summary": "熱聊對線", "detail": {"thought": "有趣話題"}, "created_at": 1700000000}]
        mock_readings = [{"title": "AI 發展報告", "opinion": "這篇分析還算精準", "source": "科技網"}]

        callback = cast(Any, cog.memory_command.callback)
        with patch("handlers.memory.self_memory.list_self_episodes", return_value=mock_episodes), \
             patch("handlers.memory.self_memory.list_self_actions", return_value=mock_actions), \
             patch("handlers.memory.self_memory.list_reading_logs", return_value=mock_readings):
            await callback(cog, interaction, action=action)

        assert interaction.followup.send.called
        _, kwargs = interaction.followup.send.call_args
        embed = kwargs.get("embed")
        assert embed is not None
        assert "雪莉自我記憶庫" in embed.title
        assert kwargs.get("ephemeral") is True

    asyncio.run(_run())


def test_admin_cog_stats_command() -> None:
    """測試 /stats 狀態儀表板指令正常生成 Embed。"""
    async def _run() -> None:
        bot = MagicMock()
        brain = MagicMock()
        brain._queue = MagicMock()
        brain._queue.qsize = MagicMock(return_value=0)
        brain._sd_generating = False
        brain._voice_mode_active = False

        mem_mgr = MagicMock()
        mem_mgr._store = {"123": {}}

        vibe_mgr = MagicMock()
        vibe_mgr._store = {"456": {}}

        def fake_get_cog(name: str) -> Any:
            if name == "BotBrain":
                return brain
            if name == "MemoryManager":
                return mem_mgr
            if name == "VibeManager":
                return vibe_mgr
            return None

        bot.get_cog = MagicMock(side_effect=fake_get_cog)
        bot._start_time = 0.0

        cog = AdminCog(bot)

        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        callback = cast(Any, cog.stats_command.callback)
        await callback(cog, interaction)

        assert interaction.response.send_message.called
        kwargs = interaction.response.send_message.call_args.kwargs
        assert "embed" in kwargs
        assert isinstance(kwargs["embed"], discord.Embed)

    asyncio.run(_run())


def test_vibe_clear_permission_and_deletion() -> None:
    """測試 /vibe clear 權限校驗：非超級管理員阻擋，超級管理員放行並自記憶體與磁碟刪除。"""
    async def _run() -> None:
        bot = MagicMock()
        vibe_mgr = MagicMock()
        vibe_mgr._store = {"12345": {"topics": ["AI", "Python"]}}
        bot.get_cog = MagicMock(return_value=vibe_mgr)

        cog = AdminCog(bot)

        target_channel = MagicMock(spec=discord.TextChannel)
        target_channel.id = 12345
        target_channel.mention = "<#12345>"

        action = app_commands.Choice(name="清除氛圍", value="clear")

        # 1. 一般成員嘗試清除 -> 阻擋
        unauth_interaction = MagicMock(spec=discord.Interaction)
        unauth_interaction.user.id = 999999
        unauth_interaction.response = MagicMock()
        unauth_interaction.response.send_message = AsyncMock()

        with patch.object(settings, "ADMIN_USER_IDS", [111222]):
            callback = cast(Any, cog.vibe_command.callback)
            await callback(cog, unauth_interaction, action=action, channel=target_channel)

            unauth_interaction.response.send_message.assert_called_once()
            args, kwargs = unauth_interaction.response.send_message.call_args
            assert "只有超級管理員才能刪除文件" in args[0]
            assert kwargs["ephemeral"] is True
            assert "12345" in vibe_mgr._store

        # 2. 超級管理員執行清除 -> 成功並呼叫 save_vibe_store
        auth_interaction = MagicMock(spec=discord.Interaction)
        auth_interaction.user.id = 111222
        auth_interaction.response = MagicMock()
        auth_interaction.response.send_message = AsyncMock()

        with patch.object(settings, "ADMIN_USER_IDS", [111222]), \
             patch("handlers.vibe_manager.storage.save_vibe_store") as mock_save:
            await callback(cog, auth_interaction, action=action, channel=target_channel)

            auth_interaction.response.send_message.assert_called_once()
            args, _ = auth_interaction.response.send_message.call_args
            assert "已成功清除" in args[0]
            assert "12345" not in vibe_mgr._store
            mock_save.assert_called_once()

    asyncio.run(_run())


def test_memory_clear_super_admin_permission() -> None:
    """測試 /memory clear 僅允許最高管理員（ADMIN_USER_IDS）執行物理刪除。"""
    async def _run() -> None:
        bot = MagicMock()
        mem_mgr = MagicMock()
        mem_mgr._store = {"777888": {"profile": "機密記憶"}}
        bot.get_cog = MagicMock(return_value=mem_mgr)

        cog = AdminCog(bot)

        target_user = MagicMock(spec=discord.Member)
        target_user.id = 777888
        target_user.mention = "<@777888>"

        action = app_commands.Choice(name="清除記憶", value="clear")

        # 1. 非最高管理員嘗試清除 -> 阻擋
        unauth_interaction = MagicMock(spec=discord.Interaction)
        unauth_interaction.user.id = 555555
        unauth_interaction.response = MagicMock()
        unauth_interaction.response.send_message = AsyncMock()

        with patch.object(settings, "ADMIN_USER_IDS", [111222]):
            callback = cast(Any, cog.memory_command.callback)
            await callback(cog, unauth_interaction, action=action, user=target_user)

            unauth_interaction.response.send_message.assert_called_once()
            args, kwargs = unauth_interaction.response.send_message.call_args
            assert "只有最高管理員能刪除使用者記憶" in args[0]
            assert kwargs["ephemeral"] is True
            assert "777888" in mem_mgr._store

        # 2. 最高管理員執行清除 -> 走 R6 交易級清除管線（軟刪 API）
        auth_interaction = MagicMock(spec=discord.Interaction)
        auth_interaction.user.id = 111222
        auth_interaction.response = MagicMock()
        auth_interaction.response.send_message = AsyncMock()

        with patch.object(settings, "ADMIN_USER_IDS", [111222]), \
             patch(
                 "handlers.memory.storage.purge_user_data",
                 new=AsyncMock(return_value={"ok": True, "scopes": {}}),
             ) as mock_purge:
            await callback(cog, auth_interaction, action=action, user=target_user)

            auth_interaction.response.send_message.assert_called_once()
            args, _ = auth_interaction.response.send_message.call_args
            assert "已成功清除" in args[0]
            assert "777888" not in mem_mgr._store
            mock_purge.assert_awaited_once()

    asyncio.run(_run())


def test_memory_control_view_button_author_validation() -> None:
    """測試 MemoryControlView 按鈕僅目標本人可操作，越權點擊應被攔截。"""
    async def _run() -> None:
        view = MemoryControlView(target_user_id=12345)

        # 1. 旁人點擊 incorrect_button -> 攔截
        other_interaction = MagicMock(spec=discord.Interaction)
        other_interaction.user.id = 99999
        other_interaction.response = MagicMock()
        other_interaction.response.send_message = AsyncMock()

        await view.incorrect_button.callback(other_interaction)
        other_interaction.response.send_message.assert_called_once()
        assert "只能清除自己的記憶" in other_interaction.response.send_message.call_args[0][0]

        # 2. 目標本人點擊 incorrect_button -> 執行清除
        self_interaction = MagicMock(spec=discord.Interaction)
        self_interaction.user.id = 12345
        self_interaction.response = MagicMock()
        self_interaction.response.send_message = AsyncMock()

        mem_mgr = MagicMock()
        mem_mgr._store = {"12345": {"facts": ["喜歡吃拉麵"]}}
        self_interaction.client.get_cog = MagicMock(return_value=mem_mgr)

        with patch(
            "handlers.memory.storage.purge_user_data",
            new=AsyncMock(return_value={"ok": True, "scopes": {}}),
        ) as mock_purge:
            await view.incorrect_button.callback(self_interaction)
            self_interaction.response.send_message.assert_called_once()
            assert "把錯亂的記憶刪除囉" in self_interaction.response.send_message.call_args[0][0]
            assert "12345" not in mem_mgr._store
            mock_purge.assert_awaited_once()


    asyncio.run(_run())

