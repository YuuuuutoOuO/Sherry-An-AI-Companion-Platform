"""
tests/unit/test_stock_algo_trader.py
====================================
美股虛擬量化交易演算法執行引擎 (AlgoTrader) 單元測試套件。
驗證：
  1. 買入演算法（指定金額、指定股數、0.1% 手續費計算）。
  2. 多次加倉之加權平均買入成本計算。
  3. 現金不足時自適應降額買入機制。
  4. 現金極端匱乏與低於碎股門檻（MIN_SHARES = 0.01）之阻擋防禦。
  5. 賣出演算法（全額賣出、指定股數賣出、比例賣出、已實現損益與勝率統計）。
  6. 未持有標的或無效價格之防禦攔截。
  7. 市價更新（update_prices）與當日資產快照（record_daily_snapshot）損益計算。
"""

from __future__ import annotations

from typing import Any, List
import pytest

from mod.stock_trader.domain.interfaces import ITraderRepository
from mod.stock_trader.domain.models import (
    DailySnapshot,
    PortfolioAccount,
    TradeAction,
    TradeRecord,
)
from mod.stock_trader.engine.algo_trader import AlgoTrader


class FakeTraderRepository(ITraderRepository):
    """記憶體內部 Fake 交易資料庫，供單元測試快速驗證。"""

    def __init__(self, initial_cash: float = 10000.0) -> None:
        self.account = PortfolioAccount(
            cash=initial_cash,
            initial_capital=initial_cash,
            positions={},
            realized_pnl=0.0,
            total_trades_count=0,
            winning_trades_count=0,
        )
        self.trades: List[TradeRecord] = []
        self.snapshots: List[DailySnapshot] = []
        self.thoughts: list[Any] = []

    def load_portfolio(self) -> PortfolioAccount:
        return self.account

    def save_portfolio(self, account: PortfolioAccount) -> bool:
        self.account = account
        return True

    def append_trade(self, record: TradeRecord) -> bool:
        self.trades.append(record)
        return True

    def load_trades(self, limit: int = 100) -> List[TradeRecord]:
        return self.trades[-limit:]

    def load_daily_history(self) -> List[DailySnapshot]:
        return self.snapshots

    def save_daily_snapshot(self, snapshot: DailySnapshot) -> bool:
        self.snapshots.append(snapshot)
        return True

    def load_thoughts(self, limit: int = 50) -> list[Any]:
        return self.thoughts[-limit:]

    def append_thought(self, thought: Any) -> bool:
        self.thoughts.append(thought)
        return True



@pytest.fixture
def fake_repo() -> FakeTraderRepository:
    return FakeTraderRepository(initial_cash=10000.0)


@pytest.fixture
def trader(fake_repo: FakeTraderRepository) -> AlgoTrader:
    return AlgoTrader(repository=fake_repo)


