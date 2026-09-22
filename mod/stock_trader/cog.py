"""
cog.py (mod/stock_trader)
===========================
雪莉美股虛擬量化交易模組 — Discord Cog 與背景排程控制器。
自動於美股交易時段 (23:00 ~ 04:00) 運作，並於 03:50 強制看盤。
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import date, datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks

from mod.market_plugin.data_fetcher import DataFetcher
from mod.stock_trader import config as cfg
from mod.stock_trader.engine.algo_trader import AlgoTrader
from mod.stock_trader.engine.strategist import TradeStrategist
from mod.stock_trader.repository import StockRepository

logger = logging.getLogger(__name__)
_TZ_TW = timezone(timedelta(hours=8))


class StockTraderCog(commands.Cog, name="Sherry Stock Trader"):
    """雪莉美股虛擬量化交易 Cog。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.repo = StockRepository()
        self.algo = AlgoTrader(self.repo)
        self.fetcher = DataFetcher()
        self.strategist = TradeStrategist(bot=bot)

        self._last_eval_time: Optional[datetime] = None
        self._next_check_interval_min: int = random.randint(90, 120)
        self._forced_today: bool = False  # 當日 03:50 是否已強制看盤標記
        self._warmup_today: bool = False  # 當日 22:00 是否已預熱標記
        self._holiday_pause_until: Optional[datetime] = None  # 例外休市 12 小時暫停時間點
        self._last_indicators_hash: Optional[str] = None  # 上次行情數據特徵碼

        # 將本 cog 注入到 bot 實例，方便 Web UI 直接調用
        setattr(bot, "_stock_trader_cog", self)

        logger.info("[stock_trader] StockTraderCog 初始化完成 ($10,000 虛擬本金已到位)")

    async def cog_load(self) -> None:
        self._trading_loop.start()
        logger.info("[stock_trader] 美股巡邏監控迴圈已啟動（每分鐘巡邏一次）")

    async def cog_unload(self) -> None:
        self._trading_loop.cancel()
        logger.info("[stock_trader] 美股巡邏監控迴圈已停止")

    # -----------------------------------------------------------------------
    # 觀察標的取得
    # -----------------------------------------------------------------------
    def get_watch_tickers(self) -> List[str]:
        """讀取 market_plugin 中的追蹤標的。"""
        try:
            if hasattr(self.bot, "_market_watchlists") and callable(self.bot._market_watchlists):
                wlist = self.bot._market_watchlists()
                raw = wlist.get("raw_list", []) or wlist.get("ai_list", [])
                if raw:
                    return [t for t in raw if not t.endswith(".TW")]  # 只過濾美股
        except Exception as e:
            logger.warning("[stock_trader] 讀取 market_plugin 標的失敗，使用預設美股標的: %s", e)

        return ["NVDA", "AAPL", "MSFT", "GOOG", "AMZN", "META", "TSLA", "TSM", "AVGO", "AMD"]

    # -----------------------------------------------------------------------
    # 背景時段與看盤迴圈
    # -----------------------------------------------------------------------
    def is_in_trading_hours(self, now: datetime) -> bool:
        """判斷目前時間是否落在美股交易時段 (23:00 ~ 04:00 台灣時間，且美東非週末與非休市國定假日)。"""
        # 0. 檢查例外休市 12 小時暫停標記
        if self._holiday_pause_until:
            if now < self._holiday_pause_until:
                return False
            else:
                self._holiday_pause_until = None

        hour = now.hour
        if not (hour >= cfg.TRADING_START_HOUR or hour < cfg.TRADING_END_HOUR):
            return False

        try:
            ny_dt = now.astimezone(ZoneInfo("America/New_York"))
            # 1. 檢查美東時間是否為週末 (5 = 週六, 6 = 週日)
            if ny_dt.weekday() in (5, 6):
                return False

            # 2. 檢查美國股市官方國定休假日 (NYSE/NASDAQ Holidays)
            if self._is_us_stock_holiday(ny_dt.date()):
                return False
        except Exception as e:
            logger.warning("[stock_trader] 美時區假日驗證異常，退回基礎時段驗證: %s", e)

        return True

    @staticmethod
    def _get_easter_sunday(year: int) -> date:
        """計算指定年份之復活節日期 (Meeus/Jones/Butcher演算法)。"""
        a = year % 19
        b = year // 100
        c = year % 100
        d = b // 4
        e = b % 4
        f = (b + 8) // 25
        g = (b - f + 1) // 3
        h = (19 * a + b - d - g + 15) % 30
        i = c // 4
        k = c % 4
        ll = (32 + 2 * e + 2 * i - h - k) % 7
        m = (a + 11 * h + 22 * ll) // 451
        month = (h + ll - 7 * m + 114) // 31
        day = ((h + ll - 7 * m + 114) % 31) + 1
        return date(year, month, day)

    @classmethod
    def _is_us_stock_holiday(cls, d: date) -> bool:
        """判斷美東日期是否為 NYSE / NASDAQ 官方休市國定假日 (含補休)。"""
        year = d.year
        month = d.month
        day = d.day
        weekday = d.weekday()  # 0=Mon, ..., 6=Sun

        # 1. 元旦 (Jan 1, 周日補休Jan 2)
        if month == 1 and (day == 1 or (day == 2 and weekday == 0)):
            return True

        # 2. 馬丁路德金紀念日 (1月第3個週一)
        if month == 1 and weekday == 0 and 15 <= day <= 21:
            return True

        # 3. 總統日 (2月第3個週一)
        if month == 2 and weekday == 0 and 15 <= day <= 21:
            return True

        # 4. 耶穌受難日 (Good Friday)
        good_friday = cls._get_easter_sunday(year) - timedelta(days=2)
        if d == good_friday:
            return True

        # 5. 陣亡將士紀念日 (5月最後一個週一)
        if month == 5 and weekday == 0 and day >= 25:
            return True

        # 6. 六月節 (Jun 19, 周日補休Jun 20)
        if month == 6 and (day == 19 or (day == 20 and weekday == 0)):
            return True

        # 7. 獨立紀念日 (Jul 4, 周日補休Jul 5, 周六提前週五Jul 3)
        if month == 7 and (day == 4 or (day == 5 and weekday == 0) or (day == 3 and weekday == 4)):
            return True

        # 8. 勞動節 (9月第1個週一)
        if month == 9 and weekday == 0 and day <= 7:
            return True

        # 9. 感恩節 (11月第4個週四)
        if month == 11 and weekday == 3 and 22 <= day <= 28:
            return True

        # 10. 聖誕節 (Dec 25, 周日補休Dec 26, 周六提前週五Dec 24)
        if month == 12 and (day == 25 or (day == 26 and weekday == 0) or (day == 24 and weekday == 4)):
            return True

        return False

    @tasks.loop(minutes=1)
    async def _trading_loop(self) -> None:
        """每分鐘檢查一次交易時段、22:00 開盤預熱與 03:50 強制看盤點。"""
        now = datetime.now(_TZ_TW)

        # 1. 22:00 預熱基準檢查 (非交易時間，僅預先抓取行情數據 Hash 作為基準，不發起雪莉交易)
        if now.hour == 22 and now.minute == 0:
            if not self._warmup_today:
                self._warmup_today = True
                await self.run_pre_market_warmup()
        elif now.hour != 22:
            self._warmup_today = False  # 重置預熱標記

        # 2. 非交易時段 (23:00 ~ 04:00 以外) 直接跳過
        if not self.is_in_trading_hours(now):
            self._forced_today = False  # 重置日間標記
            return

        is_force_point = (now.hour == cfg.FORCE_EVAL_HOUR and now.minute == cfg.FORCE_EVAL_MINUTE)

        # 3. 檢查是否達到 03:50 強制看盤點
        if is_force_point and not self._forced_today:
            logger.info("[stock_trader] ⏰ 觸發 03:50 AM 美股臨關盤強制看盤！")
            self._forced_today = True
            await self.run_evaluation_cycle(trigger_type="FORCE_CLOSE_EVAL")
            return

        # 4. 檢查常規 90~120 分鐘定時看盤
        if self._last_eval_time is None:
            # 剛啟動首度觸發
            await self.run_evaluation_cycle(trigger_type="ROUTINE")
        else:
            elapsed_min = (now - self._last_eval_time).total_seconds() / 60.0
            if elapsed_min >= self._next_check_interval_min:
                await self.run_evaluation_cycle(trigger_type="ROUTINE")

    async def run_pre_market_warmup(self) -> None:
        """晚上 22:00 美股開盤預熱數據抓取 (僅建立數據 Hash 基準，雪莉完全不進行交易與 LLM 思考)。"""
        try:
            now = datetime.now(_TZ_TW)
            ny_dt = now.astimezone(ZoneInfo("America/New_York"))
            if ny_dt.weekday() in (5, 6) or self._is_us_stock_holiday(ny_dt.date()):
                return  # 週末或節假日跳過預熱

            tickers = self.get_watch_tickers()
            loop = asyncio.get_running_loop()
            indicators = await loop.run_in_executor(
                None, self.fetcher.get_stock_info_raw, tickers
            )
            if indicators:
                self._last_indicators_hash = "|".join(
                    f"{item['ticker']}:{item['close']}:{item.get('day_pct', 0)}" for item in indicators
                )
                logger.info(
                    "[stock_trader] ⏱️ 22:00 美股開盤行情數據預熱完成 (Hash 基準已建立，不發起雪莉交易)"
                )
        except Exception as e:
            logger.warning("[stock_trader] 22:00 預熱抓取數據失敗: %s", e)

    @_trading_loop.before_loop
    async def _before_loop(self) -> None:
        await self.bot.wait_until_ready()

    # -----------------------------------------------------------------------
    # 評估與量化交易執行循環
    # -----------------------------------------------------------------------
    async def run_evaluation_cycle(self, trigger_type: str = "ROUTINE") -> Dict[str, Any]:
        """執行一次完整的觀察、AI 思考與演算法交易下單循環。

        Args:
            trigger_type: ROUTINE / FORCE_CLOSE_EVAL / MANUAL

        Returns:
            Dict 包含評估結果與交易紀錄
        """
        now = datetime.now(_TZ_TW)
        self._last_eval_time = now
        self._next_check_interval_min = random.randint(90, 120)

        tickers = self.get_watch_tickers()
        logger.info("[stock_trader] 🔍 啟動看盤評估 (%s, 標的數: %d)", trigger_type, len(tickers))

        # 1. 抓取行情與技術指標 (在 executor 中執行避免阻塞)
        loop = asyncio.get_running_loop()
        indicators = await loop.run_in_executor(
            None, self.fetcher.get_stock_info_raw, tickers
        )
        if not indicators:
            logger.warning("[stock_trader] 抓取指標數據為空，中止本次評估")
            return {"status": "error", "message": "指標抓取失敗"}

        # 比對最新行情特徵碼 (例外/突發休市保險)
        current_hash = "|".join(f"{item['ticker']}:{item['close']}:{item.get('day_pct', 0)}" for item in indicators)
        if trigger_type == "ROUTINE" and self._last_indicators_hash and current_hash == self._last_indicators_hash:
            pause_end = now + timedelta(hours=12)
            self._holiday_pause_until = pause_end
            logger.warning(
                "[stock_trader] ⚠️ 偵測到美股行情數據完全無變動 (研判為例外/突發休市)，啟動保險機制：自動暫停看盤 12 小時 (至 %s)",
                pause_end.strftime("%Y-%m-%d %H:%M:%S"),
            )
            return {"status": "holiday_paused", "message": "偵測到例外休市，自動暫停看盤 12 小時"}

        self._last_indicators_hash = current_hash

        # 建立最新價格對應表
        price_map: Dict[str, float] = {}
        for item in indicators:
            price_map[item["ticker"]] = item["close"]

        # 2. 更新持倉市價
        account = self.algo.update_prices(price_map)

        # 3. 呼叫主力模型思考與建議
        thought_log, json_actions = await self.strategist.analyze_and_decide(
            account, indicators, trigger_type
        )
        self.repo.append_thought(thought_log)

        # 4. 演算法執行下單 (Algorithmic Trading) - 賣出優先 (Sell-First) 釋放流動性
        executed_trades = []
        sell_actions = [a for a in json_actions if a.get("action", "").upper() == "SELL"]
        buy_actions = [a for a in json_actions if a.get("action", "").upper() == "BUY"]

        # 先執行所有賣出動作
        for action_item in sell_actions:
            tk = action_item.get("ticker", "")
            target_sh = action_item.get("target_shares")
            target_ratio = action_item.get("target_shares_ratio")
            reason = action_item.get("reason", "主力模型賣出決策")

            if tk not in price_map:
                continue

            current_p = price_map[tk]
            success, msg, trade_rec = self.algo.execute_order(
                ticker=tk,
                action="SELL",
                current_price=current_p,
                target_shares=target_sh,
                target_shares_ratio=target_ratio,
                reason=reason,
            )
            if success and trade_rec:
                executed_trades.append(trade_rec)

        # 賣出完成後，根據最新可用現金執行買入動作
        unallocated_buys = [b for b in buy_actions if not b.get("target_amount_usd") and not b.get("target_shares")]
        for action_item in buy_actions:
            tk = action_item.get("ticker", "")
            target_usd = action_item.get("target_amount_usd")
            target_sh = action_item.get("target_shares")
            reason = action_item.get("reason", "主力模型買入決策")

            if tk not in price_map:
                continue

            # 若未指定買入金額/股數，依剩餘現金動態分配
            if target_usd is None and target_sh is None:
                current_acc = self.repo.load_portfolio()
                if current_acc.cash > 10.0 and unallocated_buys:
                    target_usd = round(current_acc.cash / len(unallocated_buys), 2)
                else:
                    target_usd = 100.0

            current_p = price_map[tk]
            success, msg, trade_rec = self.algo.execute_order(
                ticker=tk,
                action="BUY",
                current_price=current_p,
                target_amount_usd=target_usd,
                target_shares=target_sh,
                reason=reason,
            )
            if success and trade_rec:
                executed_trades.append(trade_rec)

        # 5. 推送最新交易紀錄至指定 Discord 頻道
        if executed_trades:
            await self.announce_trades_to_discord(executed_trades)

        # 6. 紀錄每日資產快照 (用於 Web UI 折線圖)
        snapshot = self.algo.record_daily_snapshot()

        logger.info(
            "[stock_trader] ✅ 評估完成！總資產: $%.2f, 成交: %d 筆",
            snapshot.total_asset_value, len(executed_trades),
        )

        return {
            "status": "success",
            "trigger_type": trigger_type,
            "total_asset_value": snapshot.total_asset_value,
            "executed_trades_count": len(executed_trades),
            "thought_summary": thought_log.decision_summary,
        }

    async def announce_trades_to_discord(self, trades: List[Any]) -> None:
        """若有設定 TRADE_ANNOUNCE_CHANNEL_ID，將成交紀錄自動推送至 Discord 頻道。"""
        if not cfg.TRADE_ANNOUNCE_CHANNEL_ID or not self.bot:
            return

        try:
            channel = self.bot.get_channel(cfg.TRADE_ANNOUNCE_CHANNEL_ID)
            if not channel:
                try:
                    channel = await self.bot.fetch_channel(cfg.TRADE_ANNOUNCE_CHANNEL_ID)
                except Exception:
                    channel = None

            if not channel:
                logger.warning(
                    "[stock_trader] ⚠️ 無法存取指定的交易通報 Discord 頻道 ID: %s",
                    cfg.TRADE_ANNOUNCE_CHANNEL_ID,
                )
                return

            for t in trades:
                color = 0xFF4444 if t.action == "BUY" else 0x00FF88
                act_emoji = "🟢 買入 (BUY)" if t.action == "BUY" else "🔴 賣出 (SELL)"
                pnl_str = (
                    f"+${t.realized_pnl:.2f} USD"
                    if t.realized_pnl >= 0
                    else f"-${abs(t.realized_pnl):.2f} USD"
                )

                embed = discord.Embed(
                    title="📈【名偵探橘雪莉 ‧ 美股虛擬交易通報】",
                    description=f"**{act_emoji}** 標的: **`{t.ticker}`**",
                    color=color,
                    timestamp=datetime.now(timezone.utc),
                )
                embed.add_field(name="💵 成交單價", value=f"`${t.execution_price:.2f} USD`", inline=True)
                embed.add_field(name="📦 成交股數", value=f"`{t.shares:.4f} 股`", inline=True)
                embed.add_field(name="💰 成交總額", value=f"`${t.total_amount:.2f} USD`", inline=True)
                embed.add_field(name="🏷️ 手續費 (0.1%)", value=f"`${t.fee:.2f} USD`", inline=True)

                if t.action == "SELL":
                    embed.add_field(name="📊 實現損益", value=f"`{pnl_str}`", inline=True)

                embed.add_field(name="💡 雪莉下單理由", value=f"*{t.reason}*", inline=False)
                embed.set_footer(text="橘雪莉虛擬量化交易系統 ‧ 獨立 Mod 通報")

                if hasattr(channel, "send"):
                    await channel.send(embed=embed)

        except Exception as e:
            logger.error("[stock_trader] ❌ 推送交易紀錄至 Discord 頻道失敗: %s", e)

    # -----------------------------------------------------------------------
    # Discord Commands
    # -----------------------------------------------------------------------
    @commands.command(name="stock_status")
    async def cmd_stock_status(self, ctx: commands.Context[Any]) -> None:
        """查詢雪莉美股虛擬帳戶狀態。"""
        acc = self.repo.load_portfolio()
        embed = discord.Embed(
            title="📈 橘雪莉 美股虛擬投資組合",
            color=0x00E5FF,
            timestamp=datetime.now(_TZ_TW),
        )
        embed.add_field(name="💵 可用現金", value=f"${acc.cash:,.2f} USD", inline=True)
        embed.add_field(name="💰 總資產金額", value=f"${acc.get_total_asset_value():,.2f} USD", inline=True)
        embed.add_field(name="📊 累積盈餘率", value=f"{acc.get_total_pnl_pct():+.2f}% (${acc.get_total_pnl():+,.2f})", inline=True)
        embed.add_field(name="🎯 交易勝率", value=f"{acc.get_win_rate_pct():.1f}% ({acc.winning_trades_count}/{acc.total_trades_count})", inline=True)

        if acc.positions:
            pos_str = ""
            for t, p in acc.positions.items():
                pos_str += f"• **{t}**: {p.shares:.2f}股 | 均價 ${p.avg_cost:.2f} | 損益 {p.unrealized_pnl_pct:+.2f}%\n"
            embed.add_field(name="📦 當前持倉", value=pos_str, inline=False)
        else:
            embed.add_field(name="📦 當前持倉", value="(無持倉，等待名偵探雪莉出手)", inline=False)

        embed.set_footer(text="數據儲存於 ./data/sherry_stock/")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StockTraderCog(bot))
