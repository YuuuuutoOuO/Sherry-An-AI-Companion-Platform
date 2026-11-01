"""tests/unit/test_spark_service.py - Spark 服務層單元測試。

遵循 AAA (Arrange / Act / Assert) 原則。
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from handlers.spark.models import Trigger, TriggerResult
from handlers.spark.repository import SparkRepository
from handlers.spark.service import SparkService, build_spark_service


@pytest.fixture
def anyio_backend() -> str:
    """設定 anyio 測試後端為 asyncio。"""
    return "asyncio"


class DummyChecker:
    """可設定回傳結果的 Fake 檢查器。"""

    def __init__(
        self,
        trigger_type: str,
        will_fire: bool,
        summary: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.trigger_type = trigger_type
        self.will_fire = will_fire
        self.summary = summary
        self.detail = detail or {}

    async def should_fire(self, trigger: Trigger) -> TriggerResult:
        return TriggerResult(
            should_fire=self.will_fire,
            current_value=self.summary,
            detail_data=self.detail,
        )


@pytest.fixture
def service_setup(tmp_path: Path) -> tuple[SparkService, SparkRepository]:
    """建立測試用 SparkService 與臨時倉儲。"""
    repo = SparkRepository(file_path=tmp_path / "spark" / "triggers.json")
    checkers = {
        "price": DummyChecker("price", will_fire=True, summary="現價達標", detail={"last_price": 70000.0}),
        "weather": DummyChecker("weather", will_fire=False, summary="無警報"),
    }
    service = SparkService(repo, checkers)
    return service, repo


def test_service_cooldown_calculation(service_setup: tuple[SparkService, SparkRepository]) -> None:
    """測試冷卻時間計算邏輯。"""
    # Arrange
    service, _ = service_setup
    now = datetime.now(timezone.utc)

    # 1. 尚未觸發過 (無 last_triggered_at) -> 非冷卻
    t1 = Trigger(trigger_id="1", user_id=1, channel_id=1, trigger_type="price", condition={}, cooldown_seconds=3600)
    assert service.is_in_cooldown(t1, now) is False

    # 2. 10 分鐘前觸發過，冷卻 60 分鐘 -> 冷卻中
    past_10m = (now - timedelta(minutes=10)).isoformat()
    t2 = Trigger(trigger_id="2", user_id=1, channel_id=1, trigger_type="price", condition={}, last_triggered_at=past_10m, cooldown_seconds=3600)
    assert service.is_in_cooldown(t2, now) is True

    # 3. 70 分鐘前觸發過，冷卻 60 分鐘 -> 已脫離冷卻
    past_70m = (now - timedelta(minutes=70)).isoformat()
    t3 = Trigger(trigger_id="3", user_id=1, channel_id=1, trigger_type="price", condition={}, last_triggered_at=past_70m, cooldown_seconds=3600)
    assert service.is_in_cooldown(t3, now) is False


@pytest.mark.anyio
async def test_service_check_trigger_flow(service_setup: tuple[SparkService, SparkRepository]) -> None:
    """測試 check_trigger 成功觸發後的狀態更新與持久化。"""
    # Arrange
    service, repo = service_setup
    trigger = service.create_trigger(
        user_id=100,
        channel_id=200,
        trigger_type="price",
        condition={"ticker": "BTC-USD", "target_price": 65000.0},
        message="提醒",
        cooldown_seconds=1800,
    )

    # Act
    res, updated = await service.check_trigger(trigger)

    # Assert
    assert res.should_fire is True
    assert res.current_value == "現價達標"
    assert updated is not None
    assert updated.last_triggered_at != ""
    assert updated.condition["last_price"] == 70000.0

    # 確認已更新至倉儲
    reloaded = repo.get(trigger.trigger_id)
    assert reloaded is not None
    assert reloaded.last_triggered_at == updated.last_triggered_at


@pytest.mark.anyio
async def test_service_one_shot_auto_pause(service_setup: tuple[SparkService, SparkRepository]) -> None:
    """測試單次達標 (one_shot) 觸發後自動變更為暫停/完成狀態 (is_active=False)。"""
    # Arrange
    service, repo = service_setup
    service._checkers["price"] = DummyChecker(
        "price",
        will_fire=True,
        summary="NVDA 達標",
        detail={"last_triggered_price": 120.0, "one_shot_fired": True},
    )
    trigger = service.create_trigger(
        user_id=100,
        channel_id=200,
        trigger_type="price",
        condition={"ticker": "NVDA", "target_price": 100.0, "one_shot": True},
        is_active=True,
    )

    # Act
    res, updated = await service.check_trigger(trigger)

    # Assert
    assert res.should_fire is True
    assert updated is not None
    assert updated.is_active is False
    assert updated.condition["last_triggered_price"] == 120.0

    # 確認持久化狀態為暫停
    reloaded = repo.get(trigger.trigger_id)
    assert reloaded is not None
    assert reloaded.is_active is False


def test_service_toggle_and_crud(service_setup: tuple[SparkService, SparkRepository]) -> None:
    """測試建立、切換狀態與刪除操作。"""
    # Arrange
    service, _ = service_setup
    t = service.create_trigger(
        user_id=1,
        channel_id=2,
        trigger_type="weather",
        condition={"city": "台北"},
        is_active=True,
    )

    # Act 1: Toggle to Paused
    t_paused = service.toggle_trigger(t.trigger_id)
    # Assert 1
    assert t_paused is not None
    assert t_paused.is_active is False

    # Act 2: Toggle to Active
    t_active = service.toggle_trigger(t.trigger_id)
    # Assert 2
    assert t_active is not None
    assert t_active.is_active is True

    # Act 3: Delete
    del_ok = service.delete_trigger(t.trigger_id)
    # Assert 3
    assert del_ok is True
    assert service.get_trigger(t.trigger_id) is None


def test_build_spark_service_factory(tmp_path: Path) -> None:
    """測試 build_spark_service 工廠函式。"""
    # Arrange
    repo = SparkRepository(file_path=tmp_path / "triggers.json")
    clients = {
        "stock": DummyChecker("stock", False),
        "rss": DummyChecker("rss", False),
        "weather": DummyChecker("weather", False),
    }

    # Act
    svc = build_spark_service(repo, clients)

    # Assert
    assert isinstance(svc, SparkService)
    assert "price" in svc._checkers
    assert "schedule" in svc._checkers
    assert "rss" in svc._checkers
    assert "weather" in svc._checkers


@pytest.mark.anyio
async def test_service_check_trigger_preserves_action(
    service_setup: tuple[SparkService, SparkRepository],
) -> None:
    """測試 check_trigger 成功觸發後，Trigger.action 依然完整保留，絕不被沖刷為空字典。"""
    # Arrange
    service, repo = service_setup
    service._checkers["schedule"] = DummyChecker("schedule", will_fire=True, summary="排程時間到")
    action_data = {
        "action_type": "core_tool",
        "target": "PIXIV",
        "payload": {"query": "大奶"},
    }
    trigger = service.create_trigger(
        user_id=123,
        channel_id=456,
        trigger_type="schedule",
        condition={"mode": "interval", "interval_minutes": 10},
        message="傳送一張大奶圖給 Yuuto",
        cooldown_seconds=60,
        action=action_data,
    )
    assert trigger.action == action_data

    # Act
    res, updated = await service.check_trigger(trigger)

    # Assert
    assert res.should_fire is True
    assert updated is not None
    # 關鍵斷言：更新後的 Trigger 物件必須保留 action 設定！
    assert updated.action == action_data
    assert updated.action.get("target") == "PIXIV"

    # 驗證資料庫持久化後重新載入依然保有 action
    reloaded = repo.get(trigger.trigger_id)
    assert reloaded is not None
    assert reloaded.action == action_data


def test_service_toggle_trigger_preserves_action(
    service_setup: tuple[SparkService, SparkRepository],
) -> None:
    """測試 toggle_trigger 切換狀態後，Trigger.action 依然完整保留。"""
    # Arrange
    service, _ = service_setup
    action_data = {
        "action_type": "core_tool",
        "target": "CHAT",
        "payload": {"query": "講個笑話"},
    }
    trigger = service.create_trigger(
        user_id=123,
        channel_id=456,
        trigger_type="schedule",
        condition={"mode": "daily_time", "time": "08:00"},
        action=action_data,
    )

    # Act
    t_paused = service.toggle_trigger(trigger.trigger_id)

    # Assert
    assert t_paused is not None
    assert t_paused.is_active is False
    assert t_paused.action == action_data


def test_service_update_trigger_memory(
    service_setup: tuple[SparkService, SparkRepository],
) -> None:
    """測試 SparkService.update_trigger_memory 寫回跨輪記憶與時間戳記。"""
    service, repo = service_setup
    trigger = service.create_trigger(
        user_id=123,
        channel_id=456,
        trigger_type="schedule",
        condition={"mode": "interval", "interval_minutes": 10},
        message="測試記憶寫回",
    )
    assert trigger.extra_data.get("last_output_summary") is None

    updated = service.update_trigger_memory(trigger.trigger_id, "今日台股大漲200點，0050收盤180元")
    assert updated is not None
    assert updated.extra_data.get("last_output_summary") == "今日台股大漲200點，0050收盤180元"
    assert updated.extra_data.get("last_output_at") is not None

    # 驗證持久化重載
    reloaded = repo.get(trigger.trigger_id)
    assert reloaded is not None
    assert reloaded.extra_data.get("last_output_summary") == "今日台股大漲200點，0050收盤180元"


@pytest.mark.anyio
async def test_service_run_count_increment_on_fire(
    service_setup: tuple[SparkService, SparkRepository],
) -> None:
    """測試 Trigger 每次成功觸發時，extra_data.run_count 正確累加。"""
    service, _ = service_setup
    service._checkers["schedule"] = DummyChecker("schedule", will_fire=True, summary="時間到")
    trigger = service.create_trigger(
        user_id=123,
        channel_id=456,
        trigger_type="schedule",
        condition={"mode": "interval", "interval_minutes": 10},
        message="計數測試",
    )
    assert trigger.extra_data.get("run_count", 0) == 0

    res, updated = await service.check_trigger(trigger)
    assert res.should_fire is True
    assert updated is not None
    assert updated.extra_data.get("run_count") == 1


def test_build_spark_service_registers_web_monitor() -> None:
    """測試工廠函式正確註冊 web_monitor 檢查器。"""
    repo = MagicMock(spec=SparkRepository)
    clients = {
        "stock": MagicMock(),
        "rss": MagicMock(),
        "weather": MagicMock(),
        "web_monitor": MagicMock(),
    }
    svc = build_spark_service(repo, clients)
    assert "web_monitor" in svc._checkers


