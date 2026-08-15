"""
test_stock_trader.py
====================
Sherry Stock Trader 虛擬量化交易模組單元測試 (AAA 結構)。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("DISCORD_BOT_TOKEN", "mock_token")

import pytest

from mod.stock_trader.domain.models import (
    DailySnapshot, PortfolioAccount, StockPosition, TradeAction, TradeRecord,
)
from mod.stock_trader.engine.algo_trader import AlgoTrader
from mod.stock_trader.repository import StockRepository


@pytest.fixture
def temp_repo() -> StockRepository:
    """提供臨時目錄資料倉儲對象。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = StockRepository(data_dir=Path(tmp_dir))
        yield repo


def test_initial_portfolio_loading(temp_repo: StockRepository) -> None:
    """測試全新初始化帳戶是否具備 $10,000 本金。"""
    # Arrange & Act
    account = temp_repo.load_portfolio()

    # Assert
    assert account.cash == 10000.0
    assert account.initial_capital == 10000.0
    assert account.get_total_asset_value() == 10000.0
    assert account.get_total_pnl() == 0.0
    assert len(account.positions) == 0


def test_algo_trader_buy_order(temp_repo: StockRepository) -> None:
    """測試演算法買入：正確扣除金額、0.1%手續費與更新持倉。"""
    # Arrange
    trader = AlgoTrader(temp_repo)

    # Act: 買入 NVDA，金額 $1,000 美金，現價 $100.0
    # 手續費為 $1000 * 0.001 = $1.0，交割 $1001.0
    success, msg, record = trader.execute_order(
        ticker="NVDA",
        action=TradeAction.BUY,
        current_price=100.0,
        target_amount_usd=1000.0,
        reason="指標多頭排列",
    )

    # Assert
    assert success is True
    assert record is not None
    assert record.ticker == "NVDA"
    assert record.action == "BUY"
    assert record.shares == 10.0
    assert record.fee == 1.0
    assert record.total_amount == 1001.0

    # 驗證帳戶狀態
    acc = temp_repo.load_portfolio()
    assert acc.cash == 8999.0
    assert "NVDA" in acc.positions
    assert acc.positions["NVDA"].shares == 10.0
    assert acc.positions["NVDA"].avg_cost == 100.0


def test_algo_trader_fractional_shares(temp_repo: StockRepository) -> None:
    """測試演算法碎股買入 (>= 0.01 股)。"""
    # Arrange
    trader = AlgoTrader(temp_repo)

    # Act: 現價 $500.0，買入 0.05 股 ($25.0)
    success, msg, record = trader.execute_order(
        ticker="AAPL",
        action=TradeAction.BUY,
        current_price=500.0,
        target_shares=0.05,
        reason="測試碎股交易",
    )

    # Assert
    assert success is True
    assert record is not None
    assert record.shares == 0.05
    assert record.fee == 0.03  # 25 * 0.001 = 0.025 -> round 0.03

    acc = temp_repo.load_portfolio()
    assert acc.positions["AAPL"].shares == 0.05


def test_algo_trader_insufficient_cash(temp_repo: StockRepository) -> None:
    """測試可用現金不足時阻擋交易。"""
    # Arrange
    trader = AlgoTrader(temp_repo)
    acc = temp_repo.load_portfolio()
    acc.cash = 5.0  # 只剩 $5 現金
    temp_repo.save_portfolio(acc)

    # Act: 欲買入 $1,000 美金
    success, msg, record = trader.execute_order(
        ticker="MSFT",
        action=TradeAction.BUY,
        current_price=200.0,
        target_amount_usd=1000.0,
    )

    # Assert
    assert success is True  # 自動調整為最大可買股數
    acc_after = temp_repo.load_portfolio()
    assert acc_after.cash < 5.0  # 已使用剩餘現金購買小於 5 美金的碎股


