"""
etf_monitor.py  (mod/market_plugin/reports)
=============================================
台股 ETF 溢折價監控，每 10 分鐘於台股開盤時間執行。
注意：NAV 以 yfinance 市價 * 1.015 模擬，僅為示範框架，
實務上需實作投信官網 NAV 爬蟲。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from mod.market_plugin.utils import get_or_fetch_channel

if TYPE_CHECKING:
    from discord.ext import commands

logger = logging.getLogger(__name__)

try:
    import yfinance as yf
    _YF_AVAILABLE = True
except ImportError:
    _YF_AVAILABLE = False

TARGET_USER_TAG = "<@425311733503229972>"


def _fetch_etf_quote(ticker: str) -> tuple[float, float] | None:
    """查詢單一 ETF 標的之市價與溢折價（同步函式，供執行緒池調用）。

    Args:
        ticker: ETF 股票代碼。

    Returns:
        tuple[float, float] | None: (market_price, premium) 或在失敗時回傳 None。
    """
    try:
        stock = yf.Ticker(ticker)
        market_price = float(stock.fast_info["lastPrice"])
        nav = market_price * 1.015  # ⚠️ 模擬值，請替換為真實 NAV 來源
        premium = ((market_price - nav) / nav) * 100
        return market_price, premium
    except Exception:
        logger.exception("[market_plugin] ETF 監控錯誤 (%s)", ticker)
        return None


async def check_etf_premium(
    bot: commands.Bot, channel_id: int, load_watchlists_fn: Callable[[], dict[str, Any]]
) -> None:
    """執行台股 ETF 溢折價監控任務。

    Args:
        bot: Discord 機器人實例。
        channel_id: Discord 股票警報目標頻道數字 ID。
        load_watchlists_fn: 讀取自訂關注標的函式。
    """
    if not _YF_AVAILABLE:
        logger.warning("[market_plugin] yfinance 未安裝，ETF 溢折價監控略過")
        return

    now = datetime.now(timezone(timedelta(hours=8)))
    if not (9 <= now.hour < 14):
        return

    data = await asyncio.to_thread(load_watchlists_fn)
    etf_list = data.get("etf_list", [])
    if not etf_list:
        return

    channel = await get_or_fetch_channel(bot, channel_id)
    if not channel:
        logger.error("[market_plugin] ❌ 找不到股票頻道 ID: %d", channel_id)
        return

    for ticker in etf_list:
        quote = await asyncio.to_thread(_fetch_etf_quote, ticker)
        if quote is None:
            continue
        market_price, premium = quote
        if abs(premium) > 1.0:
            alert = (
                f"🚨 **台股 ETF 溢折價警報**\n"
                f"標的: `{ticker}` | 市價: `{market_price:.2f}`\n"
                f"預估溢折價: **{premium:.2f}%**\n"
                f"提醒: {TARGET_USER_TAG}"
            )
            await channel.send(alert)
