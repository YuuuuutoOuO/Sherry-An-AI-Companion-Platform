"""
handlers.scheduler — 背景排程提醒系統。

使用 tasks.loop(seconds=10) 定期掃描 schedule.json，
觸發到期的單次/週期性任務，並透過 BotBrain 生成動態提醒文案後發送至 Discord。
"""

import logging
from discord.ext import commands
from .cog import ScheduleWorker

logger = logging.getLogger(__name__)

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ScheduleWorker(bot))