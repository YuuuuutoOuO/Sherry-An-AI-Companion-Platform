"""
tests/unit/test_world_memory.py
===============================
世界記憶系統（World Memory）單元測試。
涵蓋跨伺服器隔離 (server_id)、事件紀錄、讀取、摘要產生、快取與夜間壓縮清理。
"""

from __future__ import annotations

from pathlib import Path

import handlers.memory.world_memory as wm


def _setup_temp_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test_world_memory.db"
    wm._WORLD_DB_PATH = db_path
    wm._DB_INITIALIZED = False
    wm.clear_world_memory_cache()
    wm._init_db()
    return db_path


def test_world_memory_server_isolation(tmp_path: Path) -> None:
    """測試世界事件的 server_id 寫入與過濾隔離。"""
    _setup_temp_db(tmp_path)

    # 記錄不同伺服器的事件
    e1 = wm.log_world_event("CHAT", "伺服器A發生了大事", location="主城", importance=4, server_id="guild_111")
    e2 = wm.log_world_event("CHAT", "伺服器B也發生了事件", location="森林", importance=3, server_id="guild_222")
    e3 = wm.log_world_event("DND", "全域事件", location="遺跡", importance=5, server_id="")

    assert e1 is not None
    assert e2 is not None
    assert e3 is not None

    today = wm._today_str()

    # 依 server_id 查詢
    events_g1 = wm.read_world_events(today, server_id="guild_111")
    assert len(events_g1) == 1
    assert events_g1[0]["summary"] == "伺服器A發生了大事"
    assert events_g1[0]["server_id"] == "guild_111"

    events_g2 = wm.read_world_events(today, server_id="guild_222")
    assert len(events_g2) == 1
    assert events_g2[0]["summary"] == "伺服器B也發生了事件"

    # 不限伺服器查詢
    all_events = wm.read_world_events(today, server_id="")
    assert len(all_events) == 3


def test_world_memory_snippet_requires_server_id(tmp_path: Path) -> None:
    """測試世界記憶摘要在缺乏 server_id 時回傳空字串，防止跨伺服器污染。"""
    _setup_temp_db(tmp_path)

    wm.log_world_event("CHAT", "伺服器A的大事", location="城鎮", importance=4, server_id="guild_111")

    # 無 server_id -> 回傳空字串
    snippet_none = wm.get_world_memory_snippet(days=1, min_importance=1, server_id="")
    assert snippet_none == ""

    # 指定錯誤的 server_id -> 回傳空字串
    snippet_other = wm.get_world_memory_snippet(days=1, min_importance=1, server_id="guild_999")
    assert snippet_other == ""

    # 指定正確的 server_id -> 取得摘要
    snippet_valid = wm.get_world_memory_snippet(days=1, min_importance=1, server_id="guild_111")
    assert "🌍【世界近期大事】" in snippet_valid
    assert "伺服器A的大事" in snippet_valid


def test_world_memory_snippet_cache_and_clear(tmp_path: Path) -> None:
    """測試 get_world_memory_snippet 的記憶體快取與 clear_world_memory_cache。"""
    _setup_temp_db(tmp_path)

    wm.log_world_event("CHAT", "事件一", location="A", importance=4, server_id="guild_1")

    s1 = wm.get_world_memory_snippet(days=1, min_importance=1, server_id="guild_1")
    assert "事件一" in s1

    # 新增事件二（未清快取前應命中快取）
    wm.log_world_event("CHAT", "事件二", location="B", importance=4, server_id="guild_1")
    s2 = wm.get_world_memory_snippet(days=1, min_importance=1, server_id="guild_1")
    assert "事件二" not in s2  # 命中快取

    # 清除快取後重新取得
    wm.clear_world_memory_cache()
    s3 = wm.get_world_memory_snippet(days=1, min_importance=1, server_id="guild_1")
    assert "事件二" in s3


def test_compress_daily_events_and_cleanup(tmp_path: Path) -> None:
    """測試每日事件壓縮與清理舊紀錄。"""
    _setup_temp_db(tmp_path)

    today = wm._today_str()
    wm.log_world_event("CHAT", "伺服器A事件", location="A", importance=4, server_id="guild_1")
    wm.log_world_event("CHAT", "伺服器B事件", location="B", importance=4, server_id="guild_2")

    # 針對 guild_1 壓縮
    added_g1 = wm.compress_daily_events(today, server_id="guild_1")
    assert added_g1 == 1

    # 重複壓縮應去重
    added_g1_repeat = wm.compress_daily_events(today, server_id="guild_1")
    assert added_g1_repeat == 0

    # 針對 guild_2 壓縮
    added_g2 = wm.compress_daily_events(today, server_id="guild_2")
    assert added_g2 == 1

    # 清理舊紀錄（0 筆過期）
    removed = wm.cleanup_old_logs(days=30)
    assert removed == 0


def test_chat_logger_server_id_and_world_event_archiving(tmp_path: Path) -> None:
    """測試 chat_logger 儲存 server_id 以及 world_memory log_world_event 帶 date_str 歸檔。"""
    import handlers.memory.chat_logger as cl
    from unittest.mock import MagicMock

    cl.DB_PATH = tmp_path / "test_chat_history.db"
    cl._CHAT_DB_INITIALIZED = False
    cl._init_chat_db()

    # 模擬來自特定 Guild 的訊息
    msg = MagicMock()
    msg.clean_content = "大家今天一起玩楓之谷！"
    msg.author.id = 123456
    msg.author.display_name = "Yuuto"
    msg.guild.id = 825626269747118091

    cl.append_chat_log(msg)
    cl.flush_chat_logs()

    today = cl._today_str()
    records = cl.read_chat_log(today, server_id="825626269747118091")
    assert len(records) == 1
    assert records[0]["server_id"] == "825626269747118091"
    assert "楓之谷" in records[0]["content"]

    # 測試其他伺服器查不到
    other_records = cl.read_chat_log(today, server_id="999999999")
    assert len(other_records) == 0

    # 測試 log_world_event 指定 date_str
    _setup_temp_db(tmp_path)
    ev_id = wm.log_world_event(
        "CHAT",
        "群友們熱烈討論楓之谷",
        importance=4,
        server_id="825626269747118091",
        date_str="2026-09-02",
    )
    assert ev_id is not None
    added = wm.compress_daily_events("2026-09-02", server_id="825626269747118091")
    assert added == 1

    snippet = wm.get_world_memory_snippet(days=7, server_id="825626269747118091")
    assert "熱烈討論楓之谷" in snippet
