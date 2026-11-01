"""
handlers/vibe_manager/storage.py
================================
頻道氛圍主題資料 SQLite 持久化模組。
存放於 data/sherry_memory.db 中的 channel_vibes 資料表。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, TypedDict

import settings
from core.db import get_db_connection, db_transaction

logger = logging.getLogger(__name__)
MEMORY_DB_PATH: Path = settings.DATA_DIR / "sherry_memory.db"

_CREATE_VIBE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS channel_vibes (
        channel_id TEXT PRIMARY KEY,
        topics_json TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
"""

_UPSERT_VIBE_SQL = """
    INSERT OR REPLACE INTO channel_vibes (channel_id, topics_json, updated_at)
    VALUES (?, ?, datetime('now'))
"""


class TopicEntry(TypedDict):
    summary: str
    weight: int


VibeStore = dict[str, dict[str, TopicEntry]]


def migrate_entry(value: Any) -> TopicEntry:
    """若是舊系統遺留純粹記錄 int 值的舊設定，轉到具備內容描述與熱度整數的新設定版"""
    if isinstance(value, int):
        return TopicEntry(summary="", weight=value)
    if isinstance(value, dict) and "weight" in value:
        return TopicEntry(
            summary=str(value.get("summary", "")),
            weight=int(value["weight"]),
        )
    return TopicEntry(summary="", weight=1)


def _init_vibe_db() -> None:
    """初始化 channel_vibes 資料表。"""
    try:
        with db_transaction(MEMORY_DB_PATH) as cur:
            cur.execute(_CREATE_VIBE_TABLE_SQL)
    except Exception as e:
        logger.error("[VibeStorage] 初始化 SQLite channel_vibes 失敗: %s", e)


def load_vibe_store(path: Path) -> VibeStore:
    """優先自 SQLite (sherry_memory.db) 載入主題歷史檔，若無資料則讀取 JSON 備援。"""
    _init_vibe_db()
    try:
        conn = get_db_connection(MEMORY_DB_PATH)
        try:
            cur = conn.cursor()
            cur.execute("SELECT channel_id, topics_json FROM channel_vibes")
            rows = cur.fetchall()
        finally:
            conn.close()

        if rows:
            store: VibeStore = {}
            for r in rows:
                ch_id = str(r["channel_id"])
                topics_dict = json.loads(r["topics_json"])
                store[ch_id] = {
                    topic: migrate_entry(entry)
                    for topic, entry in topics_dict.items()
                    if isinstance(topic, str)
                }
            return store
    except Exception as exc:
        logger.debug("[VibeStorage] 讀取 SQLite 失敗，切換 JSON 備援: %s", exc)

    # JSON 備援載入
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data: Any = json.load(fh)
        if not isinstance(data, dict):
            return {}

        migrated: VibeStore = {}
        for ch_id, topics in data.items():
            if not isinstance(topics, dict):
                continue
            migrated[str(ch_id)] = {
                topic: migrate_entry(entry)
                for topic, entry in topics.items()
                if isinstance(topic, str)
            }
        return migrated

    except Exception as exc:
        logger.error("[VibeStorage] JSON 存取失敗: %s", exc)
        return {}


def save_vibe_store(store: VibeStore, path: Path) -> None:
    """將主題記憶批次儲存落盤至 SQLite (sherry_memory.db)。

    以單一交易 + ``executemany`` 取代逐列 ``execute``，降低每次存檔的
    SQLite 往返次數；空 store 僅確保資料表存在。
    """
    rows = [
        (str(ch_id), json.dumps(topics, ensure_ascii=False))
        for ch_id, topics in store.items()
    ]
    try:
        with db_transaction(MEMORY_DB_PATH) as cur:
            cur.execute(_CREATE_VIBE_TABLE_SQL)
            if rows:
                cur.executemany(_UPSERT_VIBE_SQL, rows)
    except Exception as exc:
        logger.error("[VibeStorage] 寫入 SQLite 失敗: %s", exc)


def save_vibe_channel(channel_id: str, topics: dict[str, TopicEntry]) -> None:
    """僅儲存單一頻道的主題狀態，避免每次更新都重寫整張表。"""
    try:
        with db_transaction(MEMORY_DB_PATH) as cur:
            cur.execute(_CREATE_VIBE_TABLE_SQL)
            cur.execute(
                _UPSERT_VIBE_SQL,
                (str(channel_id), json.dumps(topics, ensure_ascii=False)),
            )
    except Exception as exc:
        logger.error("[VibeStorage] 寫入 SQLite 頻道 %s 失敗: %s", channel_id, exc)