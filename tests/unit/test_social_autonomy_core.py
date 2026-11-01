"""tests/unit/test_social_autonomy_core.py
============================================
賽博群友 Social Core（方向 C）的核心不變量測試。

覆蓋範圍：
1. 正式群（SOLO）永遠不會建構 WebhookSpeaker。
2. 生成失敗或罐頭台詞一律記為 SILENT，且原始碼不得再出現硬編碼回覆。
3. Kill switch 會立刻降回 SOLO、關閉 scene、停用 NPC，並取消待送 turn。
4. 模式解析的安全預設（無資料列＝SOLO、沙盒需四個條件同時成立）。
5. 決策帳本（SPEAK / SILENT）與 persona 路徑穿越防護。
6. 插話冷卻真正生效（冷卻期間 cannot interject）。
"""

from __future__ import annotations

import asyncio
import io
import time
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from handlers.care.repository import CareRepository
from handlers.social.autonomy import (
    CANNED_FALLBACK_TEXTS,
    ChannelMode,
    DecisionAction,
    Persona,
    PersonaRoster,
    PersonaValidationError,
    SocialDirector,
    SocialEvent,
    SoloModeViolation,
    SpeakerRouter,
    TriggerType,
    looks_like_canned_fallback,
)
from handlers.social.autonomy.modes import ChannelModeResolver
from handlers.social.autonomy.turns import TurnRules, TurnScheduler
from handlers.social.interject import InterjectState


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def repo(tmp_path: Path) -> CareRepository:
    return CareRepository(db_path=tmp_path / "test_social.db")


@pytest.fixture
def fake_settings(tmp_path: Path) -> types.SimpleNamespace:
    soul_dir = tmp_path / "soul"
    (soul_dir / "sandbox").mkdir(parents=True, exist_ok=True)
    (soul_dir / "sandbox" / "npc_one.md").write_text("你是阿嵐，一個愛吐槽的社畜。", encoding="utf-8")
    (soul_dir / "manosaba_sherry_default.md").write_text("你是雪莉。", encoding="utf-8")
    return types.SimpleNamespace(
        BASE_DIR=tmp_path,
        SOUL_DIR=soul_dir,
        CURRENT_SOUL_NAME="manosaba_sherry_default.md",
        BOT_NAME="雪莉",
        SOCIAL_SANDBOX_ENABLED=False,
        SOCIAL_SANDBOX_COMPAT_MODE=False,
        SOCIAL_SANDBOX_MAX_NPCS=3,
        SOCIAL_NPC_MAX_CONSECUTIVE=3,
        SOCIAL_NPC_MAX_PER_ROUND=2,
        SOCIAL_NPC_HOURLY_LIMIT=20,
        SOCIAL_NPC_DAILY_LIMIT=120,
        SOCIAL_NPC_MIN_GAP_SECONDS=45,
        SOCIAL_NPC_DELAY_MIN=0.0,
        SOCIAL_NPC_DELAY_MAX=0.0,
        SOCIAL_SCENE_MAX_TURNS=12,
        SOCIAL_SCENE_IDLE_SECONDS=600,
    )


@pytest.fixture
def surfer_config(repo: CareRepository):
    repo.update_surfer_config(
        enabled=True,
        chat_reply_enabled=True,
        sandbox_channel_id="123456",
        active_hour_start=0,
        active_hour_end=0,
    )
    return repo.get_surfer_config()


def _npc_persona() -> Persona:
    return Persona(
        key="npc_one",
        display_name="阿嵐",
        soul_name="npc_one.md",
        channel_id="123456",
        role="npc",
        is_primary=False,
        enabled=True,
    )


def _primary_persona() -> Persona:
    return Persona(
        key="sherry",
        display_name="雪莉",
        soul_name="manosaba_sherry_default.md",
        channel_id="123456",
        role="primary",
        is_primary=True,
        enabled=True,
    )


