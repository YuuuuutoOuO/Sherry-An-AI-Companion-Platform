"""mod/spark/checkers/weather_checker.py - 天氣與環境預警策略。

純業務邏輯層：透過 WeatherClientProtocol 協定注入天氣查詢客戶端。
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from ..models import Trigger, TriggerResult

logger = logging.getLogger(__name__)


class WeatherClientProtocol(Protocol):
    """天氣客戶端協定介面。"""

    async def get_weather(self, city: str) -> dict[str, Any] | None:
        """取得特定城市的天氣資訊字典。"""
        ...


class WeatherChecker:
    """即時天氣與環境預警檢查器。"""

    trigger_type: str = "weather"

    def __init__(self, weather_client: WeatherClientProtocol) -> None:
        """初始化天氣檢查器。

        Args:
            weather_client: 實作 WeatherClientProtocol 的客戶端實例。
        """
        self._client = weather_client

    async def should_fire(self, trigger: Trigger) -> TriggerResult:
        """評估天氣預警或報告條件。

        Args:
            trigger: 欲評估的 Trigger 實例。

        Returns:
            TriggerResult: 判定結果與天氣概況。
        """
        cond = trigger.condition
        city = str(cond.get("city", "Taipei")).strip()
        alert_type = str(cond.get("alert_type", "rain")).lower().strip()

        weather_data = await self._client.get_weather(city)
        if weather_data is None:
            return TriggerResult(
                should_fire=False,
                current_value=f"無法獲取 {city} 的天氣資訊",
            )

        temp_c = float(weather_data.get("temp_c", 0.0))
        feels_like = float(weather_data.get("feels_like_c", temp_c))
        desc = str(weather_data.get("description", ""))
        rain_prob = int(weather_data.get("rain_prob", 0))
        rain_1h = float(weather_data.get("rain_1h", 0.0))

        should_fire = False
        summary = ""

        if alert_type in ("rain", "raining", "rain_alert"):
            # 降雨警報：有實際降雨或降雨機率高於門檻
            threshold_prob = int(cond.get("threshold", 50))
            if rain_1h > 0 or rain_prob >= threshold_prob or "雨" in desc:
                should_fire = True
                summary = (
                    f"🌧️ 【{city} 降雨警報】\n"
                    f"目前狀況：{desc}（降雨量 {rain_1h:.1f}mm/h）\n"
                    f"氣溫：{temp_c:.1f}°C（體感 {feels_like:.1f}°C）"
                )
            else:
                summary = f"{city} 目前無降雨（狀況：{desc}，氣溫 {temp_c:.1f}°C）"

        elif alert_type in ("temp_above", "heat", "hot"):
            # 高溫警報
            threshold_temp = float(cond.get("threshold", 34.0))
            if temp_c >= threshold_temp:
                should_fire = True
                summary = (
                    f"☀️ 【{city} 高溫特報】\n"
                    f"目前氣溫已達 {temp_c:.1f}°C（超過門檻 {threshold_temp:.1f}°C）\n"
                    f"體感溫度 {feels_like:.1f}°C，請注意防曬與補水！"
                )
            else:
                summary = f"{city} 氣溫 {temp_c:.1f}°C（未達高溫門檻 {threshold_temp:.1f}°C）"

        elif alert_type in ("temp_below", "cold", "freeze"):
            # 低溫警報
            threshold_temp = float(cond.get("threshold", 12.0))
            if temp_c <= threshold_temp:
                should_fire = True
                summary = (
                    f"❄️ 【{city} 低溫特報】\n"
                    f"目前氣溫降至 {temp_c:.1f}°C（低於門檻 {threshold_temp:.1f}°C）\n"
                    f"體感溫度 {feels_like:.1f}°C，請務必做好保暖措施！"
                )
            else:
                summary = f"{city} 氣溫 {temp_c:.1f}°C（高於低溫門檻 {threshold_temp:.1f}°C）"

        elif alert_type in ("daily_summary", "report", "summary"):
            # 天氣概況播報
            should_fire = True
            summary = (
                f"🌤️ 【{city} 即時天氣報告】\n"
                f"氣溫：{temp_c:.1f}°C（體感 {feels_like:.1f}°C）\n"
                f"概況：{desc} | 濕度：{weather_data.get('humidity', 0)}%"
            )

        else:
            summary = f"未知的氣象警報類型: {alert_type}"

        return TriggerResult(
            should_fire=should_fire,
            current_value=summary,
            detail_data=weather_data,
        )
