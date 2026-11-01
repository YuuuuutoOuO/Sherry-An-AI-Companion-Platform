"""mod/spark/checkers/schedule_checker.py - 定時排程觸發策略。

純時間計算邏輯：支援間隔排程 (interval) 與每日特定時間 (daily_time)。
零外部 I/O。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import settings

from ..models import Trigger, TriggerResult

logger = logging.getLogger(__name__)


class ScheduleChecker:
    """定時排程檢查器：支援時間間隔、每日特定時間與單次時間點觸發。"""

    trigger_type: str = "schedule"

    async def should_fire(
        self,
        trigger: Trigger,
        now_dt: datetime | None = None,
    ) -> TriggerResult:
        """檢查排程條件是否已達觸發時間點。

        Args:
            trigger: 欲評估的 Trigger 實例。
            now_dt: 當前時間點 (選填，單元測試注入用)。

        Returns:
            TriggerResult: 判定成果與說明。
        """
        cond = trigger.condition
        mode = str(cond.get("mode", "interval")).lower().strip()
        now = now_dt if now_dt is not None else datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        # 解析上次觸發時間
        last_dt: datetime | None = None
        if trigger.last_triggered_at:
            try:
                last_dt = datetime.fromisoformat(trigger.last_triggered_at)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
            except Exception:
                last_dt = None

        if mode in ("interval", "periodic"):
            res = self._check_interval(cond, now, last_dt)
        elif mode in ("daily_time", "fixed_time", "daily"):
            res = self._check_daily_time(cond, now, last_dt)
        elif mode in ("once", "one_shot", "timestamp"):
            res = self._check_once(cond, now)
        else:
            return TriggerResult(
                should_fire=False,
                current_value=f"未知的排程模式: {mode}",
            )

        # 檢查 max_runs (若已達上限則不再觸發；若本次為最後一次則標記 one_shot_fired=True 以利 auto-pause)
        max_runs = cond.get("max_runs")
        if max_runs is not None:
            try:
                max_runs_val = int(max_runs)
                current_runs = int(trigger.extra_data.get("run_count", 0)) if isinstance(trigger.extra_data, dict) else 0
                if current_runs >= max_runs_val:
                    return TriggerResult(
                        should_fire=False,
                        current_value=f"已達執行次數上限 ({current_runs}/{max_runs_val})",
                    )
                if res.should_fire and current_runs + 1 >= max_runs_val:
                    detail = dict(res.detail_data)
                    detail["one_shot_fired"] = True
                    return TriggerResult(
                        should_fire=True,
                        current_value=f"{res.current_value}（已達設定上限 {max_runs_val} 次）",
                        detail_data=detail,
                    )
            except (ValueError, TypeError):
                pass

        return res

    def _check_once(
        self,
        cond: dict[str, Any],
        now: datetime,
    ) -> TriggerResult:
        """檢查單次時間點模式 (once / one_shot)。

        達到指定目標時間即刻觸發，並自動標記 one_shot_fired=True 以由 Service 自動停用結案。
        """
        # 1. 快速路徑：優先使用已快取的 target_ts (O(1) 效能最佳化)
        if "_cached_ts" in cond:
            target_ts: float | None = float(cond["_cached_ts"])
        else:
            target_val = cond.get("target_time") or cond.get("trigger_time") or cond.get("time")
            target_ts = None

            if isinstance(target_val, (int, float)):
                target_ts = float(target_val)
            elif isinstance(target_val, str) and target_val.strip():
                s = target_val.strip()
                try:
                    target_ts = float(s)
                except ValueError:
                    try:
                        s_iso = s.replace(" ", "T") if (" " in s and "T" not in s) else s
                        dt = datetime.fromisoformat(s_iso)
                        if dt.tzinfo is None:
                            tz_offset = int(cond.get("tz_offset", 8))
                            dt = dt.replace(tzinfo=timezone(timedelta(hours=tz_offset)))
                        target_ts = dt.timestamp()
                    except Exception as parse_err:
                        logger.warning("ScheduleChecker: 解析 once 目標時間異常 (%s): %s", s, parse_err)
                        return TriggerResult(should_fire=False, current_value=f"時間格式解析失敗: {s}")

            if target_ts is not None:
                cond["_cached_ts"] = target_ts

        if target_ts is None:
            return TriggerResult(should_fire=False, current_value="缺少有效的單次目標時間 (target_time)")

        now_ts = now.timestamp()
        if now_ts >= target_ts:
            return TriggerResult(
                should_fire=True,
                current_value="單次排程已到達預定時間點",
                detail_data={
                    "mode": "once",
                    "target_timestamp": target_ts,
                    "one_shot_fired": True,
                },
            )

        remaining_sec = max(0, int(target_ts - now_ts))
        return TriggerResult(
            should_fire=False,
            current_value=f"尚未到達單次排程時間（剩餘約 {remaining_sec} 秒）",
            detail_data={"mode": "once", "remaining_seconds": remaining_sec},
        )

    def _check_interval(
        self,
        cond: dict[str, Any],
        now: datetime,
        last_dt: datetime | None,
    ) -> TriggerResult:
        """檢查固定間隔模式。"""
        # 檢查星期篩選 (0=週一, 6=週日)
        tz_offset_hours = int(cond.get("tz_offset", 8))
        target_tz = timezone(timedelta(hours=tz_offset_hours))
        local_now = now.astimezone(target_tz)
        local_weekday = local_now.weekday()
        allowed_days = cond.get("days_of_week")
        if isinstance(allowed_days, list) and len(allowed_days) > 0:
            if local_weekday not in allowed_days:
                return TriggerResult(
                    should_fire=False,
                    current_value=f"今天非指定排程星期 (今日: {local_weekday})",
                )

        try:
            interval_minutes = int(cond.get("interval_minutes", 60))
        except (ValueError, TypeError):
            interval_minutes = 60

        if interval_minutes <= 0:
            interval_minutes = 1

        if last_dt is None:
            # 尚未觸發過，首次判定為觸發
            return TriggerResult(
                should_fire=True,
                current_value=f"固定排程首次啟動（每 {interval_minutes} 分鐘）",
                detail_data={"mode": "interval", "interval_minutes": interval_minutes},
            )

        elapsed_seconds = (now - last_dt).total_seconds()
        target_seconds = interval_minutes * 60

        if elapsed_seconds >= target_seconds:
            return TriggerResult(
                should_fire=True,
                current_value=f"固定間隔排程已達時間（每 {interval_minutes} 分鐘）",
                detail_data={"mode": "interval", "interval_minutes": interval_minutes},
            )

        remaining_min = max(0, int((target_seconds - elapsed_seconds) / 60))
        return TriggerResult(
            should_fire=False,
            current_value=f"排程冷卻中，預計 {remaining_min} 分鐘後再次觸發",
            detail_data={"mode": "interval", "remaining_minutes": remaining_min},
        )

    def _check_daily_time(
        self,
        cond: dict[str, Any],
        now: datetime,
        last_dt: datetime | None,
    ) -> TriggerResult:
        """檢查每日指定時間 (例如 "08:30")，並支援當日補跑窗。

        補跑政策：目標分鐘過後 ``catchup_minutes``（預設
        ``settings.SPARK_DAILY_CATCHUP_MINUTES``，0 表示關閉）內，若今日尚未
        觸發過，仍允許補跑一次；跨日不回溯，避免與隔日排程重疊。
        """
        target_time_str = str(cond.get("time", "08:00")).strip()
        parts = target_time_str.split(":")
        if len(parts) != 2:
            return TriggerResult(should_fire=False, current_value="時間格式錯誤，需為 HH:MM")

        try:
            target_hour = int(parts[0])
            target_minute = int(parts[1])
        except ValueError:
            return TriggerResult(should_fire=False, current_value="時間數字格式無效")

        if not (0 <= target_hour <= 23 and 0 <= target_minute <= 59):
            return TriggerResult(should_fire=False, current_value="時間格式錯誤，需為 HH:MM")

        # 考慮時區位移 (預設台灣/香港時間 UTC+8)
        tz_offset_hours = int(cond.get("tz_offset", 8))
        target_tz = timezone(timedelta(hours=tz_offset_hours))
        local_now = now.astimezone(target_tz)
        local_hour = local_now.hour
        local_minute = local_now.minute
        local_weekday = local_now.weekday()

        # 檢查星期篩選 (0=週一, 6=週日)
        allowed_days = cond.get("days_of_week")
        if isinstance(allowed_days, list) and len(allowed_days) > 0:
            if local_weekday not in allowed_days:
                return TriggerResult(
                    should_fire=False,
                    current_value=f"今天非指定排程星期 (今日: {local_weekday})",
                )

        target_today = local_now.replace(
            hour=target_hour, minute=target_minute, second=0, microsecond=0
        )
        delta_seconds = (local_now - target_today).total_seconds()
        late_minutes = max(0, int(delta_seconds // 60))

        try:
            catchup_minutes = max(
                0,
                int(
                    cond.get(
                        "catchup_minutes",
                        getattr(settings, "SPARK_DAILY_CATCHUP_MINUTES", 120),
                    )
                ),
            )
        except (TypeError, ValueError):
            catchup_minutes = 120

        # 今日已觸發過就不重複（含補跑），避免同日反覆推播。
        if last_dt is not None:
            local_last = last_dt.astimezone(target_tz)
            if local_last.date() == local_now.date():
                return TriggerResult(
                    should_fire=False,
                    current_value=(
                        f"今日排程已於 {local_last.strftime('%H:%M')} 完成觸發"
                    ),
                    detail_data={"mode": "daily_time", "time": target_time_str},
                )

        if delta_seconds < 0:
            return TriggerResult(
                should_fire=False,
                current_value=(
                    f"未到排程時間（當前 {local_hour:02d}:{local_minute:02d}，"
                    f"目標 {target_time_str}）"
                ),
            )

        if late_minutes <= catchup_minutes:
            is_catchup = late_minutes > 0
            if is_catchup:
                summary = (
                    f"每日定時推播補跑（目標 {target_time_str}，"
                    f"延遲 {late_minutes} 分鐘）"
                )
            else:
                summary = f"每日定時推播觸發（目標時間 {target_time_str}）"
            return TriggerResult(
                should_fire=True,
                current_value=summary,
                detail_data={
                    "mode": "daily_time",
                    "time": target_time_str,
                    "catchup": is_catchup,
                    "late_minutes": late_minutes,
                },
            )

        return TriggerResult(
            should_fire=False,
            current_value=(
                f"已錯過今日排程（目標 {target_time_str}，"
                f"補跑窗 {catchup_minutes} 分鐘已過）"
            ),
            detail_data={"mode": "daily_time", "time": target_time_str},
        )
