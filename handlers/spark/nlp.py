"""mod/spark/nlp.py - Spark 自然語言理解與條件抽取引擎。

提供雙軌智慧解析引擎：
  1. Regex 語意快路徑匹配（毫秒級直接抽取）。
  2. BotBrain (LLM) 結構化 JSON 抽取（複雜長句與語意理解）。

依據 AGENTS.md §0，此模組屬 Service / Domain 層，嚴格禁止直接引用 discord / aiohttp / sqlite3。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 台灣/香港標準時區 (UTC+8)
TAIPEI_TZ = timezone(timedelta(hours=8))

# ===========================================================================
# 0. 動作抽取提示詞 (用於推播建立時由 LLM 定義 Action)
# ===========================================================================

_SPARK_ACTION_EXTRACTION_PROMPT = """你是一個任務動作意圖定義引擎。使用者正在設定一個定時排程推播，請分析使用者在此排程中希望雪莉執行的【具體動作】。

【可用動作規格 (action_type 與 target)】：
1. core_tool: 調用日常核心工具
   - target="PIXIV"：搜圖、找圖、色圖、美圖、大奶、巨乳、動漫角色圖、找照片、桌布。payload: {"query": "精簡搜尋標籤/關鍵字"}
   - target="DRAW"：AI 繪圖、生成新圖片、畫畫、Stable Diffusion 算圖。payload: {"query": "繪圖提示詞"}
   - target="STOCK"：股票/加密貨幣/指數 K 線行情繪圖與走勢。payload: {"query": "標的代號，如 0050, NVDA, 2330"}

   - target="MUSIC"：放音樂、播歌、點歌、播放 YouTube。payload: {"query": "歌名或關鍵字"}
   - target="CHAT"：日常性格對話、講笑話、早安晚安問候、聊天、陪伴、說辛苦了、吐槽。payload: {"query": "對話指令，如 告訴我一個好笑的笑話 或 跟我說早安"}
   - target="SEARCH"：即時聯網搜尋知識、新聞、最新消息、即時天氣/氣候預報、情報、時事、動態、懶人包、CPI、非農、財報。payload: {"query": "搜尋關鍵字，如 台北 天氣預報", "force_search": true}

2. agent_prompt: 當使用者的排程包含複合多步驟需求（例如「查最新 CPI 消息順便看 NVDA 股價跟台北天氣，再跟我說早安」）或需要雪莉主動多工具調度與自主規劃時。
   payload: {"prompt": "原始任務指令"}

3. mod_deep_search: 特權模組，多輪自主深度研究調查。
   payload: {"topic": "研究主題", "rounds": 3}

4. notify: 純文字個人備忘或鬧鐘提醒（使用者只是提醒自己/大家做某事，不需雪莉主動執行工具或講話）。
   例如：提醒我開會、提醒吃藥、提醒喝水、提醒打卡、叫我起床、泡麵好了。
   payload: {}

