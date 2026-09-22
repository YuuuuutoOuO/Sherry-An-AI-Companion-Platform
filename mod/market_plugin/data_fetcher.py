"""
data_fetcher.py  (mod/market_plugin)
======================================
股票 / 加密貨幣數據抓取模組。
移植自 DC_bot_StockAnalysis-main/modules/data_fetcher.py，
並將 API Key 改為對接模組獨立的 config.py。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Any

import aiohttp
import yfinance as yf

try:
    import finnhub
    _FINNHUB_AVAILABLE = True
except ImportError:
    _FINNHUB_AVAILABLE = False

from mod.market_plugin import config as cfg

logger = logging.getLogger(__name__)


DEFAULT_TIMEOUT_SEC: float = 8.0

_CACHE_MAX_ENTRIES: int = 512
_TREND_CACHE: dict[tuple[str, ...], tuple[float, tuple[str, list[dict[str, Any]]]]] = {}
_INFO_CACHE: dict[str, tuple[float, tuple[Any, Any]]] = {}
_RAW_STOCK_CACHE: dict[tuple[str, ...], tuple[float, list[dict[str, Any]]]] = {}

# 各快取各自的鎖；呼叫 _prune_cache_if_needed 前必須持有對應鎖
_TREND_CACHE_LOCK: threading.Lock = threading.Lock()
_INFO_CACHE_LOCK: threading.Lock = threading.Lock()
_RAW_STOCK_CACHE_LOCK: threading.Lock = threading.Lock()


def _prune_cache_if_needed(cache_dict: dict[Any, Any], max_entries: int = _CACHE_MAX_ENTRIES) -> None:
    """當快取字典項目超過上限時，淘汰過期或最早寫入的條目。

    Args:
        cache_dict: 快取字典。
        max_entries: 最大允許條目數。

    Note:
        呼叫前必須已持有對應快取的 Lock，確保整個 prune+write 操作的原子性。
    """
    if len(cache_dict) <= max_entries:
        return
    now = time.time()
    keys_to_remove = []
    for k, v in list(cache_dict.items()):  # snapshot 避免迭代中其他執行緒插入/刪除
        if isinstance(v, tuple) and len(v) >= 1 and isinstance(v[0], (int, float)):
            if now - v[0] > 300.0:
                keys_to_remove.append(k)
    for k in keys_to_remove:
        cache_dict.pop(k, None)
    if len(cache_dict) > max_entries:
        excess = len(cache_dict) - (max_entries * 3 // 4)
        for k in list(cache_dict.keys())[:excess]:
            cache_dict.pop(k, None)


def _get_valuation_multiples(ticker: str) -> tuple[Any, Any]:
    """取得標的之 PB 與 PE 估值倍數（支援 fast_info 與 300 秒快取）。

    Args:
        ticker: 股票代碼。

    Returns:
        tuple[Any, Any]: (PB, PE) 估值二元組，無資料時以 "N/A" 表示。
    """
    now = time.time()
    with _INFO_CACHE_LOCK:
        if ticker in _INFO_CACHE:
            cached_ts, val = _INFO_CACHE[ticker]
            if now - cached_ts < 300.0:
                return val
    pb, pe = "N/A", "N/A"
    try:
        t_obj = yf.Ticker(ticker)
        fast_info = getattr(t_obj, "fast_info", None)
        if fast_info is not None:
            if hasattr(fast_info, "get"):
                pb = fast_info.get("priceToBook", "N/A")
                pe = fast_info.get("trailingPE", fast_info.get("forwardPE", "N/A"))
            elif hasattr(fast_info, "__getitem__"):
                try:
                    pb = fast_info["priceToBook"]
                except Exception:
                    pass
                try:
                    pe = fast_info["trailingPE"]
                except Exception:
                    try:
                        pe = fast_info["forwardPE"]
                    except Exception:
                        pass
            if pb == "N/A":
                pb = getattr(fast_info, "price_to_book", getattr(fast_info, "priceToBook", "N/A"))
            if pe == "N/A":
                pe = getattr(fast_info, "trailing_pe", getattr(fast_info, "forward_pe", "N/A"))
        if pb == "N/A" and pe == "N/A":
            time.sleep(0.05)  # 避免密集呼叫慢速 Yahoo API 造成 429 限流
            info = getattr(t_obj, "info", {}) or {}
            pb = info.get("priceToBook", "N/A")
            pe = info.get("trailingPE", info.get("forwardPE", "N/A"))
    except Exception:
        pb, pe = "N/A", "N/A"
    res = (pb, pe)
    with _INFO_CACHE_LOCK:
        _prune_cache_if_needed(_INFO_CACHE)
        _INFO_CACHE[ticker] = (now, res)
    return res


class DataFetcher:
    """股票與加密貨幣市場數據抓取器。"""

    def __init__(self) -> None:
        """初始化市場數據抓取器。"""
        self.finnhub_client = None
        if _FINNHUB_AVAILABLE and cfg.FINNHUB_API_KEY:
            try:
                self.finnhub_client = finnhub.Client(api_key=cfg.FINNHUB_API_KEY)
                logger.info("[market_plugin] Finnhub API 客戶端已連線")
            except Exception as e:
                logger.warning("[market_plugin] Finnhub 初始化失敗: %s", e)
        else:
            logger.warning("[market_plugin] 未設定 FINNHUB_API_KEY，新聞功能將受限")

    # ------------------------------------------------------------------
    # 預設清單
    # ------------------------------------------------------------------
    def get_target_crypto(self) -> list[str]:
        return ["BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD", "DOGE-USD"]

    # ------------------------------------------------------------------
    # Finnhub 新聞
    # ------------------------------------------------------------------
    def _filter_news_by_time(self, news_items: list[dict[str, Any]], hours: int = 12) -> list[str]:
        cutoff = time.time() - hours * 3600
        result = []
        for item in news_items:
            if item.get("datetime", 0) > cutoff:
                item_time = datetime.fromtimestamp(item["datetime"]).strftime("%Y-%m-%d %H:%M")
                headline = item.get("headline", "")
                summary = item.get("summary", "")[:40]
                result.append(f"- [{item_time}] {headline} ({summary}...)")
        return result

    def get_finnhub_news(self, ticker: str, is_crypto: bool = False) -> list[str]:
        """抓取重大新聞，優先使用 Finnhub，若未設定或無資料則自動透過 yfinance 備援。"""
        # 1. 嘗試 Finnhub
        if self.finnhub_client:
            try:
                today = datetime.now().strftime("%Y-%m-%d")
                yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
                if is_crypto:
                    crypto_map = {
                        "BTC-USD": "Bitcoin", "ETH-USD": "Ethereum",
                        "BNB-USD": "Binance", "SOL-USD": "Solana", "DOGE-USD": "Dogecoin",
                    }
                    keyword = crypto_map.get(ticker, "Crypto")
                    all_news = self.finnhub_client.general_news("crypto", min_id=0)
                    cutoff = time.time() - 24 * 3600
                    filtered = []
                    for item in all_news:
                        if item.get("datetime", 0) > cutoff:
                            h = item.get("headline", "")
                            s = item.get("summary", "")
                            if keyword.lower() in h.lower() or keyword.lower() in s.lower():
                                item_time_str = datetime.fromtimestamp(item["datetime"]).strftime("%H:%M")
                                filtered.append(f"- [{item_time_str}] {h}")
                    if filtered:
                        return filtered[:5]
                else:
                    news = self.finnhub_client.company_news(ticker, _from=yesterday, to=today)
                    res = self._filter_news_by_time(news, hours=24)[:5]
                    if res:
                        return res
            except Exception as e:
                logger.debug("[market_plugin] Finnhub 新聞抓取異常: %s", e)

        # 2. yfinance 備援抓取即時新聞
        try:
            yf_ticker = yf.Ticker(ticker)
            raw_news = yf_ticker.news or []
            fallback_news = []
            for n in raw_news[:5]:
                title = ""
                pub_time = ""
                if "content" in n and isinstance(n["content"], dict):
                    title = n["content"].get("title", "")
                    pub_time = n["content"].get("pubDate", "")[:10]
                if not title:
                    title = n.get("title", "")
                    pt = n.get("providerPublishTime")
                    if pt:
                        pub_time = datetime.fromtimestamp(pt).strftime("%m/%d %H:%M")
                if title:
                    fallback_news.append(f"- [{pub_time or '即時'}] {title}")
            if fallback_news:
                return fallback_news
        except Exception as e:
            logger.debug("[market_plugin] yfinance 新聞備援抓取失敗 (%s): %s", ticker, e)

        return ["無相關重大新聞"]

    # ------------------------------------------------------------------
    # 技術指標計算（AI 分析用）
    # ------------------------------------------------------------------
    def calculate_technicals(self, df: Any) -> dict[str, float] | None:
        try:
            close = df["Close"]
            ma5 = close.rolling(window=5).mean().iloc[-1]
            ma20 = close.rolling(window=20).mean().iloc[-1]
            ma60 = close.rolling(window=60).mean().iloc[-1]

            delta = close.diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rsi = (100 - (100 / (1 + gain / (loss + 1e-9)))).iloc[-1]

            std = close.rolling(window=20).std()
            mean = close.rolling(window=20).mean()
            upper = (mean + std * 2).iloc[-1]
            lower = (mean - std * 2).iloc[-1]
            bandwidth = ((upper - lower) / (mean.iloc[-1] + 1e-9)) * 100
            pct_b = ((close.iloc[-1] - lower) / (upper - lower + 1e-9))

            # KD (9, 3, 3)
            low9 = df["Low"].rolling(window=9, min_periods=1).min()
            high9 = df["High"].rolling(window=9, min_periods=1).max()
            rsv = ((close - low9) / (high9 - low9 + 1e-9)) * 100
            k_val = rsv.ewm(com=2, adjust=False).mean().iloc[-1]
            d_val = rsv.ewm(com=2, adjust=False).mean().ewm(com=2, adjust=False).mean().iloc[-1]

            return {
                "MA5": round(float(ma5), 2),
                "MA20": round(float(ma20), 2),
                "MA60": round(float(ma60), 2),
                "RSI": round(float(rsi), 2),
                "KD_K": round(float(k_val), 1),
                "KD_D": round(float(d_val), 1),
                "BB_Upper": round(float(upper), 2),
                "BB_Lower": round(float(lower), 2),
                "BB_Width": round(float(bandwidth), 2),
                "BB_PctB": round(float(pct_b), 2),
            }
        except Exception:
            return None

    # ------------------------------------------------------------------
    # 加密貨幣進階博弈數據
    # ------------------------------------------------------------------
    async def get_crypto_advanced_metrics_async(
        self, ticker: str, session: aiohttp.ClientSession | None = None
    ) -> dict[str, Any]:
        """非同步取得加密貨幣進階博弈數據（恐慌貪婪指數、資金費率、散戶多空比）。

        Args:
            ticker: 加密貨幣標的代碼（例如 BTC-USD）。
            session: 可選的 aiohttp ClientSession，若未提供則自動建立臨時工作階段。

        Returns:
            dict[str, Any]: 進階博弈指標字典。
        """
        metrics: dict[str, Any] = {}
        timeout = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT_SEC)

        async def _fetch(sess: aiohttp.ClientSession) -> dict[str, Any]:
            res_metrics: dict[str, Any] = {}
            # 1. 恐慌貪婪指數 (Fear & Greed Index)
            try:
                async with sess.get("https://api.alternative.me/fng/", timeout=timeout) as r:
                    if r.status == 200:
                        fng_json = await r.json()
                        data = fng_json["data"][0]
                        res_metrics["fng_value"] = data["value"]
                        res_metrics["fng_class"] = data["value_classification"]
                    else:
                        res_metrics["fng_value"] = "N/A"
                        res_metrics["fng_class"] = "N/A"
            except Exception:
                res_metrics["fng_value"] = "N/A"
                res_metrics["fng_class"] = "N/A"

            # 2. Binance 合約資金費率與全球大戶/散戶多空帳戶比
            symbol = ticker.replace("-USD", "USDT")
            try:
                async with sess.get(
                    f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}",
                    timeout=timeout,
                ) as fr:
                    if fr.status == 200:
                        fr_json = await fr.json()
                        raw_rate = float(fr_json.get("lastFundingRate", 0))
                        res_metrics["funding_rate"] = f"{raw_rate * 100:.4f}%"
                        if raw_rate > 0.01:
                            res_metrics["funding_signal"] = "⚠️ 多頭擁擠"
                        elif raw_rate < 0:
                            res_metrics["funding_signal"] = "🔥 空頭擁擠"
                        else:
                            res_metrics["funding_signal"] = "中性"

                        async with sess.get(
                            f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio"
                            f"?symbol={symbol}&period=5m&limit=1",
                            timeout=timeout,
                        ) as ls:
                            if ls.status == 200:
                                ls_data = await ls.json()
                                if ls_data:
                                    ratio = float(ls_data[0]["longShortRatio"])
                                    res_metrics["long_short_ratio"] = ratio
                                    if ratio > 2.0:
                                        res_metrics["ls_signal"] = "🔴 散戶做多"
                                    elif ratio < 0.8:
                                        res_metrics["ls_signal"] = "🟢 散戶看空"
                                    else:
                                        res_metrics["ls_signal"] = "中性"
                                else:
                                    res_metrics["long_short_ratio"] = "N/A"
                                    res_metrics["ls_signal"] = "N/A"
                            else:
                                res_metrics["long_short_ratio"] = "N/A"
                                res_metrics["ls_signal"] = "N/A"
                    else:
                        raise ValueError("Binance API Error")
            except Exception:
                res_metrics.setdefault("funding_rate", "N/A")
                res_metrics.setdefault("funding_signal", "無數據")
                res_metrics.setdefault("long_short_ratio", "N/A")
                res_metrics.setdefault("ls_signal", "無數據")

            return res_metrics

        try:
            if session is not None and not session.closed:
                metrics = await _fetch(session)
            else:
                async with aiohttp.ClientSession(timeout=timeout) as sess:
                    metrics = await _fetch(sess)
        except Exception:
            metrics.setdefault("fng_value", "N/A")
            metrics.setdefault("fng_class", "N/A")
            metrics.setdefault("funding_rate", "N/A")
            metrics.setdefault("funding_signal", "無數據")
            metrics.setdefault("long_short_ratio", "N/A")
            metrics.setdefault("ls_signal", "無數據")

        return metrics

    def get_crypto_advanced_metrics(self, ticker: str) -> dict[str, Any]:
        """同步相容包裝函式：取得加密貨幣進階博弈數據。

        Args:
            ticker: 加密貨幣標的代碼（例如 BTC-USD）。

        Returns:
            dict[str, Any]: 進階博弈指標字典。
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(lambda: asyncio.run(self.get_crypto_advanced_metrics_async(ticker)))
                return future.result()
        else:
            return asyncio.run(self.get_crypto_advanced_metrics_async(ticker))

    # ------------------------------------------------------------------
    # 晚報用 6 個月趨勢 + 新聞
    # ------------------------------------------------------------------
    def fetch_6mo_trend_and_news(self, tickers: list[str]) -> tuple[str, list[dict[str, Any]]]:
        logger.info("[market_plugin] 🔍 正在分析 %d 檔標的...", len(tickers))
        now = time.time()
        cache_key = tuple(sorted(tickers))
        with _TREND_CACHE_LOCK:
            if cache_key in _TREND_CACHE:
                cached_ts, cached_val = _TREND_CACHE[cache_key]
                if now - cached_ts < 60.0:
                    return cached_val

        results = []
        try:
            data = yf.download(
                tickers, period="6mo", interval="1d",
                group_by="ticker", progress=False, auto_adjust=True
            )
        except Exception:
            return "數據下載失敗", []

        for ticker in tickers:
            try:
                is_crypto = "-USD" in ticker
                df = data[ticker].copy() if len(tickers) > 1 else data.copy()
                df = df.dropna()
                if df.empty or "Close" not in df.columns or len(df) < 60:
                    continue

                current_price = df["Close"].iloc[-1]
                if hasattr(current_price, "item"):
                    current_price = current_price.item()

                techs = self.calculate_technicals(df)
                if not techs:
                    continue

                if techs["MA5"] > techs["MA20"] > techs["MA60"]:
                    trend = "強勢多頭"
                elif techs["MA5"] < techs["MA20"] < techs["MA60"]:
                    trend = "空頭排列"
                else:
                    trend = "盤整震盪"

                extra_info = ""
                if is_crypto:
                    cm = self.get_crypto_advanced_metrics(ticker)
                    extra_info = (
                        f"【進階博弈數據】\n"
                        f"* 恐慌指數: {cm['fng_value']} ({cm['fng_class']})\n"
                        f"* 資金費率: {cm['funding_rate']} -> {cm['funding_signal']}\n"
                        f"* 多空比: {cm['long_short_ratio']} -> {cm['ls_signal']}\n"
                    )

                news_list = self.get_finnhub_news(ticker, is_crypto)
                news_str = "\n".join(news_list) if news_list else "無重大新聞"

                results.append({
                    "ticker": ticker,
                    "price": round(float(current_price), 2),
                    "techs": techs,
                    "trend": trend,
                    "news": news_str,
                    "extra_info": extra_info,
                })
                time.sleep(0.05)  # 批次標的查詢平滑間隔
            except Exception:
                continue

        ai_context = "【高階市場數據】\n"
        for item in results:
            t = item.get("techs", {})
            rsi_val = t.get("RSI", "N/A") if isinstance(t, dict) else "N/A"
            ma20_val = t.get("MA20", "N/A") if isinstance(t, dict) else "N/A"
            bb_val = t.get("BB_Upper", "N/A") if isinstance(t, dict) else "N/A"
            ai_context += f"=== {item['ticker']} (現價: {item['price']}) ===\n"
            ai_context += f"趨勢: {item['trend']}\n"
            ai_context += f"技術指標: RSI={rsi_val}, MA20={ma20_val}, BB上軌={bb_val}\n"
            if item.get("extra_info"):
                ai_context += f"{item['extra_info']}\n"
            ai_context += f"過去12h新聞:\n{item['news']}\n\n"

        with _TREND_CACHE_LOCK:
            _prune_cache_if_needed(_TREND_CACHE)
            _TREND_CACHE[cache_key] = (now, (ai_context, results))
        return ai_context, results

    # ------------------------------------------------------------------
    # 每日指標晨報（raw_list）
    # ------------------------------------------------------------------
    def get_stock_info_raw(self, tickers: list[str]) -> list[dict[str, Any]]:
        logger.info("[market_plugin] 📊 抓取指標數據 (%d 檔)...", len(tickers))
        now = time.time()
        cache_key = tuple(sorted(tickers))
        with _RAW_STOCK_CACHE_LOCK:
            if cache_key in _RAW_STOCK_CACHE:
                cached_ts, cached_val = _RAW_STOCK_CACHE[cache_key]
                if now - cached_ts < 60.0:
                    return cached_val

        results = []
        try:
            data = yf.download(
                tickers, period="1y", interval="1d",
                group_by="ticker", progress=False, auto_adjust=True
            )
        except Exception as e:
            logger.error("[market_plugin] 下載失敗: %s", e)
            return []

        for ticker in tickers:
            try:
                df = data[ticker].copy() if len(tickers) > 1 else data.copy()
                df = df.dropna()
                if df.empty or len(df) < 20:
                    continue

                close = df["Close"].iloc[-1]
                day_pct = ((close - df["Close"].iloc[-2]) / df["Close"].iloc[-2]) * 100
                mon_pct = 0.0
                if len(df) > 21:
                    past_close = df["Close"].iloc[-22]
                    mon_pct = ((close - past_close) / past_close) * 100

                e20 = df["Close"].ewm(span=20, adjust=False).mean().iloc[-1]
                e50 = df["Close"].ewm(span=50, adjust=False).mean().iloc[-1]
                e200 = df["Close"].ewm(span=200, adjust=False).mean().iloc[-1]

                ma20 = df["Close"].rolling(window=20).mean().iloc[-1]
                ma60 = df["Close"].rolling(window=min(len(df), 60)).mean().iloc[-1]

                std20 = df["Close"].rolling(window=20).std().iloc[-1]
                bb_upper = ma20 + (2 * std20)
                bb_lower = ma20 - (2 * std20)

                # MACD (12, 26, 9)
                ema12_series = df["Close"].ewm(span=12, adjust=False).mean()
                ema26_series = df["Close"].ewm(span=26, adjust=False).mean()
                dif_series = ema12_series - ema26_series
                dem_series = dif_series.ewm(span=9, adjust=False).mean()
                macd_dif = dif_series.iloc[-1]
                macd_hist = (dif_series - dem_series).iloc[-1]

                # Volume Ratio (當日成交量 / 20日平均量)
                vol_ratio = 1.0
                if "Volume" in df.columns and len(df) >= 20:
                    vol_today = df["Volume"].iloc[-1]
                    vol_avg = df["Volume"].rolling(window=20).mean().iloc[-1]
                    if vol_avg > 0:
                        vol_ratio = float(vol_today / vol_avg)

                # 5D 漲跌幅
                day5_pct = 0.0
                if len(df) > 5:
                    past_close_5d = df["Close"].iloc[-6]
                    day5_pct = ((close - past_close_5d) / past_close_5d) * 100

                # 布林帶寬與 %B
                bb_width = ((bb_upper - bb_lower) / (ma20 + 1e-9)) * 100
                pct_b = ((close - bb_lower) / (bb_upper - bb_lower + 1e-9))

                # KD (9, 3, 3)
                low9 = df["Low"].rolling(window=9, min_periods=1).min()
                high9 = df["High"].rolling(window=9, min_periods=1).max()
                rsv = ((close - low9) / (high9 - low9 + 1e-9)) * 100
                kd_k = rsv.ewm(com=2, adjust=False).mean().iloc[-1]
                kd_d = rsv.ewm(com=2, adjust=False).mean().ewm(com=2, adjust=False).mean().iloc[-1]

                delta = df["Close"].diff()
                gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
                rsi = (100 - (100 / (1 + (gain / (loss + 1e-9))))).iloc[-1]

                pb, pe = _get_valuation_multiples(ticker)

                results.append({
                    "ticker": ticker,
                    "close": round(float(close), 2),
                    "day_pct": round(float(day_pct), 2),
                    "day5_pct": round(float(day5_pct), 2),
                    "mon_pct": round(float(mon_pct), 2),
                    "ma20": round(float(ma20), 2),
                    "ma60": round(float(ma60), 2),
                    "ema20": round(float(e20), 2),
                    "ema50": round(float(e50), 2),
                    "ema200": round(float(e200), 2),
                    "bb_upper": round(float(bb_upper), 2),
                    "bb_lower": round(float(bb_lower), 2),
                    "bb_width": round(float(bb_width), 2),
                    "pct_b": round(float(pct_b), 2),
                    "kd_k": round(float(kd_k), 1),
                    "kd_d": round(float(kd_d), 1),
                    "macd_dif": round(float(macd_dif), 2),
                    "macd_hist": round(float(macd_hist), 2),
                    "volume_ratio": round(float(vol_ratio), 2),
                    "rsi": round(float(rsi), 1),
                    "pb": round(pb, 2) if isinstance(pb, (int, float)) else "N/A",
                    "pe": round(pe, 1) if isinstance(pe, (int, float)) else "N/A",
                })
                time.sleep(0.05)  # 批次標的指標計算平滑間隔
            except Exception:
                continue

        with _RAW_STOCK_CACHE_LOCK:
            _prune_cache_if_needed(_RAW_STOCK_CACHE)
            _RAW_STOCK_CACHE[cache_key] = (now, results)
        return results

    # ------------------------------------------------------------------
    # 輔助
    # ------------------------------------------------------------------
    def get_current_prices(self, tickers: list[str]) -> dict[str, float]:
        if not tickers:
            return {}
        try:
            data = yf.download(tickers, period="1d", progress=False, auto_adjust=True)
            prices = {}
            for t in tickers:
                try:
                    val = data["Close"][t].iloc[-1] if len(tickers) > 1 else data["Close"].iloc[-1]
                    if hasattr(val, "item"):
                        val = val.item()
                    prices[t] = float(val)
                except Exception:
                    pass
            return prices
        except Exception:
            return {}
