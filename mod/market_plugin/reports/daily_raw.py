"""
daily_raw.py  (mod/market_plugin/reports)
==========================================
每日 08:00 技術指標晨報，發送至股票頻道。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from mod.market_plugin.utils import get_or_fetch_channel

if TYPE_CHECKING:
    from discord.ext import commands

logger = logging.getLogger(__name__)


async def run_daily_raw_report(bot: commands.Bot, fetcher: Any, channel_id: int) -> None:
    """執行每日技術指標數據晨報，並發送到指定 Discord 頻道。

    Args:
        bot: Discord 機器人實例。
        fetcher: 股票數據抓取模組實例。
        channel_id: Discord 目標頻道數字 ID。
    """
    logger.info(
        "[%s] [market_plugin] 📊 執行技術指標數據報告...",
        datetime.now(timezone(timedelta(hours=8))),
    )
    data_store = bot._market_watchlists()  # type: ignore[attr-defined]
    watchlist = data_store.get("raw_list", [])
    if not watchlist:
        logger.warning("[market_plugin] raw_list 為空，跳過")
        return

    raw_data = await asyncio.to_thread(fetcher.get_stock_info_raw, watchlist)
    if not raw_data:
        return

    channel = await get_or_fetch_channel(bot, channel_id)
    if not channel:
        logger.error("[market_plugin] ❌ 找不到股票頻道 ID: %d", channel_id)
        return

    now_str = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
    title_msg = (
        f"📊 **技術指標數據報告 ({now_str})**\n"
        "🔴 漲(-) | 🟢 跌(+) | 格式: [Sym 現價 1D% 1M% E20/50/200 RSI PE PB]\n"
    )
    table_header = "```diff\n"
    table_header += f"{'Sym':<5} {'Close':>7} {'1D%':>7} {'1M%':>7} {'E20':>6} {'E50':>6} {'E200':>6} {'RSI':>4} {'P/E':>5} {'P/B':>4}\n"
    table_header += "-" * 68 + "\n"

    current_msg = title_msg + table_header

    for d in raw_data:
        day_str = f"{d.get('day_pct', 0)}%"
        mon_str = f"{d.get('mon_pct', 0)}%"

        pe_val = d.get("pe", "N/A")
        pe_str = f"{float(pe_val):>5.1f}" if isinstance(pe_val, (int, float)) else f"{pe_val!s:>5}"
        pb_val = d.get("pb", "N/A")
        pb_str = f"{float(pb_val):>4.1f}" if isinstance(pb_val, (int, float)) else f"{pb_val!s:>4}"

        ticker = str(d.get("ticker", ""))[:5]
        line = (
            f"{ticker:<5} {d.get('close', 0):>7.2f} {day_str:>7} {mon_str:>7} "
            f"{d.get('ema20', 0):>6.1f} {d.get('ema50', 0):>6.1f} {d.get('ema200', 0):>6.1f} "
            f"{d.get('rsi', 0):>4.0f} {pe_str} {pb_str}"
        )

        if d.get("day_pct", 0) > 0:
            formatted_line = f"- {line}\n"
        elif d.get("day_pct", 0) < 0:
            formatted_line = f"+ {line}\n"
        else:
            formatted_line = f"  {line}\n"

        if len(current_msg) + len(formatted_line) + 5 > 1900:
            current_msg += "```"
            await channel.send(current_msg)
            await asyncio.sleep(1)
            current_msg = table_header + formatted_line
        else:
            current_msg += formatted_line

    if current_msg not in (title_msg + table_header, table_header):
        current_msg += "```"
        await channel.send(current_msg)

    logger.info("[market_plugin] ✅ 技術指標晨報發送完成")
