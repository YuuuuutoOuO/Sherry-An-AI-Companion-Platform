"""
tests/unit/test_react_agent.py
===============================
ReAct Agent 模組單元測試。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import discord
from discord.ext import commands

from handlers.social.react_agent import (
    ReactAgentTool,
    _parse_react_output,
    _extract_xml_tag,
)
from handlers.social.tool_base import BaseTool


class MockTool(BaseTool):
    @property
    def intent_name(self) -> str:
        return "MOCK_TOOL"

    @property
    def llm_description(self) -> str:
        return "測試用工具"

    async def execute(
        self,
        bot: commands.Bot,
        message: discord.Message,
        user_query: str,
        vibe_snippet: str,
        full_context: str,
        *,
        silent: bool = False,
        **kwargs: object,
    ) -> str | None:
        if silent:
            return f"MockTool executed with input: {user_query}"
        return None


def test_extract_xml_tag():
    text = "<thought>Thinking...</thought><action>PIXIV</action><action_input>cat</action_input>"
    assert _extract_xml_tag(text, "thought") == "Thinking..."
    assert _extract_xml_tag(text, "action") == "PIXIV"
    assert _extract_xml_tag(text, "action_input") == "cat"
    assert _extract_xml_tag(text, "nonexistent") is None


def test_parse_react_output_final():
    raw = "<thought>All set.</thought><final_answer>Hello user!</final_answer>"
    res = _parse_react_output(raw)
    assert res["type"] == "final"
    assert res["thought"] == "All set."
    assert res["final_answer"] == "Hello user!"


def test_parse_react_output_action():
    raw = "<thought>Need image.</thought><action>pixiv</action><action_input>anime girl</action_input>"
    res = _parse_react_output(raw)
    assert res["type"] == "action"
    assert res["thought"] == "Need image."
    assert res["action"] == "PIXIV"
    assert res["action_input"] == "anime girl"


def test_parse_react_output_markdown_wrapped():
    raw = "```xml\n<thought>Checking schedule</thought>\n<action>SCHEDULE</action>\n<action_input>5min later alarm</action_input>\n```"
    res = _parse_react_output(raw)
    assert res["type"] == "action"
    assert res["thought"] == "Checking schedule"
    assert res["action"] == "SCHEDULE"
    assert res["action_input"] == "5min later alarm"


def test_react_system_prompt_cleanliness():
    mock_tool = MockTool()
    agent = ReactAgentTool([mock_tool])
    prompt = agent._build_system_prompt()
    # 驗證 ReAct 規劃 System Prompt 已經精簡，不含角色扮演與記憶干擾
    assert "你是一個冷靜、精準的任務規劃與工具調度引擎" in prompt
    assert "MOCK_TOOL" in prompt
    assert "15歲" not in prompt
    assert "櫻羽艾瑪" not in prompt
    assert "Few-Shot" in prompt or "示範案例" in prompt


def test_react_agent_initialization():
    mock_tool = MockTool()
    agent = ReactAgentTool([mock_tool])
    assert "MOCK_TOOL" in agent._tools
    assert agent.intent_name == "REACT_AGENT"
    assert agent.can_handle_by_regex("anything") is False


def test_react_agent_execute_final_answer():
    import asyncio

    async def _run():
        mock_tool = MockTool()
        agent = ReactAgentTool([mock_tool])

        bot = MagicMock(spec=commands.Bot)
        brain = MagicMock()
        brain.enqueue_task = AsyncMock()
        bot.get_cog.return_value = brain

        message = MagicMock(spec=discord.Message)
        message.id = 123
        message.channel.id = 456
        message.author.id = 789
        message.author.display_name = "TestUser"

        with patch.object(
            agent,
            "_call_llm",
            new_callable=AsyncMock,
            return_value="<thought>Chat</thought><final_answer>Hi there!</final_answer>",
        ), patch(
            "handlers.social.react_agent.simulate_typing_and_send",
            new_callable=AsyncMock,
        ) as mock_send, patch(
            "handlers.social.react_agent.log_event"
        ) as mock_log:
            await agent.execute(bot, message, "Hello", "", "")
            assert mock_send.call_count == 1
            call_args, call_kwargs = mock_send.call_args
            assert call_args[0] == message
            assert call_args[1] == "Hi there!"
            assert call_kwargs["intent_name"] == "REACT"
            assert call_kwargs["prompt"] == "Hello"
            assert mock_log.call_count == 1
            log_args, log_kwargs = mock_log.call_args
            assert log_args[0] == "chat_reply"
            assert log_kwargs["intent"] == "REACT"
            assert "Chat" in log_kwargs["cot_thought"]

    asyncio.run(_run())


def test_multi_ticker_extraction():
    from handlers.social.tools.stock_tool import _quick_extract_tickers
    text = "請幫我分析股票: 0050 00878 00981A 台積電"
    tickers = _quick_extract_tickers(text)
    assert "0050.TW" in tickers
    assert "00878.TW" in tickers
    assert "00981A.TW" in tickers
    assert "2330.TW" in tickers


def test_tool_alias_resolution():
    from handlers.social.react_agent import _TOOL_ALIASES
    assert _TOOL_ALIASES.get("PIXIV_SEARCH") == "PIXIV"
    assert _TOOL_ALIASES.get("SET_ALARM") == "SCHEDULE"
    assert _TOOL_ALIASES.get("PLAY_MUSIC") == "MUSIC"
    assert _TOOL_ALIASES.get("STOCK_ANALYSIS") == "STOCK"