def test_algo_trader_sell_order_and_pnl(temp_repo: StockRepository) -> None:
    """測試演算法賣出：實現損益計算與持倉清空。"""
    # Arrange
    trader = AlgoTrader(temp_repo)

    # 先買入 10 股 @ $100.0
    trader.execute_order(
        ticker="TSLA",
        action=TradeAction.BUY,
        current_price=100.0,
        target_shares=10.0,
    )

    # Act: 漲至 $150.0 時賣出 10 股
    # 賣出總值 $1500.0，手續費 $1.5，淨所得 $1498.5
    # 成本 $1000.0，實現利潤 $498.5
    success, msg, record = trader.execute_order(
        ticker="TSLA",
        action=TradeAction.SELL,
        current_price=150.0,
        target_shares=10.0,
        reason="停利出場",
    )

    # Assert
    assert success is True
    assert record is not None
    assert record.action == "SELL"
    assert record.realized_pnl == 498.5

    acc = temp_repo.load_portfolio()
    assert "TSLA" not in acc.positions  # 持倉已清空
    assert acc.winning_trades_count == 1
    assert acc.get_win_rate_pct() == 50.0  # 買入算 1 筆，賣出算 1 筆勝場 -> 1/2 = 50%


def test_daily_snapshot_saving(temp_repo: StockRepository) -> None:
    """測試每日資產歷史快照紀錄。"""
    # Arrange
    snapshot = DailySnapshot(
        date="2026-08-06",
        total_asset_value=10500.0,
        cash=5000.0,
        total_pnl=500.0,
        return_rate_pct=5.0,
        day_pnl=200.0,
    )

    # Act
    temp_repo.save_daily_snapshot(snapshot)
    history = temp_repo.load_daily_history()

    # Assert
    assert len(history) == 1
    assert history[0].date == "2026-08-06"
    assert history[0].total_asset_value == 10500.0


def test_persona_config_switch(temp_repo: StockRepository) -> None:
    """測試 ENABLE_SHERRY_PERSONA 開關對 Prompt 切換之效果。"""
    # Arrange
    from mod.stock_trader import config as cfg
    from mod.stock_trader.engine.strategist import TradeStrategist

    account = temp_repo.load_portfolio()
    indicators = [{"ticker": "NVDA", "close": 100.0, "day_pct": 1.5, "mon_pct": 10.0, "rsi": 55.0, "ema20": 98.0, "ema50": 92.0}]
    strategist = TradeStrategist()

    # Act 1: Persona True
    cfg.ENABLE_SHERRY_PERSONA = True
    prompt_persona = strategist.build_prompt(account, indicators)

    # Act 2: Persona False
    cfg.ENABLE_SHERRY_PERSONA = False
    prompt_objective = strategist.build_prompt(account, indicators)

    # Assert
    assert "名偵探女高中生" in prompt_persona
    assert "純客觀量化模式" in prompt_objective


def test_algo_trader_ratio_sell(temp_repo: StockRepository) -> None:
    """測試持倉比例賣出 (如 50% 賣出一半)。"""
    # Arrange
    trader = AlgoTrader(temp_repo)
    trader.execute_order(ticker="NVDA", action=TradeAction.BUY, current_price=100.0, target_shares=10.0)

    # Act: 賣出 50%
    success, msg, record = trader.execute_order(
        ticker="NVDA",
        action=TradeAction.SELL,
        current_price=120.0,
        target_shares_ratio=0.5,
        reason="賣出一半獲利了結",
    )

    # Assert
    assert success is True
    assert record is not None
    assert record.shares == 5.0
    assert record.realized_pnl == 99.4  # (5 * 120 - 0.6) - 500 = 599.4 - 500 = 99.4
    acc = temp_repo.load_portfolio()
    assert "NVDA" in acc.positions
    assert acc.positions["NVDA"].shares == 5.0


