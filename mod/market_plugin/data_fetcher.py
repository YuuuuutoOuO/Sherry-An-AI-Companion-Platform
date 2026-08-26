"""
data_fetcher.py  (mod/market_plugin)
======================================
股票 / 加密貨幣數據抓取模組。
移植自 DC_bot_StockAnalysis-main/modules/data_fetcher.py，
並將 API Key 改為對接模組獨立的 config.py。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import requests
import yfinance as yf

try:
    import finnhub
    _FINNHUB_AVAILABLE = True
except ImportError:
    _FINNHUB_AVAILABLE = False

from mod.market_plugin import config as cfg


import logging

logger = logging.getLogger(__name__)


class DataFetcher:
    def __init__(self) -> None:
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
    def _filter_news_by_time(self, news_items: list, hours: int = 12) -> list[str]:
        cutoff = time.time() - hours * 3600
        result = []
        for item in news_items:
            if item.get("datetime", 0) > cutoff:
                t = datetime.fromtimestamp(item["datetime"]).strftime("%Y-%m-%d %H:%M")
                headline = item.get("headline", "")
                summary = item.get("summary", "")[:40]
                result.append(f"- [{t}] {headline} ({summary}...)")
        return result

    def get_finnhub_news(self, ticker: str, is_crypto: bool = False) -> list[str]:
        if not self.finnhub_client:
            return ["(Finnhub API Key 未設定)"]
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
                cutoff = time.time() - 12 * 3600
                filtered = []
                for item in all_news:
                    if item.get("datetime", 0) > cutoff:
                        h = item.get("headline", "")
                        s = item.get("summary", "")
                        if keyword.lower() in h.lower() or keyword.lower() in s.lower():
                            t = datetime.fromtimestamp(item["datetime"]).strftime("%H:%M")
                            filtered.append(f"- [{t}] {h}")
                return filtered[:5] if filtered else ["無相關重大新聞"]
            else:
                news = self.finnhub_client.company_news(ticker, _from=yesterday, to=today)
                return self._filter_news_by_time(news, hours=12)[:5]
        except Exception as e:
            if "429" in str(e):
                time.sleep(1)
            return []

    # ------------------------------------------------------------------
    # 技術指標計算（AI 分析用）
    # ------------------------------------------------------------------
    def calculate_technicals(self, df) -> dict | None:
        try:
            close = df["Close"]
            ma5 = close.rolling(window=5).mean().iloc[-1]
            ma20 = close.rolling(window=20).mean().iloc[-1]
            ma60 = close.rolling(window=60).mean().iloc[-1]

            delta = close.diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rsi = (100 - (100 / (1 + gain / loss))).iloc[-1]

            std = close.rolling(window=20).std()
            mean = close.rolling(window=20).mean()
            upper = (mean + std * 2).iloc[-1]
            lower = (mean - std * 2).iloc[-1]

            return {
                "MA5": round(float(ma5), 2),
                "MA20": round(float(ma20), 2),
                "MA60": round(float(ma60), 2),
                "RSI": round(float(rsi), 2),
                "BB_Upper": round(float(upper), 2),
                "BB_Lower": round(float(lower), 2),
            }
        except Exception:
            return None

    # ------------------------------------------------------------------
    # 加密貨幣進階博弈數據
    # ------------------------------------------------------------------
    def get_crypto_advanced_metrics(self, ticker: str) -> dict:
        metrics: dict = {}
        try:
            r = requests.get("https://api.alternative.me/fng/", timeout=3)
            data = r.json()["data"][0]
            metrics["fng_value"] = data["value"]
            metrics["fng_class"] = data["value_classification"]
        except Exception:
            metrics["fng_value"] = "N/A"
            metrics["fng_class"] = "N/A"

        symbol = ticker.replace("-USD", "USDT")
        try:
            fr = requests.get(
                f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}", timeout=3
            )
            if fr.status_code == 200:
                raw_rate = float(fr.json().get("lastFundingRate", 0))
                metrics["funding_rate"] = f"{raw_rate * 100:.4f}%"
                if raw_rate > 0.01:
                    metrics["funding_signal"] = "⚠️ 多頭擁擠"
                elif raw_rate < 0:
                    metrics["funding_signal"] = "🔥 空頭擁擠"
                else:
                    metrics["funding_signal"] = "中性"

                ls = requests.get(
                    f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio"
                    f"?symbol={symbol}&period=5m&limit=1",
                    timeout=3,
                )
                ls_data = ls.json()
                if ls_data:
                    ratio = float(ls_data[0]["longShortRatio"])
                    metrics["long_short_ratio"] = ratio
                    if ratio > 2.0:
                        metrics["ls_signal"] = "🔴 散戶做多"
                    elif ratio < 0.8:
                        metrics["ls_signal"] = "🟢 散戶看空"
                    else:
                        metrics["ls_signal"] = "中性"
                else:
                    metrics["long_short_ratio"] = "N/A"
                    metrics["ls_signal"] = "N/A"
            else:
                raise ValueError("Binance API Error")
        except Exception:
            metrics.setdefault("funding_rate", "N/A")
            metrics.setdefault("funding_signal", "無數據")
            metrics.setdefault("long_short_ratio", "N/A")
            metrics.setdefault("ls_signal", "無數據")

        return metrics

    # ------------------------------------------------------------------
    # 晚報用 6 個月趨勢 + 新聞
    # ------------------------------------------------------------------
    def fetch_6mo_trend_and_news(self, tickers: list[str]) -> tuple[str, list]:
        logger.info("[market_plugin] 🔍 正在分析 %d 檔標的...", len(tickers))
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
                time.sleep(0.5)
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

        return ai_context, results

    # ------------------------------------------------------------------
    # 每日指標晨報（raw_list）
    # ------------------------------------------------------------------
    def get_stock_info_raw(self, tickers: list[str]) -> list[dict]:
        logger.info("[market_plugin] 📊 抓取指標數據 (%d 檔)...", len(tickers))
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

                delta = df["Close"].diff()
                gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
                rsi = (100 - (100 / (1 + (gain / loss)))).iloc[-1]

                try:
                    info = yf.Ticker(ticker).info
                    pb = info.get("priceToBook", "N/A")
                    pe = info.get("trailingPE", info.get("forwardPE", "N/A"))
                except Exception:
                    pb, pe = "N/A", "N/A"

                results.append({
                    "ticker": ticker,
                    "close": round(float(close), 2),
                    "day_pct": round(float(day_pct), 2),
                    "mon_pct": round(float(mon_pct), 2),
                    "ma20": round(float(ma20), 2),
                    "ma60": round(float(ma60), 2),
                    "ema20": round(float(e20), 2),
                    "ema50": round(float(e50), 2),
                    "ema200": round(float(e200), 2),
                    "bb_upper": round(float(bb_upper), 2),
                    "bb_lower": round(float(bb_lower), 2),
                    "macd_dif": round(float(macd_dif), 2),
                    "macd_hist": round(float(macd_hist), 2),
                    "volume_ratio": round(float(vol_ratio), 2),
                    "rsi": round(float(rsi), 1),
                    "pb": round(pb, 2) if isinstance(pb, (int, float)) else "N/A",
                    "pe": round(pe, 1) if isinstance(pe, (int, float)) else "N/A",
                })
                time.sleep(0.05)
            except Exception:
                continue

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
