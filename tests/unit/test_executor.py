"""tests/unit/test_executor.py
==========================
Unit tests for ExecutorCog and CommandHubView (AAA pattern).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import discord

from handlers.executor.cog import (
    CommandHubCategorySelect,
    CommandHubView,
    ExecutorCog,
    PromptTestModal,
)


@pytest.fixture
def anyio_backend() -> str:
    """設定 anyio 後端為 asyncio。"""
    return "asyncio"


@pytest.fixture
def mock_bot() -> MagicMock:
    """建立 Mock Bot 實例。"""
    bot = MagicMock()
    return bot


def test_command_hub_view_init_and_embeds(mock_bot: MagicMock) -> None:
    """測試 CommandHubView 初始化與各模組預覽 Embed 產生。"""
    # Arrange & Act
    view = CommandHubView(mock_bot)

    # Assert
    assert view.bot == mock_bot
    assert len(view.children) > 0

    # Act & Assert across categories
    categories = ["spark", "dnd", "music", "stream", "admin", "intent"]
    for cat in categories:
        embed = view.build_preview_embed(cat)
        assert embed is not None
        assert embed.title is not None
        assert cat.upper() in embed.title or "預覽" in embed.title


@pytest.mark.anyio
async def test_command_hub_category_select_callback(mock_bot: MagicMock) -> None:
    """測試 CommandHubCategorySelect 選擇不同分類切換面板。"""
    # Arrange
    view = CommandHubView(mock_bot)
    select = [c for c in view.children if isinstance(c, CommandHubCategorySelect)][0]
    select._values = ["dnd"]

    interaction = MagicMock()
    interaction.response.edit_message = AsyncMock()

    # Act
    await select.callback(interaction)

    # Assert
    assert view.current_category == "dnd"
    interaction.response.edit_message.assert_awaited_once()
    kwargs = interaction.response.edit_message.call_args.kwargs
    assert kwargs.get("embed") is not None


@pytest.mark.anyio
async def test_execute_slash_command_hub_open(mock_bot: MagicMock) -> None:
    """測試無參數呼叫 /execute 開啟 CommandHubView。"""
    # Arrange
    cog = ExecutorCog(mock_bot)
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()

    # Act
    await cog.execute_intent.callback(cog, interaction=interaction, intent=None, content=None)

    # Assert
    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.call_args.kwargs
    assert isinstance(kwargs.get("view"), CommandHubView)


@pytest.mark.anyio
async def test_prompt_test_modal_submit(mock_bot: MagicMock) -> None:
    """測試 PromptTestModal 提交表單測試意圖。"""
    # Arrange
    mock_brain = MagicMock()
    mock_brain.enqueue_task = AsyncMock()
    mock_bot.get_cog.return_value = mock_brain

    modal = PromptTestModal(mock_bot)
    modal.intent_input._value = "CUSTOM_TEST"
    modal.prompt_input._value = "你好，雪莉！"

    interaction = MagicMock()
    interaction.channel = MagicMock(spec=discord.TextChannel)
    interaction.user.display_name = "Agent007"
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    mock_tool = MagicMock()
    mock_tool.intent_name = "CUSTOM_TEST"
    mock_tool.execute = AsyncMock()

    with patch("handlers.executor.cog.TOOL_LIST", [mock_tool]):
        # Act
        await modal.on_submit(interaction)

        # Assert
        interaction.response.defer.assert_awaited_once()
        mock_tool.execute.assert_awaited_once()


@pytest.mark.anyio
async def test_admin_intent_permission_gate(mock_bot: MagicMock) -> None:
    """測試一般使用者無法執行敏感管理意圖，但管理員可以。"""
    cog = ExecutorCog(mock_bot)

    # 1. 一般使用者嘗試執行 ADMIN 意圖 -> 被拒絕
    interaction_user = MagicMock(spec=discord.Interaction)
    interaction_user.user = MagicMock(spec=discord.Member)
    interaction_user.user.id = 999999
    interaction_user.user.roles = []
    interaction_user.user.guild_permissions = discord.Permissions.none()
    interaction_user.guild = None
    interaction_user.response = MagicMock()
    interaction_user.response.send_message = AsyncMock()
    interaction_user.response.defer = AsyncMock()

    with patch("settings.ADMIN_USER_IDS", [111111]):
        with patch("settings.ADMIN_ROLE_IDS", []):
            await cog.execute_intent.callback(
                cog,
                interaction=interaction_user,
                intent="ADMIN",
                content="reload all",
            )
            interaction_user.response.send_message.assert_awaited_once()
            args = interaction_user.response.send_message.call_args[0][0]
            assert "權限不足" in args

    # 2. 一般使用者開啟 CommandHubView -> 成功開放
    interaction_hub = MagicMock()
    interaction_hub.response.send_message = AsyncMock()
    await cog.execute_intent.callback(
        cog,
        interaction=interaction_hub,
        intent=None,
        content=None,
    )
    interaction_hub.response.send_message.assert_awaited_once()
    assert isinstance(interaction_hub.response.send_message.call_args.kwargs.get("view"), CommandHubView)