def test_parse_llm_response_standard_json() -> None:
    """測試 Tier 1 標準 JSON 解析。"""
    # Arrange
    from mod.stock_trader.engine.strategist import TradeStrategist
    strategist = TradeStrategist()
    raw = """
<thought>
深入分析後決定買入 TSLA 並賣出 MRVL。
</thought>

```json
[
  {"ticker": "MRVL", "action": "SELL", "target_shares_ratio": 1.0, "reason": "汰弱換強"},
  {"ticker": "TSLA", "action": "BUY", "target_amount_usd": 350.0, "reason": "超賣反彈"}
]
```
"""

    # Act
    thought_log, actions = strategist.parse_llm_response(raw)

    # Assert
    assert len(actions) == 2
    assert actions[0]["ticker"] == "MRVL"
    assert actions[0]["action"] == "SELL"
    assert actions[0]["target_shares_ratio"] == 1.0
    assert actions[1]["ticker"] == "TSLA"
    assert actions[1]["action"] == "BUY"
    assert actions[1]["target_amount_usd"] == 350.0
    assert "MRVL:SELL" in thought_log.decision_summary
    assert "TSLA:BUY" in thought_log.decision_summary


def test_parse_llm_response_user_real_samples() -> None:
    """測試使用者提供的真實雪莉推理文本解析精準度。"""
    # Arrange
    from mod.stock_trader.engine.strategist import TradeStrategist
    strategist = TradeStrategist()
    known = ["NVDA", "AAPL", "MSFT", "GOOG", "AMZN", "META", "TSLA", "MRVL", "ARM", "MU", "CRCL", "LITE", "INTC", "ANET", "CRWD", "AAOI"]

    # Sample 1: 先賣後買
    s1 = """
**4. 下單決策：**
由於現金極度匱乏（$0.01），必須優先進行「賣出」動作來換取資金空間。
- 我決定將 **MRVL** 賣出全部（目前處於微幅虧損且在 BB 通道中位）。
- 我決定將 **ARM** 賣出全部（雖然略虧，但為了騰挪 AAPL 的預算）。
- 在釋放資金後，我會投入 $100 美金進攻極度超賣的 **AAPL**。
🎯 決策結論: MRVL:SELL (現價位於 BB 通道中位，且未實現損益為負 (-2.97%)。賣出以回收資金並等待更具吸引力的入場點。)；ARM:SELL (雖然僅微幅虧損，但為了釋放資金給 RSI 極低的 AAPL 提供更多彈藥。)；AAPL:BUY (RSI 為 29.1，處於明顯超賣區，且現價遠低於 MA20。此時補倉是極佳的技術性買入點。)
"""
    log1, act1 = strategist.parse_llm_response(s1, known_tickers=known)
    assert len(act1) == 3
    assert {a["ticker"] for a in act1} == {"MRVL", "ARM", "AAPL"}
    mrvl = next(a for a in act1 if a["ticker"] == "MRVL")
    arm = next(a for a in act1 if a["ticker"] == "ARM")
    aapl = next(a for a in act1 if a["ticker"] == "AAPL")
    assert mrvl["action"] == "SELL" and mrvl["target_shares_ratio"] == 1.0
    assert arm["action"] == "SELL" and arm["target_shares_ratio"] == 1.0
    assert aapl["action"] == "BUY" and aapl["target_amount_usd"] == 100.0

    # Sample 4: 買入指定金額與賣出一半
    s4 = """
**4. 下單決策：**
*   **第一筆：BUY AAPL ($200 USD)**。理由是 RSI 29.3 是全場最低點之一，與 MA60 相較仍有補漲空間，適合在低位建倉。
*   **第二筆：BUY MRVL ($150 USD)**。作為半導體組合的補充，MRVL 今日 +5.2% 動能強勁且 BB 通道上方仍有操作空間。
*   **第三筆：SELL MU (Hold 比例或部分獲利了結)**。由於 MU 已大漲近 5%，雖然 MACD 強勁但為了鎖定當前的高點動能，我決定先 SELL 出部分持倉以確保收益（假設賣出約一半）。
🎯 決策結論: AAPL:BUY (RSI 低於 30 進入超賣區。)；MRVL:BUY (日線大漲 5.2%。)；MU:SELL (今日大漲近 5%，賣出一半。)
"""
    log4, act4 = strategist.parse_llm_response(s4, known_tickers=known)
    assert len(act4) == 3
    aapl4 = next(a for a in act4 if a["ticker"] == "AAPL")
    mrvl4 = next(a for a in act4 if a["ticker"] == "MRVL")
    mu4 = next(a for a in act4 if a["ticker"] == "MU")
    assert aapl4["action"] == "BUY" and aapl4["target_amount_usd"] == 200.0
    assert mrvl4["action"] == "BUY" and mrvl4["target_amount_usd"] == 150.0
    assert mu4["action"] == "SELL" and mu4["target_shares_ratio"] == 0.5

    # Sample 2: 賣出與買入動能標的
    s2 = """
**4. 下單決策：**
*   **SELL LITE:** 雖然目前虧損，但考慮到它目前的 MA20 與 EMA20 都高於現價 ($905 < $813)，顯示短期仍處於下行壓力中，先賣出以換取現金。
*   **BUY MU:** 利用其強勁的 MACD 動能與 6.2% 的單日漲幅進行進攻式補倉，投入較大資金。
*   **BUY AAPL:** 因為 RSI 低於 30 是極佳的入場點，雖然它目前表現平淡，但作為長期核心標的是非常適合此時機「撿便宜」。
🎯 決策結論: LITE:SELL (現價低於 MA20 與 EMA20。)；MU:BUY (單日漲幅達 6.2%。)；AAPL:BUY (RSI 低於 30。)
"""
    log2, act2 = strategist.parse_llm_response(s2, known_tickers=known)
    assert len(act2) == 3
    assert {a["ticker"] for a in act2} == {"LITE", "MU", "AAPL"}
    assert next(a for a in act2 if a["ticker"] == "LITE")["action"] == "SELL"
    assert next(a for a in act2 if a["ticker"] == "MU")["action"] == "BUY"
    assert next(a for a in act2 if a["ticker"] == "AAPL")["action"] == "BUY"

    # Sample 3: 買入與觀望 HOLD
    s3 = """
**4. 下單決策：**
1.  **AAPL:** 目前處於極度超賣區，決定投入 $200 美金進行補倉，以降低平均成本並佈局長期成長。
2.  **ARM:** 選擇在目前的強勢區域加入新標的，投入 $85 美金（扣除手續費後約為總額）。
3.  **TSLA:** 因為目前表現穩定且動能轉強，決定 **HOLD**，等待進一步突破布林通道上緣再考慮賣出或加碼。
🎯 決策結論: AAPL:BUY (RSI 低於 30。)；ARM:BUY (RSI 為中性偏強。)
"""
    log3, act3 = strategist.parse_llm_response(s3, known_tickers=known)
    assert len(act3) >= 2
    assert next(a for a in act3 if a["ticker"] == "AAPL")["target_amount_usd"] == 200.0
    assert next(a for a in act3 if a["ticker"] == "ARM")["target_amount_usd"] == 85.0

    # Sample 5: 多標的買入
    s5 = """
**4. 下單決策：**
*   針對 **ANET**: 目前表現亮眼且 RIX (5.6%) 高於 MA 各項指標，決定投入 $300 美金進行大幅補倉。
*   針對 **MU**: 抓住今日 5.9% 的噴發動能，在 RSI 中位區時投入 $200 美金建倉。
*   針對 **AMZN**: 現有持股過少，投入 $100 美金增加穩定度。
🎯 決策結論: ANET:BUY (今日大漲5.6%。)；MU:BUY (今日大漲5.9%。)；AMZN:BUY (小幅補倉。)
"""
    log5, act5 = strategist.parse_llm_response(s5, known_tickers=known)
    assert len(act5) == 3
    assert next(a for a in act5 if a["ticker"] == "ANET")["target_amount_usd"] == 300.0
    assert next(a for a in act5 if a["ticker"] == "MU")["target_amount_usd"] == 200.0
    assert next(a for a in act5 if a["ticker"] == "AMZN")["target_amount_usd"] == 100.0

    # Sample 6: 一賣二買
    s6 = """
**4. 下單決策：**
*   賣出 CRWD (RSI 過高且趨於飽和)。
*   買入 LITE ($300 USD)：捕捉其極強的動能突破。
*   買入 MU ($250 USD)：作為半導體與記憶體的平衡配置，獲取今日的高成長動力。
🎯 決策結論: CRWD:SELL (賣出落袋為安。)；LITE:BUY (捕捉動能突破。)；MU:BUY (半導體補倉。)
"""
    log6, act6 = strategist.parse_llm_response(s6, known_tickers=known)
    assert len(act6) == 3
    assert next(a for a in act6 if a["ticker"] == "CRWD")["action"] == "SELL"
    assert next(a for a in act6 if a["ticker"] == "LITE")["target_amount_usd"] == 300.0
    assert next(a for a in act6 if a["ticker"] == "MU")["target_amount_usd"] == 250.0

    # Sample 7: 單一買入與 HOLD
    s7 = """
**4. 下單決策：**
我決定投入 $500 美金買入 AAPL。雖然目前持倉僅 0.49 股，但透過增加資金可以顯著提升在價值區間內的曝光度，並降低平均成本。同時考慮到 NVDA 的表現穩健，我將繼續 HOLD 它以享受目前的趨勢利潤。
🎯 決策結論: AAPL:BUY (RSI 為 37.1 低於 MA60。)
"""
    log7, act7 = strategist.parse_llm_response(s7, known_tickers=known)
    assert len(act7) >= 1
    assert next(a for a in act7 if a["ticker"] == "AAPL")["target_amount_usd"] == 500.0

    # Sample 8: 買入與賣出
    s8 = """
**4. 下單決策：**
*   **補倉 TSLA:** 雖然目前是震盪狀態，但在 RSI 53 的中性區間增加持股量可以提高未來反彈時的獲利空間（預計投入 \\$300）。
*   **加碼 ANET:** 利用其目前的強勢動能 (RSI 67.8)，補倉以鎖定這波上升趨勢。
*   **賣出 CRCL:** 目前處於 BB 通道底部且微虧，考慮到現金流彈藥有限（\\$102），若無顯著反轉訊號，先將其結算回報資金庫。
🎯 決策結論: TSLA:BUY (降低平均成本。)；ANET:BUY (強勁上升趨勢。)；CRCL:SELL (賣出結算。)
"""
    log8, act8 = strategist.parse_llm_response(s8, known_tickers=known)
    assert len(act8) == 3
    assert next(a for a in act8 if a["ticker"] == "TSLA")["target_amount_usd"] == 300.0
    assert next(a for a in act8 if a["ticker"] == "ANET")["action"] == "BUY"
    assert next(a for a in act8 if a["ticker"] == "CRCL")["action"] == "SELL"

    # Sample 9: 賣出指定股數與段落金額買入
    s9 = """
**4. 下單決策：**
由於現在可用現金為 $0.00，必須先進行「SELL」動作來釋放流動性。
*   **Action 1**: 賣出 **NVDA** 的一半（約 9 股），因為目前的 RSI 並非最強勢，且需要騰出空間給新機會。
*   **Action 2**: 將釋出的資金投入到目前技術面極具潛力的 **CRCL**。其 Risi 與 MACD 同步顯示動能強勁，適合作為此時的突破選擇。
*   **Action 3**: 持續觀察 **TSLA** 是否能在 $330 以上站穩，若今日收盤仍維持在支撐位上方則繼續持有。

我決定先賣出 NVDA 的一半以獲取現金流，並將約 $1,850 美金投入到動能強勁的 CRCL 中進行佈局。
🎯 決策結論: NVDA:SELL (RSI處於中性區，賣出一半持倉。)；CRCL:BUY (當日漲幅達5.5%。)
"""
    log9, act9 = strategist.parse_llm_response(s9, known_tickers=known)
    assert len(act9) >= 2
    nvda9 = next(a for a in act9 if a["ticker"] == "NVDA")
    crcl9 = next(a for a in act9 if a["ticker"] == "CRCL")
    assert nvda9["action"] == "SELL" and (nvda9["target_shares"] == 9.0 or nvda9["target_shares_ratio"] == 0.5)
    assert crcl9["action"] == "BUY" and crcl9["target_amount_usd"] == 1850.0

    # Sample 10: 賣出全部指定股數
    s10 = """
**4. 下單決策：**
1.  賣出全部 **AAOI** 持有份數（約 2.93 股），回收現金資金子彈。
2.  投入 $500 美金購買 **TSLA**，利用其處於超賣區邊緣的優勢進行佈局。
🎯 決策結論: AAOI:SELL (賣出以釋放現金子彈。)；TSLA:BUY (超賣區補倉點。)
"""
    log10, act10 = strategist.parse_llm_response(s10, known_tickers=known)
    assert len(act10) == 2
    assert next(a for a in act10 if a["ticker"] == "AAOI")["target_shares"] == 2.93
    assert next(a for a in act10 if a["ticker"] == "TSLA")["target_amount_usd"] == 500.0


