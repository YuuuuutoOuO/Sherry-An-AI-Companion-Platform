"""
tests/unit/test_soft_delete_r7.py
=================================
R7 軟刪擴散與保留治理 —— 先紅後綠回歸測試（報告 §5.2.3–§5.2.7、§5.5.1、
§5.5.2；核銷 §7.1.2 B2/B4/B5/B6、P0-4）。

涵蓋：
1. world_memory：查詢全面過濾 is_deleted、軟刪除後不可檢索、重啟不復活、
   寫入／清理快取失效同步。
2. schedule_storage：審計欄位遷移、軟刪除（remove_task／到期待辦清理）、
   讀取過濾。
3. DataRetentionWorker：chat_messages 保留政策、admin_audit 成效、路徑修正、
   物理清除軟刪列、JSONL 原子輪轉、預設路徑絕對化。
4. user_memory JSON 備援僅限首次遷移（已刪記憶不得自 JSON 復活）。
5. roast JSON 回灌不覆寫刪除標記（僅補缺失）。
6. WebUI DB 日誌檢視過濾軟刪資料。
7. tools/db_manager：schedule_tasks 納入標準表、備份加密／輪替／還原。
8. tools/migrate_json_to_sqlite：無 INSERT OR REPLACE／無破壞性 rmtree。
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import time

import pytest

_TZ_TW = timezone(timedelta(hours=8))

_CHAT_MESSAGES_DDL = """
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    date_str TEXT NOT NULL,
    user_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    content TEXT NOT NULL,
    server_id TEXT NOT NULL DEFAULT '',
    processed INTEGER DEFAULT 0,
    is_deleted INTEGER NOT NULL DEFAULT 0,
    deleted_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
)
"""


def _now_tw() -> datetime:
    return datetime.now(_TZ_TW)


def _make_chat_db(path: Path, rows: list[tuple[str, str, int, str | None]]) -> None:
    """建立最小 chat_history.db（chat_messages 表）。

    rows: (date_str, content, is_deleted, deleted_at)。
    """
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(_CHAT_MESSAGES_DDL)
        for date_str, content, is_deleted, deleted_at in rows:
            conn.execute(
                "INSERT INTO chat_messages "
                "(timestamp, date_str, user_id, display_name, content, server_id, is_deleted, deleted_at) "
                "VALUES (?, ?, 'u1', 'Yuuto', ?, '', ?, ?)",
                (f"{date_str} 00:00:00", date_str, content, is_deleted, deleted_at),
            )
        conn.commit()
    finally:
        conn.close()


def _setup_world_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """將 world_memory 模組重導至臨時資料庫並回傳 (模組, DB 路徑)。"""
    import handlers.memory.world_memory as wm

    db_path = tmp_path / "world_memory.db"
    monkeypatch.setattr(wm, "_WORLD_DB_PATH", db_path)
    monkeypatch.setattr(wm, "_WORLD_LOG_DIR", tmp_path / "world_history")
    monkeypatch.setattr(wm, "_WORLD_MEMORY_PATH", tmp_path / "world_memory.json")
    monkeypatch.setattr(wm, "_DB_INITIALIZED", False)
    wm.clear_world_memory_cache()
    wm._init_db()
    return wm, db_path


# ---------------------------------------------------------------------------
# 1. world_memory
# ---------------------------------------------------------------------------


def test_world_event_soft_delete_not_retrievable_and_no_resurrect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """軟刪除的世界事件：立即不可檢索、模擬重啟後不復活、保留刪除軌跡。"""
    wm, db_path = _setup_world_db(tmp_path, monkeypatch)

    eid = wm.log_world_event("CHAT", "秘密事件", location="A", importance=5, server_id="guild_1")
    assert eid is not None
    assert "秘密事件" in wm.get_world_memory_snippet(days=1, min_importance=1, server_id="guild_1")

    assert wm.soft_delete_event(eid) is True
    # 冪等：重複刪除不再變更
    assert wm.soft_delete_event(eid) is False

    # 查詢面全面歸零（事件清單、近期事件、LLM 摘要）
    assert wm.read_world_events(wm._today_str(), server_id="guild_1") == []
    assert all(e["event_id"] != eid for e in wm.read_recent_events(days=1, server_id="guild_1"))
    assert wm.get_world_memory_snippet(days=1, min_importance=1, server_id="guild_1") == ""

    # 模擬重啟（模組狀態重置）後仍不可檢索、不得自 JSON 復活
    monkeypatch.setattr(wm, "_DB_INITIALIZED", False)
    wm.clear_world_memory_cache()
    assert wm.read_recent_events(days=1, server_id="guild_1") == []
    assert wm.get_world_memory_snippet(days=1, min_importance=1, server_id="guild_1") == ""

    # 軟刪軌跡保留於 DB
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT is_deleted, deleted_at FROM world_events WHERE event_id = ?", (eid,)
    ).fetchone()
    conn.close()
    assert row is not None and row[0] == 1 and row[1]


def test_world_cleanup_old_logs_soft_deletes_and_clears_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """保留期清理改為軟刪除：查得到軌跡、快取同步失效、內容不再注入。"""
    wm, db_path = _setup_world_db(tmp_path, monkeypatch)

    old_dt = _now_tw() - timedelta(days=40)
    wm._insert_event(
        event_id="old_event_1",
        ts=old_dt.isoformat(),
        date_str=old_dt.strftime("%Y-%m-%d"),
        event_type="CHAT",
        location="",
        summary="古老事件",
        participants=[],
        importance=4,
        server_id="guild_1",
    )
    assert wm.log_world_event("CHAT", "新事件", importance=4, server_id="guild_1") is not None

    s1 = wm.get_world_memory_snippet(days=60, min_importance=1, server_id="guild_1")
    assert "古老事件" in s1

    removed = wm.cleanup_old_logs(days=30)
    assert removed == 1

    # 快取寫入同步：無需手動 clear 即可看到清理後結果
    s2 = wm.get_world_memory_snippet(days=60, min_importance=1, server_id="guild_1")
    assert "古老事件" not in s2
    assert "新事件" in s2

    # 軌跡保留（非硬刪）
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT is_deleted, deleted_at FROM world_events WHERE event_id = 'old_event_1'"
    ).fetchone()
    conn.close()
    assert row is not None and row[0] == 1 and row[1]


def test_world_summary_retention_soft_deletes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """壓縮摘要的 90 天保留改為軟刪除，且不再注入 LLM 摘要。"""
    wm, db_path = _setup_world_db(tmp_path, monkeypatch)

    old_dt = _now_tw() - timedelta(days=100)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO world_summary (event_id, server_id, ts, event_type, location, summary, importance) "
        "VALUES ('old_sum_1', 'guild_1', ?, 'CHAT', '', '古老摘要', 4)",
        (old_dt.isoformat(),),
    )
    conn.commit()
    conn.close()

    assert wm.log_world_event("CHAT", "今日事件", importance=4, server_id="guild_1") is not None
    added = wm.compress_daily_events(wm._today_str(), server_id="guild_1")
    assert added == 1

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT is_deleted, deleted_at FROM world_summary WHERE event_id = 'old_sum_1'"
    ).fetchone()
    conn.close()
    assert row is not None and row[0] == 1 and row[1]

    # 120 天查詢窗仍涵蓋該列日期，故僅 is_deleted 過濾能排除它
    snippet = wm.get_world_memory_snippet(days=120, min_importance=1, server_id="guild_1")
    assert "古老摘要" not in snippet
    assert "今日事件" in snippet


# ---------------------------------------------------------------------------
# 2. schedule_storage
# ---------------------------------------------------------------------------


def test_schedule_soft_delete_and_read_filters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """排程任務刪除改為軟刪除：讀取面過濾、軌跡保留、到期待辦軟刪。"""
    from handlers import schedule_storage as ss

    db_path = tmp_path / "schedules.db"
    monkeypatch.setattr(ss, "DB_PATH", db_path)
    ss._init_schedule_db()

    async def _flow_remove():
        task = await ss.add_task(
            user_id="u1", channel_id="c1", schedule_type="once", content="喝水",
            trigger_time=1_700_000_000,
        )
        ok = await ss.remove_task(task["task_id"])
        return task, ok, await ss.load_all_tasks(), await ss.load_tasks_by_user("u1")

    task, ok, all_tasks, by_user = asyncio.run(_flow_remove())
    assert ok is True
    assert all_tasks == []
    assert by_user == []

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT is_deleted, deleted_at FROM schedule_tasks WHERE task_id = ?",
        (task["task_id"],),
    ).fetchone()
    conn.close()
    assert row is not None and row[0] == 1 and row[1]

    async def _flow_purge():
        t_due = await ss.add_task(
            user_id="u2", channel_id="c1", schedule_type="once", content="到期任務",
            trigger_time=100,
        )
        t_future = await ss.add_task(
            user_id="u2", channel_id="c1", schedule_type="once", content="未到期任務",
            trigger_time=2_000_000_000,
        )
        purged = await ss.purge_once_tasks_before(200)
        return t_due, t_future, purged, await ss.load_all_tasks()

    t_due, t_future, purged, remaining = asyncio.run(_flow_purge())
    assert [p["task_id"] for p in purged] == [t_due["task_id"]]
    assert [r["task_id"] for r in remaining] == [t_future["task_id"]]

    # 到期任務為軟刪而非硬刪
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT is_deleted FROM schedule_tasks WHERE task_id = ?", (t_due["task_id"],)
    ).fetchone()
    conn.close()
    assert row is not None and row[0] == 1


def test_schedule_schema_migrates_audit_columns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """舊版 schedule_tasks（無審計欄位）於初始化時冪等補齊軟刪四柱。"""
    from handlers import schedule_storage as ss

    db_path = tmp_path / "schedules.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE schedule_tasks (
            task_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            schedule_type TEXT NOT NULL,
            trigger_time INTEGER,
            cron_expr TEXT,
            content TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        )
    """)
    conn.commit()
    conn.close()

    monkeypatch.setattr(ss, "DB_PATH", db_path)
    ss._init_schedule_db()

    conn = sqlite3.connect(str(db_path))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(schedule_tasks)")}
    conn.close()
    assert {"is_deleted", "deleted_at", "created_at", "updated_at"} <= cols


