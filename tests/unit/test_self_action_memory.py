"""tests/unit/test_self_action_memory.py
=========================================
單元測試：雪莉行為記憶（Self Action Memory）。

驗證：
  1. 行為寫入 / 查詢 / 去重 / 保留期限清理。
  2. Prompt 片段只在「自我指涉」問題時才產生（平常 0 Token）。
  3. 音樂工具摘要能正確萃取歌名。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from handlers.memory import self_memory
from handlers.social.action_recorder import build_action_summary


@pytest.fixture
def self_mem_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test_self_action_memory.db"
    self_memory.init_self_memory_db(db_path)
    return db_path


def test_record_and_list_action(self_mem_db: Path) -> None:
    now = time.time()
    aid = self_memory.record_self_action(
        "MUSIC",
        "音樂操作「千本櫻」",
        verb="音樂操作",
        guild_id=456,
        channel_id=123,
        user_id=789,
        user_name="Yuuto",
        detail={"title": "千本櫻"},
        now=now - 120,
        db_path=self_mem_db,
    )
    assert aid is not None and aid.startswith("act-")

    rows = self_memory.list_self_actions(db_path=self_mem_db)
    assert len(rows) == 1
    assert rows[0]["tool"] == "MUSIC"
    assert rows[0]["summary"] == "音樂操作「千本櫻」"
    assert rows[0]["detail"]["title"] == "千本櫻"
    assert rows[0]["channel_id"] == "123"


def test_duplicate_action_is_deduplicated(self_mem_db: Path) -> None:
    now = time.time()
    first = self_memory.record_self_action(
        "MUSIC", "音樂操作「千本櫻」", channel_id=123, now=now, db_path=self_mem_db
    )
    second = self_memory.record_self_action(
        "MUSIC", "音樂操作「千本櫻」", channel_id=123, now=now + 5, db_path=self_mem_db
    )
    assert first == second
    assert len(self_memory.list_self_actions(db_path=self_mem_db)) == 1


def test_snippet_is_empty_for_unrelated_query(self_mem_db: Path) -> None:
    self_memory.record_self_action(
        "MUSIC", "音樂操作「千本櫻」", channel_id=123, now=time.time() - 60, db_path=self_mem_db
    )
    assert (
        self_memory.get_action_memory_snippet(
            "今天天氣如何？", channel_id=123, db_path=self_mem_db
        )
        == ""
    )


def test_snippet_recalls_recent_action_on_self_reference(self_mem_db: Path) -> None:
    self_memory.record_self_action(
        "MUSIC", "音樂操作「千本櫻」", channel_id=123, now=time.time() - 60, db_path=self_mem_db
    )
    snippet = self_memory.get_action_memory_snippet(
        "你剛剛播什麼歌？", channel_id=123, db_path=self_mem_db
    )
    assert "千本櫻" in snippet
    assert "行為記憶" in snippet
    assert "1 分鐘前" in snippet


def test_snippet_triggers_on_user_phrasing(self_mem_db: Path) -> None:
    self_memory.record_self_action(
        "MUSIC", "音樂操作「千本櫻」", channel_id=123, now=time.time() - 60, db_path=self_mem_db
    )
    snippet = self_memory.get_action_memory_snippet(
        "我剛剛點的歌是什麼？", channel_id=123, db_path=self_mem_db
    )
    assert "千本櫻" in snippet


def test_snippet_ignores_casual_phrase(self_mem_db: Path) -> None:
    self_memory.record_self_action(
        "MUSIC", "音樂操作「千本櫻」", channel_id=123, now=time.time() - 60, db_path=self_mem_db
    )
    assert (
        self_memory.get_action_memory_snippet(
            "你開心嗎？", channel_id=123, db_path=self_mem_db
        )
        == ""
    )


def test_snippet_falls_back_to_other_channels(self_mem_db: Path) -> None:
    self_memory.record_self_action(
        "SEARCH", "網路搜尋「台北天氣」", channel_id=999, now=time.time() - 30, db_path=self_mem_db
    )
    snippet = self_memory.get_action_memory_snippet(
        "你記得你剛剛查了什麼嗎？", channel_id=123, db_path=self_mem_db
    )
    assert "台北天氣" in snippet


def test_purge_old_actions(self_mem_db: Path) -> None:
    old = time.time() - 20 * 86400
    self_memory.record_self_action(
        "SEARCH", "網路搜尋「舊資料」", channel_id=123, now=old, db_path=self_mem_db
    )
    self_memory.record_self_action(
        "SEARCH", "網路搜尋「新資料」", channel_id=123, now=time.time(), db_path=self_mem_db
    )
    removed = self_memory.purge_old_self_actions(days=14, db_path=self_mem_db)
    assert removed == 1
    rows = self_memory.list_self_actions(db_path=self_mem_db)
    assert len(rows) == 1
    assert "新資料" in rows[0]["summary"]


def test_music_summary_extracts_title() -> None:
    summary, detail = build_action_summary(
        "MUSIC",
        "幫我播千本櫻",
        "我收到神秘的電波了。沒問題...這就發功幫你找出：「千本櫻」！🎵",
    )
    assert "千本櫻" in summary
    assert detail["title"] == "千本櫻"


def test_search_summary_uses_user_input() -> None:
    summary, detail = build_action_summary(
        "SEARCH", "台北天氣", "多雲 24°C，降雨機率 20%"
    )
    assert "台北天氣" in summary
    assert detail["input"] == "台北天氣"
