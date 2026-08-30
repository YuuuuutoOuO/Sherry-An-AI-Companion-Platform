"""mod/spark/nlp.py - Spark 自然語言理解與條件抽取引擎。

提供雙軌智慧解析引擎：
  1. Regex 語意快路徑匹配（毫秒級直接抽取）。
  2. BotBrain (LLM) 結構化 JSON 抽取（複雜長句與語意理解）。

依據 AGENTS.md §0，此模組屬 Service / Domain 層，嚴格禁止直接引用 discord / aiohttp / sqlite3。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ===========================================================================
# 1. 中文名稱自動實體識別映射表 (NAME_TO_TICKER)
# ===========================================================================
NAME_TO_TICKER: dict[str, str] = {
    "比特幣": "BTC",
    "比特": "BTC",
    "以太幣": "ETH",
    "以太坊": "ETH",
    "以太": "ETH",
    "台積電": "2330",
    "台積": "2330",
    "輝達": "NVDA",
    "英偉達": "NVDA",
    "聯發科": "2454",
    "鴻海": "2317",
    "蘋果": "AAPL",
    "微軟": "MSFT",
    "特斯拉": "TSLA",
}

# 前綴與語助詞清洗正則
_PREFIX_CLEAN_REGEX = re.compile(
    r"^(?:當|若|如果|請|麻煩|幫我|提醒我|提醒|通知我|通知大家|告知大家|叫我|發送)+[，,\s]*",
    re.IGNORECASE,
)

# 尾綴語助詞清洗正則
_SUFFIX_CLEAN_REGEX = re.compile(
    r"[，,\s]*(?:時|的時候|的話)?[，,\s]*(?:叫我|提醒我|通知我|告知大家|通知大家|提醒大家|通知|提醒|發送)+.*?$",
    re.IGNORECASE,
)

# 價格方向關鍵字正規化
_DIRECTION_ABOVE_REGEX = re.compile(r"漲破|超過|高於|突破|大於|>=|>|above|cross_above", re.IGNORECASE)
_DIRECTION_BELOW_REGEX = re.compile(r"跌破|低於|小於|<=|<|below|cross_below", re.IGNORECASE)

# 支援的台灣與主要城市名單
_WEATHER_CITIES = (
    "台北|新北|基隆|桃園|新竹|苗栗|台中|彰化|南投|雲林|嘉義|台南|高雄|屏東|宜蘭|花蓮|台東|澎湖|金門|連江|"
    "東京|大阪|首爾|紐約|倫敦|Taipei|Tokyo|London"
)


def clean_context_prefix(text: str) -> str:
    """去除語句中的前綴語助詞（例如「當」、「如果」、「提醒我」等）。

    Args:
        text: 原始輸入自然語言文字。

    Returns:
        str: 清洗前綴後的文字。
    """
    cleaned = text.strip()
    for _ in range(3):
        new_cleaned = _PREFIX_CLEAN_REGEX.sub("", cleaned).strip()
        if new_cleaned == cleaned:
            break
        cleaned = new_cleaned
    return cleaned


def normalize_ticker_name(raw_name: str) -> str:
    """將中文標的名稱或別名映射為標準 Ticker 代號。

    Args:
        raw_name: 原始標的字串（如「台積電」、「比特幣」、「NVDA」）。

    Returns:
        str: 標準化後的 Ticker 字串。
    """
    clean = raw_name.strip()
    if clean in NAME_TO_TICKER:
        return NAME_TO_TICKER[clean]
    return clean.upper()


# ===========================================================================
# 2. Regex 語意快路徑匹配
# ===========================================================================

def parse_spark_context_by_regex(
    context: str,
    user_id: int,
    channel_id: int,
) -> dict[str, Any] | None:
    """透過正規表達式快路徑解析使用者輸入的一句話推播需求。

    支援領域：
      - 💰 股票/加密貨幣價格監控
      - ⏰ 定時排程（每日特定時間 daily_time / 間隔時間 interval）
      - 🌤️ 即時天氣與預警
      - 📰 內容與 RSS 訂閱

    Args:
        context: 使用者輸入的自然語言字串。
        user_id: 建立者 Discord 使用者 ID。
        channel_id: 目標推播頻道 ID。

    Returns:
        dict[str, Any] | None: 結構化推播候選字典，若未命中快路徑則回傳 None。
    """
    if not context or not context.strip():
        return None

    raw_text = context.strip()

    # -----------------------------------------------------------------------
    # A. 內容訂閱 (RSS / YouTube Feed) 優先比對網址
    # -----------------------------------------------------------------------
    url_match = re.search(r"https?://[^\s<>\"']+", raw_text)
    if url_match and ("訂閱" in raw_text or "追蹤" in raw_text or "feed" in raw_text.lower() or "rss" in raw_text.lower()):
        feed_url = url_match.group(0)
        remaining = raw_text.replace(feed_url, "").strip()
        remaining = clean_context_prefix(remaining)
        keyword = ""
        kw_match = re.search(r"(?:關鍵字|過濾|搜尋|追蹤)?\s*([A-Za-z0-9_\u4e00-\u9fa5]+)\s*(?:新影片|新文章|更新)?", remaining)
        if kw_match and kw_match.group(1) not in ("訂閱", "追蹤", "通知", "提醒"):
            keyword = kw_match.group(1).strip()

        return {
            "trigger_type": "rss",
            "user_id": user_id,
            "channel_id": channel_id,
            "condition": {
                "feed_url": feed_url,
                "keyword": keyword,
            },
            "message": remaining if not keyword else f"訂閱更新：{keyword}",
            "cooldown_seconds": 300,
        }

    # -----------------------------------------------------------------------
    # B. 定時與間隔排程 (Schedule / Interval / Daily)
    # -----------------------------------------------------------------------
    # 1. 循環間隔模式：例如「每 30 分鐘 提醒喝水」、「每隔 60 mins 提醒站起來動一動」
    interval_match = re.search(
        r"(?:每隔|每)\s*(\d+)\s*(分鐘|分|mins?|min|小時|hr|hours?|h)\s*(?:提醒|發送)?\s*(.+)?",
        raw_text,
        re.IGNORECASE,
    )
    if interval_match:
        val = int(interval_match.group(1))
        unit = interval_match.group(2).lower()
        msg = (interval_match.group(3) or "").strip()
        if "小時" in unit or "h" in unit or "hr" in unit:
            interval_mins = val * 60
        else:
            interval_mins = val

        return {
            "trigger_type": "schedule",
            "user_id": user_id,
            "channel_id": channel_id,
            "condition": {
                "mode": "interval",
                "interval_minutes": interval_mins,
            },
            "message": msg or f"每 {interval_mins} 分鐘定時提醒",
            "cooldown_seconds": 60,
        }

    # 2. 每日固定時間模式：例如「每天 08:30 提醒吃藥」、「每日 18:00 發送下班打卡」
    daily_match = re.search(
        r"(?:每天|每日|每晚|每早|每天早上|每天下午|每天晚上)\s*(\d{1,2}:\d{2})\s*(?:提醒|發送)?\s*(.+)?",
        raw_text,
        re.IGNORECASE,
    )
    if daily_match:
        time_str = daily_match.group(1).strip()
        if len(time_str.split(":")[0]) == 1:
            time_str = f"0{time_str}"
        msg = (daily_match.group(2) or "").strip()

        return {
            "trigger_type": "schedule",
            "user_id": user_id,
            "channel_id": channel_id,
            "condition": {
                "mode": "daily_time",
                "time": time_str,
            },
            "message": msg or f"每日 {time_str} 定時提醒",
            "cooldown_seconds": 60,
        }

    # -----------------------------------------------------------------------
    # C. 天氣預警 (Weather Alerts)
    # -----------------------------------------------------------------------
    weather_city_match = re.search(rf"({_WEATHER_CITIES})", raw_text, re.IGNORECASE)
    if weather_city_match and any(k in raw_text for k in ("雨", "降雨", "下雨", "高溫", "低溫", "氣溫", "特報", "天氣")):
        city = weather_city_match.group(1).strip()
        alert_type = "rain"
        threshold: Optional[float] = None

        if any(k in raw_text for k in ("高溫", "熱", "temp_above", "hot", "heat")):
            alert_type = "temp_above"
            num_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:度|°C|°)", raw_text)
            threshold = float(num_match.group(1)) if num_match else 34.0
        elif any(k in raw_text for k in ("低溫", "冷", "temp_below", "cold", "freeze")):
            alert_type = "temp_below"
            num_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:度|°C|°)", raw_text)
            threshold = float(num_match.group(1)) if num_match else 12.0
        elif any(k in raw_text for k in ("雨", "下雨", "降雨", "rain", "raining")):
            alert_type = "rain"
            prob_match = re.search(r"(\d+)\s*(?:%|趴|機率)", raw_text)
            if prob_match:
                threshold = float(prob_match.group(1))

        condition: dict[str, Any] = {
            "city": city,
            "alert_type": alert_type,
        }
        if threshold is not None:
            condition["threshold"] = threshold

        return {
            "trigger_type": "weather",
            "user_id": user_id,
            "channel_id": channel_id,
            "condition": condition,
            "message": raw_text,
            "cooldown_seconds": 3600,
        }

    # -----------------------------------------------------------------------
    # D. 價格監控 (Price Alerts)
    # -----------------------------------------------------------------------
    ticker: Optional[str] = None
    sorted_aliases = sorted(NAME_TO_TICKER.keys(), key=lambda s: len(s), reverse=True)
    for alias in sorted_aliases:
        if alias in raw_text:
            ticker = NAME_TO_TICKER[alias]
            break

    if not ticker:
        ticker_regex_match = re.search(r"\b([A-Za-z]{2,6}|\d{4})\b", raw_text)
        if ticker_regex_match:
            cand_ticker = ticker_regex_match.group(1).upper()
            if cand_ticker not in ("WHEN", "CALL", "ALERT", "NOTIFY", "SPARK", "DAILY"):
                ticker = cand_ticker

    price_match = re.search(r"[\$NTnt元美金\s]*([0-9]+(?:,[0-9]+)*(?:\.[0-9]+)?)\s*(?:元|美金|USD|NT\$|塊)?", raw_text)

    if ticker and price_match:
        price_str = price_match.group(1).replace(",", "")
        try:
            target_price = float(price_str)
        except ValueError:
            target_price = 0.0

        if target_price > 0:
            direction = "above"
            if _DIRECTION_BELOW_REGEX.search(raw_text):
                direction = "below"
            elif _DIRECTION_ABOVE_REGEX.search(raw_text):
                direction = "above"

            return {
                "trigger_type": "price",
                "user_id": user_id,
                "channel_id": channel_id,
                "condition": {
                    "ticker": ticker,
                    "target_price": target_price,
                    "direction": direction,
                    "one_shot": True,
                    "only_in_market_hours": True,
                },
                "message": raw_text,
                "cooldown_seconds": 3600,
            }

    return None


# ===========================================================================
# 3. BotBrain (LLM) 結構化 JSON 抽取
# ===========================================================================

_SPARK_LLM_SYSTEM_PROMPT = """你是一個精準的條件推播解析引擎。請分析使用者的自然語言推播需求，並轉換為標準結構化 JSON。

