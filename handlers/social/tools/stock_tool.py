"""
handlers/social/tools/stock_tool.py
=====================================
股票 / 加密貨幣 / 指數查詢工具。

資料流：
  1. LLM 從使用者語句萃取 ticker 代號
  2. yfinance 抓取 60 天歷史 OHLCV
  3. pandas 計算 EMA20 / EMA60 / RSI14 / MACD
  4. mplfinance 生成 K 線圖 PNG（純 CPU）
  5. LLM（雪莉）以語氣播報技術指標報告
  6. Discord 回覆文字 + 附圖

完全不使用 GPU（yfinance / pandas / mplfinance 皆為 CPU 運算）。

支援市場格式：
  台股：2330 / 台積電 → 2330.TW
  美股：AAPL / NVDA / TSLA
  加密：BTC / 比特幣 → BTC-USD
  指數：加權指數 → ^TWII  |  S&P 500 → ^GSPC
"""

from __future__ import annotations

import asyncio
import io
import logging
import math
import re
import time
from typing import TYPE_CHECKING, Any

import discord
from discord.ext import commands

if TYPE_CHECKING:
    import pandas as pd

import settings
from core.concurrency import heavy_io_pool
from handlers.bot_brain import InferenceTask
from handlers.social.contracts import ToolResult
from handlers.social.observability import log_event, make_timer
from handlers.social.tool_base import BaseTool, ToolResponseMode
from handlers.social.utils import remove_hourglass_reaction

logger = logging.getLogger(__name__)

# Tier-1 記憶體 TTL 快取 (ticker -> (timestamp, png_bytes, indicators_dict))
_STOCK_DATA_CACHE: dict[str, tuple[float, bytes | None, dict[str, Any]]] = {}
_STOCK_CACHE_TTL_SEC: float = 120.0  # 2 分鐘快取
_MAX_STOCK_CACHE_SIZE: int = 64  # 快取容量上限，防止無界增長

# 全域 yfinance／matplotlib 併發閘門；semaphore 綁定 event loop，跨 loop 自動重建。
_STOCK_FETCH_SEMAPHORE: asyncio.Semaphore | None = None
_STOCK_FETCH_SEMAPHORE_LOOP: asyncio.AbstractEventLoop | None = None


def _get_stock_fetch_semaphore() -> asyncio.Semaphore:
    """取得目前 event loop 專屬的股市抓取 semaphore。"""
    global _STOCK_FETCH_SEMAPHORE, _STOCK_FETCH_SEMAPHORE_LOOP
    loop = asyncio.get_running_loop()
    if _STOCK_FETCH_SEMAPHORE is None or _STOCK_FETCH_SEMAPHORE_LOOP is not loop:
        limit = max(1, int(getattr(settings, "STOCK_MAX_CONCURRENT_FETCHES", 3)))
        _STOCK_FETCH_SEMAPHORE = asyncio.Semaphore(limit)
        _STOCK_FETCH_SEMAPHORE_LOOP = loop
    return _STOCK_FETCH_SEMAPHORE


def _store_stock_cache(ticker: str, now: float, png_bytes: bytes | None, indicators: dict[str, Any]) -> None:
    """寫入股市快取並自動淘汰過期與超額項目。"""
    if len(_STOCK_DATA_CACHE) >= _MAX_STOCK_CACHE_SIZE:
        expired_keys = [k for k, v in _STOCK_DATA_CACHE.items() if now - v[0] >= _STOCK_CACHE_TTL_SEC]
        for k in expired_keys:
            _STOCK_DATA_CACHE.pop(k, None)
        while len(_STOCK_DATA_CACHE) >= _MAX_STOCK_CACHE_SIZE:
            oldest_k = min(_STOCK_DATA_CACHE.keys(), key=lambda k: _STOCK_DATA_CACHE[k][0])
            _STOCK_DATA_CACHE.pop(oldest_k, None)
    _STOCK_DATA_CACHE[ticker] = (now, png_bytes, indicators)

# ---------------------------------------------------------------------------
# 常用中文名稱 → yfinance ticker 對照表
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 常見加密貨幣 Symbol 清單（用於自動補全 -USD 後綴）
# ---------------------------------------------------------------------------
_KNOWN_CRYPTOS: set[str] = {
    "BTC", "ETH", "SOL", "DOGE", "XAUT", "USDT", "USDC", "XRP", "BNB", "ADA",
    "AVAX", "LINK", "DOT", "UNI", "SUI", "PEPE", "SHIB", "TON", "TRX", "LTC",
    "BCH", "MATIC", "POL", "NEAR", "APT", "FIL", "ATOM", "ETC", "XLM", "RENDER",
    "RNDR", "KAS", "FET", "INJ", "TIA", "SEI", "OP", "ARB"
}

# ---------------------------------------------------------------------------
# 常用中文名稱 → yfinance ticker 對照表
# ---------------------------------------------------------------------------
_CN_TO_TICKER: dict[str, str] = {
    # 台股
    "台積電": "2330.TW", "聯發科": "2454.TW", "鴻海": "2317.TW",
    "台塑": "1301.TW", "中鋼": "2002.TW", "聯電": "2303.TW",
    "富邦金": "2881.TW", "國泰金": "2882.TW", "中信金": "2891.TW",
    "玉山金": "2884.TW", "元大金": "2885.TW",
    # 美股常見
    "蘋果": "AAPL", "英偉達": "NVDA", "輝達": "NVDA",
    "特斯拉": "TSLA", "微軟": "MSFT", "谷歌": "GOOGL",
    "亞馬遜": "AMZN", "臉書": "META", "Meta": "META",
    "GOOGLE": "GOOGL", "APPLE": "AAPL",
    # 加密
    "比特幣": "BTC-USD", "以太幣": "ETH-USD", "以太坊": "ETH-USD",
    "狗狗幣": "DOGE-USD", "索拉納": "SOL-USD", "泰達金": "XAUT-USD",
    "黃金代幣": "XAUT-USD", "泰達幣": "USDT-USD", "瑞波幣": "XRP-USD",
    "幣安幣": "BNB-USD", "艾達幣": "ADA-USD", "柴犬幣": "SHIB-USD",
    # 指數
    "加權指數": "^TWII", "加權": "^TWII",
    "道瓊": "^DJI", "納斯達克": "^IXIC",
    "S&P": "^GSPC", "S&P500": "^GSPC",
}

