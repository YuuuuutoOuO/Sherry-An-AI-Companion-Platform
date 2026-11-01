from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from handlers.interaction.contracts import AUTONOMOUS_POLICY, FULL_MENTION_POLICY
from handlers.interaction.tool_policy import ToolBroker, ToolDenied
from handlers.social.dispatch_agent import DispatchAgentTool
from handlers.social.tool_specs import TOOL_SPECS


def test_autonomous_denies_draw() -> None:
    broker = ToolBroker(bot=None)

    async def run() -> None:
        with pytest.raises(ToolDenied):
            await broker.run(AUTONOMOUS_POLICY, "DRAW", {"prompt": "cat"})

    asyncio.run(run())


def test_autonomous_allows_search(monkeypatch: pytest.MonkeyPatch) -> None:
    broker = ToolBroker(bot=None)
    broker._search = lambda args: asyncio.sleep(0, result="ok")

    async def run() -> None:
        assert await broker.run(AUTONOMOUS_POLICY, "SEARCH", {"query": "x"}) == "ok"

    asyncio.run(run())


def test_mention_policy_allows_stock() -> None:
    assert "STOCK" in FULL_MENTION_POLICY.allowed
    assert "DRAW" not in AUTONOMOUS_POLICY.allowed


def test_dispatch_tool_descriptions_follow_tool_specs() -> None:
    """被動 lane 的工具說明要跟 TOOL_SPECS 同源、同順序，避免 prompt 漂移。"""
    tools = [
        SimpleNamespace(intent_name=spec.name, llm_description=f"desc-{spec.name}")
        for spec in TOOL_SPECS
    ]
    agent = DispatchAgentTool(tools)

    text = agent._build_tool_descriptions()
    assert text == agent._build_tool_descriptions()
    assert "**CHAT**" not in text
    for spec in TOOL_SPECS:
        if spec.name == "CHAT":
            continue
        assert f"- **{spec.name}**：desc-{spec.name}" in text

    positions = [
        text.index(f"**{spec.name}**")
        for spec in TOOL_SPECS
        if spec.name != "CHAT"
    ]
    assert positions == sorted(positions)
