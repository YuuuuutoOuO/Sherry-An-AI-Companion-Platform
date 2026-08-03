from discord.ext import commands
from handlers.memory.cog import MemoryManager
from handlers.memory.nightly_cog import NightlyDigest

async def setup(bot: commands.Bot) -> None:
    """替神經接入具備個體反芻能力的『超級左腦』"""
    await bot.add_cog(MemoryManager(bot))
    await bot.add_cog(NightlyDigest(bot))