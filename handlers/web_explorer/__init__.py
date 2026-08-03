from discord.ext import commands
from handlers.web_explorer.cog import WebExplorer

async def setup(bot: commands.Bot) -> None:
    """載入重整改為外掛組包設計模式の自主機器瀏覽者模塊"""
    await bot.add_cog(WebExplorer(bot))