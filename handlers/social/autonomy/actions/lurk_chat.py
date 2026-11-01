"""handlers/social/autonomy/actions/lurk_chat.py
============================================
窺屏接話 action。

原本寫在 ``handlers/care/surfer.py`` 裡的 prompt 與解析邏輯抽到這裡，
讓正式群（SocialDirector）與沙盒群像可以共用同一份人格與規則。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..contracts import Persona
from .prompts import build_lurk_chat_prompt, extract_reply, parse_json_reply


@dataclass(frozen=True)
class ActionPrompt:
    system_prompt: str
    user_prompt: str


class LurkChatAction:
    """主動窺屏插話。"""

    name = "lurk_chat"

    def build(
        self,
        persona: Persona,
        *,
        head_persona: str,
        tail_lore: str = "",
        context_block: str = "",
        script: str = "",
        memory_block: str = "",
        live_state_block: str = "",
        roster_block: str = "",
    ) -> ActionPrompt:
        return ActionPrompt(
            system_prompt=build_lurk_chat_prompt(
                persona,
                head_persona=head_persona,
                tail_lore=tail_lore,
                context_block=context_block,
                memory_block=memory_block,
                live_state_block=live_state_block,
                roster_block=roster_block,
            ),
            user_prompt=script,
        )

    def parse(self, raw: str) -> tuple[str, str]:
        """回傳 (thought, post_text)；post_text 為空代表生成失敗。"""
        return extract_reply(parse_json_reply(raw), keys=("post_text", "reply"))