# ---------------------------------------------------------------------------
# 1. 正式群硬隔離
# ---------------------------------------------------------------------------
class _PoisonChannel:
    """只要有人嘗試建立 webhook 就會爆掉的假頻道。"""

    id = 123456

    def __init__(self) -> None:
        self.webhook_calls = 0

    async def webhooks(self):
        self.webhook_calls += 1
        raise AssertionError("SOLO 頻道不應呼叫 channel.webhooks()")

    async def create_webhook(self, **kwargs):
        self.webhook_calls += 1
        raise AssertionError("SOLO 頻道不應建立 webhook")


def test_solo_channel_never_touches_webhook(fake_settings):
    """SOLO 頻道拿 NPC persona 一定要被硬阻擋，且不得碰 webhook。"""
    webhook_factory_calls: list[tuple] = []

    def webhook_factory(channel, persona):
        webhook_factory_calls.append((channel, persona))
        return MagicMock()

    router = SpeakerRouter(fake_settings, webhook_speaker_factory=webhook_factory)
    bot_factory_calls: list[object] = []

    def bot_factory(channel):
        speaker = MagicMock()
        speaker.name = "bot"
        bot_factory_calls.append(channel)
        return speaker

    router.bot_speaker_factory = bot_factory
    channel = _PoisonChannel()

    with pytest.raises(SoloModeViolation):
        router.resolve(channel=channel, persona=_npc_persona(), mode=ChannelMode.SOLO)

    assert channel.webhook_calls == 0
    assert webhook_factory_calls == []
    assert bot_factory_calls == []

    # 雪莉本人仍然可以用 Bot 身分發言。
    primary_speaker = router.resolve(channel=channel, persona=_primary_persona(), mode="SOLO")
    assert primary_speaker.name == "bot"
    assert channel.webhook_calls == 0


def test_sandbox_disabled_blocks_webhook_even_for_group_mode(fake_settings):
    """即使頻道標成 SANDBOX_GROUP，全域開關關閉時也不得建立 WebhookSpeaker。"""
    fake_settings.SOCIAL_SANDBOX_ENABLED = False
    webhook_factory_calls: list[tuple] = []
    router = SpeakerRouter(
        fake_settings,
        webhook_speaker_factory=lambda c, p: webhook_factory_calls.append((c, p)),
    )
    with pytest.raises(SoloModeViolation):
        router.resolve(channel=_PoisonChannel(), persona=_npc_persona(), mode=ChannelMode.SANDBOX_GROUP)
    assert webhook_factory_calls == []


# ---------------------------------------------------------------------------
# 2. 生成失敗／罐頭台詞
# ---------------------------------------------------------------------------
def test_canned_fallback_helpers():
    for text in CANNED_FALLBACK_TEXTS:
        assert looks_like_canned_fallback(text) is True
        assert looks_like_canned_fallback(f"前綴 {text} 後綴") is True
    assert looks_like_canned_fallback("今天難得想安靜一下") is False
    assert looks_like_canned_fallback("") is False


def test_record_speak_blocks_canned_and_empty_text(repo: CareRepository, surfer_config):
    """record_speak 收到罐頭或空字串時必須改記 SILENT。"""
    director = SocialDirector(repo)
    event = SocialEvent(
        kind="lurk_chat",
        channel_id="123456",
        trigger_type=TriggerType.MIND_PULSE,
        is_human=False,
    )
    canned = next(iter(CANNED_FALLBACK_TEXTS))

    director.record_speak(event, mode=ChannelMode.SOLO, persona_key="sherry", text=canned)
    director.record_speak(event, mode=ChannelMode.SOLO, persona_key="sherry", text="   ")

    rows = repo.list_decisions(channel_id="123456", limit=10)
    assert len(rows) == 2
    assert all(row["action"] == DecisionAction.SILENT.value for row in rows)
    reasons = {reason for row in rows for reason in row["reasons"]}
    assert "canned_fallback" in reasons
    assert "generation_failed" in reasons


