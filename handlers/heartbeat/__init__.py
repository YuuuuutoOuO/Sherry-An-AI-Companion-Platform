"""
handlers/heartbeat/__init__.py
=============================
Heartbeat 主動排程腦模組進入點。
"""

import logging
from discord.ext import commands
from handlers.heartbeat.parser import HeartbeatTask, parse_heartbeat_markdown

logger = logging.getLogger(__name__)


async def setup(bot: commands.Bot) -> None:
    """載入 Heartbeat 主動排程腦 Cog"""
    from handlers.heartbeat.cog import HeartbeatCog
    await bot.add_cog(HeartbeatCog(bot))


__all__ = ["HeartbeatTask", "parse_heartbeat_markdown", "setup"]
