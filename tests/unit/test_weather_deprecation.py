"""tests/unit/test_weather_deprecation.py
=========================================
驗證 WeatherTool 退役與氣象查詢收斂至 SEARCH 檢索管道。
"""

from __future__ import annotations


from app.orchestrator import SherryCore
from handlers.social.dispatch.prompts import _TOOL_ALIASES
from handlers.social.registry import TOOL_LIST
from handlers.social.tools.chat_tools import _should_trigger_search
from handlers.social.tools.weather_tool import WeatherTool


def test_weather_queries_trigger_search() -> None:
    """測試各類天氣與氣象自然提問皆能自動觸發聯網檢索。"""
    queries = [
        "台北明天會下雨嗎？",
        "今天天氣怎麼樣？",
        "現在室外氣溫大概幾度？",
        "這週末高雄降雨機率高嗎？",
        "有颱風會登陸台灣嗎？",
        "今天紫外線會不會很強？",
        "新竹目前的體感溫度如何？",
    ]
    for q in queries:
        assert _should_trigger_search(q) is True, f"'{q}' 應成功觸發聯網檢索！"


def test_weather_tool_removed_from_active_registry() -> None:
    """驗證 WeatherTool 已從日常主動調用工具清單中退役。"""
    active_intents = [t.intent_name for t in TOOL_LIST]
    assert "WEATHER" not in active_intents, "WEATHER 不應存在於主動調用工具 TOOL_LIST 中！"


def test_orchestrator_capabilities_weather_consolidated() -> None:
    """驗證微核心 CapabilityRegistry 中天氣已收斂至 SEARCH。"""
    core = SherryCore()
    search_cap = core.registry.get_capability("SEARCH")
    assert search_cap is not None
    assert "天氣" in search_cap.aliases
    assert "氣溫" in search_cap.aliases
    # WEATHER 不應作為獨立核心工具註冊
    assert core.registry.get_capability("WEATHER") is None


def test_tool_aliases_weather_routes_to_search() -> None:
    """驗證調度別名中，WEATHER 與 WEATHER_LOOKUP 均自動導向至 SEARCH。"""
    assert _TOOL_ALIASES.get("WEATHER") == "SEARCH"
    assert _TOOL_ALIASES.get("WEATHER_LOOKUP") == "SEARCH"
    assert _TOOL_ALIASES.get("WEATHER_REPORT") == "SEARCH"


def test_weather_tool_backward_compatibility() -> None:
    """驗證 WeatherTool 類別定義仍維持健全，以相容舊代碼或外部單元測試。"""
    tool = WeatherTool()
    assert tool.intent_name == "WEATHER"
    assert hasattr(tool, "execute")
