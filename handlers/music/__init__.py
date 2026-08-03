from discord.ext import commands
from handlers.music.cog import MusicHandler

async def setup(bot: commands.Bot):
    """加載已拆分的重構音樂模塊"""
    await bot.add_cog(MusicHandler(bot))