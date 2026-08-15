"""
tests/unit/test_parallel_agent.py
===================================
Parallel Agent (高效能並行調度器) 模組單元測試。
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from discord.ext import commands

from handlers.social.parallel_agent import (
    ExecutionPlan,
    ParallelAgentTool,
    PlannedTask,
    parse_plan_json,
)
from handlers.social.tool_base import BaseTool, ToolResponseMode
import settings


class MockDirectUITool(BaseTool):
    @property
    def intent_name(self) -> str:
        return "MOCK_UI_TOOL"

    @property
    def llm_description(self) -> str:
        return "自帶 UI 渲染的測試工具（例如音樂、搜圖、畫圖）"

    @property
    def response_mode(self) -> ToolResponseMode:
        return ToolResponseMode.DIRECT_UI

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
            return f"MockDirectUITool executed with: {user_query}"
        return None


class MockDataOnlyTool(BaseTool):
    @property
    def intent_name(self) -> str:
        return "MOCK_DATA_TOOL"

    @property
    def llm_description(self) -> str:
        return "純資料查詢測試工具（例如股票行情、天氣）"

    @property
    def response_mode(self) -> ToolResponseMode:
        return ToolResponseMode.DATA_ONLY

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
            return f"MockDataOnlyTool data result for {user_query}"
        return None


# ---------------------------------------------------------------------------
# 1. JSON 規劃解析與容錯測試
# ---------------------------------------------------------------------------

def test_parse_plan_json_standard():
    raw = json.dumps({
        "thought": "拆解為兩個音樂任務",
        "tasks": [
            {"tool": "MUSIC", "input": "莫札特"},
            {"tool": "MUSIC", "input": "巴哈"}
        ],
        "needs_persona_reply": False
    })
    plan = parse_plan_json(raw, "原始訊息")
    assert plan.thought == "拆解為兩個音樂任務"
    assert len(plan.tasks) == 2
    assert plan.tasks[0].tool == "MUSIC"
    assert plan.tasks[0].input == "莫札特"
    assert plan.tasks[1].tool == "MUSIC"
    assert plan.tasks[1].input == "巴哈"
    assert plan.needs_persona_reply is False


def test_parse_plan_json_markdown_block():
    raw = """```json
    {
      "thought": "搜尋雪莉圖片",
      "tasks": [{"tool": "PIXIV", "input": "橘雪莉"}],
      "needs_persona_reply": false
    }
    ```"""
    plan = parse_plan_json(raw, "原始訊息")
    assert plan.thought == "搜尋雪莉圖片"
    assert len(plan.tasks) == 1
    assert plan.tasks[0].tool == "PIXIV"
    assert plan.tasks[0].input == "橘雪莉"
    assert plan.needs_persona_reply is False


def test_parse_plan_json_empty_tasks_chat():
    raw = json.dumps({
        "thought": "純日常聊天",
        "tasks": [],
        "needs_persona_reply": True
    })
    plan = parse_plan_json(raw, "今天好冷")
    assert plan.thought == "純日常聊天"
    assert len(plan.tasks) == 0
    assert plan.needs_persona_reply is True


def test_parse_plan_json_regex_fallback():
    # 模擬 LLM 吐出殘缺 JSON（缺少閉合括號等）
    raw = '{"thought": "需要畫圖", "tasks": [{"tool": "DRAW", "input": "小貓"}], "needs_persona_reply": true'
    plan = parse_plan_json(raw, "畫一隻貓")
    assert len(plan.tasks) == 1
    assert plan.tasks[0].tool == "DRAW"
    assert plan.tasks[0].input == "小貓"
    assert plan.needs_persona_reply is True


# ---------------------------------------------------------------------------
# 2. ParallelAgentTool 提示詞與屬性測試
# ---------------------------------------------------------------------------

def test_parallel_agent_initialization():
    tool_ui = MockDirectUITool()
    tool_data = MockDataOnlyTool()
    agent = ParallelAgentTool([tool_ui, tool_data])

    assert agent.intent_name == "PARALLEL_AGENT"
    assert agent.can_handle_by_regex("test") is False
    assert "MOCK_UI_TOOL" in agent._tools
    assert "MOCK_DATA_TOOL" in agent._tools

    prompt = agent._build_system_prompt()
    assert "PARALLEL TASK DISPATCH & CHAT SUPPRESSION ENGINE" in prompt
    assert "MOCK_UI_TOOL" in prompt
    assert "MOCK_DATA_TOOL" in prompt
    assert "needs_persona_reply" in prompt


# ---------------------------------------------------------------------------
# 3. 行為自適應對話抑制判定測試 (Early Termination)
# ---------------------------------------------------------------------------

def test_should_early_terminate_success():
    tool_ui = MockDirectUITool()
    agent = ParallelAgentTool([tool_ui])

    plan = ExecutionPlan(
        thought="播放兩首音樂",
        tasks=[PlannedTask("MUSIC", "莫札特"), PlannedTask("MUSIC", "巴哈")],
        needs_persona_reply=False,
    )
    executed_results = [
        ("MUSIC", ToolResponseMode.DIRECT_UI, "已加入隊列"),
        ("MUSIC", ToolResponseMode.DIRECT_UI, "已加入隊列"),
    ]

    # 無聊天需求，全為 DIRECT_UI，判定為 True (終止對話)
    assert agent._should_early_terminate(plan, executed_results, "請幫我播放莫札特的歌與巴哈的歌") is True


def test_should_early_terminate_blocked_by_data_only_tool():
    tool_ui = MockDirectUITool()
    tool_data = MockDataOnlyTool()
    agent = ParallelAgentTool([tool_ui, tool_data])

    plan = ExecutionPlan(
        thought="搜圖並查股票",
        tasks=[PlannedTask("PIXIV", "貓咪"), PlannedTask("STOCK", "2330.TW")],
        needs_persona_reply=False,
    )
    executed_results = [
        ("PIXIV", ToolResponseMode.DIRECT_UI, "已發送圖片"),
        ("STOCK", ToolResponseMode.DATA_ONLY, "2330 收盤價 980"),
    ]

    # 包含 DATA_ONLY 工具，不能終止，必須由 AI 轉述數據
    assert agent._should_early_terminate(plan, executed_results, "搜圖並查台積電") is False


def test_should_early_terminate_blocked_by_explicit_chat():
    tool_ui = MockDirectUITool()
    agent = ParallelAgentTool([tool_ui])

    plan = ExecutionPlan(
        thought="放歌並問心得",
        tasks=[PlannedTask("MUSIC", "晴天")],
        needs_persona_reply=False,  # 即使 Planner 誤設為 False
    )
    executed_results = [
        ("MUSIC", ToolResponseMode.DIRECT_UI, "已加入隊列"),
    ]

    # 使用者有明確詢問「喜不喜歡」心得，正則雙重防線攔截，不終止
    assert agent._should_early_terminate(plan, executed_results, "放一首晴天，順便跟我說妳喜不喜歡這首歌") is False


def test_should_early_terminate_switch_off(monkeypatch: pytest.MonkeyPatch):
    tool_ui = MockDirectUITool()
    agent = ParallelAgentTool([tool_ui])

    plan = ExecutionPlan(
        thought="播放音樂",
        tasks=[PlannedTask("MUSIC", "莫札特")],
        needs_persona_reply=False,
    )
    executed_results = [
        ("MUSIC", ToolResponseMode.DIRECT_UI, "已加入隊列"),
    ]

    # 當全域開關關閉時，不終止
    monkeypatch.setattr(settings, "ACTION_EARLY_TERMINATION_ENABLED", False)
    assert agent._should_early_terminate(plan, executed_results, "播放莫札特") is False


# ---------------------------------------------------------------------------
# 4. 非同步並行執行流程測試 (execute flow)
# ---------------------------------------------------------------------------

def test_parallel_agent_execute_early_termination():
    async def _run():
        tool_ui = MockDirectUITool()
        agent = ParallelAgentTool([tool_ui])

        bot = MagicMock(spec=commands.Bot)
        brain = MagicMock()
        bot.get_cog.return_value = brain

        # 模擬 Planner LLM 回傳 JSON
        async def mock_enqueue(task):
            plan_json = json.dumps({
                "thought": "播放莫札特與巴哈",
                "tasks": [
                    {"tool": "MOCK_UI_TOOL", "input": "莫札特"},
                    {"tool": "MOCK_UI_TOOL", "input": "巴哈"}
                ],
                "needs_persona_reply": False
            })
            await task.callback(plan_json)

        brain.enqueue_task = AsyncMock(side_effect=mock_enqueue)

        message = MagicMock(spec=discord.Message)
        message.id = 12345
        message.channel = MagicMock()
        message.channel.id = 67890
        message.author.id = 111
        message.author.display_name = "TestUser"

        with patch("handlers.social.parallel_agent.log_event") as mock_log:
            await agent.execute(
                bot=bot,
                message=message,
                user_query="請幫我播放莫札特的歌與巴哈的歌",
                vibe_snippet="",
                full_context="",
            )

            # 驗證記錄了 early termination 事件
            assert mock_log.called
            call_kwargs = mock_log.call_args.kwargs
            assert call_kwargs["intent_source"] == "parallel_early_term"
            assert "MOCK_UI_TOOL" in call_kwargs["tools_invoked"]

    asyncio.run(_run())


def test_parallel_agent_execute_full_conversation():
    async def _run():
        tool_data = MockDataOnlyTool()
        agent = ParallelAgentTool([tool_data])

        bot = MagicMock(spec=commands.Bot)
        brain = MagicMock()
        bot.get_cog.return_value = brain

        # 第一輪 enqueue 是 Planner，第二輪是 Primary Model
        call_count = 0

        async def mock_enqueue(task):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Planner
                plan_json = json.dumps({
                    "thought": "查詢股票行情",
                    "tasks": [{"tool": "MOCK_DATA_TOOL", "input": "2330.TW"}],
                    "needs_persona_reply": True
                })
                await task.callback(plan_json)
            else:
                # Primary Model
                await task.callback("<thought>分析台積電行情</thought>台積電今天走勢很強喔！")

        brain.enqueue_task = AsyncMock(side_effect=mock_enqueue)

        message = MagicMock(spec=discord.Message)
        message.id = 12345
        message.channel = MagicMock()
        message.channel.id = 67890
        message.author.id = 111
        message.author.display_name = "TestUser"

        with patch("handlers.social.parallel_agent.simulate_typing_and_send", new_callable=AsyncMock) as mock_send:
            await agent.execute(
                bot=bot,
                message=message,
                user_query="查一下台積電走勢",
                vibe_snippet="",
                full_context="",
            )

            assert mock_send.called
            send_args = mock_send.call_args
            assert "台積電今天走勢很強喔！" in send_args[0][1]

    asyncio.run(_run())
