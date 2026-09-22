"""
tests/unit/test_core_tools_matrix.py
====================================
核心系統工具標準合約 (ToolResult) 與並行調度矩陣測試。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from handlers.social.contracts import ToolCategory, ToolExecutionStatus, ToolResult
from handlers.social.tools.music_tool import MusicTool
from handlers.social.tools.stock_tool import StockTool
from handlers.social.tools.weather_tool import WeatherTool
from handlers.social.tools.search_tools import URLSummaryTool
from handlers.social.dispatch_agent import ParallelAgentTool, ExecutionPlan, PlannedTask


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_tool_result_contracts():
    """驗證 ToolResult 合約工廠方法與屬性。"""
    res_success = ToolResult.success("執行成功", is_direct_rendered=True, extra_data={"foo": "bar"})
    assert res_success.status == ToolExecutionStatus.SUCCESS
    assert res_success.is_direct_rendered is True
    assert res_success.summary_text == "執行成功"
    assert res_success.extra_data["foo"] == "bar"

    res_error = ToolResult.error("發生錯誤", is_direct_rendered=False)
    assert res_error.status == ToolExecutionStatus.ERROR
    assert res_error.is_direct_rendered is False
    assert res_error.summary_text == "發生錯誤"

    res_no_data = ToolResult.no_data("無資料", is_direct_rendered=True)
    assert res_no_data.status == ToolExecutionStatus.NO_DATA
    assert res_no_data.is_direct_rendered is True


@pytest.mark.anyio
async def test_music_tool_fast_path_contracts():
    """驗證 MusicTool 在 YouTube 與 查看清單 快路徑下皆回傳 ToolResult(is_direct_rendered=True)。"""
    tool = MusicTool()
    assert tool.response_mode == ToolCategory.DIRECT_UI

    brain_mock = MagicMock()
    brain_mock.enqueue_task = AsyncMock()
    handler_mock = MagicMock()
    handler_mock.headless_play = AsyncMock()
    handler_mock.send_queue_embed = AsyncMock()
    handler_mock.queues = {12345: [{"title": "Song 1"}]}
    handler_mock.current_song = {12345: {"title": "Song 1"}}

    bot = MagicMock()
    bot.loop.create_future = asyncio.get_running_loop().create_future
    bot.get_cog.side_effect = lambda name: brain_mock if name == "BotBrain" else (handler_mock if name == "MusicHandler" else None)
    
    msg = MagicMock()
    msg.guild.id = 12345
    msg.channel.id = 67890
    msg.author.voice.channel = MagicMock()
    msg.reply = AsyncMock()

    with patch("handlers.social.utils.simulate_typing_and_send", new_callable=AsyncMock):
        # 1. YouTube 網址快路徑
        res = await tool.execute(
            bot, msg, "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "", "", silent=True
        )
        assert isinstance(res, ToolResult)
        assert res.is_direct_rendered is True
        assert res.status == ToolExecutionStatus.SUCCESS
        assert "神秘的電波" in res.summary_text or "載入" in res.summary_text

        # 2. 查看播放清單快路徑
        res_list = await tool.execute(bot, msg, "列出目前的播放清單", "", "", silent=True)
        assert isinstance(res_list, ToolResult)
        assert res_list.is_direct_rendered is True
        assert "播放清單 Embed" in res_list.summary_text


@pytest.mark.anyio
async def test_stock_tool_direct_ui_contracts():
    """驗證 StockTool 為 DIRECT_UI 且圖文一體發送並回傳 ToolResult，同時 Prompt 包含多維度指標與新聞。"""
    tool = StockTool()
    assert tool.response_mode == ToolCategory.DIRECT_UI

    bot = MagicMock()
    bot.loop.create_future = asyncio.get_running_loop().create_future
    brain_mock = MagicMock()

    captured_prompt = ""
    async def fake_enqueue(task):
        nonlocal captured_prompt
        captured_prompt = task.user_prompt
        await task.callback("【BTC】名偵探直覺分析：現在氣勢很強，放量突破布林中軌！")
    brain_mock.enqueue_task = AsyncMock(side_effect=fake_enqueue)
    bot.get_cog.side_effect = lambda name: brain_mock if name == "BotBrain" else None

    msg = MagicMock()
    msg.id = 111
    msg.guild.id = 12345
    msg.channel.id = 67890
    msg.author.display_name = "Yuuto"
    msg.reply = AsyncMock()

    fake_indicators = {
        "ticker": "BTC-USD",
        "close": 98000,
        "ema20": 97000,
        "ema60": 95000,
        "bb_upper": 99500,
        "bb_lower": 94500,
        "bb_width": 5.15,
        "pct_b": 0.70,
        "rsi14": 75,
        "kd_k": 82.5,
        "kd_d": 78.0,
        "macd": 100,
        "macd_signal": 50,
        "macd_hist": 50,
        "atr14": 1800,
        "vol_ratio": 1.45,
        "change_pct": 3.5,
        "change_5d": 7.2,
        "change_20d": 18.0,
        "high_20d": 99000,
        "low_20d": 91000,
        "high_60d": 99500,
        "low_60d": 85000,
        "news": [
            {"title": "Bitcoin surges as institutional demand spikes", "publisher": "CoinDesk", "time": "09/08"}
        ],
        "rows": 60,
    }

    with patch("handlers.social.tools.stock_tool._fetch_and_plot", return_value=(b"fake_png", fake_indicators)), \
         patch("handlers.social.utils.remove_hourglass_reaction", new_callable=AsyncMock):
        # 1. 直接模式 (silent=False)：發起獨立 LLM 分析並發送圖文
        res = await tool.execute(bot, msg, "請幫我分析BTC怎麼看", "", "", silent=False)
        assert isinstance(res, ToolResult)
        assert res.is_direct_rendered is True
        assert res.status == ToolExecutionStatus.SUCCESS
        assert "僅供參考" in res.summary_text
        msg.reply.assert_called_once()
        # 驗證 Prompt 是否包含豐富的線索
        assert "盤面數據與指標線索" in captured_prompt
        assert "布林通道" in captured_prompt
        assert "KD(9,3,3)" in captured_prompt
        assert "Bitcoin surges as institutional" in captured_prompt

        # 2. 靜音模式 (silent=True)：僅發送圖表並回傳數據，不重複發起 LLM 分析
        msg.reply.reset_mock()
        brain_mock.enqueue_task.reset_mock()
        res_silent = await tool.execute(bot, msg, "請幫我分析BTC怎麼看", "", "", silent=True)
        assert isinstance(res_silent, ToolResult)
        assert res_silent.is_direct_rendered is False
        assert res_silent.status == ToolExecutionStatus.SUCCESS
        assert "盤面數據與指標線索" in res_silent.summary_text
        assert "Bitcoin surges as institutional" in res_silent.summary_text
        assert brain_mock.enqueue_task.call_count == 0  # 驗證跳過獨立 LLM 生成
        msg.reply.assert_called_once()  # 驗證圖表仍正常發送至頻道


def test_stock_stopwords_filter_macro_terms():
    """驗證 _quick_extract_tickers 正確過濾 ETF, FED, RATE 等總經與通用英文詞。"""
    from handlers.social.tools.stock_tool import _quick_extract_tickers

    # 驗證總經/搜尋語句不誤判為股票代碼
    assert _quick_extract_tickers("為什麼升息 股票 不下跌 原因 升息對 ETF 與 FED 政策 的 影響") == []
    # 驗證真實美股與台股正常提取
    assert set(_quick_extract_tickers("請幫我分析 VOO 和 2330 還有 QQQ")) == {"VOO", "2330.TW", "QQQ"}


def test_stock_technical_indicators_math():
    """驗證 KD, Bollinger Bands, ATR, RSI 指標數學計算邏輯。"""
    import numpy as np
    import pandas as pd
    from handlers.social.tools.stock_tool import (
        _compute_atr,
        _compute_bollinger_bands,
        _compute_kd,
        _compute_rsi,
        _fetch_ticker_news,
    )
    from mod.market_plugin.data_fetcher import DataFetcher

    np.random.seed(42)
    dates = pd.date_range("2026-01-01", periods=60, freq="D")
    prices = 100.0 * np.cumprod(1 + np.random.normal(0.001, 0.02, size=60))
    df = pd.DataFrame({
        "Open": prices * 0.99,
        "High": prices * 1.02,
        "Low": prices * 0.98,
        "Close": prices,
        "Volume": np.random.uniform(1000, 5000, size=60),
    }, index=dates)

    # 1. RSI
    rsi = _compute_rsi(df["Close"], 14)
    assert len(rsi) == 60
    valid_rsi = rsi.dropna()
    assert (valid_rsi >= 0).all() and (valid_rsi <= 100).all()

    # 2. KD
    k, d = _compute_kd(df, 9, 3, 3)
    assert len(k) == 60 and len(d) == 60
    assert (k.dropna() >= 0).all() and (k.dropna() <= 100).all()

    # 3. Bollinger Bands
    mid, upper, lower, bandwidth, pct_b = _compute_bollinger_bands(df["Close"], 20, 2.0)
    assert (upper.iloc[20:] >= mid.iloc[20:]).all()
    assert (mid.iloc[20:] >= lower.iloc[20:]).all()
    assert (bandwidth.iloc[20:] >= 0).all()

    # 4. ATR
    atr = _compute_atr(df, 14)
    assert (atr.dropna() >= 0).all()

    # 5. DataFetcher calculate_technicals
    fetcher = DataFetcher()
    techs = fetcher.calculate_technicals(df)
    assert techs is not None
    assert "KD_K" in techs and "BB_Width" in techs

    # 6. 新聞解析
    fake_news = [{"content": {"title": "Test Title", "provider": {"displayName": "TestPub"}, "pubDate": "2026-09-08"}}]
    with patch("yfinance.Ticker") as mock_ticker:
        mock_instance = MagicMock()
        mock_instance.news = fake_news
        mock_ticker.return_value = mock_instance
        news_res = _fetch_ticker_news("AAPL", limit=1)
        assert len(news_res) == 1
        assert news_res[0]["title"] == "Test Title"


@pytest.mark.anyio
async def test_weather_tool_data_provider_mode():
    """驗證 WeatherTool 在 silent 模式下作為 DataProvider 回傳純數據且 is_direct_rendered=False。"""
    tool = WeatherTool()
    assert tool.response_mode == ToolCategory.DATA_PROVIDER

    brain_mock = MagicMock()
    async def fake_enqueue(task):
        await task.callback("Taipei")
    brain_mock.enqueue_task = fake_enqueue
    bot = MagicMock()
    bot.loop.create_future = asyncio.get_running_loop().create_future
    bot.get_cog.return_value = brain_mock

    msg = MagicMock()
    msg.id = 222
    msg.channel.id = 67890
    msg.author.display_name = "Tester"
    msg.reply = AsyncMock()

    fake_weather_json = {
        "main": {"temp": 28.5, "feels_like": 31.0, "humidity": 70},
        "weather": [{"description": "多雲"}],
        "wind": {"speed": 3.5},
        "name": "Taipei",
    }

    class FakeSession:
        def get(self, url, params):
            class FakeResp:
                status = 200
                async def json(self): return fake_weather_json
                async def __aenter__(self): return self
                async def __aexit__(self, *args): pass
            return FakeResp()

    with patch.object(tool, "_get_session", new_callable=AsyncMock, return_value=FakeSession()), \
         patch("settings.OPENWEATHERMAP_API_KEY", "test_key"):
        res = await tool.execute(bot, msg, "台北天氣如何", "", "", silent=True)
        assert isinstance(res, ToolResult)
        assert res.is_direct_rendered is False
        assert "Taipei" in res.summary_text
        assert "28.5°C" in res.summary_text


@pytest.mark.anyio
async def test_url_summary_tool_and_youtube_redirect():
    """驗證 URLSummaryTool 遇到 YouTube 網址時轉導至 MusicTool 並回傳 ToolResult。"""
    tool = URLSummaryTool()

    brain_mock = MagicMock()
    brain_mock.enqueue_task = AsyncMock()
    handler_mock = MagicMock()
    handler_mock.headless_play = AsyncMock()
    bot = MagicMock()
    bot.loop.create_future = asyncio.get_running_loop().create_future
    bot.get_cog.side_effect = lambda name: brain_mock if name == "BotBrain" else (handler_mock if name == "MusicHandler" else None)

    msg = MagicMock()
    msg.guild.id = 12345
    msg.channel.id = 67890
    msg.author.voice.channel = MagicMock()
    msg.reply = AsyncMock()

    with patch("handlers.social.utils.simulate_typing_and_send", new_callable=AsyncMock):
        res = await tool.execute(
            bot, msg, "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "", "", silent=True
        )
        assert isinstance(res, ToolResult)
        assert res.is_direct_rendered is True
        assert res.status == ToolExecutionStatus.SUCCESS


def test_parallel_agent_early_termination_logic():
    """驗證 ParallelAgent 的 Early Termination 判定邏輯。"""
    agent = ParallelAgentTool(tool_registry=[])

    # 1. 只有自帶 UI 工具 (MUSIC + PIXIV)，且 needs_persona_reply=False ➔ 觸發終止
    plan_ui_only = ExecutionPlan(thought="純音樂操作", tasks=[PlannedTask("MUSIC", "晴天")], needs_persona_reply=False)
    results_ui_only = [("MUSIC", ToolCategory.DIRECT_UI, "已播放周杰倫-晴天")]
    assert agent._should_early_terminate(plan_ui_only, results_ui_only, "放周杰倫晴天") is True

    # 2. 包含純資料工具 (WEATHER)，即使 needs_persona_reply=False ➔ 絕不終止（必須合成回覆）
    plan_mixed = ExecutionPlan(thought="放歌查天氣", tasks=[PlannedTask("MUSIC", "晴天"), PlannedTask("WEATHER", "台北")], needs_persona_reply=False)
    results_mixed = [
        ("MUSIC", ToolCategory.DIRECT_UI, "已播放周杰倫-晴天"),
        ("WEATHER", ToolCategory.DATA_PROVIDER, "台北 28°C 多雲"),
    ]
    assert agent._should_early_terminate(plan_mixed, results_mixed, "放周杰倫晴天順便查台北天氣") is False

    # 3. 使用者有明確閒聊提問 ➔ 絕不終止
    assert agent._should_early_terminate(plan_ui_only, results_ui_only, "放周杰倫晴天，並跟我說說妳喜不喜歡這首歌") is False
