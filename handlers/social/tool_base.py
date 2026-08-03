"""
handlers/social/tool_base.py
定義代理人工具基底，拒絕 God Code 的第一步。
"""

import discord
from discord.ext import commands

class BaseTool:
    @property
    def intent_name(self) -> str:
        """Tool 唯一識別標籤 (如 'MUSIC', 'STOCK')"""
        raise NotImplementedError

    @property
    def llm_description(self) -> str:
        """交由分類模型判斷時，說明該在什麼時候喚起這個 Tool 的準則"""
        raise NotImplementedError

    def can_handle_by_regex(self, text: str) -> bool:
        """最高速匹配層：針對非常明顯的使用情境直接啟動不廢話"""
        return False

    async def execute(self, bot: commands.Bot, message: discord.Message, user_query: str, vibe_snippet: str, full_context: str) -> None:
        """工具核心：此功能實際上的運作邏輯"""
        raise NotImplementedError