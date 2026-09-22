"""handlers.sys_inspector
=======================
系統監控中樞 Discord Cog 擴充。

載入／卸載全部系統監控與延遲稽核相關的 Cog，屬 handlers 的 Presentation Layer。
"""

from __future__ import annotations

from discord.ext import commands

from .cog import (
    SelfMonitoringCog,
    SysInspectorCog,
    build_diagnostic_embed,
    get_self_monitoring_service,
)

__all__ = [
    "SysInspectorCog",
    "SelfMonitoringCog",
    "build_diagnostic_embed",
    "get_self_monitoring_service",
    "setup",
]


async def setup(bot: commands.Bot) -> None:
    """Discord 擴充載入入口。"""
    await bot.add_cog(SysInspectorCog(bot))
