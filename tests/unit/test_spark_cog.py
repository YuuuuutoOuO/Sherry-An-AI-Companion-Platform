"""tests/unit/test_spark_cog.py - Spark Discord Cog 互動介面單元測試。

遵循 AAA (Arrange / Act / Assert) 原則。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from handlers.spark.cog import (
    PriceTriggerModal,
    SparkActionSelectView,
    SparkCog,
    SparkConfirmView,
    SparkMainView,
)
from handlers.spark.models import Trigger, TriggerResult


@pytest.fixture
def anyio_backend() -> str:
    """設定 anyio 測試後端為 asyncio。"""
    return "asyncio"


@pytest.fixture
def mock_bot() -> MagicMock:
    """提供 Mock Discord Bot 實例。"""
    bot = MagicMock()
    bot.wait_until_ready = AsyncMock()
    bot.get_channel = MagicMock()
    bot.fetch_channel = AsyncMock()
    return bot


@pytest.fixture
def mock_service() -> MagicMock:
    """提供 Mock SparkService 實例。"""
    service = MagicMock()
    service.list_active_triggers.return_value = []
    service.list_triggers.return_value = [
        Trigger(
            trigger_id="mock-1-uuid",
            user_id=100,
            channel_id=200,
            trigger_type="price",
            condition={"ticker": "BTC-USD"},
        )
    ]
    service.is_in_cooldown.return_value = False
    service.check_trigger = AsyncMock()
    service.create_trigger.return_value = Trigger(
        trigger_id="mock-1-uuid",
        user_id=100,
        channel_id=200,
        trigger_type="price",
        condition={"ticker": "BTC-USD"},
    )
    service.delete_trigger.return_value = True
    service.toggle_trigger.return_value = Trigger(
        trigger_id="mock-1-uuid",
        user_id=100,
        channel_id=200,
        trigger_type="price",
        condition={"ticker": "BTC-USD"},
        is_active=False,
    )
    return service


def test_spark_cog_init(mock_bot: MagicMock, mock_service: MagicMock) -> None:
    """測試 SparkCog 正確初始化與注入 Service。"""
    cog = SparkCog(bot=mock_bot, service=mock_service)
    assert cog.bot == mock_bot
    assert cog.service == mock_service


@pytest.mark.anyio
async def test_spark_dashboard_command(mock_bot: MagicMock, mock_service: MagicMock) -> None:
    """測試 /spark 主控面板指令（預設 action=panel）。"""
    # Arrange
    cog = SparkCog(bot=mock_bot, service=mock_service)
    interaction = MagicMock()
    interaction.channel_id = 12345
    interaction.response.send_message = AsyncMock()

    # Act
    await cog.spark_command.callback(cog, interaction)

    # Assert
    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.call_args.kwargs
    assert kwargs.get("embed") is not None
    assert isinstance(kwargs.get("view"), SparkMainView)


@pytest.mark.anyio
async def test_price_modal_submit(mock_service: MagicMock) -> None:
    """測試價格 Modal 提交建立觸發器流程。"""
    # Arrange
    modal = PriceTriggerModal(mock_service)
    modal.ticker_input._value = "BTC"
    modal.target_price_input._value = "65000"
    modal.direction_input._value = "above"
    modal.message_input._value = "衝啊"

    interaction = MagicMock()
    interaction.user.id = 999
    interaction.channel_id = 888
    interaction.response.send_message = AsyncMock()

    # Act
    await modal.on_submit(interaction)

    # Assert
    mock_service.create_trigger.assert_called_once()
    interaction.response.send_message.assert_awaited_once()


@pytest.mark.anyio
async def test_spark_main_view_buttons(mock_service: MagicMock) -> None:
    """測試主面板中編輯、暫停、測試、刪除按鈕回呼。"""
    # Arrange
    view = SparkMainView(mock_service)
    view.selected_trigger_id = "mock-1-uuid"

    mock_service.get_trigger.return_value = Trigger(
        trigger_id="mock-1-uuid",
        user_id=100,
        channel_id=200,
        trigger_type="price",
        condition={"ticker": "BTC-USD", "target_price": 60000.0, "direction": "above"},
    )
    mock_service.check_trigger.return_value = (
        TriggerResult(should_fire=True, current_value="BTC 現價 65,000"),
        None,
    )

    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.send_modal = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    # Act 1: Toggle
    await view.btn_toggle.callback(interaction)
    mock_service.toggle_trigger.assert_called_once_with("mock-1-uuid")

    # Act 2: Test
    await view.btn_test.callback(interaction)
    mock_service.check_trigger.assert_awaited_once()

    # Act 3: Edit (modal)
    await view.btn_edit.callback(interaction)
    interaction.response.send_modal.assert_awaited_once()

    # Act 4: Delete
    await view.btn_delete.callback(interaction)
    mock_service.delete_trigger.assert_called_once_with("mock-1-uuid")


@pytest.mark.anyio
async def test_spark_confirm_view(mock_service: MagicMock) -> None:
    """測試 SparkConfirmView 確認與取消按鈕行為。"""
    candidate = {
        "user_id": 999,
        "channel_id": 888,
        "trigger_type": "price",
        "condition": {"ticker": "NVDA", "target_price": 135.0, "direction": "above"},
        "message": "衝啊",
        "cooldown_seconds": 3600,
    }
    view = SparkConfirmView(mock_service, candidate, author_id=999)
    preview_embed = view.build_preview_embed()
    assert "盤中監控" in (preview_embed.description or "")
    assert "One-shot" in (preview_embed.description or "")

    # 1. 權限檢查：其他人點擊應被阻擋
    other_interaction = MagicMock()
    other_interaction.user.id = 111
    other_interaction.response.send_message = AsyncMock()
    allowed = await view.interaction_check(other_interaction)
    assert allowed is False
    other_interaction.response.send_message.assert_awaited_once()

    # 2. 確認建立按鈕
    interaction = MagicMock()
    interaction.user.id = 999
    interaction.response.edit_message = AsyncMock()
    await view.btn_confirm.callback(interaction)
    mock_service.create_trigger.assert_called_once()
    interaction.response.edit_message.assert_awaited_once()

    # 3. 取消按鈕
    cancel_interaction = MagicMock()
    cancel_interaction.response.edit_message = AsyncMock()
    cancel_view = SparkConfirmView(mock_service, candidate, author_id=999)
    await cancel_view.btn_cancel.callback(cancel_interaction)
    cancel_interaction.response.edit_message.assert_awaited_once()


@pytest.mark.anyio
async def test_quick_slash_commands(mock_bot: MagicMock, mock_service: MagicMock) -> None:
    """測試單一 /spark 指令的 action 與 context 自然語言解析流程。"""
    cog = SparkCog(bot=mock_bot, service=mock_service)

    # 1. /spark action:add context:NVDA 100元時叫我 (價格警報)
    inter_price = MagicMock()
    inter_price.user.id = 101
    inter_price.channel_id = 202
    inter_price.response.defer = AsyncMock()
    inter_price.followup.send = AsyncMock()
    act_add = MagicMock()
    act_add.value = "add"

    await cog.spark_command.callback(
        cog,
        inter_price,
        action=act_add,
        context="NVDA 100元時叫我",
    )
    inter_price.response.defer.assert_awaited_once()
    inter_price.followup.send.assert_awaited_once()
    kwargs_price = inter_price.followup.send.call_args.kwargs
    assert kwargs_price.get("embed") is not None
    assert isinstance(kwargs_price.get("view"), SparkConfirmView)

    # 2. /spark action:add context:每天 08:30 提醒吃藥 (定時提醒)
    inter_remind = MagicMock()
    inter_remind.user.id = 101
    inter_remind.channel_id = 202
    inter_remind.response.defer = AsyncMock()
    inter_remind.followup.send = AsyncMock()

    await cog.spark_command.callback(
        cog,
        inter_remind,
        action=act_add,
        context="每天 08:30 提醒吃藥",
    )
    inter_remind.response.defer.assert_awaited_once()
    inter_remind.followup.send.assert_awaited_once()

    # 3. /spark action:list (清單)
    inter_list = MagicMock()
    inter_list.response.send_message = AsyncMock()
    act_list = MagicMock()
    act_list.value = "list"

    await cog.spark_command.callback(
        cog,
        inter_list,
        action=act_list,
    )
    inter_list.response.send_message.assert_awaited_once()

    # 4. /spark action:delete context:mock-1 (刪除)
    inter_del = MagicMock()
    inter_del.response.send_message = AsyncMock()
    act_del = MagicMock()
    act_del.value = "delete"

    await cog.spark_command.callback(
        cog,
        inter_del,
        action=act_del,
        context="mock-1",
    )
    inter_del.response.send_message.assert_awaited_once()

    # 5. /spark action:toggle context:mock-1 (切換)
    inter_toggle = MagicMock()
    inter_toggle.response.send_message = AsyncMock()
    act_toggle = MagicMock()
    act_toggle.value = "toggle"

    await cog.spark_command.callback(
        cog,
        inter_toggle,
        action=act_toggle,
        context="mock-1",
    )
    inter_toggle.response.send_message.assert_awaited_once()

    # 6. /spark action:delete (無 context，彈出專屬選擇選單)
    inter_del_none = MagicMock()
    inter_del_none.response.send_message = AsyncMock()
    await cog.spark_command.callback(
        cog,
        inter_del_none,
        action=act_del,
        context=None,
    )
    inter_del_none.response.send_message.assert_awaited_once()
    kwargs_del = inter_del_none.response.send_message.call_args.kwargs
    assert isinstance(kwargs_del.get("view"), SparkActionSelectView)


@pytest.mark.anyio
async def test_prefix_cli_command(mock_bot: MagicMock, mock_service: MagicMock) -> None:
    """測試 !spark 文字 CLI 指令。"""
    cog = SparkCog(bot=mock_bot, service=mock_service)
    ctx = MagicMock()
    ctx.channel.id = 555
    ctx.author.id = 666
    ctx.reply = AsyncMock()

    # 1. !spark list
    await cog.spark_prefix.callback(cog, ctx, action="list")
    ctx.reply.assert_awaited_once()

    # 2. !spark delete mock-1
    ctx.reply.reset_mock()
    await cog.spark_prefix.callback(cog, ctx, action="delete", args="mock-1")
    mock_service.delete_trigger.assert_called()
    ctx.reply.assert_awaited_once()

    # 3. !spark toggle mock-1
    ctx.reply.reset_mock()
    await cog.spark_prefix.callback(cog, ctx, action="toggle", args="mock-1")
    mock_service.toggle_trigger.assert_called()
    ctx.reply.assert_awaited_once()

    # 4. !spark add NVDA 100元時叫我
    ctx.reply.reset_mock()
    await cog.spark_prefix.callback(cog, ctx, action="add", args="NVDA 100元時叫我")
    ctx.reply.assert_awaited_once()


@pytest.mark.anyio
async def test_send_notification_price_embed(mock_bot: MagicMock, mock_service: MagicMock) -> None:
    """測試發送價格推播通知時包含市場狀態與單次達標說明欄位。"""
    # Arrange
    cog = SparkCog(bot=mock_bot, service=mock_service)
    mock_channel = MagicMock()
    mock_channel.send = AsyncMock()
    mock_bot.get_channel.return_value = mock_channel

    trigger = Trigger(
        trigger_id="trig-price-12345678",
        user_id=123,
        channel_id=456,
        trigger_type="price",
        condition={"ticker": "NVDA", "target_price": 100.0, "one_shot": True},
        message="自訂訊息",
    )

    # Act
    await cog._send_notification(trigger, "NVDA 現價 $105.00 已達標")

    # Assert
    mock_channel.send.assert_awaited_once()
    embed = mock_channel.send.call_args.kwargs.get("embed")
    assert embed is not None
    assert "NVDA 現價 $105.00 已達標" in embed.description

    field_names = [f.name for f in embed.fields]
    assert "🏛️ 市場狀態" in field_names
    assert "🎉 任務完成" in field_names
