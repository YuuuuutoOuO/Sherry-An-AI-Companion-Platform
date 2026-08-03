"""
morning_verify.py  (mod/market_plugin/reports)
================================================
每日 07:00 早報驗收：回填 AI 預測的實際收盤價並統計命中率。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from mod.market_plugin.utils import get_or_fetch_channel

if TYPE_CHECKING:
    from discord.ext import commands

logger = logging.getLogger(__name__)


async def run_morning_verify_report(
    bot: commands.Bot, fetcher: Any, db: Any, channel_id: int
) -> None:
    """執行早報驗收報告與數據庫實際收盤價回填。

    Args:
        bot: Discord 機器人實例。
        fetcher: 股票數據抓取模組實例。
        db: 資料庫模組實例。
        channel_id: Discord 目標頻道數字 ID。
    """
    now_tz = datetime.now(timezone(timedelta(hours=8)))
    today_str = now_tz.strftime("%Y-%m-%d")
    logger.info("[%s] [market_plugin] 🌅 執行 %s 早報驗收...", now_tz, today_str)

    try:
        data_store = bot._market_watchlists()  # type: ignore[attr-defined]
        ai_tickers = data_store.get("ai_list", [])
        crypto_tickers = fetcher.get_target_crypto()
        all_tickers = list(set(ai_tickers + crypto_tickers))

        prices = await bot.loop.run_in_executor(None, fetcher.get_current_prices, all_tickers)
        if not prices:
            logger.warning("[market_plugin] ⚠️ 無法獲取當前價格，早報驗收延遲")
            return

        updated_stocks = db.update_actual_prices("stock", prices)
        updated_crypto = db.update_actual_prices("crypto", prices)

        channel = await get_or_fetch_channel(bot, channel_id)
        if channel:
            msg = (
                f"🌅 **早安！市場收盤驗收報告 ({today_str})**\n"
                f"━━━━━━━━━━━━━━\n"
                f"✅ 美股實際價格更新: **{updated_stocks}** 筆\n"
                f"✅ Crypto 實際價格更新: **{updated_crypto}** 筆\n"
            )
            await channel.send(msg)
        else:
            logger.error("[market_plugin] ❌ 找不到股票頻道 ID: %d", channel_id)

    except Exception:
        logger.exception("[market_plugin] ❌ 早報驗收出錯")
