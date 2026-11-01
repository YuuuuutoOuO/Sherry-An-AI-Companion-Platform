"""tests/unit/test_spark_checkers.py - Spark 各類檢查器策略單元測試。

遵循 AAA (Arrange / Act / Assert) 原則，以 Protocol Fake 進行純邏輯驗證。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from handlers.spark.checkers.price_checker import PriceChecker, is_market_open
from handlers.spark.checkers.rss_checker import RssChecker
from handlers.spark.checkers.schedule_checker import ScheduleChecker
from handlers.spark.checkers.weather_checker import WeatherChecker
from handlers.spark.models import RssItem, Trigger


@pytest.fixture
def anyio_backend() -> str:
    """設定 anyio 測試後端為 asyncio。"""
    return "asyncio"


# ===========================================================================
# 1. PriceChecker 與 is_market_open 測試
# ===========================================================================

class FakeStockClient:
    """提供可設定報價的測試 Fake 客戶端。"""

    def __init__(self, price: float | None = None) -> None:
        self.price = price

    async def get_price(self, ticker: str) -> float | None:
        return self.price


def test_is_market_open_crypto() -> None:
    """測試加密貨幣永遠處於 24/7 開盤狀態。"""
    # 任何時間 (包括週日午夜)
    sunday_midnight = datetime(2026, 8, 30, 0, 0, tzinfo=timezone.utc)
    is_open, desc = is_market_open("BTC-USD", sunday_midnight)
    assert is_open is True
    assert "24/7" in desc

    is_open_eth, _ = is_market_open("ETH", sunday_midnight)
    assert is_open_eth is True


def test_is_market_open_tw_stock() -> None:
    """測試台股交易時段判定 (週一至週五 09:00 ~ 13:30)。"""
    # 1. 週三 10:30 (盤中) -> Open
    wed_open = datetime(2026, 8, 26, 10, 30, tzinfo=ZoneInfo("Asia/Taipei"))
    is_open, desc = is_market_open("2330", wed_open)
    assert is_open is True
    assert "盤中交易時段" in desc

    # 2. 週三 15:00 (收盤後) -> Closed
    wed_closed = datetime(2026, 8, 26, 15, 0, tzinfo=ZoneInfo("Asia/Taipei"))
    is_open2, desc2 = is_market_open("2330.TW", wed_closed)
    assert is_open2 is False
    assert "已收盤" in desc2

    # 3. 週日 10:00 (週末) -> Closed
    sun = datetime(2026, 8, 30, 10, 0, tzinfo=ZoneInfo("Asia/Taipei"))
    is_open3, desc3 = is_market_open("2330", sun)
    assert is_open3 is False
    assert "週末休市" in desc3


def test_is_market_open_us_stock() -> None:
    """測試美股交易時段判定 (美東週一至週五 09:30 ~ 16:00)。"""
    # 1. 週二 14:00 NY (盤中) -> Open
    tue_open = datetime(2026, 8, 25, 14, 0, tzinfo=ZoneInfo("America/New_York"))
    is_open, desc = is_market_open("NVDA", tue_open)
    assert is_open is True
    assert "常規盤中時段" in desc

    # 2. 週二 20:00 NY (美東夜晚收盤後) -> Closed
    tue_closed = datetime(2026, 8, 25, 20, 0, tzinfo=ZoneInfo("America/New_York"))
    is_open2, desc2 = is_market_open("AAPL", tue_closed)
    assert is_open2 is False
    assert "已收盤" in desc2

    # 3. 週六 12:00 NY (週末) -> Closed
    sat = datetime(2026, 8, 29, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    is_open3, desc3 = is_market_open("TSLA", sat)
    assert is_open3 is False
    assert "週末休市" in desc3

    # 4. 聖誕節 (Dec 25) -> Holiday Closed
    xmas = datetime(2026, 12, 25, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    is_open4, desc4 = is_market_open("NVDA", xmas)
    assert is_open4 is False
    assert "假日休市" in desc4


@pytest.mark.anyio
async def test_price_checker_market_closed_suppresses_fire() -> None:
    """測試美股休市時，即使現價滿足條件，也堅決不發送推播（杜絕收盤後持續騷擾）。"""
    # Arrange: 美東週六中午 (休市)，NVDA 現價 $125 >= 目標 $100
    sat_noon = datetime(2026, 8, 29, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    client = FakeStockClient(price=125.0)
    checker = PriceChecker(client)
    trigger = Trigger(
        trigger_id="p-closed",
        user_id=1,
        channel_id=1,
        trigger_type="price",
        condition={"ticker": "NVDA", "target_price": 100.0, "direction": "above", "only_in_market_hours": True},
    )

    # Act
    res = await checker.should_fire(trigger, now_dt=sat_noon)

    # Assert
    assert res.should_fire is False
    assert "非開盤時段暫停報價推播" in res.current_value


@pytest.mark.anyio
async def test_price_checker_unchanged_price_anti_spam() -> None:
    """測試盤中第二次檢查若價格與上次觸發價格相同，略過重複推播 (Anti-Spam)。"""
    # Arrange: 週二盤中，現價 125.0，但上次觸發價也是 125.0
    tue_open = datetime(2026, 8, 25, 14, 0, tzinfo=ZoneInfo("America/New_York"))
    client = FakeStockClient(price=125.0)
    checker = PriceChecker(client)
    trigger = Trigger(
        trigger_id="p-spam",
        user_id=1,
        channel_id=1,
        trigger_type="price",
        condition={
            "ticker": "NVDA",
            "target_price": 100.0,
            "direction": "above",
            "last_triggered_price": 125.0,
            "one_shot": False,
        },
    )

    # Act
    res = await checker.should_fire(trigger, now_dt=tue_open)

    # Assert
    assert res.should_fire is False
    assert "未發生變動" in res.current_value


@pytest.mark.anyio
async def test_price_checker_above_and_one_shot() -> None:
    """測試價格高於目標價判定，並驗證 one_shot_fired 標記。"""
    # Arrange: 現價 65,000, 目標 60,000 (Crypto 24/7)
    client = FakeStockClient(price=65000.0)
    checker = PriceChecker(client)
    trigger = Trigger(
        trigger_id="p-1",
        user_id=1,
        channel_id=1,
        trigger_type="price",
        condition={"ticker": "BTC-USD", "target_price": 60000.0, "direction": "above", "one_shot": True},
    )

    # Act
    res = await checker.should_fire(trigger)

    # Assert
    assert res.should_fire is True
    assert "65,000.00" in res.current_value
    assert res.detail_data["current_price"] == 65000.0
    assert res.detail_data["one_shot_fired"] is True
    assert res.detail_data["last_triggered_price"] == 65000.0


@pytest.mark.anyio
async def test_price_checker_below_not_firing() -> None:
    """測試價格低於目標價但尚未滿足的情況。"""
    # Arrange: 現價 65,000, 目標 50,000
    client = FakeStockClient(price=65000.0)
    checker = PriceChecker(client)
    trigger = Trigger(
        trigger_id="p-2",
        user_id=1,
        channel_id=1,
        trigger_type="price",
        condition={"ticker": "BTC-USD", "target_price": 50000.0, "direction": "below"},
    )

    # Act
    res = await checker.should_fire(trigger)

    # Assert
    assert res.should_fire is False


@pytest.mark.anyio
async def test_price_checker_cross_above() -> None:
    """測試向上突破 (cross_above) 判定。"""
    # Arrange: 上次價格 58,000, 現價 61,000, 目標 60,000
    client = FakeStockClient(price=61000.0)
    checker = PriceChecker(client)
    trigger = Trigger(
        trigger_id="p-3",
        user_id=1,
        channel_id=1,
        trigger_type="price",
        condition={
            "ticker": "BTC-USD",
            "target_price": 60000.0,
            "direction": "cross_above",
            "last_price": 58000.0,
        },
    )

    # Act
    res = await checker.should_fire(trigger)

    # Assert
    assert res.should_fire is True
    assert "強勢突破" in res.current_value


# ===========================================================================
# 2. ScheduleChecker 測試
# ===========================================================================

@pytest.mark.anyio
async def test_schedule_checker_interval() -> None:
    """測試固定間隔時間判定。"""
    # Arrange
    checker = ScheduleChecker()
    # 首次執行（無 last_triggered_at）
    trigger1 = Trigger(
        trigger_id="s-1",
        user_id=1,
        channel_id=1,
        trigger_type="schedule",
        condition={"mode": "interval", "interval_minutes": 60},
        last_triggered_at="",
    )

    # Act 1
    res1 = await checker.should_fire(trigger1)
    # Assert 1
    assert res1.should_fire is True
    assert "首次啟動" in res1.current_value

    # Arrange 2: 剛觸發過 (10 秒前)
    trigger2 = Trigger(
        trigger_id="s-2",
        user_id=1,
        channel_id=1,
        trigger_type="schedule",
        condition={"mode": "interval", "interval_minutes": 60},
        last_triggered_at=datetime.now(timezone.utc).isoformat(),
    )
    # Act 2
    res2 = await checker.should_fire(trigger2)
    # Assert 2
    assert res2.should_fire is False
    assert "冷卻中" in res2.current_value


@pytest.mark.anyio
async def test_schedule_checker_once() -> None:
    """測試單次排程模式 (once) 之時間點判定。"""
    # Arrange
    checker = ScheduleChecker()

    # 1. 未來到期：target_time 在 1 小時後
    future_time = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    trigger_future = Trigger(
        trigger_id="s-once-1",
        user_id=1,
        channel_id=1,
        trigger_type="schedule",
        condition={"mode": "once", "target_time": future_time},
    )

    # Act 1
    res1 = await checker.should_fire(trigger_future)

    # Assert 1
    assert res1.should_fire is False
    assert "尚未到達單次排程時間" in res1.current_value
    assert res1.detail_data.get("one_shot_fired") is not True

    # 2. 已經到期：target_time 在 5 分鐘前
    past_time = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    trigger_past = Trigger(
        trigger_id="s-once-2",
        user_id=1,
        channel_id=1,
        trigger_type="schedule",
        condition={"mode": "once", "target_time": past_time},
    )

    # Act 2
    res2 = await checker.should_fire(trigger_past)

    # Assert 2
    assert res2.should_fire is True
    assert "預定時間點" in res2.current_value
    assert res2.detail_data.get("one_shot_fired") is True


# ===========================================================================
# 3. RssChecker 測試
# ===========================================================================

class FakeRssClient:
    """提供可設定文章列表的 RSS Fake 客戶端。"""

    def __init__(self, items: list[RssItem]) -> None:
        self.items = items

    async def fetch_latest_items(self, feed_url: str, limit: int = 5) -> list[RssItem]:
        return self.items


@pytest.mark.anyio
async def test_rss_checker_new_item_found() -> None:
    """測試發現新文章時應觸發。"""
    # Arrange
    item1 = RssItem(item_id="post-2", title="新功能釋出", url="https://example.com/2")
    client = FakeRssClient(items=[item1])
    checker = RssChecker(client)

    trigger = Trigger(
        trigger_id="r-1",
        user_id=1,
        channel_id=1,
        trigger_type="rss",
        condition={"feed_url": "https://example.com/rss.xml", "last_item_id": "post-1"},
    )

    # Act
    res = await checker.should_fire(trigger)

    # Assert
    assert res.should_fire is True
    assert "新功能釋出" in res.current_value
    assert res.detail_data["last_item_id"] == "post-2"


@pytest.mark.anyio
async def test_rss_checker_same_item_no_fire() -> None:
    """測試文章未更新時不應觸發。"""
    # Arrange
    item1 = RssItem(item_id="post-1", title="舊文章", url="https://example.com/1")
    client = FakeRssClient(items=[item1])
    checker = RssChecker(client)

    trigger = Trigger(
        trigger_id="r-2",
        user_id=1,
        channel_id=1,
        trigger_type="rss",
        condition={"feed_url": "https://example.com/rss.xml", "last_item_id": "post-1"},
    )

    # Act
    res = await checker.should_fire(trigger)

    # Assert
    assert res.should_fire is False


# ===========================================================================
# 4. WeatherChecker 測試
# ===========================================================================

class FakeWeatherClient:
    """提供可設定天氣資料的 Fake 客戶端。"""

    def __init__(self, data: dict[str, Any] | None) -> None:
        self.data = data

    async def get_weather(self, city: str) -> dict[str, Any] | None:
        return self.data


@pytest.mark.anyio
async def test_weather_checker_rain_alert() -> None:
    """測試降雨警報判定。"""
    # Arrange
    client = FakeWeatherClient(
        data={
            "city": "台北",
            "temp_c": 26.0,
            "feels_like_c": 27.0,
            "description": "雷陣雨",
            "rain_prob": 90,
            "rain_1h": 15.0,
            "humidity": 85,
        }
    )
    checker = WeatherChecker(client)
    trigger = Trigger(
        trigger_id="w-1",
        user_id=1,
        channel_id=1,
        trigger_type="weather",
        condition={"city": "台北", "alert_type": "rain", "threshold": 50},
    )

    # Act
    res = await checker.should_fire(trigger)

    # Assert
    assert res.should_fire is True
    assert "降雨警報" in res.current_value


@pytest.mark.anyio
async def test_weather_checker_temp_above() -> None:
    """測試高溫特報判定。"""
    # Arrange: 36.5度, 門檻 35度
    client = FakeWeatherClient(
        data={
            "city": "台北",
            "temp_c": 36.5,
            "feels_like_c": 40.0,
            "description": "晴天",
            "rain_prob": 0,
            "rain_1h": 0.0,
            "humidity": 60,
        }
    )
    checker = WeatherChecker(client)
    trigger = Trigger(
        trigger_id="w-2",
        user_id=1,
        channel_id=1,
        trigger_type="weather",
        condition={"city": "台北", "alert_type": "temp_above", "threshold": 35.0},
    )

    # Act
    res = await checker.should_fire(trigger)

    # Assert
    assert res.should_fire is True
    assert "高溫特報" in res.current_value


# ===========================================================================
# 5. SPARK 2.0 進階排程 (days_of_week, max_runs) 與 WebMonitor 測試
# ===========================================================================

@pytest.mark.anyio
async def test_schedule_checker_days_of_week() -> None:
    """測試 days_of_week 星期過濾功能。"""
    checker = ScheduleChecker()
    # 限制平日 0~4 (週一到週五)
    trigger = Trigger(
        trigger_id="s-weekdays",
        user_id=1,
        channel_id=1,
        trigger_type="schedule",
        condition={
            "mode": "interval",
            "interval_minutes": 10,
            "days_of_week": [0, 1, 2, 3, 4],
        },
        last_triggered_at="",
    )

    # 模擬週六 (2026-10-17 is Saturday, weekday=5)
    saturday_dt = datetime(2026, 10, 17, 10, 0, 0, tzinfo=timezone.utc)
    res_sat = await checker.should_fire(trigger, now_dt=saturday_dt)
    assert res_sat.should_fire is False
    assert "星期" in res_sat.current_value

    # 模擬週一 (2026-10-19 is Monday, weekday=0)
    monday_dt = datetime(2026, 10, 19, 10, 0, 0, tzinfo=timezone.utc)
    res_mon = await checker.should_fire(trigger, now_dt=monday_dt)
    assert res_mon.should_fire is True


@pytest.mark.anyio
async def test_schedule_checker_max_runs() -> None:
    """測試 max_runs 上限判定與 one_shot_fired 自動標記。"""
    checker = ScheduleChecker()

    # 1. 已達上限
    trigger_maxed = Trigger(
        trigger_id="s-maxed",
        user_id=1,
        channel_id=1,
        trigger_type="schedule",
        condition={"mode": "interval", "interval_minutes": 10, "max_runs": 3},
        extra_data={"run_count": 3},
    )
    res_maxed = await checker.should_fire(trigger_maxed)
    assert res_maxed.should_fire is False
    assert "已達執行次數上限" in res_maxed.current_value

    # 2. 這是第 3 次（達到上限，本次觸發後應 auto-pause）
    trigger_last_run = Trigger(
        trigger_id="s-last-run",
        user_id=1,
        channel_id=1,
        trigger_type="schedule",
        condition={"mode": "interval", "interval_minutes": 10, "max_runs": 3},
        extra_data={"run_count": 2},
        last_triggered_at="",
    )
    res_last = await checker.should_fire(trigger_last_run)
    assert res_last.should_fire is True
    assert res_last.detail_data.get("one_shot_fired") is True


class FakeWebMonitorClient:
    """測試專用 Web 檢索客戶端。"""

    def __init__(self, results: list[dict[str, Any]]) -> None:
        self.results = results

    async def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        return self.results


@pytest.mark.anyio
async def test_web_monitor_checker_deduplication() -> None:
    """測試 WebMonitorChecker 內容指紋比對與去重機制。"""
    from handlers.spark.checkers.web_monitor_checker import WebMonitorChecker

    initial_results = [
        {"title": "美國最新CPI公布", "snippet": "年增2.4%符合市場預期", "url": "https://news.example/1"},
    ]
    client = FakeWebMonitorClient(initial_results)
    checker = WebMonitorChecker(web_client=client)

    trigger = Trigger(
        trigger_id="wm-1",
        user_id=1,
        channel_id=1,
        trigger_type="web_monitor",
        condition={"query": "CPI", "interval_minutes": 10},
    )

    # 1. 首次檢索 -> 應觸發
    res1 = await checker.should_fire(trigger)
    assert res1.should_fire is True
    content_hash = res1.detail_data.get("last_content_hash")
    assert content_hash

    # 2. 第二次檢索，指紋一致 -> 不觸發
    trigger.condition["last_content_hash"] = content_hash
    res2 = await checker.should_fire(trigger)
    assert res2.should_fire is False
    assert "內容無新變化" in res2.current_value

    # 3. 內容更新（出現重大降息新情報） -> 重新觸發
    client.results = [
        {"title": "聯準會突宣布降息兩碼", "snippet": "主席鮑爾發表演說", "url": "https://news.example/2"},
    ]
    res3 = await checker.should_fire(trigger)
    assert res3.should_fire is True
    assert res3.detail_data.get("last_content_hash") != content_hash


@pytest.mark.anyio
async def test_web_monitor_checker_ai_condition() -> None:
    """測試 WebMonitorChecker 的 AI 語意守門員過濾。"""
    from handlers.spark.checkers.web_monitor_checker import WebMonitorChecker

    client = FakeWebMonitorClient([
        {"title": "美股盤後分析", "snippet": "今日成交量平淡", "url": "https://news.example/3"},
    ])

    mock_evaluator = MagicMock()
    # 語意條件不滿足
    mock_evaluator.evaluate = AsyncMock(return_value=(False, "無重大突破或數據發布"))

    checker = WebMonitorChecker(web_client=client, ai_evaluator=mock_evaluator)
    trigger = Trigger(
        trigger_id="wm-eval",
        user_id=1,
        channel_id=1,
        trigger_type="web_monitor",
        condition={"query": "CPI", "ai_condition": "重大突破或數據發布"},
    )

    res = await checker.should_fire(trigger)
    assert res.should_fire is False
    assert "未滿足語意過濾條件" in res.current_value
    mock_evaluator.evaluate.assert_awaited_once()

