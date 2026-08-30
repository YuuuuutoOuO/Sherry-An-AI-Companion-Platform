import logging
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import settings

logger = logging.getLogger(__name__)


class RelationshipManager:
    """
    社交關係管理器（好感度、關係等級）。

    ⚠️  存檔路徑為 USER_RELATIONS_PATH（user_relations.json）。
        不再與 UserProfile (user_memory.json) 共用同一個 JSON 檔案，
        避免兩個不同結構的資料互相覆蓋破壞。

    使用方式：
        # 在 MemoryManager Cog 中自動實例化
        mem_mgr = bot.get_cog("MemoryManager")
        mem_mgr.relations.update_affinity(user_id, delta)
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
        self._load()

    def _load(self) -> None:
        path: Path = settings.USER_RELATIONS_PATH
        if path.exists():
            try:
                with path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self.store = data
            except Exception as e:
                logger.error("讀取 relationship 失敗: %s", e)

    def _do_write_sync(self, store_copy: dict[str, dict[str, Any]]) -> None:
        """同步原子寫入磁碟。"""
        path: Path = settings.USER_RELATIONS_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(store_copy, f, ensure_ascii=False, indent=2)
            tmp.replace(path)
        except Exception as e:
            logger.error("寫入 relationship 失敗: %s", e)

    def flush(self, force: bool = False) -> None:
        """強制或髒標記落盤。

        Args:
            force: 若為 True，即使 _dirty 為 False 也強制寫入磁碟。
        """
        if not self._dirty and not force:
            return

        self._dirty = False
        store_copy = dict(self.store)

        try:
            import asyncio
            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, self._do_write_sync, store_copy)
        except RuntimeError:
            self._do_write_sync(store_copy)

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

    def get_affinity(self, uid: int) -> float:
        """回傳目前好感度（float，保留小數精度）。"""
        uid_str = str(uid)
        return float(self.store.get(uid_str, {}).get("affinity", 0))

    def update_affinity(self, uid: int, delta: int | float) -> None:
        """更新好感度（以 float 精度儲存，防止 0.5 被 int() 截斷）。

        採用 Debounce 機制延遲落盤，避免每則訊息重複觸發全量寫入。

        Args:
            uid: Discord 使用者 ID。
            delta: 好感度變化量（可為小數，如 0.5）。
        """
        uid_str = str(uid)
        if uid_str not in self.store:
            self.store[uid_str] = {"affinity": 0.0, "affinity_tier": "stranger",
                                   "interaction_count": 0, "last_interaction": ""}

        current = float(self.store[uid_str].get("affinity", 0))
        # ✅ [修復] 以 float 儲存，不再用 int() 截斷，保留 0.5 的精度
        self.store[uid_str]["affinity"] = max(0.0, round(current + delta, 2))
        self.store[uid_str]["affinity_tier"] = self.get_affinity_tier(uid)
        self.store[uid_str]["interaction_count"] = int(self.store[uid_str].get("interaction_count", 0)) + 1
        self.store[uid_str]["last_interaction"] = datetime.now(timezone.utc).isoformat()

        if abs(delta) > 0:
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
