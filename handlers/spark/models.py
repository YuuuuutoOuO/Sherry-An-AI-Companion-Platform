"""mod/spark/models.py - Spark 推播系統資料模型。

定義 Trigger、TriggerResult 與相關輔助不可變資料結構。
遵循 AGENTS.md §2（不可變資料傳遞與完整型別標註）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class TriggerType(str, Enum):
    """推播觸發器支援的類型枚舉。"""

    PRICE = "price"
    SCHEDULE = "schedule"
    RSS = "rss"
    WEATHER = "weather"


class ActionType(str, Enum):
    """推播觸發後執行之動作類型。"""

    NOTIFY = "notify"                  # 純文字/Embed 訊息通知
    CORE_TOOL = "core_tool"            # 調用核心工具 (如 PIXIV, STOCK, WEATHER, DRAW)
    MOD_DEEP_SEARCH = "mod_deep_search"  # 調用特權模組: 深度探究自主研究


@dataclass(frozen=True)
class Trigger:
    """單一推播觸發器定義。

    Args:
        trigger_id: 觸發器唯一識別碼 (UUID 字串)。
        user_id: 建立者 Discord 使用者 ID。
        channel_id: 推播目標 Discord 頻道 ID。
        trigger_type: 觸發器類型 ("price" | "schedule" | "rss" | "weather")。
        condition: 類型專屬之條件參數字典。
        message: 自訂推播附加訊息或模板。
        is_active: 是否處於啟用狀態。
        cooldown_seconds: 每次觸發後的冷卻秒數（防止洗版）。
        created_at: 建立時間戳記 (ISO 8601 字串)。
        last_triggered_at: 上次成功觸發時間戳記 (ISO 8601 字串)。
        extra_data: 額外擴充與狀態儲存欄位。
        action: 觸發時執行的動作定義 (action_type, target, payload)。
    """

    trigger_id: str
    user_id: int
    channel_id: int
    trigger_type: str
    condition: dict[str, Any]
    message: str = ""
    is_active: bool = True
    cooldown_seconds: int = 3600
    created_at: str = ""
    last_triggered_at: str = ""
    extra_data: dict[str, Any] = field(default_factory=dict)
    action: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """序列化為字典，供 JSON 持久化與 API 傳輸。

        Returns:
            dict[str, Any]: 觸發器資料字典。
        """
        return {
            "trigger_id": self.trigger_id,
            "user_id": self.user_id,
            "channel_id": self.channel_id,
            "trigger_type": self.trigger_type,
            "condition": dict(self.condition),
            "message": self.message,
            "is_active": self.is_active,
            "cooldown_seconds": self.cooldown_seconds,
            "created_at": self.created_at,
            "last_triggered_at": self.last_triggered_at,
            "extra_data": dict(self.extra_data) if isinstance(self.extra_data, dict) else {},
            "action": dict(self.action) if isinstance(self.action, dict) else {},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Trigger:
        """從字典反序列化為 Trigger 物件。

        Args:
            data: 來源資料字典。

        Returns:
            Trigger: 觸發器不可變實例。
        """
        created_at = str(data.get("created_at") or datetime.now(timezone.utc).isoformat())
        raw_action = data.get("action")
        if isinstance(raw_action, dict):
            action_dict = dict(raw_action)
        elif isinstance(raw_action, str) and raw_action.strip():
            action_dict = {"action_type": raw_action.strip()}
        else:
            action_dict = {}

        return cls(
            trigger_id=str(data.get("trigger_id") or ""),
            user_id=int(data.get("user_id") or 0),
            channel_id=int(data.get("channel_id") or 0),
            trigger_type=str(data.get("trigger_type") or ""),
            condition=dict(data.get("condition") or {}) if isinstance(data.get("condition"), dict) else {},
            message=str(data.get("message") or ""),
            is_active=bool(data.get("is_active", True)),
            cooldown_seconds=int(data.get("cooldown_seconds", 3600)),
            created_at=created_at,
            last_triggered_at=str(data.get("last_triggered_at") or ""),
            extra_data=dict(data.get("extra_data") or {}) if isinstance(data.get("extra_data"), dict) else {},
            action=action_dict,
        )


@dataclass(frozen=True)
class TriggerResult:
    """觸發器檢查成果合約。

    Args:
        should_fire: 是否滿足觸發條件並應發送推播。
        current_value: 當前量測數值或事件摘要說明。
        detail_data: 額外結構化資料（如最新文章資訊、報價明細）。
    """

    should_fire: bool
    current_value: str = ""
    detail_data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RssItem:
    """RSS / Atom 訂閱項目資料模型。

    Args:
        item_id: 文章或項目的唯一識別碼。
        title: 文章標題。
        url: 文章連結。
        published_at: 發布時間字串。
        summary: 文章摘要或簡述。
    """

    item_id: str
    title: str
    url: str
    published_at: str = ""
    summary: str = ""
