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
import re
from typing import TYPE_CHECKING, Any

import discord
from discord.ext import commands

if TYPE_CHECKING:
    import pandas as pd

import settings
from handlers.bot_brain import BotBrain, InferenceTask
from handlers.social.contracts import ToolCategory, ToolResult
from handlers.social.observability import log_event, make_timer
from handlers.social.tool_base import BaseTool, ToolResponseMode
from handlers.social.utils import remove_hourglass_reaction

logger = logging.getLogger(__name__)

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
    "- 若有多個標的，以逗號分隔（例如：2330, NVDA, BTC）。\n"
    "- 只能輸出代號，不要有任何多餘的字元、解釋或標點。\n"
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


def _quick_extract_tickers(text: str) -> list[str]:
    """
    純用正則一步到位提取所有 tickers。
    支援多標的同時提取（如：0050 00878 00981A / 台積電 鴻海 NVDA / BTC ETH）。
    """
    tickers: list[str] = []
    seen: set[str] = set()

    def _add(t: str):
        resolved = _resolve_ticker(t)
        if resolved and resolved not in seen:
            seen.add(resolved)
            tickers.append(resolved)

    # 1. 中文名稱對照
    for cn, t in _CN_TO_TICKER.items():
        if cn.upper() in text.upper():
            _add(t)

    # 2. 加密貨幣交易對正則（例如 XAUT/USDT, BTC-USDT）
    pairs = re.findall(r"(?<![A-Za-z0-9])([A-Za-z0-9]{2,10})[/-]?(USDT|USD|BUSD|BTC|ETH)(?![A-Za-z0-9])", text, re.IGNORECASE)
    for base_coin, quote_coin in pairs:
        base_coin = base_coin.upper()
        quote_coin = quote_coin.upper()
        if base_coin not in ("HTTP", "HTTPS", "WWW") and base_coin != quote_coin:
            _add(f"{base_coin}-USD")

    # 3. 台股四位或五位數字+字母代號 (如 0050, 00878, 00981A, 2330)
    tw_matches = re.findall(r"(?<![A-Za-z0-9])(\d{4,5}[A-Za-z]?)(?![A-Za-z0-9])", text)
    for m in tw_matches:
        _add(f"{m.upper()}.TW")

    # 4. 純 ASCII 大寫代號（2~8 字元）
    ascii_matches = re.findall(r"(?<![A-Za-z])([A-Za-z]{2,8})(?![A-Za-z])", text)
    for candidate in ascii_matches:
        cand_upper = candidate.upper()
        if len(cand_upper) >= 2 and cand_upper not in ("HELP", "INFO", "SHOW", "LIST", "VIEW", "TWII", "USDT", "BUSD", "USD"):
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


