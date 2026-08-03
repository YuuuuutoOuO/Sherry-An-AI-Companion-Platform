import discord
from discord.ext import commands
from .cog import VoiceCog

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VoiceCog(bot))