# ---------------------------------------------------------------------------
# 3. DataRetentionWorker
# ---------------------------------------------------------------------------


def test_retention_default_base_dir_is_settings_data_dir() -> None:
    """預設資料根目錄須為絕對路徑（settings.DATA_DIR），不再使用相對 'data'。"""
    import settings

    from handlers.memory.retention_worker import DataRetentionWorker

    worker = DataRetentionWorker()
    assert worker.base_data_dir == settings.DATA_DIR
    assert worker.base_data_dir.is_absolute()


def test_retention_chat_messages_soft_delete_policy(tmp_path: Path) -> None:
    """對話訊息保留政策（預設 180 天）：過期訊息軟刪、新鮮訊息保留。"""
    from handlers.memory.retention_worker import DataRetentionWorker, RetentionPolicy

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    old_date = (_now_tw() - timedelta(days=200)).strftime("%Y-%m-%d")
    today = _now_tw().strftime("%Y-%m-%d")
    _make_chat_db(
        data_dir / "chat_history.db",
        [(old_date, "過期對話", 0, None), (today, "新鮮對話", 0, None)],
    )

    worker = DataRetentionWorker(
        base_data_dir=data_dir,
        policy=RetentionPolicy(chat_messages_max_days=180),
    )
    removed = worker.soft_delete_expired_chat_messages()
    assert removed == 1

    conn = sqlite3.connect(str(data_dir / "chat_history.db"))
    rows = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT content, is_deleted, deleted_at FROM chat_messages"
    )}
    conn.close()
    assert rows["過期對話"][0] == 1 and rows["過期對話"][1]
    assert rows["新鮮對話"][0] == 0


