"""mod/spark/checkers/price_checker.py - 價格監控觸發策略。

純業務邏輯層：依據 AGENTS.md §0 禁止直接 import aiohttp/requests，
透過 StockClientProtocol 介面進行依賴注入。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from ..models import Trigger, TriggerResult

logger = logging.getLogger(__name__)

# 加密貨幣常見代號集合
_CRYPTO_SYMBOLS = {
    "BTC",
    "ETH",
    "SOL",
    "DOGE",
    "BNB",
    "XRP",
    "ADA",
    "AVAX",
    "DOT",
    "MATIC",
    "LINK",
    "UNI",
    "SHIB",
    "LTC",
    "BCH",
    "NEAR",
    "APT",
    "SUI",
    "PEPE",
    "TRX",
}


def _easter_sunday(year: int) -> date:
    """計算特定年份的復活節星期日 (Butcher 演算法)。

    Args:
        year: 西元年份。

    Returns:
        date: 該年復活節星期日日期。
    """
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    ll = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ll) // 451
    month = (h + ll - 7 * m + 114) // 31
    day = ((h + ll - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """計算月份中第 N 個特定星期幾 (0=週一, 6=週日)。

    Args:
        year: 年份。
        month: 月份 (1-12)。
        weekday: 星期 (0=Monday...6=Sunday)。
        n: 第幾次出現 (1-5)。

    Returns:
        date: 符合條件的日期。
    """
    first_day = date(year, month, 1)
    offset = (weekday - first_day.weekday()) % 7
    day = 1 + offset + (n - 1) * 7
    return date(year, month, day)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """計算月份中最後一個特定星期幾。

    Args:
        year: 年份。
        month: 月份 (1-12)。
        weekday: 星期 (0=Monday...6=Sunday)。

    Returns:
        date: 符合條件的日期。
    """
    if month in (1, 3, 5, 7, 8, 10, 12):
        last_day = date(year, month, 31)
    elif month in (4, 6, 9, 11):
        last_day = date(year, month, 30)
    else:
        is_leap = (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)
        last_day = date(year, month, 29 if is_leap else 28)

    offset = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=offset)


def _is_us_holiday(dt: date) -> bool:
    """判斷指定日期是否為美國主要股市休假日 (NYSE/NASDAQ 休市日)。

    包含：元旦、馬丁路德金紀念日、華盛頓誕辰(總統日)、受難日(受難週五)、
    陣亡將士紀念日、六月節獨立日、美國獨立日、勞動節、感恩節、聖誕節。

    Args:
        dt: 欲檢查的日期物件 (美東時間)。

    Returns:
        bool: 若為法定/休假日回傳 True。
    """
    year = dt.year
    holidays: set[date] = set()

    # 1. 固定節日與其調休 (週日順延週一，週六提前週五)
    fixed_dates = [
        date(year, 1, 1),    # New Year's Day
        date(year, 6, 19),   # Juneteenth
        date(year, 7, 4),    # Independence Day
        date(year, 12, 25),  # Christmas Day
    ]
    for f_date in fixed_dates:
        holidays.add(f_date)
        if f_date.weekday() == 6:  # Sunday -> Monday
            holidays.add(f_date + timedelta(days=1))
        elif f_date.weekday() == 5:  # Saturday -> Friday
            holidays.add(f_date - timedelta(days=1))

    # 2. 浮動節日
    holidays.add(_nth_weekday(year, 1, 0, 3))       # MLK Day: 3rd Monday in Jan
    holidays.add(_nth_weekday(year, 2, 0, 3))       # Presidents Day: 3rd Monday in Feb
    holidays.add(_easter_sunday(year) - timedelta(days=2))  # Good Friday: 2 days before Easter
    holidays.add(_last_weekday(year, 5, 0))         # Memorial Day: Last Monday in May
    holidays.add(_nth_weekday(year, 9, 0, 1))       # Labor Day: 1st Monday in Sep
    holidays.add(_nth_weekday(year, 11, 3, 4))      # Thanksgiving: 4th Thursday in Nov

    return dt in holidays


def is_market_open(ticker: str, now_dt: datetime | None = None) -> tuple[bool, str]:
    """判斷特定股票或加密貨幣當前是否處於開盤交易時段。

    支援市場：
      - 🪙 加密貨幣 (Crypto): 24/7 全天候運作。
      - 🇹🇼 台股 (TW Stock): 台灣時間 (UTC+8) 週一至週五 09:00 ~ 13:30（週末與國定假日休市）。
      - 🇺🇸 美股 (US Stock): 美東時間 (America/New_York) 週一至週五 09:30 ~ 16:00（週末與聯邦節日休市）。

    Args:
        ticker: 標的代號（例如 "BTC-USD", "2330", "NVDA", "AAPL"）。
        now_dt: 當前時間（選填，預設為 UTC 現在時間）。

    Returns:
        tuple[bool, str]: (是否開盤, 當前市場狀態說明)。
    """
    clean_ticker = ticker.upper().strip()
    now = now_dt or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    # -----------------------------------------------------------------------
    # 1. 加密貨幣判定 (24/7 全天候開盤)
    # -----------------------------------------------------------------------
    is_crypto = False
    if clean_ticker.endswith("-USD") or clean_ticker.endswith("USDT") or clean_ticker.endswith("-USDT"):
        is_crypto = True
    else:
        base_sym = clean_ticker.split(".")[0].split("-")[0]
        if base_sym in _CRYPTO_SYMBOLS:
            is_crypto = True

    if is_crypto:
        return True, "加密貨幣 (24/7 全天候交易)"

    # -----------------------------------------------------------------------
    # 2. 台股判定 (Asia/Taipei 週一至週五 09:00 ~ 13:30)
    # -----------------------------------------------------------------------
    is_tw = False
    if clean_ticker.endswith(".TW") or clean_ticker.endswith(".TWO"):
        is_tw = True
    elif clean_ticker.isdigit() and len(clean_ticker) in (4, 5, 6):
        is_tw = True

    if is_tw:
        tw_now = now.astimezone(ZoneInfo("Asia/Taipei"))
        weekday = tw_now.weekday()
        if weekday >= 5:  # 5=Saturday, 6=Sunday
            return False, "台股週末休市"

        current_time = tw_now.time()
        open_time = time(9, 0)
        close_time = time(13, 30)

        if open_time <= current_time <= close_time:
            return True, "台股盤中交易時段 (09:00-13:30)"
        else:
            return False, "台股非交易時段 (已收盤)"

    # -----------------------------------------------------------------------
    # 3. 美股判定 (America/New_York 美東時間週一至週五 09:30 ~ 16:00)
    # -----------------------------------------------------------------------
    ny_now = now.astimezone(ZoneInfo("America/New_York"))
    ny_weekday = ny_now.weekday()
    if ny_weekday >= 5:  # 5=Saturday, 6=Sunday
        return False, "美股週末休市"

    if _is_us_holiday(ny_now.date()):
        return False, "美股假日休市 (NYSE/NASDAQ 休市)"

    ny_time = ny_now.time()
    us_open_time = time(9, 30)
    us_close_time = time(16, 0)

    if us_open_time <= ny_time <= us_close_time:
        return True, "美股常規盤中時段 (09:30-16:00)"
    else:
        return False, "美股非交易時段 (已收盤)"


class StockClientProtocol(Protocol):
    """股票/加密貨幣客戶端協定介面。"""

    async def get_price(self, ticker: str) -> float | None:
        """取得標的物即時現價。"""
        ...


class PriceChecker:
    """價格警報檢查器：支援高於、低於、向上突破、跌破等條件判定與開盤防騷擾機制。"""

    trigger_type: str = "price"

    def __init__(self, stock_client: StockClientProtocol) -> None:
        """初始化價格檢查器。

        Args:
            stock_client: 實作 StockClientProtocol 的報價客戶端。
        """
        self._client = stock_client

    async def should_fire(
        self,
        trigger: Trigger,
        now_dt: datetime | None = None,
    ) -> TriggerResult:
        """評估價格觸發器條件。

        包含開盤時段篩選、Anti-Spam 價格未變動抑制與單次達標處理。

        Args:
            trigger: 欲檢查的 Trigger 實例。
            now_dt: 當前時間（選填，供測試或自訂時間注入）。

        Returns:
            TriggerResult: 判定成果與數值摘要。
        """
        cond = trigger.condition
        ticker = str(cond.get("ticker", "")).strip()
        if not ticker:
            return TriggerResult(should_fire=False, current_value="未指定標的代號")

        try:
            target_price = float(cond.get("target_price", 0.0))
        except (ValueError, TypeError):
            return TriggerResult(should_fire=False, current_value="無效的目標價格")

        direction = str(cond.get("direction", "above")).lower().strip()
        now = now_dt or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        # -------------------------------------------------------------------
        # 1. 市場開盤時段判定與休市抑制 (智慧降噪防騷擾)
        # -------------------------------------------------------------------
        only_in_market_hours = bool(cond.get("only_in_market_hours", True))
        if only_in_market_hours:
            is_open, market_desc = is_market_open(ticker, now)
            if not is_open:
                return TriggerResult(
                    should_fire=False,
                    current_value=f"非開盤時段暫停報價推播 ({market_desc})",
                    detail_data={"ticker": ticker, "market_status": market_desc},
                )

        # -------------------------------------------------------------------
        # 2. 獲取現價
        # -------------------------------------------------------------------
        current_price = await self._client.get_price(ticker)
        if current_price is None:
            return TriggerResult(
                should_fire=False,
                current_value=f"無法獲取 {ticker} 即時價格",
            )

        # -------------------------------------------------------------------
        # 3. 價格未變動抑制 (Anti-Spam)
        # -------------------------------------------------------------------
        last_trig_raw = cond.get("last_triggered_price")
        if last_trig_raw is not None:
            try:
                last_trig = float(last_trig_raw)
                if abs(last_trig - current_price) < 1e-6:
                    return TriggerResult(
                        should_fire=False,
                        current_value=f"{ticker} 現價 ${current_price:,.2f} 與上次觸發價相同（未發生變動，略過重複推播）",
                        detail_data={"ticker": ticker, "current_price": current_price},
                    )
            except (ValueError, TypeError):
                pass

        last_price_raw = cond.get("last_price")
        last_price: float | None = float(last_price_raw) if last_price_raw is not None else None

        should_fire = False
        summary = ""

        if direction in ("above", ">=", ">"):
            if current_price >= target_price:
                should_fire = True
                summary = f"{ticker} 現價 ${current_price:,.2f}（已達到或高於目標價 ${target_price:,.2f}）"
            else:
                summary = f"{ticker} 現價 ${current_price:,.2f}（未達目標價 ${target_price:,.2f}）"

        elif direction in ("below", "<=", "<"):
            if current_price <= target_price:
                should_fire = True
                summary = f"{ticker} 現價 ${current_price:,.2f}（已達到或低於目標價 ${target_price:,.2f}）"
            else:
                summary = f"{ticker} 現價 ${current_price:,.2f}（高於目標價 ${target_price:,.2f}）"

        elif direction in ("cross_above", "breakout"):
            if last_price is not None and last_price < target_price <= current_price:
                should_fire = True
                summary = f"🚀 {ticker} 強勢突破目標價！由 ${last_price:,.2f} 漲至 ${current_price:,.2f}"
            elif current_price >= target_price and last_price is None:
                # 首次檢查若已高於，直接觸發
                should_fire = True
                summary = f"🚀 {ticker} 現價 ${current_price:,.2f} 高於目標價 ${target_price:,.2f}"
            else:
                summary = f"{ticker} 現價 ${current_price:,.2f} 未向上突破 ${target_price:,.2f}"

        elif direction in ("cross_below", "breakdown"):
            if last_price is not None and last_price > target_price >= current_price:
                should_fire = True
                summary = f"⚠️ {ticker} 跌破支撐價！由 ${last_price:,.2f} 跌至 ${current_price:,.2f}"
            elif current_price <= target_price and last_price is None:
                should_fire = True
                summary = f"⚠️ {ticker} 現價 ${current_price:,.2f} 低於目標價 ${target_price:,.2f}"
            else:
                summary = f"{ticker} 現價 ${current_price:,.2f} 未跌破 ${target_price:,.2f}"

        else:
            summary = f"未知的價格判定方向: {direction}"

        detail_data: dict[str, Any] = {
            "ticker": ticker,
            "current_price": current_price,
            "target_price": target_price,
            "direction": direction,
            "last_price": current_price,  # 記錄本次價格供下次比對
        }

        if should_fire:
            detail_data["last_triggered_price"] = current_price
            one_shot = bool(cond.get("one_shot", True))
            if one_shot:
                detail_data["one_shot_fired"] = True

        return TriggerResult(
            should_fire=should_fire,
            current_value=summary,
            detail_data=detail_data,
        )
