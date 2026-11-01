"""tests/unit/test_emotion_r21.py
==============================
R21: 動態情感狀態機 (Dynamic Affect State Machine) 驗收測試。
驗證：
1. PAD 三維有界狀態、clamping 與 8+1 情緒分桶映射；
2. 半衰期指數衰減公式與多軸獨立衰減預測；
3. SQLite affect_state 與 affect_events 持久化、事件累加與 auto_decay；
4. Prompt Cache 友善之動態尾段注入（head_persona 靜態前綴完全恆定）；
5. WebUI /api/system/emotion 端點；
6. 語氣變化感知性 (A/B tone guidelines)。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

from handlers.emotion.decay import (
    DEFAULT_AXIS_HALF_LIFE,
    compute_decay_factor,
    decay_affect_state,
)
from handlers.emotion.service import EmotionService
from handlers.emotion.state import (
    AffectState,
    bucket_mood,
    build_emotion_prompt_tail,
    clamp,
)
from handlers.social.persona import build_dynamic_persona
import settings


# ---------------------------------------------------------------------------
# 1. 狀態有界與分桶映射
# ---------------------------------------------------------------------------

def test_clamp_boundaries():
    assert clamp(0.5) == 0.5
    assert clamp(1.5) == 1.0
    assert clamp(-2.0) == -1.0
    assert clamp(-0.01) == -0.01


def test_affect_state_clamping_and_serialization():
    state = AffectState(
        entity_type="channel",
        entity_id="12345",
        pleasure=1.8,
        arousal=-1.5,
        dominance=0.3,
        last_event="test_event",
    )
    assert state.pleasure == 1.0
    assert state.arousal == -1.0
    assert state.dominance == 0.3
    assert state.updated_at != ""

    d = state.to_dict()
    assert d["entity_type"] == "channel"
    assert d["entity_id"] == "12345"
    assert d["pleasure"] == 1.0
    assert "mood_bucket" in d
    assert d["mood_bucket"]["key"] in {
        "exuberant", "playful", "relaxed", "gentle", "feisty", "anxious", "aloof", "weary", "neutral"
    }


def test_mood_bucket_all_octants_and_neutral():
    # 總模長小於 0.25 為 neutral
    neutral = bucket_mood(0.1, 0.1, 0.1)
    assert neutral.key == "neutral"

    # 8 個八分象限
    cases = [
        ((0.8, 0.8, 0.8), "exuberant"),
        ((0.8, 0.8, -0.8), "playful"),
        ((0.8, -0.8, 0.8), "relaxed"),
        ((0.8, -0.8, -0.8), "gentle"),
        ((-0.8, 0.8, 0.8), "feisty"),
        ((-0.8, 0.8, -0.8), "anxious"),
        ((-0.8, -0.8, 0.8), "aloof"),
        ((-0.8, -0.8, -0.8), "weary"),
    ]
    seen_keys = set()
    for (p, a, d), expected_key in cases:
        b = bucket_mood(p, a, d)
        assert b.key == expected_key
        assert b.tone_guideline != ""
        seen_keys.add(b.key)
    assert len(seen_keys) == 8


# ---------------------------------------------------------------------------
# 2. 衰減模型可預測性 (Decay Predictability)
# ---------------------------------------------------------------------------

def test_decay_factor_calculation():
    # 剛好 1 個半衰期 -> factor == 0.5
    factor_1hl = compute_decay_factor(4 * 3600, 4.0)
    assert pytest.approx(factor_1hl, rel=1e-4) == 0.5

    # 2 個半衰期 -> factor == 0.25
    factor_2hl = compute_decay_factor(8 * 3600, 4.0)
    assert pytest.approx(factor_2hl, rel=1e-4) == 0.25

    # 0 時間 -> 1.0
    factor_0 = compute_decay_factor(0, 4.0)
    assert factor_0 == 1.0


def test_decay_affect_state_multi_axis():
    base_time = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc)
    state = AffectState(
        entity_type="channel",
        entity_id="test_channel",
        pleasure=0.8,
        arousal=0.8,
        dominance=0.8,
        updated_at=base_time.isoformat(),
    )

    # 經過 4 小時：
    # pleasure (hl=4h) -> 0.8 * 0.5 = 0.4
    # arousal (hl=2h) -> 0.8 * (0.5^2) = 0.8 * 0.25 = 0.2
    # dominance (hl=6h) -> 0.8 * (0.5^(4/6)) ≈ 0.8 * 0.62996 ≈ 0.50397
    after_4h = base_time + timedelta(hours=4)
    decayed = decay_affect_state(
        state,
        now=after_4h,
        half_life_hours={"pleasure": 4.0, "arousal": 2.0, "dominance": 6.0},
    )

    assert pytest.approx(decayed.pleasure, rel=1e-3) == 0.4
    assert pytest.approx(decayed.arousal, rel=1e-3) == 0.2
    assert pytest.approx(decayed.dominance, rel=1e-3) == 0.504
    assert decayed.updated_at == after_4h.isoformat()


def test_decay_tiny_residuals_snap_to_zero():
    base_time = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc)
    state = AffectState(
        entity_type="channel",
        entity_id="test_channel",
        pleasure=0.003,
        arousal=0.002,
        dominance=0.001,
        updated_at=base_time.isoformat(),
    )
    decayed = decay_affect_state(state, now=base_time + timedelta(hours=1))
    assert decayed.pleasure == 0.0
    assert decayed.arousal == 0.0
    assert decayed.dominance == 0.0


# ---------------------------------------------------------------------------
# 3. EmotionService 持久化與事件記帳
# ---------------------------------------------------------------------------

def test_emotion_service_crud_and_events(tmp_path: Path):
    db_file = tmp_path / "test_emotion.db"
    svc = EmotionService(db_path=db_file)

    # 1. 讀取未建立之實體 -> 回傳預設中性狀態
    init_state = svc.get_state("user", "u100")
    assert init_state.pleasure == 0.0
    assert init_state.arousal == 0.0
    assert init_state.dominance == 0.0
    assert init_state.last_event is None

    # 2. 套用情緒事件（例如：收到稱讚）
    t0 = datetime(2026, 10, 21, 10, 0, 0, tzinfo=timezone.utc)
    s1 = svc.apply_event(
        "user", "u100",
        event_name="user_praise",
        delta_p=0.6,
        delta_a=0.4,
        delta_d=0.2,
        note="被使用者誇獎可愛",
        now=t0,
    )
    assert s1.pleasure == 0.6
    assert s1.arousal == 0.4
    assert s1.dominance == 0.2
    assert s1.last_event == "user_praise"

    # 3. 檢查事件日誌
    events = svc.get_recent_events("user", "u100", limit=10)
    assert len(events) == 1
    assert events[0]["event_name"] == "user_praise"
    assert events[0]["note"] == "被使用者誇獎可愛"

    # 4. 再次套用事件，測試累積與有界限制
    s2 = svc.apply_event(
        "user", "u100",
        event_name="super_praise",
        delta_p=0.8,
        delta_a=0.9,
        delta_d=0.9,
        now=t0,
    )
    assert s2.pleasure == 1.0   # clamp to 1.0
    assert s2.arousal == 1.0    # clamp to 1.0
    assert s2.dominance == 1.0  # clamp to 1.0

    # 5. 時間推移 4 小時後讀取，驗證 auto_decay
    t1 = t0 + timedelta(hours=4)
    s3 = svc.get_state("user", "u100", auto_decay=True, now=t1)
    assert s3.pleasure < 1.0
    assert s3.arousal < 1.0

    # 6. reset_state 歸零
    reset_s = svc.reset_state("user", "u100")
    assert reset_s.pleasure == 0.0
    assert reset_s.arousal == 0.0
    assert reset_s.dominance == 0.0


# ---------------------------------------------------------------------------
# 4. Prompt Cache 友善度與語氣微調尾段注入
# ---------------------------------------------------------------------------

def test_prompt_tail_and_head_persona_immutability(tmp_path: Path, monkeypatch):
    db_file = tmp_path / "test_emotion_prompt.db"
    svc = EmotionService(db_path=db_file)

    # 注入全域 service
    import handlers.emotion.service as emotion_module
    monkeypatch.setattr(emotion_module, "_GLOBAL_EMOTION_SERVICE", svc)
    monkeypatch.setattr(settings, "EMOTION_ENABLED", True)

    channel_id = 998877

    # 1. 中性初始狀態：tail_lore 不包含情緒標籤（避免浪費 context）
    head1, tail1 = build_dynamic_persona(
        current_text="你好呀",
        intent_name="CHAT",
        channel_id=channel_id,
    )
    assert "【🎭 雪莉當前動態情緒狀態" not in tail1

    # 2. 觸發強烈雀躍情緒事件
    svc.apply_event(
        "channel", str(channel_id),
        event_name="great_news",
        delta_p=0.8,
        delta_a=0.8,
        delta_d=0.8,
    )

    head2, tail2 = build_dynamic_persona(
        current_text="你好呀",
        intent_name="CHAT",
        channel_id=channel_id,
    )

    # 核心驗證：head_persona 必須 100% 絕對完全一致！
    # 這是保證 KV Cache / Prefix Cache 完全命中的最高鐵則！
    assert head1 == head2, "head_persona 靜態前綴被動態情感污染，破壞了 KV Cache！"

    # 動態尾段注入了情緒微調
    assert "【🎭 雪莉當前動態情緒狀態" in tail2
    assert "興奮雀躍" in tail2


def test_ab_tone_guideline_perception():
    """A/B 感知測試：驗證不同情感狀態產生鮮明且符合人設的語氣指導。"""
    happy_state = AffectState("channel", "1", pleasure=0.9, arousal=0.8, dominance=0.7)
    anxious_state = AffectState("channel", "1", pleasure=-0.8, arousal=0.7, dominance=-0.8)

    tail_happy = build_emotion_prompt_tail(happy_state)
    tail_anxious = build_emotion_prompt_tail(anxious_state)

    assert "興奮雀躍" in tail_happy
    assert "輕快上揚" in tail_happy

    assert "微帶焦慮" in tail_anxious
    assert "躊躇或緊繃" in tail_anxious
    assert tail_happy != tail_anxious


# ---------------------------------------------------------------------------
# 5. WebUI 端點測試
# ---------------------------------------------------------------------------

def test_web_ui_system_emotion_endpoint(tmp_path: Path, monkeypatch):
    import asyncio
    from web_ui.server import app

    db_file = tmp_path / "test_web_emotion.db"
    svc = EmotionService(db_path=db_file)
    svc.apply_event("channel", "c123", "test_ping", 0.5, 0.4, 0.3, note="測試")

    import handlers.emotion.service as emotion_module
    monkeypatch.setattr(emotion_module, "_GLOBAL_EMOTION_SERVICE", svc)

    async def _run():
        test_client = app.test_client()
        resp = await test_client.get("/api/system/emotion")
        assert resp.status_code == 200

        data = await resp.get_json()
        assert data["status"] == "ok"
        assert "states" in data
        assert "recent_events" in data
        assert len(data["recent_events"]) >= 1
        assert data["recent_events"][0]["event_name"] == "test_ping"

    asyncio.run(_run())
