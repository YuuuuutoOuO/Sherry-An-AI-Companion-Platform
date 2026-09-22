"""handlers/context_menu/__init__.py
==================================
雪莉右鍵訊息選單指令模組 (Message Context Menu Commands)。
"""

from __future__ import annotations

from discord.ext import commands
from .cog import ContextMenuCog


async def setup(bot: commands.Bot) -> None:
    """掛載右鍵訊息選單 Cog。"""
    await bot.add_cog(ContextMenuCog(bot))
