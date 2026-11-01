"""
tests/unit/test_db_migration.py
===============================
針對 core.db_migration 模組之單元測試，驗證 SQLite 版本取得、遷移、備份與回滾機制。
"""

from __future__ import annotations

from pathlib import Path
import sqlite3

from core.db_migration import (
    Migration,
    MigrationRunner,
    create_db_backup,
    get_schema_version,
    set_schema_version,
)


def test_schema_version_get_and_set(tmp_path: Path) -> None:
    # Arrange
    db_file = tmp_path / "test_version.db"

    # Act & Assert (Initial)
    assert get_schema_version(db_file) == 0

    # Act (Set version)
    with sqlite3.connect(str(db_file)) as conn:
        set_schema_version(conn, 3)

    # Assert
    assert get_schema_version(db_file) == 3


def test_db_migration_up_and_rollback(tmp_path: Path) -> None:
    # Arrange
    db_file = tmp_path / "test_app.db"
    runner = MigrationRunner(db_file, backup_on_migrate=True)

    def v1_up(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);")

    def v1_down(conn: sqlite3.Connection) -> None:
        conn.execute("DROP TABLE users;")

    def v2_up(conn: sqlite3.Connection) -> None:
        conn.execute("ALTER TABLE users ADD COLUMN email TEXT;")

    def v2_down(conn: sqlite3.Connection) -> None:
        # SQLite doesn't drop column natively in older versions, recreate table
        conn.execute("CREATE TABLE users_temp (id INTEGER PRIMARY KEY, name TEXT);")
        conn.execute("INSERT INTO users_temp (id, name) SELECT id, name FROM users;")
        conn.execute("DROP TABLE users;")
        conn.execute("ALTER TABLE users_temp RENAME TO users;")

    runner.register(Migration(version=1, name="create_users_table", up=v1_up, down=v1_down))
    runner.register(Migration(version=2, name="add_email_column", up=v2_up, down=v2_down))

    # Act - Migrate to latest
    latest_v = runner.migrate()

    # Assert - Migration
    assert latest_v == 2
    assert get_schema_version(db_file) == 2

    # Verify table structure
    with sqlite3.connect(str(db_file)) as conn:
        conn.execute("INSERT INTO users (id, name, email) VALUES (1, 'Alice', 'alice@example.com');")
        cur = conn.cursor()
        cur.execute("SELECT name, email FROM users WHERE id = 1;")
        row = cur.fetchone()
        assert row[0] == "Alice"
        assert row[1] == "alice@example.com"

    # Act - Rollback to v1
    rollback_v = runner.rollback(target_version=1)

    # Assert - Rollback
    assert rollback_v == 1
    assert get_schema_version(db_file) == 1


def test_db_backup_creation(tmp_path: Path) -> None:
    # Arrange
    db_file = tmp_path / "test_data.db"
    with sqlite3.connect(str(db_file)) as conn:
        conn.execute("CREATE TABLE records (val TEXT);")
        conn.execute("INSERT INTO records VALUES ('important_data');")

    backup_dir = tmp_path / "custom_backups"

    # Act
    backup_path = create_db_backup(db_file, backup_dir=backup_dir)

    # Assert
    assert backup_path.exists()
    assert backup_path.parent == backup_dir

    with sqlite3.connect(str(backup_path)) as b_conn:
        cur = b_conn.cursor()
        cur.execute("SELECT val FROM records;")
        assert cur.fetchone()[0] == "important_data"
