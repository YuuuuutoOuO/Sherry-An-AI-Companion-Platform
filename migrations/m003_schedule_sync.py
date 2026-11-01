"""v3：schedule_tasks → canonical schedules 的同步 triggers。"""

from __future__ import annotations

import sqlite3

from core.migrations import Migration

_UPSERT_BODY = """
    INSERT INTO schedules(
        schedule_id, source, source_id, user_id, channel_id, schedule_type,
        trigger_time, cron_expr, content, is_active, updated_at
    ) VALUES (
        'schedule_tasks:' || NEW.task_id, 'schedule_tasks', NEW.task_id,
        NEW.user_id, NEW.channel_id, NEW.schedule_type, NEW.trigger_time,
        NEW.cron_expr, NEW.content,
        CASE WHEN NEW.is_deleted = 1 THEN 0 ELSE NEW.is_active END,
        COALESCE(NEW.updated_at, datetime('now', 'localtime'))
    )
    ON CONFLICT(schedule_id) DO UPDATE SET
        user_id = excluded.user_id,
        channel_id = excluded.channel_id,
        schedule_type = excluded.schedule_type,
        trigger_time = excluded.trigger_time,
        cron_expr = excluded.cron_expr,
        content = excluded.content,
        is_active = excluded.is_active,
        updated_at = excluded.updated_at;
"""


def _apply(conn: sqlite3.Connection) -> None:
    # 逐條 execute（而非 executescript）：executescript 會先隱式 commit，
    # 使 migration 失敗時無法整體 rollback。這裡保持單一交易語意。
    conn.execute(
        f"""
        CREATE TRIGGER IF NOT EXISTS trg_schedule_tasks_ai
        AFTER INSERT ON schedule_tasks
        BEGIN
            {_UPSERT_BODY}
        END;
        """
    )
    conn.execute(
        f"""
        CREATE TRIGGER IF NOT EXISTS trg_schedule_tasks_au
        AFTER UPDATE ON schedule_tasks
        BEGIN
            {_UPSERT_BODY}
        END;
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_schedule_tasks_ad
        AFTER DELETE ON schedule_tasks
        BEGIN
            DELETE FROM schedules
            WHERE source = 'schedule_tasks' AND source_id = OLD.task_id;
        END;
        """
    )


MIGRATION = Migration(
    version=3,
    name="schedule_sync_triggers",
    apply=_apply,
    applies_to=("schedules.db",),
)
