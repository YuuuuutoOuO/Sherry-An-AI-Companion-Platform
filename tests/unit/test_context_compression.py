"""
tests/unit/test_context_compression.py
======================================
對話上下文濃縮與截斷機制單元測試。
涵蓋 fetch_context_script 短對話、長對話濃縮、快取 TTL、以及不對稱記憶閹割優先權。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord

import settings
from handlers.social.utils import (
    _CONTEXT_COMPRESS_CACHE,
    fetch_context_script,
    _estimate_tokens,
)


def _make_fake_message(
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


def _make_fake_channel(messages: list[discord.Message], bot_id: int = 9999) -> Any:
    channel = MagicMock()
    channel.id = 12345
    guild = MagicMock()
    guild.me = MagicMock()
    guild.me.id = bot_id
    channel.guild = guild

    async def fake_history(limit: int = 30, before: Any = None, oldest_first: bool = False):
        # 還原 discord 語意：history(oldest_first=False) 由新至舊回傳
        ordered = sorted(messages, key=lambda m: m.created_at, reverse=True)
        for m in ordered[:limit]:
            yield m

    channel.history = fake_history
    return channel


def test_format_msg_append_only_invariance() -> None:
    """測試對話歷史採 Append-Only 嚴格字串不變性，避免動態破壞歷史導致 KV Cache 失效。"""
    async def _run() -> None:
        now = datetime.now(timezone.utc)
        bot_id = 9999
        long_old_bot_reply = "這是很長很長的雪莉歷史發言..." * 50  # 超過 300 字

        # 構造 15 則訊息
        messages = [
            _make_fake_message(100 + i, 1001, f"User{i}", f"Hello {i}", now - timedelta(seconds=i * 10))
            for i in range(12)
        ]
        # 雪莉的超長訊息
        old_bot_msg = _make_fake_message(50, bot_id, "雪莉", long_old_bot_reply, now - timedelta(seconds=200))
        messages.append(old_bot_msg)

        channel = _make_fake_channel(messages, bot_id=bot_id)

        result = await fetch_context_script(channel)

        # 驗證：訊息採純函數確定性格式化（>300字統一截斷），絕不出現動態破壞字串（已被系統掃除）
        assert "已被系統掃除" not in result
        assert "這是很長很長的雪莉歷史發言" in result
        assert "...(過長截斷)" in result

    asyncio.run(_run())


def test_fetch_context_script_short_conversation_not_compressed() -> None:
    """測試一般短對話（字數未達門檻）原樣回傳，不觸發濃縮。"""
    async def _run() -> None:
        now = datetime.now(timezone.utc)
        bot_id = 9999
        messages = [
            _make_fake_message(1, 1001, "Alice", "早安雪莉！", now - timedelta(seconds=10)),
            _make_fake_message(2, bot_id, "雪莉", "早安呀～今天有什麼委託嗎？", now - timedelta(seconds=5)),
        ]
        channel = _make_fake_channel(messages, bot_id=bot_id)

        result = await fetch_context_script(channel)

        assert "早安雪莉！" in result
        assert "早安呀～今天有什麼委託嗎？" in result
        assert "【過往話題濃縮摘要】" not in result

    asyncio.run(_run())


def test_fetch_context_script_compression_and_cache() -> None:
    """測試超長對話觸發主力模型濃縮，且結果被寫入 60 秒記憶體快取。"""
    async def _run() -> None:
        _CONTEXT_COMPRESS_CACHE.clear()
        now = datetime.now(timezone.utc)
        bot_id = 9999

        # 構造超過 CONTEXT_COMPRESS_THRESHOLD (2000字) 的密集長對話
        long_dialogue = [
            _make_fake_message(
                i, 1000 + (i % 3), f"User{i % 3}",
                f"這是一段詳細的討論細節第 {i} 條：" + ("很長很長" * 30),
                now - timedelta(seconds=(20 - i) * 5)
            )
            for i in range(20)
        ]
        channel = _make_fake_channel(long_dialogue, bot_id=bot_id)

        bot = MagicMock()
        brain = MagicMock()
        bot.get_cog = MagicMock(return_value=brain)

        # Mock enqueue_task 讓 callback 回傳濃縮摘要
        async def fake_enqueue(task: Any) -> None:
            if hasattr(task, "callback"):
                await task.callback("大家正在討論任務規劃細節與進度。")

        brain.enqueue_task = AsyncMock(side_effect=fake_enqueue)

        with patch.object(settings, "CONTEXT_COMPRESS_THRESHOLD", 500):
            result = await fetch_context_script(channel, bot=bot)

            assert "【過往話題濃縮摘要】大家正在討論任務規劃細節與進度。" in result
            assert 12345 in _CONTEXT_COMPRESS_CACHE

            # 第二次呼叫應命中快取
            brain.enqueue_task.reset_mock()
            result_cached = await fetch_context_script(channel, bot=bot)
            assert result_cached == result
            assert brain.enqueue_task.call_count == 0  # 未重新發送推論任務

    asyncio.run(_run())


def test_fetch_context_script_temporary_card_lifecycle() -> None:
    """測試長對話濃縮時發送提示卡片，並在濃縮完成後自動刪除該卡片留言。"""
    async def _run() -> None:
        _CONTEXT_COMPRESS_CACHE.clear()
        now = datetime.now(timezone.utc)
        bot_id = 9999

        long_dialogue = [
            _make_fake_message(
                i, 1000 + (i % 3), f"User{i % 3}",
                f"這是一段詳細的討論細節第 {i} 條：" + ("很長很長" * 30),
                now - timedelta(seconds=(20 - i) * 5)
            )
            for i in range(20)
        ]
        channel = _make_fake_channel(long_dialogue, bot_id=bot_id)

        # 模擬 channel.send 與卡片刪除
        card_msg_mock = MagicMock(spec=discord.Message)
        card_msg_mock.delete = AsyncMock()
        channel.send = AsyncMock(return_value=card_msg_mock)

        # 模擬使用者觸發的 before_message
        before_msg = MagicMock(spec=discord.Message)
        before_msg.reply = AsyncMock(return_value=card_msg_mock)

        bot = MagicMock()
        brain = MagicMock()
        bot.get_cog = MagicMock(return_value=brain)

        async def fake_enqueue(task: Any) -> None:
            if hasattr(task, "callback"):
                await task.callback("話題摘要：任務討論中。")

        brain.enqueue_task = AsyncMock(side_effect=fake_enqueue)

        with patch.object(settings, "CONTEXT_COMPRESS_THRESHOLD", 500):
            result = await fetch_context_script(channel, before_message=before_msg, bot=bot)
            assert "【過往話題濃縮摘要】話題摘要：任務討論中。" in result

            # 驗證 1：有透過 reply 或 send 發送包含 Embed 的提示卡片
            before_msg.reply.assert_called_once()
            called_embed = before_msg.reply.call_args.kwargs.get("embed")
            assert called_embed is not None
            assert "對話上下文整理中" in called_embed.title

            # 驗證 2：在濃縮結束後呼叫了 delete 刪除卡片留言
            card_msg_mock.delete.assert_called_once()

    asyncio.run(_run())


def test_fetch_context_script_watermark_throttles_recompression() -> None:
    """測試水位節流：壓縮後若新訊息累積不足門檻，應重用既有摘要並疊加新訊息，不發送全量 raw 歷史。"""
    async def _run() -> None:
        _CONTEXT_COMPRESS_CACHE.clear()
        now = datetime.now(timezone.utc)
        bot_id = 9999

        dense = [
            _make_fake_message(
                i, 1000 + (i % 3), f"User{i % 3}",
                f"這是一段詳細的討論細節第 {i} 條：" + ("很長很長" * 30),
                now - timedelta(seconds=(30 - i) * 5)
            )
            for i in range(20)
        ]
        channel = _make_fake_channel(dense, bot_id=bot_id)

        bot = MagicMock()
        brain = MagicMock()
        bot.get_cog = MagicMock(return_value=brain)

        async def fake_enqueue(task: Any) -> None:
            if hasattr(task, "callback"):
                await task.callback("大家正在討論任務規劃細節與進度。")

        brain.enqueue_task = AsyncMock(side_effect=fake_enqueue)

        with patch.object(settings, "CONTEXT_COMPRESS_THRESHOLD", 500):
            first = await fetch_context_script(channel, bot=bot)
            assert "【過往話題濃縮摘要】" in first

            # 只新增 1 則新訊息（低於水位門檻 8）→ 應重用上一輪摘要並疊加新訊息，不重新呼叫 LLM 濃縮
            brain.enqueue_task.reset_mock()
            new_msg = _make_fake_message(
                200, 1001, "Alice", "這是一則熱騰騰的新訊息",
                now - timedelta(seconds=1),
            )
            channel2 = _make_fake_channel(dense + [new_msg], bot_id=bot_id)
            result = await fetch_context_script(channel2, bot=bot)

            # 驗證：正確取出上一輪快取摘要，絕不退回全量 raw 歷史
            assert "【過往話題濃縮摘要】" in result
            assert "大家正在討論任務規劃細節與進度。" in result
            assert "這是一則熱騰騰的新訊息" in result
            assert brain.enqueue_task.call_count == 0

    asyncio.run(_run())


def test_fetch_context_script_hard_cap_trims_without_bot() -> None:
    """測試硬性封頂：即使濃縮不可用（無 bot），歷史區塊也不得超過 token 預算，且保住最新訊息。"""
    async def _run() -> None:
        _CONTEXT_COMPRESS_CACHE.clear()
        now = datetime.now(timezone.utc)
        bot_id = 9999

        dense = [
            _make_fake_message(
                i, 1000 + (i % 3), f"User{i % 3}",
                f"這是一段非常詳細的長篇討論內容第 {i} 條：" + ("很長很長" * 40),
                now - timedelta(seconds=(30 - i) * 5)
            )
            for i in range(30)
        ]
        # 最新一則的內容特別標記，驗證封頂裁切後仍被保留
        dense[29] = _make_fake_message(
            29, 1001, "Alice", "【最新最重要的即時訊息】",
            now - timedelta(seconds=5),
        )
        channel = _make_fake_channel(dense, bot_id=bot_id)

        with patch.object(settings, "CONTEXT_COMPRESS_THRESHOLD", 500), \
             patch.object(settings, "CONTEXT_HISTORY_MAX_TOKENS", 1000):
            result = await fetch_context_script(channel)  # 不帶 bot → 濃縮不可用

            assert _estimate_tokens(result) <= 1000
            assert "過往話題濃縮摘要" not in result
            assert "【最新最重要的即時訊息】" in result

    asyncio.run(_run())