_TICKER_REGEX = re.compile(
    r"(股票|股價|K線|EMA|RSI|MACD|技術指標|技術分析|走勢|行情|幣價|趨勢|分析"
    r"|\b\d{4}(?:\.TW)?\b"           # 台股四位數
    r"|[A-Za-z0-9]{2,10}[/-]?(?:USDT|USD|BUSD|BTC|ETH)" # 加密交易對 (如 XAUT/USDT)
    r"|BTC|ETH|SOL|DOGE|XAUT|USDT|USDC|XRP|BNB|ADA|AVAX|LINK|DOT|UNI|SUI|PEPE|SHIB|TON|TRX"
    r"|加密貨幣|虛擬貨幣|幣圈|幣種|幣"
    r"|AAPL|NVDA|TSLA|MSFT|GOOGL|AMZN|META"
    r"|台積電|聯發科|鴻海|台塑|中鋼"
    r"|加權指數|道瓊|納斯達克)",
    re.IGNORECASE,
)


_TICKER_EXTRACT_SYSTEM_PROMPT = (
    "你是一個金融標的代號識別專家。請從使用者語句中萃取出所有的股票代號、加密貨幣 Symbol 或指數代碼。\n"
    "規則：\n"
    "- 若有多個標的，以逗號分隔（例如：2330, NVDA, BTC, MU）。\n"
    "- 只能輸出代號，不要有任何多餘的字元、解釋或標點。\n"
    "- ⚠️ 絕對嚴禁將當前年份、日期或時間數字（例如 2026、2026-09-05、10:00 等）誤識別為股票代號！\n"
    "- 若完全找不到任何金融標的，輸出 NONE。"
)


def _parse_extracted_tickers(raw: str) -> list[str]:
    cleaned = re.sub(r"<\/?thought>", "", raw).strip()
    if not cleaned or cleaned.upper() == "NONE":
        return []
    result: list[str] = []
    for part in cleaned.replace("，", ",").split(","):
        resolved = _resolve_ticker(part.strip())
        if resolved and resolved not in result:
            result.append(resolved)
    return result


def _resolve_ticker(raw: str) -> str:
    """將 LLM 萃取的字串轉為 yfinance ticker。"""
    raw = re.sub(r"<\/?thought>", "", raw).strip()
    raw = raw.strip().upper()

    # 防呆機制：如果 LLM 回了一大串廢話（像是安全警告），直接丟棄
    if len(raw) > 20 or "無法" in raw or "不支援" in raw or "建議" in raw or "HTTP" in raw or " " in raw:
        return ""

    # 嘗試對齊交易對：如 XAUT/USDT, XAUT-USDT, XAUTUSDT
    m_pair = re.match(r"^([A-Z0-9]{2,10})[/-]?(USDT|USD|BUSD)$", raw)
    if m_pair:
        return f"{m_pair.group(1)}-USD"

    # 嘗試中文對照
    for cn, ticker in _CN_TO_TICKER.items():
        if cn in raw:
            return ticker
    # 台股純數字
    if re.fullmatch(r"\d{4}", raw):
        return f"{raw}.TW"
    # 已含 .TW / -USD 等後綴
    if "." in raw or "-" in raw or raw.startswith("^"):
        return raw
    # 已知加密貨幣
    if raw in _KNOWN_CRYPTOS:
        return f"{raw}-USD"
    # 純英文視為美股或待下載嘗試
    if re.fullmatch(r"[A-Z]{1,8}", raw):
        return raw
    return raw


# 停用詞與市場後綴清單（避免誤識別為美股 ticker）
_STOCK_STOPWORDS: set[str] = {
    "TW", "TWO", "HK", "SS", "SZ", "US", "USD", "USDT", "BUSD", "EUR", "JPY", "TWD",
    "HELP", "INFO", "SHOW", "LIST", "VIEW", "TWII", "STOCK", "STOCKS",
    "BUY", "SELL", "HOLD", "KLINE", "EMA", "RSI", "MACD", "CHART", "PRICE",
    "AND", "FOR", "THE", "ALL", "NEW", "TOP", "APP", "API", "HTTP", "HTTPS", "WWW",
    # ── 總經與金融通用術語（避免實體抽取誤判）──
    "ETF", "ETFS", "FED", "RATE", "RATES", "CPI", "GDP", "FOMC", "SEC", "IPO", "QE", "QT",
    # ── 常見英文提問詞 ──
    "WHY", "HOW", "WHAT", "WHEN", "WHERE", "WHO", "WHICH",
    # ── 盤面數據術語 ──
    "HIGH", "LOW", "OPEN", "CLOSE", "VOL", "VOLUME",
}


