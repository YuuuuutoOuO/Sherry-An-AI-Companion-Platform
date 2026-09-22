"""
repository.py (mod/stock_trader)
=================================
SQLite 資料持久化倉儲 (實作 ITraderRepository 介面)。
資料庫檔案儲存於 ./data/sherry_stock/stock_trader.db。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from core.db import get_db_connection, db_transaction
from mod.stock_trader import config as cfg
from mod.stock_trader.domain.interfaces import ITraderRepository
from mod.stock_trader.domain.models import (
    DailySnapshot, PortfolioAccount, StockPosition, ThoughtLog, TradeRecord,
)

logger = logging.getLogger(__name__)
_TZ_TW = timezone(timedelta(hours=8))


class StockRepository(ITraderRepository):
    """虛擬量化交易 SQLite 資料庫倉儲實作。"""

    def __init__(self, data_dir: Optional[Path] = None) -> None:
        if data_dir is not None:
            self.data_dir = data_dir
            self.db_path = self.data_dir / "stock_trader.db" if self.data_dir.is_dir() else self.data_dir
        else:
            sub = cfg.STOCK_DATA_DIR / "stock_trader.db"
            root = cfg.PROJECT_ROOT / "data" / "stock_trader.db"
            if sub.exists():
                self.db_path = sub
            elif root.exists():
                self.db_path = root
            else:
                self.db_path = sub
            self.data_dir = self.db_path.parent
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._init_tables()

    def _init_tables(self) -> None:
        """初始化 SQLite 資料表與索引。"""
        with db_transaction(self.db_path) as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS portfolio (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    cash REAL NOT NULL,
                    initial_capital REAL NOT NULL,
                    realized_pnl REAL NOT NULL,
                    total_trades_count INTEGER NOT NULL,
                    winning_trades_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS positions (
                    ticker TEXT PRIMARY KEY,
                    shares REAL NOT NULL,
                    avg_cost REAL NOT NULL,
                    current_price REAL NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    trade_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    action TEXT NOT NULL,
                    shares REAL NOT NULL,
                    execution_price REAL NOT NULL,
                    fee REAL NOT NULL,
                    total_amount REAL NOT NULL,
                    realized_pnl REAL NOT NULL,
                    reason TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS daily_history (
                    date TEXT PRIMARY KEY,
                    total_asset_value REAL NOT NULL,
                    cash REAL NOT NULL,
                    total_pnl REAL NOT NULL,
                    return_rate_pct REAL NOT NULL,
                    day_pnl REAL NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS thought_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    trigger_type TEXT NOT NULL,
                    thought_process TEXT NOT NULL,
                    decision_summary TEXT NOT NULL,
                    recommended_actions_json TEXT NOT NULL
                )
            """)

    # ------------------------------------------------------------------
    # Portfolio
    # ------------------------------------------------------------------
    def load_portfolio(self) -> PortfolioAccount:
        try:
            conn = get_db_connection(self.db_path)
            cur = conn.cursor()
            cur.execute("SELECT * FROM portfolio WHERE id = 1")
            row = cur.fetchone()

            if not row:
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
                conn.close()
                self.save_portfolio(account)
                return account

            # 讀取持倉
            cur.execute("SELECT ticker, shares, avg_cost, current_price FROM positions")
            pos_rows = cur.fetchall()
            positions: Dict[str, StockPosition] = {}
            for p in pos_rows:
                positions[p["ticker"]] = StockPosition(
                    ticker=p["ticker"],
                    shares=float(p["shares"]),
                    avg_cost=float(p["avg_cost"]),
                    current_price=float(p["current_price"]),
                )
            conn.close()

            return PortfolioAccount(
                cash=float(row["cash"]),
                initial_capital=float(row["initial_capital"]),
                positions=positions,
                realized_pnl=float(row["realized_pnl"]),
                total_trades_count=int(row["total_trades_count"]),
                winning_trades_count=int(row["winning_trades_count"]),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
            )
        except Exception as e:
            logger.error("[stock_trader] 讀取 SQLite portfolio 失敗: %s", e)
            now_str = datetime.now(_TZ_TW).strftime("%Y-%m-%d %H:%M:%S")
            return PortfolioAccount(created_at=now_str, updated_at=now_str)

    def save_portfolio(self, account: PortfolioAccount) -> bool:
        try:
            account.updated_at = datetime.now(_TZ_TW).strftime("%Y-%m-%d %H:%M:%S")
            with db_transaction(self.db_path) as cur:
                cur.execute("""
                    INSERT OR REPLACE INTO portfolio (id, cash, initial_capital, realized_pnl, total_trades_count, winning_trades_count, created_at, updated_at)
                    VALUES (1, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    account.cash,
                    account.initial_capital,
                    account.realized_pnl,
                    account.total_trades_count,
                    account.winning_trades_count,
                    account.created_at,
                    account.updated_at,
                ))

                # 更新持倉
                cur.execute("DELETE FROM positions")
                for ticker, pos in account.positions.items():
                    cur.execute("""
                        INSERT INTO positions (ticker, shares, avg_cost, current_price, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        ticker,
                        pos.shares,
                        pos.avg_cost,
                        pos.current_price,
                        account.updated_at,
                    ))
            return True
        except Exception as e:
            logger.error("[stock_trader] 儲存 SQLite portfolio 失敗: %s", e)
            return False

    # ------------------------------------------------------------------
    # Daily History
    # ------------------------------------------------------------------
    def load_daily_history(self) -> List[DailySnapshot]:
        try:
            conn = get_db_connection(self.db_path)
            cur = conn.cursor()
            cur.execute("""
                SELECT date, total_asset_value, cash, total_pnl, return_rate_pct, day_pnl
                FROM daily_history
                ORDER BY date ASC
                LIMIT 365
            """)
            rows = cur.fetchall()
            conn.close()

            result: List[DailySnapshot] = []
            for r in rows:
                result.append(
                    DailySnapshot(
                        date=str(r["date"]),
                        total_asset_value=float(r["total_asset_value"]),
                        cash=float(r["cash"]),
                        total_pnl=float(r["total_pnl"]),
                        return_rate_pct=float(r["return_rate_pct"]),
                        day_pnl=float(r["day_pnl"]),
                    )
                )
            return result
        except Exception as e:
            logger.error("[stock_trader] 讀取 SQLite daily_history 失敗: %s", e)
            return []

    def save_daily_snapshot(self, snapshot: DailySnapshot) -> bool:
        try:
            with db_transaction(self.db_path) as cur:
                cur.execute("""
                    INSERT OR REPLACE INTO daily_history (date, total_asset_value, cash, total_pnl, return_rate_pct, day_pnl)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    snapshot.date,
                    snapshot.total_asset_value,
                    snapshot.cash,
                    snapshot.total_pnl,
                    snapshot.return_rate_pct,
                    snapshot.day_pnl,
                ))
            return True
        except Exception as e:
            logger.error("[stock_trader] 儲存 SQLite daily_history 失敗: %s", e)
            return False

    # ------------------------------------------------------------------
    # Trades
    # ------------------------------------------------------------------
    def load_trades(self, limit: int = 100) -> List[TradeRecord]:
        try:
            conn = get_db_connection(self.db_path)
            cur = conn.cursor()
            cur.execute("""
                SELECT trade_id, timestamp, ticker, action, shares, execution_price, fee, total_amount, realized_pnl, reason
                FROM trades
                ORDER BY timestamp DESC
                LIMIT ?
            """, (limit,))
            rows = cur.fetchall()
            conn.close()

            records: List[TradeRecord] = []
            for r in rows:
                records.append(
                    TradeRecord(
                        trade_id=str(r["trade_id"]),
                        timestamp=str(r["timestamp"]),
                        ticker=str(r["ticker"]),
                        action=str(r["action"]),
                        shares=float(r["shares"]),
                        execution_price=float(r["execution_price"]),
                        fee=float(r["fee"]),
                        total_amount=float(r["total_amount"]),
                        realized_pnl=float(r["realized_pnl"]),
                        reason=str(r["reason"]),
                    )
                )
            return records
        except Exception as e:
            logger.error("[stock_trader] 讀取 SQLite trades 失敗: %s", e)
            return []

    def append_trade(self, trade: TradeRecord) -> bool:
        try:
            with db_transaction(self.db_path) as cur:
                cur.execute("""
                    INSERT OR REPLACE INTO trades (trade_id, timestamp, ticker, action, shares, execution_price, fee, total_amount, realized_pnl, reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    trade.trade_id,
                    trade.timestamp,
                    trade.ticker,
                    trade.action,
                    trade.shares,
                    trade.execution_price,
                    trade.fee,
                    trade.total_amount,
                    trade.realized_pnl,
                    trade.reason,
                ))
            return True
        except Exception as e:
            logger.error("[stock_trader] 寫入 SQLite trades 失敗: %s", e)
            return False

    # ------------------------------------------------------------------
    # Thoughts
    # ------------------------------------------------------------------
    def load_thoughts(self, limit: int = 50) -> List[ThoughtLog]:
        try:
            conn = get_db_connection(self.db_path)
            cur = conn.cursor()
            cur.execute("""
                SELECT timestamp, trigger_type, thought_process, decision_summary, recommended_actions_json
                FROM thought_logs
                ORDER BY id DESC
                LIMIT ?
            """, (limit,))
            rows = cur.fetchall()
            conn.close()

            logs: List[ThoughtLog] = []
            for r in rows:
                rec_actions = []
                try:
                    rec_actions = json.loads(r["recommended_actions_json"])
                except Exception:
                    rec_actions = []
                logs.append(
                    ThoughtLog(
                        timestamp=str(r["timestamp"]),
                        trigger_type=str(r["trigger_type"]),
                        thought_process=str(r["thought_process"]),
                        decision_summary=str(r["decision_summary"]),
                        recommended_actions=rec_actions,
                    )
                )
            return logs
        except Exception as e:
            logger.error("[stock_trader] 讀取 SQLite thought_logs 失敗: %s", e)
            return []

    def append_thought(self, thought: ThoughtLog) -> bool:
        try:
            actions_json = json.dumps(thought.recommended_actions, ensure_ascii=False)
            with db_transaction(self.db_path) as cur:
                cur.execute("""
                    INSERT INTO thought_logs (timestamp, trigger_type, thought_process, decision_summary, recommended_actions_json)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    thought.timestamp,
                    thought.trigger_type,
                    thought.thought_process,
                    thought.decision_summary,
                    actions_json,
                ))
            return True
        except Exception as e:
            logger.error("[stock_trader] 寫入 SQLite thought_logs 失敗: %s", e)
            return False
