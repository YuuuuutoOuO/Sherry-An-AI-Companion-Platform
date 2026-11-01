"""v2：排程收斂的 canonical 讀模型（schedules）。"""

from __future__ import annotations

import sqlite3

from core.migrations import Migration


def _apply(conn: sqlite3.Connection) -> None:
    # 讓遷移可獨立套用於全新 DB；欄位與 handlers/schedule_storage.py 保持一致。
    conn.execute(
        """
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
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schedules (
            schedule_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            source_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            channel_id TEXT NOT NULL DEFAULT '',
            schedule_type TEXT NOT NULL,
            trigger_time INTEGER,
            cron_expr TEXT,
            content TEXT NOT NULL DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            UNIQUE(source, source_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_schedules_user ON schedules(user_id);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_schedules_active ON schedules(is_active);")
    # 既有 schedule_tasks 回填（不覆寫已軟刪資料）
    conn.execute(
        """
        INSERT INTO schedules(
            schedule_id, source, source_id, user_id, channel_id, schedule_type,
            trigger_time, cron_expr, content, is_active, updated_at
        )
        SELECT
            'schedule_tasks:' || task_id, 'schedule_tasks', task_id, user_id, channel_id,
            schedule_type, trigger_time, cron_expr, content,
            CASE WHEN is_deleted = 1 THEN 0 ELSE is_active END,
            COALESCE(updated_at, datetime('now', 'localtime'))
        FROM schedule_tasks
        WHERE 1
        ON CONFLICT(schedule_id) DO UPDATE SET
            user_id = excluded.user_id,
            channel_id = excluded.channel_id,
            schedule_type = excluded.schedule_type,
            trigger_time = excluded.trigger_time,
            cron_expr = excluded.cron_expr,
            content = excluded.content,
            is_active = excluded.is_active,
            updated_at = excluded.updated_at
        """
    )


MIGRATION = Migration(
    version=2,
    name="schedule_ledger",
    apply=_apply,
    applies_to=("schedules.db",),
)
