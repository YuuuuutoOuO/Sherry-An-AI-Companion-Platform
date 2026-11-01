"""mod/spark/service.py - Spark 核心業務邏輯服務層。

負責觸發器排程調度、冷卻判斷、條件評估與 CRUD 生命週期管理。
純業務邏輯層：依據 AGENTS.md §0 禁止直接引用 Discord 或任何外部 I/O 套件。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from .checkers.base import BaseChecker
from .checkers.price_checker import PriceChecker
from .checkers.rss_checker import RssChecker
from .checkers.schedule_checker import ScheduleChecker
from .checkers.weather_checker import WeatherChecker
from .checkers.web_monitor_checker import WebMonitorChecker
from .models import Trigger, TriggerResult
from .quota import SparkPushQuota, clamp_interval_minutes
from .repository import SparkRepository

logger = logging.getLogger(__name__)


def _settings_int(name: str, default: int) -> int:
    """讀取 settings 的整數設定，缺失時回退預設值。"""
    try:
        import settings as _settings

        return int(getattr(_settings, name, default))
    except (ImportError, TypeError, ValueError):
        return default


def _settings_float(name: str, default: float) -> float:
    """讀取 settings 的浮點設定，缺失或非法時回退預設值。"""
    try:
        import settings as _settings

        value = float(getattr(_settings, name, default))
        return value if value > 0 else default
    except (ImportError, TypeError, ValueError):
        return default


class SparkQuotaExceeded(ValueError):
    """建立 trigger 時超出配額上限。"""


class SparkService:
    """Spark 核心推播業務服務。"""

    def __init__(
        self,
        repository: SparkRepository,
        checkers: dict[str, BaseChecker],
        max_triggers_per_user: int | None = None,
        push_quota: SparkPushQuota | None = None,
    ) -> None:
        """初始化服務實例。

        Args:
            repository: 觸發器資料倉儲。
            checkers: 觸發器類型對應之檢查策略字典。
            max_triggers_per_user: 每位使用者 trigger 數上限（None 讀取 settings）。
            push_quota: 每日推送配額管理器（None 建立預設實例）。
        """
        self._repo = repository
        self._checkers = checkers
        self._max_triggers_per_user = (
            max_triggers_per_user
            if max_triggers_per_user is not None
            else _settings_int("SPARK_MAX_TRIGGERS_PER_USER", 10)
        )
        self._push_quota = push_quota or SparkPushQuota(
            daily_limit=_settings_int("SPARK_MAX_PUSHES_PER_DAY", 30)
        )
        # 最近一輪巡檢摘要（供 /api/spark/stats 與 WebUI 觀測；僅記憶體狀態）。
        self._last_cycle_summary: dict[str, Any] = {}

    @property
    def repository(self) -> SparkRepository:
        """唯讀暴露倉儲，供關懷引擎等上層讀取 trigger 候選。"""
        return self._repo

    def is_in_cooldown(
        self,
        trigger: Trigger,
        now_dt: datetime | None = None,
    ) -> bool:
        """判斷特定觸發器是否仍在冷卻時間內。

        Args:
            trigger: 欲評估的觸發器。
            now_dt: 當前時間（選填，預設為 UTC 現在時間）。

        Returns:
            bool: 若仍在冷卻中則回傳 True，否則回傳 False。
        """
        if not trigger.last_triggered_at:
            return False

        if trigger.cooldown_seconds <= 0:
            return False

        now = now_dt or datetime.now(timezone.utc)
        try:
            last_dt = datetime.fromisoformat(trigger.last_triggered_at)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            elapsed = (now - last_dt).total_seconds()
            return elapsed < trigger.cooldown_seconds
        except Exception as exc:
            logger.debug("[SparkService] 解析 last_triggered_at 失敗: %s", exc)
            return False

    async def check_trigger(
        self,
        trigger: Trigger,
        now_dt: datetime | None = None,
        dry_run: bool = False,
    ) -> tuple[TriggerResult, Trigger | None]:
        """評估單一觸發器條件，並於觸發時更新狀態與持久化。

        Args:
            trigger: 欲評估之 Trigger。
            now_dt: 當前時間（選填）。
            dry_run: 僅測試條件，不寫回 ``last_triggered_at``／``run_count``，
                也不消耗 one-shot 任務；供「立即測試」入口使用。

        Returns:
            tuple[TriggerResult, Trigger | None]:
                - TriggerResult: 檢查成果。
                - Trigger | None: 若成功觸發則回傳更新後之 Trigger，否則為 None。
                  ``dry_run=True`` 時恆為 None。
        """
        checker = self._checkers.get(trigger.trigger_type)
        if not checker:
            return (
                TriggerResult(
                    should_fire=False,
                    current_value=f"未註冊之觸發類型: {trigger.trigger_type}",
                ),
                None,
            )

        timeout_seconds = _settings_float("SPARK_CHECKER_TIMEOUT_SECONDS", 45.0)
        try:
            import inspect

            sig = inspect.signature(checker.should_fire)
            from core.compat import async_timeout
            async with async_timeout(timeout_seconds):
                if "now_dt" in sig.parameters:
                    result = await checker.should_fire(trigger, now_dt=now_dt)  # type: ignore[call-arg]
                else:
                    result = await checker.should_fire(trigger)
        except (asyncio.TimeoutError, TimeoutError):
            logger.warning(
                "[SparkService] Checker (%s) 超過 %.1f 秒未回應，已取消",
                trigger.trigger_id,
                timeout_seconds,
            )
            return (
                TriggerResult(
                    should_fire=False,
                    current_value=f"檢查器逾時（>{timeout_seconds:.0f} 秒）",
                    detail_data={"timeout": True, "timeout_seconds": timeout_seconds},
                ),
                None,
            )
        except Exception as exc:
            logger.exception(
                "[SparkService] 執行 Checker (%s) 異常: %s", trigger.trigger_id, exc
            )
            return (
                TriggerResult(
                    should_fire=False,
                    current_value=f"檢查器執行失敗: {exc}",
                ),
                None,
            )

        if not result.should_fire:
            return result, None

        if dry_run:
            logger.info(
                "[SparkService] dry-run 測試觸發（不寫回狀態）: %s", trigger.trigger_id
            )
            return result, None

        # 成功觸發：更新 last_triggered_at 與可能變更的 condition / extra_data 狀態
        now = now_dt or datetime.now(timezone.utc)
        new_condition = dict(trigger.condition)
        new_extra_data = dict(trigger.extra_data) if isinstance(trigger.extra_data, dict) else {}

        # 累計觸發次數遞增
        new_extra_data["run_count"] = int(new_extra_data.get("run_count", 0)) + 1

        # 若檢查器回傳需更新的狀態（如 RSS 最新 ID、股價 last_price、內容指紋 last_content_hash）
        if "last_item_id" in result.detail_data:
            new_condition["last_item_id"] = result.detail_data["last_item_id"]
        if "last_price" in result.detail_data:
            new_condition["last_price"] = result.detail_data["last_price"]
        if "last_triggered_price" in result.detail_data:
            new_condition["last_triggered_price"] = result.detail_data["last_triggered_price"]
        if "last_content_hash" in result.detail_data:
            new_condition["last_content_hash"] = result.detail_data["last_content_hash"]
            new_extra_data["last_content_hash"] = result.detail_data["last_content_hash"]

        # 單次達標 (one_shot) 處理：若觸發標記為 one_shot_fired，自動切換為暫停/完成狀態 (is_active=False)
        is_active = trigger.is_active
        if result.detail_data.get("one_shot_fired") is True:
            is_active = False

        updated_trigger = Trigger(
            trigger_id=trigger.trigger_id,
            user_id=trigger.user_id,
            channel_id=trigger.channel_id,
            trigger_type=trigger.trigger_type,
            condition=new_condition,
            message=trigger.message,
            is_active=is_active,
            cooldown_seconds=trigger.cooldown_seconds,
            created_at=trigger.created_at,
            last_triggered_at=now.isoformat(),
            extra_data=new_extra_data,
            action=dict(trigger.action),
        )

        saved = self._repo.save(updated_trigger)
        return result, saved

    def create_trigger(
        self,
        user_id: int,
        channel_id: int,
        trigger_type: str,
        condition: dict[str, Any],
        message: str = "",
        cooldown_seconds: int = 3600,
        is_active: bool = True,
        action: dict[str, Any] | None = None,
    ) -> Trigger:
        """建立並儲存新的推播觸發器。

        Args:
            user_id: 建立者 Discord ID。
            channel_id: 目標推播頻道 ID。
            trigger_type: 類型 ("price" | "schedule" | "rss" | "weather")。
            condition: 條件字典。
            message: 自訂附加訊息。
            cooldown_seconds: 觸發後冷卻秒數。
            is_active: 是否立即啟用。
            action: 觸發時執行的動作定義字典。

        Returns:
            Trigger: 已持久化的新觸發器實例。

        Raises:
            SparkQuotaExceeded: 該使用者的 trigger 數已達上限。
        """
        action_dict = dict(action or {})
        if self._max_triggers_per_user > 0:
            owned = sum(1 for t in self._repo.list_all() if t.user_id == user_id)
            if owned >= self._max_triggers_per_user:
                raise SparkQuotaExceeded(
                    f"每位使用者最多 {self._max_triggers_per_user} 個推播任務（目前已 {owned} 個），"
                    "請先刪除不用的任務。"
                )

        condition = clamp_interval_minutes(condition, action_dict)
        trigger_id = str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()

        trigger = Trigger(
            trigger_id=trigger_id,
            user_id=user_id,
            channel_id=channel_id,
            trigger_type=trigger_type,
            condition=condition,
            message=message,
            is_active=is_active,
            cooldown_seconds=max(0, cooldown_seconds),
            created_at=now_iso,
            last_triggered_at="",
            extra_data={},
            action=action_dict,
        )
        return self._repo.save(trigger)

    def delete_trigger(self, trigger_id: str) -> bool:
        """刪除指定觸發器。

        Args:
            trigger_id: 觸發器 UUID。

        Returns:
            bool: 是否成功刪除。
        """
        return self._repo.delete(trigger_id)

    def toggle_trigger(self, trigger_id: str) -> Trigger | None:
        """切換觸發器的啟用/暫停狀態。

        Args:
            trigger_id: 觸發器 UUID。

        Returns:
            Trigger | None: 更新後的觸發器實例，若不存在則為 None。
        """
        trigger = self._repo.get(trigger_id)
        if not trigger:
            return None

        updated = Trigger(
            trigger_id=trigger.trigger_id,
            user_id=trigger.user_id,
            channel_id=trigger.channel_id,
            trigger_type=trigger.trigger_type,
            condition=trigger.condition,
            message=trigger.message,
            is_active=not trigger.is_active,
            cooldown_seconds=trigger.cooldown_seconds,
            created_at=trigger.created_at,
            last_triggered_at=trigger.last_triggered_at,
            extra_data=trigger.extra_data,
            action=dict(trigger.action),
        )
        return self._repo.save(updated)

    def get_trigger(self, trigger_id: str) -> Trigger | None:
        """取得單一觸發器。"""
        return self._repo.get(trigger_id)

    def list_triggers(
        self,
        user_id: int | None = None,
        is_active: bool | None = None,
    ) -> list[Trigger]:
        """查詢符合條件的觸發器清單。

        Args:
            user_id: 依建立者篩選（選填）。
            is_active: 依啟用狀態篩選（選填）。

        Returns:
            list[Trigger]: 觸發器清單。
        """
        triggers = self._repo.list_all()
        if user_id is not None:
            triggers = [t for t in triggers if t.user_id == user_id]
        if is_active is not None:
            triggers = [t for t in triggers if t.is_active == is_active]
        return triggers

    def list_active_triggers(self) -> list[Trigger]:
        """取得所有處於啟用狀態的觸發器。"""
        return self._repo.list_active()

    def get_stats(self) -> dict[str, Any]:
        """取得統計資料（含最近一輪巡檢摘要）。"""
        stats = self._repo.get_stats()
        stats["last_cycle"] = self.get_last_cycle_summary()
        return stats

    def record_cycle_summary(self, summary: dict[str, Any]) -> None:
        """記錄最近一輪巡檢的執行摘要（供 WebUI／日誌觀測）。

        Args:
            summary: 摘要鍵值（觸發數、成功／失敗／跳過、耗時、預算拒絕等）。
        """
        self._last_cycle_summary = dict(summary)

    def get_last_cycle_summary(self) -> dict[str, Any]:
        """回傳最近一輪巡檢摘要的副本。"""
        return dict(self._last_cycle_summary)

    def consume_push_quota(self, user_id: int) -> tuple[bool, str]:
        """消耗一次每日推送配額（供行動派發前檢查）。

        Args:
            user_id: 推送對象 Discord ID。

        Returns:
            tuple[bool, str]: (是否允許, 原因)。
        """
        return self._push_quota.consume(user_id)

    def update_trigger_memory(self, trigger_id: str, output_summary: str) -> Trigger | None:
        """更新觸發器的跨輪增量記憶摘要 (Stateful Spark Memory)。

        Args:
            trigger_id: 觸發器唯一識別碼。
            output_summary: 雪莉本輪匯報成果摘要。

        Returns:
            Trigger | None: 更新後的 Trigger，若不存在則回傳 None。
        """
        trigger = self._repo.get(trigger_id)
        if not trigger:
            return None

        extra_data = dict(trigger.extra_data) if isinstance(trigger.extra_data, dict) else {}
        extra_data["last_output_summary"] = str(output_summary)[:300].strip()
        extra_data["last_output_at"] = datetime.now(timezone.utc).isoformat()

        updated = Trigger(
            trigger_id=trigger.trigger_id,
            user_id=trigger.user_id,
            channel_id=trigger.channel_id,
            trigger_type=trigger.trigger_type,
            condition=trigger.condition,
            message=trigger.message,
            is_active=trigger.is_active,
            cooldown_seconds=trigger.cooldown_seconds,
            created_at=trigger.created_at,
            last_triggered_at=trigger.last_triggered_at,
            extra_data=extra_data,
            action=dict(trigger.action),
        )
        return self._repo.save(updated)


def build_spark_service(
    repo: SparkRepository,
    clients: dict[str, Any],
) -> SparkService:
    """工廠函式：組裝 SparkService 並完成策略依賴注入 (IoC)。

    Args:
        repo: 觸發器資料持久化倉儲。
        clients: 包含各類 I/O 客戶端的字典。

    Returns:
        SparkService: 已注入依賴的服務實例。
    """
    checkers: dict[str, BaseChecker] = {
        "price": PriceChecker(clients["stock"]),
        "schedule": ScheduleChecker(),
        "rss": RssChecker(clients["rss"]),
        "weather": WeatherChecker(clients["weather"]),
        "web_monitor": WebMonitorChecker(clients.get("web_monitor")),
    }
    return SparkService(repo, checkers)
