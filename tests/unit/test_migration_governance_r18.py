"""R18 遷移治理（user_version、乾跑、排程收斂）回歸測試。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from core.migrations import Migration, MigrationRunner
from migrations import MIGRATIONS
from tools import migrate_json_to_sqlite, unify_schedules


def _schedule_tasks_ddl() -> str:
    return """
        CREATE TABLE IF NOT EXISTS schedule_tasks (
            task_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            schedule_type TEXT NOT NULL,
            trigger_time INTEGER,
            cron_expr TEXT,
            content TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            is_deleted INTEGER NOT NULL DEFAULT 0,
            deleted_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
    """


def test_runtime_migration_applies_once_and_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "runtime_state.db"
    runner = MigrationRunner(db, MIGRATIONS, db_filename="runtime_state.db")

    first = runner.run()
    assert [(item.version, item.applied) for item in first] == [(1, True)]
    assert runner.current_version() == 1

    second = runner.run()
    assert second == []
    assert runner.current_version() == 1


def test_dry_run_never_creates_or_writes_db(tmp_path: Path) -> None:
    db = tmp_path / "runtime_state.db"
    runner = MigrationRunner(db, MIGRATIONS, db_filename="runtime_state.db")

    results = runner.run(dry_run=True)

    assert [(item.version, item.dry_run, item.applied) for item in results] == [(1, True, False)]
    assert not db.exists()
    assert runner.current_version() == 0


def test_backup_snapshot_and_restore_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "runtime_state.db"
    runner = MigrationRunner(db, MIGRATIONS, db_filename="runtime_state.db")
    runner.run()
    conn = sqlite3.connect(db)
    try:
        conn.execute("INSERT INTO state_kv(scope, key, value, updated_at) VALUES ('s', 'k', 'v1', 1.0)")
        conn.commit()
    finally:
        conn.close()

    backup = runner.backup()
    assert backup is not None and backup.exists()
    assert runner._integrity_ok(backup)

    conn = sqlite3.connect(db)
    try:
        conn.execute("UPDATE state_kv SET value = 'v2' WHERE scope='s' AND key='k'")
        conn.commit()
    finally:
        conn.close()
    safety = runner.restore(backup)
    assert safety.exists()

    conn = sqlite3.connect(db)
    try:
        value = conn.execute("SELECT value FROM state_kv WHERE scope='s' AND key='k'").fetchone()[0]
    finally:
        conn.close()
    assert value == "v1"


def test_dry_run_on_existing_db_does_not_create_wal_sidecar(tmp_path: Path) -> None:
    db = tmp_path / "runtime_state.db"
    sqlite3.connect(db).close()
    runner = MigrationRunner(db, MIGRATIONS, db_filename="runtime_state.db")

    results = runner.run(dry_run=True)

    assert [item.version for item in results] == [1]
    assert runner.current_version() == 0
    assert not Path(str(db) + "-wal").exists()
    assert not Path(str(db) + "-shm").exists()


def test_failed_migration_rolls_back_schema_and_version(tmp_path: Path) -> None:
    db = tmp_path / "runtime_state.db"

    def _boom(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE should_not_survive (id INTEGER);")
        raise RuntimeError("boom")

    runner = MigrationRunner(
        db,
        [Migration(version=9, name="boom", apply=_boom)],
        db_filename="runtime_state.db",
    )
    results = runner.run()

    assert len(results) == 1
    assert results[0].applied is False
    assert "boom" in results[0].error
    assert runner.current_version() == 0
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='should_not_survive'"
        ).fetchone()
    finally:
        conn.close()
    assert row is None


def test_schedule_ledger_backfill_and_sync_triggers(tmp_path: Path) -> None:
    db = tmp_path / "schedules.db"
    with sqlite3.connect(db) as conn:
        conn.execute(_schedule_tasks_ddl())
        conn.execute(
            """
            INSERT INTO schedule_tasks(task_id, user_id, display_name, channel_id,
                                       schedule_type, trigger_time, content, is_active)
            VALUES ('t1', 'u1', '喝水', 'c1', 'once', 1700000000, '喝水', 1)
            """
        )

    runner = MigrationRunner(db, MIGRATIONS, db_filename="schedules.db")
    results = runner.run()
    assert [item.version for item in results] == [2, 3]
    assert runner.current_version() == 3

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM schedules WHERE source='schedule_tasks' AND source_id='t1'").fetchone()
        assert row is not None
        assert row["content"] == "喝水"
        assert row["is_active"] == 1

        conn.execute(
            """
            INSERT INTO schedule_tasks(task_id, user_id, display_name, channel_id,
                                       schedule_type, trigger_time, content, is_active)
            VALUES ('t2', 'u1', '倒垃圾', 'c1', 'once', 1700000100, '倒垃圾', 1)
            """
        )
        assert conn.execute("SELECT COUNT(*) FROM schedules WHERE source_id='t2'").fetchone()[0] == 1

        conn.execute("UPDATE schedule_tasks SET is_deleted = 1, is_active = 0 WHERE task_id='t1'")
        assert conn.execute("SELECT is_active FROM schedules WHERE source_id='t1'").fetchone()[0] == 0

        conn.execute("DELETE FROM schedule_tasks WHERE task_id='t2'")
        assert conn.execute("SELECT COUNT(*) FROM schedules WHERE source_id='t2'").fetchone()[0] == 0
    finally:
        conn.close()


def test_unify_schedules_dry_run_then_apply_is_idempotent(tmp_path: Path) -> None:
    spark_db = tmp_path / "spark.db"
    schedules_db = tmp_path / "schedules.db"
    trigger = {
        "user_id": 42,
        "channel_id": 99,
        "trigger_type": "schedule",
        "is_active": True,
        "condition": {"mode": "once", "target_time": "2026-09-03T22:30:00", "tz_offset": 8},
        "message": "提醒我開會",
        "action": {"action_type": "notify"},
    }
    with sqlite3.connect(spark_db) as conn:
        conn.execute(
            """
            CREATE TABLE triggers (
                trigger_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL DEFAULT 0,
                trigger_type TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                is_active INTEGER NOT NULL DEFAULT 1,
                data_json TEXT NOT NULL,
                is_deleted INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            "INSERT INTO triggers(trigger_id, user_id, channel_id, trigger_type, is_active, data_json) "
            "VALUES ('trg1', 42, 99, 'schedule', 1, ?)",
            (json.dumps(trigger, ensure_ascii=False),),
        )

    dry = unify_schedules.unify(spark_db, schedules_db, dry_run=True)
    assert dry["planned"] == 1
    assert dry["by_source"]["spark"] == 1
    assert not schedules_db.exists()

    applied = unify_schedules.unify(spark_db, schedules_db, dry_run=False)
    assert applied["written"] == 1
    conn = sqlite3.connect(schedules_db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM schedules WHERE schedule_id='spark:trg1'").fetchone()
        assert row is not None
        assert row["schedule_type"] == "once"
        assert row["trigger_time"] is not None
        assert row["content"] == "提醒我開會"

        conn.execute(
            """
            INSERT INTO schedule_tasks(task_id, user_id, display_name, channel_id,
                                       schedule_type, content, is_active)
            VALUES ('t3', 'u1', '巡檢', 'c1', 'interval', '巡檢', 1)
            """
        )
        conn.commit()
    finally:
        conn.close()

    second = unify_schedules.unify(spark_db, schedules_db, dry_run=False)
    assert second["planned"] == 2
    assert second["written"] == 2
    conn = sqlite3.connect(schedules_db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM schedules").fetchone()[0] == 2
    finally:
        conn.close()


def test_migrate_json_tool_dry_run_has_no_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(migrate_json_to_sqlite, "BASE_DIR", tmp_path)
    monkeypatch.setattr(migrate_json_to_sqlite, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "runtime_log.jsonl").write_text("{}\n", encoding="utf-8")

    migrate_json_to_sqlite.run_migration(dry_run=True)

    output = capsys.readouterr().out
    assert "乾跑" in output
    assert "chat_history.db" in output
    assert not (tmp_path / "data" / "backups").exists()
    assert not (tmp_path / "data" / "chat_history.db").exists()


def test_migrate_tool_has_no_replace_or_rmtree() -> None:
    source = Path(migrate_json_to_sqlite.__file__).read_text(encoding="utf-8")
    assert "INSERT OR REPLACE" not in source
    assert ".rmtree(" not in source
    assert "--dry-run" in source
