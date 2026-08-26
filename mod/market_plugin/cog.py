"""
cog.py  (mod/market_plugin)
=============================
Market & PTCG Tracker — 主要 Discord Cog。

同時支援：
  - 前綴指令 (!add, !remove, !watchlist, !raw, !addcard, !removecard, !listcard, !updatecard)
  - Discord 斜線指令 (/market_add, /market_remove, /market_list, /market_raw,
                       /card_add, /card_remove, /card_list, /card_update)

排程任務（discord.ext.tasks）：
  - 07:00  早報驗收
  - 08:00  技術指標晨報
  - 21:30  晚間 AI 分析
  - 每10分  ETF 溢折價監控（台股開盤時間）
  - 10:00  PTCG 每日卡價

所有設定讀取自本模組的 config.py，完全不依賴雪莉主程式的 settings.py。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

from mod.market_plugin import config as cfg
from mod.market_plugin.ai_analyst import AIAnalyst
from mod.market_plugin.card_scraper import fetch_snkrdunk_grades_sync
from mod.market_plugin.data_fetcher import DataFetcher
from mod.market_plugin.repository import Database, MarketRepository
from mod.market_plugin.reports.card_report import run_card_daily_report
from mod.market_plugin.reports.daily_raw import run_daily_raw_report
from mod.market_plugin.reports.etf_monitor import check_etf_premium
from mod.market_plugin.reports.evening_ai import run_evening_ai_report
from mod.market_plugin.reports.morning_verify import run_morning_verify_report
from mod.market_plugin.visualizer import Visualizer

TAIWAN_TZ = timezone(timedelta(hours=8))

logger = logging.getLogger(__name__)


class MarketCog(commands.Cog, name="Market & PTCG"):
    """股票市場分析與 PTCG 卡片價格追蹤模組。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.fetcher = DataFetcher()
        self.analyst = AIAnalyst()
        self.db = Database()
        self.visualizer = Visualizer()
        self._last_triggered: dict[str, str] = {}
        self._last_etf_trigger_minute: int | None = None

        # 注入到 bot，讓 reports 子模組可以呼叫
        setattr(bot, "_market_watchlists", self._load_watchlists)

        logger.info("[market_plugin] MarketCog 初始化完成")

    # =========================================================================
    # JSON 讀寫工具
    # =========================================================================
    def _load_watchlists(self) -> dict[str, list[str]]:
        path: Path = cfg.STOCKS_JSON_PATH
        if not path.exists():
            default = {
                "ai_list": ["NVDA", "AAPL", "MSFT", "GOOG", "AMZN", "META", "TSLA", "TSM", "AVGO", "AMD"],
                "raw_list": ["NVDA", "AAPL", "MSFT", "TSLA", "AMD", "GOOG", "META", "AMZN", "TSM", "AVGO"],
                "etf_list": ["0050.TW", "0056.TW", "00878.TW"],
            }
            self._save_watchlists(default)
            return default
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            for key in ("ai_list", "raw_list", "etf_list"):
                if key not in data:
                    data[key] = []
                else:
                    data[key] = [t.replace(".B", "-B").replace(".A", "-A") for t in data[key]]
            return dict(data)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            logger.error("[market_plugin] 讀取 stocks.json 失敗: %s", e)
            return {"ai_list": [], "raw_list": [], "etf_list": []}

    def _save_watchlists(self, data: dict[str, list[str]]) -> bool:
        try:
            with cfg.STOCKS_JSON_PATH.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            return True
        except (OSError, json.JSONDecodeError, ValueError) as e:
            logger.error("[market_plugin] 儲存 stocks.json 失敗: %s", e)
            return False

    def _load_cards(self) -> dict[str, Any]:
        if not cfg.CARDS_JSON_PATH.exists():
            self._save_cards({})
            return {}
        try:
            with cfg.CARDS_JSON_PATH.open("r", encoding="utf-8") as f:
                return dict(json.load(f))
        except (OSError, json.JSONDecodeError, ValueError) as e:
            logger.error("[market_plugin] 讀取 cards.json 失敗: %s", e)
            return {}

    def _save_cards(self, data: dict[str, Any]) -> bool:
        try:
            with cfg.CARDS_JSON_PATH.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            return True
        except (OSError, json.JSONDecodeError, ValueError) as e:
            logger.error("[market_plugin] 儲存 cards.json 失敗: %s", e)
            return False

    # =========================================================================
    # Cog 生命週期與排程
    # =========================================================================
    async def cog_load(self) -> None:
        """Cog 載入後啟動排程器。"""
        self._market_schedule_loop.start()
        logger.info(
            "[market_plugin] ✅ 排程已啟動 (discord.ext.tasks) | 股票頻道: %s | PTCG頻道: %s",
            cfg.STOCK_REPORT_CHANNEL_ID,
            cfg.PTCG_REPORT_CHANNEL_ID,
        )

    async def cog_unload(self) -> None:
        """Cog 卸載時終止排程任務。"""
        self._market_schedule_loop.cancel()
        logger.info("[market_plugin] 排程器已關閉")

    @tasks.loop(minutes=1)
    async def _market_schedule_loop(self) -> None:
        """定期每分鐘檢查是否到達市場報價排程時間。"""
        await self.bot.wait_until_ready()
        now_tw = datetime.now(TAIWAN_TZ)
        today_str = now_tw.strftime("%Y-%m-%d")

        # 每日 07:00 早報驗收
        if (
            now_tw.hour == cfg.MORNING_VERIFY_HOUR
            and now_tw.minute == cfg.MORNING_VERIFY_MINUTE
            and self._last_triggered.get("morning_verify") != today_str
            and cfg.MORNING_VERIFY == True
        ):
            self._last_triggered["morning_verify"] = today_str
            bot_loop = self.bot.loop
            bot_loop.create_task(self._task_morning_verify())

        # 每日 08:00 技術指標晨報
        if (
            now_tw.hour == cfg.DAILY_RAW_HOUR
            and now_tw.minute == cfg.DAILY_RAW_MINUTE
            and self._last_triggered.get("daily_raw") != today_str
            and cfg.DAILY_RAW == True
        ):
            self._last_triggered["daily_raw"] = today_str
            bot_loop = self.bot.loop
            bot_loop.create_task(self._task_daily_raw())

        # 每日 10:00 PTCG 每日卡價
        if (
            now_tw.hour == cfg.CARD_REPORT_HOUR
            and now_tw.minute == cfg.CARD_REPORT_MINUTE
            and self._last_triggered.get("card_report") != today_str
            and cfg.CARD_REPORT == True
        ):
            self._last_triggered["card_report"] = today_str
            bot_loop = self.bot.loop
            bot_loop.create_task(self._task_card_report())

        # 每日 21:30 晚間 AI 分析
        if (
            now_tw.hour == cfg.EVENING_AI_HOUR
            and now_tw.minute == cfg.EVENING_AI_MINUTE
            and self._last_triggered.get("evening_ai") != today_str
            and cfg.EVENING_AI == True
        ):
            self._last_triggered["evening_ai"] = today_str
            bot_loop = self.bot.loop
            bot_loop.create_task(self._task_evening_ai())

        # 台股開盤期間 (09:00 ~ 13:30) 每 10 分鐘 ETF 溢折價監控
        if (
            9 <= now_tw.hour < 14
            and now_tw.minute % cfg.ETF_MONITOR_INTERVAL_MINUTES == 0
            and self._last_etf_trigger_minute != now_tw.minute
            and cfg.ETF_MONITOR_INTERVAL == True
        ):
            self._last_etf_trigger_minute = now_tw.minute
            bot_loop = self.bot.loop
            bot_loop.create_task(self._task_etf_monitor())

    # =========================================================================
    # 排程任務
    # =========================================================================
    async def _task_morning_verify(self) -> None:
        await run_morning_verify_report(self.bot, self.fetcher, self.db, cfg.STOCK_REPORT_CHANNEL_ID)

    async def _task_daily_raw(self, target_channel_id: int | None = None) -> None:
        await run_daily_raw_report(
            self.bot, self.fetcher, target_channel_id or cfg.STOCK_REPORT_CHANNEL_ID
        )

    async def _task_evening_ai(self) -> None:
        await run_evening_ai_report(
            self.bot, self.fetcher, self.analyst, self.db, cfg.STOCK_REPORT_CHANNEL_ID
        )

    async def _task_etf_monitor(self) -> None:
        await check_etf_premium(self.bot, cfg.STOCK_REPORT_CHANNEL_ID, self._load_watchlists)

    async def _task_card_report(self, target_channel_id: int | None = None) -> None:
        await run_card_daily_report(
            self.bot, fetch_snkrdunk_grades_sync,
            self._load_cards, self._save_cards,
            target_channel_id or cfg.PTCG_REPORT_CHANNEL_ID,
        )

    # =========================================================================
    # 股票指令 — 前綴
    # =========================================================================
    @commands.command(name="raw", help="手動觸發技術指標報告")
    async def prefix_raw(self, ctx: commands.Context) -> None:
        await ctx.message.add_reaction("⏳")
        await ctx.send("📊 正在抓取最新技術指標，請稍候...")
        try:
            await self._task_daily_raw(target_channel_id=ctx.channel.id)
            if self.bot.user:
                await ctx.message.remove_reaction("⏳", self.bot.user)
            await ctx.message.add_reaction("✅")
        except Exception as e:  # noqa: BLE001
            await ctx.send(f"❌ 報告生成失敗: {e}")

    @commands.command(name="add", help="加入股票追蹤: !add [ai/raw/etf] [代號]")
    async def prefix_add(self, ctx: commands.Context, list_type: str | None = None, ticker: str | None = None) -> None:
        if list_type not in ("ai", "raw", "etf") or not ticker:
            await ctx.send("❌ 格式錯誤！使用: `!add [ai/raw/etf] [代號]`")
            return
        ticker = ticker.upper().strip().replace(".B", "-B")
        data = self._load_watchlists()
        key = f"{list_type}_list"
        if ticker in data[key]:
            await ctx.send(f"⚠️ **{ticker}** 已在 {list_type} 清單中")
            return
        data[key].append(ticker)
        if self._save_watchlists(data):
            await ctx.send(f"✅ 已將 **{ticker}** 加入 {list_type} 組")

    @commands.command(name="remove", help="移除股票追蹤: !remove [ai/raw/etf] [代號]")
    async def prefix_remove(self, ctx: commands.Context, list_type: str | None = None, ticker: str | None = None) -> None:
        if list_type not in ("ai", "raw", "etf") or not ticker:
            await ctx.send("❌ 格式錯誤！使用: `!remove [ai/raw/etf] [代號]`")
            return
        ticker = ticker.upper().strip().replace(".B", "-B")
        data = self._load_watchlists()
        key = f"{list_type}_list"
        if ticker not in data[key]:
            await ctx.send(f"⚠️ {list_type} 清單中找不到 **{ticker}**")
            return
        data[key].remove(ticker)
        if self._save_watchlists(data):
            await ctx.send(f"🗑️ 已從 {list_type} 清單移除 **{ticker}**")

    @commands.command(name="watchlist", help="查看所有股票追蹤清單")
    async def prefix_watchlist(self, ctx: commands.Context) -> None:
        data = self._load_watchlists()
        msg = (
            "📋 **目前股票追蹤清單**\n"
            f"🔹 **AI 分析組**: `{', '.join(data.get('ai_list', []))}`\n"
            f"🔸 **指標晨報組**: `{', '.join(data.get('raw_list', []))}`\n"
            f"📈 **台股 ETF 組**: `{', '.join(data.get('etf_list', []))}`"
        )
        await ctx.send(msg)

    # =========================================================================
    # 股票指令 — /market 整合指令 (Choice 選單模式)
    # =========================================================================
    @app_commands.command(name="market", description="📈 股票追蹤、清單管理與即時技術指標晨報")
    @app_commands.describe(
        action="選擇執行的動作",
        list_type="清單類型 (僅在 add / remove 時需要)",
        ticker="股票代號 (僅在 add / remove 時需要，例如 NVDA 或 2330.TW)",
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="📰 即時晨報 (raw) — 手動推送最新技術指標晨報", value="raw"),
        app_commands.Choice(name="➕ 加入追蹤 (add) — 將股票加入追蹤清單", value="add"),
        app_commands.Choice(name="➖ 移除追蹤 (remove) — 從追蹤清單中移除股票", value="remove"),
        app_commands.Choice(name="📋 查看清單 (list) — 查看目前追蹤的所有股票清單", value="list"),
    ])
    @app_commands.choices(list_type=[
        app_commands.Choice(name="🤖 AI 分析組 (ai)", value="ai"),
        app_commands.Choice(name="☀️ 指標晨報組 (raw)", value="raw"),
        app_commands.Choice(name="📊 台股 ETF 組 (etf)", value="etf"),
    ])
    async def slash_market(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        list_type: app_commands.Choice[str] | None = None,
        ticker: str | None = None,
    ) -> None:
        act = action.value
        if act == "raw":
            await interaction.response.send_message("📊 正在抓取最新技術指標，請稍候...", ephemeral=False)
            try:
                await self._task_daily_raw(target_channel_id=interaction.channel_id)
            except Exception as e:  # noqa: BLE001
                await interaction.followup.send(f"❌ 報告生成失敗: {e}", ephemeral=True)

        elif act == "add":
            if not list_type or not ticker:
                await interaction.response.send_message("❌ 請提供 `list_type` (清單類型) 與 `ticker` (股票代號)！", ephemeral=True)
                return
            cleaned_ticker = ticker.upper().strip().replace(".B", "-B")
            data = self._load_watchlists()
            key = f"{list_type.value}_list"
            if cleaned_ticker in data.get(key, []):
                await interaction.response.send_message(f"⚠️ **{cleaned_ticker}** 已在 {list_type.name} 中", ephemeral=True)
                return
            data.setdefault(key, []).append(cleaned_ticker)
            if self._save_watchlists(data):
                await interaction.response.send_message(f"✅ 已將 **{cleaned_ticker}** 加入 {list_type.name}！")

        elif act == "remove":
            if not list_type or not ticker:
                await interaction.response.send_message("❌ 請提供 `list_type` (清單類型) 與 `ticker` (股票代號)！", ephemeral=True)
                return
            cleaned_ticker = ticker.upper().strip().replace(".B", "-B")
            data = self._load_watchlists()
            key = f"{list_type.value}_list"
            if cleaned_ticker not in data.get(key, []):
                await interaction.response.send_message(f"⚠️ 在 {list_type.name} 中找不到 **{cleaned_ticker}**", ephemeral=True)
                return
            data[key].remove(cleaned_ticker)
            if self._save_watchlists(data):
                await interaction.response.send_message(f"🗑️ 已從 {list_type.name} 移除 **{cleaned_ticker}**！")

        elif act == "list":
            data = self._load_watchlists()
            msg = (
                "📋 **目前股票追蹤清單**\n"
                f"🔹 **AI 分析組**: `{', '.join(data.get('ai_list', []))}`\n"
                f"🔸 **指標晨報組**: `{', '.join(data.get('raw_list', []))}`\n"
                f"📈 **台股 ETF 組**: `{', '.join(data.get('etf_list', []))}`"
            )
            await interaction.response.send_message(msg)

    # =========================================================================
    # PTCG 卡片指令 — 前綴
    # =========================================================================
    @commands.command(name="addcard", help="新增卡片追蹤: !addcard <URL> <名稱>")
    async def prefix_addcard(self, ctx: commands.Context, url: str, *, name: str) -> None:
        data = self._load_cards()
        if name in data:
            await ctx.send(f"⚠️ **{name}** 已在追蹤清單中")
            return
        data[name] = {"url": url.strip(), "last_prices": {}}
        if self._save_cards(data):
            await ctx.send(f"✅ 已將 **{name}** 加入卡片追蹤清單！")

    @prefix_addcard.error
    async def addcard_error(self, ctx: commands.Context, error: Exception) -> None:
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                "❌ **指令格式錯誤！**\n"
                "💡 **正確用法**：`!addcard <網址> <卡片名稱>`\n"
                "📝 **範例**：`!addcard https://snkrdunk.com/en/trading-cards/105574 奇樹 SAR`"
            )

    @commands.command(name="removecard", help="移除卡片追蹤: !removecard <名稱>")
    async def prefix_removecard(self, ctx: commands.Context, *, name: str) -> None:
        data = self._load_cards()
        if name not in data:
            await ctx.send(f"⚠️ 找不到名為 **{name}** 的卡片")
            return
        del data[name]
        if self._save_cards(data):
            await ctx.send(f"🗑️ 已停止追蹤 **{name}**")

    @commands.command(name="listcard", help="查看追蹤的卡片清單與最新報價")
    async def prefix_listcard(self, ctx: commands.Context) -> None:
        data = self._load_cards()
        if not data:
            await ctx.send("⚠️ 目前沒有追蹤任何卡片")
            return
        msg = "📋 **目前追蹤的卡片清單**\n"
        for name, info in data.items():
            lp = info.get("last_prices", {})
            pa = lp.get("A", "N/A")
            pp = lp.get("PSA 10", "N/A")
            pb = lp.get("BGS 9.5", "N/A")
            msg += (
                f"• **{name}** "
                f"(A: {'$'+str(pa) if pa!='N/A' else 'N/A'} | "
                f"PSA 10: {'$'+str(pp) if pp!='N/A' else 'N/A'} | "
                f"BGS 9.5: {'$'+str(pb) if pb!='N/A' else 'N/A'})\n"
            )
        await ctx.send(msg)

    @commands.command(name="updatecard", help="手動觸發卡片報價更新")
    async def prefix_updatecard(self, ctx: commands.Context) -> None:
        await ctx.message.add_reaction("⏳")
        await ctx.send("🃏 正在啟動爬蟲潛入 SNKRDUNK 抓取最新卡價，請稍候...")
        try:
            await self._task_card_report(target_channel_id=ctx.channel.id)
            if self.bot.user:
                await ctx.message.remove_reaction("⏳", self.bot.user)
            await ctx.message.add_reaction("✅")
        except Exception as e:  # noqa: BLE001
            await ctx.send(f"❌ 卡片報價獲取失敗: {e}")

    # =========================================================================
    # PTCG 卡片指令 — /card 整合指令 (Choice 選單模式)
    # =========================================================================
    @app_commands.command(name="card", description="🃏 PTCG 卡片價格追蹤與即時行情")
    @app_commands.describe(
        action="選擇執行的動作",
        name="卡片自訂名稱 (僅在 add / remove 時需要)",
        url="SNKRDUNK 卡片頁面網址 (僅在 add 時需要)",
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="➕ 加入追蹤 (add) — 新增 PTCG 卡片追蹤", value="add"),
        app_commands.Choice(name="➖ 移除追蹤 (remove) — 移除 PTCG 卡片追蹤", value="remove"),
        app_commands.Choice(name="📋 查看清單 (list) — 查看追蹤的卡片清單與最新報價", value="list"),
        app_commands.Choice(name="🔄 更新卡價 (update) — 手動觸發卡片報價爬蟲更新", value="update"),
    ])
    async def slash_card(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        name: str | None = None,
        url: str | None = None,
    ) -> None:
        act = action.value
        if act == "add":
            if not name or not url:
                await interaction.response.send_message("❌ 請提供 `name` (卡片名稱) 與 `url` (SNKRDUNK 網址)！", ephemeral=True)
                return
            data = self._load_cards()
            if name in data:
                await interaction.response.send_message(f"⚠️ **{name}** 已在追蹤清單中", ephemeral=True)
                return
            data[name] = {"url": url.strip(), "last_prices": {}}
            if self._save_cards(data):
                await interaction.response.send_message(f"✅ 已將 **{name}** 加入卡片追蹤清單！")

        elif act == "remove":
            if not name:
                await interaction.response.send_message("❌ 請提供 `name` (卡片名稱)！", ephemeral=True)
                return
            data = self._load_cards()
            if name not in data:
                await interaction.response.send_message(f"⚠️ 找不到 **{name}**", ephemeral=True)
                return
            del data[name]
            if self._save_cards(data):
                await interaction.response.send_message(f"🗑️ 已停止追蹤 **{name}**")

        elif act == "list":
            data = self._load_cards()
            if not data:
                await interaction.response.send_message("⚠️ 目前沒有追蹤任何卡片", ephemeral=True)
                return
            msg = "📋 **目前追蹤的卡片清單**\n"
            for card_name, info in data.items():
                lp = info.get("last_prices", {})
                pa = lp.get("A", "N/A")
                pp = lp.get("PSA 10", "N/A")
                pb = lp.get("BGS 9.5", "N/A")
                msg += (
                    f"• **{card_name}** "
                    f"(A: {'$'+str(pa) if pa!='N/A' else 'N/A'} | "
                    f"PSA 10: {'$'+str(pp) if pp!='N/A' else 'N/A'} | "
                    f"BGS 9.5: {'$'+str(pb) if pb!='N/A' else 'N/A'})\n"
                )
            await interaction.response.send_message(msg)

        elif act == "update":
            await interaction.response.send_message("🃏 正在啟動爬蟲抓取最新卡價，請稍候...")
            try:
                await self._task_card_report(target_channel_id=interaction.channel_id)
            except Exception as e:  # noqa: BLE001
                await interaction.followup.send(f"❌ 卡片報價獲取失敗: {e}", ephemeral=True)


# =========================================================================
# Discord.py Extension Entry Point — 必要函式
# =========================================================================
async def setup(bot: commands.Bot) -> None:
    """動態載入 Cog 的入口，由 bot.load_extension 呼叫。"""
    cog = MarketCog(bot)
    await bot.add_cog(cog)
    logger.info("[market_plugin] ✅ MarketCog 已成功掛載到雪莉機器人")
