"""心智狀態持久化：重啟後不歸零，離線期間有上限補算。"""

from __future__ import annotations

import json
import time

import pytest

import settings
from handlers.care import surfer


@pytest.fixture()
def isolated_mind(tmp_path, monkeypatch):
    """把心智狀態檔導到暫存目錄，並在測試後還原全域狀態。"""
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(surfer, "_mind_state_loaded", False, raising=False)
    monkeypatch.setattr(surfer, "_mind_persistence_enabled", False, raising=False)
    monkeypatch.setattr(surfer, "_last_mind_persist_at", 0.0, raising=False)
    snapshot = dict(surfer._mind_state)
    try:
        yield tmp_path
    finally:
        surfer._mind_state.clear()
        surfer._mind_state.update(snapshot)
        surfer._mind_state_loaded = False


def test_mind_state_survives_restart(isolated_mind):
    surfer._mind_state["social_satiety"] = 34.0
    surfer._mind_state["curiosity_satiety"] = 41.0
    surfer._mind_state["last_thought"] = "好想找人講話"

    surfer._persist_mind_state(force=True)
    assert (isolated_mind / surfer._MIND_STATE_FILENAME).exists()

    # 模擬程式重啟：記憶體歸零回預設值。
    surfer._mind_state["social_satiety"] = 72.0
    surfer._mind_state["curiosity_satiety"] = 68.0
    surfer._mind_state["last_thought"] = ""

    assert surfer.load_mind_state(force=True) is True
    assert surfer._mind_state["social_satiety"] <= 34.0
    assert surfer._mind_state["boredom_level"] >= 66.0
    assert surfer._mind_state["last_thought"] == "好想找人講話"


def test_mind_state_offline_catchup_is_capped(isolated_mind):
    path = isolated_mind / surfer._MIND_STATE_FILENAME
    path.write_text(
        json.dumps(
            {
                "social_satiety": 90.0,
                "curiosity_satiety": 90.0,
                "saved_at": time.time() - 10 * 3600,  # 離線十小時
            }
        ),
        encoding="utf-8",
    )
    surfer._mind_state["social_satiety"] = 100.0
    surfer._mind_state["curiosity_satiety"] = 100.0

    surfer.load_mind_state(force=True)

    # 最多只補算 10 分鐘，normal 節奏 1.8/分 → 約 18 分。
    assert surfer._mind_state["social_satiety"] == pytest.approx(72.0, abs=1.0)


def test_load_mind_state_without_file_keeps_defaults(isolated_mind):
    surfer._mind_state["social_satiety"] = 55.0
    assert surfer.load_mind_state(force=True) is False
    assert surfer._mind_state["social_satiety"] == 55.0


def test_persistence_is_off_until_enabled(isolated_mind):
    """單元測試與未啟動的流程不該寫入執行期存檔。"""
    path = isolated_mind / surfer._MIND_STATE_FILENAME
    surfer._mind_state["social_satiety"] = 10.0

    surfer._persist_mind_state()
    assert not path.exists()

    surfer.enable_mind_persistence()
    surfer._persist_mind_state()
    assert path.exists()