def test_sell_first_execution_order_with_low_cash(temp_repo: StockRepository) -> None:
    """測試低現金 ($0.01) 時，先賣出釋放現金再買入的執行流程。"""
    # Arrange
    trader = AlgoTrader(temp_repo)
    # 初始買入 MRVL 10 股 @ $100.0
    trader.execute_order(ticker="MRVL", action=TradeAction.BUY, current_price=100.0, target_shares=10.0)
    # 將剩餘現金手動設為 $0.01
    acc = temp_repo.load_portfolio()
    acc.cash = 0.01
    temp_repo.save_portfolio(acc)

    # 模擬 Cog 的下單決策：先賣出 MRVL 10 股，再買入 AAPL $500
    price_map = {"MRVL": 110.0, "AAPL": 50.0}
    json_actions = [
        {"ticker": "AAPL", "action": "BUY", "target_amount_usd": 500.0, "reason": "超賣買入"},
        {"ticker": "MRVL", "action": "SELL", "target_shares_ratio": 1.0, "reason": "回收現金"},
    ]

    # Act: 採用賣出優先排序
    sell_actions = [a for a in json_actions if a.get("action", "").upper() == "SELL"]
    buy_actions = [a for a in json_actions if a.get("action", "").upper() == "BUY"]

    executed = []
    for s_act in sell_actions:
        ok, msg, rec = trader.execute_order(
            ticker=s_act["ticker"],
            action="SELL",
            current_price=price_map[s_act["ticker"]],
            target_shares_ratio=s_act.get("target_shares_ratio"),
        )
        if ok and rec:
            executed.append(rec)

    for b_act in buy_actions:
        ok, msg, rec = trader.execute_order(
            ticker=b_act["ticker"],
            action="BUY",
            current_price=price_map[b_act["ticker"]],
            target_amount_usd=b_act.get("target_amount_usd"),
        )
        if ok and rec:
            executed.append(rec)

    # Assert: 兩筆交易均成功執行（先賣換得約 $1100 現金，再買入 $500 AAPL）
    assert len(executed) == 2
    assert executed[0].action == "SELL" and executed[0].ticker == "MRVL"
    assert executed[1].action == "BUY" and executed[1].ticker == "AAPL"
    final_acc = temp_repo.load_portfolio()
    assert "MRVL" not in final_acc.positions
    assert "AAPL" in final_acc.positions
    assert final_acc.cash > 500.0



