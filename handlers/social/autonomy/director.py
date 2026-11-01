"""handlers/social/autonomy/director.py
====================================
SocialDirector：四條發話路徑的共同入口。

責任很窄：
1. 解析頻道模式（SOLO / SANDBOX_GROUP）。
2. 呼叫 SpeechGate 決定「要不要說話」。
3. **不論結果**都寫一筆 social_decisions。
4. 回傳決策給呼叫端；真正的生成與傳輸仍由 action / speaker 負責。

這樣「她為什麼沉默」永遠有答案，而不是只剩 log。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import settings as default_settings

from .contracts import (
    ChannelMode,
    DecisionAction,
    GateResult,
    Persona,
    SocialEvent,
    TriggerType,
    looks_like_canned_fallback,
)
from .gate import SpeechGate
from .modes import ChannelModeResolver, ModeDecision
from .personas import PersonaRoster

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DirectorDecision:
    """一次完整決策的結果。"""

    event: SocialEvent
    gate: GateResult
    mode: ModeDecision
    action: DecisionAction
    decision_id: int = 0

    @property
    def allowed(self) -> bool:
        return self.gate.allow

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event.to_dict(),
            "gate": self.gate.to_dict(),
            "mode": self.mode.to_dict(),
            "action": self.action.value,
            "decision_id": self.decision_id,
        }


class SocialDirector:
    """統一決策與落帳。"""

    def __init__(
        self,
        repository: Any,
        settings_module: Any | None = None,
        *,
        gate: SpeechGate | None = None,
        resolver: ChannelModeResolver | None = None,
        roster: PersonaRoster | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings_module or default_settings
        self.gate = gate or SpeechGate(self.settings)
        self.resolver = resolver or ChannelModeResolver(repository, self.settings)
        self.roster = roster or PersonaRoster(repository, self.settings)

    # ------------------------------------------------------------------
    # 模式與身分
    # ------------------------------------------------------------------
    def mode_for(self, channel_id: int | str, *, guild_id: str | int = "", config: Any | None = None) -> ModeDecision:
        cfg = config if config is not None else self.repository.get_surfer_config()
        return self.resolver.resolve(channel_id, guild_id=guild_id, config=cfg)

    def primary_persona(self, channel_id: int | str = 0, guild_id: str | int = "") -> Persona:
        return self.roster.ensure_primary(channel_id, guild_id)

    # ------------------------------------------------------------------
    # 決策
    # ------------------------------------------------------------------
    def decide(
        self,
        event: SocialEvent,
        config: Any | None = None,
        *,
        room: Any | None = None,
        ai_consecutive: int = 0,
        persona_key: str = "",
        author_is_bot: bool = False,
        ignore_cooldown: bool = False,
        bypass_budget: bool = False,
        hourly_speaks: int | None = None,
        daily_speaks: int | None = None,
    ) -> DirectorDecision:
        """評估事件並在 SILENT 時立即落帳。

        Args:
            ignore_cooldown: 直接回覆人類（熱聊）時不受插話冷卻限制。
            bypass_budget: 直接回覆人類時不受每小時／每日主動冒泡額度限制。
            hourly_speaks: 過去一小時發言數；``None`` 時自動查帳本。
            daily_speaks: 過去 24 小時發言數；``None`` 時自動查帳本。
        """
        cfg = config if config is not None else self.repository.get_surfer_config()
        if not bypass_budget and (hourly_speaks is None or daily_speaks is None):
            snapshot = self.budget_snapshot(event.channel_id)
            if hourly_speaks is None:
                hourly_speaks = snapshot["hourly"]
            if daily_speaks is None:
                daily_speaks = snapshot["daily"]
        mode = self.mode_for(event.channel_id, guild_id=event.guild_id, config=cfg)
        gate = self.gate.evaluate(
            config=cfg,
            trigger_type=event.trigger_type,
            mode=mode.mode,
            room=None if ignore_cooldown else room,
            npc_consecutive=ai_consecutive,
            human_author_is_bot=author_is_bot,
            hourly_speaks=hourly_speaks or 0,
            daily_speaks=daily_speaks or 0,
            bypass_budget=bypass_budget,
        )

        action = DecisionAction.SPEAK if gate.allow else DecisionAction.SILENT
        decision_id = 0
        if not gate.allow:
            reasons = list(gate.reasons) or ["blocked"]
            if mode.mode is ChannelMode.SOLO and mode.reason != "default_solo":
                # 沙盒降級的原因也要留痕，否則 UI 只會看到「沉默」。
                reasons.append(f"mode:{mode.reason}")
            decision_id = self.record_silent(
                event,
                mode=mode.mode,
                persona_key=persona_key or event.persona_key,
                reasons=reasons,
                gate=gate,
                score=gate.score,
            )
        return DirectorDecision(
            event=event,
            gate=gate,
            mode=mode,
            action=action,
            decision_id=decision_id,
        )

    # ------------------------------------------------------------------
    # 落帳
    # ------------------------------------------------------------------
    def record_silent(
        self,
        event: SocialEvent,
        *,
        mode: ChannelMode,
        reasons: list[str] | tuple[str, ...],
        persona_key: str = "",
        gate: GateResult | None = None,
        score: float = 0.0,
        trigger_ref: str = "",
    ) -> int:
        return self.repository.record_decision(
            channel_id=event.channel_id,
            guild_id=event.guild_id,
            scene_id=event.scene_id,
            mode=mode.value,
            trigger_type=event.trigger_type.value,
            trigger_ref=trigger_ref or event.message_id,
            persona_key=persona_key,
            action=DecisionAction.SILENT.value,
            score=score,
            reasons=list(reasons),
            gate=gate.to_dict() if gate is not None else {"allow": False},
            now=time.time(),
        )

    def record_speak(
        self,
        event: SocialEvent,
        *,
        mode: ChannelMode,
        action: DecisionAction = DecisionAction.SPEAK,
        persona_key: str = "",
        score: float = 0.0,
        reasons: list[str] | tuple[str, ...] | None = None,
        gate: GateResult | None = None,
        latency_ms: int = 0,
        model: str = "",
        tokens: int = 0,
        scene_id: int | None = None,
        text: str = "",
    ) -> int:
        """記錄一次實際發言；文字為罐頭或空白時改記 SILENT。"""
        if not str(text or "").strip() or looks_like_canned_fallback(text):
            return self.record_silent(
                event,
                mode=mode,
                persona_key=persona_key,
                reasons=["generation_failed"] if not str(text or "").strip() else ["canned_fallback"],
                gate=gate,
                score=score,
            )
        return self.repository.record_decision(
            channel_id=event.channel_id,
            guild_id=event.guild_id,
            scene_id=scene_id if scene_id is not None else event.scene_id,
            mode=mode.value,
            trigger_type=event.trigger_type.value,
            trigger_ref=event.message_id,
            persona_key=persona_key,
            action=action.value,
            score=score,
            reasons=list(reasons or []),
            gate=gate.to_dict() if gate is not None else {"allow": True},
            latency_ms=latency_ms,
            model=model,
            tokens=tokens,
            now=time.time(),
        )

    # ------------------------------------------------------------------
    # 房間狀態
    # ------------------------------------------------------------------
    def observe_human(self, event: SocialEvent) -> None:
        """人類發言：更新房間狀態並作廢待送 AI turn（由呼叫端取消 scheduler）。"""
        self.repository.touch_room(
            event.channel_id,
            role="human",
            guild_id=event.guild_id,
            now=event.created_at or time.time(),
        )

    def observe_ai(self, event: SocialEvent, *, role: str, persona_key: str) -> None:
        self.repository.touch_room(
            event.channel_id,
            role=role,
            persona_key=persona_key,
            guild_id=event.guild_id,
            now=time.time(),
        )

    def apply_cooldown(self, channel_id: int | str, config: Any, *, proactive: bool = False) -> None:
        until = self.gate.mark_cooldown(config=config, proactive=proactive)
        self.repository.upsert_room(channel_id, cooldown_until=until)

    def budget_snapshot(self, channel_id: int | str, now: float | None = None) -> dict[str, int]:
        """回傳該頻道目前的發言額度使用量（給閘門與 WebUI 共用）。"""
        ts = time.time() if now is None else float(now)
        counter = getattr(self.repository, "count_speaks", None)
        if counter is None:  # 舊版 repository 或測試替身
            return {"hourly": 0, "daily": 0}

        def _count(since: float) -> int:
            try:
                return max(0, int(counter(channel_id, since=since) or 0))
            except Exception:
                return 0

        return {"hourly": _count(ts - 3600.0), "daily": _count(ts - 86400.0)}
