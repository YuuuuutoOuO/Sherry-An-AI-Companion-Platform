import logging
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import settings
from core.concurrency import db_pool, schedule_in_pool
from core.db import get_db_connection, db_transaction

logger = logging.getLogger(__name__)
MEMORY_DB_PATH = settings.DATA_DIR / "sherry_memory.db"


_RELATIONS_DB_INITIALIZED: bool = False


_INITIALIZED_RELATIONS_DBS: set[str] = set()


class RelationshipManager:
    """
    社交關係管理器（好感度、關係等級，支援 SQLite 持久化）。

    資料庫表：sherry_memory.db -> user_relations
    """

    # 好感度自然衰減：每天減少此量
    DAILY_DECAY: float = 0.5

    # 好感度區間對映
    TIER_THRESHOLDS: list[tuple[int, str]] = [
        (80, "close"),
        (50, "friend"),
        (20, "acquaintance"),
        (0,  "stranger"),
    ]

    def __init__(self, debounce_seconds: float = 30.0) -> None:
        self.store: dict[str, dict[str, Any]] = {}
        self._last_decay_date: str = ""
        self._dirty: bool = False
        self._debounce_seconds: float = debounce_seconds
        self._flush_task: Any = None
        self._init_db()
        self._load()

    def _init_db(self) -> None:
        """初始化 user_relations 資料表 (僅首次執行)。"""
        db_key = str(MEMORY_DB_PATH.resolve()) if MEMORY_DB_PATH.exists() else str(MEMORY_DB_PATH)
        if db_key in _INITIALIZED_RELATIONS_DBS and MEMORY_DB_PATH.exists():
            return
        try:
            MEMORY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            with db_transaction(MEMORY_DB_PATH) as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS user_relations (
                        user_id TEXT PRIMARY KEY,
                        affinity REAL NOT NULL DEFAULT 0.0,
                        tier TEXT NOT NULL DEFAULT 'stranger',
                        interaction_count INTEGER NOT NULL DEFAULT 0,
                        last_interaction TEXT NOT NULL DEFAULT '',
                        is_deleted INTEGER NOT NULL DEFAULT 0,
                        deleted_at TEXT DEFAULT NULL,
                        created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                        updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
                    )
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_rel_affinity ON user_relations(affinity DESC);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_rel_deleted ON user_relations(is_deleted);")
            _INITIALIZED_RELATIONS_DBS.add(db_key)
        except Exception as e:
            logger.error("初始化 SQLite user_relations 失敗: %s", e)

    def _load(self) -> None:
        """優先自 SQLite 載入好感度，若無資料則讀取 JSON 備援。"""
        loaded = False
        try:
            conn = get_db_connection(MEMORY_DB_PATH)
            cur = conn.cursor()
            cur.execute("""
                SELECT user_id, affinity, tier, interaction_count, last_interaction, updated_at
                FROM user_relations
                WHERE is_deleted = 0
            """)
            rows = cur.fetchall()
            conn.close()

            if rows:
                for r in rows:
                    tier_val = str(r["tier"]) if r["tier"] else "stranger"
                    self.store[str(r["user_id"])] = {
                        "affinity": float(r["affinity"]),
                        "tier": tier_val,
                        "affinity_tier": tier_val,
                        "interaction_count": int(r["interaction_count"]),
                        "last_interaction": str(r["last_interaction"]),
                        "updated_at": str(r["updated_at"]),
                    }
                loaded = True
        except Exception as e:
            logger.warning("自 SQLite 讀取 relations 失敗，切換 JSON: %s", e)

        if not loaded:
            path: Path = settings.USER_RELATIONS_PATH
            if path.exists():
                try:
                    with path.open("r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        self.store = data
                except Exception as e:
                    logger.error("讀取 relationship JSON 失敗: %s", e)

    def _do_write_sync(self, store_copy: dict[str, dict[str, Any]]) -> None:
        """同步寫入 SQLite 與備份 JSON (在 ThreadPool 中執行)。"""
        now_str = datetime.now(timezone.utc).isoformat()
        try:
            with db_transaction(MEMORY_DB_PATH) as cur:
                rows = [
                    (
                        str(uid),
                        float(r_obj.get("affinity", 0.0)),
                        str(r_obj.get("affinity_tier") or r_obj.get("tier") or "stranger"),
                        int(r_obj.get("interaction_count", 0)),
                        str(r_obj.get("last_interaction", "")),
                        str(r_obj.get("updated_at", now_str)),
                    )
                    for uid, r_obj in store_copy.items()
                ]
                if rows:
                    cur.executemany("""
                        INSERT INTO user_relations (user_id, affinity, tier, interaction_count, last_interaction, updated_at, is_deleted, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, 0, datetime('now', 'localtime'))
                        ON CONFLICT(user_id) DO UPDATE SET
                            affinity = excluded.affinity,
                            tier = excluded.tier,
                            interaction_count = excluded.interaction_count,
                            last_interaction = excluded.last_interaction,
                            updated_at = excluded.updated_at,
                            is_deleted = 0;
                    """, rows)
        except Exception as e:
            logger.error("寫入 SQLite user_relations 失敗: %s", e)

    def flush(self, force: bool = False) -> None:
        """強制或髒標記落盤。

        Args:
            force: 若為 True，即使 _dirty 為 False 也強制寫入磁碟。
        """
        if not self._dirty and not force:
            return

        self._dirty = False
        store_copy = dict(self.store)

        # 專用 DB 池：避免與 WebUI／長 I/O 共用預設 executor。
        schedule_in_pool(db_pool, self._do_write_sync, store_copy)

    def _schedule_debounce(self) -> None:
        """排程延遲落盤任務（Debounce）。"""
        try:
            import asyncio
            loop = asyncio.get_running_loop()
            if self._flush_task is None or self._flush_task.done():
                async def _delayed_flush() -> None:
                    await asyncio.sleep(self._debounce_seconds)
                    self.flush()
                self._flush_task = loop.create_task(_delayed_flush())
        except RuntimeError:
            # 無運行中的事件迴圈時（如非非同步環境或單元測試），由呼叫方或直接落盤
            pass

    def _save(self) -> None:
        """相容舊版直接存檔介面，觸發 flush。"""
        self.flush(force=True)

    def soft_delete_user(self, uid: int | str) -> None:
        """R6：軟刪除＋匿名化單一使用者關係資料（分數歸零、欄位清空）。

        - 同步自 RAM store 移除該使用者，避免後續 flush 將其寫回。
        - 寫入失敗時拋出例外（由清除管線 `purge_user_data` 記錄為子範圍失敗）。
        """
        uid_str = str(uid)
        self.store.pop(uid_str, None)
        now_str = datetime.now(timezone.utc).isoformat()
        with db_transaction(MEMORY_DB_PATH) as cur:
            cur.execute("""
                UPDATE user_relations
                SET affinity = 0, tier = 'stranger', interaction_count = 0, last_interaction = '',
                    is_deleted = 1, deleted_at = ?, updated_at = datetime('now', 'localtime')
                WHERE user_id = ? AND is_deleted = 0
            """, (now_str, uid_str))
        logger.info("RelationshipManager: 已軟刪除並匿名化使用者 %s 的關係資料", uid_str)

    MAX_AFFINITY: float = 1000.0
    DAILY_AFFINITY_GAIN_LIMIT: float = 10.0

    def get_affinity(self, uid: int) -> float:
        """回傳目前好感度（float，保留小數精度）。"""
        uid_str = str(uid)
        return float(self.store.get(uid_str, {}).get("affinity", 0))

    def update_affinity(self, uid: int, delta: int | float) -> None:
        """更新好感度（以 float 精度儲存，防止 0.5 被 int() 截斷）。

        採用 Debounce 機制延遲落盤，並內建每日防刷上限與總量封頂保護。

        Args:
            uid: Discord 使用者 ID。
            delta: 好感度變化量（可為小數，如 0.5）。
        """
        uid_str = str(uid)
        if uid_str not in self.store:
            self.store[uid_str] = {"affinity": 0.0, "affinity_tier": "stranger",
                                   "interaction_count": 0, "last_interaction": ""}

        # 防刷機制：檢查單日正向增量上限
        actual_delta = float(delta)
        if actual_delta > 0:
            if not hasattr(self, "_daily_gains"):
                self._daily_gains: dict[tuple[str, str], float] = {}
            from datetime import datetime, timezone, timedelta
            today_str = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
            gain_key = (uid_str, today_str)
            today_gain = self._daily_gains.get(gain_key, 0.0)
            if today_gain >= self.DAILY_AFFINITY_GAIN_LIMIT:
                logger.debug("RelationshipManager: 使用者 %s 當日好感度增量已達上限 (+%.1f)，略過累加", uid_str, self.DAILY_AFFINITY_GAIN_LIMIT)
                return
            allowed_gain = min(actual_delta, self.DAILY_AFFINITY_GAIN_LIMIT - today_gain)
            actual_delta = allowed_gain
            self._daily_gains[gain_key] = round(today_gain + allowed_gain, 2)

        current = float(self.store[uid_str].get("affinity", 0))
        new_affinity = min(self.MAX_AFFINITY, max(0.0, round(current + actual_delta, 2)))
        self.store[uid_str]["affinity"] = new_affinity
        self.store[uid_str]["affinity_tier"] = self.get_affinity_tier(uid)
        self.store[uid_str]["interaction_count"] = int(self.store[uid_str].get("interaction_count", 0)) + 1
        self.store[uid_str]["last_interaction"] = datetime.now(timezone.utc).isoformat()

        if abs(actual_delta) > 0:
            self._dirty = True
            self._schedule_debounce()

    def get_affinity_tier(self, uid: int) -> str:
        """根據當前好感度回傳關係等級（使用 float 比較）。"""
        affinity = self.get_affinity(uid)  # 現為 float
        for threshold, tier in self.TIER_THRESHOLDS:
            if affinity >= threshold:
                return tier
        return "stranger"

    def decay_all(self) -> int:
        """對所有使用者進行每日好感度自然衰減（僅對 affinity > 0 者生效）。
        每天至多呼叫一次。回傳受影響的使用者數量。"""
        from datetime import datetime, timezone, timedelta

        today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
        if self._last_decay_date == today:
            return 0
        self._last_decay_date = today

        affected = 0
        for uid_str, data in self.store.items():
            if float(data.get("affinity", 0)) > 0:
                current = float(data["affinity"])
                # ✅ [修復] 衰減也以 float 精度儲存
                data["affinity"] = max(0.0, round(current - self.DAILY_DECAY, 2))
                data["affinity_tier"] = self.get_affinity_tier(int(uid_str))
                affected += 1

        if affected > 0:
            self.flush(force=True)
            logger.info("RelationshipManager: 好感度每日衰減完成，影響 %d 人", affected)
        return affected

    def get_relationship_snippet(self, uid: int) -> str:
        """產生可注入 LLM Prompt 的關係狀態摘要。"""
        tier = self.get_affinity_tier(uid)
        affinity = self.get_affinity(uid)  # float
        tier_names = {
            "stranger": "陌生人", "acquaintance": "認識的人",
            "friend": "朋友", "close": "親近的人",
        }
        tier_cn = tier_names.get(tier, tier)
        return f"❤️ 關係狀態：{tier_cn}（好感度 {affinity:.1f}）"