def _quick_extract_tickers(text: str) -> list[str]:
    """
    純用正則一步到位提取所有 tickers。
    支援多標的同時提取（如：0050 00878 00981A / 台積電 鴻海 NVDA / BTC ETH）。
    具備佔位遮罩機制，防止 0050.TW 中的 .TW 被重複識別為美股 TW。
    """
    tickers: list[str] = []
    seen: set[str] = set()
    cleaned_text = text

    def _add(t: str):
        resolved = _resolve_ticker(t)
        if resolved and resolved not in seen:
            seen.add(resolved)
            tickers.append(resolved)

    # 0. Mask dates and times
    cleaned_text = re.sub(r"\b\d{4}[-/\u5e74]\d{1,2}(?:[-/\u6708]\d{1,2}(?:\u65e5)?)?\b", " ", cleaned_text)
    cleaned_text = re.sub(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", " ", cleaned_text)
    cleaned_text = re.sub(r"\b20[2-3]\d\s*\u5e74\b", " ", cleaned_text)

    # 1. 中文名稱對照
    for cn, t in _CN_TO_TICKER.items():
        if cn.upper() in cleaned_text.upper():
            _add(t)
            cleaned_text = re.sub(re.escape(cn), " ", cleaned_text, flags=re.IGNORECASE)

    # 2. 加密貨幣交易對正則（例如 XAUT/USDT, BTC-USDT, ETH-USD）
    pair_pattern = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9]{2,10})[/-]?(USDT|USD|BUSD|BTC|ETH)(?![A-Za-z0-9])", re.IGNORECASE)
    for m in pair_pattern.finditer(cleaned_text):
        base_coin = m.group(1).upper()
        quote_coin = m.group(2).upper()
        if base_coin not in ("HTTP", "HTTPS", "WWW") and base_coin != quote_coin:
            _add(f"{base_coin}-USD")
    cleaned_text = pair_pattern.sub(" ", cleaned_text)

    # 3. 指數代號（例如 ^TWII, ^GSPC, ^DJI）
    idx_pattern = re.compile(r"(\^[A-Za-z0-9]+)")
    for m in idx_pattern.finditer(cleaned_text):
        _add(m.group(1).upper())
    cleaned_text = idx_pattern.sub(" ", cleaned_text)

    # 4. 台股四位或五位數字+字母代號（支援帶 .TW/.TWO 或純數字，例如 0050.TW, 2330, 00981A）
    tw_pattern = re.compile(r"(?<![A-Za-z0-9])(\d{4,5}[A-Za-z]?)(?:\.(?:TW|TWO))?(?![A-Za-z0-9])", re.IGNORECASE)
    for m in tw_pattern.finditer(cleaned_text):
        _add(f"{m.group(1).upper()}.TW")
    cleaned_text = tw_pattern.sub(" ", cleaned_text)

    # 5. 純 ASCII 大寫代號（2~8 字元，例如 AAPL, NVDA, TSLA, BTC）
    ascii_matches = re.findall(r"(?<![A-Za-z])([A-Za-z]{2,8})(?![A-Za-z])", cleaned_text)
    for candidate in ascii_matches:
        cand_upper = candidate.upper()
        if len(cand_upper) >= 2 and cand_upper not in _STOCK_STOPWORDS:
            if cand_upper in _KNOWN_CRYPTOS:
                _add(f"{cand_upper}-USD")
            else:
                _add(cand_upper)

    return tickers


def _quick_extract_ticker(text: str) -> str:
    """單一標的相容介面。"""
    res = _quick_extract_tickers(text)
    return res[0] if res else ""


def _compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """用 pandas ewm 計算 RSI，不依賴外部套件。"""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / (avg_loss + 1e-9)
    return 100 - (100 / (1 + rs))


def _compute_kd(df: pd.DataFrame, n: int = 9, m1: int = 3, m2: int = 3) -> tuple[pd.Series, pd.Series]:
    """計算 KD (隨機指標, Stochastic Oscillator)。

    Args:
        df: 包含 High, Low, Close 的歷史資料 DataFrame。
        n: RSV 週期 (預設 9)。
        m1: K 值平滑週期 (預設 3)。
        m2: D 值平滑週期 (預設 3)。

    Returns:
        tuple[pd.Series, pd.Series]: (K 值序列, D 值序列)。
    """
    low_n = df["Low"].rolling(window=n, min_periods=1).min()
    high_n = df["High"].rolling(window=n, min_periods=1).max()
    rsv = ((df["Close"] - low_n) / (high_n - low_n + 1e-9)) * 100
    rsv = rsv.fillna(50.0).clip(lower=0, upper=100)
    k_series = rsv.ewm(com=m1 - 1, adjust=False).mean()
    d_series = k_series.ewm(com=m2 - 1, adjust=False).mean()
    return k_series, d_series


def _compute_bollinger_bands(series: pd.Series, period: int = 20, std_dev: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series, pd.Series]:
    """計算布林通道 (Bollinger Bands)。

    Returns:
        tuple: (中軌 MA20, 上軌 Upper, 下軌 Lower, 帶寬 Bandwidth %, %B 位階)。
    """
    mid = series.rolling(window=period, min_periods=1).mean()
    std = series.rolling(window=period, min_periods=1).std().fillna(0.0)
    upper = mid + (std * std_dev)
    lower = mid - (std * std_dev)
    bandwidth = ((upper - lower) / (mid + 1e-9)) * 100
    pct_b = (series - lower) / (upper - lower + 1e-9)
    return mid, upper, lower, bandwidth, pct_b


def _compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """計算真實波動區間 (Average True Range, ATR)。"""
    prev_close = df["Close"].shift(1)
    tr1 = df["High"] - df["Low"]
    tr2 = (df["High"] - prev_close).abs()
    tr3 = (df["Low"] - prev_close).abs()
    tr = tr1.combine(tr2, max).combine(tr3, max)
    return tr.rolling(window=period, min_periods=1).mean()


