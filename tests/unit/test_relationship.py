"""
tests/unit/test_relationship.py
===============================
使用者社交關係管理器 (RelationshipManager) 單元測試。
涵蓋 SQLite 持久化、好感度增減 (update_affinity)、等級判定 (affinity_tier/tier)、每日衰減與 Prompt 摘要。
"""

from __future__ import annotations

from pathlib import Path
import pytest

import handlers.memory.relationship as rel_mod
from handlers.memory.relationship import RelationshipManager


@pytest.fixture
def temp_rel_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔離測試環境的 SQLite 資料庫路徑與 JSON 檔案路徑。"""
    db_file = tmp_path / "test_memory.db"
    json_file = tmp_path / "test_user_relations.json"
    monkeypatch.setattr(rel_mod, "MEMORY_DB_PATH", db_file)
    monkeypatch.setattr(rel_mod.settings, "USER_RELATIONS_PATH", json_file)
    rel_mod._INITIALIZED_RELATIONS_DBS.clear()
    return db_file


def test_relationship_tiers_and_snippet(temp_rel_db: Path) -> None:
    """驗證等級閾值判定與 Prompt 關係狀態摘要。"""
    mgr = RelationshipManager(debounce_seconds=0.0)
    uid = 12345

    # 初始為陌生人 (0.0)
    assert mgr.get_affinity(uid) == 0.0
    assert mgr.get_affinity_tier(uid) == "stranger"
    assert "陌生人" in mgr.get_relationship_snippet(uid)

    # 累加至認識的人 (25.0)
    mgr.store[str(uid)] = {"affinity": 25.0, "affinity_tier": "acquaintance"}
    assert mgr.get_affinity(uid) == 25.0
    assert mgr.get_affinity_tier(uid) == "acquaintance"
    assert "認識的人" in mgr.get_relationship_snippet(uid)

    # 累加至朋友 (55.0)
    mgr.store[str(uid)] = {"affinity": 55.0, "affinity_tier": "friend"}
    assert mgr.get_affinity_tier(uid) == "friend"
    assert "朋友" in mgr.get_relationship_snippet(uid)

    # 累加至親近的人 (85.0)
    mgr.store[str(uid)] = {"affinity": 85.0, "affinity_tier": "close"}
    assert mgr.get_affinity_tier(uid) == "close"
    assert "親近的人" in mgr.get_relationship_snippet(uid)


def test_update_affinity_and_daily_cap(temp_rel_db: Path) -> None:
    """測試 update_affinity 正常累加與單日上限保護。"""
    mgr = RelationshipManager(debounce_seconds=0.0)
    uid = 99901

    # 增加 5.0
    mgr.update_affinity(uid, 5.0)
    assert mgr.get_affinity(uid) == 5.0
    assert mgr.get_affinity_tier(uid) == "stranger"

    # 再增加 10.0 (受單日上限 DAILY_AFFINITY_GAIN_LIMIT=10.0 限制，至多再得 5.0)
    mgr.update_affinity(uid, 10.0)
    assert mgr.get_affinity(uid) == 10.0

    # 再次增加應被忽略（已達當日上限）
    mgr.update_affinity(uid, 2.0)
    assert mgr.get_affinity(uid) == 10.0


def test_sqlite_persistence_and_reload(temp_rel_db: Path) -> None:
    """驗證好感度與 affinity_tier/tier 欄位寫入 SQLite 並可重新載入。"""
    mgr1 = RelationshipManager(debounce_seconds=0.0)
    uid1 = 88801
    uid2 = 88802

    mgr1.store[str(uid1)] = {
        "affinity": 85.0,
        "affinity_tier": "close",
        "interaction_count": 10,
        "last_interaction": "2026-09-24T00:00:00Z",
    }
    mgr1.store[str(uid2)] = {
        "affinity": 15.0,
        "affinity_tier": "stranger",
        "interaction_count": 2,
        "last_interaction": "2026-09-24T00:00:00Z",
    }
    mgr1.flush(force=True)

    # 模擬重新啟動載入
    mgr2 = RelationshipManager(debounce_seconds=0.0)
    assert mgr2.get_affinity(uid1) == 85.0
    assert mgr2.get_affinity_tier(uid1) == "close"
    assert mgr2.store[str(uid1)]["affinity_tier"] == "close"
    assert mgr2.store[str(uid1)]["tier"] == "close"

    assert mgr2.get_affinity(uid2) == 15.0
    assert mgr2.get_affinity_tier(uid2) == "stranger"
    assert mgr2.store[str(uid2)]["affinity_tier"] == "stranger"
    assert mgr2.store[str(uid2)]["tier"] == "stranger"


def test_decay_all(temp_rel_db: Path) -> None:
    """驗證每日好感度自然衰減邏輯。"""
    mgr = RelationshipManager(debounce_seconds=0.0)
    uid = 77701
    mgr.store[str(uid)] = {"affinity": 50.0, "affinity_tier": "friend"}
    mgr._last_decay_date = "2026-01-01"

    affected = mgr.decay_all()
    assert affected == 1
    # 預設 DAILY_DECAY = 0.5
    assert mgr.get_affinity(uid) == 49.5

    # 當日重複呼叫不應重複衰減
    affected_second = mgr.decay_all()
    assert affected_second == 0
    assert mgr.get_affinity(uid) == 49.5