【輸出規則】：
- 僅輸出單一合法 JSON 物件，嚴禁包含任何 Markdown 格式或額外字元。
- 範例：{"action_type": "core_tool", "target": "SEARCH", "payload": {"query": "最新CPI消息", "force_search": true}, "message": "跟我說最新CPI的消息"}
- 範例：{"action_type": "agent_prompt", "payload": {"prompt": "查最新CPI順便看NVDA股價"}, "message": "查最新CPI順便看NVDA股價"}
- 範例：{"action_type": "core_tool", "target": "PIXIV", "payload": {"query": "大奶"}, "message": "傳一張大奶圖片"}
- 範例：{"action_type": "notify", "message": "提醒我喝水"}
"""

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

# 中文數字與常見時間數量詞映射表
_CN_NUM_MAP: dict[str, int] = {
    "半": 30,
    "一": 1,
    "二": 2,
    "兩": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
    "十三": 13,
    "十四": 14,
    "十五": 15,
    "十六": 16,
    "十七": 17,
    "十八": 18,
    "十九": 19,
    "二十": 20,
    "三十": 30,
    "四十": 40,
    "五十": 50,
    "六十": 60,
}


def parse_cn_or_arabic_int(val_str: str) -> int | None:
    """解析阿拉伯數字或常見繁簡中文數字字串為整數。

    Args:
        val_str: 數字字串（例如 "10", "十", "十五", "兩" 等）。

    Returns:
        int | None: 解析成功之整數數值，若無法解析則回傳 None。
    """
    clean_s = val_str.strip()
    if clean_s.isdigit():
        return int(clean_s)
    if clean_s in _CN_NUM_MAP:
        return _CN_NUM_MAP[clean_s]
    # 支援「二十五」、「三十五」等複合寫法
    if len(clean_s) == 3 and clean_s[1] == "十":
        tens = _CN_NUM_MAP.get(clean_s[0])
        ones = _CN_NUM_MAP.get(clean_s[2])
        if tens is not None and ones is not None:
            return tens * 10 + ones
    return None


# 前綴與語助詞清洗正則
_PREFIX_CLEAN_REGEX = re.compile(
    r"^(?:當|若|如果|請|麻煩|幫我|提醒我|提醒|通知我|通知大家|告知大家|叫我|發送)+[，,\s]*",
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


def is_pure_reminder_memo(msg: str) -> bool:
    """判斷使用者語句是否純為個人備忘/鬧鐘提醒（無需雪莉調用工具或對話）。

    Args:
        msg: 提取出的動作描述文字（例如「提醒我開會」、「叫我起床」）。

    Returns:
        bool: 是否為純個人備忘。
    """
    clean = msg.strip()
    if not clean:
        return True
    if clean in ("提醒", "提醒我", "定時提醒", "鬧鐘", "備忘", "通知"):
        return True
    if re.search(
        r"^(?:定時)?(?:提醒|叫|喊)?(?:我|大家)?(?:\s*(?:要|記得))?\s*(?:開會|吃藥|吃維他命|維他命|喝水|尿尿|動一動|打卡|起床|睡覺|泡麵|帶傘|出門|洗衣服|倒垃圾|買菜|買牛奶|繳費|運動|打電話|看|去)",
        clean,
    ):
        return True
    # 若語句明確包含提醒/叫我前綴，且不含任何工具或對話意圖
    if re.match(r"^(?:定時)?(?:提醒|叫|喊)?(?:我|大家)?(?:\s*(?:要|記得))?", clean):
        tool_in_msg = any(k in clean for k in (
            "畫", "圖", "照片", "k線", "K線", "股價", "走勢", "天氣", "氣溫",
            "放歌", "播歌", "點歌", "音樂", "最新", "消息", "新聞", "情報", "搜尋",
            "查一下", "查詢", "CPI", "非農", "早安", "晚安", "笑話", "冷笑話", "深度研究"
        ))
        if not tool_in_msg and re.match(r"^(?:提醒|叫我|喊我|記得|通知我)", clean):
            return True
    return False


def detect_action_from_message_fallback(msg: str) -> dict[str, Any]:
    """當 LLM 未就緒時，提供精確的規則快路徑動作推斷（涵蓋 Core Tools、Chat、Deep Search 與 Agent Prompt）。

    Args:
        msg: 欲分析的動作意圖文字。

    Returns:
        dict[str, Any]: 動作結構化字典。
    """
    if not msg or not msg.strip():
        return {"action_type": "notify"}

    clean = msg.strip()

    # 0. 複合多任務檢測 (Agent Prompt: Integrated Strategy 多工具自主調度)
    # 若同時命中 2 種以上工具特徵（如查新聞順便看股價查天氣，並跟我說早安），或帶有順便/一併等複合連接詞，派發為 Agent Prompt
    image_regex = (
        r"(大奶|巨乳|大胸|貧乳|平胸|色圖|美圖|搜圖|找圖|來點圖|來張圖|傳張圖|傳點圖|"
        r"圖片|照片|找照片|看圖|妹子|二次元|桌布|壁紙|插畫|本子|"
        r"pixiv|danbooru|safebooru|"
        r"(?:傳|發|來|求|找|看|搜|發送|分享)[一幾1-9]?張?[^，。\n]{0,10}(?:圖|圖片|照片))"
    )
    has_deep_search = any(k in clean for k in ("深度研究", "深度探究", "自主研究", "多輪研究", "論文級", "深度調查"))
    has_draw = bool(re.search(r"(?:幫我畫|畫一張|生成圖片|畫圖|繪製|ai繪圖|sd繪圖|\bdraw\b)", clean, re.IGNORECASE))
    has_pixiv = bool(re.search(image_regex, clean, re.IGNORECASE))
    has_stock = any(k in clean for k in ("走勢", "k線", "K線", "股價", "行情", "大盤", "技術分析")) or bool(re.search(r"\b(0050|NVDA|TSLA|2330|BTC|ETH|AAPL|MSFT)\b", clean, re.IGNORECASE))
    has_weather = any(k in clean for k in ("天氣", "氣溫", "下雨", "氣候", "即時天氣", "天氣預報"))
    has_music = bool(re.search(r"(?:放歌|播歌|點歌|聽歌|放音樂|播放音樂|來首|來點音樂|我想聽|\bmusic\b|(?:播|放|來|點)[一幾1-9]?首|(?:播|放|點|聽|推薦).*(?:歌|音樂|純音樂|bgm))", clean, re.IGNORECASE))
    has_search = bool(re.search(r"(?:最新|消息|新聞|情報|時事|動態|搜尋|查一下|查詢|懶人包|總結|報告|CPI|非農|FOMC|利率|財報|法說)", clean, re.IGNORECASE))
    has_chat_greeting = bool(re.search(r"(?:早安|晚安|笑話|冷笑話|跟我說早|跟我說晚|辛苦了)", clean, re.IGNORECASE))

    # 計算獨立工具命中數（排除純通知備忘詞）
    tool_hits = sum([has_draw, has_pixiv, has_stock, has_weather, has_music, has_search, has_chat_greeting])
    has_compound_keyword = any(k in clean for k in ("順便", "一併", "順道", "而且還要", "以及看"))
    if tool_hits >= 2 or (tool_hits >= 1 and has_compound_keyword):
        return {"action_type": "agent_prompt", "payload": {"prompt": clean}}

    # 1. 深度研究 (Deep Search)
    if has_deep_search:
        topic = re.sub(r"^(?:請|麻煩|幫我|進行|做)?(?:對|針對)?", "", clean).strip()
        return {"action_type": "mod_deep_search", "payload": {"topic": topic or clean, "rounds": 3}}

    # 2. AI 繪圖 (DRAW)
    if has_draw:
        prompt = re.sub(r"^(?:請|麻煩|幫我|我要)?(?:畫|生成|繪製)?[一幾1-9]?張?", "", clean).strip()
        return {"action_type": "core_tool", "target": "DRAW", "payload": {"query": prompt or clean}}

    # 3. 二次元圖搜 (PIXIV)
    if has_pixiv:
        query = clean
        query = re.sub(r"^(?:請|麻煩|幫我|我要)?(?:傳|發送|發|分享|給我|看|找|來|搜)?[一幾1-9]?張?", "", query).strip()
        query = re.sub(r"(?:給我|謝謝|喔|啊|啦|吧)+$", "", query).strip()
        return {"action_type": "core_tool", "target": "PIXIV", "payload": {"query": query or clean}}

    # 4. 即時聯網搜尋情報 (SEARCH)
    if has_search:
        q_search = re.sub(r"^(?:請|麻煩|幫我|我要)?(?:查一下|查詢|搜尋|看一下|看|跟我說|告訴我|報告)?", "", clean).strip()
        return {"action_type": "core_tool", "target": "SEARCH", "payload": {"query": q_search or clean, "force_search": True}}

    # 5. 股票/K線行情 (STOCK)
    if has_stock:
        return {"action_type": "core_tool", "target": "STOCK", "payload": {"query": clean}}

    # 6. 即時天氣 (收斂至 SEARCH 聯網檢索)
    if has_weather:
        return {"action_type": "core_tool", "target": "SEARCH", "payload": {"query": clean, "force_search": True}}

    # 7. 音樂播放 (MUSIC)
    if has_music:
        q = re.sub(r"^(?:請|麻煩|幫我|我要)?(?:放歌|播歌|點歌|放音樂|播放音樂|來首|我想聽|播放|播|放|來點)?", "", clean).strip()
        return {"action_type": "core_tool", "target": "MUSIC", "payload": {"query": q or clean}}

    # 8. 純文字備忘 (NOTIFY)
    if is_pure_reminder_memo(clean):
        return {"action_type": "notify"}

    # 9. 對話/講笑話/早安問候 (CHAT)
    return {"action_type": "core_tool", "target": "CHAT", "payload": {"query": clean}}


def sanitize_schedule_condition(
    condition: dict[str, Any],
    context: str,
) -> dict[str, Any]:
    """定時排程時間模式校驗守衛：糾正 LLM 或抽取器誤判單次/循環間隔的問題。

    遵循使用者期待：以 LLM 決定核心動作與意圖，以時間規則守護排程模式 (mode) 零失誤。

    Args:
        condition: LLM 或規則抽取之原始 condition 字典。
        context: 使用者輸入之原始上下文文字。

    Returns:
        dict[str, Any]: 糾偏後的 condition 字典。
    """
    new_cond = dict(condition)
    mode = str(new_cond.get("mode", "")).lower().strip()
    text = context.strip()

    # 1. 檢測是否有明確的「循環間隔」特徵詞（如「每10分鐘」、「每隔半小時」、「每2小時一次」）
    interval_match = re.search(
        r"(?:每隔|每)\s*([0-9]+|[一二兩三四五六七八九十半]+)\s*(?:個)?(分鐘|分|mins?|min|小時|hr|hours?|h)",
        text,
        re.IGNORECASE,
    )
    is_interval_text = bool(interval_match) or bool(re.search(r"(?:每隔|每)\s*(?:個)?(半小時|小時)", text, re.IGNORECASE))

    if is_interval_text:
        # 若被誤標為 once 或 daily_time，強制修正為 interval！
        if mode != "interval":
            logger.info("[Spark NLP Guard] 糾正排程模式: %s -> interval (context=%s)", mode, text)
            new_cond["mode"] = "interval"
            new_cond.pop("target_time", None)
            new_cond.pop("time", None)

        # 確保 interval_minutes 為合法正整數
        current_mins = new_cond.get("interval_minutes")
        if current_mins is None or not isinstance(current_mins, int) or current_mins <= 0:
            if interval_match:
                raw_num = interval_match.group(1)
                raw_unit = interval_match.group(2).lower()
                num = parse_cn_or_arabic_int(raw_num) or 10
                if raw_num == "半":
                    new_cond["interval_minutes"] = 30
                elif "小時" in raw_unit or "h" in raw_unit or "hr" in raw_unit:
                    new_cond["interval_minutes"] = num * 60
                else:
                    new_cond["interval_minutes"] = num
            elif "半小時" in text:
                new_cond["interval_minutes"] = 30
            elif "小時" in text:
                new_cond["interval_minutes"] = 60
            else:
                new_cond["interval_minutes"] = 10

        new_cond.setdefault("tz_offset", 8)
    elif re.search(r"([0-9]+|[一二兩三四五六七八九十半]+)\s*(?:個)?(分鐘|分|mins?|min|小時|hr|hours?|h|秒|secs?|s)後", text, re.IGNORECASE) and mode != "once":
        # 2. 檢測是否有明確的「單次相對時間」特徵詞（如「10分鐘後」、「1小時後」）
        logger.info("[Spark NLP Guard] 糾正排程模式: %s -> once (context=%s)", mode, text)
        new_cond["mode"] = "once"
        new_cond.pop("interval_minutes", None)
        new_cond.setdefault("tz_offset", 8)

    # 3. 補齊 max_runs 與 days_of_week
    if "max_runs" not in new_cond:
        max_runs_m = re.search(r"(?:總共|共|限制)?\s*([0-9]+|[一二兩三四五六七八九十]+)\s*次(?:就好)?", text)
        if max_runs_m:
            parsed_runs = parse_cn_or_arabic_int(max_runs_m.group(1))
            if parsed_runs and parsed_runs > 0:
                new_cond["max_runs"] = parsed_runs

    if "days_of_week" not in new_cond:
        if re.search(r"(?:每個)?(?:工作日|平日|週一到週五|周一到周五|禮拜一到禮拜五)", text):
            new_cond["days_of_week"] = [0, 1, 2, 3, 4]
        elif re.search(r"(?:每個)?(?:週末|周末|假日)", text):
            new_cond["days_of_week"] = [5, 6]
        else:
            w_m = re.search(r"(?:每(?:個)?(?:週|周|禮拜|星期)([一二三四五六日天1-7]))", text)
            if w_m:
                w_map = {"一": 0, "1": 0, "二": 1, "2": 1, "三": 2, "3": 2, "四": 3, "4": 3, "五": 4, "5": 4, "六": 5, "6": 5, "日": 6, "天": 6, "7": 6}
                new_cond["days_of_week"] = [w_map.get(w_m.group(1), 0)]

    return new_cond


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
    raw_text = re.sub(r"^(?:<@!?\d+>|@?雪莉)\s*", "", raw_text).strip()

    # 抽取執行次數上限 (max_runs)，例如「總共3次」、「提醒5次就好」
    max_runs: int | None = None
    max_runs_m = re.search(r"(?:，|,|\s)*(?:總共|共|限制|提醒)?\s*([0-9]+|[一二兩三四五六七八九十]+)\s*次(?:就好)?", raw_text)
    if max_runs_m:
        raw_m_str = max_runs_m.group(0)
        parsed_r = parse_cn_or_arabic_int(max_runs_m.group(1))
        if parsed_r and parsed_r > 0:
            if any(k in raw_m_str for k in ("總共", "共", "限制", "就好")) or parsed_r > 1 or (not re.search(r"(?:每|每隔).*(?:分|秒|小時|min|hr|h)\s*一次", raw_text)):
                max_runs = parsed_r

    # -----------------------------------------------------------------------
    # 0. 智慧語意巡檢守門員 (Web Monitor)
    # -----------------------------------------------------------------------
    # 匹配例如：「每小時幫我看看有沒有CPI或降息的重大新聞，有的話通知我」、「如果有最新CPI消息才通知我」
    web_monitor_match = re.search(
        r"(?:每隔|每)?\s*([0-9]+|[一二兩三四五六七八九十半]+)?\s*(?:個)?(小時|分鐘|分|hr|h)?\s*"
        r"(?:幫我)?(?:巡檢|監控|監測|追蹤|看看有沒有|看看)\s*(.+?)(?:的)?(?:重大|最新)?(?:新聞|消息|情報|進展|動態)"
        r"(?:.*?(?:有的話|有新進展|有重大新聞)?(?:才|時|再)?(?:通知|叫|提醒)(?:我)?)?",
        raw_text,
    )
    if not web_monitor_match:
        web_monitor_match = re.search(
            r"(?:如果|若)?(?:有)(?:關於)?(.+?)(?:的)?(?:重大|最新)?(?:新聞|消息|情報|進展|動態)(?:才|時|再)?(?:通知|叫|提醒)(?:我)?",
            raw_text,
        )
    if web_monitor_match:
        if len(web_monitor_match.groups()) >= 3 and web_monitor_match.group(3):
            raw_interval = web_monitor_match.group(1)
            raw_unit = (web_monitor_match.group(2) or "").lower()
            query = web_monitor_match.group(3).strip()
            interval_mins = 60
            if raw_interval:
                n = parse_cn_or_arabic_int(raw_interval) or 1
                if "分" in raw_unit:
                    interval_mins = n
                else:
                    interval_mins = n * 60
        else:
            query = web_monitor_match.group(1).strip()
            interval_mins = 30

        ai_cond = "有最新或重大相關消息"
        if "重大" in raw_text:
            ai_cond = "重大新聞與實質進展"

        cond_web: dict[str, Any] = {
            "query": query,
            "ai_condition": ai_cond,
            "interval_minutes": interval_mins,
        }
        if max_runs is not None:
            cond_web["max_runs"] = max_runs

        return {
            "trigger_type": "web_monitor",
            "user_id": user_id,
            "channel_id": channel_id,
            "condition": cond_web,
            "message": raw_text,
            "action": {"action_type": "core_tool", "target": "SEARCH", "payload": {"query": query, "force_search": True}},
            "cooldown_seconds": interval_mins * 60,
        }

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
    # B. 定時與間隔排程 (Schedule / Interval / Daily / Once)
    # -----------------------------------------------------------------------
    # 星期篩選提取 (days_of_week)
    extracted_days_of_week: list[int] | None = None
    if re.search(r"(?:每個)?(?:工作日|平日|週一到週五|周一到周五|禮拜一到禮拜五)", raw_text):
        extracted_days_of_week = [0, 1, 2, 3, 4]
    elif re.search(r"(?:每個)?(?:週末|周末|假日)", raw_text):
        extracted_days_of_week = [5, 6]
    else:
        week_match = re.search(r"(?:每(?:個)?(?:週|周|禮拜|星期)([一二三四五六日天1-7]))", raw_text)
        if week_match:
            w_map = {"一": 0, "1": 0, "二": 1, "2": 1, "三": 2, "3": 2, "四": 3, "4": 3, "五": 4, "5": 4, "六": 5, "6": 5, "日": 6, "天": 6, "7": 6}
            extracted_days_of_week = [w_map.get(week_match.group(1), 0)]

    # 1. 循環間隔模式：例如「每 30 分鐘 提醒喝水」、「每半小時總結聊天」、「每隔 60 mins 提醒動一動」
    half_match = re.search(r"(?:每隔|每)\s*(?:個)?半小時\s*(?:提醒|發送)?\s*(.+)?", raw_text, re.IGNORECASE)
    if half_match:
        msg = (half_match.group(1) or "").strip()
        if max_runs_m:
            msg = re.sub(r"(?:，|,|\s)*(?:總共|共|限制)?\s*[0-9一二兩三四五六七八九十]+\s*次(?:就好)?", "", msg).strip()
        action = {"action_type": "notify"} if is_pure_reminder_memo(msg) else detect_action_from_message_fallback(msg)
        cond_half: dict[str, Any] = {
            "mode": "interval",
            "interval_minutes": 30,
            "tz_offset": 8,
        }
        if extracted_days_of_week:
            cond_half["days_of_week"] = extracted_days_of_week
        if max_runs is not None:
            cond_half["max_runs"] = max_runs
        return {
            "trigger_type": "schedule",
            "user_id": user_id,
            "channel_id": channel_id,
            "condition": cond_half,
            "message": msg or "每 30 分鐘定時提醒",
            "action": action,
            "cooldown_seconds": 60,
        }

    interval_match = re.search(
        r"(?:每隔|每)\s*([0-9]+|[一二兩三四五六七八九十半]+)\s*(?:個)?(分鐘|分|mins?|min|小時|hr|hours?|h)\s*(?:提醒我|提醒|發送|傳送|傳|通知我|通知|叫我|執行|跑)?\s*(.+)?",
        raw_text,
        re.IGNORECASE,
    )
    # 支援倒裝句型：例如「傳一張大奶圖給我，每10分鐘一次」
    reverse_interval_match = None
    if not interval_match:
        reverse_interval_match = re.search(
            r"^(.+?)[，,\s]+(?:每隔|每)\s*([0-9]+|[一二兩三四五六七八九十半]+)\s*(?:個)?(分鐘|分|mins?|min|小時|hr|hours?|h)(?:\s*一次)?$",
            raw_text,
            re.IGNORECASE,
        )

    if interval_match or reverse_interval_match:
        if interval_match:
            raw_val = interval_match.group(1)
            unit = interval_match.group(2).lower()
            msg = (interval_match.group(3) or "").strip()
            if interval_match.start() > 0:
                clean_msg_candidate = re.sub(r"^(?:一次|傳一次|發一次)[，,\s]*", "", msg).strip()
                if not clean_msg_candidate:
                    msg = raw_text[:interval_match.start()].rstrip("，, ")
        else:
            assert reverse_interval_match is not None
            msg = (reverse_interval_match.group(1) or "").strip()
            raw_val = reverse_interval_match.group(2)
            unit = reverse_interval_match.group(3).lower()

        val = parse_cn_or_arabic_int(raw_val) or 10
        if raw_val == "半":
            interval_mins = 30
        elif "小時" in unit or "h" in unit or "hr" in unit:
            interval_mins = val * 60
        else:
            interval_mins = val

        # 去除 msg 開頭或結尾的「一次」或「傳一次」等干擾字眼
        msg = re.sub(r"^(?:一次|傳一次|發一次)[，,\s]*", "", msg).strip()
        msg = re.sub(r"[，,\s]*(?:一次|傳一次|發一次)$", "", msg).strip()
        if max_runs_m:
            msg = re.sub(r"(?:，|,|\s)*(?:總共|共|限制)?\s*[0-9一二兩三四五六七八九十]+\s*次(?:就好)?", "", msg).strip()

        action = {"action_type": "notify"} if is_pure_reminder_memo(msg) else detect_action_from_message_fallback(msg)
        cond_inter: dict[str, Any] = {
            "mode": "interval",
            "interval_minutes": interval_mins,
            "tz_offset": 8,
        }
        if extracted_days_of_week:
            cond_inter["days_of_week"] = extracted_days_of_week
        if max_runs is not None:
            cond_inter["max_runs"] = max_runs

        return {
            "trigger_type": "schedule",
            "user_id": user_id,
            "channel_id": channel_id,
            "condition": cond_inter,
            "message": msg or f"每 {interval_mins} 分鐘定時提醒",
            "action": action,
            "cooldown_seconds": 60,
        }

    # 2. 每日固定時間模式（含工作日/週末/特定星期）
    # 例如「每天 08:30 提醒吃藥」、「每個工作日早上9點提醒我開會」、「每週三晚上8點跟我說最新AI新聞」
    daily_match = re.search(
        r"(?:每天|每日|每晚|每早|每天早上|每天下午|每天晚上|每個工作日|平日|週一到週五|每週末|每個週末|每[週周禮拜星期][一二三四五六日天1-7])\s*"
        r"(?:(?P<period>早上|早晨|上午|中午|下午|傍晚|晚上|夜間)\s*)?"
        r"(?P<hour>\d{1,2}|[一二兩三四五六七八九十]{1,2})(?:(?::|\.)(?P<min_colon>\d{0,2})|點(?P<min_dot>\d{0,2}|[一二兩三四五六七八九十]{1,3}|半)?)(?:\s*分)?\s*"
        r"(?:提醒|發送|跟我說|告訴我|通知|叫我|執行|傳)?\s*(?P<msg>.+)?",
        raw_text,
        re.IGNORECASE,
    )
    if daily_match:
        hour_raw = daily_match.group("hour")
        hour = int(hour_raw) if hour_raw.isdigit() else (parse_cn_or_arabic_int(hour_raw) or 8)
        period = daily_match.group("period")
        min_colon = daily_match.group("min_colon")
        min_dot = daily_match.group("min_dot")
        if min_colon:
            minute = int(min_colon) if min_colon.isdigit() else (parse_cn_or_arabic_int(min_colon) or 0)
        elif min_dot == "半":
            minute = 30
        elif min_dot:
            minute = int(min_dot) if min_dot.isdigit() else (parse_cn_or_arabic_int(min_dot) or 0)
        else:
            minute = 0

        match_str = daily_match.group(0)
        if period in ("下午", "晚上", "傍晚", "夜間") or "晚" in match_str:
            if hour < 12:
                hour += 12

        time_str = f"{hour:02d}:{minute:02d}"
        msg = (daily_match.group("msg") or "").strip()
        if max_runs_m:
            msg = re.sub(r"(?:，|,|\s)*(?:總共|共|限制)?\s*[0-9一二兩三四五六七八九十]+\s*次(?:就好)?", "", msg).strip()

        action = {"action_type": "notify"} if is_pure_reminder_memo(msg) else detect_action_from_message_fallback(msg)
        cond_daily: dict[str, Any] = {
            "mode": "daily_time",
            "time": time_str,
            "tz_offset": 8,
        }
        if extracted_days_of_week:
            cond_daily["days_of_week"] = extracted_days_of_week
        if max_runs is not None:
            cond_daily["max_runs"] = max_runs

        return {
            "trigger_type": "schedule",
            "user_id": user_id,
            "channel_id": channel_id,
            "condition": cond_daily,
            "message": msg or f"每日 {time_str} 定時提醒",
            "action": action,
            "cooldown_seconds": 60,
        }

    # 3. 單次相對時間模式：例如「5分鐘後 提醒我要傳色圖給ㄜㄋ」、「10分鐘後叫我」、「1小時後提醒開會」
    once_rel_match = re.search(
        r"([0-9]+|[一二兩三四五六七八九十半]+)\s*(?:個)?(分鐘|分|mins?|min|小時|hr|hours?|h|秒|secs?|s)後\s*(?:提醒我|提醒|通知我|通知|叫我)?\s*(.+)?",
        raw_text,
        re.IGNORECASE,
    )
    if once_rel_match:
        raw_val = once_rel_match.group(1)
        val = parse_cn_or_arabic_int(raw_val) or 1
        unit = once_rel_match.group(2).lower()
        msg = (once_rel_match.group(3) or "").strip()
        if max_runs_m:
            msg = re.sub(r"(?:，|,|\s)*(?:總共|共|限制)?\s*[0-9一二兩三四五六七八九十]+\s*次(?:就好)?", "", msg).strip()

        if "小時" in unit or "h" in unit or "hr" in unit:
            delta_seconds = val * 3600
        elif "秒" in unit or "s" in unit or "sec" in unit:
            delta_seconds = val
        elif raw_val == "半" and ("分" in unit or "鐘" in unit):
            delta_seconds = 30 * 60
        else:
            delta_seconds = val * 60

        target_dt = (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).astimezone(TAIPEI_TZ)
        action = {"action_type": "notify"} if is_pure_reminder_memo(msg) else detect_action_from_message_fallback(msg)

        cond_rel: dict[str, Any] = {
            "mode": "once",
            "target_time": target_dt.isoformat(),
            "tz_offset": 8,
        }
        if max_runs is not None:
            cond_rel["max_runs"] = max_runs

        return {
            "trigger_type": "schedule",
            "user_id": user_id,
            "channel_id": channel_id,
            "condition": cond_rel,
            "message": msg or f"{val} {unit}後單次提醒",
            "action": action,
            "cooldown_seconds": 60,
        }

    # 4. 單次指定時刻模式（無「每」前綴）：例如「8.30分跟我說最新CPI的消息」、「明天早上8:30告訴我...」、「今天晚上9點提醒我開會」
    abs_time_match = re.search(
        r"(?<!每)(?<!每隔)(?<!每天)(?<!每日)(?<!每早)(?<!每晚)"
        r"(?:(?P<day_rel>今天|今晚|今早|明天|明早|明晚|後天)\s*)?"
        r"(?:(?P<period>早上|早晨|上午|中午|下午|傍晚|晚上|半夜|夜間)\s*)?"
        r"(?P<hour>\d{1,2})(?:(?::|\.)(?P<minute_colon>\d{0,2})|點(?P<minute_dot>\d{0,2}|半)?)(?:\s*分)?"
        r"\s*(?:跟我說|告訴我|提醒我|提醒|通知我|通知|叫我|執行|發送|傳送|傳|回報)?\s*(?P<msg>.+)",
        raw_text,
        re.IGNORECASE,
    )
    if abs_time_match:
        day_rel = abs_time_match.group("day_rel")
        period = abs_time_match.group("period")
        hour = int(abs_time_match.group("hour"))
        min_colon = abs_time_match.group("minute_colon")
        min_dot = abs_time_match.group("minute_dot")
        if min_colon:
            minute = int(min_colon)
        elif min_dot == "半":
            minute = 30
        elif min_dot:
            minute = int(min_dot)
        else:
            minute = 0

        is_pm = False
        if period in ("下午", "晚上", "傍晚", "夜間", "半夜") or day_rel in ("今晚", "明晚"):
            is_pm = True
            if hour < 12:
                hour += 12
        elif period == "中午":
            if hour < 12 and hour != 12:
                hour += 12

        now_tw = datetime.now(timezone.utc).astimezone(TAIPEI_TZ)
        target_day = now_tw.date()
        if day_rel in ("明天", "明早", "明晚"):
            target_day = now_tw.date() + timedelta(days=1)
        elif day_rel == "後天":
            target_day = now_tw.date() + timedelta(days=2)

        try:
            target_dt = datetime(target_day.year, target_day.month, target_day.day, hour, minute, 0, tzinfo=TAIPEI_TZ)
        except ValueError:
            target_dt = now_tw + timedelta(minutes=10)

        # 若未指定「明天/後天」，且計算出來的時刻早於當前台灣時間：
        if not day_rel or day_rel in ("今天", "今晚", "今早"):
            if target_dt <= now_tw:
                if not period and not is_pm and hour < 12 and (target_dt + timedelta(hours=12)) > now_tw:
                    target_dt = target_dt + timedelta(hours=12)
                else:
                    target_dt = target_dt + timedelta(days=1)

        msg = (abs_time_match.group("msg") or "").strip()
        if max_runs_m:
            msg = re.sub(r"(?:，|,|\s)*(?:總共|共|限制)?\s*[0-9一二兩三四五六七八九十]+\s*次(?:就好)?", "", msg).strip()

        action = detect_action_from_message_fallback(msg)
        cond_dict: dict[str, Any] = {
            "mode": "once",
            "target_time": target_dt.isoformat(),
            "tz_offset": 8,
        }
        if max_runs is not None:
            cond_dict["max_runs"] = max_runs

        return {
            "trigger_type": "schedule",
            "user_id": user_id,
            "channel_id": channel_id,
            "condition": cond_dict,
            "message": msg or f"{target_dt.strftime('%H:%M')} 單次提醒",
            "action": action,
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
# 3. BotBrain (LLM) 結構化 JSON 抽取與動作定義
# ===========================================================================

_SPARK_LLM_SYSTEM_PROMPT = """你是一個精準的條件推播與主動任務解析引擎。請分析使用者的自然語言推播/排程需求，並轉換為標準結構化 JSON。所有時間皆以台灣/香港時間 (UTC+8) 為準。

