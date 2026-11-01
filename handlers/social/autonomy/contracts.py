"""handlers/social/autonomy/contracts.py
======================================
賽博群友 Social Core 的共用資料契約。

設計原則：
1. 這一層不依賴 Discord，也不直接碰資料庫，方便單元測試。
2. 正式群（SOLO）與沙盒群像（SANDBOX_GROUP）共用同一組事件與決策型別。
3. 所有「為什麼不說話」都必須能變成可序列化的 reasons。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ChannelMode(str, Enum):
    """頻道模式。SOLO 是預設值，也是正式群唯一允許的模式。"""

    SOLO = "SOLO"
    SANDBOX_GROUP = "SANDBOX_GROUP"

    @classmethod
    def parse(cls, value: Any) -> "ChannelMode":
        """寬鬆解析模式字串；未知值一律視為 SOLO（安全預設）。"""
        raw = str(value or "").strip().upper()
        if raw == cls.SANDBOX_GROUP.value:
            return cls.SANDBOX_GROUP
        return cls.SOLO

    @property
    def is_group(self) -> bool:
        return self is ChannelMode.SANDBOX_GROUP


class DecisionAction(str, Enum):
    """一次決策的結果。SILENT 也必須落帳。"""

    SPEAK = "SPEAK"
    REACT = "REACT"
    SILENT = "SILENT"


class TriggerType(str, Enum):
    """觸發來源；與 social_decisions.trigger_type 對應。"""

    HUMAN_MESSAGE = "human_message"
    MIND_PULSE = "mind_pulse"
    SURF = "surf"
    TIMER = "timer"
    NPC_TURN = "npc_turn"


@dataclass(frozen=True)
class Persona:
    """一個可發言身分。雪莉是 is_primary=True 的 persona。"""

    key: str
    display_name: str
    soul_name: str
    channel_id: str = ""
    guild_id: str = ""
    avatar_path: str = ""
    role: str = "npc"
    is_primary: bool = False
    enabled: bool = False
    webhook_id: str = ""
    speech_style: str = ""

    @classmethod
    def from_record(cls, record: Any) -> "Persona":
        """由 repository.SocialPersonaRecord 轉換（duck typing，避免循環匯入）。"""
        return cls(
            key=str(getattr(record, "persona_key", "")),
            display_name=str(getattr(record, "display_name", "")),
            soul_name=str(getattr(record, "soul_name", "")),
            channel_id=str(getattr(record, "channel_id", "")),
            guild_id=str(getattr(record, "guild_id", "")),
            avatar_path=str(getattr(record, "avatar_path", "")),
            role=str(getattr(record, "role", "npc")),
            is_primary=bool(getattr(record, "is_primary", False)),
            enabled=bool(getattr(record, "enabled", False)),
            webhook_id=str(getattr(record, "webhook_id", "")),
            speech_style=str(getattr(record, "speech_style", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "display_name": self.display_name,
            "soul_name": self.soul_name,
            "channel_id": self.channel_id,
            "guild_id": self.guild_id,
            "avatar_path": self.avatar_path,
            "role": self.role,
            "is_primary": self.is_primary,
            "enabled": self.enabled,
            "webhook_id": self.webhook_id,
            "speech_style": self.speech_style,
        }


@dataclass(frozen=True)
class GateResult:
    """發話閘門的判斷結果。allow=False 時 reasons 一定有內容。"""

    allow: bool
    reasons: tuple[str, ...] = ()
    score: float = 0.0
    action: DecisionAction = DecisionAction.SILENT

    @classmethod
    def allowed(cls, score: float = 0.0, action: DecisionAction = DecisionAction.SPEAK) -> "GateResult":
        return cls(allow=True, reasons=(), score=score, action=action)

    @classmethod
    def blocked(cls, *reasons: str, score: float = 0.0) -> "GateResult":
        cleaned = tuple(str(r) for r in reasons if str(r))
        return cls(
            allow=False,
            reasons=cleaned or ("blocked_without_reason",),
            score=score,
            action=DecisionAction.SILENT,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "allow": self.allow,
            "reasons": list(self.reasons),
            "score": self.score,
            "action": self.action.value,
        }


@dataclass(frozen=True)
class SocialEvent:
    """進入 Social Core 的統一事件。四條發話路徑都必須轉成這個型別。"""

    kind: str
    channel_id: str
    trigger_type: TriggerType = TriggerType.HUMAN_MESSAGE
    guild_id: str = ""
    persona_key: str = ""
    message_id: str = ""
    content: str = ""
    author_id: str = ""
    author_name: str = ""
    is_human: bool = True
    scene_id: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "channel_id": self.channel_id,
            "trigger_type": self.trigger_type.value,
            "guild_id": self.guild_id,
            "persona_key": self.persona_key,
            "message_id": self.message_id,
            "content": self.content,
            "author_id": self.author_id,
            "author_name": self.author_name,
            "is_human": self.is_human,
            "scene_id": self.scene_id,
            "payload": self.payload,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class SpeechIntent:
    """一次準備送出的發言；SpeakerRouter 依 persona 決定用 Bot 還是 Webhook。"""

    persona: Persona
    channel: Any
    text: str
    mode: ChannelMode = ChannelMode.SOLO
    reply_to: Any = None
    bubbles: tuple[str, ...] = ()


# 生成失敗時絕對不可以送出的罐頭台詞（I7）。
# 這裡只放「曾被硬編碼成 fallback」的字串，避免未來又有人加回去。
CANNED_FALLBACK_TEXTS: frozenset[str] = frozenset(
    {
        "本名偵探現在宣布進入摸魚模式，誰都別叫我破案！",
        "哼…本名偵探只是剛好滑過去順便問問而已，可別誤會喔！",
        "幹嘛啦！本名偵探好心說話，你那是什麼反應啊！😤",
    }
)


def looks_like_canned_fallback(text: str) -> bool:
    """判斷文字是否為已知罐頭台詞（含片段命中）。"""
    normalized = (text or "").strip()
    if not normalized:
        return False
    return any(banned in normalized for banned in CANNED_FALLBACK_TEXTS)
