"""
utils.py  (mod/market_plugin)
=============================
模組共用輔助函式庫。
提供高可靠的 Discord 頻道獲取機制，優先讀取快取，未命中時請求 Discord API。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from discord.ext import commands

logger = logging.getLogger(__name__)


async def get_or_fetch_channel(
    bot: commands.Bot, channel_id: int
) -> discord.abc.Messageable | None:
    """嘗試自 bot 快取中讀取指定 Channel，若未在快取內則調用 API 獲取。

    Args:
        bot: Discord 機器人實例。
        channel_id: Discord 頻道數字 ID。

    Returns:
        獲取成功的 Messageable 頻道物件，若不存在或發生錯誤則為 None。
    """
    if not channel_id:
        return None

    channel = bot.get_channel(channel_id)
    if channel is not None and isinstance(channel, discord.abc.Messageable):
        return channel

    try:
        fetched = await bot.fetch_channel(channel_id)
        if isinstance(fetched, discord.abc.Messageable):
            return fetched
    except Exception:
        logger.exception(
            "[market_plugin] 獲取頻道 ID %d 失敗",
            channel_id,
        )
    return None