【支援的 trigger_type 與 condition 規格】
1. price (股票/加密貨幣價格警報):
   - condition: {"ticker": "標的代號(如 BTC/NVDA/2330)", "target_price": 數字浮點數, "direction": "above" 或 "below"}
2. schedule (定時推播):
   - condition: {"mode": "daily_time", "time": "HH:MM"} 或 {"mode": "interval", "interval_minutes": 整數分鐘}
   - message: "欲推播的文字內容"
3. weather (天氣預警):
   - condition: {"city": "城市名稱", "alert_type": "rain"|"temp_above"|"temp_below"|"summary", "threshold": 數值(選填)}
4. rss (內容與訂閱):
   - condition: {"feed_url": "完整網址", "keyword": "過濾關鍵字(選填)"}

【繁中實體映射規則】
- 比特幣/比特 -> BTC | 以太幣/以太坊 -> ETH | 台積電/台積 -> 2330 | 輝達/英偉達 -> NVDA | 聯發科 -> 2454 | 鴻海 -> 2317 | 蘋果 -> AAPL | 微軟 -> MSFT | 特斯拉 -> TSLA

【輸出規則】
- 僅輸出單一合法 JSON 物件，嚴禁包含任何 markdown 外框或註解。
- 格式範例：
  {"trigger_type": "price", "condition": {"ticker": "NVDA", "target_price": 100.0, "direction": "below"}, "message": ""}
