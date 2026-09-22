"""tests/unit/test_schedule_spark_tool.py - ScheduleTool 與 SPARK 調度整合單元測試。

驗證核心原則：
1. 嚴禁關鍵字比對：can_handle_by_regex 永不因「提醒我」等詞觸發，全交由 LLM Planner 語意規劃。
2. ScheduleTool.execute 成功整合 SparkNLP 與 SparkConfirmView 互動防呆卡片。
遵循 AGENTS.md 規範與 AAA 測試結構。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from handlers.social.tools.schedule_tool import ScheduleTool


def test_schedule_tool_no_regex_keyword_matching() -> None:
    """驗證零關鍵字比對原則：任何包含『提醒我』、『排程』等字串均不可被 regex 攔截。"""
    # Arrange
    tool = ScheduleTool()

    # Act & Assert
    assert tool.can_handle_by_regex("提醒我後天8點開會") is False
    assert tool.can_handle_by_regex("請提醒我 9/21 是ㄜㄋ的生日") is False
    assert tool.can_handle_by_regex("每天早上10點提醒我研究0050") is False
    assert tool.can_handle_by_regex("每60分鐘傳一張色圖") is False
    assert tool.can_handle_by_regex("定時排程通知") is False


@pytest.mark.anyio
async def test_schedule_tool_execute_presents_confirm_view() -> None:
    """測試 ScheduleTool.execute 正確解析語意並回傳 SparkConfirmView。"""
    # Arrange
    tool = ScheduleTool()
    mock_bot = MagicMock()
    mock_service = MagicMock()
    mock_spark_cog = MagicMock()
    mock_spark_cog.service = mock_service
    mock_bot.get_cog.return_value = mock_spark_cog

    mock_message = MagicMock()
    mock_message.author.id = 12345
    mock_message.author.display_name = "UserA"
    mock_message.channel.id = 67890
    mock_message.reply = AsyncMock()

    candidate = {
        "trigger_type": "schedule",
        "user_id": 12345,
        "channel_id": 67890,
        "condition": {"mode": "once", "target_time": "2026-09-05T08:00:00"},
        "message": "後天8.開會",
        "action": {"action_type": "notify"},
    }

    # Act
    with patch("handlers.spark.nlp.parse_spark_context", AsyncMock(return_value=candidate)):
        result = await tool.execute(
            bot=mock_bot,
            message=mock_message,
            user_query="後天8.我要開會 請記得提醒我",
            vibe_snippet="",
            full_context="",
        )

    from handlers.social.contracts import ToolExecutionStatus
    # Assert
    assert result.status == ToolExecutionStatus.SUCCESS
    assert result.is_direct_rendered is True
    mock_message.reply.assert_awaited_once()
    kwargs = mock_message.reply.call_args.kwargs
    assert "view" in kwargs
    assert kwargs.get("view").__class__.__name__ == "SparkConfirmView"
    assert "embed" in kwargs
