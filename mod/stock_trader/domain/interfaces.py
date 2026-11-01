"""
interfaces.py (mod/stock_trader/domain)
=========================================
倉儲與數據抓取的抽象介面定義 (遵守 AGENTS.md 抽象注入原則)。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from mod.stock_trader.domain.models import (
    DailySnapshot, PortfolioAccount, ThoughtLog, TradeRecord,
)


class ITraderRepository(ABC):
    """交易模組資料倉儲抽象介面。"""

    @abstractmethod
    def load_portfolio(self) -> PortfolioAccount:
        """載入帳戶與持倉狀態。"""
        pass

    @abstractmethod
    def save_portfolio(self, account: PortfolioAccount) -> bool:
        """儲存帳戶與持倉狀態。"""
        pass

    @abstractmethod
    def load_daily_history(self) -> List[DailySnapshot]:
        """載入每日資產歷史快照 (最近 30 天/1 個月)。"""
        pass

    @abstractmethod
    def save_daily_snapshot(self, snapshot: DailySnapshot) -> bool:
        """寫入每日資產歷史快照。"""
        pass

    @abstractmethod
    def load_trades(self, limit: int = 100) -> List[TradeRecord]:
        """載入交易歷史紀錄。"""
        pass

    @abstractmethod
    def append_trade(self, trade: TradeRecord) -> bool:
        """新增一筆交易歷史紀錄。"""
        pass

    @abstractmethod
    def load_thoughts(self, limit: int = 50) -> List[ThoughtLog]:
        """載入雪莉思考日誌。"""
        pass

    @abstractmethod
    def append_thought(self, thought: ThoughtLog) -> bool:
        """新增一條雪莉思考日誌。"""
        pass
