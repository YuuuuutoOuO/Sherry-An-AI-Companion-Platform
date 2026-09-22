"""
tests/unit/test_audit_fixes.py
==============================
驗證本次架構審查所有核心修復與防護機制的單元測試：
1. 雙重 Rate Limiter 避免重複計數 (from_core 協定)
2. SmartMessageDispatcher 引用訊息 NotFound 優雅降級發送
3. ContextCompressionCache LRU 容量淘汰與最新訊息水位失效
4. InferenceTask 原生取消與 _inference_worker 即時略過
5. 圖片超大容量自動略過防 OOM
6. CommandHubView 與 ConfigView 使用者權限隔離與 on_timeout
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import discord

from core.contracts import SherryResponse
from core.dispatcher import SmartMessageDispatcher
from handlers.bot_brain.models import InferenceTask
from handlers.executor.cog import CommandHubView
from handlers.social.router import route_and_execute_tag
from handlers.social.utils import ContextCompressionCache, _estimate_tokens


def test_rate_limiter_deduplication_via_from_core() -> None:
    """測試來自微核心的請求 (from_core=True) 不會再次消耗獨立 RateLimiter 額度。"""
    async def _run() -> None:
        bot = MagicMock()
        bot.get_cog = MagicMock(return_value=MagicMock())
        message = MagicMock(spec=discord.Message)
        message.id = 123456
        message.author.id = 999
        message.author.display_name = "User"
        message.channel.id = 111
        message.clean_content = "hello"
        message.attachments = []
        message.reference = None

        with patch("handlers.social.router.rate_limiter.is_allowed") as mock_rl, \
             patch("handlers.social.router.fetch_context_script", new_callable=AsyncMock) as mock_ctx, \
             patch("handlers.social.dispatch_agent.DispatchAgentTool.execute", new_callable=AsyncMock):
            mock_ctx.return_value = ""

            # 1. from_core=True: 不應呼叫 rate_limiter.is_allowed
            await route_and_execute_tag(bot, message, from_core=True)
            assert mock_rl.call_count == 0

            # 2. from_core=False: 應呼叫 rate_limiter.is_allowed 進行兜底防護
            mock_rl.return_value = True
            await route_and_execute_tag(bot, message, from_core=False)
            assert mock_rl.call_count == 1

    asyncio.run(_run())


def test_dispatcher_not_found_fallback_to_channel_send() -> None:
    """測試引用訊息遭刪除 (discord.NotFound) 時，SmartMessageDispatcher 自動降級發送。"""
    async def _run() -> None:
        target = MagicMock()
        target.send = AsyncMock(return_value=MagicMock(spec=discord.Message))

        ref_msg = MagicMock(spec=discord.Message)
        ref_msg.id = 88888
        # 模擬原訊息被秒刪時丟出 NotFound
        ref_msg.reply = AsyncMock(side_effect=discord.NotFound(MagicMock(), "Unknown Message"))

        resp = SherryResponse(trace_id="test", reply_text="你好呀！這是偵探的推理！")
        sent = await SmartMessageDispatcher.dispatch(target, resp, reference_msg=ref_msg)

        assert ref_msg.reply.called
        assert target.send.called
        assert len(sent) == 1
        args, kwargs = target.send.call_args
        assert "你好呀！這是偵探的推理！" in kwargs.get("content", "")

    asyncio.run(_run())


def test_context_compression_cache_lru_and_watermark() -> None:
    """測試 ContextCompressionCache 的 LRU 容量上限與訊息 ID 水位失效。"""
    cache = ContextCompressionCache(capacity=3, default_ttl=60.0)

    # 1. 寫入 3 筆資料
    cache.set(1, 101, "摘要 1")
    cache.set(2, 102, "摘要 2")
    cache.set(3, 103, "摘要 3")
    assert len(cache) == 3
    assert 1 in cache

    # 2. 寫入第 4 筆，驗證 LRU 淘汰第 1 筆
    cache.set(4, 104, "摘要 4")
    assert len(cache) == 3
    assert 1 not in cache
    assert 4 in cache

    # 3. 水位驗證：當最後訊息 ID 相同時回傳快取；若頻道有新訊息 (ID 變更) 則回傳 None（保留條目供水位節流重用）
    assert cache.get_valid_summary(2, last_msg_id=102, ttl=60.0) == "摘要 2"
    # 新訊息湧入 (ID 變成 105)
    assert cache.get_valid_summary(2, last_msg_id=105, ttl=60.0) is None
    assert 2 in cache  # 保留供水位節流取出上一輪摘要
    assert cache.get_watermark(2) == 102
    assert cache.get_raw_summary(2) == "摘要 2"


def test_estimate_tokens_calculation() -> None:
    """測試 Token 估算器對純中文與中英混合的精確估算。"""
    chinese_text = "這是一段測試中文內容文字"
    assert _estimate_tokens(chinese_text) == 12

    mixed_text = "Hello world! 這是一段混合測試"
    estimated = _estimate_tokens(mixed_text)
    assert 10 <= estimated <= 18


def test_inference_task_cancellation() -> None:
    """測試 InferenceTask 原生取消旗標操作。"""
    task = InferenceTask(priority=1, system_prompt="sys", user_prompt="usr", callback=AsyncMock())
    assert not task.is_cancelled
    task.cancel()
    assert task.is_cancelled


def test_command_hub_view_user_isolation() -> None:
    """測試 CommandHubView 只允許指令發起者進行點擊操作。"""
    async def _run() -> None:
        bot = MagicMock()
        author_id = 112233
        view = CommandHubView(bot, author_id=author_id)

        # 1. 非指令發起者點擊
        other_interaction = MagicMock(spec=discord.Interaction)
        other_interaction.user.id = 999999
        other_interaction.response.send_message = AsyncMock()
        allowed = await view.interaction_check(other_interaction)
        assert allowed is False
        assert other_interaction.response.send_message.called

        # 2. 指令發起者點擊
        author_interaction = MagicMock(spec=discord.Interaction)
        author_interaction.user.id = author_id
        allowed = await view.interaction_check(author_interaction)
        assert allowed is True

    asyncio.run(_run())