【支援的 trigger_type 與 condition 規格】
1. price (股票/加密貨幣價格警報):
   - condition: {"ticker": "標的代號(如 BTC/NVDA/2330)", "target_price": 數字浮點數, "direction": "above" 或 "below"}
2. schedule (定時排程/單次提醒):
   - condition:
     * 循環間隔 (週期重複): {"mode": "interval", "interval_minutes": 整數分鐘, "tz_offset": 8}
       ⚠️【重要強約束】：只要語意包含「每」、「每隔」、「每N分鐘」、「每N小時」等週期循環詞，絕對必須設定為 mode="interval"，嚴禁輸出 mode="once" 或計算為單次目標時間！
     * 單次提醒 (如幾分鐘後、明天/後天某時、特定日期生日開會): {"mode": "once", "target_time": "ISO格式日期時間或時間點描述", "tz_offset": 8}
     * 每日定時: {"mode": "daily_time", "time": "HH:MM", "tz_offset": 8}
   - message: "欲推播或提醒的內容描述"
3. weather (天氣預警):
   - condition: {"city": "城市名稱", "alert_type": "rain"|"temp_above"|"temp_below"|"summary", "threshold": 數值(選填)}
4. rss (內容與訂閱):
   - condition: {"feed_url": "完整網址", "keyword": "過濾關鍵字(選填)"}
