"""tests/unit/test_reply_quote_context.py
=========================================
驗證「回覆引用」的可見性：

1. ``reference.resolved`` 有效時直接使用，不多打 Discord API。
2. resolved 為 None（機器人重啟／回覆舊訊息）時，必須主動 fetch 補抓。
3. 原訊息已刪除或抓取失敗時，給模型一段誠實的提示而不是假裝沒有引用。
4. 引用內容為 Embed／附件時仍能轉成可讀文字。
5. router 會把補抓到的引用內容真正塞進送給模型的 full_context。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from handlers.social.utils import build_reply_quote_context, resolve_reply_reference

_QUOTED_TEXT = "PEPE這個死青蛙再漲三小的，孫哥買爽沒"


def _make_quoted_message(
    *,
    content: str = _QUOTED_TEXT,
    author_name: str = "Yuuto",
    author_id: int = 111,
    message_id: int = 987654,
) -> MagicMock:
    msg = MagicMock(spec=discord.Message)
    msg.id = message_id
    msg.content = content
    msg.clean_content = content
    msg.author.display_name = author_name
    msg.author.name = author_name
    msg.author.id = author_id
    msg.embeds = []
    msg.attachments = []
    msg.stickers = []
    msg.created_at = None
    return msg


def _make_user_message(*, content: str = "@雪莉 所以誰再買") -> MagicMock:
    message = MagicMock()
    message.id = 123
    message.author.id = 111
    message.author.display_name = "Yuuto"
    message.channel.id = 456
    message.clean_content = content
    message.content = content
    message.attachments = []
    message.add_reaction = AsyncMock()
    message.reference.message_id = 987654
    message.reference.channel_id = 456
    message.reference.type = None
    message.reference.resolved = None
    message.channel.fetch_message = AsyncMock()
    return message


def test_uses_resolved_message_without_extra_fetch() -> None:
    async def _run() -> None:
        ref = _make_quoted_message()
        message = _make_user_message()
        message.reference.resolved = ref

        quote = await build_reply_quote_context(message, bot_user_id=999)

        assert _QUOTED_TEXT in quote
        assert "原訊息作者：Yuuto" in quote
        assert "同一個話題" in quote
        message.channel.fetch_message.assert_not_awaited()

    asyncio.run(_run())


def test_fetches_reference_when_resolved_is_missing() -> None:
    """回歸測試：重啟後 resolved 為 None，仍要靠 fetch 補回引用內容。"""

    async def _run() -> None:
        ref = _make_quoted_message()
        message = _make_user_message()
        message.channel.fetch_message = AsyncMock(return_value=ref)

        quote = await build_reply_quote_context(message, bot_user_id=999)

        message.channel.fetch_message.assert_awaited_once_with(ref.id)
        assert _QUOTED_TEXT in quote
        assert "原訊息作者：Yuuto" in quote

    asyncio.run(_run())


def test_deleted_reference_returns_honest_hint() -> None:
    async def _run() -> None:
        message = _make_user_message()
        message.reference.resolved = discord.DeletedReferencedMessage(
            discord.MessageReference(message_id=987654, channel_id=456)
        )

        quote = await build_reply_quote_context(message, bot_user_id=999)

        assert "讀不到被引用的原訊息" in quote
        assert "反問" in quote
        message.channel.fetch_message.assert_not_awaited()

    asyncio.run(_run())


def test_fetch_failure_returns_honest_hint() -> None:
    async def _run() -> None:
        message = _make_user_message()
        message.channel.fetch_message = AsyncMock(side_effect=discord.NotFound(MagicMock(), "Unknown Message"))

        assert await resolve_reply_reference(message) is None
        quote = await build_reply_quote_context(message, bot_user_id=999)
        assert "讀不到被引用的原訊息" in quote

    asyncio.run(_run())


def test_quote_of_own_message_is_labelled_as_self() -> None:
    async def _run() -> None:
        ref = _make_quoted_message(content="現在不趕快開空單嗎？", author_name="雪莉", author_id=999)
        message = _make_user_message()
        message.reference.resolved = ref

        quote = await build_reply_quote_context(message, bot_user_id=999)

        assert "原訊息作者：雪莉妳自己" in quote
        assert "妳自己剛才說過的話" in quote

    asyncio.run(_run())


def test_embed_only_quote_is_still_readable() -> None:
    async def _run() -> None:
        ref = _make_quoted_message(content="")
        ref.embeds = [MagicMock(title="PEPE 突破新高", description="24h 漲幅 38%")]
        message = _make_user_message()
        message.reference.resolved = ref

        quote = await build_reply_quote_context(message, bot_user_id=999)

        assert "PEPE 突破新高" in quote
        assert "24h 漲幅 38%" in quote

    asyncio.run(_run())


def test_no_reference_returns_empty_string() -> None:
    async def _run() -> None:
        message = _make_user_message()
        message.reference = None
        assert await build_reply_quote_context(message, bot_user_id=999) == ""

    asyncio.run(_run())


def test_router_injects_fetched_quote_into_full_context() -> None:
    """端到端：resolved 失效時，router 送給模型的 full_context 仍含引用原文。"""

    async def _run() -> None:
        ref = _make_quoted_message()
        message = _make_user_message()
        message.channel.fetch_message = AsyncMock(return_value=ref)
        message.guild = None

        bot = MagicMock()
        bot.user.id = 999
        bot.user.display_name = "雪莉"
        bot.get_cog = MagicMock(return_value=MagicMock())

        with patch("handlers.social.router.fetch_context_script", new_callable=AsyncMock, return_value=""), \
             patch("handlers.social.router.rate_limiter.is_allowed", return_value=True), \
             patch(
                 "handlers.social.dispatch_agent.DispatchAgentTool.execute",
                 new_callable=AsyncMock,
             ) as mock_execute:
            from handlers.social.router import route_and_execute_tag

            await route_and_execute_tag(bot, message)

        # route_and_execute_tag 以位置參數傳遞 full_context（bot, message, query, vibe, context）。
        full_context = mock_execute.call_args.args[4]
        assert _QUOTED_TEXT in full_context
        assert "同一個話題" in full_context

    asyncio.run(_run())
