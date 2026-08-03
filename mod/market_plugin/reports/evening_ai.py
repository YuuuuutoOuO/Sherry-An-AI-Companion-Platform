"""
evening_ai.py  (mod/market_plugin/reports)
============================================
每日 21:30 晚間 AI 深度分析，發送至股票頻道。
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


async def run_evening_ai_report(
    bot: commands.Bot, fetcher: Any, analyst: Any, db: Any, channel_id: int
) -> None:
    """執行 21:30 晚報 AI 深度分析任務，並將預測結果存入資料庫與發布報告。

    Args:
        bot: Discord 機器人實例。
        fetcher: 股票數據抓取模組實例。
        analyst: AI 分析師模組實例。
        db: 資料庫模組實例。
        channel_id: Discord 目標頻道數字 ID。
    """
    logger.info(
        "[%s] [market_plugin] 🌙 執行 21:30 晚報分析預測任務...",
        datetime.now(timezone(timedelta(hours=8))),
    )

    channel = await get_or_fetch_channel(bot, channel_id)
    if not channel:
        logger.error("[market_plugin] ❌ 找不到股票頻道 ID: %d", channel_id)
        return

    data_store = bot._market_watchlists()  # type: ignore[attr-defined]
    ai_stocks = data_store.get("ai_list", [])
    crypto_list = fetcher.get_target_crypto()

    async def safe_send(content: str) -> None:
        try:
            if len(content) > 1900:
                for i in range(0, len(content), 1900):
                    await channel.send(content[i:i+1900])
                    await asyncio.sleep(0.5)
            else:
                await channel.send(content)
        except Exception as e:  # noqa: BLE001
            logger.warning("[market_plugin] ⚠️ 發送失敗: %s", e)

    # --- Part A: 美股 AI 分析 ---
    try:
        await safe_send(f"🔍 正在針對 **{len(ai_stocks)} 檔美股** 進行 AI 深度分析，請稍候...")
        s_data_str, s_data_list = await asyncio.to_thread(fetcher.fetch_6mo_trend_and_news, ai_stocks)
        stock_preds = await analyst.analyze_evening_review(s_data_str)

        if stock_preds:
            s_price_map = {item["ticker"]: item["price"] for item in s_data_list}
            for p in stock_preds:
                p["price"] = s_price_map.get(p["ticker"], 0)
            db.save_prediction("stock", stock_preds)
            report = await analyst.generate_discord_report(stock_preds, title_suffix="美股科技巨頭")
            await safe_send(report)

        logger.info("[market_plugin] ☕ API 冷卻 30s...")
        await asyncio.sleep(30)

    except Exception:
        logger.exception("[market_plugin] ❌ 美股晚報失敗")

    # --- Part B: 加密貨幣 AI 分析 ---
    try:
        await safe_send("🔍 正在分析 **加密貨幣** 趨勢...")
        c_data_str, c_data_list = await asyncio.to_thread(fetcher.fetch_6mo_trend_and_news, crypto_list)
        crypto_preds = await analyst.analyze_evening_review(c_data_str)

        if crypto_preds:
            c_price_map = {item["ticker"]: item["price"] for item in c_data_list}
            for p in crypto_preds:
                p["price"] = c_price_map.get(p["ticker"], 0)
            db.save_prediction("crypto", crypto_preds)
            report_c = await analyst.generate_discord_report(crypto_preds, title_suffix="加密貨幣趨勢")
            await safe_send(report_c)

    except Exception:
        logger.exception("[market_plugin] ❌ Crypto 晚報失敗")
