"""
handlers/schedule_storage.py
=============================
排程任務 SQLite 資料庫讀寫模組。
所有 schedule 任務均持久化儲存於 data/schedules.db。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any

import settings
from core.db import db_transaction, get_db_connection

logger = logging.getLogger(__name__)

DB_PATH: Path = settings.DATA_DIR / "schedules.db"
_lock = asyncio.Lock()


def _init_schedule_db() -> None:
    """初始化 schedules.db 資料表與索引。"""
    try:
        with db_transaction(DB_PATH) as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS schedule_tasks (
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
            cur.execute("CREATE INDEX IF NOT EXISTS idx_sched_user ON schedule_tasks(user_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_sched_trigger ON schedule_tasks(trigger_time);")
    except Exception as e:
        logger.error("[ScheduleStorage] 初始化 schedules.db 失敗: %s", e)


_init_schedule_db()


def _row_to_dict(r: Any) -> dict[str, Any]:
    return {
        "task_id": str(r["task_id"]),
        "user_id": str(r["user_id"]),
        "display_name": str(r["display_name"]),
        "channel_id": str(r["channel_id"]),
        "schedule_type": str(r["schedule_type"]),
        "trigger_time": int(r["trigger_time"]) if r["trigger_time"] is not None else None,
        "cron_expr": str(r["cron_expr"]) if r["cron_expr"] is not None else None,
        "content": str(r["content"]),
        "is_active": bool(r["is_active"]),
    }


async def load_all_tasks() -> list[dict[str, Any]]:
    """取得所有排程任務（WAL 模式多連線並發讀取）。"""
    def _fetch() -> list[dict[str, Any]]:
        try:
            conn = get_db_connection(DB_PATH)
            try:
                cur = conn.cursor()
                cur.execute("SELECT * FROM schedule_tasks")
                rows = cur.fetchall()
                return [_row_to_dict(r) for r in rows]
            finally:
                conn.close()
        except Exception as e:
            logger.error("[ScheduleStorage] 讀取任務清單失敗: %s", e)
            return []

    return await asyncio.to_thread(_fetch)


async def load_tasks_by_user(user_id: str) -> list[dict[str, Any]]:
    """取得指定使用者的所有排程任務（WAL 模式多連線並發讀取）。"""
    def _fetch() -> list[dict[str, Any]]:
        try:
            conn = get_db_connection(DB_PATH)
            try:
                cur = conn.cursor()
                cur.execute("SELECT * FROM schedule_tasks WHERE user_id = ?", (str(user_id),))
                rows = cur.fetchall()
                return [_row_to_dict(r) for r in rows]
            finally:
                conn.close()
        except Exception as e:
            logger.error("[ScheduleStorage] 依使用者讀取任務失敗: %s", e)
            return []

    return await asyncio.to_thread(_fetch)


async def add_task(
    *,
    user_id: str,
    channel_id: str,
    schedule_type: str,
    content: str,
    display_name: str = "",
    trigger_time: int | None = None,
    cron_expr: str | None = None,
) -> dict[str, Any]:
    """新增一個排程任務。"""
    task_entry: dict[str, Any] = {
        "task_id": uuid.uuid4().hex,
        "user_id": str(user_id),
        "display_name": display_name,
        "channel_id": str(channel_id),
        "schedule_type": schedule_type,
        "trigger_time": trigger_time,
        "cron_expr": cron_expr,
        "content": content,
        "is_active": True,
    }

    async with _lock:
        def _insert() -> None:
            with db_transaction(DB_PATH) as cur:
                cur.execute("""
                    INSERT INTO schedule_tasks (
                        task_id, user_id, display_name, channel_id, schedule_type, trigger_time, cron_expr, content, is_active
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                """, (
                    task_entry["task_id"],
                    task_entry["user_id"],
                    task_entry["display_name"],
                    task_entry["channel_id"],
                    task_entry["schedule_type"],
                    task_entry["trigger_time"],
                    task_entry["cron_expr"],
                    task_entry["content"],
                ))

        await asyncio.to_thread(_insert)

    logger.info(
        "schedule_storage: added %s task=%s trigger=%s",
        schedule_type, task_entry["task_id"], trigger_time or cron_expr,
    )
    return task_entry


async def remove_task(task_id: str) -> bool:
    """移除指定排程任務。"""
    async with _lock:
        def _delete() -> bool:
            try:
                with db_transaction(DB_PATH) as cur:
                    cur.execute("DELETE FROM schedule_tasks WHERE task_id = ?", (str(task_id),))
                    return cur.rowcount > 0
            except Exception as e:
                logger.error("[ScheduleStorage] 刪除任務失敗: %s", e)
                return False

        success = await asyncio.to_thread(_delete)
        if success:
            logger.info("schedule_storage: removed task=%s", task_id)
        return success


async def purge_once_tasks_before(ts: int) -> list[dict[str, Any]]:
    """移除所有 trigger_time <= ts 的單次任務並回傳（單一原子交易）。"""
    async with _lock:
        def _purge() -> list[dict[str, Any]]:
            triggered: list[dict[str, Any]] = []
            try:
                with db_transaction(DB_PATH) as cur:
                    cur.execute("""
                        SELECT * FROM schedule_tasks
                        WHERE schedule_type = 'once' AND trigger_time <= ?
                    """, (ts,))
                    rows = cur.fetchall()
                    triggered = [_row_to_dict(r) for r in rows]

                    if triggered:
                        cur.execute("""
                            DELETE FROM schedule_tasks
                            WHERE schedule_type = 'once' AND trigger_time <= ?
                        """, (ts,))
                        logger.info("schedule_storage: purged %d once tasks (ts<=%s)", len(triggered), ts)
            except Exception as e:
                logger.error("[ScheduleStorage] 清理到期單次任務失敗: %s", e)
            return triggered

        return await asyncio.to_thread(_purge)