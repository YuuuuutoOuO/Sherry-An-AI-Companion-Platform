"""tests/unit/test_social_dialogue_trigger.py
===============================================
正式群（SOLO）「什麼時候該回覆」的判定測試。

覆蓋範圍：
1. 對話鏈：只有「叫她」或「她正在跟這個人一來一往」才算在跟她說話。
2. 別人在聊（AMBIENT）不會自動接話，對話鏈也不會被別人的插話續命。
3. 發言額度（每小時／每日）真的會擋下發言。
4. 主動插話的冷卻比被動回話長。
5. 主動冒泡的模式選擇依頻道現場決定（安靜 → 上網找話題，正熱 → 不插話）。
"""

from __future__ import annotations

import time
import types
from pathlib import Path

import pytest

from handlers.care.repository import CareRepository
from handlers.care.surfer import CareSurfer
from handlers.social.autonomy import (
    DialoguePhase,
    DialogueTracker,
    SocialDirector,
    SocialEvent,
    TriggerType,
)
from handlers.social.autonomy.gate import SpeechGate


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def repo(tmp_path: Path) -> CareRepository:
    return CareRepository(db_path=tmp_path / "test_dialogue.db")


def _config(**overrides):
    base = dict(
        sandbox_channel_id="123456",
        enabled=True,
        chat_reply_enabled=True,
        active_hour_start=0,
        active_hour_end=23,
        interject_threshold=6,
        interject_score_threshold=7,
        interject_cooldown_seconds=45,
        proactive_cooldown_seconds=120,
        active_chat_window_seconds=180,
        max_replies_per_hour=8,
        max_replies_per_day=60,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _settings(**overrides):
    base = dict(SOCIAL_SANDBOX_ENABLED=False, SOCIAL_NPC_MAX_CONSECUTIVE=2)
    base.update(overrides)
    return types.SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# 1. 對話鏈判定
# ---------------------------------------------------------------------------
def test_addressed_by_mention_or_reply() -> None:
    tracker = DialogueTracker(clock=lambda: 1000.0)
    assert tracker.classify("c1", author_id="u1", is_mentioned=True, now=1000.0) is DialoguePhase.ADDRESSED
    assert tracker.classify("c1", author_id="u1", is_reply_to_bot=True, now=1000.0) is DialoguePhase.ADDRESSED
    assert tracker.classify("c1", author_id="u1", content="雪莉妳在幹嘛", now=1000.0) is DialoguePhase.ADDRESSED


def test_continuation_only_for_the_person_she_was_talking_to() -> None:
    tracker = DialogueTracker(clock=lambda: 1000.0)
    tracker.note_bot_speak("c1", target_author_id="u1", now=1000.0)

    # 她剛才回的是 u1，u1 接著講 → 延續對話
    tracker.note_human_message("c1", "u1")
    assert tracker.classify("c1", author_id="u1", content="哪有啦", now=1010.0) is DialoguePhase.CONTINUATION

    # 另一個人插話（她沒有回過他）→ 不算在跟她說話
    tracker.note_human_message("c1", "u2")
    assert tracker.classify("c1", author_id="u2", content="哈哈", now=1012.0) is DialoguePhase.AMBIENT


def test_continuation_breaks_after_extra_humans_speak() -> None:
    tracker = DialogueTracker(clock=lambda: 1000.0)
    tracker.note_bot_speak("c1", target_author_id="", now=1000.0)

    tracker.note_human_message("c1", "u1")
    assert tracker.classify("c1", author_id="u1", content="嗯", now=1005.0) is DialoguePhase.CONTINUATION

    # 她還沒回，第二個人又講 → 對話鏈斷了，不要跟著亂接
    tracker.note_human_message("c1", "u2")
    assert tracker.classify("c1", author_id="u2", content="我也覺得", now=1006.0) is DialoguePhase.AMBIENT


def test_window_expiry_falls_back_to_ambient() -> None:
    tracker = DialogueTracker(clock=lambda: 1000.0)
    tracker.note_bot_speak("c1", target_author_id="u1", now=1000.0)
    tracker.note_human_message("c1", "u1")
    assert (
        tracker.classify("c1", author_id="u1", content="欸", window_seconds=180, now=1400.0)
        is DialoguePhase.AMBIENT
    )


def test_note_bot_speak_resets_human_counter() -> None:
    tracker = DialogueTracker(clock=lambda: 1000.0)
    tracker.note_human_message("c1", "u1")
    tracker.note_human_message("c1", "u2")
    tracker.note_bot_speak("c1", target_author_id="u2", now=1000.0)
    assert tracker.state("c1").humans_since_bot == 0
    tracker.note_human_message("c1", "u2")
    assert tracker.classify("c1", author_id="u2", content="所以咧", now=1001.0) is DialoguePhase.CONTINUATION


# ---------------------------------------------------------------------------
# 2. 發言額度
# ---------------------------------------------------------------------------
def test_hourly_budget_blocks_speech() -> None:
    gate = SpeechGate(_settings())
    result = gate.evaluate(
        config=_config(max_replies_per_hour=3),
        trigger_type=TriggerType.HUMAN_MESSAGE,
        hourly_speaks=3,
        daily_speaks=3,
        now=time.mktime((2026, 10, 30, 12, 0, 0, 0, 0, -1)),
    )
    assert result.allow is False
    assert any(r.startswith("hourly_budget") for r in result.reasons)


def test_daily_budget_blocks_speech() -> None:
    gate = SpeechGate(_settings())
    result = gate.evaluate(
        config=_config(max_replies_per_day=10),
        trigger_type=TriggerType.HUMAN_MESSAGE,
        hourly_speaks=1,
        daily_speaks=10,
        now=time.mktime((2026, 10, 30, 12, 0, 0, 0, 0, -1)),
    )
    assert result.allow is False
    assert any(r.startswith("daily_budget") for r in result.reasons)


def test_zero_budget_means_unlimited() -> None:
    gate = SpeechGate(_settings())
    result = gate.evaluate(
        config=_config(max_replies_per_hour=0, max_replies_per_day=0),
        trigger_type=TriggerType.HUMAN_MESSAGE,
        hourly_speaks=999,
        daily_speaks=9999,
        now=time.mktime((2026, 10, 30, 12, 0, 0, 0, 0, -1)),
    )
    assert result.allow is True


def test_group_mode_ignores_budget() -> None:
    """沙盒群像仍在測試，額度不應該套用到 SANDBOX_GROUP。"""
    gate = SpeechGate(_settings(SOCIAL_SANDBOX_ENABLED=True))
    result = gate.evaluate(
        config=_config(max_replies_per_hour=1),
        trigger_type=TriggerType.HUMAN_MESSAGE,
        mode="SANDBOX_GROUP",
        hourly_speaks=99,
        daily_speaks=999,
        now=time.mktime((2026, 10, 30, 12, 0, 0, 0, 0, -1)),
    )
    assert result.allow is True


def test_proactive_cooldown_is_longer_than_reply_cooldown() -> None:
    gate = SpeechGate(_settings())
    now = 1000.0
    reply_until = gate.mark_cooldown(config=_config(), now=now)
    proactive_until = gate.mark_cooldown(config=_config(), now=now, proactive=True)
    assert reply_until - now == pytest.approx(45.0)
    assert proactive_until - now == pytest.approx(120.0)


def test_director_reads_budget_from_ledger(repo: CareRepository) -> None:
    repo.update_surfer_config(
        enabled=True,
        chat_reply_enabled=True,
        sandbox_channel_id="123456",
        active_hour_start=0,
        active_hour_end=23,
        max_replies_per_hour=2,
        max_replies_per_day=100,
    )
    config = repo.get_surfer_config()
    director = SocialDirector(repo, _settings())

    event = SocialEvent(
        kind="hot_chat",
        channel_id="123456",
        trigger_type=TriggerType.HUMAN_MESSAGE,
        message_id="m1",
        author_id="u1",
        created_at=time.time(),
    )
    for i in range(2):
        repo.record_decision(
            channel_id="123456",
            mode="SOLO",
            trigger_type="human_message",
            action="SPEAK",
            trigger_ref=f"m{i}",
        )

    decision = director.decide(event, config, ignore_cooldown=True)
    assert decision.allowed is False
    assert any("hourly_budget" in r for r in decision.gate.reasons)
    assert repo.count_speaks("123456", since=time.time() - 3600) == 2


def test_count_speaks_ignores_silent_records(repo: CareRepository) -> None:
    repo.record_decision(channel_id="c9", mode="SOLO", trigger_type="human_message", action="SILENT")
    repo.record_decision(channel_id="c9", mode="SOLO", trigger_type="human_message", action="SPEAK")
    assert repo.count_speaks("c9", since=time.time() - 60) == 1


# ---------------------------------------------------------------------------
# 3. 主動冒泡的時機
# ---------------------------------------------------------------------------
def test_bubble_mode_defers_when_channel_is_hot(repo: CareRepository) -> None:
    surfer = CareSurfer(repository=repo)
    config = repo.get_surfer_config()
    mode = asyncio_run(surfer.choose_social_bubble_mode({"silence_minutes": 0}, config))
    assert mode is None


def test_bubble_mode_uses_web_surf_when_channel_is_quiet(repo: CareRepository) -> None:
    surfer = CareSurfer(repository=repo)
    config = repo.get_surfer_config()
    mode = asyncio_run(surfer.choose_social_bubble_mode({"silence_minutes": 45}, config))
    assert mode == "surf_gossip"


def test_bubble_mode_falls_back_to_daily_vibe_when_only_briefly_idle(repo: CareRepository) -> None:
    surfer = CareSurfer(repository=repo)
    config = repo.get_surfer_config()
    mode = asyncio_run(surfer.choose_social_bubble_mode({"silence_minutes": 8}, config))
    assert mode == "daily_vibe"


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)
