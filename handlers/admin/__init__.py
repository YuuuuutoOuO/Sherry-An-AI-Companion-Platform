import discord
from discord.ext import commands
from .cog import AdminCog

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
