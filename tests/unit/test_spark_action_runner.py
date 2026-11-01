"""tests/unit/test_spark_action_runner.py - SparkActionRunner 動作執行分流單元測試。

驗證動作分流執行：
1. NOTIFY: 正常推送 Embed 提醒至指定頻道。
2. CORE_TOOL: 調度核心工具（如 STOCK, PIXIV 等）並帶入查詢參數。
3. MOD_DEEP_SEARCH: 特權啟動地端多輪自主深度研究任務。
遵循 AGENTS.md 規範與 AAA 測試結構。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from handlers.spark.action_runner import SparkActionRunner
from handlers.spark.models import ActionType, Trigger


@pytest.fixture
def mock_bot() -> MagicMock:
    """提供標準 Mock Discord Bot。"""
    bot = MagicMock()
    bot.get_channel = MagicMock()
    bot.fetch_channel = AsyncMock()
    bot.get_cog = MagicMock(return_value=None)
    bot.loop = MagicMock()
    return bot


@pytest.fixture
def mock_channel() -> MagicMock:
    """提供標準 Mock Discord 頻道。"""
    channel = MagicMock()
    channel.id = 999
    channel.send = AsyncMock()
    return channel


@pytest.mark.anyio
async def test_action_runner_notify(mock_bot: MagicMock, mock_channel: MagicMock) -> None:
    """測試 ActionType.NOTIFY 動作分流。"""
    # Arrange
    mock_bot.get_channel.return_value = mock_channel
    runner = SparkActionRunner(mock_bot)
    trigger = Trigger(
        trigger_id="trig-notify-1234",
        user_id=101,
        channel_id=999,
        trigger_type="schedule",
        condition={"mode": "once", "target_time": "2026-09-21T08:00:00"},
        message="記得提醒ㄜㄋ生日！",
        action={"action_type": ActionType.NOTIFY.value},
    )

    # Act
    success = await runner.run_action(trigger, "單次提醒時間已到")

    # Assert
    assert success is True
    mock_channel.send.assert_awaited_once()
    embed = mock_channel.send.call_args.kwargs.get("embed")
    assert embed is not None
    assert "記得提醒ㄜㄋ生日！" in str(embed.fields)


@pytest.mark.anyio
async def test_action_runner_core_tool(mock_bot: MagicMock, mock_channel: MagicMock) -> None:
    """測試 ActionType.CORE_TOOL 動作分流。"""
    # Arrange
    mock_bot.get_channel.return_value = mock_channel
    runner = SparkActionRunner(mock_bot)

    mock_tool = MagicMock()
    mock_tool.intent_name = "STOCK"
    mock_tool.execute = AsyncMock(return_value="已繪製 0050 走勢圖")

    trigger = Trigger(
        trigger_id="trig-core-1234",
        user_id=101,
        channel_id=999,
        trigger_type="schedule",
        condition={"mode": "daily_time", "time": "10:00"},
        message="查詢本日0050走勢",
        action={
            "action_type": ActionType.CORE_TOOL.value,
            "target": "STOCK",
            "payload": {"query": "0050"},
        },
    )

    # Act
    with patch("handlers.social.registry.TOOL_LIST", [mock_tool]):
        success = await runner.run_action(trigger, "每日定時 10:00 觸發")

    # Assert
    assert success is True
    mock_tool.execute.assert_awaited_once()
    _, kwargs = mock_tool.execute.call_args
    assert kwargs.get("user_query") == "0050"


@pytest.mark.anyio
async def test_action_runner_deep_search(mock_bot: MagicMock, mock_channel: MagicMock) -> None:
    """測試 ActionType.MOD_DEEP_SEARCH 特權調度。"""
    # Arrange
    mock_channel.send = AsyncMock(return_value=MagicMock(id=8888))
    mock_bot.get_channel.return_value = mock_channel

    mock_deep_search_cog = MagicMock()
    mock_deep_search_cog._run_research_flow = AsyncMock()
    mock_bot.get_cog.side_effect = lambda name: mock_deep_search_cog if name == "DeepSearch" else None

    runner = SparkActionRunner(mock_bot)
    trigger = Trigger(
        trigger_id="trig-deep-1234",
        user_id=101,
        channel_id=999,
        trigger_type="schedule",
        condition={"mode": "daily_time", "time": "10:00"},
        message="0050股票走勢研究",
        action={
            "action_type": ActionType.MOD_DEEP_SEARCH.value,
            "payload": {"topic": "本日0050股票走勢研究", "rounds": 3},
        },
    )

    # Act
    with patch("asyncio.create_task") as mock_create_task:
        success = await runner.run_action(trigger, "每日定時觸發深度研究")

    # Assert
    assert success is True
    mock_channel.send.assert_awaited_once()  # 發送啟動卡片
    mock_create_task.assert_called_once()    # 建立背景研究任務


@pytest.mark.anyio
async def test_action_runner_chat_tool_banner_suppression(mock_bot: MagicMock, mock_channel: MagicMock) -> None:
    """測試 CHAT 對話工具調度時免除機器人橫幅，直接調用 ChatTool 自然發話。"""
    # Arrange
    mock_bot.get_channel.return_value = mock_channel
    runner = SparkActionRunner(mock_bot)

    mock_chat_tool = MagicMock()
    mock_chat_tool.intent_name = "CHAT"
    mock_chat_tool.execute = AsyncMock(return_value="名偵探講了一個冷笑話～")

    trigger = Trigger(
        trigger_id="trig-chat-5678",
        user_id=101,
        channel_id=999,
        trigger_type="schedule",
        condition={"mode": "daily_time", "time": "08:00", "tz_offset": 8},
        message="告訴我一個笑話",
        action={
            "action_type": "core_tool",
            "target": "CHAT",
            "payload": {"query": "告訴我一個好笑的笑話"},
        },
    )

    # Act
    with patch("handlers.social.registry.TOOL_LIST", [mock_chat_tool]):
        success = await runner.run_action(trigger, "每日 08:00 定時觸發")

    # Assert
    assert success is True
    # 確保沒有發送機器人式的 intro_embed 橫幅
    mock_channel.send.assert_not_called()
    # 確保直接調用了 ChatTool.execute
    mock_chat_tool.execute.assert_awaited_once()
    _, kwargs = mock_chat_tool.execute.call_args
    assert kwargs.get("user_query") == "告訴我一個好笑的笑話"


@pytest.mark.anyio
async def test_action_runner_legacy_trigger_fallback_inference(mock_bot: MagicMock, mock_channel: MagicMock) -> None:
    """測試歷史任務（因舊 bug 或舊版本遺失 action）觸發時，能自動從 message 推導並調用核心工具。"""
    # Arrange
    mock_bot.get_channel.return_value = mock_channel
    runner = SparkActionRunner(mock_bot)

    mock_pixiv_tool = MagicMock()
    mock_pixiv_tool.intent_name = "PIXIV"
    mock_pixiv_tool.execute = AsyncMock(return_value="已找到大奶圖！")

    # 模擬歷史任務：action 欄位為空字典
    legacy_trigger = Trigger(
        trigger_id="trig-legacy-empty-action",
        user_id=101,
        channel_id=999,
        trigger_type="schedule",
        condition={"mode": "interval", "interval_minutes": 10},
        message="傳一張大奶圖給 Yuuto",
        action={},  # 故意為空
    )

    # Act
    with patch("handlers.social.registry.TOOL_LIST", [mock_pixiv_tool]):
        success = await runner.run_action(legacy_trigger, "定時觸發")

    # Assert
    assert success is True
    # 驗證自動相容機制成功辨識出 PIXIV 並執行工具！
    mock_pixiv_tool.execute.assert_awaited_once()
    _, kwargs = mock_pixiv_tool.execute.call_args
    assert "大奶" in kwargs.get("user_query", "")


@pytest.mark.anyio
async def test_action_runner_tags_user_and_view_on_notify(mock_bot: MagicMock, mock_channel: MagicMock) -> None:
    """測試 NOTIFY 動作一定標註使用者 (<@user_id>) 並掛載 SparkFollowUpView。"""
    from handlers.spark.action_runner import SparkFollowUpView

    mock_bot.get_channel.return_value = mock_channel
    mock_service = MagicMock()
    runner = SparkActionRunner(mock_bot, service=mock_service)

    trigger = Trigger(
        trigger_id="trig-tag-notify-1",
        user_id=425311733503229972,
        channel_id=999,
        trigger_type="schedule",
        condition={"mode": "once", "target_time": "2026-10-18T08:30:00+08:00"},
        message="提醒我吃維他命",
        action={"action_type": ActionType.NOTIFY.value},
    )

    success = await runner.run_action(trigger, "單次提醒觸發")
    assert success is True
    mock_channel.send.assert_awaited_once()
    send_kwargs = mock_channel.send.call_args.kwargs
    assert send_kwargs.get("content") == "<@425311733503229972>"
    assert isinstance(send_kwargs.get("view"), SparkFollowUpView)


@pytest.mark.anyio
async def test_spark_channel_context_prepends_mention_and_view(mock_channel: MagicMock) -> None:
    """測試 SparkChannelContext.send 自動加上 @Tag 與 follow_up_view。"""
    from handlers.spark.action_runner import SparkChannelContext

    mock_view = MagicMock()
    ctx = SparkChannelContext(channel=mock_channel, user_id=12345, bot=MagicMock(), follow_up_view=mock_view)

    # 1. 帶有 content 參數
    await ctx.send("早安！今天天氣真好。")
    mock_channel.send.assert_awaited_once()
    pos_args, kwargs = mock_channel.send.call_args
    sent_text = pos_args[0] if pos_args else kwargs.get("content", "")
    assert "<@12345>" in sent_text
    assert kwargs.get("view") == mock_view

    mock_channel.send.reset_mock()
    # 2. 第二次發送不應重複標記
    await ctx.send("第二則訊息")
    pos_args2, kwargs2 = mock_channel.send.call_args
    sent_text2 = pos_args2[0] if pos_args2 else kwargs2.get("content", "")
    assert "<@12345>" not in sent_text2


@pytest.mark.anyio
async def test_action_runner_agent_prompt_and_memory_update(mock_bot: MagicMock, mock_channel: MagicMock) -> None:
    """測試 AGENT_PROMPT 動作調度 DispatchAgentTool 並寫回跨輪記憶。"""
    mock_bot.get_channel.return_value = mock_channel
    mock_service = MagicMock()
    runner = SparkActionRunner(mock_bot, service=mock_service)

    trigger = Trigger(
        trigger_id="trig-agent-prompt-1",
        user_id=101,
        channel_id=999,
        trigger_type="schedule",
        condition={"mode": "daily_time", "time": "08:00"},
        message="查最新CPI與台股並問候",
        action={
            "action_type": ActionType.AGENT_PROMPT.value,
            "payload": {"prompt": "查最新CPI與台股並跟我說早安"},
        },
        extra_data={"last_output_summary": "昨日CPI持平"},
    )

    with patch("handlers.social.dispatch_agent.DispatchAgentTool.execute", new_callable=AsyncMock) as mock_agent_exec:
        mock_agent_exec.return_value = "名偵探今日分析：最新CPI降溫，台股開高，早安！"
        success = await runner.run_action(trigger, "每日定時觸發")

    assert success is True
    mock_agent_exec.assert_awaited_once()
    exec_kwargs = mock_agent_exec.call_args.kwargs
    assert exec_kwargs.get("spark_previous_memory") == "昨日CPI持平"
    mock_service.update_trigger_memory.assert_called_once_with(
        "trig-agent-prompt-1",
        "名偵探今日分析：最新CPI降溫，台股開高，早安！",
    )


@pytest.mark.anyio
async def test_spark_follow_up_view_permission() -> None:
    """測試 SparkFollowUpView 只允許排程擁有者點擊。"""
    from handlers.spark.action_runner import SparkFollowUpView

    trigger = Trigger(
        trigger_id="trig-perm-test",
        user_id=99999,
        channel_id=1,
        trigger_type="schedule",
        condition={"mode": "interval", "interval_minutes": 10},
        message="測試按鈕",
        action={"action_type": "notify"},
    )
    view = SparkFollowUpView(MagicMock(), trigger)

    # 模擬非擁有者
    other_interaction = MagicMock()
    other_interaction.user.id = 11111
    other_interaction.response.send_message = AsyncMock()
    can_proceed = await view.interaction_check(other_interaction)
    assert can_proceed is False
    other_interaction.response.send_message.assert_awaited_once()

    # 模擬擁有者
    owner_interaction = MagicMock()
    owner_interaction.user.id = 99999
    assert await view.interaction_check(owner_interaction) is True