def test_source_never_hardcodes_canned_replies():
    """回歸測試：任何模組都不得再把罐頭台詞當成 fallback 回覆。"""
    root = Path(__file__).resolve().parents[2]
    targets = [
        root / "handlers" / "care" / "surfer.py",
        root / "handlers" / "care" / "cog.py",
        root / "handlers" / "social" / "autonomy",
        root / "handlers" / "social" / "interject.py",
    ]
    contracts_path = root / "handlers" / "social" / "autonomy" / "contracts.py"

    files: list[Path] = []
    for target in targets:
        if target.is_dir():
            files.extend(p for p in target.rglob("*.py"))
        else:
            files.append(target)

    offenders: list[str] = []
    for path in files:
        if path.resolve() == contracts_path.resolve():
            continue
        text = io.open(path, encoding="utf-8").read()
        for canned in CANNED_FALLBACK_TEXTS:
            if canned in text:
                offenders.append(f"{path.name}: {canned}")
    assert offenders == [], f"發現硬編碼罐頭回覆：{offenders}"


@pytest.mark.asyncio
async def test_sandbox_generation_failure_never_sends(repo: CareRepository, fake_settings, monkeypatch):
    """沙盒 NPC 生成失敗時，不得呼叫 router.send，且要留下 SILENT 決策。"""
    from handlers.social.autonomy import group as group_module

    fake_settings.SOCIAL_SANDBOX_ENABLED = True
    repo.upsert_channel_mode(
        "123456",
        mode="SANDBOX_GROUP",
        enabled=True,
        allow_webhooks=True,
    )
    repo.upsert_persona(
        persona_key="npc_one",
        channel_id="123456",
        soul_name="npc_one.md",
        display_name="阿嵐",
        role="npc",
        enabled=True,
    )

    sent: list[tuple] = []

    class _Router:
        async def send(self, **kwargs):
            sent.append(kwargs)
            raise AssertionError("生成失敗時不應該送出任何訊息")

    async def _fake_generate(*args, **kwargs):
        return ""

    monkeypatch.setattr(group_module, "generate_text", _fake_generate)

    runner = group_module.SandboxGroupRunner(
        bot=MagicMock(),
        repository=repo,
        settings_module=fake_settings,
        router=_Router(),
    )

    message = MagicMock()
    message.channel.id = 123456
    message.channel.name = "sandbox"
    message.guild.id = 999
    message.author.bot = False
    message.author.id = 111
    message.author.display_name = "群友"
    message.id = 555
    message.content = "今天好累喔"

    result = await runner.handle_human_message(message, repo.get_surfer_config())
    assert result["status"] == "scheduled"
    assert sent == []

    # 等待排程的 turn 執行完（延遲為 0）
    await runner.turns.wait_idle("123456", timeout=10)

    assert sent == []
    rows = repo.list_decisions(channel_id="123456", limit=10)
    assert any(
        row["action"] == DecisionAction.SILENT.value and "generation_failed" in row["reasons"]
        for row in rows
    )


# ---------------------------------------------------------------------------
# 3. Kill switch
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_kill_switch_stops_all_npc_within_10s(repo: CareRepository, fake_settings):
    """Kill switch 必須在 10 秒內取消待送 turn 並降回 SOLO。"""
    fake_settings.SOCIAL_SANDBOX_ENABLED = True
    repo.upsert_channel_mode("123456", mode="SANDBOX_GROUP", enabled=True, allow_webhooks=True)
    repo.upsert_persona(
        persona_key="npc_one",
        channel_id="123456",
        soul_name="npc_one.md",
        display_name="阿嵐",
        role="npc",
        enabled=True,
    )
    scene = repo.open_scene("123456", topic="測試")

    from handlers.social.autonomy import group as group_module

    runner = group_module.SandboxGroupRunner(
        bot=MagicMock(),
        repository=repo,
        settings_module=fake_settings,
    )

    executed: list[str] = []

    async def _should_not_run():
        executed.append("ran")
        return "oops"

    runner.turns.schedule("123456", 5.0, _should_not_run)

    result = runner.kill("123456")
    assert result["modes"] >= 1
    assert result["scenes"] == 1
    assert result["personas"] == 1

    mode_record = repo.get_channel_mode("123456")
    assert mode_record is not None
    assert mode_record.mode == "SOLO"
    assert mode_record.enabled is False
    assert mode_record.allow_webhooks is False

    assert repo.get_open_scene("123456") is None
    closed = repo.get_scene(scene.scene_id)
    assert closed is not None
    assert closed.status == "killed"

    persona = repo.get_persona("npc_one")
    assert persona is not None
    assert persona.enabled is False

    await asyncio.sleep(0.2)
    assert executed == []