# ===========================================================================
# 1. 買入演算 (BUY) 測試
# ===========================================================================
class TestAlgoTraderBuy:
    """測試買入下單、費用計算、加權成本與自適應降額。"""

    def test_buy_with_target_amount_usd(self, trader: AlgoTrader, fake_repo: FakeTraderRepository) -> None:
        """測試依美金目標金額買入，正確換算股數並扣除 0.1% 手續費。"""
        # 1000 USD at $100/share -> 10.0 股
        success, msg, record = trader.execute_order(
            ticker="NVDA",
            action=TradeAction.BUY,
            current_price=100.0,
            target_amount_usd=1000.0,
            reason="主力突破",
        )

        assert success is True
        assert record is not None
        assert record.ticker == "NVDA"
        assert record.shares == 10.0
        assert record.fee == 1.0  # 1000 * 0.001
        assert record.total_amount == 1001.0  # 1000 + 1.0

        # 檢查帳戶狀態
        account = fake_repo.load_portfolio()
        assert account.cash == round(10000.0 - 1001.0, 2)
        assert "NVDA" in account.positions
        pos = account.positions["NVDA"]
        assert pos.shares == 10.0
        assert pos.avg_cost == 100.0
        assert account.total_trades_count == 1
        assert len(fake_repo.trades) == 1

    def test_buy_with_target_shares(self, trader: AlgoTrader, fake_repo: FakeTraderRepository) -> None:
        """測試依目標股數買入。"""
        success, msg, record = trader.execute_order(
            ticker="AAPL",
            action="BUY",
            current_price=200.0,
            target_shares=5.5,
            reason="逢低建倉",
        )

        assert success is True
        assert record is not None
        assert record.shares == 5.5
        trade_val = 5.5 * 200.0  # 1100.0
        fee = round(1100.0 * 0.001, 2)  # 1.10
        assert record.fee == fee
        assert fake_repo.account.cash == round(10000.0 - (trade_val + fee), 2)

    def test_buy_weighted_average_cost(self, trader: AlgoTrader, fake_repo: FakeTraderRepository) -> None:
        """測試同標的多次買入，加權平均買入成本計算正確性。"""
        # 第一次買入：10 股 @ $100 -> 總成本 $1000
        trader.execute_order("MSFT", TradeAction.BUY, current_price=100.0, target_shares=10.0)
        # 第二次加倉：10 股 @ $120 -> 總成本 $1200
        trader.execute_order("MSFT", TradeAction.BUY, current_price=120.0, target_shares=10.0)

        pos = fake_repo.account.positions["MSFT"]
        assert pos.shares == 20.0
        # (1000 + 1200) / 20 = 110.0
        assert pos.avg_cost == 110.0

    def test_buy_insufficient_cash_auto_downsize(self, trader: AlgoTrader, fake_repo: FakeTraderRepository) -> None:
        """測試當購買力不足時，自動降額至帳戶剩餘現金之最大可買股數。"""
        fake_repo.account.cash = 500.0

        # 企圖購買 $1000，現價 $100
        success, msg, record = trader.execute_order(
            ticker="TSLA",
            action=TradeAction.BUY,
            current_price=100.0,
            target_amount_usd=1000.0,
        )

        assert success is True
        assert record is not None
        # max_cash_for_value = 500 / 1.001 = 499.5005 -> shares = 499.5005 / 100 = 4.995 股
        assert record.shares < 10.0
        assert record.total_amount <= 500.0
        assert fake_repo.account.cash >= 0.0

    def test_buy_insufficient_cash_below_min_shares_rejected(self, trader: AlgoTrader, fake_repo: FakeTraderRepository) -> None:
        """測試當剩餘現金連最小碎股 (0.01 股) 都買不起時，阻擋並回傳現金不足。"""
        fake_repo.account.cash = 0.50

        success, msg, record = trader.execute_order(
            ticker="AMZN",
            action=TradeAction.BUY,
            current_price=200.0,
            target_amount_usd=100.0,
        )

        assert success is False
        assert "現金不足" in msg
        assert record is None

    def test_buy_invalid_inputs(self, trader: AlgoTrader) -> None:
        """測試價格 <= 0、未指定數量或低於 MIN_SHARES 之輸入檢驗。"""
        # 現價 <= 0
        s1, m1, _ = trader.execute_order("NVDA", TradeAction.BUY, current_price=0.0, target_shares=1.0)
        assert s1 is False and "現價無效" in m1

        # 動作為 HOLD
        s2, m2, _ = trader.execute_order("NVDA", TradeAction.HOLD, current_price=100.0, target_shares=1.0)
        assert s2 is False and "選擇觀望" in m2

        # 未指定金額或股數
        s3, m3, _ = trader.execute_order("NVDA", TradeAction.BUY, current_price=100.0)
        assert s3 is False and "未指定有效金額或股數" in m3

        # 股數小於 MIN_SHARES (0.01)
        s4, m4, _ = trader.execute_order("NVDA", TradeAction.BUY, current_price=100.0, target_shares=0.005)
        assert s4 is False and "低於最小交易門檻" in m4


