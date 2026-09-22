"""
handlers/memory/chat_logger.py
================================
每日聊天記錄工具 (SQLite 持久化版本)。

每一則群組訊息都寫入 data/chat_history.db 中的 chat_messages 資料表。
支援記憶體緩衝區批次寫入、按日期建立索引快速查詢、以及非同步安全寫入。
"""
from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

import discord
import settings
from core.db import get_db_connection, db_transaction

logger = logging.getLogger(__name__)

_TZ_TW = timezone(timedelta(hours=8))   # 台灣時間 UTC+8
DB_PATH = settings.DATA_DIR / "chat_history.db"

_BUFFER_LOCK = threading.Lock()
_LOG_BUFFER: list[tuple[str, dict[str, Any]]] = []
_BUFFER_MAX_SIZE: int = 10


_CHAT_DB_INITIALIZED: bool = False


def _init_chat_db() -> None:
    """初始化 chat_history.db 資料表 (僅首次執行)。"""
    global _CHAT_DB_INITIALIZED
    if _CHAT_DB_INITIALIZED:
        return
    try:
        with db_transaction(DB_PATH) as cur:
            cur.execute("""
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
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_date ON chat_messages(date_str);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_user ON chat_messages(user_id, timestamp);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_server ON chat_messages(server_id, date_str);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_deleted ON chat_messages(is_deleted);")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS processed_dates (
                    date_str TEXT PRIMARY KEY,
                    processed_at TEXT NOT NULL,
                    stats_json TEXT NOT NULL DEFAULT '{}'
                )
            """)
        _CHAT_DB_INITIALIZED = True
    except Exception as exc:
        logger.debug("chat_logger: 初始化 SQLite 資料表失敗: %s", exc)


def _today_str() -> str:
    """回傳台灣時間的今日日期字串 YYYY-MM-DD。"""
    return datetime.now(_TZ_TW).strftime("%Y-%m-%d")


def flush_chat_logs() -> None:
    """將緩衝區內所有未落盤的聊天記錄批次寫入 SQLite 資料庫中 (標記 is_deleted = 0)。"""
    with _BUFFER_LOCK:
        if not _LOG_BUFFER:
            return
        to_flush = list(_LOG_BUFFER)
        _LOG_BUFFER.clear()

    try:
        _init_chat_db()
        with db_transaction(DB_PATH) as cur:
            rows = [
                (
                    str(entry.get("ts", "")),
                    date_str,
                    str(entry.get("user_id", "")),
                    str(entry.get("display_name", "")),
                    str(entry.get("content", "")),
                    str(entry.get("server_id", "")),
                )
                for date_str, entry in to_flush
            ]
            if rows:
                cur.executemany("""
                    INSERT INTO chat_messages (timestamp, date_str, user_id, display_name, content, server_id, processed, is_deleted, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 0, 0, datetime('now', 'localtime'), datetime('now', 'localtime'))
                """, rows)
    except Exception as exc:
        logger.debug("chat_logger flush: 寫入 SQLite 失敗: %s", exc)


def append_chat_log(message: discord.Message) -> None:
    """把一則訊息加入記憶體緩衝區；若滿 _BUFFER_MAX_SIZE 筆則批次非同步落盤。"""
    try:
        content = message.clean_content.strip()
        if not content:
            return

        content_safe = re.sub(r"@\S+", "[某群友]", content)
        guild = getattr(message, "guild", None)
        server_id = str(guild.id) if guild is not None else ""
        entry = {
            "ts": datetime.now(_TZ_TW).isoformat(),
            "user_id": str(message.author.id),
            "display_name": message.author.display_name,
            "content": content_safe,
            "server_id": server_id,
        }
        date_str = _today_str()

        should_flush = False
        with _BUFFER_LOCK:
            _LOG_BUFFER.append((date_str, entry))
            if len(_LOG_BUFFER) >= _BUFFER_MAX_SIZE:
                should_flush = True

        if should_flush:
            try:
                import asyncio
                loop = asyncio.get_running_loop()
                loop.run_in_executor(None, flush_chat_logs)
            except RuntimeError:
                flush_chat_logs()
    except Exception as exc:
        logger.debug("chat_logger: 加入緩衝失敗: %s", exc)


