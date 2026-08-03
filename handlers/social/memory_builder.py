"""
handlers/social/memory_builder.py
===================================
SillyTavern 風格的「長期記憶注入器」。

從 MemoryManager 的 user_memory.json 中讀取已知使用者資訊，
並組裝成一段可以被插入 Prompt 的「記憶摘要文字」。

這複製了 SillyTavern 的 Smart Context / User Persona 記憶系統。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def build_user_memory_block(bot, user_id: int, display_name: str) -> str:
    """
    從 MemoryManager 取得特定使用者的記憶摘要。
    若 MemoryManager 尚未載入或該使用者無記錄，回傳空字串。

    Args:
        bot: discord.ext.commands.Bot 實例，用來取得 MemoryManager Cog。
        user_id: Discord 使用者的 ID。
        display_name: 使用者的目前顯示名稱，用於 fallback 稱呼。

    Returns:
        組裝完成的記憶字串，可直接嵌入 Prompt。
    """
    try:
        mem_mgr = bot.get_cog("MemoryManager")
        if not mem_mgr:
            return ""

        snippet = mem_mgr.get_user_memory_snippet(user_id)
        if not snippet:
            return ""

        return (
            f"【🧠 關於正在與妳說話的這個人「{display_name}」的長期記憶】\n"
            f"（以下是妳過去積累的真實記憶，請自然地融入對話中運用，不要直接背誦）\n"
            f"{snippet}"
        )

    except Exception as exc:
        logger.warning("memory_builder: 無法取得使用者記憶: %s", exc)
        return ""