def _fetch_and_plot(ticker: str) -> tuple[bytes | None, dict]:
    """
    同步函式：抓資料、計算指標、畫圖（在 executor 中執行，不阻塞 event loop）。
    回傳 (png_bytes, indicators_dict)。
    """
    try:
        import matplotlib
        matplotlib.use("Agg")  # 修正: 確保在背景執行緒畫圖不會崩潰
        import mplfinance as mpf
        import pandas as pd
        import yfinance as yf

        df = yf.download(
            ticker,
            period="90d",
            interval="1d",
            progress=False,
            auto_adjust=True,
            timeout=getattr(settings, "DEFAULT_TIMEOUT_SEC", 8),
        )

        # 若抓取失敗或資料筆數不足，且標的為純英文字母與數字（未含 .TW / - / ^ ），嘗試自動備援加上 -USD 抓取加密貨幣行情
        if (df.empty or len(df) < 20) and re.fullmatch(r"[A-Z0-9]{2,10}", ticker):
            fallback_ticker = f"{ticker}-USD"
            df_fallback = yf.download(
                fallback_ticker,
                period="90d",
                interval="1d",
                progress=False,
                auto_adjust=True,
                timeout=getattr(settings, "DEFAULT_TIMEOUT_SEC", 8),
            )
            if not df_fallback.empty and len(df_fallback) >= 20:
                df = df_fallback
                ticker = fallback_ticker

        if df.empty or len(df) < 20:
            return None, {}

        # 把 MultiIndex 壓扁（yfinance >= 0.2 回傳 MultiIndex columns）
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        # 計算技術指標
        df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
        df["EMA60"] = df["Close"].ewm(span=60, adjust=False).mean()
        df["RSI14"] = _compute_rsi(df["Close"], 14)
        # MACD
        ema12 = df["Close"].ewm(span=12, adjust=False).mean()
        ema26 = df["Close"].ewm(span=26, adjust=False).mean()
        df["MACD"] = ema12 - ema26
        df["Signal"] = df["MACD"].ewm(span=9, adjust=False).mean()

        # 最新值
        latest = df.iloc[-1]
        close = float(latest["Close"])
        ema20 = float(latest["EMA20"])
        ema60 = float(latest["EMA60"])
        rsi = float(latest["RSI14"])
        macd = float(latest["MACD"])
        signal = float(latest["Signal"])
        prev_close = float(df["Close"].iloc[-2]) if len(df) > 1 else close
        change_pct = (close - prev_close) / prev_close * 100

        indicators = {
            "ticker": ticker,
            "close": round(close, 2),
            "change_pct": round(change_pct, 2),
            "ema20": round(ema20, 2),
            "ema60": round(ema60, 2),
            "rsi14": round(rsi, 1),
            "macd": round(macd, 3),
            "macd_signal": round(signal, 3),
            "rows": len(df),
        }

        # 畫 K 線圖（最近 60 天）
        plot_df = df[["Open", "High", "Low", "Close", "Volume"]].tail(60)
        apds = [
            mpf.make_addplot(df["EMA20"].tail(60), color="#E65100", width=1.5, label="EMA20"),
            mpf.make_addplot(df["EMA60"].tail(60), color="#1976D2", width=1.5, label="EMA60"),
        ]
        mc = mpf.make_marketcolors(up="#ef5350", down="#26a69a", inherit=True)
        style = mpf.make_mpf_style(marketcolors=mc, gridstyle="--", gridcolor="#e0e0e0",
                                   facecolor="white", edgecolor="#cccccc", figcolor="white",
                                   y_on_right=False, rc={"axes.labelcolor": "black",
                                                         "xtick.color": "black",
                                                         "ytick.color": "black",
                                                         "text.color": "black"})
        fig, _ = mpf.plot(
            plot_df, type="candle", addplot=apds, volume=True,
            style=style, returnfig=True, figsize=(12, 7),
            title=f"\n{ticker}  |  60-Day Chart",
        )
        fig.patch.set_facecolor("white")

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100, bbox_inches="tight", facecolor="white")
        buf.seek(0)
        import matplotlib.pyplot as plt
        plt.close(fig)

        return buf.read(), indicators

    except Exception as exc:
        logger.warning("StockTool: 資料獲取/繪圖失敗: %s", exc)
        return None, {}


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
            return ToolResult.error("BotBrain 未就緒。", is_direct_rendered=True)
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
                model=settings.CLASSIFIER_MODEL,
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
            await message.reply("唔⋯⋯名偵探沒看懂你想查哪支股票或幣耶！可以提供代號嗎？（例如：`2330`、`AAPL`、`BTC`）", mention_author=True)
            return ToolResult.no_data("未能識別出股票或加密貨幣代號。", is_direct_rendered=True)

        # ── Step 2: 平行抓取數據並繪圖 ──────────────────────────────────────
        indicator_summaries: list[str] = []
        files: list[discord.File] = []
        loop = asyncio.get_event_loop()

        for ticker in tickers[:3]:  # 最多查 3 檔，防濫用
            try:
                png_bytes, indicators = await asyncio.wait_for(
                    loop.run_in_executor(None, _fetch_and_plot, ticker),
                    timeout=getattr(settings, "DEFAULT_TIMEOUT_SEC", 15),
                )
            except asyncio.TimeoutError:
                logger.warning("StockTool: _fetch_and_plot 超時 (ticker=%s)", ticker)
                continue

            if not indicators:
                continue

            trend = "多頭排列 📈" if indicators["ema20"] > indicators["ema60"] else "空頭排列 📉"
            rsi_status = (
                "超買區 ⚠️" if indicators["rsi14"] > 70
                else "超賣區 💡" if indicators["rsi14"] < 30
                else "中性區"
            )
            macd_status = "黃金交叉（多方動能）" if indicators["macd"] > indicators["macd_signal"] else "死亡交叉（空方動能）"
            change_str = f"+{indicators['change_pct']}%" if indicators["change_pct"] >= 0 else f"{indicators['change_pct']}%"

            summary = (
                f"【{ticker} 技術指標快報】\n"
                f"收盤價：{indicators['close']} ({change_str})\n"
                f"EMA20：{indicators['ema20']} / EMA60：{indicators['ema60']} → {trend}\n"
                f"RSI14：{indicators['rsi14']} → {rsi_status}\n"
                f"MACD：{indicators['macd']} / Signal：{indicators['macd_signal']} → {macd_status}"
            )
            indicator_summaries.append(summary)

            if png_bytes:
                files.append(discord.File(io.BytesIO(png_bytes), filename=f"{ticker}_chart.png"))

        if not indicator_summaries:
            await message.reply(f"哇哇，找不到代號「{', '.join(tickers)}」的行情資料耶⋯⋯", mention_author=True)
            return ToolResult.no_data(f"找不到 {', '.join(tickers)} 的股市數據。", is_direct_rendered=True)

        combined_summary = "\n\n".join(indicator_summaries)

        # ── Step 3: 交給雪莉播報發送至 Discord ─────────────────────────────
        wants_analysis = bool(re.search(r"(分析|建議|怎麼看|能買嗎|該買嗎|看法)", user_query))

        reply_future: asyncio.Future[str] = bot.loop.create_future()

        async def _on_stock_reply(raw: str) -> None:
            if not reply_future.done():
                reply_future.set_result(raw.strip())

        from handlers.social.persona import build_dynamic_persona
        head_persona, _tail = build_dynamic_persona(user_query, self.intent_name, channel_id=message.channel.id)

        analysis_instruction = (
            "使用者希望你給出買賣看法，請以名偵探雪莉的直覺給出簡短的市場觀點，"
            "並記得加上免責聲明（本內容純屬娛樂，非投資建議）。"
            if wants_analysis else
            "只需要用輕鬆的語氣播報以下技術指標資料，不要給買賣建議，可以吐槽數字。"
        )

        reply_task = InferenceTask(
            priority=settings.PRIORITY_TAG_RESPONSE,
            system_prompt=f"{head_persona}\n\n{analysis_instruction}",
            user_prompt=f"{user_query}\n\n{combined_summary}",
            callback=_on_stock_reply,
            model=settings.PRIMARY_MODEL,
            task_id=f"stock-reply-{message.id}",
        )
        await brain.enqueue_task(reply_task)

        try:
            reply_text = await asyncio.wait_for(reply_future, timeout=settings.STOCK_REPLY_TIMEOUT)
        except asyncio.TimeoutError:
            reply_text = f"嗯嗯，指標我查到了！\n{combined_summary}"

        DISCLAIMER = "\n\n⚠️ *雪莉僅供參考，不構成投資建議*"
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
