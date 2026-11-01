"""handlers/spark/quota.py - SPARK 推播配額與週期分級。

將「每位使用者的每日推送上限」與「排程週期下限」集中為單一政策物件，
供 `SparkService`（建立 trigger 時）與 `SparkActionRunner`（實際推送前）共用。
純記憶體狀態、零外部 I/O，符合 AGENTS.md §0。
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import threading

logger = logging.getLogger(__name__)

# 週期分級：會觸發 LLM 推論的動作需較長間隔，純通知可較短
LLM_ACTION_TYPES = frozenset({"core_tool", "agent_prompt", "mod_deep_search", "deep_search", "agent"})
LLM_INTERVAL_FLOOR_MINUTES = 30
NOTIFY_INTERVAL_FLOOR_MINUTES = 5


class SparkPushQuota:
    """每日推送配額（per-user），跨日自動重置。"""

    def __init__(self, daily_limit: int = 30) -> None:
        """初始化配額管理器。

        Args:
            daily_limit: 每位使用者每日允許的推送次數上限（<=0 表示不限）。
        """
        self.daily_limit = max(0, int(daily_limit))
        self._lock = threading.Lock()
        self._counts: dict[tuple[int, str], int] = {}

    @staticmethod
    def _day_key(now: datetime | None) -> str:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc).date().isoformat()

    def consume(self, user_id: int, now: datetime | None = None) -> tuple[bool, str]:
        """嘗試消耗一次推送額度。

        Args:
            user_id: 推送對象的 Discord ID。
            now: 判定時間（測試注入用）。

        Returns:
            tuple[bool, str]: (是否允許, 拒絕原因或成功提示)。
        """
        if self.daily_limit <= 0:
            return True, "未設定每日上限"

        key = (int(user_id), self._day_key(now))
        with self._lock:
            # 跨日清理：只保留當日鍵，避免長期累積
            for stale in [k for k in self._counts if k[1] != key[1]]:
                self._counts.pop(stale, None)
            used = self._counts.get(key, 0)
            if used >= self.daily_limit:
                reason = f"已達每日推送上限 ({used}/{self.daily_limit})"
                logger.warning("[SparkQuota] %s，略過使用者 %s 的推送", reason, user_id)
                return False, reason
            self._counts[key] = used + 1
            return True, "配額核准"


def interval_floor_minutes(action: dict[str, object] | None) -> int:
    """依動作型態回傳排程週期下限（分鐘）。

    Args:
        action: Trigger 的動作定義字典。

    Returns:
        int: LLM 型 30 分鐘、純通知型 5 分鐘。
    """
    action_type = ""
    if isinstance(action, dict):
        action_type = str(action.get("action_type") or "").lower().strip()
    if action_type in LLM_ACTION_TYPES:
        return LLM_INTERVAL_FLOOR_MINUTES
    return NOTIFY_INTERVAL_FLOOR_MINUTES


def clamp_interval_minutes(
    condition: dict[str, object],
    action: dict[str, object] | None,
) -> dict[str, object]:
    """將 interval 排程的週期夾到政策下限。

    Args:
        condition: Trigger 條件字典（不會被就地修改）。
        action: Trigger 動作定義。

    Returns:
        dict[str, object]: 套用下限後的新條件字典。
    """
    updated = dict(condition)
    mode = str(updated.get("mode") or "").lower().strip()
    if mode not in ("interval", "periodic"):
        return updated
    try:
        minutes = int(updated.get("interval_minutes", 60))
    except (TypeError, ValueError):
        minutes = 60
    floor = interval_floor_minutes(action)
    if minutes < floor:
        logger.info(
            "[SparkQuota] 週期 %d 分鐘低於下限 %d 分鐘，已自動調升",
            minutes,
            floor,
        )
        updated["interval_minutes"] = floor
    return updated
