"""handlers/social/action_recorder.py
=====================================
雪莉「行為記憶」集中寫入器。

所有工具執行完畢後，統一由本模組將「雪莉剛剛做了什麼」寫入
`handlers.memory.self_memory.self_actions`，供後續 Prompt 檢索。

設計原則：
  1. 只在工具執行成功／失敗後寫入一筆，成本 O(1)，不呼叫 LLM。
  2. summary 以第一人稱、人類可讀為目標（例如「播放了《千本櫻》」）。
  3. 任何例外都靜默吞掉，絕不影響主對話流程。
"""

from __future__ import annotations

import logging
import re
from typing import Any

import settings

logger = logging.getLogger(__name__)

# 工具 → 中文動作動詞（用於 UI 與摘要）
TOOL_VERB_MAP: dict[str, str] = {
    "MUSIC": "音樂操作",
    "SEARCH": "網路搜尋",
    "DRAW": "繪圖",
    "STOCK": "股市查詢",
    "PIXIV": "插畫搜尋",
    "SCHEDULE": "排程設定",
    "SYSTEM_INSPECT": "系統檢測",
    "WEATHER": "天氣查詢",
    "CHAT": "聊天",
}

# 以「使用者要求」為主體摘要的工具（查了什麼比查到什麼更重要）
_INPUT_CENTRIC_TOOLS = frozenset(
    {"SEARCH", "STOCK", "PIXIV", "SCHEDULE", "SYSTEM_INSPECT", "WEATHER", "DRAW"}
)


def _clean(text: Any, limit: int = 300) -> str:
    """壓縮空白並截斷，避免把整篇工具輸出塞進記憶。"""
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


def _extract_music_title(observation: str, user_input: str) -> str:
    """從音樂工具回覆中萃取歌名（支援《》與「」兩種引號）。"""
    match = re.search(r"《([^》]{1,80})》|「([^」]{1,80})」", observation or "")
    if match:
        return (match.group(1) or match.group(2) or "").strip()
    return _clean(user_input, 80)


def build_action_summary(
    tool_name: str,
    user_input: str,
    observation: str,
) -> tuple[str, dict[str, Any]]:
    """組裝人類可讀的行為摘要與結構化細節。

    Args:
        tool_name: 工具名稱（MUSIC / SEARCH / DRAW …）。
        user_input: 使用者原始指令或工具參數。
        observation: 工具回傳的觀測文字。

    Returns:
        tuple[str, dict]: (summary, detail)
    """
    tool = str(tool_name or "").strip().upper()
    verb = TOOL_VERB_MAP.get(tool, tool or "工具")
    clean_input = _clean(user_input, 80)
    clean_obs = _clean(observation, 300)
    detail: dict[str, Any] = {"input": clean_input, "observation": clean_obs}

    if tool == "MUSIC":
        title = _extract_music_title(clean_obs, clean_input)
        detail["title"] = title
        summary = f"音樂操作「{title}」" if title else "執行了一次音樂操作"
    elif tool in _INPUT_CENTRIC_TOOLS:
        summary = f"{verb}「{clean_input}」" if clean_input else verb
    else:
        target = clean_input or clean_obs
        summary = f"{verb}：{target[:100]}" if target else verb

    return summary, detail


def record_tool_action(
    message: Any,
    tool_name: str,
    user_input: str,
    observation: str,
    *,
    status: str = "success",
    importance: int = 3,
) -> None:
    """將一次工具執行寫入雪莉的行為記憶。

    本函式永不拋出例外，呼叫端可直接在 finally/except 中使用。

    Args:
        message: 觸發的 Discord 訊息（用於取得 guild/channel/user）。
        tool_name: 工具名稱。
        user_input: 使用者輸入或工具參數。
        observation: 工具回傳摘要。
        status: success / error。
        importance: 重要度 1~5。
    """
    if not getattr(settings, "SELF_ACTION_MEMORY_ENABLED", True):
        return

    try:
        from handlers.memory.self_memory import record_self_action

        summary, detail = build_action_summary(tool_name, user_input, observation)
        if not summary:
            return

        guild = getattr(message, "guild", None)
        channel = getattr(message, "channel", None)
        author = getattr(message, "author", None)

        record_self_action(
            str(tool_name or "").strip().upper(),
            summary,
            verb=TOOL_VERB_MAP.get(str(tool_name or "").strip().upper(), ""),
            guild_id=getattr(guild, "id", ""),
            channel_id=getattr(channel, "id", ""),
            user_id=getattr(author, "id", ""),
            user_name=getattr(author, "display_name", ""),
            detail=detail,
            status=status,
            importance=importance,
        )
    except Exception as exc:  # pragma: no cover - 防禦性路徑
        logger.debug("action_recorder: 行為記憶寫入略過 (%s)", exc)