5. web_monitor (智慧網頁巡檢守門員):
   - condition: {"query": "搜尋關鍵字", "ai_condition": "語意觸發條件(例如：有重大消息或數據公布時)", "interval_minutes": 30}

【觸發動作 action 規格】
- 若涉及「最新消息/新聞/情報/CPI/非農/FOMC/財報/法說/搜尋/查一下/懶人包」：
  "action": {"action_type": "core_tool", "target": "SEARCH", "payload": {"query": "搜尋關鍵字", "force_search": true}}
- 若涉及「研究/深度研究/深度探究/論文/分析走勢/深入調查」：
  "action": {"action_type": "mod_deep_search", "payload": {"topic": "研究主題"}}
- 若涉及複合多步驟需求（例如同時要查新聞、看股價、查天氣或問候）：
  "action": {"action_type": "agent_prompt", "payload": {"prompt": "原始指令內容"}}
- 若涉及「二次元搜圖/美圖/色圖/大奶/找照片/桌布」：
  "action": {"action_type": "core_tool", "target": "PIXIV", "payload": {"query": "搜尋關鍵字"}}
- 若涉及「AI繪圖/畫畫/生成圖片/算圖」：
  "action": {"action_type": "core_tool", "target": "DRAW", "payload": {"query": "繪圖提示詞"}}
