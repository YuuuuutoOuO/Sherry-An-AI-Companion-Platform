"""handlers/social/autonomy/gate.py
================================
最小可行發話閘門（SpeechGate）。

這不是「決定要說什麼」的地方，而是所有主動發話前的最後一道共同檢查：
- 設定是否開啟（surfer_enabled / chat_reply_enabled）
- 是否在允許講話時段
- 是否仍在接話冷卻
- 沙盒總開關是否開啟
- NPC 是否已連續發言到上限

任何一項不通過，都回傳 allow=False 並附上可讀的 reason，
由 SocialDirector 寫進 social_decisions（action=SILENT）。
"""

from __future__ import annotations

import time
from typing import Any

import settings as default_settings

from handlers.care.repository import is_within_active_hours

from .contracts import ChannelMode, GateResult, TriggerType


class SpeechGate:
    """集中式發話閘門。所有主動路徑都必須先經過 evaluate()。"""

    def __init__(self, settings_module: Any | None = None) -> None:
        self.settings = settings_module or default_settings

    def evaluate(
        self,
        *,
        config: Any,
        trigger_type: TriggerType | str,
        mode: ChannelMode | str = ChannelMode.SOLO,
        now: float | None = None,
        room: Any | None = None,
        npc_consecutive: int = 0,
        human_author_is_bot: bool = False,
        hourly_speaks: int = 0,
        daily_speaks: int = 0,
        bypass_budget: bool = False,
    ) -> GateResult:
        """回傳本次是否允許發言。

        Args:
            config: ``CareSurferConfig``（只需 enabled / chat_reply_enabled /
                active_hour_start / active_hour_end / interject_cooldown_seconds）。
            trigger_type: 觸發來源。
            mode: 頻道模式。
            now: 測試用時間注入（秒）。
            room: ``SocialRoom``；用於讀取 cooldown_until。
            npc_consecutive: 目前已連續幾個 NPC turn。
            hourly_speaks: 過去一小時已發言次數（由呼叫端查帳本）。
            daily_speaks: 過去 24 小時已發言次數（由呼叫端查帳本）。
            bypass_budget: 直接回覆人類（熱聊／巡檢接話／沙盒窺屏接話）時設為 True。
                這種情況是「有人在跟她說話」，不該因為她今天已經主動冒泡太多次
                而被靜音；額度只約束定時巡航與心智冒泡。
        """
        ts = time.time() if now is None else float(now)
        trigger = TriggerType(trigger_type) if not isinstance(trigger_type, TriggerType) else trigger_type
        channel_mode = ChannelMode.parse(mode)
        reasons: list[str] = []

        if human_author_is_bot:
            return GateResult.blocked("author_is_bot")

        if channel_mode.is_group and not bool(getattr(self.settings, "SOCIAL_SANDBOX_ENABLED", False)):
            reasons.append("sandbox_disabled")

        if trigger is TriggerType.HUMAN_MESSAGE:
            if not bool(getattr(config, "chat_reply_enabled", True)):
                reasons.append("chat_reply_disabled")
        else:
            if not bool(getattr(config, "enabled", False)):
                reasons.append("surfer_disabled")

        # 一律以台北時區判定活躍時段，避免依賴伺服器本地時區。
        try:
            from datetime import datetime, timedelta, timezone

            hour = datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=8))).hour
        except Exception:
            hour = time.localtime(ts).tm_hour
        if not is_within_active_hours(
            hour,
            int(getattr(config, "active_hour_start", 0)),
            int(getattr(config, "active_hour_end", 23)),
        ):
            reasons.append(f"outside_active_hours(hour={hour})")

        cooldown_until = float(getattr(room, "cooldown_until", 0.0) or 0.0) if room is not None else 0.0
        if cooldown_until > ts:
            remaining = int(cooldown_until - ts)
            reasons.append(f"cooldown_remaining={remaining}s")

        max_consecutive = int(getattr(self.settings, "SOCIAL_NPC_MAX_CONSECUTIVE", 3))
        if npc_consecutive >= max_consecutive:
            reasons.append(f"npc_streak={npc_consecutive}")

        # 發言預算：冷卻只管「別講太快」，預算管「今天別講太多」。
        # 沙盒群像（SANDBOX_GROUP）仍在測試，額度只作用在正式群（SOLO）。
        try:
            hourly_used = max(0, int(hourly_speaks or 0))
        except (TypeError, ValueError):
            hourly_used = 0
        try:
            daily_used = max(0, int(daily_speaks or 0))
        except (TypeError, ValueError):
            daily_used = 0

        if not channel_mode.is_group and not bypass_budget:
            hourly_cap = int(getattr(config, "max_replies_per_hour", 0) or 0)
            if hourly_cap > 0 and hourly_used >= hourly_cap:
                reasons.append(f"hourly_budget={hourly_used}/{hourly_cap}")

            daily_cap = int(getattr(config, "max_replies_per_day", 0) or 0)
            if daily_cap > 0 and daily_used >= daily_cap:
                reasons.append(f"daily_budget={daily_used}/{daily_cap}")

        if reasons:
            return GateResult.blocked(*reasons)
        return GateResult.allowed()

    def mark_cooldown(self, *, config: Any, now: float | None = None, proactive: bool = False) -> float:
        """回傳本次發言後的冷卻截止時間。

        被動回話（有人在跟她說話）走 ``interject_cooldown_seconds``；
        主動插話／冒泡走 ``proactive_cooldown_seconds``，間隔必須更長。
        """
        ts = time.time() if now is None else float(now)
        if proactive:
            seconds = int(getattr(config, "proactive_cooldown_seconds", 0) or 0)
            if seconds <= 0:
                seconds = int(getattr(config, "interject_cooldown_seconds", 20)) * 2
        else:
            seconds = int(getattr(config, "interject_cooldown_seconds", 20))
        return ts + max(0, seconds)