def test_retention_cycle_admin_audit_db_purge_and_atomic(
    tmp_path: Path,
) -> None:
    """完整保留週期：admin_audit 生效＋暫存路徑修正＋軟刪列物理清除＋原子重寫。"""
    from handlers.memory.retention_worker import DataRetentionWorker, RetentionPolicy

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    old_dt = _now_tw() - timedelta(days=200)
    now_dt = _now_tw()
    old_date = old_dt.strftime("%Y-%m-%d")
    today = now_dt.strftime("%Y-%m-%d")
    deleted_at = (_now_tw() - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")

    _make_chat_db(
        data_dir / "chat_history.db",
        [
            (old_date, "仍現役的過期對話", 0, None),
            (old_date, "已軟刪的過期對話", 1, deleted_at),
            (today, "新鮮對話", 0, None),
        ],
    )

    audit_path = data_dir / "admin_audit.jsonl"
    audit_path.write_text(
        json.dumps({"timestamp": old_dt.strftime("%Y-%m-%d %H:%M:%S"), "action": "OLD"}, ensure_ascii=False) + "\n"
        + json.dumps({"timestamp": now_dt.strftime("%Y-%m-%d %H:%M:%S"), "action": "NEW"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # 正確的暫存路徑為 chat_logs/temp（修正舊有拼寫 chat_log/temp）
    chat_temp = data_dir / "chat_logs" / "temp"
    chat_temp.mkdir(parents=True)
    stale = chat_temp / "old.tmp"
    stale.write_text("x", encoding="utf-8")
    past = time.time() - (48 * 3600)
    os.utime(stale, (past, past))

    worker = DataRetentionWorker(
        base_data_dir=data_dir,
        policy=RetentionPolicy(
            chat_messages_max_days=180,
            soft_delete_grace_days=30,
            admin_audit_max_days=90,
        ),
    )
    report = worker.run_retention_cycle()

    assert report.errors == []
    assert report.cleaned_files_count == 1
    assert report.soft_deleted_db_rows == 1
    assert report.purged_db_rows == 1

    text = audit_path.read_text(encoding="utf-8")
    assert "OLD" not in text and "NEW" in text
    # 原子輪轉：不得殘留暫存檔
    assert not list(data_dir.glob("*.tmp"))
    assert not list(data_dir.glob("admin_audit.jsonl.*"))

    conn = sqlite3.connect(str(data_dir / "chat_history.db"))
    remaining = {r[0]: r[1] for r in conn.execute(
        "SELECT content, is_deleted FROM chat_messages"
    )}
    conn.close()
    assert remaining == {"仍現役的過期對話": 1, "新鮮對話": 0}


# ---------------------------------------------------------------------------
# 4. user_memory JSON 備援僅限首次遷移
# ---------------------------------------------------------------------------


def test_user_memory_json_fallback_only_on_first_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """JSON 備援僅在 SQLite 完全空白（首次遷移）時可用；已刪記憶不得復活。"""
    import handlers.memory.storage as mem

    db_path = tmp_path / "sherry_memory.db"
    monkeypatch.setattr(mem, "MEMORY_DB_PATH", db_path)
    monkeypatch.setattr(mem, "_INITIALIZED_MEMORY_DBS", set())

    json_path = tmp_path / "user_memory.json"
    json_path.write_text(
        json.dumps(
            {
                "u111": {
                    "aliases": ["小乖"],
                    "facts": [
                        {"fact": "喜歡紅茶", "category": "preference", "importance": 4, "updated_at": ""}
                    ],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # 1) 首次遷移：SQLite 完全空白 → 允許 JSON 備援
    first = mem.load_user_memory(json_path)
    assert "u111" in first

    # 2) 寫入 SQLite 後軟刪除 → JSON 檔仍在，但不得復活
    mem.save_single_user_memory("u111", first["u111"], first, json_path)
    mem.soft_delete_all("u111")
    reloaded = mem.load_user_memory(json_path)
    assert "u111" not in reloaded

    # 3) 模擬重啟（模組狀態重置）後仍不得自 JSON 復活
    monkeypatch.setattr(mem, "_INITIALIZED_MEMORY_DBS", set())
    assert "u111" not in mem.load_user_memory(json_path)


# ---------------------------------------------------------------------------
# 5. roast JSON 回灌不覆寫刪除標記
# ---------------------------------------------------------------------------


def test_roast_json_backfill_does_not_resurrect_deleted(tmp_path: Path) -> None:
    """roast_rules.json 回灌僅補缺失：已軟刪規則不得被復活或改寫。"""
    from handlers.roast.manager import RoastManager

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "chat_history.db"

    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE roast_rules (
            id TEXT PRIMARY KEY,
            keyword TEXT NOT NULL,
            reply_guide TEXT NOT NULL,
            probability INTEGER NOT NULL DEFAULT 100,
            match_mode TEXT NOT NULL DEFAULT 'contains',
            enabled INTEGER NOT NULL DEFAULT 1,
            is_deleted INTEGER NOT NULL DEFAULT 0,
            deleted_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
    """)
    conn.execute(
        "INSERT INTO roast_rules (id, keyword, reply_guide, is_deleted, deleted_at) "
        "VALUES ('r_deleted', '舊關鍵字', '舊回覆', 1, '2026-09-01 00:00:00')"
    )
    conn.commit()
    conn.close()

    json_path = data_dir / "roast_rules.json"
    json_path.write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "id": "r_deleted",
                        "keyword": "新關鍵字",
                        "reply_guide": "新回覆",
                        "probability": 100,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # 觸發 JSON → SQLite 回灌
    RoastManager(file_path=json_path)

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT keyword, is_deleted FROM roast_rules WHERE id = 'r_deleted'"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[1] == 1, "JSON 回灌不得覆寫刪除標記（不得復活已刪規則）"
    assert row[0] == "舊關鍵字", "回灌僅補缺失，不得覆寫既有資料"


# ---------------------------------------------------------------------------
# 6. WebUI DB 日誌檢視過濾軟刪
# ---------------------------------------------------------------------------


def test_db_logs_viewer_filters_soft_deleted(tmp_path: Path) -> None:
    """日誌檢視器查詢含 is_deleted 的表時須過濾軟刪資料；無該欄位時相容。"""
    from web_ui.server import _query_sqlite_logs_sync

    db_path = tmp_path / "chat_history.db"
    _make_chat_db(
        db_path,
        [("2026-10-01", "可見訊息", 0, None), ("2026-10-02", "已刪訊息", 1, "2026-10-02 00:00:00")],
    )

    result = _query_sqlite_logs_sync(db_path, "chat_messages", 1, 50)
    assert result["total"] == 1
    assert [e.get("content") for e in result["entries"]] == ["可見訊息"]

    # 相容：無 is_deleted 欄位的表照常查詢
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE other_events (id INTEGER PRIMARY KEY AUTOINCREMENT, msg TEXT)")
    conn.execute("INSERT INTO other_events (msg) VALUES ('a'), ('b')")
    conn.commit()
    conn.close()
    result2 = _query_sqlite_logs_sync(db_path, "other_events", 1, 50)
    assert result2["total"] == 2


# ---------------------------------------------------------------------------
# 7. tools/db_manager
# ---------------------------------------------------------------------------


def test_canonical_tables_include_schedule_tasks_and_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """schedule_tasks 納入標準表（四柱齊備＋清運涵蓋），UTC ISO 刪除時間亦可清運。"""
    from tools import db_manager

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    backups = data_dir / "backups"
    backups.mkdir()
    monkeypatch.setattr(db_manager, "DATA_DIR", data_dir)
    monkeypatch.setattr(db_manager, "BACKUP_DIR", backups)

    table_keys = {(t.db_relative_path, t.table_name) for t in db_manager.CANONICAL_TABLES}
    assert ("schedules.db", "schedule_tasks") in table_keys

    db_manager.run_migration()

    conn = sqlite3.connect(str(data_dir / "schedules.db"))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(schedule_tasks)")}
    assert {"is_deleted", "deleted_at", "created_at", "updated_at"} <= cols

    deleted_at = (_now_tw() - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
    utc_iso_deleted_at = (_now_tw() - timedelta(days=40)).astimezone(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO schedule_tasks (task_id, user_id, display_name, channel_id, schedule_type, content, is_deleted, deleted_at) "
        "VALUES ('t_old', 'u1', 'n', 'c1', 'once', 'x', 1, ?)",
        (deleted_at,),
    )
    conn.execute(
        "INSERT INTO schedule_tasks (task_id, user_id, display_name, channel_id, schedule_type, content, is_deleted, deleted_at) "
        "VALUES ('t_old_utc', 'u1', 'n', 'c1', 'once', 'x', 1, ?)",
        (utc_iso_deleted_at,),
    )
    conn.execute(
        "INSERT INTO schedule_tasks (task_id, user_id, display_name, channel_id, schedule_type, content) "
        "VALUES ('t_alive', 'u1', 'n', 'c1', 'once', 'y')"
    )
    conn.commit()
    conn.close()

    purged = db_manager.cleanup_soft_deleted(days=30)
    assert purged == 2

    conn = sqlite3.connect(str(data_dir / "schedules.db"))
    remaining = [r[0] for r in conn.execute("SELECT task_id FROM schedule_tasks")]
    conn.close()
    assert remaining == ["t_alive"]


def test_backup_encrypts_rotates_and_restores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """備份產物加密（不得留明文 DB）、保留 N 份輪替、可還原。"""
    from tools import db_manager

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    backups = data_dir / "backups"
    backups.mkdir()
    monkeypatch.setattr(db_manager, "DATA_DIR", data_dir)
    monkeypatch.setattr(db_manager, "BACKUP_DIR", backups)
    monkeypatch.delenv("SHERRY_BACKUP_KEY", raising=False)

    src_db = data_dir / "chat_history.db"
    conn = sqlite3.connect(str(src_db))
    conn.execute("CREATE TABLE t (x TEXT)")
    conn.execute("INSERT INTO t VALUES ('hello')")
    conn.commit()
    conn.close()

    # 模擬舊備份（輪替對象）
    for i in range(1, 4):
        old_dir = backups / f"manual_db_backup_20250101_00000{i}"
        old_dir.mkdir()
        (old_dir / "old.txt").write_text("old", encoding="utf-8")

    real_dir = db_manager.backup_all_databases(keep=2)

    assert list(real_dir.glob("*.enc")), "備份 DB 需加密產出 .enc"
    assert not list(real_dir.rglob("*.db")), "備份目錄不得殘留明文 DB"

    remaining = sorted(p.name for p in backups.glob("manual_db_backup_*"))
    assert len(remaining) == 2, f"備份輪替應只保留 2 組，實際: {remaining}"
    assert remaining[-1] == real_dir.name

    target = tmp_path / "restore_target"
    target.mkdir()
    restored = db_manager.restore_backup(real_dir, target_dir=target)
    assert restored >= 1

    conn = sqlite3.connect(str(target / "chat_history.db"))
    value = conn.execute("SELECT x FROM t").fetchone()[0]
    conn.close()
    assert value == "hello"


# ---------------------------------------------------------------------------
# 8. tools/migrate_json_to_sqlite 靜態守門
# ---------------------------------------------------------------------------


def test_migrate_tool_no_insert_or_replace_and_no_rmtree() -> None:
    """遷移工具不得使用 INSERT OR REPLACE（復活軟刪列）或破壞性 rmtree。"""
    src_path = Path(__file__).resolve().parents[2] / "tools" / "migrate_json_to_sqlite.py"
    src = src_path.read_text(encoding="utf-8")
    assert "INSERT OR REPLACE" not in src
    assert "shutil.rmtree" not in src
    assert "ON CONFLICT" in src