- 若涉及「股票/K線/行情/走勢」：
  "action": {"action_type": "core_tool", "target": "STOCK", "payload": {"query": "標的代號"}}
- 若涉及「即時天氣/氣溫/降雨」：
  "action": {"action_type": "core_tool", "target": "SEARCH", "payload": {"query": "城市名稱 天氣", "force_search": true}}
- 若涉及「播放音樂/放歌/點歌」：
  "action": {"action_type": "core_tool", "target": "MUSIC", "payload": {"query": "歌名或關鍵字"}}
- 若涉及「日常對話/講笑話/早安晚安問候/聊天陪伴/說辛苦了」：
  "action": {"action_type": "core_tool", "target": "CHAT", "payload": {"query": "對話指令"}}
- 一般個人備忘提醒通知（如開會、吃藥、喝水、打卡、起床、泡麵）：
  "action": {"action_type": "notify"}

【繁中實體映射規則】
- 比特幣/比特 -> BTC | 以太幣/以太坊 -> ETH | 台積電/台積 -> 2330 | 輝達/英偉達 -> NVDA | 聯發科 -> 2454 | 鴻海 -> 2317 | 蘋果 -> AAPL | 微軟 -> MSFT | 特斯拉 -> TSLA

【輸出規則】
- 僅輸出單一合法 JSON 物件，嚴禁包含任何 markdown 外框或註解。
- 格式範例 1 (循環間隔調用工具)：
  {"trigger_type": "schedule", "condition": {"mode": "interval", "interval_minutes": 10, "tz_offset": 8}, "action": {"action_type": "core_tool", "target": "PIXIV", "payload": {"query": "大奶"}}, "message": "傳送一張大奶圖給 Yuuto"}
