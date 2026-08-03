"""
card_report.py  (mod/market_plugin/reports)
=============================================
每日 10:00 PTCG 卡片報價報告，發送至 PTCG 頻道。
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


async def run_card_daily_report(
    bot: commands.Bot,
    fetch_snkrdunk_grades_sync: Callable[..., Any],
    load_cards_fn: Callable[[], dict[str, Any]],
    save_cards_fn: Callable[[dict[str, Any]], Any],
    channel_id: int,
) -> None:
    """執行 PTCG 每日卡價檢查與報告發布。

    Args:
        bot: Discord 機器人實例。
        fetch_snkrdunk_grades_sync: SNKRDUNK 價格查詢同步函式。
        load_cards_fn: 讀取卡片追蹤清單函式。
        save_cards_fn: 儲存卡片追蹤清單函式。
        channel_id: Discord PTCG 目標頻道數字 ID。
    """
    now_str = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    logger.info("[%s] [market_plugin] 🃏 執行 PTCG 卡片報價任務...", now_str)

    cards_db = load_cards_fn()
    if not cards_db:
        logger.warning("[market_plugin] ⚠️ 沒有追蹤任何卡片，跳過")
        return

    card_dict = {name: data["url"] for name, data in cards_db.items()}
    target_grades = ["A", "PSA 10", "BGS 9.5"]

    raw_results = await asyncio.to_thread(fetch_snkrdunk_grades_sync, card_dict, target_grades)

    channel = await get_or_fetch_channel(bot, channel_id)
    if not channel:
        logger.error("[market_plugin] ❌ 找不到 PTCG 頻道 ID: %d", channel_id)
        return

    async def safe_send(content: str) -> None:
        try:
            await channel.send(content)
        except Exception as e:  # noqa: BLE001
            logger.warning("[market_plugin] ⚠️ 發送失敗: %s", e)

    title_msg = (
        f"🃏 **每日 PTCG 收藏卡片報價 ({now_str})**\n"
        "🔴 漲(-) | 🟢 跌(+) | 格式: [評級: 價格 (漲跌幅)]\n"
    )
    code_block_start = "```diff\n"
    current_msg = title_msg + code_block_start

    for result in raw_results:
        card_name = result["Card Name"]
        card_block = f"[{card_name}]\n"

        old_prices = cards_db.get(card_name, {}).get("last_prices", {})
        new_prices = {}

        for grade in target_grades:
            current_val = result.get(grade, "N/A")
            old_val = old_prices.get(grade, "N/A")
            new_prices[grade] = current_val

            if current_val == "N/A":
                card_block += f"  {grade:<7}: 暫無掛單\n"
                continue

            if old_val == "N/A" or old_val == 0:
                card_block += f"  {grade:<7}: ${current_val}\n"
            else:
                diff = current_val - old_val
                pct = (diff / old_val) * 100
                price_str = f"${current_val} "
                if diff > 0:
                    card_block += f"- {grade:<7}: {price_str:<6} (+{pct:.1f}%) ▲\n"
                elif diff < 0:
                    card_block += f"+ {grade:<7}: {price_str:<6} ({pct:.1f}%) ▼\n"
                else:
                    card_block += f"  {grade:<7}: {price_str:<6} (0.0%) -\n"

        card_block += "-----------------------------------\n"

        if card_name in cards_db:
            cards_db[card_name]["last_prices"] = new_prices

        if len(current_msg) + len(card_block) + 5 > 1900:
            current_msg += "```"
            await safe_send(current_msg)
            await asyncio.sleep(1)
            current_msg = code_block_start + card_block
        else:
            current_msg += card_block

    if current_msg not in (title_msg + code_block_start, code_block_start):
        current_msg += "```"
        await safe_send(current_msg)

    save_cards_fn(cards_db)
    logger.info("[market_plugin] ✅ 卡片報價發送完成！")