def _fetch_ticker_news(ticker: str, limit: int = 4) -> list[dict[str, str]]:
    """抓取標的即時重大新聞與情報（在背景 executor 執行）。

    具備智慧備援機制：當 ETF (如 0050, 00878) 或指數自身無專屬新聞 Feed 時，
    自動備援抓取權值核心或大盤焦點新聞。

    Args:
        ticker: 股票或加密貨幣代號。
        limit: 最大新聞則數。

    Returns:
        list[dict[str, str]]: 新聞清單 [{"title": ..., "publisher": ..., "time": ..., "url": ...}]。
    """
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        raw_news = t.news or []

        # 智慧備援：若 ETF / 指數本身無新聞，自動獲取關聯權值或大盤新聞
        if not raw_news:
            fallback_target = ""
            if ticker.endswith(".TW") or ticker.endswith(".TWO") or ticker.startswith("^TW"):
                fallback_target = "2330.TW"  # 台股權值與半導體風向球
            elif ticker in ("^GSPC", "^IXIC", "^DJI", "SPY", "QQQ"):
                fallback_target = "NVDA"     # 美股大盤動能指標
            elif ticker.endswith("-USD") and ticker != "BTC-USD":
                fallback_target = "BTC-USD"  # 幣圈大盤總指標

            if fallback_target:
                try:
                    fb_t = yf.Ticker(fallback_target)
                    raw_news = fb_t.news or []
                except Exception:
                    raw_news = []

        parsed: list[dict[str, str]] = []
        for item in raw_news[:limit]:
            title = ""
            publisher = ""
            pub_time = ""
            url = ""
            # yfinance 0.2+ 結構
            if "content" in item and isinstance(item["content"], dict):
                cnt = item["content"]
                title = cnt.get("title", "").strip()
                publisher = cnt.get("provider", {}).get("displayName", "")
                pub_date = cnt.get("pubDate", "")
                if pub_date:
                    pub_time = pub_date[:10]  # YYYY-MM-DD
                click_info = cnt.get("clickThroughUrl") or cnt.get("canonicalUrl")
                if isinstance(click_info, dict):
                    url = click_info.get("url", "")
                elif isinstance(click_info, str):
                    url = click_info

            # legacy 結構
            if not title:
                title = item.get("title", "").strip()
                publisher = item.get("publisher", "")
                provider_publish_time = item.get("providerPublishTime")
                if provider_publish_time:
                    import datetime
                    pub_time = datetime.datetime.fromtimestamp(provider_publish_time).strftime("%m/%d")
                url = item.get("link", "")

            if title:
                parsed.append({
                    "title": title,
                    "publisher": publisher or "Market News",
                    "time": pub_time or "即時",
                    "url": url or "",
                })
        return parsed
    except Exception as exc:
        logger.debug("StockTool: 新聞抓取跳過 (%s): %s", ticker, exc)
        return []


