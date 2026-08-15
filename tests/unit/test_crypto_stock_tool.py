"""tests/unit/test_crypto_stock_tool.py
======================================
單元測試：驗證加密貨幣（如 XAUT/USDT、BTC/USDT）之技術分析觸發、正則解析與 Ticker 轉換。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# 將專案根目錄加入 PYTHONPATH
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from handlers.social.tools.stock_tool import (
    StockTool,
    _quick_extract_ticker,
    _resolve_ticker,
)


class TestCryptoStockTool(unittest.TestCase):
    """測試加密貨幣與股票 Ticker 正則解析與工具觸發條件。"""

    def setUp(self) -> None:
        """Arrange: 初始化 StockTool 實例。"""
        self.stock_tool = StockTool()

    def test_quick_extract_crypto_trading_pairs(self) -> None:
        """測試 Quick Extract 萃取交易對 (XAUT/USDT, BTC/USDT) 並轉為 yfinance 格式。"""
        # Act & Assert
        self.assertEqual(_quick_extract_ticker("幫我分析XAUT/USDT，分析完獎勵你摸摸頭"), "XAUT-USD")
        self.assertEqual(_quick_extract_ticker("分析 BTC/USDT 的走勢"), "BTC-USD")
        self.assertEqual(_quick_extract_ticker("ETH-USDT 現在價格"), "ETH-USD")
        self.assertEqual(_quick_extract_ticker("SOLUSDT 漲跌幅度"), "SOL-USD")

    def test_quick_extract_known_cryptos_and_stocks(self) -> None:
        """測試 Quick Extract 處理知名單一幣種與股票。"""
        self.assertEqual(_quick_extract_ticker("比特幣行情"), "BTC-USD")
        self.assertEqual(_quick_extract_ticker("台積電 2330 走勢"), "2330.TW")
        self.assertEqual(_quick_extract_ticker("AAPL 股價"), "AAPL")
        self.assertEqual(_quick_extract_ticker("泰達金價格"), "XAUT-USD")

    def test_resolve_ticker(self) -> None:
        """測試 _resolve_ticker 的字串轉置邏輯。"""
        self.assertEqual(_resolve_ticker("XAUT/USDT"), "XAUT-USD")
        self.assertEqual(_resolve_ticker("XAUT"), "XAUT-USD")
        self.assertEqual(_resolve_ticker("BTC"), "BTC-USD")
        self.assertEqual(_resolve_ticker("2330"), "2330.TW")

    def test_stock_tool_regex_matching(self) -> None:
        """測試 StockTool.can_handle_by_regex 正則觸發機制。"""
        # 應命中的問句
        self.assertTrue(self.stock_tool.can_handle_by_regex("幫我分析XAUT/USDT，分析完獎勵你摸摸頭"))
        self.assertTrue(self.stock_tool.can_handle_by_regex("比特幣走勢如何"))
        self.assertTrue(self.stock_tool.can_handle_by_regex("台積電分析"))
        self.assertTrue(self.stock_tool.can_handle_by_regex("2330.TW K線圖"))

        # 不該命中的問句
        self.assertFalse(self.stock_tool.can_handle_by_regex("今天天氣怎麼樣"))
        self.assertFalse(self.stock_tool.can_handle_by_regex("幫我畫一隻可愛的貓咪"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
