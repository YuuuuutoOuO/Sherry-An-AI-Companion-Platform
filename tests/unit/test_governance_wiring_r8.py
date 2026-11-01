"""tests/unit/test_governance_wiring_r8.py
==========================================
R8 治理接線與 SPARK 預算回歸測試（報告 §6.1.1–§6.1.3、§7.1.3 C1–C4、P0-5）。

驗證：
1. `AutonomyGuard.check()` 真正串接 `RiskPolicyEngine` 與 `SystemStateNormalizer`：
   HIGH 風險拒絕、INCIDENT 降級、預算消耗。
2. BotBrain 提交 choke point：背景/自主任務受熔斷與預算管制，互動任務不受影響；
   被拒絕時 callback 收到空字串（fail-closed，呼叫端不會永久等待）。
3. SPARK 行動派發 choke point：派發前經過 guard、動作有長生命週期 semaphore、
   強引用集合與 timeout。
4. SPARK 配額：每位使用者 trigger 數上限、週期分級下限、每日推送上限。
5. ADR-0002 狀態已改為三段制並標記 R8 驗證。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import settings
from core.autonomy_guard import AutonomyGuard
from core.risk_engine import ActionRiskLevel, RiskPolicyEngine
from core.system_state import SystemMetrics, SystemStateNormalizer
from handlers.bot_brain.cog import BotBrain
from handlers.bot_brain.models import InferenceTask
from handlers.spark.cog import SparkCog
from handlers.spark.models import Trigger, TriggerResult
from handlers.spark.quota import SparkPushQuota
from handlers.spark.repository import SparkRepository
from handlers.spark.service import SparkQuotaExceeded, SparkService


@pytest.fixture
def anyio_backend() -> str:
    """設定 anyio 測試後端為 asyncio。"""
    return "asyncio"


@pytest.fixture
def guard() -> AutonomyGuard:
    """建立隔離的 guard（避免污染全域單例）。"""
    return AutonomyGuard(
        max_actions_per_hour=3,
        max_actions_per_day=5,
        risk_engine=RiskPolicyEngine(),
        state_normalizer=SystemStateNormalizer(),
    )


@pytest.fixture
def mock_bot() -> MagicMock:
    """提供標準 Mock Discord Bot。"""
    bot = MagicMock()
    bot.wait_until_ready = AsyncMock()
    bot.get_channel = MagicMock()
    bot.fetch_channel = AsyncMock()
    return bot


@pytest.fixture
def service_setup(tmp_path: Path) -> tuple[SparkService, SparkRepository]:
    """建立測試用 SparkService 與臨時倉儲。"""
    repo = SparkRepository(file_path=tmp_path / "spark" / "triggers.json")

    class _NeverFire:
        trigger_type = "price"

        async def should_fire(self, trigger: Trigger) -> TriggerResult:
            return TriggerResult(should_fire=False)

    service = SparkService(repo, {"price": _NeverFire()})
    return service, repo


# ===========================================================================
# 1. Guard 與 RiskEngine / SystemState 的實際串接
# ===========================================================================

def test_guard_denies_high_risk_action(guard: AutonomyGuard) -> None:
    """HIGH 風險行動（如清空記憶）不得自主執行。"""
    decision = guard.check("clear_memory", description="夜間清理全部記憶")

    assert decision.allowed is False
    assert decision.risk_level is ActionRiskLevel.HIGH
    assert "人工" in decision.reason or "審批" in decision.reason


def test_guard_allows_medium_action_and_consumes_budget(guard: AutonomyGuard) -> None:
    """MEDIUM 行動（SPARK 推播）在預算內允許，並消耗額度。"""
    first = guard.check("spark_push", description="NVDA 達標推播")
    assert first.allowed is True
    assert first.risk_level is ActionRiskLevel.MEDIUM

    # max_actions_per_hour=3：再兩次核准後，第四次應被拒絕
    assert guard.check("spark_push").allowed is True
    assert guard.check("spark_push").allowed is True
    denied = guard.check("spark_push")
    assert denied.allowed is False
    assert "上限" in denied.reason


def test_guard_uses_system_state_incident(guard: AutonomyGuard) -> None:
    """系統進入 INCIDENT 時，MEDIUM 行動被拒；LOW 行動仍可執行。"""
    level = guard.update_system_metrics(SystemMetrics(cpu_percent=99.0))
    assert level.value == "INCIDENT"

    medium = guard.check("spark_push", description="事件期間推播")
    assert medium.allowed is False
    assert medium.health_level.value == "INCIDENT"

    low = guard.check("metric_collect", description="僅採集指標")
    assert low.allowed is True


def test_guard_kill_switch_blocks_all(guard: AutonomyGuard) -> None:
    """全域熔斷啟動後，任何風險等級皆拒絕。"""
    guard.trigger_kill_switch("測試")
    for action in ("spark_push", "metric_collect", "clear_memory"):
        assert guard.check(action).allowed is False


# ===========================================================================
# 2. BotBrain 提交 choke point
# ===========================================================================

@pytest.mark.anyio
async def test_botbrain_blocks_autonomous_task_on_kill_switch(
    mock_bot: MagicMock, guard: AutonomyGuard
) -> None:
    """熔斷期間，背景自主任務不得入列，且 callback 收到空字串。"""
    brain = BotBrain(mock_bot, autonomy_guard=guard)
    guard.trigger_kill_switch("測試")
    callback = AsyncMock()
    task = InferenceTask(
        priority=settings.PRIORITY_HEARTBEAT,
        system_prompt="s",
        user_prompt="u",
        callback=callback,
        task_id="auto-blocked",
        model="local:test",
    )

    await brain.enqueue_task(task)

    assert brain._queue.qsize() == 0
    callback.assert_awaited_once_with("")


@pytest.mark.anyio
async def test_botbrain_does_not_block_interactive_task(
    mock_bot: MagicMock, guard: AutonomyGuard
) -> None:
    """互動任務（優先序低於背景門檻）不受熔斷影響。"""
    brain = BotBrain(mock_bot, autonomy_guard=guard)
    guard.trigger_kill_switch("測試")
    task = InferenceTask(
        priority=settings.PRIORITY_TAG_RESPONSE,
        system_prompt="s",
        user_prompt="u",
        callback=AsyncMock(),
        task_id="interactive",
        model="local:test",
    )

    await brain.enqueue_task(task)

    assert brain._queue.qsize() == 1


@pytest.mark.anyio
async def test_botbrain_allows_autonomous_task_and_consumes_budget(
    mock_bot: MagicMock, guard: AutonomyGuard
) -> None:
    """正常狀態下背景任務可入列，並計入 guard 預算。"""
    brain = BotBrain(mock_bot, autonomy_guard=guard)
    task = InferenceTask(
        priority=settings.PRIORITY_VIBE_UPDATE,
        system_prompt="s",
        user_prompt="u",
        callback=AsyncMock(),
        task_id="auto-ok",
        model="local:test",
    )

    await brain.enqueue_task(task)

    assert brain._queue.qsize() == 1
    assert len(guard._action_timestamps) == 1


# ===========================================================================
# 3. SPARK 行動派發 choke point
# ===========================================================================

def _build_spark_cog(
    mock_bot: MagicMock, service: MagicMock, guard: AutonomyGuard
) -> SparkCog:
    """建立注入 mock service / guard 的 SparkCog。"""
    cog = SparkCog(bot=mock_bot, service=service)
    cog._autonomy_guard = guard
    service.consume_push_quota.return_value = (True, "配額核准")
    cog.action_runner = MagicMock()
    cog.action_runner.run_action = AsyncMock(return_value=True)
    return cog


def _firing_trigger(trigger_id: str = "trig-r8-0001", user_id: int = 123) -> Trigger:
    return Trigger(
        trigger_id=trigger_id,
        user_id=user_id,
        channel_id=456,
        trigger_type="price",
        condition={"ticker": "NVDA", "target_price": 100.0},
        message="測試",
    )


@pytest.mark.anyio
async def test_spark_dispatch_goes_through_guard_and_blocks_on_kill_switch(
    mock_bot: MagicMock, guard: AutonomyGuard
) -> None:
    """SPARK 派發前必須經過 guard；熔斷期間不得建立動作任務。"""
    service = MagicMock()
    service.list_active_triggers.return_value = [_firing_trigger()]
    service.is_in_cooldown.return_value = False
    service.check_trigger = AsyncMock(
        return_value=(TriggerResult(should_fire=True, current_value="達標"), None)
    )
    cog = _build_spark_cog(mock_bot, service, guard)
    guard.trigger_kill_switch("測試")

    with patch.object(guard, "check", wraps=guard.check) as spy:
        await cog._run_check_cycle()

    spy.assert_called_once()
    assert spy.call_args.args[0] == "spark_push"
    cog.action_runner.run_action.assert_not_awaited()
    assert len(cog._action_tasks) == 0


@pytest.mark.anyio
async def test_spark_dispatch_tracks_task_and_releases_reference(
    mock_bot: MagicMock, guard: AutonomyGuard
) -> None:
    """允許派發時建立強引用任務，完成後自動移除。"""
    service = MagicMock()
    service.list_active_triggers.return_value = [_firing_trigger()]
    service.is_in_cooldown.return_value = False
    service.check_trigger = AsyncMock(
        return_value=(TriggerResult(should_fire=True, current_value="達標"), None)
    )
    cog = _build_spark_cog(mock_bot, service, guard)

    await cog._run_check_cycle()
    assert len(cog._action_tasks) == 1

    await asyncio.gather(*cog._action_tasks)
    await asyncio.sleep(0)
    cog.action_runner.run_action.assert_awaited_once()
    assert len(cog._action_tasks) == 0


@pytest.mark.anyio
async def test_spark_action_concurrency_capped(
    mock_bot: MagicMock, guard: AutonomyGuard, monkeypatch: pytest.MonkeyPatch
) -> None:
    """動作派發受長生命週期 semaphore 限制（不再無界累積）。"""
    monkeypatch.setattr(settings, "SPARK_MAX_CONCURRENT_ACTIONS", 2)
    # 本測試聚焦並發上限，放寬 guard 預算避免 4 個 trigger 被每小時上限攔下
    guard.max_actions_per_hour = 50
    guard.max_actions_per_day = 100
    release = asyncio.Event()
    active = 0
    peak = 0

    async def _blocking_run(trigger: Trigger, content: str) -> bool:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await release.wait()
        active -= 1
        return True

    service = MagicMock()
    triggers = [_firing_trigger(f"trig-r8-{i:04d}", user_id=100 + i) for i in range(4)]
    service.list_active_triggers.return_value = triggers
    service.is_in_cooldown.return_value = False
    service.check_trigger = AsyncMock(
        return_value=(TriggerResult(should_fire=True, current_value="達標"), None)
    )
    cog = _build_spark_cog(mock_bot, service, guard)
    cog.action_runner.run_action = AsyncMock(side_effect=_blocking_run)

    await cog._run_check_cycle()
    await asyncio.sleep(0.05)
    assert peak == 2
    assert len(cog._action_tasks) == 4

    release.set()
    await asyncio.gather(*cog._action_tasks)
    assert peak == 2


@pytest.mark.anyio
async def test_spark_action_timeout_cancels_runaway_action(
    mock_bot: MagicMock, guard: AutonomyGuard, monkeypatch: pytest.MonkeyPatch
) -> None:
    """動作超過 timeout 會被取消，不拖垮巡檢迴圈。"""
    monkeypatch.setattr(settings, "SPARK_ACTION_TIMEOUT_SECONDS", 0.05)

    async def _hang(trigger: Trigger, content: str) -> bool:
        await asyncio.sleep(5)
        return True

    service = MagicMock()
    service.list_active_triggers.return_value = [_firing_trigger()]
    service.is_in_cooldown.return_value = False
    service.check_trigger = AsyncMock(
        return_value=(TriggerResult(should_fire=True, current_value="達標"), None)
    )
    cog = _build_spark_cog(mock_bot, service, guard)
    cog.action_runner.run_action = AsyncMock(side_effect=_hang)

    started = asyncio.get_running_loop().time()
    await cog._run_check_cycle()
    await asyncio.gather(*cog._action_tasks)
    await asyncio.sleep(0)
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 1.0
    assert len(cog._action_tasks) == 0


# ===========================================================================
# 4. SPARK 配額：trigger 數、週期分級、每日推送
# ===========================================================================

def test_service_rejects_trigger_over_user_limit(service_setup: tuple[SparkService, SparkRepository]) -> None:
    """每位使用者的 trigger 數不得超過上限。"""
    service, _ = service_setup
    limit = int(getattr(settings, "SPARK_MAX_TRIGGERS_PER_USER", 10))
    for i in range(limit):
        service.create_trigger(
            user_id=777,
            channel_id=1,
            trigger_type="price",
            condition={"ticker": f"T{i}"},
        )

    with pytest.raises(SparkQuotaExceeded):
        service.create_trigger(
            user_id=777,
            channel_id=1,
            trigger_type="price",
            condition={"ticker": "OVER"},
        )

    # 其他使用者不受影響
    service.create_trigger(
        user_id=778,
        channel_id=1,
        trigger_type="price",
        condition={"ticker": "OK"},
    )


def test_service_clamps_llm_interval_floor(service_setup: tuple[SparkService, SparkRepository]) -> None:
    """LLM 型排程週期下限 30 分鐘；純通知型下限 5 分鐘。"""
    service, _ = service_setup

    llm = service.create_trigger(
        user_id=1,
        channel_id=1,
        trigger_type="schedule",
        condition={"mode": "interval", "interval_minutes": 5},
        action={"action_type": "core_tool", "target": "CHAT", "payload": {"query": "早安"}},
    )
    assert llm.condition["interval_minutes"] == 30

    notify = service.create_trigger(
        user_id=2,
        channel_id=1,
        trigger_type="schedule",
        condition={"mode": "interval", "interval_minutes": 1},
    )
    assert notify.condition["interval_minutes"] == 5

    notify_ok = service.create_trigger(
        user_id=3,
        channel_id=1,
        trigger_type="schedule",
        condition={"mode": "interval", "interval_minutes": 10},
    )
    assert notify_ok.condition["interval_minutes"] == 10


def test_spark_push_quota_daily_limit_and_rollover() -> None:
    """每日推送配額按使用者計算，跨日自動重置。"""
    quota = SparkPushQuota(daily_limit=2)
    day1 = datetime(2026, 10, 19, 10, 0, tzinfo=timezone.utc)

    assert quota.consume(1, now=day1)[0] is True
    assert quota.consume(1, now=day1)[0] is True
    allowed, reason = quota.consume(1, now=day1)
    assert allowed is False
    assert "每日" in reason

    # 另一位使用者仍有額度
    assert quota.consume(2, now=day1)[0] is True

    day2 = day1 + timedelta(days=1)
    assert quota.consume(1, now=day2)[0] is True


# ===========================================================================
# 5. ADR 狀態更正
# ===========================================================================

def test_adr_0002_marks_r8_verified() -> None:
    """ADR-0002 必須以三段制描述實作狀態，並標記 R8 驗證。"""
    adr_path = Path(__file__).resolve().parents[2] / "Docs" / "adr" / "ADR-0002-governance-and-ai-evaluation.md"
    text = adr_path.read_text(encoding="utf-8")

    assert "Designed" in text
    assert "Implemented" in text
    assert "Verified" in text
    assert "R8" in text
    assert "spark_push" in text
