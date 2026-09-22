"""mod/spark/clients/stock_client.py - 股票與加密貨幣即時行情客戶端。

依據 AGENTS.md §0，此為唯一允許直接進行網路 I/O 的外部客戶端層之一。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from ..config import DEFAULT_TIMEOUT_SEC

logger = logging.getLogger(__name__)

# 常見加密貨幣 Symbol 集合（用於自動加上 -USD 後綴）
_KNOWN_CRYPTOS: set[str] = {
    "BTC", "ETH", "SOL", "DOGE", "BNB", "XRP", "ADA", "AVAX", "LINK", "DOT",
    "SUI", "PEPE", "SHIB", "TON", "TRX", "LTC", "NEAR", "APT", "ATOM", "ARB",
}


def normalize_ticker(ticker: str) -> str:
    """標準化股票或加密貨幣代號。

    Args:
        ticker: 輸入代號（如 'btc', '2330', 'aapl'）。

    Returns:
        str: 標準化後的 Ticker 字串。
    """
    clean = ticker.strip().upper()
    if clean in _KNOWN_CRYPTOS:
        return f"{clean}-USD"
    # 若為 4 位數字代號且無後綴，預設為台股代號 (.TW)
    if clean.isdigit() and len(clean) == 4:
        return f"{clean}.TW"
    return clean


class StockClient:
    """股票與加密貨幣即時報價查詢客戶端。"""

    def __init__(self, timeout_sec: int = DEFAULT_TIMEOUT_SEC) -> None:
        """初始化客戶端。

        Args:
            timeout_sec: 網路請求超時秒數。
        """
        self._timeout = aiohttp.ClientTimeout(total=timeout_sec)

    async def get_price(self, ticker: str) -> float | None:
        """取得標的物即時現價。

        Args:
            ticker: 股票或加密貨幣代號。

        Returns:
            float | None: 最新價格，若獲取失敗則為 None。
        """
        norm_ticker = normalize_ticker(ticker)

        # 1. 優先嘗試 Yahoo Finance Chart API (aiohttp 非同步呼叫)
        price = await self._fetch_yahoo_price(norm_ticker)
        if price is not None:
            return price

        # 2. 若為加密貨幣，嘗試 Binance 公開 API 備援
        if norm_ticker.endswith("-USD"):
            crypto_symbol = norm_ticker.replace("-USD", "USDT")
            price = await self._fetch_binance_price(crypto_symbol)
            if price is not None:
                return price

        # 3. 最後嘗試 yfinance 同步封裝 (透過 asyncio.to_thread 執行防阻塞)
        return await self._fetch_yfinance_price(norm_ticker)

    async def _fetch_yahoo_price(self, ticker: str) -> float | None:
        """透過 Yahoo Finance v8 Chart API 查詢現價。"""
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1m&range=1d"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        return None
                    data: dict[str, Any] = await resp.json()
                    chart = data.get("chart", {})
                    results = chart.get("result")
                    if not results:
                        return None
                    meta = results[0].get("meta", {})
                    regular_price = meta.get("regularMarketPrice")
                    if regular_price is not None:
                        return float(regular_price)
        except Exception as exc:
            logger.debug("[StockClient] Yahoo API 查詢失敗 (%s): %s", ticker, exc)
        return None

    async def _fetch_binance_price(self, symbol: str) -> float | None:
        """透過 Binance 公開 API 查詢加密貨幣現價。"""
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data: dict[str, Any] = await resp.json()
                        return float(data["price"])
        except Exception as exc:
            logger.debug("[StockClient] Binance API 查詢失敗 (%s): %s", symbol, exc)
        return None

    async def _fetch_yfinance_price(self, ticker: str) -> float | None:
        """透過 yfinance 套件執行抓取 (在 thread pool 執行)。"""
        def _sync_get() -> float | None:
            try:
                import yfinance as yf
                t = yf.Ticker(ticker)
                hist = t.history(period="1d")
                if not hist.empty and "Close" in hist.columns:
                    return float(hist["Close"].iloc[-1])
            except Exception as exc:
                logger.debug("[StockClient] yfinance 查詢失敗 (%s): %s", ticker, exc)
            return None

        try:
            return await asyncio.to_thread(_sync_get)
        except Exception as exc:
            logger.warning("[StockClient] yfinance thread 執行異常 (%s): %s", ticker, exc)
            return None
