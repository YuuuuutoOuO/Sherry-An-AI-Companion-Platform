"""tests/unit/test_spark_resilience_r11.py
=========================================
R11 SPARK 韌性與背景可觀測回歸測試（報告 §6.1.4、§6.1.5、§6.2.2、§6.4.1、§7.2 D5/D9）。

驗證：
1. checker 級 timeout：卡死檢查器不拖垮服務層。
2. 單輪 cycle timeout：整體 deadline 取消剩餘檢查並留下摘要。
3. daily catch-up：目標時間過後仍在補跑窗內可補一次，跨日不回溯。
4. 「立即測試」dry-run：不寫回 last_triggered／run_count、不關閉 one-shot。
5. 熱路徑 `except: pass` 已清除（至少記錄 debug）。
6. 優先序枚舉化與魔術數字清除。
7. SPARK 執行摘要落 SparkService 與 runtime 觀測事件。
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import settings
from handlers.spark.checkers.schedule_checker import ScheduleChecker
from handlers.spark.cog import SparkCog
from handlers.spark.models import Trigger, TriggerResult
from handlers.spark.repository import SparkRepository
from handlers.spark.service import SparkService

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def anyio_backend() -> str:
    """設定 anyio 測試後端為 asyncio。"""
    return "asyncio"


class FixedChecker:
    """回傳固定結果的 Fake 檢查器。"""

    def __init__(
        self,
        trigger_type: str,
        *,
        should_fire: bool,
        detail: dict[str, Any] | None = None,
        summary: str = "條件達標",
    ) -> None:
        self.trigger_type = trigger_type
        self._should_fire = should_fire
        self._detail = detail or {}
        self._summary = summary

    async def should_fire(self, trigger: Trigger) -> TriggerResult:
        return TriggerResult(
            should_fire=self._should_fire,
            current_value=self._summary,
            detail_data=dict(self._detail),
        )


class SlowChecker:
    """永遠睡著的檢查器，用於 timeout 測試。"""

    trigger_type = "slow"

    async def should_fire(self, trigger: Trigger) -> TriggerResult:
        await asyncio.sleep(5)
        return TriggerResult(should_fire=True, current_value="不應抵達")


def _build_service(
    tmp_path: Path, checkers: dict[str, Any]
) -> tuple[SparkService, SparkRepository]:
    repo = SparkRepository(file_path=tmp_path / "spark" / "triggers.json")
    return SparkService(repo, checkers), repo


def _trigger(trigger_id: str = "trig-r11-0001", trigger_type: str = "price") -> Trigger:
    return Trigger(
        trigger_id=trigger_id,
        user_id=123,
        channel_id=456,
        trigger_type=trigger_type,
        condition={"ticker": "NVDA", "target_price": 100.0},
        message="測試",
    )


# ===========================================================================
# 1. checker timeout
# ===========================================================================

@pytest.mark.anyio
async def test_checker_timeout_returns_structured_result_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """卡死 checker 由服務層 timeout 接住，回傳可辨識結果且不落狀態。"""
    service, repo = _build_service(tmp_path, {"slow": SlowChecker()})
    monkeypatch.setattr(settings, "SPARK_CHECKER_TIMEOUT_SECONDS", 0.02)
    trigger = service.create_trigger(
        user_id=123,
        channel_id=456,
        trigger_type="slow",
        condition={},
    )

    started = time.monotonic()
    result, updated = await service.check_trigger(trigger)
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert result.should_fire is False
    assert result.detail_data.get("timeout") is True
    assert "逾時" in result.current_value
    assert updated is None
    reloaded = repo.get(trigger.trigger_id)
    assert reloaded is not None
    assert reloaded.last_triggered_at == ""
    assert reloaded.extra_data.get("run_count", 0) == 0


# ===========================================================================
# 2. dry-run（立即測試）
# ===========================================================================

@pytest.mark.anyio
async def test_dry_run_does_not_mutate_state_or_consume_one_shot(
    tmp_path: Path,
) -> None:
    """dry-run 即使達標也不寫回 last_triggered、run_count 或 one-shot 狀態。"""
    detail = {
        "one_shot_fired": True,
        "last_item_id": "item-42",
        "last_content_hash": "hash-42",
    }
    service, repo = _build_service(
        tmp_path, {"price": FixedChecker("price", should_fire=True, detail=detail)}
    )
    trigger = service.create_trigger(
        user_id=123,
        channel_id=456,
        trigger_type="price",
        condition={"ticker": "NVDA", "target_price": 100.0},
        cooldown_seconds=60,
    )

    result, updated = await service.check_trigger(trigger, dry_run=True)

    assert result.should_fire is True
    assert result.detail_data == detail
    assert updated is None
    reloaded = repo.get(trigger.trigger_id)
    assert reloaded is not None
    assert reloaded.is_active is True
    assert reloaded.last_triggered_at == ""
    assert reloaded.extra_data.get("run_count", 0) == 0
    assert "last_item_id" not in reloaded.condition
    assert "last_content_hash" not in reloaded.extra_data


@pytest.mark.anyio
async def test_normal_check_still_persists_after_dry_run(tmp_path: Path) -> None:
    """dry-run 不影響正式巡檢路徑：非 dry-run 仍會寫回狀態。"""
    service, repo = _build_service(
        tmp_path,
        {
            "price": FixedChecker(
                "price", should_fire=True, detail={"last_price": 101.5}
            )
        },
    )
    trigger = service.create_trigger(
        user_id=123,
        channel_id=456,
        trigger_type="price",
        condition={"ticker": "NVDA", "target_price": 100.0},
    )

    _ = await service.check_trigger(trigger, dry_run=True)
    result, updated = await service.check_trigger(trigger)

    assert result.should_fire is True
    assert updated is not None
    assert updated.last_triggered_at != ""
    assert updated.condition.get("last_price") == 101.5
    reloaded = repo.get(trigger.trigger_id)
    assert reloaded is not None
    assert reloaded.last_triggered_at == updated.last_triggered_at


def test_all_immediate_test_entry_points_use_dry_run() -> None:
    """Discord 按鈕／前綴指令／Web API 三個「立即測試」入口都必須 dry-run。"""
    cog_src = (_REPO_ROOT / "handlers" / "spark" / "cog.py").read_text(encoding="utf-8")
    web_src = (_REPO_ROOT / "handlers" / "spark" / "web.py").read_text(encoding="utf-8")
    pattern = re.compile(r"check_trigger\([^)]*dry_run=True")
    matches = pattern.findall(cog_src) + pattern.findall(web_src)
    assert len(matches) == 3


# ===========================================================================
# 3. daily catch-up
# ===========================================================================

def _local(day: int, hour: int, minute: int) -> datetime:
    """建立 UTC+8 當地時間對應的 UTC datetime。"""
    return datetime(2026, 10, day, hour, minute, tzinfo=timezone(timedelta(hours=8))).astimezone(
        timezone.utc
    )


@pytest.mark.anyio
async def test_daily_catchup_fires_within_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    """目標 08:00、09:30 抵達時仍在 120 分鐘補跑窗內，應補跑一次。"""
    monkeypatch.setattr(settings, "SPARK_DAILY_CATCHUP_MINUTES", 120)
    checker = ScheduleChecker()
    trigger = Trigger(
        trigger_id="daily-catchup",
        user_id=1,
        channel_id=2,
        trigger_type="schedule",
        condition={"mode": "daily_time", "time": "08:00", "tz_offset": 8},
    )

    result = await checker.should_fire(trigger, now_dt=_local(20, 9, 30))

    assert result.should_fire is True
    assert result.detail_data.get("catchup") is True
    assert result.detail_data.get("late_minutes") == 90
    assert "補跑" in result.current_value


@pytest.mark.anyio
async def test_daily_catchup_window_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    """超過補跑窗即不補跑，避免午後突然補發早晨提醒。"""
    monkeypatch.setattr(settings, "SPARK_DAILY_CATCHUP_MINUTES", 120)
    checker = ScheduleChecker()
    trigger = Trigger(
        trigger_id="daily-expired",
        user_id=1,
        channel_id=2,
        trigger_type="schedule",
        condition={"mode": "daily_time", "time": "08:00", "tz_offset": 8},
    )

    result = await checker.should_fire(trigger, now_dt=_local(20, 10, 31))

    assert result.should_fire is False
    assert "補跑窗" in result.current_value


@pytest.mark.anyio
async def test_daily_catchup_does_not_refire_same_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """今日已觸發過就不再補跑。"""
    monkeypatch.setattr(settings, "SPARK_DAILY_CATCHUP_MINUTES", 120)
    checker = ScheduleChecker()
    trigger = Trigger(
        trigger_id="daily-fired",
        user_id=1,
        channel_id=2,
        trigger_type="schedule",
        condition={"mode": "daily_time", "time": "08:00", "tz_offset": 8},
        last_triggered_at=_local(20, 8, 5).isoformat(),
    )

    result = await checker.should_fire(trigger, now_dt=_local(20, 9, 30))

    assert result.should_fire is False
    assert "今日排程已於" in result.current_value


@pytest.mark.anyio
async def test_daily_catchup_can_be_disabled_by_condition() -> None:
    """catchup_minutes=0 時回歸精確分鐘觸發，錯過即不補。"""
    checker = ScheduleChecker()
    trigger = Trigger(
        trigger_id="daily-disabled",
        user_id=1,
        channel_id=2,
        trigger_type="schedule",
        condition={
            "mode": "daily_time",
            "time": "08:00",
            "tz_offset": 8,
            "catchup_minutes": 0,
        },
    )

    missed = await checker.should_fire(trigger, now_dt=_local(20, 8, 1))
    exact = await checker.should_fire(trigger, now_dt=_local(20, 8, 0))

    assert missed.should_fire is False
    assert exact.should_fire is True
    assert exact.detail_data.get("catchup") is False


# ===========================================================================
# 4. 單輪 cycle timeout 與執行摘要
# ===========================================================================

def _build_cog(service: MagicMock) -> SparkCog:
    cog = SparkCog(bot=MagicMock(), service=service)
    cog.action_runner = MagicMock()
    cog.action_runner.run_action = AsyncMock(return_value=True)
    return cog


@pytest.mark.anyio
async def test_cycle_timeout_cancels_stuck_checkers_and_records_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """單一卡死 checker 不得讓整輪巡檢永久停擺。"""
    service = MagicMock()
    service.list_active_triggers.return_value = [_trigger()]
    service.is_in_cooldown.return_value = False

    async def _stuck(_trigger: Trigger) -> tuple[TriggerResult, Trigger | None]:
        await asyncio.sleep(5)
        return TriggerResult(should_fire=True, current_value="不應抵達"), None

    service.check_trigger = AsyncMock(side_effect=_stuck)
    cog = _build_cog(service)
    monkeypatch.setattr(settings, "SPARK_CHECK_CYCLE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr("handlers.spark.cog.log_event", MagicMock())

    started = time.monotonic()
    await cog._run_check_cycle()
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert len(cog._action_tasks) == 0
    service.record_cycle_summary.assert_called_once()
    summary = service.record_cycle_summary.call_args.args[0]
    assert summary["cycle_timed_out"] is True
    assert summary["triggers"] == 1
    assert summary["actions_dispatched"] == 0
    assert summary["duration_ms"] >= 0


@pytest.mark.anyio
async def test_cycle_summary_reports_success_and_emits_observability_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """成功派發時摘要計數正確，並寫入 spark_cycle 觀測事件。"""
    service = MagicMock()
    service.list_active_triggers.return_value = [_trigger()]
    service.is_in_cooldown.return_value = False
    service.check_trigger = AsyncMock(
        return_value=(TriggerResult(should_fire=True, current_value="達標"), None)
    )
    service.consume_push_quota.return_value = (True, "核准")
    cog = _build_cog(service)
    fake_log = MagicMock()
    monkeypatch.setattr("handlers.spark.cog.log_event", fake_log)
    monkeypatch.setattr("handlers.spark.cog.new_trace_id", lambda prefix="spark": "spark-r11-test")

    await cog._run_check_cycle()
    await asyncio.gather(*cog._action_tasks)

    summary = service.record_cycle_summary.call_args.args[0]
    assert summary["fired"] == 1
    assert summary["actions_dispatched"] == 1
    assert summary["failed"] == 0
    assert summary["cycle_timed_out"] is False

    fake_log.assert_called_once()
    kwargs = fake_log.call_args.kwargs
    assert kwargs["event"] == "spark_cycle"
    assert kwargs["trace_id"] == "spark-r11-test"
    assert kwargs["fired"] == 1
    assert kwargs["actions_dispatched"] == 1


@pytest.mark.anyio
async def test_cycle_summary_counts_guard_and_quota_rejections() -> None:
    """治理閘門或配額拒絕需在摘要中可見，而非只留 warning。"""
    service = MagicMock()
    service.list_active_triggers.return_value = [_trigger("trig-r11-guard"), _trigger("trig-r11-quota")]
    service.is_in_cooldown.return_value = False
    service.check_trigger = AsyncMock(
        return_value=(TriggerResult(should_fire=True, current_value="達標"), None)
    )
    cog = _build_cog(service)

    guard_decision = MagicMock()
    guard_decision.allowed = False
    guard_decision.reason = "測試熔斷"
    cog._autonomy_guard = MagicMock()
    cog._autonomy_guard.check.return_value = guard_decision

    await cog._run_check_cycle()
    summary = service.record_cycle_summary.call_args.args[0]
    assert summary["guard_rejected"] == 2
    assert summary["actions_dispatched"] == 0
    assert len(cog._action_tasks) == 0


@pytest.mark.anyio
async def test_cycle_summary_exposed_through_stats(tmp_path: Path) -> None:
    """最近一輪摘要可從 SparkService.get_stats() 取得（WebUI /api/spark/stats）。"""
    service, _ = _build_service(tmp_path, {})
    service.record_cycle_summary({"triggers": 3, "fired": 1})

    stats = service.get_stats()

    assert stats["last_cycle"]["triggers"] == 3
    assert stats["last_cycle"]["fired"] == 1


# ===========================================================================
# 5. 高風險靜默失敗與優先序清理
# ===========================================================================

def test_hot_path_except_pass_removed() -> None:
    """SPARK／健康巡檢／觀測熱路徑不得再以 `except: pass` 吞錯。"""
    targets = [
        _REPO_ROOT / "handlers" / "spark" / "cog.py",
        _REPO_ROOT / "handlers" / "bot_brain" / "health_monitor.py",
        _REPO_ROOT / "handlers" / "social" / "observability.py",
    ]
    pattern = re.compile(r"except[^\n:]*:\s*\n\s+pass\b")
    offenders = {
        str(path.relative_to(_REPO_ROOT)): pattern.findall(path.read_text(encoding="utf-8"))
        for path in targets
    }
    assert all(not hits for hits in offenders.values()), offenders


def test_priority_enum_and_magic_numbers_removed() -> None:
    """優先序以語意枚舉表示，且高風險魔術數字已清除。"""
    assert settings.Priority.INTERACTIVE < settings.Priority.DRAW
    assert settings.Priority.DRAW < settings.Priority.TOOL_BACKGROUND
    assert settings.Priority.TOOL_BACKGROUND < settings.Priority.MAINTENANCE
    assert settings.priority_for(settings.Priority.INTERACTIVE) == settings.PRIORITY_TAG_RESPONSE
    assert settings.priority_for(settings.Priority.TOOL_BACKGROUND) == settings.PRIORITY_HEARTBEAT

    utils_src = (_REPO_ROOT / "handlers" / "social" / "utils.py").read_text(encoding="utf-8")
    weather_src = (
        _REPO_ROOT / "handlers" / "social" / "tools" / "weather_tool.py"
    ).read_text(encoding="utf-8")

    assert "priority=20" not in utils_src
    assert "Priority.TOOL_BACKGROUND" in utils_src
    assert "priority=0," not in weather_src
    assert "Priority.INTERACTIVE" in weather_src

    # _parse_priority_env 測試（相容 Python 3.11+ IntEnum str 改變與各種設定格式）
    from settings import _parse_priority_env
    assert _parse_priority_env("NON_EXISTENT_KEY_12345", settings.Priority.INTERACTIVE) == 1
    import os
    os.environ["__TEST_PRIORITY_ENV_1"] = "42"
    assert _parse_priority_env("__TEST_PRIORITY_ENV_1", settings.Priority.IDLE) == 42
    os.environ["__TEST_PRIORITY_ENV_2"] = "DRAW"
    assert _parse_priority_env("__TEST_PRIORITY_ENV_2", settings.Priority.IDLE) == 5
    os.environ["__TEST_PRIORITY_ENV_3"] = "Priority.INTERACTIVE"
    assert _parse_priority_env("__TEST_PRIORITY_ENV_3", settings.Priority.IDLE) == 1
    os.environ["__TEST_PRIORITY_ENV_4"] = "INVALID_NAME"
    assert _parse_priority_env("__TEST_PRIORITY_ENV_4", settings.Priority.IDLE) == 99