- 格式範例 2 (每日定時對話)：
  {"trigger_type": "schedule", "condition": {"mode": "daily_time", "time": "08:00", "tz_offset": 8}, "action": {"action_type": "core_tool", "target": "CHAT", "payload": {"query": "告訴我一個好笑的笑話"}}, "message": "講個笑話"}
- 格式範例 3 (單次提醒備忘)：
  {"trigger_type": "schedule", "condition": {"mode": "once", "target_time": "2026-09-03T22:30:00", "tz_offset": 8}, "action": {"action_type": "notify"}, "message": "提醒我開會"}
- 若無法理解或不符合任何推播類型，請回傳 {"error": "無法解析推播需求"}。
"""


async def define_spark_action_by_llm(
    bot: Any,
    intent_text: str,
    timeout_sec: float = 15.0,
) -> dict[str, Any]:
    """利用 BotBrain (LLM) 在推播建立時精確定義所需執行的 Action。

    Args:
        bot: Discord Bot 實例。
        intent_text: 使用者希望在排程觸發時執行的動作文字。
        timeout_sec: LLM 推論逾時秒數。

    Returns:
        dict[str, Any]: 標準化的 action 字典。
    """
    if bot is None or not intent_text or not intent_text.strip():
        return {"action_type": "notify"}

    brain = getattr(bot, "get_cog", lambda name: None)("BotBrain")
    if brain is None or not hasattr(brain, "enqueue_task"):
        return detect_action_from_message_fallback(intent_text)

    result_future: asyncio.Future[str] = asyncio.get_event_loop().create_future()

    async def _on_action_result(raw: str) -> None:
        if not result_future.done():
            result_future.set_result(raw)

    user_prompt = f"使用者希望在排程觸發時執行：「{intent_text}」\n請輸出定義好的 JSON："

    try:
        from handlers.bot_brain import InferenceTask
        import settings

        task = InferenceTask(
            priority=getattr(settings, "PRIORITY_TAG_RESPONSE", 10),
            system_prompt=_SPARK_ACTION_EXTRACTION_PROMPT,
            user_prompt=user_prompt,
            callback=_on_action_result,
            model=settings.LIGHT_MODEL,
            task_id=f"spark-action-{id(intent_text)}",
        )
        await brain.enqueue_task(task)
        raw_output = await asyncio.wait_for(result_future, timeout=timeout_sec)
        cleaned = raw_output.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        json_match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
        if json_match:
            cleaned = json_match.group(0)
        data = json.loads(cleaned)
        if isinstance(data, dict) and "action_type" in data:
            action_type = str(data.get("action_type", "notify")).lower().strip()
            target = str(data.get("target", "")).upper().strip()
            payload = dict(data.get("payload") or {})
            res: dict[str, Any] = {"action_type": action_type}
            if target:
                res["target"] = target
            if payload:
                res["payload"] = payload
            return res
    except Exception as exc:
        logger.warning("[Spark NLP] define_spark_action_by_llm 異常或逾時: %s，切換至規則判定", exc)

    return detect_action_from_message_fallback(intent_text)


async def parse_spark_context_by_llm(
    bot: Any,
    context: str,
    user_id: int,
    channel_id: int,
    timeout_sec: float = 30.0,
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

    now_tw = datetime.now(timezone.utc).astimezone(TAIPEI_TZ)
    weekday_names = ["一", "二", "三", "四", "五", "六", "日"]
    weekday_str = weekday_names[now_tw.weekday()]
    current_time_str = f"當前台灣時間 (UTC+8)：{now_tw.strftime('%Y-%m-%d %H:%M:%S')} (星期{weekday_str})"

    user_prompt = f"{current_time_str}\n使用者說：「{context}」\n請直接輸出抽取的 JSON："

    try:
        from handlers.bot_brain import InferenceTask
        import settings

        task = InferenceTask(
            priority=getattr(settings, "PRIORITY_TAG_RESPONSE", 10),
            system_prompt=_SPARK_LLM_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            callback=_on_llm_result,
            model=settings.LIGHT_MODEL,
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
        action = dict(data.get("action") or {})

        if trigger_type not in ("price", "schedule", "rss", "weather", "web_monitor"):
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

        # 若 LLM 未回傳 action，根據上下文語意自動補齊
        if not action:
            action = detect_action_from_message_fallback(message or context)

        cooldown = 3600
        if trigger_type == "schedule":
            condition = sanitize_schedule_condition(condition, context)
            cooldown = 60
        elif trigger_type == "rss":
            cooldown = 300
        elif trigger_type == "web_monitor":
            cooldown = int(condition.get("interval_minutes", 30)) * 60

        return {
            "trigger_type": trigger_type,
            "user_id": user_id,
            "channel_id": channel_id,
            "condition": condition,
            "message": message or context,
            "action": action,
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

    若由 Regex 快路徑匹配到定時排程，會在建立當下透過 LLM 精準定義欲調用的 Action (Core Tool / Chat / Deep Search / Notify)，
    存入 Trigger.action；時間到達觸發時由 SparkActionRunner 直連執行，零二次 LLM 推論。

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

    # Step 1: Regex 語意快路徑匹配 (0ms)
    candidate = parse_spark_context_by_regex(context, user_id=user_id, channel_id=channel_id)
    if candidate is not None:
        # 若為定時排程，讓 LLM 在建立當下定義具體的動作 (Action)，觸發時直連執行！
        if candidate.get("trigger_type") == "schedule":
            candidate["condition"] = sanitize_schedule_condition(candidate["condition"], context)
            msg = str(candidate.get("message") or "").strip()
            if is_pure_reminder_memo(msg):
                candidate["action"] = {"action_type": "notify"}
            elif bot is not None:
                defined_action = await define_spark_action_by_llm(bot, msg)
                candidate["action"] = defined_action
            else:
                candidate["action"] = detect_action_from_message_fallback(msg)
        return candidate

    # Step 2: BotBrain (LLM) 全句結構化抽取（時間 + 動作）
    if bot is not None:
        cand = await parse_spark_context_by_llm(bot, context, user_id=user_id, channel_id=channel_id)
        if cand and cand.get("trigger_type") == "schedule":
            cand["condition"] = sanitize_schedule_condition(cand["condition"], context)
        return cand

    return None