# ---------------------------------------------------------------------------
# 4. 模式解析
# ---------------------------------------------------------------------------
def test_mode_resolver_defaults_to_solo(repo: CareRepository, fake_settings, surfer_config):
    resolver = ChannelModeResolver(repo, fake_settings)
    decision = resolver.resolve("999", config=surfer_config)
    assert decision.mode is ChannelMode.SOLO
    assert decision.reason == "default_solo"


def test_mode_resolver_requires_sandbox_switch_and_npc(repo: CareRepository, fake_settings, surfer_config):
    repo.upsert_channel_mode("123456", mode="SANDBOX_GROUP", enabled=True, allow_webhooks=True)
    resolver = ChannelModeResolver(repo, fake_settings)

    # 全域開關關閉 → 降級 SOLO
    assert resolver.resolve("123456", config=surfer_config).reason == "sandbox_disabled"

    fake_settings.SOCIAL_SANDBOX_ENABLED = True
    # 還沒有 NPC → 降級 SOLO
    assert resolver.resolve("123456", config=surfer_config).reason == "no_enabled_npc_persona"

    repo.upsert_persona(
        persona_key="npc_one",
        channel_id="123456",
        soul_name="npc_one.md",
        display_name="阿嵐",
        role="npc",
        enabled=True,
    )
    decision = resolver.resolve("123456", config=surfer_config)
    assert decision.mode is ChannelMode.SANDBOX_GROUP
    assert decision.is_group is True


def test_compat_mode_off_keeps_legacy_sandbox_solo(repo: CareRepository, fake_settings, surfer_config):
    """未開啟 SOCIAL_SANDBOX_COMPAT_MODE 時，舊 sandbox_channel_id 仍是 SOLO。"""
    fake_settings.SOCIAL_SANDBOX_ENABLED = True
    fake_settings.SOCIAL_SANDBOX_COMPAT_MODE = False
    resolver = ChannelModeResolver(repo, fake_settings)
    assert resolver.resolve("123456", config=surfer_config).mode is ChannelMode.SOLO


# ---------------------------------------------------------------------------
# 5. 決策帳本與 persona 驗證
# ---------------------------------------------------------------------------
def test_silent_decisions_are_recorded(repo: CareRepository, surfer_config):
    director = SocialDirector(repo)
    event = SocialEvent(
        kind="mind_pulse",
        channel_id="123456",
        trigger_type=TriggerType.MIND_PULSE,
        is_human=False,
    )
    disabled_cfg = repo.get_surfer_config()
    repo.update_surfer_config(enabled=False)
    cfg = repo.get_surfer_config()

    decision = director.decide(event, cfg, room=repo.get_room("123456"))
    assert decision.allowed is False
    assert decision.action is DecisionAction.SILENT
    assert decision.decision_id > 0

    rows = repo.list_decisions(channel_id="123456", limit=5)
    assert rows[0]["trigger_type"] == TriggerType.MIND_PULSE.value
    assert any("surfer_disabled" in r for r in rows[0]["reasons"])

    # 恢復原設定，避免影響其他測試使用的檔案狀態
    repo.update_surfer_config(enabled=disabled_cfg.enabled)


