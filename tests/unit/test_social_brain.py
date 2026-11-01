"""tests/unit/test_social_brain.py
=================================
賽博群友大腦（內容生成層）的單元測試。

覆蓋範圍：
1. 結構化上下文：回覆關係、她最近說過的話、誰在跟誰講話。
2. 意圖分類：REPLY / JOIN / INITIATE。
3. Prompt 不再假設「妳剛剛開口說話」，四種意圖各有自己的規則。
4. 統一輸出契約：decision / score / thought / reaction / reply。
5. 生成失敗、分數不足一律 silent，且不得留下殘缺 reply。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from handlers.social.brain import (
    BrainIntent,
    BrainResult,
    ContextMessage,
    ConversationContext,
    build_brain_prompt,
    build_conversation_context,
    decide_intent,
    think,
)
from handlers.social.brain.engine import clean_reply

_GEN = "handlers.social.autonomy.generation.generate_text"


# ---------------------------------------------------------------------------
# 測試替身
# ---------------------------------------------------------------------------
class _Author:
    def __init__(self, author_id: str, name: str, *, bot: bool = False) -> None:
        self.id = int(author_id)
        self.display_name = name
        self.name = name
        self.bot = bot


class _Message:
    def __init__(
        self,
        message_id: int,
        author: _Author,
        content: str,
        *,
        reference: object | None = None,
        created_at: datetime | None = None,
    ) -> None:
        self.id = message_id
        self.author = author
        self.content = content
        self.clean_content = content
        self.mentions: list[object] = []
        self.reference = reference
        self.created_at = created_at or datetime.now(timezone.utc)
        self.embeds = []
        self.attachments = []


class _Reference:
    def __init__(self, message_id: int, resolved: object | None = None) -> None:
        self.message_id = message_id
        self.resolved = resolved


class _Channel:
    def __init__(self, messages: list[_Message], channel_id: int = 777) -> None:
        self.id = channel_id
        self.guild = None
        self._messages = messages

    def history(self, limit: int = 50, oldest_first: bool = False):
        async def _iter():
            # Discord 預設由新到舊。
            for msg in reversed(self._messages):
                yield msg

        return _iter()


def _bot(bot_id: int = 12345):
    bot = MagicMock()
    bot.user.id = bot_id
    return bot


def _ctx_for_prompts() -> ConversationContext:
    return ConversationContext(
        channel_id="777",
        messages=[
            ContextMessage(
                message_id="1",
                author_id="1",
                author_name="阿明",
                content="今天好累",
            ),
            ContextMessage(
                message_id="2",
                author_id="12345",
                author_name="雪莉",
                content="累就早點睡啊",
                is_self=True,
            ),
            ContextMessage(
                message_id="3",
                author_id="1",
                author_name="阿明",
                content="可是我還想聊天",
                reply_to_self=True,
            ),
        ],
        focus=ContextMessage(
            message_id="3",
            author_id="1",
            author_name="阿明",
            content="可是我還想聊天",
            reply_to_self=True,
        ),
    )


# ---------------------------------------------------------------------------
# 1. 結構化上下文
# ---------------------------------------------------------------------------
def test_context_marks_reply_to_sherry_and_keeps_self_lines() -> None:
    ctx = _ctx_for_prompts()

    assert ctx.focus_to_self is True
    assert ctx.recent_self_lines() == ["累就早點睡啊"]
    assert ctx.recent_self_lines(limit=0) == []
    assert ctx.recent_self_lines(limit=-3) == []
    assert ctx.participants == ["阿明"]
    transcript = ctx.render_transcript()
    assert "妳自己（雪莉）" in transcript
    assert "（回覆妳）" in transcript
    assert "對方直接回覆了妳剛才說的話" in ctx.render_focus_block()


async def test_build_context_from_fake_channel() -> None:
    bot = _bot()
    sherry = _Author("12345", "雪莉", bot=True)
    a = _Author("1", "阿明")

    messages = [
        _Message(1, a, "雪莉妳在嗎", created_at=datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)),
        _Message(2, sherry, "幹嘛啦", created_at=datetime(2026, 10, 30, 12, 1, tzinfo=timezone.utc)),
    ]
    focus = _Message(
        3,
        a,
        "沒事 只是想找妳聊天",
        reference=_Reference(2, messages[1]),
        created_at=datetime(2026, 10, 30, 12, 2, tzinfo=timezone.utc),
    )

    ctx = await build_conversation_context(_Channel(messages), focus_message=focus, bot=bot)
    assert ctx.channel_id == "777"
    assert ctx.focus is not None and ctx.focus.reply_to_self is True
    assert ctx.recent_self_lines() == ["幹嘛啦"]
    assert "妳自己（雪莉）" in ctx.render_transcript()


def test_context_falls_back_to_script_when_history_unavailable() -> None:
    channel = MagicMock()
    channel.id = 999
    channel.history = None

    ctx = asyncio.run(
        build_conversation_context(channel, bot=_bot(), fallback_script="群友：「哈囉」")
    )
    assert ctx.source == "fallback"
    assert ctx.render_transcript() == "群友：「哈囉」"


# ---------------------------------------------------------------------------
# 2. 意圖分類
# ---------------------------------------------------------------------------
def test_decide_intent_prefers_reply_then_join_then_initiate() -> None:
    assert decide_intent(to_sherry=True) is BrainIntent.REPLY
    assert decide_intent(continuation=True) is BrainIntent.REPLY
    assert decide_intent(has_recent_human=True) is BrainIntent.JOIN
    assert decide_intent(has_recent_human=False) is BrainIntent.INITIATE
    assert decide_intent(explicit="monologue") is BrainIntent.MONOLOGUE


# ---------------------------------------------------------------------------
# 3. Prompt 內容
# ---------------------------------------------------------------------------
def test_reply_prompt_does_not_assume_she_just_spoke() -> None:
    system_prompt, user_prompt = build_brain_prompt(
        BrainIntent.REPLY,
        head_persona="【雪莉人格】",
        context=_ctx_for_prompts(),
        to_sherry=True,
    )
    combined = f"{system_prompt}\n{user_prompt}"
    assert "妳剛剛在群組裡開口說話" not in combined
    assert "現在有人在跟妳說話" in system_prompt
    assert "對方直接回覆了妳剛才說的話" in user_prompt
    assert "妳最近在頻道說過的話" in user_prompt
    assert "累就早點睡啊" in user_prompt


def test_join_prompt_uses_threshold_and_allows_silence() -> None:
    ctx = ConversationContext(
        channel_id="777",
        messages=[
            ContextMessage(
                message_id="1",
                author_id="1",
                author_name="阿明",
                content="你們覺得這部新番怎樣",
            ),
            ContextMessage(
                message_id="2",
                author_id="2",
                author_name="小美",
                content="作畫很穩 可是節奏有點慢",
            ),
        ],
        focus=ContextMessage(
            message_id="2",
            author_id="2",
            author_name="小美",
            content="作畫很穩 可是節奏有點慢",
        ),
    )
    system_prompt, user_prompt = build_brain_prompt(
        BrainIntent.JOIN,
        head_persona="【雪莉人格】",
        context=ctx,
        threshold=7,
    )
    assert "插話門檻是 7 分" in system_prompt
    assert "decision=silent" in system_prompt
    assert "群友之間的對話" in user_prompt


def test_initiate_prompt_includes_silence_and_stats() -> None:
    ctx = _ctx_for_prompts()
    ctx.silence_minutes = 42
    system_prompt, user_prompt = build_brain_prompt(
        BrainIntent.INITIATE,
        head_persona="【雪莉人格】",
        context=ctx,
        stats={"time_period": "深夜修仙", "hour": 2, "bowls_of_rice": 3},
    )
    assert "主動開話題" in system_prompt
    assert "安靜了約 42 分鐘" in user_prompt
    assert "深夜修仙" in user_prompt
    assert "3 碗賽博白飯" in user_prompt


def test_dynamic_context_stays_out_of_system_prompt() -> None:
    """同一人格下，逐字稿與對象應該放在 user_prompt，保護 KV cache。"""
    system_prompt, user_prompt = build_brain_prompt(
        BrainIntent.REPLY,
        head_persona="【雪莉人格】",
        context=_ctx_for_prompts(),
    )
    assert "可是我還想聊天" not in system_prompt
    assert "可是我還想聊天" in user_prompt


# ---------------------------------------------------------------------------
# 4. 統一輸出契約
# ---------------------------------------------------------------------------
async def test_think_returns_silent_on_empty_generation() -> None:
    with patch(_GEN, AsyncMock(return_value="")):
        result = await think(
            MagicMock(),
            intent=BrainIntent.REPLY,
            head_persona="p",
            context=_ctx_for_prompts(),
        )
    assert result.decision == "silent"
    assert result.should_speak is False
    assert result.reply == ""


async def test_think_reply_parses_json_contract() -> None:
    raw = '{"decision":"speak","thought":"他在撒嬌","reply":"「那就再聊一下下」","reaction":""}'
    with patch(_GEN, AsyncMock(return_value=raw)):
        result = await think(
            MagicMock(),
            intent=BrainIntent.REPLY,
            head_persona="p",
            context=_ctx_for_prompts(),
        )
    assert result.should_speak is True
    assert result.reply == "那就再聊一下下"
    assert result.thought == "他在撒嬌"


async def test_think_reply_accepts_plain_text_fallback() -> None:
    with patch(_GEN, AsyncMock(return_value="哼 才不是咧")):
        result = await think(
            MagicMock(),
            intent=BrainIntent.REPLY,
            head_persona="p",
            context=_ctx_for_prompts(),
        )
    assert result.should_speak is True
    assert result.reply == "哼 才不是咧"


async def test_think_join_below_threshold_is_silent_and_clears_reply() -> None:
    raw = '{"decision":"speak","score":4,"thought":"有點想接","reply":"笑死"}'
    with patch(_GEN, AsyncMock(return_value=raw)):
        result = await think(
            MagicMock(),
            intent=BrainIntent.JOIN,
            head_persona="p",
            context=_ctx_for_prompts(),
            threshold=7,
        )
    assert result.decision == "silent"
    assert result.reply == ""
    assert result.score == 4


async def test_think_join_at_threshold_speaks() -> None:
    raw = '{"decision":"speak","score":8,"thought":"忍不住","reply":"這操作太狠了吧"}'
    with patch(_GEN, AsyncMock(return_value=raw)):
        result = await think(
            MagicMock(),
            intent=BrainIntent.JOIN,
            head_persona="p",
            context=_ctx_for_prompts(),
            threshold=7,
        )
    assert result.should_speak is True
    assert result.reply == "這操作太狠了吧"


async def test_think_initiate_respects_explicit_silent() -> None:
    raw = '{"decision":"silent","thought":"想不到要說什麼","reply":""}'
    with patch(_GEN, AsyncMock(return_value=raw)):
        result = await think(
            MagicMock(),
            intent=BrainIntent.INITIATE,
            head_persona="p",
            context=_ctx_for_prompts(),
        )
    assert result.decision == "silent"
    assert result.should_speak is False


def test_clean_reply_strips_quotes_and_fence() -> None:
    assert clean_reply("「哼，才不是呢。」") == "哼，才不是呢"
    assert clean_reply("```json\n哈哈\n```") == "哈哈"


def test_brain_result_dict_has_stable_keys() -> None:
    result = BrainResult(intent=BrainIntent.JOIN, decision="silent", score=3)
    payload = result.to_dict()
    for key in ("intent", "decision", "reply", "thought", "reaction", "score"):
        assert key in payload


# ---------------------------------------------------------------------------
# 5. 接線：窺屏插話走 JOIN
# ---------------------------------------------------------------------------
async def test_lurk_eval_uses_brain_join_and_stays_silent(tmp_path) -> None:
    from handlers.care.repository import CareRepository
    from handlers.care.surfer import CareSurfer

    repo = CareRepository(db_path=tmp_path / "lurk.db")
    repo.update_surfer_config(
        enabled=True,
        chat_reply_enabled=True,
        sandbox_channel_id="1",
        active_hour_start=0,
        active_hour_end=23,
        interject_score_threshold=7,
    )

    broadcaster = MagicMock()
    broadcaster.broadcast = AsyncMock()
    surfer = CareSurfer(repository=repo, broadcaster=broadcaster)

    channel = MagicMock()
    channel.id = 1
    channel.guild = None

    target = MagicMock()
    target.id = 10
    target.author.id = 5
    target.author.bot = False
    target.add_reaction = AsyncMock()

    silent = BrainResult(intent=BrainIntent.JOIN, decision="silent", score=3)
    with patch("handlers.care.surfer.build_dynamic_persona", return_value=("p", "")), patch(
        "handlers.care.surfer.think", AsyncMock(return_value=silent)
    ) as brain_think, patch(
        "handlers.care.surfer.send_cyber_bubbles", AsyncMock(return_value=[])
    ) as send_bubbles:
        result = await surfer.evaluate_channel_lurk(
            MagicMock(),
            channel,
            "群友：「哈囉」",
            target_message=target,
        )

    assert brain_think.await_args.kwargs["intent"] is BrainIntent.JOIN
    assert result["acted"] is False
    assert send_bubbles.await_count == 0
