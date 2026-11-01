from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RoomPhase(str, Enum):
    IDLE = "idle"
    THINKING = "thinking"
    SPEAKING = "speaking"
    LISTENING = "listening"
    COOLDOWN = "cooldown"


class TriggerKind(str, Enum):
    HUMAN = "human"
    PROACTIVE = "proactive"
    RECONCILE = "reconcile"
    MANUAL = "manual"


@dataclass(slots=True, frozen=True)
class ChatEvent:
    """正規化後的人類訊息。raw 只供除錯，不進 prompt。"""

    channel_id: str
    message_id: str
    author_id: str
    author_name: str
    content: str
    created_at: float
    guild_id: str = ""
    is_reply_to_bot: bool = False
    is_mentioned: bool = False
    is_bot: bool = False
    attachment_urls: tuple[str, ...] = ()
    raw: Any = field(default=None, compare=False, repr=False)

    @property
    def dedupe_key(self) -> str:
        return f"{self.channel_id}:{self.message_id}"


@dataclass(slots=True)
class SendTicket:
    """一次回合的發送憑證。begin_turn 建立，commit_send/abort_turn 關閉。"""

    channel_id: str
    turn_id: str
    opened_at: float
    target_author_id: str = ""
    reason: str = ""
    event_ids: tuple[str, ...] = ()
    prev_last_bot_at: float = 0.0
    prev_last_bot_target_id: str = ""
    prev_participants: set[str] = field(default_factory=set)
    prev_humans_since_bot: int = 0
    committed_at: float = 0.0
    message_ids: tuple[str, ...] = ()
    cancelled: bool = False


@dataclass(slots=True)
class SentBatch:
    channel_id: str
    turn_id: str
    first_ts: float
    last_ts: float
    message_ids: tuple[str, ...]
    bubbles: tuple[str, ...]

    @property
    def empty(self) -> bool:
        return not self.message_ids


@dataclass(slots=True)
class TurnDecision:
    speak: bool
    reason: str
    target_author_id: str = ""
    score: float = 0.0
    addressed: bool = False
    continuation: bool = False
    event_ids: tuple[str, ...] = ()
    ambient_count: int = 0
    trigger: str = TriggerKind.HUMAN.value

    @classmethod
    def silent(cls, reason: str, **kwargs: Any) -> "TurnDecision":
        return cls(speak=False, reason=reason, **kwargs)


@dataclass(slots=True)
class RoomSnapshot:
    channel_id: str
    phase: str = RoomPhase.IDLE.value
    window_open: bool = False
    window_remaining: float = 0.0
    followup_open: bool = False
    followup_remaining: float = 0.0
    opened_at: float = 0.0
    last_bot_at: float = 0.0
    last_human_at: float = 0.0
    pending: int = 0
    lurk_streak: int = 0
    turns: int = 0
    last_reason: str = ""
    last_model: str = ""
    last_gen_ms: int = 0
    last_error: str = ""
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel_id": self.channel_id,
            "phase": self.phase,
            "window_open": self.window_open,
            "window_remaining_seconds": round(max(0.0, self.window_remaining), 1),
            "followup_open": self.followup_open,
            "followup_remaining_seconds": round(max(0.0, self.followup_remaining), 1),
            "opened_at": self.opened_at,
            "last_bot_at": self.last_bot_at,
            "last_human_at": self.last_human_at,
            "pending_events": self.pending,
            "lurk_streak": self.lurk_streak,
            "turns_total": self.turns,
            "last_reason": self.last_reason,
            "last_model": self.last_model,
            "last_gen_ms": self.last_gen_ms,
            "last_error": self.last_error,
            "updated_at": self.updated_at or time.time(),
        }
