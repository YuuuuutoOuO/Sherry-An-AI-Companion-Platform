"""三軌排程收斂工具（R18）。

將 SPARK ``triggers`` 與 ``schedule_tasks`` 的排程資料投影到
``data/schedules.db`` 的 canonical ``schedules`` 讀模型：

- 預設為乾跑（``--dry-run``），只輸出計畫、不建立 DB、不寫入；
- ``--apply`` 才會確保 schema（套用 ``migrations/``）並以
  ``INSERT ... ON CONFLICT DO UPDATE`` 冪等 upsert；
- 不刪除任何來源資料，重跑安全。

用法：
    python -m tools.unify_schedules --dry-run
    python -m tools.unify_schedules --apply
    python -m tools.unify_schedules --spark-db X --schedules-db Y --apply
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.migrations import MigrationRunner
from migrations import MIGRATIONS

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SPARK_DB = BASE_DIR / "data" / "spark" / "spark.db"
DEFAULT_SCHEDULES_DB = BASE_DIR / "data" / "schedules.db"

_UPSERT_SQL = """
    INSERT INTO schedules(
        schedule_id, source, source_id, user_id, channel_id, schedule_type,
        trigger_time, cron_expr, content, is_active, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
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


def _parse_epoch(value: Any, tz_offset: int) -> int | None:
    """將 epoch 或 ISO 時間字串轉為 epoch 秒；無法解析時回傳 None。"""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    try:
        return int(float(text))
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone(timedelta(hours=tz_offset)))
    return int(parsed.timestamp())


def _trigger_schedule_fields(condition: dict[str, Any]) -> tuple[str, int | None, str | None]:
    """把 SPARK condition 映射為 (schedule_type, trigger_time, cron_expr)。"""
    mode = str(condition.get("mode") or "").strip().lower()
    try:
        tz_offset = int(condition.get("tz_offset", 8))
    except (TypeError, ValueError):
        tz_offset = 8

    if mode == "interval":
        try:
            minutes = int(condition.get("interval_minutes") or 0)
        except (TypeError, ValueError):
            minutes = 0
        cron = f"*/{minutes} * * * *" if minutes > 0 else None
        return "interval", None, cron

    if mode == "daily_time":
        raw = str(condition.get("time") or "")
        cron = None
        try:
            hour_text, minute_text = raw.split(":", 1)
            cron = f"{int(minute_text)} {int(hour_text)} * * *"
        except (ValueError, TypeError):
            pass
        return "daily_time", None, cron

    if mode == "once":
        epoch = _parse_epoch(
            condition.get("target_time")
            or condition.get("trigger_time")
            or condition.get("time"),
            tz_offset,
        )
        return "once", epoch, None

    if mode:
        return mode, _parse_epoch(condition.get("trigger_time"), tz_offset), None

    return "schedule", None, None


def _trigger_row(trigger_id: str, data: dict[str, Any]) -> dict[str, Any]:
    condition = data.get("condition") if isinstance(data.get("condition"), dict) else {}
    action = data.get("action") if isinstance(data.get("action"), dict) else {}
    payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
    schedule_type, trigger_time, cron_expr = _trigger_schedule_fields(condition)
    content = str(data.get("message") or payload.get("query") or data.get("name") or "")
    return {
        "schedule_id": f"spark:{trigger_id}",
        "source": "spark",
        "source_id": trigger_id,
        "user_id": str(data.get("user_id") or ""),
        "channel_id": str(data.get("channel_id") or ""),
        "schedule_type": schedule_type,
        "trigger_time": trigger_time,
        "cron_expr": cron_expr,
        "content": content,
        "is_active": 0 if data.get("is_active") is False else 1,
    }


