from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


class TurnMode(str, Enum):
    MENTION = "mention"
    REPLY = "reply"
    AMBIENT = "ambient"
    FOLLOWUP = "followup"
    PROACTIVE = "proactive"


class Lane(str, Enum):
    PASSIVE = "passive"
    AUTONOMOUS = "autonomous"


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    backend: Literal["dispatch", "readonly", "none"] = "none"
    allowed: frozenset[str] = frozenset()
    max_calls: int = 0
    allow_direct_ui: bool = False


NO_TOOL_POLICY = ToolPolicy()

FULL_MENTION_POLICY = ToolPolicy(
    backend="dispatch",
    allowed=frozenset({
        "SUMMARY", "MUSIC", "SCHEDULE", "DRAW", "PIXIV",
        "STOCK", "SYSTEM_INSPECT", "SEARCH", "CHAT",
    }),
    max_calls=4,
    allow_direct_ui=True,
)

AUTONOMOUS_POLICY = ToolPolicy(
    backend="readonly",
    allowed=frozenset({"SEARCH", "STOCK", "SUMMARY"}),
    max_calls=2,
    allow_direct_ui=False,
)


@dataclass(slots=True)
class TurnRequest:
    channel_id: str
    mode: TurnMode
    events: tuple[Any, ...] = ()
    guild_id: str = ""
    target_author_id: str = ""
    target_author_name: str = ""
    message: Any = None
    reply_to: Any = None
    priority: int = 2
    must_reply: bool = False
    tool_policy: ToolPolicy = NO_TOOL_POLICY
    trace_id: str = ""
    source: str = ""

    @property
    def lane(self) -> Lane:
        return (
            Lane.PASSIVE
            if self.mode in (TurnMode.MENTION, TurnMode.REPLY)
            else Lane.AUTONOMOUS
        )


@dataclass(slots=True)
class TurnResult:
    status: Literal["sent", "silent", "blocked", "failed", "shadow"]
    reason: str = ""
    bubbles: tuple[str, ...] = ()
    message_ids: tuple[str, ...] = ()
    model: str = ""
    latency_ms: int = 0
    tool_calls: int = 0
    memory_sources: tuple[str, ...] = ()
    preempted: bool = False