def _fetch_and_plot(ticker: str) -> tuple[bytes | None, dict]:
    """
    同步函式：抓資料、計算指標、抓取新聞、畫圖（在 executor 中執行，不阻塞 event loop）。
    回傳 (png_bytes, indicators_dict)。
    """
    orig_ticker = ticker
    now = time.time()
    if ticker in _STOCK_DATA_CACHE:
        ts, cached_png, cached_indicators = _STOCK_DATA_CACHE[ticker]
        if now - ts < _STOCK_CACHE_TTL_SEC:
            logger.info("StockTool: 命中 Tier-1 股市快取 -> %s", ticker)
            return cached_png, cached_indicators

    try:
        import matplotlib
        matplotlib.use("Agg")  # 修正: 確保在背景執行緒畫圖不會崩潰
        import matplotlib.pyplot as plt
        import mplfinance as mpf
        import pandas as pd
        import yfinance as yf

        df = yf.download(
            ticker,
            period="180d",
            interval="1d",
            progress=False,
            auto_adjust=True,
            timeout=getattr(settings, "DEFAULT_TIMEOUT_SEC", 8),
        )

        # Fallback to index (^ticker) and crypto (ticker-USD)
        if (df.empty or len(df) < 20) and re.fullmatch(r"[A-Z0-9]{2,10}", ticker):
            for candidate in (f"^{ticker}", f"{ticker}-USD"):
                try:
                    df_fallback = yf.download(
                        candidate,
                        period="180d",
                        interval="1d",
                        progress=False,
                        auto_adjust=True,
                        timeout=getattr(settings, "DEFAULT_TIMEOUT_SEC", 8),
                    )
                    if not df_fallback.empty and len(df_fallback) >= 20:
                        df = df_fallback
                        ticker = candidate
                        break
                except Exception as fb_exc:
                    logger.debug("StockTool: fallback candidate %s failed: %s", candidate, fb_exc)
        if df.empty or len(df) < 15:
            return None, {}

        # 把 MultiIndex 壓扁（yfinance >= 0.2 回傳 MultiIndex columns）
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        # 優先清洗掉未開盤或空值的無效行
        df = df.dropna(subset=["Close", "Open", "High", "Low"])
        if df.empty or len(df) < 5:
            return None, {}

        # ── 1. 多維度技術指標計算 ─────────────────────────────────────────
        # 均線 EMA20 / EMA60
        df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
        df["EMA60"] = df["Close"].ewm(span=60, adjust=False).mean()

        # 布林通道 (20, 2)
        bb_mid, bb_upper, bb_lower, bb_width, bb_pct_b = _compute_bollinger_bands(df["Close"], period=20, std_dev=2.0)
        df["BB_Mid"] = bb_mid
        df["BB_Upper"] = bb_upper
        df["BB_Lower"] = bb_lower
        df["BB_Width"] = bb_width
        df["BB_PctB"] = bb_pct_b

        # RSI(14)
        df["RSI14"] = _compute_rsi(df["Close"], 14)

        # KD (9, 3, 3)
        k_ser, d_ser = _compute_kd(df, n=9, m1=3, m2=3)
        df["KD_K"] = k_ser
        df["KD_D"] = d_ser

        # MACD (12, 26, 9)
        ema12 = df["Close"].ewm(span=12, adjust=False).mean()
        ema26 = df["Close"].ewm(span=26, adjust=False).mean()
        df["MACD"] = ema12 - ema26
        df["Signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
        df["MACD_Hist"] = df["MACD"] - df["Signal"]

        # ATR(14)
        df["ATR14"] = _compute_atr(df, period=14)

        # 成交量均線與量比 (Volume Ratio)
        if "Volume" in df.columns:
            df["Vol_MA20"] = df["Volume"].rolling(window=20, min_periods=1).mean()
            df["Vol_Ratio"] = df["Volume"] / (df["Vol_MA20"] + 1e-9)
        else:
            df["Vol_MA20"] = 0.0
            df["Vol_Ratio"] = 1.0

        # ── 2. 最新數值提取與安全防呆 ───────────────────────────────────────
        valid_df = df[df["Close"].notna()]
        if valid_df.empty:
            return None, {}

        latest = valid_df.iloc[-1]

        def _safe_float(val: Any, fallback: float = 0.0) -> float:
            try:
                f = float(val)
                return fallback if (math.isnan(f) or math.isinf(f)) else f
            except Exception:
                return fallback

        close = _safe_float(latest.get("Close"), 0.0)
        ema20 = _safe_float(latest.get("EMA20"), close)
        ema60 = _safe_float(latest.get("EMA60"), close)
        bb_u = _safe_float(latest.get("BB_Upper"), close)
        bb_l = _safe_float(latest.get("BB_Lower"), close)
        bb_w = _safe_float(latest.get("BB_Width"), 0.0)
        pct_b = _safe_float(latest.get("BB_PctB"), 0.5)
        rsi = _safe_float(latest.get("RSI14"), 50.0)
        kd_k = _safe_float(latest.get("KD_K"), 50.0)
        kd_d = _safe_float(latest.get("KD_D"), 50.0)
        macd = _safe_float(latest.get("MACD"), 0.0)
        signal = _safe_float(latest.get("Signal"), 0.0)
        macd_hist = _safe_float(latest.get("MACD_Hist"), 0.0)
        atr14 = _safe_float(latest.get("ATR14"), 0.0)
        vol_ratio = _safe_float(latest.get("Vol_Ratio"), 1.0)

        # 多週期報酬率計算
        prev_close_1d = float(valid_df["Close"].iloc[-2]) if len(valid_df) > 1 else close
        change_1d = ((close - prev_close_1d) / prev_close_1d * 100) if prev_close_1d else 0.0

        prev_close_5d = float(valid_df["Close"].iloc[-6]) if len(valid_df) > 5 else prev_close_1d
        change_5d = ((close - prev_close_5d) / prev_close_5d * 100) if prev_close_5d else 0.0

        prev_close_20d = float(valid_df["Close"].iloc[-21]) if len(valid_df) > 20 else prev_close_5d
        change_20d = ((close - prev_close_20d) / prev_close_20d * 100) if prev_close_20d else 0.0

        # 近 20 日與 60 日高低點（關鍵支撐與壓力）
        window_20 = valid_df.tail(20)
        high_20d = _safe_float(window_20["High"].max(), close) if "High" in window_20.columns else close
        low_20d = _safe_float(window_20["Low"].min(), close) if "Low" in window_20.columns else close

        window_60 = valid_df.tail(60)
        high_60d = _safe_float(window_60["High"].max(), high_20d) if "High" in window_60.columns else high_20d
        low_60d = _safe_float(window_60["Low"].min(), low_20d) if "Low" in window_60.columns else low_20d

        # ── 3. 抓取即時重大新聞 ───────────────────────────────────────────
        news_items = _fetch_ticker_news(ticker, limit=4)

        indicators = {
            "ticker": ticker,
            "close": round(close, 2),
            "change_pct": round(change_1d, 2),
            "change_5d": round(change_5d, 2),
            "change_20d": round(change_20d, 2),
            "ema20": round(ema20, 2),
            "ema60": round(ema60, 2),
            "bb_upper": round(bb_u, 2),
            "bb_lower": round(bb_l, 2),
            "bb_width": round(bb_w, 2),
            "pct_b": round(pct_b, 2),
            "rsi14": round(rsi, 1),
            "kd_k": round(kd_k, 1),
            "kd_d": round(kd_d, 1),
            "macd": round(macd, 3),
            "macd_signal": round(signal, 3),
            "macd_hist": round(macd_hist, 3),
            "atr14": round(atr14, 2),
            "vol_ratio": round(vol_ratio, 2),
            "high_20d": round(high_20d, 2),
            "low_20d": round(low_20d, 2),
            "high_60d": round(high_60d, 2),
            "low_60d": round(low_60d, 2),
            "news": news_items,
            "rows": len(df),
        }

        # ── 4. 畫專業 K 線圖（最近 60 天，含布林通道與均量線）──────────────
        plot_df = df[["Open", "High", "Low", "Close", "Volume", "EMA20", "EMA60", "BB_Upper", "BB_Lower", "Vol_MA20"]].tail(60)

        apds = [
            mpf.make_addplot(plot_df["EMA20"], color="#FF6F00", width=1.4, label="EMA20"),
            mpf.make_addplot(plot_df["EMA60"], color="#1565C0", width=1.4, label="EMA60"),
            mpf.make_addplot(plot_df["BB_Upper"], color="#7B1FA2", width=1.0, linestyle="--", alpha=0.7),
            mpf.make_addplot(plot_df["BB_Lower"], color="#7B1FA2", width=1.0, linestyle="--", alpha=0.7),
        ]
        if "Volume" in plot_df.columns and plot_df["Volume"].sum() > 0:
            apds.append(mpf.make_addplot(plot_df["Vol_MA20"], panel=1, color="#FF9800", width=1.0, alpha=0.85))

        mc = mpf.make_marketcolors(up="#ef5350", down="#26a69a", inherit=True)
        style = mpf.make_mpf_style(
            marketcolors=mc,
            gridstyle="--",
            gridcolor="#e0e0e0",
            facecolor="white",
            edgecolor="#cccccc",
            figcolor="white",
            y_on_right=False,
            rc={
                "axes.labelcolor": "black",
                "xtick.color": "black",
                "ytick.color": "black",
                "text.color": "black",
            },
        )
        fig, _ = mpf.plot(
            plot_df[["Open", "High", "Low", "Close", "Volume"]],
            type="candle",
            addplot=apds,
            volume=True,
            style=style,
            returnfig=True,
            figsize=(12, 7.5),
            title=f"\n{ticker}  |  60-Day Technical Chart (with Bollinger Bands & EMA)",
        )
        try:
            fig.patch.set_facecolor("white")

            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=100, bbox_inches="tight", facecolor="white")
            buf.seek(0)
            res_png = buf.read()
            _store_stock_cache(ticker, now, res_png, indicators)
            if orig_ticker != ticker:
                _store_stock_cache(orig_ticker, now, res_png, indicators)
            return res_png, indicators
        finally:
            plt.close(fig)

    except Exception as exc:
        logger.warning("StockTool: 資料獲取/繪圖失敗 (%s): %s", ticker, exc)
        return None, {}


