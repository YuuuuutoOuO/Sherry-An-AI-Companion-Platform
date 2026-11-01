from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .contracts import SentBatch

logger = logging.getLogger(__name__)


def _message_ts(message: Any) -> float:
    created = getattr(message, "created_at", None)
    try:
        return float(created.timestamp())
    except Exception:
        return time.time()


async def send_bubbles(
    channel: Any,
    bubbles: list[str],
    *,
    reply_to: Any = None,
    turn_id: str = "",
    typing_delay: float = 1.0,
    max_bubbles: int = 3,
) -> SentBatch:
    """逐顆發送；只回傳結果，不更新任何對話窗狀態。"""
    cid = str(getattr(channel, "id", "") or "")
    if not channel or not bubbles:
        return SentBatch(cid, turn_id, 0.0, 0.0, (), ())

    cleaned = [bubble.strip() for bubble in bubbles if bubble and bubble.strip()]
    if len(cleaned) > max_bubbles:
        cleaned = cleaned[:max_bubbles - 1] + [" ".join(cleaned[max_bubbles - 1:])]

    message_ids: list[str] = []
    first_ts = 0.0
    last_ts = 0.0
    sent_texts: list[str] = []

    for idx, bubble in enumerate(cleaned):
        try:
            if hasattr(channel, "typing"):
                async with channel.typing():
                    delay = min(1.5, max(0.5, len(bubble) * 0.03 * typing_delay))
                    await asyncio.sleep(delay)
        except Exception:
            pass

        try:
            if idx == 0 and reply_to is not None and hasattr(reply_to, "reply"):
                message = await reply_to.reply(bubble, mention_author=False)
            else:
                message = await channel.send(bubble)
        except Exception as exc:
            logger.warning("[CyberV2] 氣泡發送失敗: %s", exc)
            continue

        if message is None:
            continue
        ts = _message_ts(message)
        message_ids.append(str(getattr(message, "id", "") or ""))
        sent_texts.append(bubble)
        first_ts = ts if not first_ts else min(first_ts, ts)
        last_ts = max(last_ts, ts)

    return SentBatch(
        channel_id=cid,
        turn_id=turn_id,
        first_ts=first_ts or time.time(),
        last_ts=last_ts or time.time(),
        message_ids=tuple(message_ids),
        bubbles=tuple(sent_texts),
    )
