"""handlers/social/brain/intents.py
================================
雪莉開口前的「意圖」分類。

同一個角色、同一份人格，但四種情境的說法完全不同：

- REPLY    ：有人在跟她說話（被點名、回覆她、或延續她剛才那串對話）。
- JOIN     ：別人在聊，她只是覺得有趣想插一句。
- INITIATE ：頻道冷場，她自己開一個話題。
- MONOLOGUE：只寫內心日記，不外發。

舊版把所有情況都塞進同一段「妳剛剛在群組裡開口說話」的 prompt，
所以被動接話、插話、主動開話題全都長得像在自言自語。
"""

from __future__ import annotations

from enum import Enum


class BrainIntent(str, Enum):
    """一次生成的目的。"""

    REPLY = "reply"
    JOIN = "join"
    INITIATE = "initiate"
    MONOLOGUE = "monologue"

    @property
    def speaks_to_someone(self) -> bool:
        """是否針對某個具體的人說話。"""
        return self in (BrainIntent.REPLY, BrainIntent.JOIN)

    @property
    def requires_decision(self) -> bool:
        """是否允許模型自己決定「這次不說話」。"""
        return self in (BrainIntent.JOIN, BrainIntent.INITIATE)

    @property
    def is_public(self) -> bool:
        """是否會發到頻道（日記不外發）。"""
        return self is not BrainIntent.MONOLOGUE


def decide_intent(
    *,
    to_sherry: bool = False,
    continuation: bool = False,
    has_recent_human: bool = True,
    explicit: BrainIntent | str | None = None,
) -> BrainIntent:
    """依現場訊號決定意圖。

    Args:
        to_sherry: 這則訊息是否直接叫她（tag / 回覆她 / 文中帶名字）。
        continuation: 是否延續她剛才跟同一個人的對話。
        has_recent_human: 頻道最近是否有人講話。
        explicit: 呼叫端已明確指定時直接採用。
    """
    if explicit is not None:
        return explicit if isinstance(explicit, BrainIntent) else BrainIntent(str(explicit))
    if to_sherry or continuation:
        return BrainIntent.REPLY
    if has_recent_human:
        return BrainIntent.JOIN
    return BrainIntent.INITIATE
