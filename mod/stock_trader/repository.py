"""
repository.py (mod/stock_trader)
=================================
JSON / JSONL 檔案資料持久化倉儲 (實作 ITraderRepository 介面)。
檔案均儲存於 ./data/sherry_stock/。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from mod.stock_trader import config as cfg
from mod.stock_trader.domain.interfaces import ITraderRepository
from mod.stock_trader.domain.models import (
    DailySnapshot, PortfolioAccount, StockPosition, ThoughtLog, TradeRecord,
)

logger = logging.getLogger(__name__)
_TZ_TW = timezone(timedelta(hours=8))


class StockRepository(ITraderRepository):
    """虛擬量化交易 JSON 檔案倉儲實作。"""

    def __init__(self, data_dir: Optional[Path] = None) -> None:
        self.data_dir = data_dir or cfg.STOCK_DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.portfolio_path = self.data_dir / "portfolio.json"
        self.daily_history_path = self.data_dir / "daily_history.json"
        self.trades_path = self.data_dir / "trades.json"
        self.thoughts_path = self.data_dir / "thoughts.jsonl"

    # ------------------------------------------------------------------
    # Portfolio
    # ------------------------------------------------------------------
    def load_portfolio(self) -> PortfolioAccount:
        if not self.portfolio_path.exists():
            now_str = datetime.now(_TZ_TW).strftime("%Y-%m-%d %H:%M:%S")
            account = PortfolioAccount(
                cash=cfg.INITIAL_CAPITAL_USD,
                initial_capital=cfg.INITIAL_CAPITAL_USD,
                positions={},
                realized_pnl=0.0,
                total_trades_count=0,
                winning_trades_count=0,
                created_at=now_str,
                updated_at=now_str,
            )
            self.save_portfolio(account)
            return account

        try:
            with self.portfolio_path.open("r", encoding="utf-8") as f:
                data = json.load(f)

            raw_positions = data.get("positions", {})
            positions: Dict[str, StockPosition] = {}
            for ticker, p_data in raw_positions.items():
                positions[ticker] = StockPosition(
                    ticker=ticker,
                    shares=float(p_data.get("shares", 0.0)),
                    avg_cost=float(p_data.get("avg_cost", 0.0)),
                    current_price=float(p_data.get("current_price", 0.0)),
                )

            return PortfolioAccount(
                cash=float(data.get("cash", cfg.INITIAL_CAPITAL_USD)),
                initial_capital=float(data.get("initial_capital", cfg.INITIAL_CAPITAL_USD)),
                positions=positions,
                realized_pnl=float(data.get("realized_pnl", 0.0)),
                total_trades_count=int(data.get("total_trades_count", 0)),
                winning_trades_count=int(data.get("winning_trades_count", 0)),
                created_at=str(data.get("created_at", "")),
                updated_at=str(data.get("updated_at", "")),
            )
        except Exception as e:
            logger.error("[stock_trader] 讀取 portfolio.json 失敗: %s", e)
            now_str = datetime.now(_TZ_TW).strftime("%Y-%m-%d %H:%M:%S")
            return PortfolioAccount(created_at=now_str, updated_at=now_str)

    def save_portfolio(self, account: PortfolioAccount) -> bool:
        try:
            account.updated_at = datetime.now(_TZ_TW).strftime("%Y-%m-%d %H:%M:%S")
            positions_dict = {}
            for ticker, pos in account.positions.items():
                positions_dict[ticker] = {
                    "ticker": pos.ticker,
                    "shares": pos.shares,
                    "avg_cost": pos.avg_cost,
                    "current_price": pos.current_price,
                    "total_cost": pos.total_cost,
                    "market_value": pos.market_value,
                    "unrealized_pnl": pos.unrealized_pnl,
                    "unrealized_pnl_pct": pos.unrealized_pnl_pct,
                }

            data = {
                "cash": account.cash,
                "initial_capital": account.initial_capital,
                "total_asset_value": account.get_total_asset_value(),
                "total_pnl": account.get_total_pnl(),
                "total_pnl_pct": account.get_total_pnl_pct(),
                "win_rate_pct": account.get_win_rate_pct(),
                "realized_pnl": account.realized_pnl,
                "total_trades_count": account.total_trades_count,
                "winning_trades_count": account.winning_trades_count,
                "created_at": account.created_at,
                "updated_at": account.updated_at,
                "positions": positions_dict,
            }

            with self.portfolio_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            logger.error("[stock_trader] 儲存 portfolio.json 失敗: %s", e)
            return False

    # ------------------------------------------------------------------
    # Daily History (Snapshots for 1-month Return Rate Chart)
    # ------------------------------------------------------------------
    def load_daily_history(self) -> List[DailySnapshot]:
        if not self.daily_history_path.exists():
            return []
        try:
            with self.daily_history_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            result: List[DailySnapshot] = []
            for item in data:
                result.append(
                    DailySnapshot(
                        date=str(item["date"]),
                        total_asset_value=float(item.get("total_asset_value", 0.0)),
                        cash=float(item.get("cash", 0.0)),
                        total_pnl=float(item.get("total_pnl", 0.0)),
                        return_rate_pct=float(item.get("return_rate_pct", 0.0)),
                        day_pnl=float(item.get("day_pnl", 0.0)),
                    )
                )
            return result
        except Exception as e:
            logger.error("[stock_trader] 讀取 daily_history.json 失敗: %s", e)
            return []

    def save_daily_snapshot(self, snapshot: DailySnapshot) -> bool:
        try:
            history = self.load_daily_history()
            # 若同日已存在快照則覆蓋，否則新增
            filtered = [s for s in history if s.date != snapshot.date]
            filtered.append(snapshot)
            filtered.sort(key=lambda x: x.date)

            # 保留最近 365 天
            filtered = filtered[-365:]

            data = [
                {
                    "date": s.date,
                    "total_asset_value": s.total_asset_value,
                    "cash": s.cash,
                    "total_pnl": s.total_pnl,
                    "return_rate_pct": s.return_rate_pct,
                    "day_pnl": s.day_pnl,
                }
                for s in filtered
            ]

            with self.daily_history_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            logger.error("[stock_trader] 儲存 daily_history.json 失敗: %s", e)
            return False

    # ------------------------------------------------------------------
    # Trades
    # ------------------------------------------------------------------
    def load_trades(self, limit: int = 100) -> List[TradeRecord]:
        if not self.trades_path.exists():
            return []
        try:
            with self.trades_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            records: List[TradeRecord] = []
            for item in data[-limit:]:
                records.append(
                    TradeRecord(
                        trade_id=str(item.get("trade_id", "")),
                        timestamp=str(item.get("timestamp", "")),
                        ticker=str(item.get("ticker", "")),
                        action=str(item.get("action", "BUY")),
                        shares=float(item.get("shares", 0.0)),
                        execution_price=float(item.get("execution_price", 0.0)),
                        fee=float(item.get("fee", 0.0)),
                        total_amount=float(item.get("total_amount", 0.0)),
                        realized_pnl=float(item.get("realized_pnl", 0.0)),
                        reason=str(item.get("reason", "")),
                    )
                )
            records.reverse()  # 最新在最前面
            return records
        except Exception as e:
            logger.error("[stock_trader] 讀取 trades.json 失敗: %s", e)
            return []

    def append_trade(self, trade: TradeRecord) -> bool:
        try:
            existing: List[Dict[str, Any]] = []
            if self.trades_path.exists():
                try:
                    with self.trades_path.open("r", encoding="utf-8") as f:
                        existing = json.load(f)
                except Exception:
                    existing = []

            trade_dict = {
                "trade_id": trade.trade_id,
                "timestamp": trade.timestamp,
                "ticker": trade.ticker,
                "action": trade.action,
                "shares": trade.shares,
                "execution_price": trade.execution_price,
                "fee": trade.fee,
                "total_amount": trade.total_amount,
                "realized_pnl": trade.realized_pnl,
                "reason": trade.reason,
            }
            existing.append(trade_dict)

            with self.trades_path.open("w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            logger.error("[stock_trader] 寫入 trades.json 失敗: %s", e)
            return False

    # ------------------------------------------------------------------
    # Thoughts
    # ------------------------------------------------------------------
    def load_thoughts(self, limit: int = 50) -> List[ThoughtLog]:
        if not self.thoughts_path.exists():
            return []
        try:
            lines: List[str] = []
            with self.thoughts_path.open("r", encoding="utf-8") as f:
                lines = f.readlines()

            logs: List[ThoughtLog] = []
            for line in lines[-limit:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                    logs.append(
                        ThoughtLog(
                            timestamp=str(item.get("timestamp", "")),
                            trigger_type=str(item.get("trigger_type", "ROUTINE")),
                            thought_process=str(item.get("thought_process", "")),
                            decision_summary=str(item.get("decision_summary", "")),
                            recommended_actions=list(item.get("recommended_actions", [])),
                        )
                    )
                except json.JSONDecodeError:
                    continue
            logs.reverse()  # 最新在最前面
            return logs
        except Exception as e:
            logger.error("[stock_trader] 讀取 thoughts.jsonl 失敗: %s", e)
            return []

    def append_thought(self, thought: ThoughtLog) -> bool:
        try:
            item = {
                "timestamp": thought.timestamp,
                "trigger_type": thought.trigger_type,
                "thought_process": thought.thought_process,
                "decision_summary": thought.decision_summary,
                "recommended_actions": thought.recommended_actions,
            }
            with self.thoughts_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
            return True
        except Exception as e:
            logger.error("[stock_trader] 寫入 thoughts.jsonl 失敗: %s", e)
            return False
