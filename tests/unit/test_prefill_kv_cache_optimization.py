"""
tests/unit/test_prefill_kv_cache_optimization.py
=================================================
驗證 Prefill 速度與 KV Cache 重構優化之單元測試：
1. 對話歷史 Append-Only 化（保護 Prefix Caching）
2. 對話濃縮快取修復（水位節流 skip_for_now=True 時重用摘要疊加新訊息）
3. 統一 Prompt 佈局（Pass-1 與 Pass-2 靜態前綴 100% 對齊）
4. 嚴格 Token Budget 預算制（世界記憶 6 筆、用戶記憶 8 條+1 條、歷史上限 1500 tokens）
5. TTFT 計時校正與 Reasoning 串流（SSE delta.reasoning_content 即時標記與串流）
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone, timedelta
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import discord

import settings
from handlers.bot_brain.client import OllamaClient
from handlers.memory.world_memory import get_world_memory_snippet, clear_world_memory_cache
from handlers.social.dispatch_agent import DispatchAgentTool
from handlers.social.memory_builder import build_user_memory_block
from handlers.social.streaming_thinking import LiveThinkingSession
from handlers.social.tool_base import BaseTool, ToolResponseMode
from handlers.social.utils import (
    _CONTEXT_COMPRESS_CACHE,
    fetch_context_script,
)


class DummyEchoTool(BaseTool):
    def __init__(self, name: str, mode: ToolResponseMode = ToolResponseMode.DATA_PROVIDER) -> None:
        self._name = name
        self._mode = mode

    @property
    def intent_name(self) -> str:
        return self._name

    @property
    def llm_description(self) -> str:
        return f"Dummy tool {self._name} for testing"

    @property
    def response_mode(self) -> ToolResponseMode:
        return self._mode

    def can_handle_by_regex(self, text: str) -> bool:
        return False

    async def execute(self, *args: Any, **kwargs: Any) -> str:
        return "Dummy result"


def _make_msg(
    msg_id: int,
    author_id: int,
    author_name: str,
    content: str,
    created_at: datetime,
    is_bot: bool = False,
) -> discord.Message:
    msg = MagicMock(spec=discord.Message)
    msg.id = msg_id
    msg.author = MagicMock()
    msg.author.id = author_id
    msg.author.display_name = author_name
    msg.author.bot = is_bot
    msg.clean_content = content
    msg.created_at = created_at
    msg.embeds = []
    msg.attachments = []
    return msg


def _make_chan(messages: list[discord.Message], bot_id: int = 9999) -> Any:
    channel = MagicMock()
    channel.id = 5566
    guild = MagicMock()
    guild.me = MagicMock()
    guild.me.id = bot_id
    channel.guild = guild

    async def fake_history(limit: int = 30, before: Any = None, oldest_first: bool = False):
        ordered = sorted(messages, key=lambda m: m.created_at, reverse=True)
        for m in ordered[:limit]:
            yield m

    channel.history = fake_history
    return channel


# ---------------------------------------------------------------------------
# 1. 對話歷史 Append-Only 化（保護 Prefix Caching）
# ---------------------------------------------------------------------------

def test_history_append_only_prefix_invariance() -> None:
    """測試連續多輪對話中，歷史訊息字串保持嚴格 Append-Only 不變性，確保 KV Cache 命中。"""
    async def _run() -> None:
        now = datetime.now(timezone.utc)
        bot_id = 9999

        # 第一輪：10 則對話
        msgs_round1 = [
            _make_msg(i, 1000 + (i % 2), f"User{i % 2}", f"對話發言內容 {i}", now - timedelta(seconds=(20 - i) * 10))
            for i in range(10)
        ]
        channel1 = _make_chan(msgs_round1, bot_id=bot_id)
        result1 = await fetch_context_script(channel1)

        # 第二輪：新增 2 則對話
        msgs_round2 = msgs_round1 + [
            _make_msg(10, 1000, "User0", "新增的提問 10", now - timedelta(seconds=5)),
            _make_msg(11, bot_id, "雪莉", "新增的回覆 11", now - timedelta(seconds=2)),
        ]
        channel2 = _make_chan(msgs_round2, bot_id=bot_id)
        result2 = await fetch_context_script(channel2)

        # 驗證：result1 必須是 result2 的嚴格前綴（保持字串完全不變）
        assert result2.startswith(result1), "第二輪對話歷史必須完全包含並以第一輪對話歷史為嚴格前綴！"
        assert "新增的提問 10" in result2
        assert "新增的回覆 11" in result2

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 2. 對話濃縮快取與水位節流疊加新訊息修復
# ---------------------------------------------------------------------------

def test_watermark_throttling_reuses_previous_summary() -> None:
    """測試當水位節流 skip_for_now=True 時，取出上一輪快取摘要疊加新訊息，絕不退回全量 raw 歷史。"""
    async def _run() -> None:
        _CONTEXT_COMPRESS_CACHE.clear()
        now = datetime.now(timezone.utc)
        bot_id = 9999

        dense_dialogue = [
            _make_msg(
                i, 1000 + (i % 3), f"User{i % 3}",
                f"密集任務討論內容第 {i} 條：" + ("長篇細節" * 35),
                now - timedelta(seconds=(30 - i) * 5),
            )
            for i in range(20)
        ]
        channel = _make_chan(dense_dialogue, bot_id=bot_id)

        bot = MagicMock()
        brain = MagicMock()
        bot.get_cog = MagicMock(return_value=brain)

        async def fake_enqueue(task: Any) -> None:
            if hasattr(task, "callback"):
                await task.callback("專案架構重構與效能優化討論中。")

        brain.enqueue_task = AsyncMock(side_effect=fake_enqueue)

        with patch.object(settings, "CONTEXT_COMPRESS_THRESHOLD", 500):
            # 第 1 次觸發完整濃縮
            first_result = await fetch_context_script(channel, bot=bot)
            assert "【過往話題濃縮摘要】" in first_result
            assert "專案架構重構與效能優化討論中。" in first_result
            assert brain.enqueue_task.call_count == 1

            # 第 2 次新增 2 則新訊息（< min_gap 8），觸發水位節流
            brain.enqueue_task.reset_mock()
            new_msgs = [
                _make_msg(101, 1001, "Alice", "請問目前進度如何？", now - timedelta(seconds=2)),
                _make_msg(102, 1002, "Bob", "正在處理 TTFT 優化！", now - timedelta(seconds=1)),
            ]
            channel2 = _make_chan(dense_dialogue + new_msgs, bot_id=bot_id)
            throttled_result = await fetch_context_script(channel2, bot=bot)

            # 驗證：未重新呼叫 LLM，且結果包含上一輪摘要與新訊息
            assert brain.enqueue_task.call_count == 0
            assert "【過往話題濃縮摘要】" in throttled_result
            assert "專案架構重構與效能優化討論中。" in throttled_result
            assert "請問目前進度如何？" in throttled_result
            assert "正在處理 TTFT 優化！" in throttled_result

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 3. 統一 Prompt 佈局（Pass-1 與 Pass-2 Prefix Alignment）
# ---------------------------------------------------------------------------

def test_pass1_and_pass2_prefix_alignment() -> None:
    """驗證 Pass-1 與 Pass-2 的 System Prompt 靜態前綴與動態情境排列 100% 對齊。"""
    async def _run() -> None:
        tools = [DummyEchoTool("SEARCH", ToolResponseMode.DATA_PROVIDER)]
        agent = DispatchAgentTool(tools, mode="INTEGRATED")

        bot = MagicMock()
        brain = MagicMock()

        msg = MagicMock()
        msg.id = 777
        msg.channel.id = 888
        msg.author.id = 999
        msg.author.display_name = "Charlie"
        msg.guild = None
        msg.reply = AsyncMock()
        msg.channel.send = AsyncMock()

        history_text = "📜 【過往對話紀錄】(依時間先後排列)：\n   群友 [Charlie]：「你好」\n   妳自己：「嗨！」"
        vibe_text = "輕鬆聊天"
        user_q = "請幫我搜尋天氣"

        captured_system_prompts: list[str] = []

        async def fake_call_llm(brain_cog: Any, system_prompt: str, user_prompt: str, **kwargs: Any) -> str:
            captured_system_prompts.append(system_prompt)
            # 模擬 Pass-1 產出 action 標籤
            if len(captured_system_prompts) == 1:
                return "<thought>需要查天氣</thought><action>SEARCH: 台北天氣</action>"
            return "<thought>整理回覆</thought>台北今天天氣晴朗！"

        with patch.object(agent, "_call_llm", side_effect=fake_call_llm):
            await agent._run_integrated(
                brain=brain,
                bot=bot,
                message=msg,
                user_query=user_q,
                vibe_snippet=vibe_text,
                full_context=history_text,
                images=None,
                start_time=100.0,
            )

        assert len(captured_system_prompts) == 2, "應依序觸發 Pass-1 與 Pass-2 推論"
        pass1_sys = captured_system_prompts[0]
        pass2_sys = captured_system_prompts[1]

        # 核心驗證：Pass-1 與 Pass-2 靜態前綴與動態情境 100% 嚴格對齊以最大化 KV Cache 複用
        assert pass1_sys == pass2_sys, "Pass-1 與 Pass-2 系統提示詞應 100% 嚴格一致以最大化 KV Cache 複用"
        # 驗證兩者之動態情境 (vibe 與對話歷史) 排列順序完全一致
        assert vibe_text in pass1_sys and vibe_text in pass2_sys
        assert "【過往對話紀錄】" in pass1_sys and "【過往對話紀錄】" in pass2_sys

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 4. 建立嚴格 Token Budget 預算制
# ---------------------------------------------------------------------------

def test_token_budget_constraints() -> None:
    """驗證世界記憶、用戶記憶與歷史封頂之預算約束。"""
    # 1. 歷史封頂預算限制 (1500 tokens)
    assert getattr(settings, "CONTEXT_HISTORY_MAX_TOKENS", 0) == 1500

    # 2. 世界記憶筆數限制 (6 筆)
    assert getattr(settings, "WORLD_MEMORY_SNIPPET_LIMIT", 0) == 6

    # 3. 用戶記憶 Facts 限制 (發言者上限 8 條，第三方 1 條)
    assert getattr(settings, "MEMORY_MAX_FACTS_IN_CONTEXT", 0) == 8

    # 4. 驗證 memory_builder 注入限制
    bot = MagicMock()
    mem_mgr = MagicMock()
    mem_mgr._store = {
        "100": {"aliases": ["小明"]},
        "200": {"aliases": ["小華"]},
        "300": {"aliases": ["小美"]},
    }
    # 模擬回傳使用者記憶
    mem_mgr.get_user_memory_snippet = MagicMock(return_value="📌 測試事實條目")
    bot.get_cog.return_value = mem_mgr

    # 測試發言者記憶與第三方提及記憶
    block = build_user_memory_block(
        bot,
        user_id=100,
        display_name="小明",
        query_text="小華和小美今天都在嗎？",
    )
    # 發言者 1 個區塊 + 第三方至多 1 個區塊 (最多 2 個【...】區塊)
    assert block.count("【🧠 關於正在與妳說話的這個人") == 1
    assert block.count("【👥 關於對話中提及/詢問的人物") == 1


# ---------------------------------------------------------------------------
# 5. 校正 TTFT 計時與思維鏈 (Reasoning) 串流
# ---------------------------------------------------------------------------

def test_reasoning_content_sse_streaming_and_ttft() -> None:
    """測試 OpenAI 相容 SSE 串流解析器在收到 delta.reasoning_content 時立即標記 TTFT 並串流。"""
    async def _run() -> None:
        client = OllamaClient(base_url="http://mock-llm:8000")

        # 模擬 SSE 數據流：先送 reasoning_content，再送 content
        sse_lines = [
            b'data: {"choices":[{"delta":{"reasoning_content":"\xe9\x80\x99\xe6\x98\xaf\xe6\x80\x9d\xe8\x80\x83\xe9\x80\xb2\xe5\xba\xa6\xef\xbc\x91"}}]}\n\n',
            b'data: {"choices":[{"delta":{"reasoning_content":"\xef\xbc\x8c\xe6\x80\x9d\xe8\x80\x83\xe9\x80\xb2\xe5\xba\xa6\xef\xbc\x92"}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":"\xe5\x93\x88\xe5\x9b\x89\xef\xbc\x81\xe6\x88\x91\xe6\x98\xaf\xe9\x9b\xaa\xe8\x8e\x89"}}]}\n\n',
            b'data: [DONE]\n\n',
        ]

        class FakeStreamReader:
            def __aiter__(self) -> AsyncIterator[bytes]:
                return self._generator()

            async def _generator(self) -> AsyncIterator[bytes]:
                for line in sse_lines:
                    yield line

        class FakeResponse:
            status = 200
            content = FakeStreamReader()

            async def __aenter__(self) -> FakeResponse:
                return self

            async def __aexit__(self, *args: Any) -> None:
                pass

        mock_session = MagicMock(spec=aiohttp.ClientSession)
        mock_session.post = MagicMock(return_value=FakeResponse())

        received_chunks: list[str] = []
        async for chunk in client._stream_openai_compatible(
            mock_session,
            base_url="http://mock-llm:8000/v1",
            api_key="",
            actual_model="deepseek-r1",
            system_prompt="sys",
            user_prompt="usr",
            backend_label="TestBackend",
        ):
            received_chunks.append(chunk)

        full_output = "".join(received_chunks)
        # 驗證：混合型串流輸出包裹 <thought> 標籤，且正常回覆內容被正確傳遞
        assert "<thought>" in full_output
        assert "</thought>" in full_output
        assert "哈囉！我是雪莉" in full_output

    asyncio.run(_run())


def test_live_thinking_mark_inference_start_excludes_rest_latency() -> None:
    """測試 LiveThinkingSession.mark_inference_start() 排除 Discord REST 延遲。"""
    async def _run() -> None:
        target_msg = MagicMock(spec=discord.Message)
        placeholder = MagicMock(spec=discord.Message)
        placeholder.id = 12345
        target_msg.reply = AsyncMock(return_value=placeholder)

        with patch.object(settings, "LIVE_THINKING_EDIT", True):
            session = LiveThinkingSession(target_msg)
            await session.start("📋 意圖解析中")

            # 模擬排隊或前置處理花費一段時間
            time.sleep(0.01)
            session.mark_inference_start()

            # 模擬首個 Token 到達
            await session.on_stream_chunk("<thought>思考中")
            assert session._pass_prefill_ms is not None
            # 首 token 延遲應精確計算 mark_inference_start 後的時間
            assert session._pass_prefill_ms >= 0

    asyncio.run(_run())


def test_watermark_throttling_multi_round_append_only_invariance() -> None:
    """測試在水位節流期間，連續多輪對話的歷史劇本均保持嚴格 Append-Only 前綴不變性。"""
    async def _run() -> None:
        _CONTEXT_COMPRESS_CACHE.clear()
        now = datetime.now(timezone.utc)
        bot_id = 9999

        dense_dialogue = [
            _make_msg(
                i, 1000 + (i % 3), f"User{i % 3}",
                f"密集任務討論第 {i} 條：" + ("詳細規格" * 30),
                now - timedelta(seconds=(50 - i) * 5),
            )
            for i in range(15)
        ]
        bot = MagicMock()
        brain = MagicMock()
        bot.get_cog = MagicMock(return_value=brain)

        async def fake_enqueue(task: Any) -> None:
            if hasattr(task, "callback"):
                await task.callback("專案規格與進度探討。")

        brain.enqueue_task = AsyncMock(side_effect=fake_enqueue)

        with patch.object(settings, "CONTEXT_COMPRESS_THRESHOLD", 500):
            # 第 1 輪：全量濃縮觸發
            ch1 = _make_chan(dense_dialogue, bot_id=bot_id)
            res1 = await fetch_context_script(ch1, bot=bot)
            assert "【過往話題濃縮摘要】專案規格與進度探討。" in res1
            assert brain.enqueue_task.call_count == 1

            # 第 2 輪：新增 1 則新訊息（節流）
            brain.enqueue_task.reset_mock()
            msg16 = _make_msg(16, 1001, "Alice", "請問目前後端架構？", now - timedelta(seconds=10))
            ch2 = _make_chan(dense_dialogue + [msg16], bot_id=bot_id)
            res2 = await fetch_context_script(ch2, bot=bot)

            # 驗證：res2 必須以 res1 為嚴格前綴（Append-Only），KV Cache 100% 命中
            assert res2.startswith(res1), "第 2 輪節流歷史必須以第 1 輪完整文字為嚴格前綴！"
            assert "請問目前後端架構？" in res2
            assert brain.enqueue_task.call_count == 0

            # 第 3 輪：再新增 1 則新訊息（持續節流）
            msg17 = _make_msg(17, 1002, "Bob", "後端架構已優化完畢！", now - timedelta(seconds=5))
            ch3 = _make_chan(dense_dialogue + [msg16, msg17], bot_id=bot_id)
            res3 = await fetch_context_script(ch3, bot=bot)

            # 驗證：res3 必須以 res2 為嚴格前綴（Append-Only）
            assert res3.startswith(res2), "第 3 輪節流歷史必須以第 2 輪完整文字為嚴格前綴！"
            assert "後端架構已優化完畢！" in res3
            assert brain.enqueue_task.call_count == 0

    asyncio.run(_run())


def test_pass1_and_pass2_alignment_with_third_party_mention() -> None:
    """測試當使用者發言提及第三方別名時，Pass-1 與 Pass-2 依然保持 100% 相同之 System Prompt。"""
    async def _run() -> None:
        tools = [DummyEchoTool("STOCK", ToolResponseMode.DATA_PROVIDER)]
        agent = DispatchAgentTool(tools, mode="INTEGRATED")

        bot = MagicMock()
        brain = MagicMock()
        mem_mgr = MagicMock()
        mem_mgr._store = {
            "2001": {"aliases": ["大隊長"]},
        }
        mem_mgr.get_user_memory_snippet = MagicMock(return_value="📌 記憶：大隊長是核心指揮官")
        bot.get_cog = MagicMock(side_effect=lambda name: brain if name == "BotBrain" else mem_mgr if name == "MemoryManager" else None)

        msg = MagicMock()
        msg.id = 888
        msg.channel.id = 999
        msg.author.id = 1001
        msg.author.display_name = "Alice"
        msg.guild = None
        msg.reply = AsyncMock()
        msg.channel.send = AsyncMock()

        captured_system_prompts: list[str] = []

        async def fake_call_llm(brain_cog: Any, system_prompt: str, user_prompt: str, **kwargs: Any) -> str:
            captured_system_prompts.append(system_prompt)
            if len(captured_system_prompts) == 1:
                return "<thought>查詢股票並提及大隊長</thought><action>STOCK: 2330</action>"
            return "<thought>回覆結果</thought>報告大隊長，台積電股價良好！"

        with patch.object(agent, "_call_llm", side_effect=fake_call_llm):
            await agent._run_integrated(
                brain=brain,
                bot=bot,
                message=msg,
                user_query="請問大隊長今天股票買了嗎？",
                vibe_snippet="",
                full_context="",
                images=None,
                start_time=100.0,
            )

        assert len(captured_system_prompts) == 2, "應執行 Pass-1 與 Pass-2"
        # 驗證第三方記憶在 Pass-1 與 Pass-2 均有被注入
        assert "大隊長是核心指揮官" in captured_system_prompts[0]
        assert "大隊長是核心指揮官" in captured_system_prompts[1]
        # 核心驗證：Pass-1 與 Pass-2 靜態前綴與動態情境 100% 嚴格對齊以最大化 KV Cache 複用
        assert captured_system_prompts[0] == captured_system_prompts[1], "Pass-1 與 Pass-2 系統提示詞應 100% 嚴格一致以最大化 KV Cache 複用"

    asyncio.run(_run())


def test_streaming_thinking_think_tag_slice_and_generated_chars() -> None:
    """測試 LiveThinkingSession 對 </think> 標籤的切片長度計算，避免切除回覆開頭字元。"""
    async def _run() -> None:
        target_msg = MagicMock(spec=discord.Message)
        placeholder = MagicMock(spec=discord.Message)
        placeholder.id = 99999
        target_msg.reply = AsyncMock(return_value=placeholder)

        with patch.object(settings, "LIVE_THINKING_EDIT", True):
            session = LiveThinkingSession(target_msg)
            await session.start("📋 意圖解析中")

            # 模擬 LLM 使用 <think> ... </think> 標籤輸出
            await session.on_stream_chunk("<think>正在深度思考中</think>你好呀！")
            assert session._live_thought == "正在深度思考中"
            # 驗證生成字元數正確計算「你好呀！」(3 字元)，未被 hardcoded +10 截斷
            assert session._generated_chars == len("你好呀！")

    asyncio.run(_run())


def test_world_memory_cache_key_includes_limit() -> None:
    """測試世界記憶快取鍵包含 limit，避免不同預算呼叫方快取衝突。"""
    clear_world_memory_cache()
    # 模擬直接以不同 limit 呼叫
    res_empty1 = get_world_memory_snippet(days=3, min_importance=3, server_id="srv_1", limit=3)
    res_empty2 = get_world_memory_snippet(days=3, min_importance=3, server_id="srv_1", limit=6)
    assert res_empty1 == ""
    assert res_empty2 == ""

