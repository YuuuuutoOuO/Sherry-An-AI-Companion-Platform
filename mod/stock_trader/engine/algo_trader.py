"""
algo_trader.py (mod/stock_trader/engine)
=========================================
美股虛擬量化交易演算法執行引擎 (Algorithmic Trade Execution Engine)。
嚴格獨立於 LLM，負責驗證資金、手續費(0.1%)、碎股(>=0.01股)、當日買賣與交割更新。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from mod.stock_trader import config as cfg
from mod.stock_trader.domain.interfaces import ITraderRepository
from mod.stock_trader.domain.models import (
    DailySnapshot, PortfolioAccount, StockPosition, TradeAction, TradeRecord,
)

logger = logging.getLogger(__name__)
_TZ_TW = timezone(timedelta(hours=8))


class AlgoTrader:
    """量化下單與資產精算引擎。"""

    def __init__(self, repository: ITraderRepository) -> None:
        self.repo = repository

    def update_prices(self, price_map: Dict[str, float]) -> PortfolioAccount:
        """更新所有持倉之最新市價並儲存。

        Args:
            price_map: 股票代碼 -> 最新成交價字典

        Returns:
            更新後的 PortfolioAccount 實例
        """
        account = self.repo.load_portfolio()
        updated_positions: Dict[str, StockPosition] = {}

        for ticker, pos in account.positions.items():
            current_p = price_map.get(ticker, pos.current_price)
            if current_p <= 0:
                current_p = pos.current_price
            updated_positions[ticker] = StockPosition(
                ticker=ticker,
                shares=pos.shares,
                avg_cost=pos.avg_cost,
                current_price=current_p,
            )

        account.positions = updated_positions
        self.repo.save_portfolio(account)
        return account

    def execute_order(
        self,
        ticker: str,
        action: TradeAction | str,
        current_price: float,
        target_amount_usd: Optional[float] = None,
        target_shares: Optional[float] = None,
        target_shares_ratio: Optional[float] = None,
        reason: str = "主力模型策略下單",
    ) -> Tuple[bool, str, Optional[TradeRecord]]:
        """執行演算法下單。

        Args:
            ticker: 股票代碼 (例: NVDA)
            action: 交易動作 (BUY / SELL / HOLD)
            current_price: 最新成交價 (USD)
            target_amount_usd: 欲投入之美金金額 (用於 BUY)
            target_shares: 欲買賣之股數 (用於 BUY/SELL)
            target_shares_ratio: 欲賣出之持倉比例 (0.0~1.0，例: 0.5 賣出一半)
            reason: 決策理由

        Returns:
            Tuple[是否成功, 訊息字串, 交易紀錄物件(若成功)]
        """
        if current_price <= 0:
            return False, f"標的 {ticker} 現價無效 ({current_price})", None

        if hasattr(action, "value"):
            act_str = str(action.value).upper()
        else:
            act_str = str(action).upper().replace("TRADEACTION.", "")

        if act_str not in (TradeAction.BUY.value, TradeAction.SELL.value):
            return False, f"標的 {ticker} 選擇觀望 (HOLD)", None

        account = self.repo.load_portfolio()
        now_str = datetime.now(_TZ_TW).strftime("%Y-%m-%d %H:%M:%S")
        trade_id = f"TRD-{uuid.uuid4().hex[:8].upper()}"

        if act_str == TradeAction.BUY.value:
            # ------------------------------------------------------------------
            # 買入演算
            # ------------------------------------------------------------------
            # 計算可下單股數
            shares = 0.0
            if target_shares and target_shares > 0:
                shares = round(target_shares, 4)
            elif target_amount_usd and target_amount_usd > 0:
                shares = round(target_amount_usd / current_price, 4)
            else:
                return False, f"買入 {ticker} 未指定有效金額或股數", None

            if shares < cfg.MIN_SHARES:
                return False, f"買入股數 {shares} 低於最小交易門檻 ({cfg.MIN_SHARES} 股)", None

            # 計算交易總額與 0.1% 手續費
            trade_value = round(shares * current_price, 2)
            fee = round(trade_value * cfg.FEE_RATE, 2)
            total_cost = round(trade_value + fee, 2)

            # 現金查驗與調整
            if total_cost > account.cash:
                # 嘗試調整為當前現金最大可買股數
                max_cash_for_value = account.cash / (1.0 + cfg.FEE_RATE)
                shares = round(max_cash_for_value / current_price, 4)
                if shares < cfg.MIN_SHARES:
                    return False, f"現金不足 (可用 ${account.cash:.2f}，需要 ${total_cost:.2f})", None
                trade_value = round(shares * current_price, 2)
                fee = round(trade_value * cfg.FEE_RATE, 2)
                total_cost = round(trade_value + fee, 2)

            # 更新現金
            account.cash = round(account.cash - total_cost, 2)

            # 更新持倉
            existing_pos = account.positions.get(ticker)
            if existing_pos:
                old_shares = existing_pos.shares
                old_cost = old_shares * existing_pos.avg_cost
                new_shares = round(old_shares + shares, 4)
                new_avg_cost = round((old_cost + trade_value) / new_shares, 4)
                account.positions[ticker] = StockPosition(
                    ticker=ticker,
                    shares=new_shares,
                    avg_cost=new_avg_cost,
                    current_price=current_price,
                )
            else:
                account.positions[ticker] = StockPosition(
                    ticker=ticker,
                    shares=shares,
                    avg_cost=current_price,
                    current_price=current_price,
                )

            account.total_trades_count += 1
            self.repo.save_portfolio(account)

            record = TradeRecord(
                trade_id=trade_id,
                timestamp=now_str,
                ticker=ticker,
                action="BUY",
                shares=shares,
                execution_price=current_price,
                fee=fee,
                total_amount=total_cost,
                realized_pnl=0.0,
                reason=reason,
            )
            self.repo.append_trade(record)
            logger.info(
                "[stock_trader] 買入 %s: %.4f 股 @ $%.2f (費用: $%.2f)",
                ticker, shares, current_price, total_cost,
            )
            return True, f"成功買入 {ticker} {shares} 股 (含手續費 $%.2f)" % fee, record

        else:
            # ------------------------------------------------------------------
            # 賣出演算
            # ------------------------------------------------------------------
            existing_pos = account.positions.get(ticker)
            if not existing_pos or existing_pos.shares < cfg.MIN_SHARES:
                return False, f"未持有 {ticker} 或持倉不足可供賣出", None

            shares_to_sell = existing_pos.shares
            if target_shares and 0 < target_shares < shares_to_sell:
                shares_to_sell = round(target_shares, 4)
            elif target_shares_ratio and 0.0 < target_shares_ratio < 1.0:
                shares_to_sell = round(existing_pos.shares * target_shares_ratio, 4)

            if shares_to_sell < cfg.MIN_SHARES:
                return False, f"賣出股數低於門檻 ({cfg.MIN_SHARES} 股)", None

            trade_value = round(shares_to_sell * current_price, 2)
            fee = round(trade_value * cfg.FEE_RATE, 2)
            net_payout = round(trade_value - fee, 2)

            # 計算已實現損益
            cost_basis = round(shares_to_sell * existing_pos.avg_cost, 2)
            realized_pnl = round(net_payout - cost_basis, 2)

            # 更新現金與帳戶統計
            account.cash = round(account.cash + net_payout, 2)
            account.realized_pnl = round(account.realized_pnl + realized_pnl, 2)
            account.total_trades_count += 1
            if realized_pnl > 0:
                account.winning_trades_count += 1

            # 更新持倉
            rem_shares = round(existing_pos.shares - shares_to_sell, 4)
            if rem_shares < cfg.MIN_SHARES:
                del account.positions[ticker]
            else:
                account.positions[ticker] = StockPosition(
                    ticker=ticker,
                    shares=rem_shares,
                    avg_cost=existing_pos.avg_cost,
                    current_price=current_price,
                )

            self.repo.save_portfolio(account)

            record = TradeRecord(
                trade_id=trade_id,
                timestamp=now_str,
                ticker=ticker,
                action="SELL",
                shares=shares_to_sell,
                execution_price=current_price,
                fee=fee,
                total_amount=net_payout,
                realized_pnl=realized_pnl,
                reason=reason,
            )
            self.repo.append_trade(record)
            logger.info(
                "[stock_trader] 賣出 %s: %.4f 股 @ $%.2f (實現損益: $%.2f)",
                ticker, shares_to_sell, current_price, realized_pnl,
            )
            return True, f"成功賣出 {ticker} {shares_to_sell} 股 (獲利/虧損: $%.2f)" % realized_pnl, record

    def record_daily_snapshot(self) -> DailySnapshot:
        """拍攝並記錄當日資產歷史快照 (用於 Web UI 走勢折線圖)。"""
        account = self.repo.load_portfolio()
        history = self.repo.load_daily_history()

        today_str = datetime.now(_TZ_TW).strftime("%Y-%m-%d")
        total_val = account.get_total_asset_value()
        total_pnl = account.get_total_pnl()
        return_rate_pct = account.get_total_pnl_pct()

        # 當日損益與昨日快照比較
        day_pnl = 0.0
        if history:
            prev_snapshot = history[-1]
            if prev_snapshot.date != today_str:
                day_pnl = round(total_val - prev_snapshot.total_asset_value, 2)
            else:
                # 若今天已經有舊快照，與前天比較
                if len(history) > 1:
                    day_pnl = round(total_val - history[-2].total_asset_value, 2)

        snapshot = DailySnapshot(
            date=today_str,
            total_asset_value=total_val,
            cash=account.cash,
            total_pnl=total_pnl,
            return_rate_pct=return_rate_pct,
            day_pnl=day_pnl,
        )
        self.repo.save_daily_snapshot(snapshot)
        return snapshot
