"""
models.py (mod/stock_trader/domain)
=====================================
美股虛擬量化交易資料結構與領域模型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List


class TradeAction(str, Enum):
    """交易動作列舉。"""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass(frozen=True)
class StockPosition:
    """單一股票持倉狀態。

    Attributes:
        ticker: 股票代碼 (例: NVDA)
        shares: 持有股數 (支援 >= 0.01 碎股)
        avg_cost: 平均買入成本 (USD)
        current_price: 最新市價 (USD)
        total_cost: 總成本金額 (USD)
        market_value: 目前市值 (USD)
        unrealized_pnl: 未實現損益 (USD)
        unrealized_pnl_pct: 未實現損益百分比 (%)
    """
    ticker: str
    shares: float
    avg_cost: float
    current_price: float

    @property
    def total_cost(self) -> float:
        return round(self.shares * self.avg_cost, 2)

    @property
    def market_value(self) -> float:
        return round(self.shares * self.current_price, 2)

    @property
    def unrealized_pnl(self) -> float:
        return round(self.market_value - self.total_cost, 2)

    @property
    def unrealized_pnl_pct(self) -> float:
        if self.total_cost <= 0:
            return 0.0
        return round((self.unrealized_pnl / self.total_cost) * 100, 2)


@dataclass
class PortfolioAccount:
    """帳戶資產與資金狀態。

    Attributes:
        cash: 可用現金 (USD)
        initial_capital: 初始資金 (USD，預設 10,000)
        positions: 當前持倉字典 (ticker -> StockPosition)
        realized_pnl: 累計已實現損益 (USD)
        total_trades_count: 總交易次數
        winning_trades_count: 獲利交易次數
        created_at: 帳戶創建時間字串
        updated_at: 帳戶更新時間字串
    """
    cash: float = 10000.0
    initial_capital: float = 10000.0
    positions: Dict[str, StockPosition] = field(default_factory=dict)
    realized_pnl: float = 0.0
    total_trades_count: int = 0
    winning_trades_count: int = 0
    created_at: str = ""
    updated_at: str = ""

    def get_total_asset_value(self) -> float:
        """計算總資產價值（現金 + 股票總市值）。"""
        pos_val = sum(pos.market_value for pos in self.positions.values())
        return round(self.cash + pos_val, 2)

    def get_total_pnl(self) -> float:
        """計算總累計損益（總資產 - 初始資金）。"""
        return round(self.get_total_asset_value() - self.initial_capital, 2)

    def get_total_pnl_pct(self) -> float:
        """計算總報酬率 (%)。"""
        if self.initial_capital <= 0:
            return 0.0
        return round((self.get_total_pnl() / self.initial_capital) * 100, 2)

    def get_win_rate_pct(self) -> float:
        """計算交易勝率 (%)。"""
        if self.total_trades_count <= 0:
            return 0.0
        return round((self.winning_trades_count / self.total_trades_count) * 100, 2)


@dataclass(frozen=True)
class DailySnapshot:
    """每日資產歷史快照 (用於 Web UI 1 個月盈餘率折線圖)。

    Attributes:
        date: 日期字串 (YYYY-MM-DD)
        total_asset_value: 總資產金額 (USD)
        cash: 剩餘現金 (USD)
        total_pnl: 累計損益 (USD)
        return_rate_pct: 累計盈餘率 (%)
        day_pnl: 當日損益 (USD)
    """
    date: str
    total_asset_value: float
    cash: float
    total_pnl: float
    return_rate_pct: float
    day_pnl: float


@dataclass(frozen=True)
class TradeRecord:
    """單筆交易歷史紀錄。

    Attributes:
        trade_id: 交易唯一識別碼
        timestamp: 交易時間字串 (ISO 格式/標準時間)
        ticker: 標的代碼
        action: 交易動作 (BUY / SELL)
        shares: 股數
        execution_price: 成交價格 (USD)
        fee: 手續費 (USD)
        total_amount: 交易交割總額 (USD)
        realized_pnl: 該次交易實現之損益 (僅 SELL 時有效，BUY 為 0.0)
        reason: 交易決策理由/主力模型分析摘要
    """
    trade_id: str
    timestamp: str
    ticker: str
    action: str
    shares: float
    execution_price: float
    fee: float
    total_amount: float
    realized_pnl: float
    reason: str


@dataclass(frozen=True)
class ThoughtLog:
    """雪莉主力模型思考日誌 (CoT)。

    Attributes:
        timestamp: 時間字串
        trigger_type: 觸發類型 (ROUTINE / FORCE_CLOSE_EVAL / MANUAL)
        thought_process: 推理過程思考文字
        decision_summary: 決策結論摘要
        recommended_actions: 推薦動作列表
    """
    timestamp: str
    trigger_type: str
    thought_process: str
    decision_summary: str
    recommended_actions: List[Dict[str, Any]]