# ===========================================================================
# 2. 賣出演算 (SELL) 測試
# ===========================================================================
class TestAlgoTraderSell:
    """測試賣出下單、部分賣出、手續費、已實現損益與勝率統計。"""

    def test_sell_not_held_ticker_fails(self, trader: AlgoTrader) -> None:
        """未持有該標的時賣出應失敗。"""
        success, msg, record = trader.execute_order(
            ticker="GOOGL",
            action=TradeAction.SELL,
            current_price=150.0,
            target_shares=1.0,
        )
        assert success is False
        assert "未持有" in msg
        assert record is None

    def test_sell_full_position_with_profit(self, trader: AlgoTrader, fake_repo: FakeTraderRepository) -> None:
        """全額賣出獲利部位，驗證現金增加、持倉清除、已實現損益及獲利次數累加。"""
        # 先以 $100 買入 10 股
        trader.execute_order("NVDA", TradeAction.BUY, current_price=100.0, target_shares=10.0)
        cash_after_buy = fake_repo.account.cash

        # 以 $150 全額賣出 10 股
        success, msg, record = trader.execute_order(
            ticker="NVDA",
            action=TradeAction.SELL,
            current_price=150.0,
            reason="獲利了結",
        )

        assert success is True
        assert record is not None
        assert record.shares == 10.0
        # trade_val = 1500.0, fee = 1.50, net_payout = 1498.50
        assert record.fee == 1.50
        assert record.total_amount == 1498.50
        # cost_basis = 10 * 100 = 1000.0, realized_pnl = 1498.50 - 1000.0 = 498.50
        assert record.realized_pnl == 498.50

        # 檢查持倉已刪除
        account = fake_repo.account
        assert "NVDA" not in account.positions
        assert account.cash == round(cash_after_buy + 1498.50, 2)
        assert account.realized_pnl == 498.50
        assert account.winning_trades_count == 1
        assert account.total_trades_count == 2

    def test_sell_partial_position_by_ratio(self, trader: AlgoTrader, fake_repo: FakeTraderRepository) -> None:
        """測試依比例 (例: 50%) 部分賣出持倉。"""
        # 買入 10 股 @ $100
        trader.execute_order("META", TradeAction.BUY, current_price=100.0, target_shares=10.0)

        # 賣出 50%
        success, msg, record = trader.execute_order(
            ticker="META",
            action=TradeAction.SELL,
            current_price=120.0,
            target_shares_ratio=0.5,
        )

        assert success is True
        assert record is not None
        assert record.shares == 5.0
        # 剩餘持倉應為 5 股，均價維持 $100
        pos = fake_repo.account.positions["META"]
        assert pos.shares == 5.0
        assert pos.avg_cost == 100.0

    def test_sell_with_loss_records_correctly(self, trader: AlgoTrader, fake_repo: FakeTraderRepository) -> None:
        """虧損停損賣出時，已實現損益應為負數且不增加 winning_trades_count。"""
        # 買入 10 股 @ $100 (成本 $1000)
        trader.execute_order("INTC", TradeAction.BUY, current_price=100.0, target_shares=10.0)

        # 跌至 $80 停損賣出 (市值 $800, 手續費 $0.80, payout $799.20)
        # realized_pnl = 799.20 - 1000 = -200.80
        success, msg, record = trader.execute_order(
            ticker="INTC",
            action=TradeAction.SELL,
            current_price=80.0,
            target_shares=10.0,
        )

        assert success is True
        assert record is not None
        assert record.realized_pnl == -200.80
        assert fake_repo.account.winning_trades_count == 0


# ===========================================================================
# 3. 市價更新與資產快照測試
# ===========================================================================
class TestAlgoTraderSnapshotAndPrices:
    """測試市價更新與每日歷史快照。"""

    def test_update_prices(self, trader: AlgoTrader, fake_repo: FakeTraderRepository) -> None:
        """更新所有持倉市價。"""
        trader.execute_order("NVDA", TradeAction.BUY, current_price=100.0, target_shares=5.0)
        trader.execute_order("AAPL", TradeAction.BUY, current_price=150.0, target_shares=10.0)

        updated_account = trader.update_prices({"NVDA": 130.0, "AAPL": 160.0})
        assert updated_account.positions["NVDA"].current_price == 130.0
        assert updated_account.positions["AAPL"].current_price == 160.0

    def test_record_daily_snapshot(self, trader: AlgoTrader, fake_repo: FakeTraderRepository) -> None:
        """測試拍攝每日資產快照並計算當日損益。"""
        # 初始快照
        s1 = trader.record_daily_snapshot()
        assert s1.total_asset_value == 10000.0
        assert s1.day_pnl == 0.0

        # 買入並獲利
        trader.execute_order("NVDA", TradeAction.BUY, current_price=100.0, target_shares=10.0)
        trader.update_prices({"NVDA": 150.0})

        # 第二次快照
        s2 = trader.record_daily_snapshot()
        # 原始現金 10000 - 1001 = 8999, NVDA 市值 1500 -> 總資產 10499
        assert s2.total_asset_value == 10499.0
        assert len(fake_repo.snapshots) == 2