def _read_spark_rows(spark_db: Path) -> list[dict[str, Any]]:
    if not spark_db.exists():
        return []
    conn = sqlite3.connect(f"file:{spark_db.as_posix()}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT trigger_id, user_id, channel_id, is_active, data_json
                FROM triggers
                WHERE trigger_type = 'schedule' AND is_deleted = 0
                """
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    finally:
        conn.close()

    result: list[dict[str, Any]] = []
    for row in rows:
        try:
            data = json.loads(row["data_json"])
        except (TypeError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("user_id", row["user_id"])
        data.setdefault("channel_id", row["channel_id"])
        data.setdefault("is_active", bool(row["is_active"]))
        result.append(_trigger_row(str(row["trigger_id"]), data))
    return result


def _read_schedule_task_rows(schedules_db: Path) -> list[dict[str, Any]]:
    if not schedules_db.exists():
        return []
    conn = sqlite3.connect(f"file:{schedules_db.as_posix()}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT task_id, user_id, channel_id, schedule_type, trigger_time,
                       cron_expr, content, is_active, is_deleted
                FROM schedule_tasks
                """
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    finally:
        conn.close()

    return [
        {
            "schedule_id": f"schedule_tasks:{row['task_id']}",
            "source": "schedule_tasks",
            "source_id": str(row["task_id"]),
            "user_id": str(row["user_id"]),
            "channel_id": str(row["channel_id"]),
            "schedule_type": str(row["schedule_type"]),
            "trigger_time": int(row["trigger_time"]) if row["trigger_time"] is not None else None,
            "cron_expr": str(row["cron_expr"]) if row["cron_expr"] is not None else None,
            "content": str(row["content"]),
            "is_active": 0 if row["is_deleted"] else int(row["is_active"]),
        }
        for row in rows
    ]


def collect_rows(spark_db: Path, schedules_db: Path) -> list[dict[str, Any]]:
    """收集兩軌來源並以 (source, source_id) 去重（後者覆蓋前者）。"""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for row in _read_spark_rows(spark_db) + _read_schedule_task_rows(schedules_db):
        merged[(row["source"], row["source_id"])] = row
    return list(merged.values())


def _ensure_schema(schedules_db: Path) -> list[int]:
    runner = MigrationRunner(schedules_db, MIGRATIONS, db_filename="schedules.db")
    results = runner.run()
    failed = [result for result in results if result.error]
    if failed:
        detail = "; ".join(f"v{result.version}: {result.error}" for result in failed)
        raise RuntimeError(f"schedules schema 遷移失敗：{detail}")
    return [result.version for result in results if result.applied]


def apply_rows(schedules_db: Path, rows: list[dict[str, Any]]) -> int:
    """冪等寫入 canonical schedules；回傳寫入筆數。"""
    applied_versions = _ensure_schema(schedules_db)
    conn = sqlite3.connect(str(schedules_db), timeout=10.0)
    try:
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("BEGIN IMMEDIATE")
        for row in rows:
            conn.execute(
                _UPSERT_SQL,
                (
                    row["schedule_id"],
                    row["source"],
                    row["source_id"],
                    row["user_id"],
                    row["channel_id"],
                    row["schedule_type"],
                    row["trigger_time"],
                    row["cron_expr"],
                    row["content"],
                    row["is_active"],
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    if applied_versions:
        print(f"已套用 schedules schema 版本：{applied_versions}")
    return len(rows)


def unify(
    spark_db: Path | str = DEFAULT_SPARK_DB,
    schedules_db: Path | str = DEFAULT_SCHEDULES_DB,
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    """執行三軌收斂；``dry_run=True`` 時保證不建立/寫入任何 DB。"""
    spark_path = Path(spark_db)
    schedules_path = Path(schedules_db)
    rows = collect_rows(spark_path, schedules_path)
    summary: dict[str, Any] = {
        "dry_run": bool(dry_run),
        "spark_db": str(spark_path),
        "schedules_db": str(schedules_path),
        "planned": len(rows),
        "by_source": {
            source: sum(1 for row in rows if row["source"] == source)
            for source in ("spark", "schedule_tasks")
        },
    }
    if dry_run:
        summary["sample"] = rows[:20]
        return summary
    summary["written"] = apply_rows(schedules_path, rows)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="將 SPARK／schedule_tasks 排程收斂至 canonical schedules")
    parser.add_argument("--spark-db", default=str(DEFAULT_SPARK_DB), help="SPARK SQLite 路徑")
    parser.add_argument("--schedules-db", default=str(DEFAULT_SCHEDULES_DB), help="schedules SQLite 路徑")
    parser.add_argument("--dry-run", action="store_true", help="只輸出計畫（預設）")
    parser.add_argument("--apply", action="store_true", help="實際寫入 canonical schedules")
    args = parser.parse_args(argv)

    dry_run = not args.apply
    summary = unify(args.spark_db, args.schedules_db, dry_run=dry_run)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