def test_direct_human_reply_bypasses_budget(repo: CareRepository):
    """直接回覆人類（熱聊／巡檢）不該被主動冒泡額度靜音。"""
    director = SocialDirector(repo)
    repo.update_surfer_config(
        enabled=True,
        chat_reply_enabled=True,
        active_hour_start=0,
        active_hour_end=0,
        max_replies_per_hour=1,
        max_replies_per_day=1,
    )
    cfg = repo.get_surfer_config()
    event = SocialEvent(
        kind="hot_chat",
        channel_id="123456",
        trigger_type=TriggerType.HUMAN_MESSAGE,
        is_human=True,
    )

    blocked = director.decide(
        event,
        cfg,
        room=repo.get_room("123456"),
        ignore_cooldown=True,
        hourly_speaks=5,
        daily_speaks=5,
    )
    assert blocked.allowed is False
    assert any("daily_budget" in reason for reason in blocked.gate.reasons)

    allowed = director.decide(
        event,
        cfg,
        room=repo.get_room("123456"),
        ignore_cooldown=True,
        bypass_budget=True,
    )
    assert allowed.allowed is True
    assert allowed.action is DecisionAction.SPEAK


def test_persona_rejects_path_traversal(repo: CareRepository, fake_settings):
    roster = PersonaRoster(repo, fake_settings)
    with pytest.raises(PersonaValidationError):
        roster.validate_soul_name("../evil.md")
    with pytest.raises(PersonaValidationError):
        roster.validate_soul_name("sub/dir.md")
    with pytest.raises(PersonaValidationError):
        roster.validate_key("sherry")
    assert roster.validate_soul_name("npc_one.md") == "npc_one.md"


# ---------------------------------------------------------------------------
# 6. 插話冷卻
# ---------------------------------------------------------------------------
def test_interject_cooldown_blocks_during_window(monkeypatch):
    state = InterjectState()
    state.sync_config(60)
    assert state.can_interject() is True

    state.reset_cooldown(60)
    assert state.is_cooling_down() is True
    assert state.can_interject() is False

    # 手動把冷卻時間撥到過去，應重新可插話
    state.cooldown_until = 0.0
    state.consecutive_count = 0
    assert state.can_interject() is True


def test_interject_respects_consecutive_cap():
    state = InterjectState()
    state.sync_config(0)
    state.cooldown_until = 0.0

    # 剛插話過（尚未達 stale 門檻）→ 連續次數超標必須擋下
    state.last_action_at = time.monotonic()
    state.consecutive_count = 99
    assert state.can_interject() is False

    # 距上次行動已久（> 60 秒），計數會重置
    state.last_action_at = time.monotonic() - 120
    assert state.can_interject() is True


# ---------------------------------------------------------------------------
# 7. TurnScheduler 規則
# ---------------------------------------------------------------------------
def test_turn_rules_block_same_speaker_and_streak(repo: CareRepository):
    scene = repo.open_scene("123456", topic="t", max_turns=12)
    repo.add_scene_turn(
        scene_id=scene.scene_id,
        channel_id="123456",
        role="npc",
        persona_key="npc_one",
        content="我先說一句",
    )
    scheduler = TurnScheduler(repo, TurnRules(min_gap_seconds=0, delay_min=0, delay_max=0))
    scene = repo.get_scene(scene.scene_id)

    same = scheduler.evaluate(scene=scene, persona_key="npc_one", role="npc", now=0)
    assert same.allowed is False
    assert "same_speaker_consecutive" in same.reasons

    other = scheduler.evaluate(scene=scene, persona_key="npc_two", role="npc", now=0)
    assert other.allowed is True


def test_turn_similarity_guard():
    scheduler = TurnScheduler.__new__(TurnScheduler)
    assert TurnScheduler.is_too_similar("笑死這什麼鬼", "笑死這什麼鬼啦") is True
    assert TurnScheduler.is_too_similar("完全不一樣的開頭", "笑死這什麼鬼啦") is False
