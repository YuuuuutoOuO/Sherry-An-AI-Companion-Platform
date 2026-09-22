import discord
from discord.ext import commands

# 對整個專案公開這些變數！其他使用模塊完全不需要知道你分層裝進子系統這件事情！
from .models import InferenceTask
from .cog import BotBrain
from .client import OllamaClient
from .health_monitor import HealthMonitor

__all__ = ["InferenceTask", "BotBrain", "OllamaClient", "HealthMonitor", "setup"]


async def setup(bot: commands.Bot) -> None:
    """掛載機制。從底下的 cog 文件把真的組件找上來送去 main 的裝備清單之中。"""
    await bot.add_cog(BotBrain(bot))
    await bot.add_cog(HealthMonitor(bot))