from discord.ext import commands
from .cog import VibeManager

async def setup(bot: commands.Bot) -> None:
    """代理載入氛圍控制元件的包裹橋段"""
    await bot.add_cog(VibeManager(bot))