async def async_append_chat_log(message: discord.Message) -> None:
    """非同步版本的追加寫入，於需要 Flush 時在 ThreadPool 中執行，避免阻塞事件迴圈。"""
    append_chat_log(message)


# ---------------------------------------------------------------------------
# 讀取
# ---------------------------------------------------------------------------

def read_chat_log(date_str: str, server_id: str = "") -> list[dict[str, Any]]:
    """讀取指定日期的所有未刪除訊息記錄 (WHERE is_deleted = 0)，可依 server_id 過濾。

    回傳 list[dict]，順序為寫入順序（時間正序）。
    """
    flush_chat_logs()
    records: list[dict[str, Any]] = []

    try:
        _init_chat_db()
        conn = get_db_connection(DB_PATH)
        try:
            cur = conn.cursor()
            if server_id:
                cur.execute("""
                    SELECT timestamp, user_id, display_name, content, server_id
                    FROM chat_messages
                    WHERE date_str = ? AND server_id = ? AND is_deleted = 0
                    ORDER BY id ASC
                """, (date_str, server_id))
            else:
                cur.execute("""
                    SELECT timestamp, user_id, display_name, content, server_id
                    FROM chat_messages
                    WHERE date_str = ? AND is_deleted = 0
                    ORDER BY id ASC
                """, (date_str,))
            rows = cur.fetchall()
        finally:
            conn.close()

        for r in rows:
            sid = r["server_id"] if "server_id" in r.keys() else ""
            records.append({
                "ts": r["timestamp"],
                "user_id": r["user_id"],
                "display_name": r["display_name"],
                "content": r["content"],
                "server_id": sid,
            })

        if records:
            return records
    except Exception as exc:
        logger.warning("chat_logger: 讀取 SQLite %s 失敗: %s", date_str, exc)

    # 向下相容備援：若 DB 無資料，嘗試讀取既有 JSONL
    path = settings.CHAT_LOG_DIR / f"{date_str}.jsonl"
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        except Exception as exc:
            logger.warning("chat_logger: 讀取 JSONL %s 失敗: %s", date_str, exc)

    return records


# ---------------------------------------------------------------------------
# 處理標記
# ---------------------------------------------------------------------------

def is_date_processed(date_str: str) -> bool:
    """確認該日期是否已整合過。"""
    try:
        _init_chat_db()
        conn = get_db_connection(DB_PATH)
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM processed_dates WHERE date_str = ?", (date_str,))
            row = cur.fetchone()
            if row:
                return True
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("chat_logger: 檢查 processed_dates 失敗: %s", exc)

    # 向下相容檢查 .processed 檔
    proc_file = settings.CHAT_LOG_DIR / f"{date_str}.jsonl.processed"
    return proc_file.exists()


def mark_date_processed(date_str: str, stats: Optional[dict[str, Any]] = None) -> None:
    """標記該日期已完成整合。"""
    now_str = datetime.now(_TZ_TW).isoformat()
    stats_json = json.dumps(stats or {}, ensure_ascii=False)

    try:
        _init_chat_db()
        with db_transaction(DB_PATH) as cur:
            cur.execute("""
                INSERT OR REPLACE INTO processed_dates (date_str, processed_at, stats_json)
                VALUES (?, ?, ?)
            """, (date_str, now_str, stats_json))
            cur.execute("UPDATE chat_messages SET processed = 1 WHERE date_str = ?", (date_str,))
    except Exception as exc:
        logger.warning("chat_logger: 寫入 processed_dates 失敗: %s", exc)

    # 同步建立標記檔以確保舊邏輯相容
    try:
        settings.CHAT_LOG_DIR.mkdir(parents=True, exist_ok=True)
        info: dict[str, Any] = {
            "processed_at": now_str,
            "date": date_str,
        }
        if stats:
            info.update(stats)
        path = settings.CHAT_LOG_DIR / f"{date_str}.jsonl.processed"
        with path.open("w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def get_yesterday_str() -> str:
    """回傳台灣時間的昨日日期字串 YYYY-MM-DD。"""
    yesterday = datetime.now(_TZ_TW) - timedelta(days=1)
    return yesterday.strftime("%Y-%m-%d")
