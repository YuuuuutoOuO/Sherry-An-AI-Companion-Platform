"""
handlers/schedule_storage.py
=============================
排程 JSON 檔案讀寫模組 — 使用 asyncio.Lock 保護寫入競爭。

所有 schedule.json 的存取均必須透過此模組，確保資料完整性。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import settings

logger = logging.getLogger(__name__)

_lock = asyncio.Lock()


def _default_schema() -> dict[str, Any]:
    return {"tasks": []}


async def _read_raw() -> dict[str, Any]:
    """讀取 schedule.json 原始字典（不鎖定，供內部使用）。"""
    path: Path = settings.SCHEDULE_PATH
    if not path.exists():
        return _default_schema()
    try:
        content = await asyncio.to_thread(path.read_text, encoding="utf-8")
        data = json.loads(content) if content.strip() else _default_schema()
        if not isinstance(data, dict) or "tasks" not in data:
            return _default_schema()
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("schedule_storage: 無法讀取 schedule.json: %s", exc)
        return _default_schema()


async def _write_raw(data: dict[str, Any]) -> None:
    """寫入 schedule.json（不鎖定，供內部使用）。"""
    path: Path = settings.SCHEDULE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        content = json.dumps(data, ensure_ascii=False, indent=2)
        await asyncio.to_thread(tmp.write_text, content, encoding="utf-8")
        await asyncio.to_thread(tmp.replace, path)
    except OSError as exc:
        logger.error("schedule_storage: 寫入 schedule.json 失敗: %s", exc)


async def load_all_tasks() -> list[dict[str, Any]]:
    """取得所有排程任務（執行緒安全讀取）。"""
    async with _lock:
        data = await _read_raw()
        return data.get("tasks", [])


async def load_tasks_by_user(user_id: str) -> list[dict[str, Any]]:
    """取得指定使用者的所有排程任務（執行緒安全讀取）。"""
    async with _lock:
        data = await _read_raw()
        return [t for t in data.get("tasks", []) if t.get("user_id") == user_id]


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
    """
    新增一個排程任務。

    Parameters
    ----------
    user_id : str
        使用者 Discord ID。
    channel_id : str
        要發送提醒的頻道 ID。
    schedule_type : str
        "once" 或 "cron"。
    content : str
        提醒事項內容。
    display_name : str
        使用者顯示名稱（建立時記錄，觸發時直接使用，避免快取失效）。
    trigger_time : int | None
        Unix timestamp，單次提醒用，僅 schedule_type == "once" 時有效。
    cron_expr : str | None
        Cron 表達式，週期提醒用，僅 schedule_type == "cron" 時有效。

    Returns
    -------
    dict
        新增完成的任務資料。
    """
    task_entry: dict[str, Any] = {
        "task_id": uuid.uuid4().hex,
        "user_id": user_id,
        "display_name": display_name,
        "channel_id": channel_id,
        "schedule_type": schedule_type,
        "trigger_time": trigger_time,
        "cron_expr": cron_expr,
        "content": content,
        "is_active": True,
    }

    async with _lock:
        data = await _read_raw()
        data.setdefault("tasks", []).append(task_entry)
        await _write_raw(data)

    logger.info(
        "schedule_storage: added %s task=%s trigger=%s",
        schedule_type, task_entry["task_id"], trigger_time or cron_expr,
    )
    return task_entry


async def remove_task(task_id: str) -> bool:
    """
    移除指定排程任務。

    Returns
    -------
    bool
        True 表示成功刪除，False 表示找不到該 task_id。
    """
    async with _lock:
        data = await _read_raw()
        tasks = data.get("tasks", [])
        before = len(tasks)
        data["tasks"] = [t for t in tasks if t.get("task_id") != task_id]
        after = len(data["tasks"])
        if before == after:
            return False
        await _write_raw(data)
    logger.info("schedule_storage: removed task=%s", task_id)
    return True


async def deactivate_task(task_id: str) -> bool:
    """將任務設為非活躍（is_active=False），用於暫停而非刪除。"""
    async with _lock:
        data = await _read_raw()
        for t in data.get("tasks", []):
            if t.get("task_id") == task_id:
                t["is_active"] = False
                await _write_raw(data)
                logger.info("schedule_storage: deactivated task=%s", task_id)
                return True
    return False


async def purge_once_tasks_before(ts: int) -> list[dict[str, Any]]:
    """
    移除所有 trigger_time <= ts 的單次任務。
    回傳被移除的任務清單以便觸發提醒。
    """
    triggered: list[dict[str, Any]] = []
    async with _lock:
        data = await _read_raw()
        remaining: list[dict[str, Any]] = []
        for t in data.get("tasks", []):
            if t.get("schedule_type") == "once" and t.get("trigger_time", 0) <= ts:
                triggered.append(t)
            else:
                remaining.append(t)
        if triggered:
            data["tasks"] = remaining
            await _write_raw(data)
            logger.info("schedule_storage: purged %d once tasks (ts<=%s)", len(triggered), ts)
    return triggered