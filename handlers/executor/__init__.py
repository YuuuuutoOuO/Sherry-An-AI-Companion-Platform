import discord
from discord.ext import commands
from .cog import ExecutorCog

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ExecutorCog(bot))