"""tests/unit/test_spark_repository.py - Spark 持久化倉儲單元測試。

遵循 AAA (Arrange / Act / Assert) 原則。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from handlers.spark.models import Trigger
from handlers.spark.repository import SparkRepository


@pytest.fixture
def temp_repo(tmp_path: Path) -> SparkRepository:
    """提供隔離的 SparkRepository 實例。"""
    file_path = tmp_path / "spark" / "triggers.json"
    return SparkRepository(file_path=file_path)


def test_repository_crud_operations(temp_repo: SparkRepository) -> None:
    """測試倉儲層 CRUD 完整操作。"""
    # Arrange
    trigger1 = Trigger(
        trigger_id="t-1",
        user_id=101,
        channel_id=201,
        trigger_type="price",
        condition={"ticker": "ETH-USD", "target_price": 3000.0},
        message="以太幣提醒",
        is_active=True,
    )
    trigger2 = Trigger(
        trigger_id="t-2",
        user_id=102,
        channel_id=202,
        trigger_type="schedule",
        condition={"mode": "interval", "interval_minutes": 30},
        is_active=False,
    )

    # Act 1: Save
    temp_repo.save(trigger1)
    temp_repo.save(trigger2)

    # Assert 1: Get & List
    assert temp_repo.get("t-1") == trigger1
    assert temp_repo.get("t-2") == trigger2
    assert len(temp_repo.list_all()) == 2
    assert len(temp_repo.list_active()) == 1
    assert temp_repo.list_active()[0].trigger_id == "t-1"
    assert len(temp_repo.list_by_user(101)) == 1

    # Act 2: Reload from disk
    new_repo_inst = SparkRepository(file_path=temp_repo._file_path)
    # Assert 2: Persistence
    assert len(new_repo_inst.list_all()) == 2
    assert new_repo_inst.get("t-1") is not None

    # Act 3: Delete
    del_res = temp_repo.delete("t-1")
    # Assert 3: Deletion
    assert del_res is True
    assert temp_repo.get("t-1") is None
    assert len(temp_repo.list_all()) == 1
    assert temp_repo.delete("non-existent") is False


def test_repository_stats(temp_repo: SparkRepository) -> None:
    """測試倉儲統計資訊統計計算。"""
    # Arrange
    temp_repo.save(
        Trigger(
            trigger_id="t-p1",
            user_id=1,
            channel_id=1,
            trigger_type="price",
            condition={},
            is_active=True,
        )
    )
    temp_repo.save(
        Trigger(
            trigger_id="t-p2",
            user_id=1,
            channel_id=1,
            trigger_type="price",
            condition={},
            is_active=False,
        )
    )
    temp_repo.save(
        Trigger(
            trigger_id="t-r1",
            user_id=1,
            channel_id=1,
            trigger_type="rss",
            condition={},
            is_active=True,
        )
    )

    # Act
    stats = temp_repo.get_stats()

    # Assert
    assert stats["total"] == 3
    assert stats["active"] == 2
    assert stats["paused"] == 1
    assert stats["by_type"]["price"] == 2
    assert stats["by_type"]["rss"] == 1