async def fetch_and_plot_bounded(
    ticker: str, timeout_sec: float | None = None
) -> tuple[str, bytes | None, dict[str, Any] | None]:
    """在專用執行器中抓取股市資料，受全域併發上限與逾時保護。

    Args:
        ticker: 已解析的 yfinance ticker。
        timeout_sec: 等候抓取完成的秒數；``None`` 時使用 ``DEFAULT_TIMEOUT_SEC``。

    Returns:
        tuple[str, bytes | None, dict | None]: (ticker, PNG bytes, 指標字典)；
        逾時或失敗時後兩者為 ``None``。
    """
    timeout_val = float(
        timeout_sec
        if timeout_sec is not None
        else getattr(settings, "DEFAULT_TIMEOUT_SEC", 15)
    )
    semaphore = _get_stock_fetch_semaphore()
    async with semaphore:
        try:
            res = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    heavy_io_pool, _fetch_and_plot, ticker
                ),
                timeout=timeout_val,
            )
            resolved_ticker = res[1].get("ticker", ticker) if res[1] else ticker
            return resolved_ticker, res[0], res[1]
        except asyncio.TimeoutError:
            logger.warning("StockTool: _fetch_and_plot 超時 (ticker=%s)", ticker)
            return ticker, None, None
        except Exception as e:
            logger.warning("StockTool: _fetch_and_plot 異常 (ticker=%s): %s", ticker, e)
            return ticker, None, None


