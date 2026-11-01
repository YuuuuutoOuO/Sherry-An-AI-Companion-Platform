"""tests/unit/test_spark_nlp.py - Spark NLP 解析單元測試。

遵循 AAA (Arrange / Act / Assert) 原則。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
import pytest

from handlers.spark.nlp import (
    clean_context_prefix,
    normalize_ticker_name,
    parse_spark_context,
    parse_spark_context_by_llm,
    parse_spark_context_by_regex,
)


@pytest.fixture
def anyio_backend() -> str:
    """設定 anyio 測試後端為 asyncio。"""
    return "asyncio"


def test_clean_context_prefix() -> None:
    """測試前綴與語助詞清洗。"""
    assert clean_context_prefix("當如果提醒我 NVDA 100") == "NVDA 100"
    assert clean_context_prefix("請幫我通知大家 台積電 1000") == "台積電 1000"
    assert clean_context_prefix("若比特幣跌破 60000") == "比特幣跌破 60000"


def test_normalize_ticker_name() -> None:
    """測試中文名稱與別名映射至標準 Ticker。"""
    assert normalize_ticker_name("台積電") == "2330"
    assert normalize_ticker_name("比特幣") == "BTC"
    assert normalize_ticker_name("輝達") == "NVDA"
    assert normalize_ticker_name("聯發科") == "2454"
    assert normalize_ticker_name("鴻海") == "2317"
    assert normalize_ticker_name("蘋果") == "AAPL"
    assert normalize_ticker_name("微軟") == "MSFT"
    assert normalize_ticker_name("特斯拉") == "TSLA"
    assert normalize_ticker_name("nvda") == "NVDA"


def test_parse_spark_context_price() -> None:
    """測試價格警報 context 解析與實體映射。"""
    # 1. 繁中名稱 + 目標價
    cand = parse_spark_context_by_regex("NVDA 100元時叫我", user_id=1, channel_id=2)
    assert cand is not None
    assert cand["trigger_type"] == "price"
    assert cand["condition"]["ticker"] == "NVDA"
    assert cand["condition"]["target_price"] == 100.0
    assert cand["condition"]["one_shot"] is True
    assert cand["condition"]["only_in_market_hours"] is True

    # 2. 跌破 + 比特幣 -> BTC
    cand2 = parse_spark_context_by_regex("當比特幣跌破 60000 提醒我", user_id=1, channel_id=2)
    assert cand2 is not None
    assert cand2["condition"]["ticker"] == "BTC"
    assert cand2["condition"]["target_price"] == 60000.0
    assert cand2["condition"]["direction"] == "below"
    assert cand2["condition"]["one_shot"] is True
    assert cand2["condition"]["only_in_market_hours"] is True

    # 3. 台積電 1000 元
    cand3 = parse_spark_context_by_regex("台積電 1000 元通知我", user_id=1, channel_id=2)
    assert cand3 is not None
    assert cand3["condition"]["ticker"] == "2330"
    assert cand3["condition"]["target_price"] == 1000.0

    # 4. ETH 漲破 $3500 告知大家
    cand4 = parse_spark_context_by_regex("ETH 漲破 $3,500 告知大家", user_id=1, channel_id=2)
    assert cand4 is not None
    assert cand4["condition"]["ticker"] == "ETH"
    assert cand4["condition"]["target_price"] == 3500.0
    assert cand4["condition"]["direction"] == "above"

    # 5. 特斯拉 250 提醒我
    cand5 = parse_spark_context_by_regex("特斯拉 250 提醒我", user_id=1, channel_id=2)
    assert cand5 is not None
    assert cand5["condition"]["ticker"] == "TSLA"
    assert cand5["condition"]["target_price"] == 250.0


def test_parse_spark_context_schedule() -> None:
    """測試定時與間隔排程 context 解析。"""
    # 1. 每日固定時間
    cand = parse_spark_context_by_regex("每天 08:30 提醒吃藥", user_id=1, channel_id=2)
    assert cand is not None
    assert cand["trigger_type"] == "schedule"
    assert cand["condition"]["mode"] == "daily_time"
    assert cand["condition"]["time"] == "08:30"
    assert "吃藥" in cand["message"]

    # 2. 每日傍晚打卡
    cand_off = parse_spark_context_by_regex("每日 18:00 發送下班打卡", user_id=1, channel_id=2)
    assert cand_off is not None
    assert cand_off["condition"]["time"] == "18:00"

    # 3. 循環間隔 (分鐘)
    cand_int = parse_spark_context_by_regex("每 30 分鐘 提醒喝水", user_id=1, channel_id=2)
    assert cand_int is not None
    assert cand_int["condition"]["mode"] == "interval"
    assert cand_int["condition"]["interval_minutes"] == 30

    # 4. 循環間隔 (mins / 小時)
    cand_hr = parse_spark_context_by_regex("每隔 2 小時 提醒站起來動一動", user_id=1, channel_id=2)
    assert cand_hr is not None
    assert cand_hr["condition"]["interval_minutes"] == 120

    # 5. 單次相對時間 (5分鐘後 提醒我要傳色圖給ㄜㄋ)
    cand_rel = parse_spark_context_by_regex("5分鐘後 提醒我要傳色圖給ㄜㄋ", user_id=1, channel_id=2)
    assert cand_rel is not None
    assert cand_rel["trigger_type"] == "schedule"
    assert cand_rel["condition"]["mode"] == "once"
    assert "target_time" in cand_rel["condition"]
    assert cand_rel["action"]["action_type"] == "core_tool"
    assert cand_rel["action"]["target"] == "PIXIV"


def test_parse_spark_context_weather() -> None:
    """測試天氣預警 context 解析。"""
    # 1. 降雨警報
    cand_rain = parse_spark_context_by_regex("台北下雨通知我", user_id=1, channel_id=2)
    assert cand_rain is not None
    assert cand_rain["trigger_type"] == "weather"
    assert cand_rain["condition"]["city"] == "台北"
    assert cand_rain["condition"]["alert_type"] == "rain"

    # 2. 高溫特報
    cand_heat = parse_spark_context_by_regex("高雄高溫 35 度特報提醒我", user_id=1, channel_id=2)
    assert cand_heat is not None
    assert cand_heat["trigger_type"] == "weather"
    assert cand_heat["condition"]["city"] == "高雄"
    assert cand_heat["condition"]["alert_type"] == "temp_above"
    assert cand_heat["condition"]["threshold"] == 35.0

    # 3. 低溫特報
    cand_cold = parse_spark_context_by_regex("台中低溫 10 度提醒我", user_id=1, channel_id=2)
    assert cand_cold is not None
    assert cand_cold["condition"]["alert_type"] == "temp_below"
    assert cand_cold["condition"]["threshold"] == 10.0


def test_parse_spark_context_rss() -> None:
    """測試 RSS / YouTube 內容訂閱解析。"""
    # 1. 基本 RSS 訂閱
    cand_rss = parse_spark_context_by_regex("訂閱 https://example.com/feed.xml", user_id=1, channel_id=2)
    assert cand_rss is not None
    assert cand_rss["trigger_type"] == "rss"
    assert cand_rss["condition"]["feed_url"] == "https://example.com/feed.xml"

    # 2. 帶關鍵字訂閱
    cand_yt = parse_spark_context_by_regex("追蹤 https://youtube.com/feed AI新影片", user_id=1, channel_id=2)
    assert cand_yt is not None
    assert cand_yt["trigger_type"] == "rss"
    assert "AI" in cand_yt["condition"]["keyword"]


@pytest.mark.anyio
async def test_parse_spark_context_llm_flow() -> None:
    """測試當 Regex 未命中時透過 BotBrain (LLM) 抽取流程。"""
    # Arrange
    mock_bot = MagicMock()
    mock_brain = MagicMock()

    async def fake_enqueue(task: object) -> None:
        callback = getattr(task, "callback", None)
        if callback:
            await callback('''
            {
                "trigger_type": "schedule",
                "condition": {"mode": "daily_time", "time": "08:30"},
                "message": "早安！記得吃早餐"
            }
            ''')

    mock_brain.enqueue_task = AsyncMock(side_effect=fake_enqueue)
    mock_bot.get_cog.return_value = mock_brain

    # Act
    cand = await parse_spark_context(
        context="每天早上八點半在這個頻道發早安問候大家",
        user_id=101,
        channel_id=202,
        bot=mock_bot,
    )

    # Assert
    assert cand is not None
    assert cand["trigger_type"] == "schedule"
    assert cand["condition"]["time"] == "08:30"
    assert "早安" in cand["message"]


@pytest.mark.anyio
async def test_parse_spark_context_with_llm_action_definition_pixiv() -> None:
    """測試在建立推播時，LLM 成功將「每10分鐘傳一張大奶圖片給我」定義為 PIXIV 搜圖工具。"""
    # Arrange
    mock_bot = MagicMock()
    mock_brain = MagicMock()

    async def fake_enqueue(task: object) -> None:
        callback = getattr(task, "callback", None)
        if callback:
            await callback('''
            {
                "action_type": "core_tool",
                "target": "PIXIV",
                "payload": {"query": "大奶"},
                "message": "傳一張大奶圖片給 Yuuto"
            }
            ''')

    mock_brain.enqueue_task = AsyncMock(side_effect=fake_enqueue)
    mock_bot.get_cog.return_value = mock_brain

    # Act
    cand = await parse_spark_context(
        context="@雪莉 每10分鐘傳一張大奶圖片給我",
        user_id=425311733503229972,
        channel_id=999,
        bot=mock_bot,
    )

    # Assert
    assert cand is not None
    assert cand["trigger_type"] == "schedule"
    assert cand["condition"]["mode"] == "interval"
    assert cand["condition"]["interval_minutes"] == 10
    assert cand["condition"]["tz_offset"] == 8
    # 確保動作已在建立當下由 LLM 定義為 PIXIV 工具
    assert cand["action"]["action_type"] == "core_tool"
    assert cand["action"]["target"] == "PIXIV"
    assert cand["action"]["payload"]["query"] == "大奶"


@pytest.mark.anyio
async def test_parse_spark_context_with_llm_action_definition_chat() -> None:
    """測試在建立推播時，LLM 成功將「請每天早上8.告訴我一個笑話」定義為 CHAT 對話工具。"""
    # Arrange
    mock_bot = MagicMock()
    mock_brain = MagicMock()

    async def fake_enqueue(task: object) -> None:
        callback = getattr(task, "callback", None)
        if callback:
            await callback('''
            {
                "action_type": "core_tool",
                "target": "CHAT",
                "payload": {"query": "告訴我一個好笑的笑話"},
                "message": "講個笑話"
            }
            ''')

    mock_brain.enqueue_task = AsyncMock(side_effect=fake_enqueue)
    mock_bot.get_cog.return_value = mock_brain

    # Act
    cand = await parse_spark_context(
        context="@雪莉 請每天早上8.告訴我一個笑話",
        user_id=425311733503229972,
        channel_id=999,
        bot=mock_bot,
    )

    # Assert
    assert cand is not None
    assert cand["trigger_type"] == "schedule"
    assert cand["condition"]["mode"] == "daily_time"
    assert cand["condition"]["time"] == "08:00"
    assert cand["condition"]["tz_offset"] == 8
    # 確保動作已在建立當下定義為 CHAT 工具
    assert cand["action"]["action_type"] == "core_tool"
    assert cand["action"]["target"] == "CHAT"
    assert cand["action"]["payload"]["query"] == "告訴我一個好笑的笑話"


@pytest.mark.anyio
async def test_parse_spark_context_pure_reminder_memo() -> None:
    """測試單純個人備忘/鬧鐘快路徑篩選（如提醒開會、喝水），自動設定為 notify 且免呼叫 LLM。"""
    # Arrange
    mock_bot = MagicMock()
    mock_brain = MagicMock()
    mock_brain.enqueue_task = AsyncMock()
    mock_bot.get_cog.return_value = mock_brain

    # Act
    cand = await parse_spark_context(
        context="5分鐘後 提醒我開會",
        user_id=123,
        channel_id=456,
        bot=mock_bot,
    )

    # Assert
    assert cand is not None
    assert cand["trigger_type"] == "schedule"
    assert cand["condition"]["mode"] == "once"
    assert cand["action"]["action_type"] == "notify"
    # 驗證免呼叫 LLM
    mock_brain.enqueue_task.assert_not_called()


def test_schedule_timezone_utc8_formatting() -> None:
    """測試單次排程時間全面帶有 +08:00 且對齊台灣時間。"""
    # Act
    cand = parse_spark_context_by_regex("10分鐘後 叫我", user_id=1, channel_id=2)

    # Assert
    assert cand is not None
    target_time_str = cand["condition"]["target_time"]
    assert "+08:00" in target_time_str
    assert cand["condition"]["tz_offset"] == 8


def test_detect_action_fallback_matrix() -> None:
    """測試當 LLM 未就緒時，所有工具別名與 fallback 能精準推斷。"""
    from handlers.spark.nlp import detect_action_from_message_fallback

    # 1. PIXIV
    assert detect_action_from_message_fallback("傳一張大奶圖片")["target"] == "PIXIV"
    assert detect_action_from_message_fallback("來張色圖")["target"] == "PIXIV"

    # 2. DRAW
    assert detect_action_from_message_fallback("幫我畫一隻戴帽子的貓")["target"] == "DRAW"

    # 3. STOCK
    assert detect_action_from_message_fallback("幫我看0050走勢")["target"] == "STOCK"

    # 4. WEATHER
    assert detect_action_from_message_fallback("查台北天氣")["target"] == "SEARCH"

    # 5. MUSIC
    assert detect_action_from_message_fallback("播一首周杰倫的歌")["target"] == "MUSIC"

    # 6. CHAT
    assert detect_action_from_message_fallback("說個笑話來聽聽")["target"] == "CHAT"
    assert detect_action_from_message_fallback("跟我說聲早安")["target"] == "CHAT"

    # 7. NOTIFY
    assert detect_action_from_message_fallback("提醒我吃藥")["action_type"] == "notify"
    assert detect_action_from_message_fallback("提醒喝水")["action_type"] == "notify"


def test_parse_spark_context_schedule_interval_variations() -> None:
    """測試循環間隔排程的各種變體（中文數字、常見動詞、倒裝句型）。"""
    # 1. 使用者原話：每10分鐘傳送一張大奶圖給我
    cand1 = parse_spark_context_by_regex("每10分鐘傳送一張大奶圖給我", user_id=1, channel_id=2)
    assert cand1 is not None
    assert cand1["condition"]["mode"] == "interval"
    assert cand1["condition"]["interval_minutes"] == 10
    assert cand1["action"]["target"] == "PIXIV"

    # 2. 中文數字：每十分鐘傳一次大奶圖給我
    cand2 = parse_spark_context_by_regex("每十分鐘傳一次大奶圖給我", user_id=1, channel_id=2)
    assert cand2 is not None
    assert cand2["condition"]["mode"] == "interval"
    assert cand2["condition"]["interval_minutes"] == 10
    assert cand2["action"]["target"] == "PIXIV"

    # 3. 倒裝句型：傳一張大奶圖給我，每10分鐘一次
    cand3 = parse_spark_context_by_regex("傳一張大奶圖給我，每10分鐘一次", user_id=1, channel_id=2)
    assert cand3 is not None
    assert cand3["condition"]["mode"] == "interval"
    assert cand3["condition"]["interval_minutes"] == 10
    assert cand3["action"]["target"] == "PIXIV"

    # 4. 中文數字兩小時
    cand4 = parse_spark_context_by_regex("每兩小時提醒我喝水", user_id=1, channel_id=2)
    assert cand4 is not None
    assert cand4["condition"]["mode"] == "interval"
    assert cand4["condition"]["interval_minutes"] == 120

    # 5. 中文數字十分鐘後（單次相對時間）
    cand5 = parse_spark_context_by_regex("十分鐘後叫我", user_id=1, channel_id=2)
    assert cand5 is not None
    assert cand5["condition"]["mode"] == "once"


def test_sanitize_schedule_condition_guard() -> None:
    """測試排程模式守衛函式：當 LLM 誤將循環詞解析為 once 時，能精準自動糾正。"""
    from handlers.spark.nlp import sanitize_schedule_condition

    # Case 1: LLM 產生了 once，但句子明確為「每10分鐘傳一張大奶圖」
    bad_llm_cond = {
        "mode": "once",
        "target_time": "2026-09-03T22:30:00",
        "tz_offset": 8,
    }
    fixed = sanitize_schedule_condition(bad_llm_cond, "每10分鐘傳送一張大奶圖給我")
    assert fixed["mode"] == "interval"
    assert fixed["interval_minutes"] == 10
    assert "target_time" not in fixed

    # Case 2: LLM 產生了 once，但句子為「每半小時講笑話」
    fixed_half = sanitize_schedule_condition(bad_llm_cond, "每半小時講個笑話")
    assert fixed_half["mode"] == "interval"
    assert fixed_half["interval_minutes"] == 30

    # Case 3: 單次提醒防呆糾正（LLM 誤產 interval）
    bad_interval = {"mode": "interval", "interval_minutes": 10}
    fixed_once = sanitize_schedule_condition(bad_interval, "10分鐘後叫我")
    assert fixed_once["mode"] == "once"
    assert "interval_minutes" not in fixed_once


@pytest.mark.anyio
async def test_parse_spark_context_llm_guard_fixes_wrong_mode() -> None:
    """測試在 LLM 結構化抽取時，若 LLM 輸出錯誤的 once 模式，系統自動守衛糾正為 interval。"""
    # Arrange
    mock_bot = MagicMock()
    mock_brain = MagicMock()

    # 模擬 LLM 產生了包含 PIXIV 動作但模式誤判為 once 的結果
    async def fake_enqueue(task: object) -> None:
        callback = getattr(task, "callback", None)
        if callback:
            await callback('''
            {
                "trigger_type": "schedule",
                "condition": {"mode": "once", "target_time": "2026-09-03T22:30:00"},
                "action": {"action_type": "core_tool", "target": "PIXIV", "payload": {"query": "大奶"}},
                "message": "傳送一張大奶圖給 Yuuto"
            }
            ''')

    mock_brain.enqueue_task = AsyncMock(side_effect=fake_enqueue)
    mock_bot.get_cog.return_value = mock_brain

    # Act
    cand = await parse_spark_context_by_llm(
        mock_bot,
        context="每10分鐘傳送一張大奶圖給我",
        user_id=123,
        channel_id=456,
    )

    # Assert
    assert cand is not None
    # 驗證 LLM 的核心動作理解被完整保留
    assert cand["action"]["action_type"] == "core_tool"
    assert cand["action"]["target"] == "PIXIV"
    assert cand["action"]["payload"]["query"] == "大奶"
    assert cand["message"] == "傳送一張大奶圖給 Yuuto"
    # 關鍵：驗證時間模式守衛成功將 once 糾正為 interval！
    assert cand["condition"]["mode"] == "interval"
    assert cand["condition"]["interval_minutes"] == 10


@pytest.mark.anyio
async def test_parse_spark_context_oneshot_search_time() -> None:
    """測試單次指定時刻（8.30分）自動識別 SEARCH 動作與 force_search。"""
    cand = await parse_spark_context(
        context="@雪莉 8.30分跟我說最新CPI的消息",
        user_id=123,
        channel_id=456,
    )
    assert cand is not None
    assert cand["trigger_type"] == "schedule"
    assert cand["condition"]["mode"] == "once"
    assert "target_time" in cand["condition"]
    assert cand["action"]["action_type"] == "core_tool"
    assert cand["action"]["target"] == "SEARCH"
    assert cand["action"]["payload"].get("force_search") is True
    assert "CPI" in cand["action"]["payload"]["query"]


@pytest.mark.anyio
async def test_parse_spark_context_weekdays_schedule() -> None:
    """測試工作日與週期性 days_of_week 抽取。"""
    cand = await parse_spark_context(
        context="每個工作日早上9點提醒我開會",
        user_id=123,
        channel_id=456,
    )
    assert cand is not None
    assert cand["trigger_type"] == "schedule"
    assert cand["condition"]["mode"] == "daily_time"
    assert cand["condition"]["time"] == "09:00"
    assert cand["condition"]["days_of_week"] == [0, 1, 2, 3, 4]


@pytest.mark.anyio
async def test_parse_spark_context_max_runs_extraction() -> None:
    """測試 max_runs 次數抽取與自動過濾任務訊息。"""
    cand = await parse_spark_context(
        context="每10分鐘提醒我喝水，總共3次",
        user_id=123,
        channel_id=456,
    )
    assert cand is not None
    assert cand["trigger_type"] == "schedule"
    assert cand["condition"]["mode"] == "interval"
    assert cand["condition"]["interval_minutes"] == 10
    assert cand["condition"]["max_runs"] == 3
    assert "喝水" in cand["message"]
    assert "總共3次" not in cand["message"]


@pytest.mark.anyio
async def test_parse_spark_context_web_monitor_extraction() -> None:
    """測試 web_monitor 智慧巡檢守門員抽取。"""
    cand = await parse_spark_context(
        context="每小時幫我看看有沒有CPI或降息的重大新聞，有的話通知我",
        user_id=123,
        channel_id=456,
    )
    assert cand is not None
    assert cand["trigger_type"] == "web_monitor"
    assert cand["condition"]["interval_minutes"] == 60
    assert "CPI" in cand["condition"]["query"] or "降息" in cand["condition"]["query"]
    assert "ai_condition" in cand["condition"]


@pytest.mark.anyio
async def test_parse_spark_context_multi_task_agent_prompt() -> None:
    """測試複合多任務排程自動標記為 agent_prompt。"""
    cand = await parse_spark_context(
        context="每天早上8點查最新CPI消息跟0050股價並跟我說早安",
        user_id=123,
        channel_id=456,
    )
    assert cand is not None
    assert cand["trigger_type"] == "schedule"
    assert cand["condition"]["mode"] == "daily_time"
    assert cand["action"]["action_type"] == "agent_prompt"
    assert "prompt" in cand["action"]["payload"]


