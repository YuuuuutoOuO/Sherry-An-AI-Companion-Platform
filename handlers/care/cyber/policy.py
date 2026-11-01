from __future__ import annotations

import time
from collections import deque
from pathlib import Path

from .contracts import ChatEvent, TurnDecision
from .ledger import ContactLedger


def _cfg(config: object, name: str, default: object) -> object:
    return getattr(config, name, default)


class ReplyBudget:
    """每頻道每小時/每日回覆額度。

    傳入 ``path`` 時會把實際發送時間寫入跨重啟的 ``ContactLedger``；
    不傳 path 則維持原本的純記憶體行為（測試與臨時 worker 使用）。
    """

    def __init__(
        self,
        *,
        channel_id: str = "",
        path: Path | str | None = None,
    ) -> None:
        self._ledger = ContactLedger(channel_id, path) if path is not None else None
        self._sends: deque[float] = self._ledger.sends if self._ledger else deque()

    def _prune(self, now: float) -> None:
        cutoff = now - 86400.0
        while self._sends and self._sends[0] < cutoff:
            self._sends.popleft()

    def allow(self, *, now: float, hourly: int, daily: int) -> bool:
        self._prune(now)
        if hourly > 0 and sum(1 for ts in self._sends if now - ts < 3600.0) >= hourly:
            return False
        if daily > 0 and len(self._sends) >= daily:
            return False
        return True

    def record(self, now: float) -> None:
        if self._ledger is not None:
            self._ledger.record(now)
        else:
            self._sends.append(float(now))

    def count_today(self, now: float) -> int:
        """回傳最近 24 小時的發送次數（給 WebUI／自主狀態顯示）。"""
        self._prune(now)
        return len(self._sends)


