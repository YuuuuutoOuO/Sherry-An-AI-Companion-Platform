"""handlers/social/live_state.py
================================
雪莉的即時情境狀態（Live State）。

讓雪莉「活在當下」：每次回覆前，把目前正在播放的音樂、佇列長度等
即時狀態濃縮成一行短句注入 Prompt 尾部。

Token 預算：只有真的有音樂在播放時才輸出，約 25~40 Token；
沒有播放時回傳空字串（0 Token）。
"""

from __future__ import annotations

import logging
from typing import Any

import settings

logger = logging.getLogger(__name__)


def build_live_state_snippet(
    bot: Any,
    guild_id: int | str | None = None,
) -> str:
    """組裝雪莉當下的即時情境摘要（目前主要為音樂播放狀態）。

    Args:
        bot: Discord Bot 實例（用來取得 MusicHandler Cog）。
        guild_id: 當前伺服器 ID。

    Returns:
        str: 例如「🎧 現在正在播《千本櫻》（01:23 / 04:05，佇列還有 2 首）。」；
             無音樂或模組未載入時回傳空字串。
    """
    if bot is None or guild_id in (None, ""):
        return ""
    if not getattr(settings, "SELF_ACTION_LIVE_STATE_ENABLED", True):
        return ""

    try:
        handler = bot.get_cog("MusicHandler")
    except Exception:
        return ""
    if handler is None:
        return ""

    try:
        gid = int(guild_id)
    except (TypeError, ValueError):
        return ""

    current_song = getattr(handler, "current_song", None)
    if not isinstance(current_song, dict):
        return ""
    current = current_song.get(gid)
    if not current:
        return ""

    queues = getattr(handler, "queues", None)
    queue = queues.get(gid, []) if isinstance(queues, dict) else []
    queue_len = len(queue) if isinstance(queue, (list, tuple)) else 0

    title = str(current.get("title") or current.get("webpage_url") or "未知曲目").strip()
    if not title:
        return ""

    parts: list[str] = [f"🎧 現在正在播《{title}》"]

    try:
        elapsed = handler.get_elapsed_time(gid)
        duration = current.get("duration")
        if elapsed is not None:
            elapsed_str = handler.format_duration(elapsed)
            if duration:
                duration_str = handler.format_duration(duration)
                detail = f"{elapsed_str} / {duration_str}"
            else:
                detail = f"已播 {elapsed_str}"
            if queue_len:
                detail += f"，佇列還有 {queue_len} 首"
            parts.append(f"（{detail}）")
    except Exception as exc:  # pragma: no cover - 防禦性路徑
        logger.debug("live_state: 取得播放進度失敗: %s", exc)

    parts.append("。")
    return "".join(parts)
