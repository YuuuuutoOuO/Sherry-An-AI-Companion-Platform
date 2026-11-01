"""handlers/social/autonomy/__init__.py
====================================
賽博群友 Social Core。

正式群（SOLO）與沙盒群像（SANDBOX_GROUP）共用同一套決策、狀態與帳本；
差別只在 Speaker adapter 與輪流規則。

對外主要入口：
- ``SocialDirector``：決策與落帳
- ``ChannelModeResolver``：頻道模式（安全預設 SOLO）
- ``SpeakerRouter``：正式群硬隔離
- ``SandboxGroupRunner``：沙盒群像（雪莉 + NPC）
"""

from __future__ import annotations

from .contracts import (
    CANNED_FALLBACK_TEXTS,
    ChannelMode,
    DecisionAction,
    GateResult,
    Persona,
    SocialEvent,
    SpeechIntent,
    TriggerType,
    looks_like_canned_fallback,
)
from .director import DirectorDecision, SocialDirector
from .dialogue import (
    DEFAULT_NAME_TOKENS,
    DialoguePhase,
    DialogueState,
    DialogueTracker,
    get_dialogue_tracker,
    reset_dialogue_tracker,
)
from .gate import SpeechGate
from .group import SandboxGroupRunner
from .modes import ChannelModeResolver, ModeDecision
from .personas import PersonaRoster, PersonaValidationError
from .router import SoloModeViolation, SpeakerRouter
from .scene import SceneInspection, SceneManager
from .speakers import BotSpeaker, DeliveryResult, WebhookSpeaker
from .turns import TurnDecision, TurnRules, TurnScheduler

__all__ = [
    "CANNED_FALLBACK_TEXTS",
    "BotSpeaker",
    "ChannelMode",
    "ChannelModeResolver",
    "DEFAULT_NAME_TOKENS",
    "DecisionAction",
    "DeliveryResult",
    "DialoguePhase",
    "DialogueState",
    "DialogueTracker",
    "DirectorDecision",
    "GateResult",
    "ModeDecision",
    "Persona",
    "PersonaRoster",
    "PersonaValidationError",
    "SandboxGroupRunner",
    "SceneInspection",
    "SceneManager",
    "SocialDirector",
    "SocialEvent",
    "SoloModeViolation",
    "SpeechGate",
    "SpeechIntent",
    "SpeakerRouter",
    "TriggerType",
    "TurnDecision",
    "TurnRules",
    "TurnScheduler",
    "WebhookSpeaker",
    "get_dialogue_tracker",
    "looks_like_canned_fallback",
    "reset_dialogue_tracker",
]