class ReplyPolicy:
    def __init__(self, *, name_tokens: tuple[str, ...] = ("雪莉", "sherry")) -> None:
        self.name_tokens = tuple(token.lower() for token in name_tokens if token)

    def decide_mention(
        self,
        events: list[ChatEvent],
        runtime: object,
        config: object,
        *,
        now: float,
    ) -> TurnDecision:
        if not events:
            return TurnDecision.silent("no_events")
        latest = events[-1]
        return TurnDecision(
            speak=True,
            reason="mention",
            target_author_id=str(latest.author_id),
            score=10.0,
            addressed=True,
            continuation=False,
            event_ids=tuple(event.message_id for event in events),
            ambient_count=len(events),
        )

    def decide_human(
        self,
        events: list[ChatEvent],
        runtime: object,
        config: object,
        *,
        now: float,
        budget: ReplyBudget | None = None,
    ) -> TurnDecision:
        fresh = [event for event in events if not runtime.is_answered(event.message_id)]
        if not fresh:
            return TurnDecision.silent("no_fresh_events")
        if not bool(_cfg(config, "enabled", False)):
            return TurnDecision.silent("disabled")
        if not bool(_cfg(config, "chat_reply_enabled", True)):
            return TurnDecision.silent("chat_reply_disabled")

        if not self._in_active_hours(config, now):
            return TurnDecision.silent("outside_active_hours")

        window_seconds = float(_cfg(config, "active_chat_window_seconds", 180))
        window_open = runtime.window_open(now=now, window_seconds=window_seconds)
        latest = fresh[-1]
        target_id = self._pick_target(fresh, runtime)
        addressed = any(
            event.is_mentioned or event.is_reply_to_bot or self._mentions_name(event.content)
            for event in fresh
        )
        # 熱聊窗口（預設 5 分鐘，可被 WebUI 調短）關閉後，只要對方是在回雪莉
        # 上一句，仍有一段「追話寬限」；這段不需要插話分數，避免一般對話被
        # lurk_streak / interject_score_threshold 卡死。
        followup_seconds = float(_cfg(config, "followup_window_seconds", 900))
        followup_open = bool(
            runtime.in_followup(
                now=now,
                followup_seconds=followup_seconds,
                author_id=str(latest.author_id),
            )
        )
        continuation = followup_open or (
            bool(window_open)
            and (
                not runtime.participants
                or any(str(event.author_id) in runtime.participants for event in fresh[-4:])
                or int(getattr(runtime, "humans_since_bot", 0)) <= 1
            )
        )

        if addressed:
            return TurnDecision(
                speak=True,
                reason="addressed",
                target_author_id=str(target_id),
                score=10.0,
                addressed=True,
                continuation=continuation,
                event_ids=tuple(event.message_id for event in fresh),
                ambient_count=len(fresh),
            )

        if continuation:
            if budget is not None and not budget.allow(
                now=now,
                hourly=int(_cfg(config, "max_replies_per_hour", 8)),
                daily=int(_cfg(config, "max_replies_per_day", 60)),
            ):
                return TurnDecision.silent(
                    "budget_exhausted",
                    score=9.0,
                    continuation=True,
                    ambient_count=len(fresh),
                )
            return TurnDecision(
                speak=True,
                reason="followup" if followup_open and not window_open else "continuation",
                target_author_id=str(target_id),
                score=9.0,
                addressed=False,
                continuation=True,
                event_ids=tuple(event.message_id for event in fresh),
                ambient_count=len(fresh),
            )

        # v2 不再要求「先潛水 N 句」才能接話；連續訊息已由 worker 的
        # debounce 合併。是否開口可選擇用 0~10 分評分，或關閉評分一律接話。
        scoring_enabled = bool(_cfg(config, "reply_scoring_enabled", True))
        score = self.score_ambient(fresh)
        # 雪莉上一句是在對 A 說，B 突然接一句「路過」時降分；
        # B 若真的要找雪莉，會先走 @／名字／引用回覆的 addressed 路徑。
        last_target = str(getattr(runtime, "last_bot_target_id", "") or "")
        if last_target and str(latest.author_id) != last_target:
            score = max(0.0, score - 2.0)
        score_threshold = float(
            _cfg(
                config,
                "ambient_score_threshold",
                _cfg(config, "interject_score_threshold", 6),
            )
        )
        if scoring_enabled and score < score_threshold:
            return TurnDecision.silent(
                "ambient_low_score",
                score=score,
                ambient_count=len(fresh),
                event_ids=tuple(event.message_id for event in fresh),
            )

        cooldown = float(_cfg(config, "interject_cooldown_seconds", 45))
        if cooldown > 0 and runtime.in_cooldown(now) and not addressed:
            return TurnDecision.silent("cooldown", score=score, ambient_count=len(fresh))

        if budget is not None and not budget.allow(
            now=now,
            hourly=int(_cfg(config, "max_replies_per_hour", 8)),
            daily=int(_cfg(config, "max_replies_per_day", 60)),
        ):
            return TurnDecision.silent("budget_exhausted", score=score, ambient_count=len(fresh))

        return TurnDecision(
            speak=True,
            reason="ambient_score" if scoring_enabled else "ambient_always",
            target_author_id=str(target_id),
            score=score,
            addressed=False,
            continuation=False,
            event_ids=tuple(event.message_id for event in fresh),
            ambient_count=len(fresh),
        )

    def decide_proactive(
        self,
        runtime: object,
        config: object,
        *,
        now: float,
        budget: ReplyBudget | None = None,
    ) -> TurnDecision:
        if not bool(_cfg(config, "enabled", False)):
            return TurnDecision.silent("disabled", trigger="proactive")
        if not self._in_active_hours(config, now):
            return TurnDecision.silent("outside_active_hours", trigger="proactive")

        window_seconds = float(_cfg(config, "active_chat_window_seconds", 180))
        if runtime.window_open(now=now, window_seconds=window_seconds):
            return TurnDecision.silent("window_open", trigger="proactive")

        cooldown = float(_cfg(config, "proactive_cooldown_seconds", 120))
        if cooldown > 0 and runtime.in_cooldown(now):
            return TurnDecision.silent("cooldown", trigger="proactive")

        last_activity = max(float(runtime.last_human_at or 0.0), float(runtime.last_bot_at or 0.0))
        if last_activity and (now - last_activity) < cooldown:
            return TurnDecision.silent("recent_activity", trigger="proactive")

        if budget is not None and not budget.allow(
            now=now,
            hourly=int(_cfg(config, "max_replies_per_hour", 8)),
            daily=int(_cfg(config, "max_replies_per_day", 60)),
        ):
            return TurnDecision.silent("budget_exhausted", trigger="proactive")

        return TurnDecision(
            speak=True,
            reason="proactive",
            score=5.0,
            trigger="proactive",
        )

    def _pick_target(self, events: list[ChatEvent], runtime: object) -> str:
        if not events:
            return ""
        for event in reversed(events):
            if event.is_mentioned or event.is_reply_to_bot:
                return str(event.author_id)
        for event in reversed(events):
            if str(event.author_id) in runtime.participants:
                return str(event.author_id)
        return str(events[-1].author_id)

    def score_ambient(self, events: list[ChatEvent]) -> float:
        """0~10 分的「這串訊息值不值得接」分數。

        沙盒頻道的基本分是 5：有人正常打字就會進入可接話區間；
        問句、一次多句、引用雪莉、附件再把分數往上推。
        短到像亂碼／單一表情的訊息才會低於預設門檻 6。
        """
        if not events:
            return 0.0
        latest = events[-1]
        content = (latest.content or "").strip()
        score = 5.0
        if len(content) >= 2:
            score += 1.0
        if "?" in content or "？" in content:
            score += 2.0
        if len(content) >= 12:
            score += 1.0
        if len(events) >= 2:
            score += 1.0
        if any(token in content for token in ("覺得", "為什麼", "怎麼", "嗎", "欸", "笑死", "靠")):
            score += 1.0
        if any(event.is_reply_to_bot for event in events[-3:]):
            score += 1.0
        if latest.attachment_urls:
            score += 1.0
        return min(10.0, score)

    def _mentions_name(self, content: str) -> bool:
        lowered = str(content or "").lower()
        return any(token in lowered for token in self.name_tokens)

    def _in_active_hours(self, config: object, now: float) -> bool:
        from handlers.care.repository import is_within_active_hours

        hour = time.localtime(now).tm_hour
        return is_within_active_hours(
            hour,
            int(_cfg(config, "active_hour_start", 9)),
            int(_cfg(config, "active_hour_end", 23)),
        )
