"""
tests/unit/test_core_architecture.py
====================================
測試 SherryCore Linux 風格微核心架構各組件。
"""

from __future__ import annotations

import asyncio

from core.contracts import (
    CapabilityDescriptor,
    SherryRequest,
    SherryResponse,
    ThinkingStep,
)
from core.dispatcher import SmartMessageDispatcher
from core.guard import CoreGuard
from app.orchestrator import SherryCore
from core.registry import CapabilityRegistry
from core.state import SherryMindState


def test_contracts_data_structures() -> None:
    """測試核心合約資料結構與唯讀屬性。"""
    step = ThinkingStep(
        step_type="plan",
        title="意圖解析",
        content="拆解為股市查詢與對話",
        latency_ms=12,
    )
    assert step.step_type == "plan"
    assert step.latency_ms == 12

    req = SherryRequest(
        trace_id="test-req-001",
        source_type="chat_message",
        user_id=123,
        user_name="Alice",
        guild_id=456,
        channel_id=789,
        raw_content="你好雪莉",
    )
    assert req.trace_id == "test-req-001"
    assert req.raw_content == "你好雪莉"

    resp = SherryResponse(
        trace_id="test-req-001",
        reply_text="你好呀！",
        thinking_steps=[step],
    )
    assert resp.reply_text == "你好呀！"
    assert len(resp.thinking_steps) == 1


def test_mind_state_tracker() -> None:
    """測試即時心智狀態機 (SherryMindState)。"""
    state = SherryMindState()
    assert state.get_snapshot().status == "idle"
    assert state.get_snapshot().current_task_count == 0

    state.record_action_start("查詢天氣")
    snap1 = state.get_snapshot()
    assert snap1.current_task_count == 1
    assert snap1.status == "thinking"
    assert any("查詢天氣" in act for act in snap1.recent_actions)

    state.record_action_finish("查詢天氣", success=True)
    snap2 = state.get_snapshot()
    assert snap2.current_task_count == 0
    assert snap2.status == "idle"

    # 測試多筆動作保留上限為 5 筆
    for i in range(10):
        state.record_action_start(f"任務-{i}")
        state.record_action_finish(f"任務-{i}")
    snap3 = state.get_snapshot()
    assert len(snap3.recent_actions) == 5


def test_capability_registry_isolation() -> None:
    """測試能力註冊表與雙層隔離（AI 只能看到日常工具，Mod 維持專題）。"""
    registry = CapabilityRegistry()

    # 註冊日常工具
    registry.register(
        CapabilityDescriptor(
            name="SEARCH",
            category="core_builtin",
            description="搜尋引擎",
            aliases=["查詢", "GOOGLE"],
            is_ai_callable=True,
        )
    )

    # 註冊專題外掛
    registry.register(
        CapabilityDescriptor(
            name="DND",
            category="mod_plugin",
            description="D&D 跑團",
            aliases=["跑團"],
            is_ai_callable=False,
        )
    )

    # 1. 別名查詢
    assert registry.get_capability("GOOGLE") is not None
    assert registry.get_capability("google") is not None
    assert registry.get_capability("跑團") is not None

    # 2. AI 可調用工具嚴格過濾
    ai_tools = registry.get_ai_callable_tools()
    ai_names = [t.name for t in ai_tools]
    assert "SEARCH" in ai_names
    assert "DND" not in ai_names  # 專題外掛不洩漏進 AI 日常工具清單！


def test_smart_message_dispatcher_chunking() -> None:
    """測試 2000 字智慧分段發送器 (Smart Markdown Chunker)。"""
    # 1. 短文字不分割
    short_text = "這是一段簡短的回答。"
    chunks1 = SmartMessageDispatcher.split_text_smart(short_text, max_chars=1900)
    assert len(chunks1) == 1
    assert chunks1[0] == short_text

    # 2. 長文字依段落分割
    p1 = "A" * 1200
    p2 = "B" * 1200
    long_text = f"{p1}\n\n{p2}"
    chunks2 = SmartMessageDispatcher.split_text_smart(long_text, max_chars=1900)
    assert len(chunks2) == 2
    assert chunks2[0] == p1
    assert chunks2[1] == p2

    # 3. 長代碼塊安全封閉與重啟
    code_body = "x = 1\n" * 300
    code_text = f"```python\n{code_body}```"
    chunks3 = SmartMessageDispatcher.split_text_smart(code_text, max_chars=1000)
    assert len(chunks3) > 1
    # 第一段末尾應該被自動補齊 ```
    assert chunks3[0].endswith("```")
    # 第二段開頭應該被自動重啟 ```python
    assert chunks3[1].startswith("```python")


def test_core_guard_rate_limit_and_loop() -> None:
    """測試安全守衛之頻率限制與死循環偵測。"""
    guard = CoreGuard()

    # 頻率限制測試 (max 3 次 / 10 秒)
    u_id = 999
    guard.clear_user_history(u_id)
    allowed1, _ = guard.check_rate_limit(u_id, max_calls=3, window_sec=10.0)
    allowed2, _ = guard.check_rate_limit(u_id, max_calls=3, window_sec=10.0)
    allowed3, _ = guard.check_rate_limit(u_id, max_calls=3, window_sec=10.0)
    allowed4, cd = guard.check_rate_limit(u_id, max_calls=3, window_sec=10.0)

    assert allowed1 is True
    assert allowed2 is True
    assert allowed3 is True
    assert allowed4 is False
    assert cd > 0

    # 死循環偵測測試 (同一工具連續 3 次相同參數)
    assert guard.check_tool_loop(u_id, "STOCK", "2330", max_consecutive=3) is False
    assert guard.check_tool_loop(u_id, "STOCK", "2330", max_consecutive=3) is False
    assert guard.check_tool_loop(u_id, "STOCK", "2330", max_consecutive=3) is True


def test_sherry_core_handle_request() -> None:
    """測試 SherryCore 請求處理與流程協調。"""
    async def _run() -> None:
        core = SherryCore(bot=None)

        req = SherryRequest(
            trace_id="tr-core-test-01",
            source_type="slash_command",
            user_id=111,
            user_name="Bob",
            guild_id=222,
            channel_id=333,
            raw_content="/help",
        )

        resp = await core.handle_request(req)
        assert resp.trace_id == "tr-core-test-01"
        assert resp.success is True
        assert "SherryCore" in resp.reply_text

    asyncio.run(_run())

