"""handlers/social/autonomy/actions/daily_vibe.py
=============================================
日常摸魚碎碎念 action。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..contracts import Persona
from .prompts import build_daily_vibe_prompt, extract_reply, parse_json_reply


@dataclass(frozen=True)
class ActionPrompt:
    system_prompt: str
    user_prompt: str


class DailyVibeAction:
    """摸魚日常與時段吐槽。"""

    name = "daily_vibe"

    def build(
        self,
        persona: Persona,
        *,
        head_persona: str,
        tail_lore: str = "",
        context_block: str = "",
        stats: dict[str, Any] | None = None,
        memory_block: str = "",
        live_state_block: str = "",
    ) -> ActionPrompt:
        return ActionPrompt(
            system_prompt=build_daily_vibe_prompt(
                persona,
                head_persona=head_persona,
                tail_lore=tail_lore,
                context_block=context_block,
                stats=stats,
                memory_block=memory_block,
                live_state_block=live_state_block,
            ),
            user_prompt="請發布妳此時此刻的摸魚碎碎念動態",
        )

    def parse(self, raw: str) -> tuple[str, str]:
        return extract_reply(parse_json_reply(raw), keys=("post_text", "reply"))