class StockTool(BaseTool):
    """股票 / 加密貨幣 / 指數查詢，附 K 線圖與技術指標報告。"""

    @property
    def intent_name(self) -> str:
        return "STOCK"

    @property
    def response_mode(self) -> ToolResponseMode:
        return ToolResponseMode.DIRECT_UI

    @property
    def llm_description(self) -> str:
        return "查詢股票、加密貨幣、指數的即時價格、K線走勢、EMA/RSI/MACD 等技術指標"

    def can_handle_by_regex(self, text: str) -> bool:
        return bool(_TICKER_REGEX.search(text))

    async def execute(
        self,
        bot: commands.Bot,
        message: discord.Message,
        user_query: str,
        vibe_snippet: str,
        full_context: str,
        **kwargs: object,
    ) -> ToolResult:
        silent = bool(kwargs.get("silent", False))
        elapsed = make_timer()
        brain_cog = bot.get_cog("BotBrain")
        if not brain_cog:
            return ToolResult.error("BotBrain 未就緒。", is_direct_rendered=not silent)
        brain: Any = brain_cog

        # ── Step 1: 提取所有提及的 tickers（支援單次查詢多標的）──────────────
        tickers = _quick_extract_tickers(user_query)
        if not tickers:
            # 純正則失敗，嘗試 LLM 萃取
            logger.debug("StockTool: Regex 失敗，嘗試 LLM 萃取 ticker...")
            extract_future: asyncio.Future[str] = bot.loop.create_future()

            async def _on_extract_reply(raw: str) -> None:
                if not extract_future.done():
                    extract_future.set_result(raw.strip())

            extract_task = InferenceTask(
                priority=settings.PRIORITY_TAG_RESPONSE,
                system_prompt=_TICKER_EXTRACT_SYSTEM_PROMPT,
                user_prompt=user_query,
                callback=_on_extract_reply,
                model=settings.LIGHT_MODEL,
                task_id=f"stock-extract-{message.id}",
            )
            await brain.enqueue_task(extract_task)

            try:
                raw_extracted = await asyncio.wait_for(extract_future, timeout=settings.STOCK_EXTRACT_TIMEOUT)
                tickers = _parse_extracted_tickers(raw_extracted)
            except asyncio.TimeoutError:
                logger.warning("StockTool: LLM ticker 萃取超時")
                tickers = []

        if not tickers:
            if not silent:
                await message.reply("唔⋯⋯名偵探沒看懂你想查哪支股票或幣耶！可以提供代號嗎？（例如：`2330`、`AAPL`、`BTC`）", mention_author=True)
            return ToolResult.no_data("未能識別出股票或加密貨幣代號。", is_direct_rendered=not silent)

        # ── Step 2: 平行抓取數據並繪圖（受全域併發上限保護）────────────────
        indicator_summaries: list[str] = []
        files: list[discord.File] = []
        timeout_val = getattr(settings, "DEFAULT_TIMEOUT_SEC", 15)

        target_tickers = tickers[:3]  # 最多查 3 檔，防濫用
        tasks = [fetch_and_plot_bounded(t, timeout_val) for t in target_tickers]
        results = await asyncio.gather(*tasks)

        for ticker, png_bytes, indicators in results:
            if not indicators:
                continue

            # 趨勢與均線狀態
            trend_str = "多頭排列向上發散 📈" if indicators["ema20"] > indicators["ema60"] else "空頭排列偏弱整理 📉"
            price = indicators["close"]
            change_1d_str = f"+{indicators['change_pct']}%" if indicators["change_pct"] >= 0 else f"{indicators['change_pct']}%"
            change_5d_str = f"+{indicators['change_5d']}%" if indicators["change_5d"] >= 0 else f"{indicators['change_5d']}%"
            change_20d_str = f"+{indicators['change_20d']}%" if indicators["change_20d"] >= 0 else f"{indicators['change_20d']}%"

            # 量能狀態
            vr = indicators["vol_ratio"]
            vol_desc = "爆量突進 (≥2.0x)" if vr >= 2.0 else "顯著放量 (≥1.3x)" if vr >= 1.3 else "量能萎縮/窒息量 (≤0.7x)" if vr <= 0.7 else "量能溫和常態"

            # 布林通道狀態
            pct_b = indicators["pct_b"]
            bb_pos = "突破上軌極強勢區" if pct_b >= 1.0 else "跌破下軌極弱勢區" if pct_b <= 0.0 else f"處於中軌與上軌間 (%B: {pct_b})" if pct_b >= 0.5 else f"處於中軌與下軌間 (%B: {pct_b})"

            # 動能 KD / RSI / MACD
            kd_k = indicators["kd_k"]
            kd_d = indicators["kd_d"]
            kd_status = "K向上突破D (黃金交叉)" if kd_k > kd_d else "K向下跌破D (死亡交叉)"
            rsi_val = indicators["rsi14"]
            rsi_status = "超買過熱區 (≥70) ⚠️" if rsi_val >= 70 else "超賣低檔區 (≤30) 💡" if rsi_val <= 30 else "常態震盪區"
            macd_h = indicators["macd_hist"]
            macd_status = "多方動能擴張 🔺" if macd_h > 0 else "空方動能主導 🔻"

            summary_lines = [
                f"【{ticker} 盤面數據與指標線索】",
                f"• 即時現價：${price} (今日 {change_1d_str} | 近5日 {change_5d_str} | 近20日 {change_20d_str})",
                f"• 均線趨勢：EMA20 ${indicators['ema20']} / EMA60 ${indicators['ema60']} → {trend_str}",
                f"• 量價結構：今日量能為20日均量 {vr}x → {vol_desc}",
                f"• 布林通道：帶寬 {indicators['bb_width']}% | 上軌 ${indicators['bb_upper']} / 下軌 ${indicators['bb_lower']} → {bb_pos}",
                f"• 動能擺盪：KD(9,3,3) K:{kd_k} D:{kd_d} ({kd_status}) | RSI14: {rsi_val} ({rsi_status}) | MACD柱: {macd_h} ({macd_status})",
                f"• 關鍵位階：近20日高點 ${indicators['high_20d']} (上方壓力) | 近20日低點 ${indicators['low_20d']} (下方支撐) | ATR14: ${indicators['atr14']}",
            ]

            news_list = indicators.get("news", [])
            if news_list:
                summary_lines.append("• 最新重大新聞與情報：")
                for n in news_list[:3]:
                    n_title = n.get("title", "")
                    n_url = n.get("url", "")
                    n_time = n.get("time", "即時")
                    n_pub = n.get("publisher", "Market")
                    if n_url:
                        summary_lines.append(f"  - [{n_time}] [{n_title}]({n_url}) ({n_pub})")
                    else:
                        summary_lines.append(f"  - [{n_time}] {n_title} ({n_pub})")
            else:
                summary_lines.append("• 最新情報：近期暫無突發重大新聞消息")

            summary = "\n".join(summary_lines)
            indicator_summaries.append(summary)

            if png_bytes:
                files.append(discord.File(io.BytesIO(png_bytes), filename=f"{ticker}_chart.png"))

        if not indicator_summaries:
            if not silent:
                await message.reply(f"哇哇，找不到代號「{', '.join(tickers)}」的行情資料耶⋯⋯", mention_author=True)
            return ToolResult.no_data(f"找不到 {', '.join(tickers)} 的股市數據。", is_direct_rendered=not silent)

        combined_summary = "\n\n".join(indicator_summaries)

        # ── Step 3: 靜音模式直接發送圖表並回傳數據，非靜音模式進行 LLM 獨立分析 ──
        if silent:
            await remove_hourglass_reaction(message, bot.user)
            if files:
                try:
                    await message.reply(
                        "📊 **已為您調閱最新市場行情與技術線圖：**",
                        files=files,
                        mention_author=True,
                    )
                except Exception as exc:
                    logger.warning("StockTool: silent 模式發送圖表失敗 (非致命): %s", exc)

            observation = f"【📊 {', '.join(tickers)} 盤面數據與指標線索】\n{combined_summary}"
            all_news_links_silent = [
                f"• [{n.get('title')}]({n.get('url')}) ({n.get('publisher', 'Market')})"
                for _, _, ind in results
                if ind and ind.get("news")
                for n in ind["news"][:2]
                if n.get("url")
            ]
            if all_news_links_silent:
                observation += "\n\n📰 **即時重大新聞來源**：\n" + "\n".join(all_news_links_silent)

            log_event(
                "stock_query",
                message_id=message.id,
                user=message.author.display_name,
                channel_id=message.channel.id,
                raw_input=user_query,
                intent="STOCK",
                intent_source="dispatch_silent",
                reply_preview=observation[:120],
                model_used=settings.PRIMARY_MODEL,
                latency_ms=elapsed(),
                extra_ticker=",".join(tickers),
            )
            return ToolResult.success(
                observation,
                is_direct_rendered=False,
                extra_data={"tickers": tickers, "indicators": indicator_summaries},
            )

        wants_analysis = bool(re.search(r"(分析|建議|怎麼看|能買嗎|該買嗎|看法|趨勢|行情|走勢|評價)", user_query))

        reply_future: asyncio.Future[str] = bot.loop.create_future()

        async def _on_stock_reply(raw: str) -> None:
            if not reply_future.done():
                reply_future.set_result(raw.strip())

        from handlers.social.persona import build_dynamic_persona
        head_persona, _tail = build_dynamic_persona(user_query, self.intent_name, channel_id=message.channel.id)

        analysis_instruction = (
            "請完全遵循你的人設背景、性格語氣、第一人稱與說話習慣，為使用者產出一份【條理分明、深度詳實且符合你當前人設風格】的完整盤面深度分析！\n\n"
            "【輸出結構與詳細內容規範】（請嚴格依下列四大重點分段深入剖析，禁止草草帶過）：\n\n"
            "📌 **一、 盤面背景與情報解析**\n"
            "• 結合最新新聞情報或該標的產業背景，解讀目前市場正在發酵什麼題材或預期？消息面對多空雙方的心理影響是什麼？（若無重大新聞，則從短中長多週期 1D/5D/20D 漲跌動能切入評估市場人氣與冷熱度）。\n\n"
            "📊 **二、 量價型態與動能深度推導**\n"
            "• 均線與趨勢位階：分析 EMA20/EMA60 排列結構、均線扣抵與股價乖離率。\n"
            "• 量價關係：剖析今日量比（Vol Ratio），是爆量攻擊、量縮洗盤、還是高檔量價背離？主力是否有進出跡象？\n"
            "• 擺盪動能交叉驗證：詳細比對 KD(9,3,3) 黃金/死亡交叉位階、RSI 超買/超賣強度，以及 MACD 柱狀體擴張或收斂動能。\n\n"
            "🛡️ **三、 關鍵攻防與支撐壓力位階**\n"
            "• 標出布林通道（上軌、中軌、下軌）與帶寬收縮/擴張意義（是否即將變盤？）。\n"
            "• 明確點出上方近 20/60 日高點壓力價位（解套賣壓區）與下方低點支撐防守點，評估當前的風險報酬比。\n\n"
            "🎯 **四、 綜合研判結論與操作指引**\n"
            "• 保持你的角色口吻與風格，給出清晰具體的盤勢定性（例如：【強勢順勢進攻】/【回踩支撐逢低佈局】/【高檔過熱減碼警戒】/【破位弱勢整理觀望】）。\n"
            "• 給出使用者接下來幾天最該留意的「關鍵盤面訊號」（例如：守住某個價位才算真突破）。\n\n"
            "※ 全文使用繁體中文，內容務必豐富扎實、論據充分，嚴格貫徹當前設定的角色口氣與性格！"
            if wants_analysis else
            "請完全遵循你的人設性格與語氣播報以下技術指標與最新新聞重點，分段條列說明盤面狀態，並給予 2~3 句符合角色的生動短評，不要給予具體買賣指示。"
        )

        reply_task = InferenceTask(
            priority=settings.PRIORITY_TAG_RESPONSE,
            system_prompt=f"{head_persona}\n\n{analysis_instruction}",
            user_prompt=f"{user_query}\n\n{combined_summary}",
            callback=_on_stock_reply,
            model=settings.PRIMARY_MODEL,
            task_id=f"stock-reply-{message.id}",
            max_tokens=max(2048, int(getattr(settings, "LLM_MAX_OUTPUT_TOKENS", 2048))) if wants_analysis else int(getattr(settings, "LLM_MAX_OUTPUT_TOKENS", 2048)),
        )
        await brain.enqueue_task(reply_task)

        try:
            reply_text = await asyncio.wait_for(reply_future, timeout=settings.STOCK_REPLY_TIMEOUT)
        except asyncio.TimeoutError:
            setattr(reply_task, "is_cancelled", True)
            logger.warning(
                "StockTool: LLM 分析生成超時 (%.1fs)，已標記取消背景推論以釋放 GPU，降級回傳指標快報",
                float(settings.STOCK_REPLY_TIMEOUT),
            )
            reply_text = f"嗯嗯，我已經為你整理好最新情報與技術數據囉！\n\n{combined_summary}"

        # 整理新聞點擊來源區塊（若有）
        all_news_links: list[str] = []
        for _ticker, _png, ind in results:
            if ind and ind.get("news"):
                for n in ind["news"][:2]:
                    if n.get("url"):
                        all_news_links.append(f"• [{n.get('title')}]({n.get('url')}) ({n.get('publisher', 'Market')})")

        if all_news_links and "http" not in reply_text:
            reply_text += "\n\n📰 **即時重大新聞來源**：\n" + "\n".join(all_news_links)

        DISCLAIMER = "\n\n⚠️ *僅供參考，不構成投資建議*"
        reply_text = reply_text + DISCLAIMER

        await remove_hourglass_reaction(message, bot.user)

        try:
            if files:
                await message.reply(reply_text, files=files, mention_author=True)
            else:
                await message.reply(reply_text, mention_author=True)
        except Exception as exc:
            logger.error("StockTool: 回覆失敗: %s", exc)

        log_event(
            "stock_query",
            message_id=message.id,
            user=message.author.display_name,
            channel_id=message.channel.id,
            raw_input=user_query,
            intent="STOCK",
            intent_source="regex_fast",
            reply_preview=reply_text[:120],
            model_used=settings.PRIMARY_MODEL,
            latency_ms=elapsed(),
            extra_ticker=",".join(tickers),
            extra_wants_analysis=wants_analysis,
        )
        return ToolResult.success(reply_text, is_direct_rendered=True)