- 若無法理解或不符合任何推播類型，請回傳 {"error": "無法解析推播需求"}。
"""


async def parse_spark_context_by_llm(
    bot: Any,
    context: str,
    user_id: int,
    channel_id: int,
    timeout_sec: float = 8.0,
) -> dict[str, Any] | None:
    """利用 BotBrain (LLM) 抽取複雜長句的推播結構化參數。

    Args:
        bot: Discord Bot 實例（具備 get_cog("BotBrain") 或推論隊列）。
        context: 使用者輸入之自然語言需求描述。
        user_id: 建立者 Discord ID。
        channel_id: 目標推播頻道 ID。
        timeout_sec: LLM 推論超時時間。

    Returns:
        dict[str, Any] | None: 結構化推播候選字典，失敗或超時回傳 None。
    """
    if bot is None:
        return None

    brain = getattr(bot, "get_cog", lambda name: None)("BotBrain")
    if brain is None or not hasattr(brain, "enqueue_task"):
        logger.debug("[Spark NLP] BotBrain Cog 未就緒，跳過 LLM 抽取")
        return None

    result_future: asyncio.Future[str] = asyncio.get_event_loop().create_future()

    async def _on_llm_result(raw: str) -> None:
        if not result_future.done():
            result_future.set_result(raw)

    user_prompt = f"使用者說：「{context}」\n請直接輸出抽取的 JSON："

    try:
        from handlers.bot_brain import InferenceTask
        import settings

        task = InferenceTask(
            priority=getattr(settings, "PRIORITY_TAG_RESPONSE", 10),
            system_prompt=_SPARK_LLM_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            callback=_on_llm_result,
            model=getattr(settings, "QUICK_MODEL", "gemini-1.5-flash"),
            task_id=f"spark-nlp-{user_id}",
        )
        await brain.enqueue_task(task)
        raw_output = await asyncio.wait_for(result_future, timeout=timeout_sec)
    except Exception as exc:
        logger.warning("[Spark NLP] LLM 結構化抽取失敗或超時: %s", exc)
        return None

    try:
        cleaned = raw_output.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        json_match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
        if json_match:
            cleaned = json_match.group(0)

        data = json.loads(cleaned)
        if not isinstance(data, dict) or "error" in data:
            return None

        trigger_type = str(data.get("trigger_type", "")).lower().strip()
        condition = dict(data.get("condition") or {})
        message = str(data.get("message") or "").strip()

        if trigger_type not in ("price", "schedule", "rss", "weather"):
            return None

        if trigger_type == "price" and "ticker" in condition:
            condition["ticker"] = normalize_ticker_name(str(condition["ticker"]))
            if "target_price" in condition:
                condition["target_price"] = float(condition["target_price"])
            if "direction" not in condition:
                condition["direction"] = "above"
            if "one_shot" not in condition:
                condition["one_shot"] = True
            if "only_in_market_hours" not in condition:
                condition["only_in_market_hours"] = True

        cooldown = 3600
        if trigger_type == "schedule":
            cooldown = 60
        elif trigger_type == "rss":
            cooldown = 300

        return {
            "trigger_type": trigger_type,
            "user_id": user_id,
            "channel_id": channel_id,
            "condition": condition,
            "message": message or context,
            "cooldown_seconds": cooldown,
        }
    except Exception as exc:
        logger.warning("[Spark NLP] 解析 LLM 回傳 JSON 失敗: %s | raw=%s", exc, raw_output[:200])
        return None


# ===========================================================================
# 4. 雙軌智慧解析引擎統一入口
# ===========================================================================

async def parse_spark_context(
    context: str,
    user_id: int,
    channel_id: int,
    bot: Any | None = None,
) -> dict[str, Any] | None:
    """雙軌自然語言解析統一入口：優先嘗試 Regex 快路徑，未命中時走 LLM 結構化抽取。

    Args:
        context: 使用者輸入的一句話描述。
        user_id: 使用者 Discord ID。
        channel_id: 頻道 Discord ID。
        bot: Discord Bot 實例（選填）。

    Returns:
        dict[str, Any] | None: 標準 Trigger 候選參數字典。
    """
    if not context or not context.strip():
        return None

    # Step 1 & 2: Regex 語意快路徑匹配 (0ms)
    candidate = parse_spark_context_by_regex(context, user_id=user_id, channel_id=channel_id)
    if candidate is not None:
        return candidate

    # Step 3: BotBrain (LLM) 結構化抽取
    if bot is not None:
        return await parse_spark_context_by_llm(bot, context, user_id=user_id, channel_id=channel_id)

    return None
