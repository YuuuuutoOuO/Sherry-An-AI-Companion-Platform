"""mod/spark/clients/weather_client.py - 即時天氣與預警資料查詢客戶端。

負責對接 OpenWeatherMap API 或公開天氣端點獲取氣溫、降雨與天氣概況。
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from ..config import DEFAULT_TIMEOUT_SEC, OPENWEATHERMAP_API_KEY, OPENWEATHERMAP_API_URL

logger = logging.getLogger(__name__)

# 常見城市中英文轉換表
_CITY_MAP: dict[str, str] = {
    "台北": "Taipei",
    "新北": "New Taipei",
    "桃園": "Taoyuan",
    "台中": "Taichung",
    "台南": "Tainan",
    "高雄": "Kaohsiung",
    "基隆": "Keelung",
    "新竹": "Hsinchu",
    "花蓮": "Hualien",
    "宜蘭": "Yilan",
    "屏東": "Pingtung",
    "東京": "Tokyo",
    "大阪": "Osaka",
    "首爾": "Seoul",
    "紐約": "New York",
    "倫敦": "London",
}


def normalize_city(city: str) -> str:
    """轉換城市名稱為英文標準格式。

    Args:
        city: 中文或英文城市名稱。

    Returns:
        str: 英文標準城市名。
    """
    clean = city.strip()
    return _CITY_MAP.get(clean, clean)


class WeatherClient:
    """即時天氣查詢客戶端。"""

    def __init__(
        self,
        api_key: str = OPENWEATHERMAP_API_KEY,
        api_url: str = OPENWEATHERMAP_API_URL,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        """初始化天氣客戶端。

        Args:
            api_key: OpenWeatherMap API Key。
            api_url: OpenWeatherMap 查詢端點。
            timeout_sec: 網路請求超時秒數。
        """
        self._api_key = api_key
        self._api_url = api_url
        self._timeout = aiohttp.ClientTimeout(total=timeout_sec)

    async def get_weather(self, city: str) -> dict[str, Any] | None:
        """取得特定城市的天氣資訊。

        Args:
            city: 城市名稱（支援中英文）。

        Returns:
            dict[str, Any] | None: 包含氣溫、濕度、天氣描述等之字典，失敗時回傳 None。
        """
        norm_city = normalize_city(city)

        # 1. 若有 OpenWeatherMap API Key，優先使用 OWM
        if self._api_key:
            owm_data = await self._fetch_open_weather_map(norm_city)
            if owm_data is not None:
                return owm_data

        # 2. 備援方案：wttr.in 公開 API
        return await self._fetch_wttr_in(norm_city)

    async def _fetch_open_weather_map(self, city: str) -> dict[str, Any] | None:
        """透過 OpenWeatherMap API 獲取即時資料。"""
        params = {
            "q": city,
            "appid": self._api_key,
            "units": "metric",
            "lang": "zh_tw",
        }
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.get(self._api_url, params=params) as resp:
                    if resp.status != 200:
                        return None
                    data: dict[str, Any] = await resp.json()
                    main = data.get("main", {})
                    weather_list = data.get("weather", [])
                    weather_desc = weather_list[0].get("description", "") if weather_list else ""
                    rain_data = data.get("rain", {})
                    rain_1h = rain_data.get("1h", 0.0)

                    return {
                        "city": city,
                        "temp_c": float(main.get("temp", 0.0)),
                        "feels_like_c": float(main.get("feels_like", 0.0)),
                        "humidity": int(main.get("humidity", 0)),
                        "description": weather_desc,
                        "rain_1h": float(rain_1h),
                        "rain_prob": 100 if rain_1h > 0 or "雨" in weather_desc else 0,
                    }
        except Exception as exc:
            logger.debug("[WeatherClient] OpenWeatherMap 失敗 (%s): %s", city, exc)
            return None

    async def _fetch_wttr_in(self, city: str) -> dict[str, Any] | None:
        """透過 wttr.in 獲取備援天氣資料。"""
        url = f"https://wttr.in/{city}?format=j1"
        headers = {"User-Agent": "curl/7.68.0"}
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        return None
                    data: dict[str, Any] = await resp.json()
                    current = data.get("current_condition", [{}])[0]
                    desc_list = current.get("lang_zh-tw") or current.get("weatherDesc") or [{}]
                    desc = desc_list[0].get("value", "")

                    temp_c = float(current.get("temp_C", 0.0))
                    feels_like = float(current.get("FeelsLikeC", temp_c))
                    humidity = int(current.get("humidity", 0))
                    precip_mm = float(current.get("precipMM", 0.0))

                    return {
                        "city": city,
                        "temp_c": temp_c,
                        "feels_like_c": feels_like,
                        "humidity": humidity,
                        "description": desc,
                        "rain_1h": precip_mm,
                        "rain_prob": 100 if precip_mm > 0 or "雨" in desc else 0,
                    }
        except Exception as exc:
            logger.debug("[WeatherClient] wttr.in 查詢失敗 (%s): %s", city, exc)
            return None
