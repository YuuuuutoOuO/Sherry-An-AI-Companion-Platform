"""插話評分在上游推論失敗時的降級行為測試。

評分僅作為「要不要插話」的門檻判斷，當 provider 內容過濾或推論失敗時
應安全地略過插話，而非讓流程丟出例外或亂猜分數。
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

from handlers.social import interject as ij


def _make_channel() -> MagicMock:
    channel = MagicMock()
    channel.id = 4242
    channel.name = "test-channel"
    return channel


def _ready_state() -> MagicMock:
    state = MagicMock()
    state.can_interject.return_value = True
    return state


class _FakeBot:
    """假 discord.Bot：只提供插話流程會碰到的屬性。"""

    def __init__(self, brain: "_FakeBrain") -> None:
        self._brain = brain
        self.loop = asyncio.get_event_loop()

    def get_cog(self, name: str) -> object:
        return self._brain if name == "BotBrain" else None


class _FakeBrain:
    """假 BotBrain：依 task_id 前綴回覆指定的字串。"""

    def __init__(self, score_reply: str, reply_reply: str) -> None:
        self._score_reply = score_reply
        self._reply_reply = reply_reply
        self.submitted: list[object] = []

    async def enqueue_task(self, task: object) -> None:
        self.submitted.append(task)
        text = (
            self._score_reply
            if str(getattr(task, "task_id", "")).startswith("interject-score-")
            else self._reply_reply
        )
        await task.callback(text)


async def _run(
    score_reply: str, reply_reply: str = "插話內容", score_threshold: int | None = None
) -> dict[str, MagicMock]:
    channel = _make_channel()
    state = _ready_state()
    brain = _FakeBrain(score_reply, reply_reply)
    bot = _FakeBot(brain)

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(ij, "fetch_recent_script", AsyncMock(return_value="腳本內容"))
        )
        stack.enter_context(
            patch.object(ij, "build_dynamic_persona", return_value=("人設", "尾巴"))
        )
        thinking_log = stack.enter_context(patch.object(ij, "append_thinking_log"))
        send = stack.enter_context(
            patch.object(ij, "simulate_typing_and_send_to_channel", AsyncMock())
        )
        if score_threshold is not None:
            stack.enter_context(
                patch.object(ij.settings, "INTERJECT_SCORE_THRESHOLD", score_threshold)
            )
        await ij.maybe_interject(bot, channel, state)

    return {"thinking_log": thinking_log, "send": send, "brain": brain}


class TestInterjectScoreDegradation:
    """驗證評分失敗時安全降級。"""

    def test_empty_score_reply_skips_interject(self) -> None:
        """評分回空字串（過濾或推論失敗）時應直接略過，不插話也不記錄。"""
        result = asyncio.run(_run("", score_threshold=1))

        result["thinking_log"].assert_not_called()
        result["send"].assert_not_called()

    def test_unparsable_score_reply_skips_interject(self) -> None:
        """完全無法判讀分數時不應亂猜，應略過插話。"""
        result = asyncio.run(_run("這段文字裡完全沒有分數", score_threshold=1))

        result["thinking_log"].assert_not_called()
        result["send"].assert_not_called()

    def test_valid_score_below_threshold_skips_send(self) -> None:
        """分數低於門檻時正常評分但不插話。"""
        result = asyncio.run(_run('{"score": 2, "reasoning": "無聊"}', score_threshold=8))

        assert result["thinking_log"].call_count == 1
        assert result["thinking_log"].call_args[0][0]["score"] == 2
        result["send"].assert_not_called()

    def test_valid_high_score_proceeds(self) -> None:
        """高分且可插話時流程維持正常（降級邏輯未誤擋正常路徑）。"""
        result = asyncio.run(_run('{"score": 10, "reasoning": "超好笑"}', score_threshold=5))

        # 評分與實際插話各記一筆 thinking log
        assert result["thinking_log"].call_count == 2
        assert result["thinking_log"].call_args_list[0][0][0]["score"] == 10
        result["send"].assert_awaited()