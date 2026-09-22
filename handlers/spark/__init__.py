"""handlers/spark/__init__.py - Spark 條件觸發與排程推播核心系統。"""

from __future__ import annotations

from typing import Any
from discord.ext import commands

from .cog import SparkCog
from .service import SparkService, build_spark_service
from .repository import SparkRepository

MOD_INFO: dict[str, Any] = {
    "id": "spark",
    "name": "📡 Spark 泛用推播",
    "icon": "📡",
    "description": "泛用排程與條件推播通知系統（價格 / 定時 / RSS / 天氣）。",
    "category": "工具 / Tools",
    "version": "1.0.0",
    "author": "Sherry Developer",
    "has_web_ui": True,
    "nav_id": "spark",
    "nav_title": "Spark 推播",
    "nav_icon": "📡",
    "view_title": "📡 Spark 推播主控台",
    "admin_only": False,
    "order": 60,
    "script_file": "spark.js",
}


async def setup(bot: commands.Bot) -> None:
    """Cog 載入標準入口點。"""
    await bot.add_cog(SparkCog(bot))


__all__ = ["MOD_INFO", "SparkCog", "SparkService", "SparkRepository", "setup"]

