"""
handlers/memory/world_memory.py
===============================
世界層級記憶系統（World Memory）— SQLite 持久化版本。

原以 JSONL append-only 記錄世界事件、以 world_memory.json 儲存壓縮摘要，
現改以 SQLite（data/world_memory.db）為主要儲存層：
  - world_events    ：每日原始世界事件
  - world_summary   ：每日壓縮後的世界重要事件摘要

若偵測到舊的 world_history/*.jsonl 或 world_memory.json 存在，
會於首次初始化時一次性種入 DB（唯讀舊檔，不刪除）。

用途：
- D&D 世界歷史記錄
- 跨群組/跨頻道的全域事件追蹤
- 供 persona.py 注入「世界近期大事」到 LLM prompt
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import settings
from core.db import get_db_connection, db_transaction

logger = logging.getLogger(__name__)

_TZ_TW = timezone(timedelta(hours=8))
_WORLD_LOG_DIR: Path = settings.WORLD_LOG_DIR
_WORLD_MEMORY_PATH: Path = settings.WORLD_MEMORY_PATH
_WORLD_DB_PATH: Path = settings.DATA_DIR / "world_memory.db"

_DB_INITIALIZED: bool = False


def _today_str() -> str:
    return datetime.now(_TZ_TW).strftime("%Y-%m-%d")


def _init_db() -> None:
    """初始化 world_memory.db 資料表（僅首次執行）。"""
    global _DB_INITIALIZED
    if _DB_INITIALIZED:
        return
    try:
        _WORLD_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with db_transaction(_WORLD_DB_PATH) as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS world_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT UNIQUE NOT NULL,
                    server_id TEXT NOT NULL DEFAULT '',
                    ts TEXT NOT NULL,
                    date_str TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    location TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    participants_json TEXT NOT NULL DEFAULT '[]',
                    importance INTEGER NOT NULL DEFAULT 3,
                    is_deleted INTEGER NOT NULL DEFAULT 0,
                    deleted_at TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_world_events_date ON world_events(date_str);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_world_events_ts ON world_events(ts);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_world_events_server ON world_events(server_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_world_events_lookup ON world_events(server_id, date_str, ts);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_world_events_deleted ON world_events(is_deleted);")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS world_summary (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT UNIQUE NOT NULL,
                    server_id TEXT NOT NULL DEFAULT '',
                    ts TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    location TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    importance INTEGER NOT NULL DEFAULT 3,
                    participants_json TEXT NOT NULL DEFAULT '[]',
                    is_deleted INTEGER NOT NULL DEFAULT 0,
                    deleted_at TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_world_summary_ts ON world_summary(ts);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_world_summary_server ON world_summary(server_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_world_summary_lookup ON world_summary(server_id, ts, importance);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_world_summary_deleted ON world_summary(is_deleted);")
        _DB_INITIALIZED = True
    except sqlite3.Error as exc:
        logger.error("world_memory: 初始化 SQLite 資料庫失敗: %s", exc)


def _seed_legacy_json() -> None:
    """一次性：若 DB 空白且有舊檔 (world_history/*.jsonl)，唯讀種入 DB。"""
    if not _WORLD_LOG_DIR.exists():
        return
    try:
        conn = get_db_connection(_WORLD_DB_PATH)
        cnt = conn.execute("SELECT COUNT(*) FROM world_events").fetchone()[0]
        conn.close()
        if cnt > 0:
            return
    except sqlite3.Error:
        return

    seeded = 0
    for f in sorted(_WORLD_LOG_DIR.glob("*.jsonl")):
        date_str = f.stem
        try:
            with f.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(rec, dict):
                        continue
                    event_id = str(rec.get("event_id", uuid.uuid4().hex[:8]))
                    _insert_event(
                        event_id=event_id,
                        ts=str(rec.get("ts", "")),
                        date_str=date_str,
                        event_type=str(rec.get("event_type", "")),
                        location=str(rec.get("location", "")),
                        summary=str(rec.get("summary", "")),
                        participants=list(rec.get("participants", []) or []),
                        importance=int(rec.get("importance", 3)),
                    )
                    seeded += 1
        except OSError as exc:
            logger.warning("world_memory: 種入舊檔 %s 失敗: %s", f, exc)
    if seeded:
        logger.info("world_memory: 已自舊 JSONL 種入 %d 筆世界事件", seeded)


def _insert_event(
    event_id: str,
    ts: str,
    date_str: str,
    event_type: str,
    location: str,
    summary: str,
    participants: list[str],
    importance: int,
    server_id: str = "",
) -> None:
    try:
        with db_transaction(_WORLD_DB_PATH) as cur:
            cur.execute("""
                INSERT INTO world_events
                    (event_id, server_id, ts, date_str, event_type, location, summary, participants_json, importance)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event_id,
                server_id,
                ts,
                date_str,
                event_type,
                location,
                summary,
                json.dumps(participants, ensure_ascii=False),
                importance,
            ))
    except sqlite3.Error as exc:
        logger.warning("world_memory: 寫入事件失敗: %s", exc)


# ---------------------------------------------------------------------------
# 寫入事件
# ---------------------------------------------------------------------------

def log_world_event(
    event_type: str,
    summary: str,
    location: str = "",
    participants: list[str] | None = None,
    importance: int = 3,
    server_id: str = "",
    date_str: str = "",
) -> str | None:
    """記錄一筆世界事件到 SQLite。

    Args:
        event_type: 事件類型（如 DND, CHAT, DISCOVERY 等）。
        summary: 事件內容摘要。
        location: 發生地點（選填）。
        participants: 參與成員名稱列表（選填）。
        importance: 重要度評級 1(瑣碎)~5(改變世界格局)。
        server_id: 伺服器唯一識別碼（TEXT snowflake），用於跨伺服器記憶隔離。
        date_str: 事件所屬日期 YYYY-MM-DD，若為空則使用今日。

    Returns:
        str | None: 成功則回傳 event_id，失敗則回傳 None。
    """
    try:
        _init_db()
        event_id = str(uuid.uuid4())[:8]
        _insert_event(
            event_id=event_id,
            ts=datetime.now(_TZ_TW).isoformat(),
            date_str=date_str or _today_str(),
            event_type=event_type,
            location=location,
            summary=summary,
            participants=participants or [],
            importance=max(1, min(5, importance)),
            server_id=server_id,
        )
        return event_id
    except Exception as exc:
        logger.warning("world_memory: 寫入事件失敗: %s", exc)
        return None


# ---------------------------------------------------------------------------
# 讀取事件
# ---------------------------------------------------------------------------

def _row_to_event(row: sqlite3.Row) -> dict[str, Any]:
    try:
        participants = json.loads(row["participants_json"])
    except Exception:
        participants = []
    return {
        "event_id": str(row["event_id"]),
        "server_id": str(row["server_id"]) if "server_id" in row.keys() else "",
        "ts": str(row["ts"]),
        "event_type": str(row["event_type"]),
        "location": str(row["location"]),
        "summary": str(row["summary"]),
        "participants": participants if isinstance(participants, list) else [],
        "importance": int(row["importance"]),
    }


def read_world_events(date_str: str, server_id: str = "") -> list[dict[str, Any]]:
    """讀取指定日期的所有世界事件，可依 server_id 過濾。

    Args:
        date_str: 日期字串（格式 YYYY-MM-DD）。
        server_id: 伺服器唯一識別碼（空字串代表不限伺服器）。

    Returns:
        list[dict[str, Any]]: 世界事件字典列表。
    """
    try:
        conn = get_db_connection(_WORLD_DB_PATH)
        if server_id:
            rows = conn.execute(
                "SELECT * FROM world_events WHERE date_str = ? AND server_id = ? ORDER BY ts ASC",
                (date_str, server_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM world_events WHERE date_str = ? ORDER BY ts ASC",
                (date_str,),
            ).fetchall()
        conn.close()
        return [_row_to_event(r) for r in rows]
    except sqlite3.Error as exc:
        logger.warning("world_memory: 讀取 %s 失敗: %s", date_str, exc)
        return []


def read_recent_events(days: int = 1, server_id: str = "") -> list[dict[str, Any]]:
    """讀取最近 N 天的所有世界事件，可依 server_id 過濾。

    Args:
        days: 往前回溯天數（預設 1 天）。
        server_id: 伺服器唯一識別碼（空字串代表不限伺服器）。

    Returns:
        list[dict[str, Any]]: 最近 N 天的世界事件列表。
    """
    if days <= 0:
        return []
    now_dt = datetime.now(_TZ_TW)
    today_date = now_dt.strftime("%Y-%m-%d")
    cutoff_date = (now_dt - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    try:
        conn = get_db_connection(_WORLD_DB_PATH)
        if server_id:
            rows = conn.execute(
                "SELECT * FROM world_events WHERE server_id = ? AND date_str >= ? AND date_str <= ? ORDER BY ts ASC",
                (server_id, cutoff_date, today_date),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM world_events WHERE date_str >= ? AND date_str <= ? ORDER BY ts ASC",
                (cutoff_date, today_date),
            ).fetchall()
        conn.close()
        return [_row_to_event(r) for r in rows]
    except sqlite3.Error as exc:
        logger.warning("world_memory: 讀取最近 %d 天事件失敗: %s", days, exc)
        return []


# ---------------------------------------------------------------------------
# 摘要（供 LLM prompt 注入）
# ---------------------------------------------------------------------------

_WORLD_MEMORY_CACHE_TTL: float = 60.0
_WORLD_MEMORY_SNIPPET_CACHE: dict[tuple[str, int, int, int], tuple[float, str]] = {}


def clear_world_memory_cache() -> None:
    """清除世界記憶的記憶體快取。"""
    _WORLD_MEMORY_SNIPPET_CACHE.clear()


def get_world_memory_snippet(
    days: int = 1,
    min_importance: int = 3,
    server_id: str = "",
    limit: int | None = None,
) -> str:
    """取得可注入 LLM prompt 的世界近期事件摘要文字（具備 60 秒 TTL 記憶體快取）。

    優先讀取壓縮摘要（world_summary），若無則從原始事件即時產生。

    Args:
        days: 往前回溯天數。
        min_importance: 最低重要度門檻（1~5）。
        server_id: 當前伺服器識別碼（缺乏 server_id 時回傳空字串，落實隔離）。
        limit: 注入的摘要條數上限（預設取 settings.WORLD_MEMORY_SNIPPET_LIMIT）。

    Returns:
        str: 格式化後的世界事件摘要文字。
    """
    if limit is None:
        limit = getattr(settings, "WORLD_MEMORY_SNIPPET_LIMIT", 6)
    # 缺乏 server_id 時不注入世界記憶，避免跨伺服器記憶互相污染
    if not server_id:
        return ""
    now = time.time()
    cache_key = (server_id, days, min_importance, limit)
    if cache_key in _WORLD_MEMORY_SNIPPET_CACHE:
        cached_ts, cached_snippet = _WORLD_MEMORY_SNIPPET_CACHE[cache_key]
        if now - cached_ts < _WORLD_MEMORY_CACHE_TTL:
            return cached_snippet

    events = _load_compressed_memory(days, min_importance, server_id=server_id)
    if not events:
        events = [
            e for e in read_recent_events(days, server_id=server_id)
            if e.get("importance", 3) >= min_importance
        ]

    if not events:
        _WORLD_MEMORY_SNIPPET_CACHE[cache_key] = (now, "")
        return ""

    lines = ["🌍【世界近期大事】"]
    for e in sorted(events, key=lambda x: x.get("ts", ""), reverse=True)[:limit]:
        ts = e.get("ts", "")[:16].replace("T", " ")
        loc = f" [{e['location']}]" if e.get("location") else ""
        lines.append(f"- {ts}{loc} {e['summary']}")

    result = "\n".join(lines)
    _WORLD_MEMORY_SNIPPET_CACHE[cache_key] = (now, result)
    return result


def _load_compressed_memory(days: int, min_importance: int, server_id: str = "") -> list[dict[str, Any]]:
    """讀取壓縮後的世界記憶（world_summary 表格），可依 server_id 過濾。"""
    try:
        now_dt = datetime.now(_TZ_TW)
        cutoff_dt = now_dt - timedelta(days=days)
        cutoff = cutoff_dt.strftime("%Y-%m-%d %H:%M:%S")
        now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        conn = get_db_connection(_WORLD_DB_PATH)
        # 取 ts 前 19 字元並將 'T' 替換為空格，使 "YYYY-MM-DD HH:MM:SS" 格式完全一致，
        # 避免 ISO 8601 中的 'T' 與空格混合造成的 ASCII 排序偏差與時區偏移字串誤判。
        if server_id:
            rows = conn.execute(
                """
                SELECT * FROM world_summary
                WHERE server_id = ?
                  AND REPLACE(SUBSTR(ts, 1, 19), 'T', ' ') >= ?
                  AND REPLACE(SUBSTR(ts, 1, 19), 'T', ' ') <= ?
                  AND importance >= ?
                ORDER BY ts DESC
                """,
                (server_id, cutoff, now_str, min_importance),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM world_summary
                WHERE REPLACE(SUBSTR(ts, 1, 19), 'T', ' ') >= ?
                  AND REPLACE(SUBSTR(ts, 1, 19), 'T', ' ') <= ?
                  AND importance >= ?
                ORDER BY ts DESC
                """,
                (cutoff, now_str, min_importance),
            ).fetchall()
        conn.close()
        return [_row_to_event(r) for r in rows]
    except sqlite3.Error as exc:
        logger.warning("world_memory: 讀取壓縮記憶失敗: %s", exc)
        return []


# ---------------------------------------------------------------------------
# 每日壓縮 / 清理
# ---------------------------------------------------------------------------

def compress_daily_events(date_str: str, server_id: str = "") -> int:
    """將指定日期的原始事件壓縮為摘要並寫入 world_summary。

    Args:
        date_str: 日期字串（格式 YYYY-MM-DD）。
        server_id: 伺服器唯一識別碼（非空時僅壓縮該伺服器的事件，並標記為該伺服器）。

    Returns:
        int: 壓縮後新增寫入的事件數量。
    """
    records = read_world_events(date_str, server_id=server_id)
    if not records:
        return 0

    conn = None
    try:
        conn = get_db_connection(_WORLD_DB_PATH)
        seen = {r["event_id"] for r in conn.execute("SELECT event_id FROM world_summary").fetchall()}
    except sqlite3.Error:
        seen = set()
    finally:
        if conn is not None:
            conn.close()

    added = 0
    for rec in records:
        eid = str(rec.get("event_id", ""))
        if not eid or eid in seen:
            continue
        seen.add(eid)
        try:
            with db_transaction(_WORLD_DB_PATH) as cur:
                cur.execute("""
                    INSERT INTO world_summary
                        (event_id, server_id, ts, event_type, location, summary, importance, participants_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    eid,
                    str(rec.get("server_id", server_id)),
                    str(rec.get("ts", "")),
                    str(rec.get("event_type", "")),
                    str(rec.get("location", "")),
                    str(rec.get("summary", "")),
                    int(rec.get("importance", 3)),
                    json.dumps(rec.get("participants", []), ensure_ascii=False),
                ))
            added += 1
        except sqlite3.Error:
            pass

    # 保留最近 90 天的摘要
    cutoff = datetime.now(_TZ_TW) - timedelta(days=90)
    cutoff_str = cutoff.isoformat()
    try:
        with db_transaction(_WORLD_DB_PATH) as cur:
            cur.execute("DELETE FROM world_summary WHERE ts < ?", (cutoff_str,))
    except sqlite3.Error as exc:
        logger.warning("world_memory: 清理舊摘要失敗: %s", exc)

    if added:
        clear_world_memory_cache()
    return added


def cleanup_old_logs(days: int = 30) -> int:
    """清理超過指定天數的原始世界事件。

    Args:
        days: 欲保留的天數（預設 30 天，超過此天數的原始記錄將被刪除）。

    Returns:
        int: 實際刪除的記錄筆數。
    """
    cutoff = (datetime.now(_TZ_TW) - timedelta(days=days)).isoformat()
    removed = 0
    try:
        conn = get_db_connection(_WORLD_DB_PATH)
        cur = conn.cursor()
        cur.execute("DELETE FROM world_events WHERE ts < ?", (cutoff,))
        removed = cur.rowcount if cur.rowcount is not None and cur.rowcount > 0 else 0
        conn.commit()
        conn.close()
        return removed
    except sqlite3.Error as exc:
        logger.warning("world_memory: 清理原始事件失敗: %s", exc)
        return removed


# 確保資料表存在並執行一次性舊檔種入（模組載入時安全執行）
_init_db()
_seed_legacy_json()
