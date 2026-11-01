"""主動關懷引擎（R19–R20）：候選來源、治理閘門、額度與回饋循環。

本模組負責「要不要主動關心、額度是否允許、留下稽核與回饋統計」；
實際送出訊息的投放迴圈不在本模組內，R20 只提供指令與 WebUI 控制面。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

import settings
from core.autonomy_guard import AutonomyGuard, global_autonomy_guard

from .repository import CareRepository, get_care_repository
from .rollout import CareRollout

logger = logging.getLogger(__name__)

CARE_ACTION_TYPE = "proactive_care"


@dataclass(frozen=True)
class CareCandidate:
    """一個可被評估的主動關懷候選。"""

    user_id: int
    channel_id: int = 0
    guild_id: int = 0
    source: str = "manual"
    source_ref: str = ""
    topic: str = ""
    score: float = 0.0
    topic_id: int | None = None


@dataclass(frozen=True)
class CareDecision:
    """關懷候選的治理判定結果（允許與拒絕都會有 action_id 供稽核）。"""

    candidate: CareCandidate
    allowed: bool
    reason: str
    action_id: str = ""


class CareEngine:
    """可治理、有預算、可退出的主動關懷引擎。"""

    def __init__(
        self,
        repository: CareRepository | None = None,
        guard: AutonomyGuard | None = None,
        relationship_manager: Any | None = None,
        spark_repository: Any | None = None,
        rollout: CareRollout | None = None,
        *,
        weekly_limit: int | None = None,
        daily_limit: int | None = None,
        min_affinity: float | None = None,
        inactive_days: float | None = None,
        adaptive_budget: bool | None = None,
        feedback_window_days: float | None = None,
        min_feedback_samples: int | None = None,
        silence_downgrade_threshold: float | None = None,
        silence_stop_threshold: float | None = None,
    ) -> None:
        self.repository = repository or get_care_repository()
        self._guard = guard or global_autonomy_guard
        self.relationship_manager = relationship_manager
        self.spark_repository = spark_repository
        # 直接建構 CareEngine 時預設不啟用灰度（方便單元測試與離線評估）；
        # production 入口（get_care_engine／setup）會注入 CareRollout。
        self.rollout = rollout
        self.weekly_limit = (
            int(getattr(settings, "CARE_WEEKLY_LIMIT_PER_USER", 2))
            if weekly_limit is None
            else int(weekly_limit)
        )
        self.daily_limit = (
            int(getattr(settings, "CARE_DAILY_LIMIT_GLOBAL", 20))
            if daily_limit is None
            else int(daily_limit)
        )
        self.min_affinity = (
            float(getattr(settings, "CARE_MIN_AFFINITY", 60.0))
            if min_affinity is None
            else float(min_affinity)
        )
        self.inactive_days = (
            float(getattr(settings, "CARE_INACTIVE_DAYS", 14.0))
            if inactive_days is None
            else float(inactive_days)
        )
        self.adaptive_budget = (
            bool(getattr(settings, "CARE_ADAPTIVE_BUDGET_ENABLED", True))
            if adaptive_budget is None
            else bool(adaptive_budget)
        )
        self.feedback_window_days = (
            float(getattr(settings, "CARE_FEEDBACK_WINDOW_DAYS", 30.0))
            if feedback_window_days is None
            else float(feedback_window_days)
        )
        self.min_feedback_samples = (
            int(getattr(settings, "CARE_MIN_FEEDBACK_SAMPLES", 3))
            if min_feedback_samples is None
            else int(min_feedback_samples)
        )
        self.silence_downgrade_threshold = (
            float(getattr(settings, "CARE_SILENCE_DOWNGRADE_THRESHOLD", 0.6))
            if silence_downgrade_threshold is None
            else float(silence_downgrade_threshold)
        )
        self.silence_stop_threshold = (
            float(getattr(settings, "CARE_SILENCE_STOP_THRESHOLD", 0.85))
            if silence_stop_threshold is None
            else float(silence_stop_threshold)
        )
        self.install_guard(self._guard)

    @property
    def guard(self) -> AutonomyGuard:
        return self._guard

    def install_guard(self, guard: AutonomyGuard) -> None:
        """把 opt-in 同意閘門註冊進 AutonomyGuard（所有 care 檢查必經）。"""
        self._guard = guard
        guard.register_gate("care_consent", self.consent_gate)

    def consent_gate(
        self, action_type: str, params: dict[str, object]
    ) -> tuple[bool, str]:
        """AutonomyGuard 的同意閘門；非關懷行動一律放行。"""
        if action_type != CARE_ACTION_TYPE:
            return True, ""
        raw_user_id = params.get("user_id")
        if raw_user_id in (None, ""):
            return False, "主動關懷缺少 user_id，拒絕執行"
        guild_id = params.get("guild_id", "")
        try:
            opted_in = self.repository.is_opted_in(raw_user_id, guild_id)  # type: ignore[arg-type]
        except Exception as exc:  # pragma: no cover - DB 故障時 fail-closed
            logger.error("[Care] 讀取 opt-in 狀態失敗，拒絕主動關懷: %s", exc)
            return False, "無法確認使用者 opt-in 狀態，拒絕主動關懷"
        if not opted_in:
            return False, f"使用者 {raw_user_id} 未 opt-in 主動關懷"
        return True, "使用者已 opt-in"

    # ------------------------------------------------------------------
    # 候選來源
    # ------------------------------------------------------------------
    def relationship_candidates(self, now: float | None = None) -> list[CareCandidate]:
        manager = self.relationship_manager
        store = getattr(manager, "store", None)
        if not isinstance(store, dict):
            return []
        current = time_now(now)
        cutoff = current - self.inactive_days * 86400.0
        candidates: list[CareCandidate] = []
        for raw_uid, data in store.items():
            if not isinstance(data, dict):
                continue
            try:
                user_id = int(raw_uid)
            except (TypeError, ValueError):
                continue
            affinity = _to_float(data.get("affinity"))
            if affinity < self.min_affinity:
                continue
            if not _is_inactive(data.get("last_interaction"), cutoff):
                continue
            candidates.append(
                CareCandidate(
                    user_id=user_id,
                    source="relationship",
                    source_ref=str(user_id),
                    topic="好久沒聊聊了，想關心一下近況",
                    score=affinity,
                )
            )
        return candidates

    def spark_candidates(self, now: float | None = None) -> list[CareCandidate]:
        repository = self.spark_repository
        list_all = getattr(repository, "list_all", None)
        if not callable(list_all):
            return []
        candidates: list[CareCandidate] = []
        for trigger in list_all():
            if str(getattr(trigger, "trigger_type", "")) != "schedule":
                continue
            if not bool(getattr(trigger, "is_active", True)):
                continue
            condition = getattr(trigger, "condition", {}) or {}
            extra = getattr(trigger, "extra_data", {}) or {}
            action = getattr(trigger, "action", {}) or {}
            is_care = bool(
                condition.get("care")
                or extra.get("care")
                or str(action.get("target", "")).upper() == "CARE"
            )
            if not is_care:
                continue
            topic = (
                str(getattr(trigger, "message", "") or "").strip()
                or str(condition.get("care_topic") or "").strip()
                or "SPARK 定時關懷"
            )
            try:
                score = float(extra.get("care_score", 50.0))
            except (TypeError, ValueError):
                score = 50.0
            candidates.append(
                CareCandidate(
                    user_id=_to_int(getattr(trigger, "user_id", 0)),
                    channel_id=_to_int(getattr(trigger, "channel_id", 0)),
                    guild_id=_to_int(extra.get("guild_id")),
                    source="spark",
                    source_ref=str(getattr(trigger, "trigger_id", "")),
                    topic=topic,
                    score=score,
                )
            )
        return candidates

    def nightly_candidates(self, now: float | None = None) -> list[CareCandidate]:
        candidates: list[CareCandidate] = []
        for row in self.repository.list_pending_topics():
            try:
                user_id = int(row["user_id"])
            except (TypeError, ValueError):
                continue
            candidates.append(
                CareCandidate(
                    user_id=user_id,
                    guild_id=_to_int(row.get("guild_id")),
                    channel_id=_to_int(row.get("channel_id")),
                    source="nightly",
                    source_ref=str(row.get("source_ref") or row.get("topic_id")),
                    topic=str(row.get("topic") or ""),
                    score=_to_float(row.get("score")),
                    topic_id=int(row["topic_id"]),
                )
            )
        return candidates

    def candidates(self, now: float | None = None) -> list[CareCandidate]:
        """合併三軌來源並以 (user, guild, topic) 去重，高分優先。"""
        merged: dict[tuple[int, int, str], CareCandidate] = {}
        for candidate in (
            self.relationship_candidates(now)
            + self.spark_candidates(now)
            + self.nightly_candidates(now)
        ):
            if candidate.user_id <= 0 or not candidate.topic.strip():
                continue
            key = (candidate.user_id, candidate.guild_id, candidate.topic.strip().lower())
            existing = merged.get(key)
            if existing is None or candidate.score > existing.score:
                merged[key] = candidate
        return sorted(merged.values(), key=lambda item: item.score, reverse=True)

    # ------------------------------------------------------------------
    # 治理與額度
    # ------------------------------------------------------------------
    def evaluate(self, candidate: CareCandidate, now: float | None = None) -> CareDecision:
        """依序通過同意閘門、全域治理與關懷額度，並留下稽核。"""
        if candidate.user_id <= 0:
            action_id = self._audit(candidate, "rejected", "無效的 user_id", now)
            return CareDecision(candidate, False, "無效的 user_id", action_id)

        try:
            decision = self._guard.check(
                CARE_ACTION_TYPE,
                params={
                    "user_id": candidate.user_id,
                    "guild_id": candidate.guild_id,
                    "channel_id": candidate.channel_id,
                    "source": candidate.source,
                },
                description=f"主動關懷 {candidate.user_id}: {candidate.topic}",
            )
        except Exception as exc:
            logger.error("[Care] AutonomyGuard 檢查失敗，fail-closed: %s", exc)
            action_id = self._audit(candidate, "rejected", "治理閘門例外，拒絕", now)
            return CareDecision(candidate, False, "治理閘門例外，拒絕", action_id)

        if not decision.allowed:
            action_id = self._audit(candidate, "rejected", decision.reason, now)
            return CareDecision(candidate, False, decision.reason, action_id)

        # 防禦縱深：即使呼叫端傳入未註冊 care_consent 的 guard，仍再檢查一次。
        if not self.repository.is_opted_in(candidate.user_id, candidate.guild_id):
            reason = f"使用者 {candidate.user_id} 未 opt-in 主動關懷"
            action_id = self._audit(candidate, "rejected", reason, now)
            return CareDecision(candidate, False, reason, action_id)

        if self.rollout is not None:
            rollout_decision = self.rollout.check(
                candidate.guild_id, candidate.user_id
            )
            if not rollout_decision.allowed:
                action_id = self._audit(
                    candidate, "rejected", rollout_decision.reason, now
                )
                return CareDecision(
                    candidate, False, rollout_decision.reason, action_id
                )

        effective_limit, limit_reason = self.effective_weekly_limit(
            candidate.user_id, now=now
        )
        if effective_limit <= 0 and limit_reason:
            action_id = self._audit(candidate, "rejected", limit_reason, now)
            return CareDecision(candidate, False, limit_reason, action_id)

        budget = self.repository.consume_budget(
            candidate.user_id,
            weekly_limit=effective_limit,
            daily_limit=self.daily_limit,
            now=now,
        )
        if not budget.allowed:
            action_id = self._audit(candidate, "rejected", budget.reason, now)
            return CareDecision(candidate, False, budget.reason, action_id)

        action_id = self._audit(candidate, "approved", budget.reason, now)
        if candidate.source == "nightly" and candidate.topic_id is not None:
            self.repository.mark_topic_consumed(candidate.topic_id, now=now)
        logger.info(
            "[Care] 核准主動關懷 user=%s source=%s topic=%s (%s/%s)",
            candidate.user_id,
            candidate.source,
            candidate.topic,
            budget.user_used,
            budget.user_limit,
        )
        return CareDecision(candidate, True, budget.reason, action_id)

    def effective_weekly_limit(
        self, user_id: int | str, now: float | None = None
    ) -> tuple[int, str]:
        """依近期沉默率動態調整每人每週上限。

        樣本不足時維持基準值；沉默率過高先降 1 條，超過停止門檻則本週
        暫停（回傳 0 與可稽核原因）。停用自適應時永遠回傳基準值。
        """
        base = max(0, int(self.weekly_limit))
        if base <= 0 or not self.adaptive_budget:
            return base, ""
        ts = time_now(now)
        stats = self.repository.feedback_stats(
            user_id=user_id,
            since=ts - max(0.0, self.feedback_window_days) * 86400.0,
        )
        if stats.resolved < max(1, int(self.min_feedback_samples)):
            return base, ""
        if stats.silence_rate >= self.silence_stop_threshold:
            return 0, (
                f"近期主動關懷沉默率 {stats.silence_rate:.0%} 過高"
                f"（{stats.silent}/{stats.resolved}），本週暫停"
            )
        if stats.silence_rate >= self.silence_downgrade_threshold:
            return max(0, base - 1), ""
        return base, ""

    def preference_status(
        self, user_id: int | str, guild_id: int | str = ""
    ) -> dict[str, Any]:
        """供指令與 WebUI 顯示的單一使用者狀態。"""
        preference = self.repository.get_preference(user_id, guild_id)
        rollout = (
            self.rollout.check(guild_id, user_id)
            if self.rollout is not None
            else None
        )
        limit, limit_reason = self.effective_weekly_limit(user_id)
        return {
            "user_id": str(user_id),
            "guild_id": "" if guild_id in (None, 0) else str(guild_id),
            "opt_in": preference.opt_in,
            "frequency": preference.frequency,
            "guild_enabled": rollout.allowed if rollout is not None else True,
            "rollout_percent": (
                rollout.rollout_percent if rollout is not None else 100
            ),
            "rollout_reason": rollout.reason if rollout is not None else "",
            "effective_weekly_limit": limit,
            "limit_reason": limit_reason,
        }

    def feedback_report(
        self,
        *,
        days: float | None = None,
        guild_id: int | str | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        """沉默率／回覆率報表；供 /care report 與 WebUI 查詢。"""
        window = self.feedback_window_days if days is None else float(days)
        ts = time_now(now)
        since = ts - max(0.0, window) * 86400.0
        gid = None if guild_id is None else str(guild_id)
        overall = self.repository.feedback_stats(guild_id=gid, since=since)
        per_guild = (
            self.repository.feedback_by_guild(since=since)
            if gid is None
            else []
        )
        return {
            "window_days": window,
            "generated_at": ts,
            "guild_id": gid or "",
            "overall": overall.to_dict(),
            "by_guild": per_guild,
            "opt_in_users": self.repository.count_opted_in(),
            "pending_topics": self.repository.count_pending_topics(),
            "budget": self.repository.budget_snapshot(limit=50),
            "limits": {
                "weekly_per_user": self.weekly_limit,
                "daily_global": self.daily_limit,
                "adaptive_enabled": self.adaptive_budget,
                "feedback_window_days": self.feedback_window_days,
                "min_feedback_samples": self.min_feedback_samples,
                "silence_downgrade_threshold": self.silence_downgrade_threshold,
                "silence_stop_threshold": self.silence_stop_threshold,
            },
        }

    def settle_silence(
        self, *, hours: float | None = None, now: float | None = None
    ) -> int:
        """把逾時未回應的核准行動結案為 silent；回傳受影響筆數。"""
        window = (
            float(getattr(settings, "CARE_SILENCE_SETTLE_HOURS", 48.0))
            if hours is None
            else float(hours)
        )
        ts = time_now(now)
        return self.repository.settle_stale_actions(
            before=ts - max(0.0, window) * 3600.0, now=ts
        )

    def remember_nightly_topics(
        self,
        user_id: int | str,
        topics: Iterable[Any],
        *,
        source_ref: str = "",
        guild_id: int | str = "",
        channel_id: int | str = "",
    ) -> int:
        """深夜整合寫入待回訪話題；回傳實際新增筆數。"""
        added = 0
        for topic in topics:
            text = str(topic or "").strip()
            if not text:
                continue
            topic_id = self.repository.add_topic(
                user_id=user_id,
                guild_id=guild_id,
                channel_id=channel_id,
                topic=text,
                source="nightly",
                source_ref=source_ref,
            )
            if topic_id is not None:
                added += 1
        return added

    def record_response(self, action_id: str, *, responded: bool) -> bool:
        """R20 回饋循環用：記錄主動訊息是否有回應。"""
        status = "responded" if responded else "silent"
        return self.repository.update_action(action_id, status=status)

    def _audit(
        self,
        candidate: CareCandidate,
        status: str,
        reason: str,
        now: float | None = None,
    ) -> str:
        return self.repository.record_action(
            user_id=candidate.user_id,
            guild_id=candidate.guild_id,
            channel_id=candidate.channel_id,
            source=candidate.source,
            source_ref=candidate.source_ref,
            topic=candidate.topic,
            status=status,
            reason=reason,
            now=now,
        )


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _is_inactive(raw: Any, cutoff: float) -> bool:
    if raw in (None, ""):
        return True
    if isinstance(raw, (int, float)):
        return float(raw) <= cutoff
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp() <= cutoff


def time_now(now: float | None = None) -> float:
    import time

    return time.time() if now is None else float(now)
