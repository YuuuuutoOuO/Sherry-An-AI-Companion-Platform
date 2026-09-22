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
from typing import Any

logger = logging.getLogger(__name__)


def build_user_memory_block(
    bot: Any,
    user_id: int,
    display_name: str,
    mentioned_users: list[Any] | None = None,
    query_text: str = "",
) -> str:
    """從 MemoryManager 取得特定使用者與提及對象的記憶摘要。
    若 MemoryManager 尚未載入或該使用者無記錄，回傳空字串。

    Args:
        bot: discord.ext.commands.Bot 實例，用來取得 MemoryManager Cog。
        user_id: Discord 使用者的 ID。
        display_name: 使用者的目前顯示名稱，用於 fallback 稱呼。
        mentioned_users: 訊息中顯式 @提及 之使用者列表（可選）。
        query_text: 使用者訊息文字，用於模糊比對記憶庫中其他使用者的暱稱（可選）。

    Returns:
        組裝完成的記憶字串，可直接嵌入 Prompt。
    """
    try:
        mem_mgr = bot.get_cog("MemoryManager")
        if not mem_mgr:
            return ""

        blocks: list[str] = []

        # 1. 說話者本身的記憶（上限 8 條 facts，預算 ~400 tok）與好感度關係狀態
        rel_snippet = ""
        if hasattr(mem_mgr, "relations") and hasattr(mem_mgr.relations, "get_relationship_snippet"):
            try:
                res = mem_mgr.relations.get_relationship_snippet(user_id)
                if isinstance(res, str):
                    rel_snippet = res
            except Exception:
                rel_snippet = ""

        snippet = ""
        try:
            res_s = mem_mgr.get_user_memory_snippet(user_id, max_facts=8, query_text=query_text)
            if isinstance(res_s, str):
                snippet = res_s
        except TypeError:
            try:
                res_s = mem_mgr.get_user_memory_snippet(user_id, max_facts=8)
                if isinstance(res_s, str):
                    snippet = res_s
            except TypeError:
                res_s = mem_mgr.get_user_memory_snippet(user_id)
                if isinstance(res_s, str):
                    snippet = res_s

        user_content_parts: list[str] = []
        if rel_snippet:
            user_content_parts.append(rel_snippet)
        if snippet:
            user_content_parts.append(snippet)

        if user_content_parts:
            combined_user_content = "\n".join(user_content_parts)
            blocks.append(
                f"【🧠 關於正在與妳說話的這個人「{display_name}」的長期記憶】\n"
                f"（以下是妳過去積累的真實記憶，請自然地融入對話中運用，不要直接背誦）\n"
                f"{combined_user_content}"
            )

        # 2. 被 @提及 或 暱稱提及 的第三方使用者記憶（最多 1 人，上限 1 條 fact）
        target_uids: list[str] = []
        target_map: dict[str, str] = {}

        bot_user_id = getattr(getattr(bot, "user", None), "id", None)

        if mentioned_users:
            for u in mentioned_users:
                uid = getattr(u, "id", None)
                if uid and uid != user_id and uid != bot_user_id:
                    uid_str = str(uid)
                    if uid_str not in target_uids:
                        target_uids.append(uid_str)
                        target_map[uid_str] = getattr(u, "display_name", str(uid))
                    if len(target_uids) >= 1:
                        break

        if len(target_uids) < 1 and query_text and hasattr(mem_mgr, "_store") and isinstance(mem_mgr._store, dict):
            for uid_str, data in mem_mgr._store.items():
                if uid_str == str(user_id):
                    continue
                aliases = data.get("aliases", []) if isinstance(data, dict) else []
                for alias in aliases:
                    if alias and len(alias) >= 2 and alias in query_text:
                        if uid_str not in target_uids:
                            target_uids.append(uid_str)
                            target_map[uid_str] = alias
                        break
                if len(target_uids) >= 1:
                    break

        for uid_str in target_uids[:1]:
            other_snippet = ""
            try:
                res_o = mem_mgr.get_user_memory_snippet(int(uid_str), max_facts=1)
                if isinstance(res_o, str):
                    other_snippet = res_o
            except TypeError:
                res_o = mem_mgr.get_user_memory_snippet(int(uid_str))
                if isinstance(res_o, str):
                    other_snippet = res_o
            if other_snippet:
                t_name = target_map.get(uid_str, uid_str)
                blocks.append(
                    f"【👥 關於對話中提及/詢問的人物「{t_name}」的長期記憶與背景資訊】\n"
                    f"（以下是妳關於該使用者的記憶，若對話涉及該人物可作參考，非必要請勿過度喧賓奪主）\n"
                    f"{other_snippet}"
                )

        return "\n\n".join(blocks)

    except Exception as exc:
        logger.warning("memory_builder: 無法取得使用者記憶: %s", exc)
        return ""
