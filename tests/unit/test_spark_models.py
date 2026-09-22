"""tests/unit/test_spark_models.py - Spark 資料模型單元測試。

遵循 AAA (Arrange / Act / Assert) 原則。
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from handlers.spark.models import RssItem, Trigger, TriggerResult, TriggerType


def test_trigger_immutability() -> None:
    """測試 Trigger 為不可變 dataclass (frozen=True)。"""
    # Arrange
    trigger = Trigger(
        trigger_id="test-id-1",
        user_id=12345,
        channel_id=67890,
        trigger_type=TriggerType.PRICE.value,
        condition={"ticker": "BTC-USD", "target_price": 60000.0, "direction": "above"},
    )

    # Act & Assert
    with pytest.raises(FrozenInstanceError):
        trigger.is_active = False  # type: ignore[misc]


def test_trigger_serialization_roundtrip() -> None:
    """測試 Trigger 序列化為字典與反序列化還原完整性。"""
    # Arrange
    original = Trigger(
        trigger_id="uuid-abc-123",
        user_id=987654321,
        channel_id=123456789,
        trigger_type="schedule",
        condition={"mode": "daily_time", "time": "08:30"},
        message="早安！提醒檢查今日盤勢",
        is_active=True,
        cooldown_seconds=1800,
        created_at="2026-08-27T08:00:00+00:00",
        last_triggered_at="2026-08-27T08:30:00+00:00",
        extra_data={"tag": "daily"},
    )

    # Act
    data_dict = original.to_dict()
    restored = Trigger.from_dict(data_dict)

    # Assert
    assert restored.trigger_id == original.trigger_id
    assert restored.user_id == original.user_id
    assert restored.channel_id == original.channel_id
    assert restored.trigger_type == original.trigger_type
    assert restored.condition == original.condition
    assert restored.message == original.message
    assert restored.is_active == original.is_active
    assert restored.cooldown_seconds == original.cooldown_seconds
    assert restored.created_at == original.created_at
    assert restored.last_triggered_at == original.last_triggered_at
    assert restored.extra_data == original.extra_data


def test_trigger_result_defaults() -> None:
    """測試 TriggerResult 預設值與結構。"""
    # Arrange & Act
    res = TriggerResult(should_fire=True, current_value="BTC 突破 60k")

    # Assert
    assert res.should_fire is True
    assert res.current_value == "BTC 突破 60k"
    assert res.detail_data == {}


def test_rss_item_structure() -> None:
    """測試 RssItem 結構與屬性。"""
    # Arrange & Act
    item = RssItem(
        item_id="post-100",
        title="OpenAI 發表新架構",
        url="https://openai.com/news",
        published_at="2026-08-27",
        summary="摘要說明",
    )

    # Assert
    assert item.item_id == "post-100"
    assert item.title == "OpenAI 發表新架構"
    assert item.url == "https://openai.com/news"
    assert item.published_at == "2026-08-27"
    assert item.summary == "摘要說明"


def test_trigger_from_dict_legacy_compatibility_without_action() -> None:
    """測試舊版本資料庫中完全沒有 'action' 鍵時，反序列化安全降級為空字典。"""
    # Arrange: 模擬舊版 SQLite data_json 內容（無 action 欄位）
    legacy_data = {
        "trigger_id": "legacy-id-001",
        "user_id": 12345,
        "channel_id": 67890,
        "trigger_type": "schedule",
        "condition": {"mode": "interval", "interval_minutes": 10},
        "message": "傳一張大奶圖給我",
        "is_active": True,
        "cooldown_seconds": 60,
    }

    # Act
    trigger = Trigger.from_dict(legacy_data)

    # Assert
    assert trigger.trigger_id == "legacy-id-001"
    assert trigger.action == {}
    # 序列化依然合法健全
    serialized = trigger.to_dict()
    assert "action" in serialized
    assert serialized["action"] == {}


def test_trigger_from_dict_action_string_or_none_safe() -> None:
    """測試當 action 為 None 或歷史字串型別時，能安全反序列化而不拋出異常。"""
    # 1. action is None
    data_none = {"trigger_id": "t-none", "action": None}
    t_none = Trigger.from_dict(data_none)
    assert t_none.action == {}

    # 2. action is string (e.g. 舊架構曾用字串記錄動作類型)
    data_str = {"trigger_id": "t-str", "action": "notify"}
    t_str = Trigger.from_dict(data_str)
    assert t_str.action == {"action_type": "notify"}

    # 3. action is invalid non-dict/non-str (e.g. 數值或陣列)
    data_int = {"trigger_id": "t-int", "action": 123}
    t_int = Trigger.from_dict(data_int)
    assert t_int.action == {}


def test_trigger_to_dict_action_none_safe() -> None:
    """測試即使 Trigger 物件的 action 屬性為 None，to_dict 也絕對不會引發 TypeError。"""
    # Arrange
    trigger = Trigger(
        trigger_id="t-none-prop",
        user_id=1,
        channel_id=1,
        trigger_type="schedule",
        condition={},
        action=None,  # type: ignore[arg-type]
    )

    # Act
    d = trigger.to_dict()

    # Assert
    assert isinstance(d["action"], dict)
    assert d["action"] == {}

