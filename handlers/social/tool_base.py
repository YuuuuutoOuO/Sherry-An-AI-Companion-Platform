from __future__ import annotations

import discord
from discord.ext import commands

from handlers.social.contracts import (
    ToolCategory,
    ToolExecutionStatus,
    ToolResult,
)

# 向下相容別名
ToolResponseMode = ToolCategory


class BaseTool:
    @property
    def intent_name(self) -> str:
        """Tool 唯一識別標籤 (如 'MUSIC', 'STOCK')"""
        raise NotImplementedError

    @property
    def llm_description(self) -> str:
        """交由分類模型判斷時，說明該在什麼時候喚起這個 Tool 的準則"""
        raise NotImplementedError

    @property
    def response_mode(self) -> ToolResponseMode:
        """工具的回應模式，預設為 DATA_PROVIDER。自帶 Discord 渲染/語音的工具可覆寫為 DIRECT_UI。"""
        return ToolCategory.DATA_PROVIDER

    def can_handle_by_regex(self, text: str) -> bool:
        """最高速匹配層：針對非常明顯的使用情境直接啟動不廢話"""
        return False

    async def execute(
        self,
        bot: commands.Bot,
        message: discord.Message,
        user_query: str,
        vibe_snippet: str,
        full_context: str,
        **kwargs: object,
    ) -> ToolResult | str | None:
        """工具核心：此功能實際上的運作邏輯。

        Args:
            bot: Discord Bot 實例。
            message: 觸發的 Discord 訊息。
            user_query: 使用者的文字輸入。
            vibe_snippet: 頻道近期話題氛圍摘要。
            full_context: 含上下文的完整提示文本。
            **kwargs: 擴充參數，例如 images: list[str]。

        Returns:
            標準 ToolResult 實例、字串摘要或 None。
        """
        raise NotImplementedError