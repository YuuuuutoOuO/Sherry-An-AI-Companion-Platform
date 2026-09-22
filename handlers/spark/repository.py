"""mod/spark/repository.py - Spark 資料持久化倉儲層 (SQLite 實作)。

負責 Trigger 觸發器設定資料的讀取、儲存、查詢與刪除。
符合 mod規範 §6（資料存放於 data/spark/spark.db，受版本控制保護）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from core.db import get_db_connection, db_transaction
from .config import SPARK_DATA_DIR, TRIGGERS_FILE_PATH
from .models import Trigger

logger = logging.getLogger(__name__)


class SparkRepository:
    """Spark 觸發器 SQLite 資料倉儲。"""

    def __init__(
        self,
        db_path: Path | None = None,
        file_path: Path = TRIGGERS_FILE_PATH,
    ) -> None:
        """初始化資料倉儲並載入現有資料。

        Args:
            db_path: 觸發器 SQLite 儲存路徑。
            file_path: 舊版 JSON 備援路徑。
        """
        self._file_path = file_path
        if db_path is not None:
            self._db_path = db_path
        elif file_path != TRIGGERS_FILE_PATH:
            self._db_path = file_path.parent / "spark.db"
        else:
            self._db_path = SPARK_DATA_DIR / "spark.db"

        self._cache: dict[str, Trigger] = {}
        self._init_db()
        self.reload()

    def _init_db(self) -> None:
        """初始化 SQLite 資料表與索引。"""
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            with db_transaction(self._db_path) as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS triggers (
                        trigger_id TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        channel_id INTEGER NOT NULL DEFAULT 0,
                        trigger_type TEXT NOT NULL,
                        name TEXT NOT NULL DEFAULT '',
                        is_active INTEGER NOT NULL DEFAULT 1,
                        data_json TEXT NOT NULL,
                        is_deleted INTEGER NOT NULL DEFAULT 0,
                        deleted_at TEXT,
                        created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                        updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
                    )
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_spark_user ON triggers(user_id);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_spark_type ON triggers(trigger_type);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_spark_deleted ON triggers(is_deleted);")
        except Exception as exc:
            logger.error("[SparkRepository] 初始化 SQLite 資料庫失敗: %s", exc)

    def reload(self) -> None:
        """重新從 SQLite 載入所有有效 Trigger 資料至記憶體快取 (WHERE is_deleted = 0)。"""
        try:
            conn = get_db_connection(self._db_path)
            cur = conn.cursor()
            cur.execute("SELECT trigger_id, data_json FROM triggers WHERE is_deleted = 0")
            rows = cur.fetchall()
            conn.close()

            loaded: dict[str, Trigger] = {}
            for r in rows:
                try:
                    tdata = json.loads(r["data_json"])
                    loaded[r["trigger_id"]] = Trigger.from_dict(tdata)
                except Exception as parse_err:
                    logger.warning("[SparkRepository] 解析 Trigger 異常: %s", parse_err)
            self._cache = loaded
        except Exception as exc:
            logger.debug("[SparkRepository] 讀取 SQLite 失敗: %s", exc)

    def get(self, trigger_id: str) -> Trigger | None:
        """根據 ID 取得單一觸發器。"""
        if trigger_id not in self._cache:
            self.reload()
        return self._cache.get(trigger_id)

    def list_all(self) -> list[Trigger]:
        """取得所有已註冊的觸發器清單。"""
        self.reload()
        return list(self._cache.values())

    def list_active(self) -> list[Trigger]:
        """取得目前處於啟用狀態 (is_active=True) 的觸發器清單。"""
        self.reload()
        return [t for t in self._cache.values() if t.is_active]

    def list_by_user(self, user_id: int) -> list[Trigger]:
        """取得特定使用者建立的觸發器清單。"""
        self.reload()
        return [t for t in self._cache.values() if t.user_id == user_id]

    def save(self, trigger: Trigger) -> Trigger:
        """儲存或更新單一觸發器（寫入記憶體與 SQLite，標記 is_deleted = 0）。"""
        self._cache[trigger.trigger_id] = trigger
        try:
            t_dict = trigger.to_dict()
            data_json = json.dumps(t_dict, ensure_ascii=False)
            name_val = trigger.condition.get("ticker") or trigger.condition.get("query") or trigger.message or trigger.trigger_type
            with db_transaction(self._db_path) as cur:
                cur.execute("""
                    INSERT INTO triggers (trigger_id, user_id, channel_id, trigger_type, name, is_active, data_json, is_deleted, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0, datetime('now', 'localtime'), datetime('now', 'localtime'))
                    ON CONFLICT(trigger_id) DO UPDATE SET
                        user_id = excluded.user_id,
                        channel_id = excluded.channel_id,
                        trigger_type = excluded.trigger_type,
                        name = excluded.name,
                        is_active = excluded.is_active,
                        data_json = excluded.data_json,
                        is_deleted = 0,
                        updated_at = datetime('now', 'localtime');
                """, (
                    trigger.trigger_id,
                    trigger.user_id,
                    trigger.channel_id,
                    trigger.trigger_type,
                    str(name_val or ""),
                    1 if trigger.is_active else 0,
                    data_json,
                ))
        except Exception as exc:
            logger.error("[SparkRepository] 寫入 SQLite 觸發器失敗: %s", exc)
        return trigger

    def delete(self, trigger_id: str) -> bool:
        """軟刪除指定 ID 的觸發器 (WHERE is_deleted = 0 -> is_deleted = 1)。"""
        if trigger_id not in self._cache:
            return False
        del self._cache[trigger_id]
        try:
            with db_transaction(self._db_path) as cur:
                cur.execute("""
                    UPDATE triggers
                    SET is_deleted = 1, deleted_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime')
                    WHERE trigger_id = ? AND is_deleted = 0;
                """, (trigger_id,))
            return True
        except Exception as exc:
            logger.error("[SparkRepository] 自 SQLite 軟刪除觸發器失敗: %s", exc)
            return False

    def get_stats(self) -> dict[str, Any]:
        """計算觸發器統計摘要。"""
        total = len(self._cache)
        active = sum(1 for t in self._cache.values() if t.is_active)
        paused = total - active

        type_counts: dict[str, int] = {}
        for t in self._cache.values():
            type_counts[t.trigger_type] = type_counts.get(t.trigger_type, 0) + 1

        return {
            "total": total,
            "active": active,
            "paused": paused,
            "by_type": type_counts,
        }
