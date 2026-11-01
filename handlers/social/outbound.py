from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from handlers.care.cyber.send import send_bubbles
from handlers.social.text_style import sanitize_reply, split_bubbles

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OutboundResult:
    bubbles: tuple[str, ...]
    message_ids: tuple[str, ...]
    first_ts: float
    last_ts: float

    @property
    def empty(self) -> bool:
        return not self.message_ids


class OutboundPipeline:
    """所有對外文字的唯一出口。"""

    def __init__(self, bot: Any) -> None:
        self.bot = bot

    async def send(
        self,
        channel: Any,
        text: str,
        *,
        mode: str = "ambient",
        reply_to: Any = None,
        max_bubbles: int = 2,
        allow_urls: bool = False,
        typing_delay: float = 1.0,
        turn_id: str = "",
    ) -> OutboundResult:
        cleaned = sanitize_reply(text, allow_urls=allow_urls)
        if not cleaned:
            return OutboundResult((), (), 0.0, 0.0)

        bubbles = split_bubbles(cleaned, max_bubbles=max(1, int(max_bubbles)))
        if not bubbles:
            return OutboundResult((), (), 0.0, 0.0)

        sent = await send_bubbles(
            channel,
            bubbles,
            reply_to=reply_to,
            turn_id=turn_id,
            typing_delay=typing_delay,
            max_bubbles=max_bubbles,
        )
        return OutboundResult(
            bubbles=tuple(sent.bubbles),
            message_ids=tuple(sent.message_ids),
            first_ts=sent.first_ts,
            last_ts=sent.last_ts,
        )
