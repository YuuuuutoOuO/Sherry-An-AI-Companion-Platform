"""
handlers/social/tools/weather_tool.py
=======================================
天氣查詢工具。

資料流：
  1. LLM 從使用者語句萃取城市名稱（英文）
  2. OpenWeatherMap Free API 查詢即時天氣
  3. LLM（雪莉）以語氣播報天氣（熱就抱怨，冷就抱怨，總之都要抱怨）

完全不使用 GPU（純 HTTP 請求）。

環境變數：OPENWEATHERMAP_API_KEY（在 .env 設定）
"""

from __future__ import annotations

import asyncio
import logging
import re

import aiohttp
from typing import Any
import discord
from discord.ext import commands

import settings
from handlers.bot_brain import BotBrain, InferenceTask
from handlers.social.contracts import ToolCategory, ToolResult
from handlers.social.observability import log_event, make_timer
from handlers.social.tool_base import BaseTool, ToolResponseMode

logger = logging.getLogger(__name__)

_WEATHER_REGEX = re.compile(
    r"(天氣|氣溫|溫度|會下雨|下雨嗎|紫外線|體感|濕度|幾度|weather|降雨|颱風)",
    re.IGNORECASE,
)

# 常見城市中英文對照
_CITY_MAP: dict[str, str] = {
    "台北": "Taipei", "新北": "New Taipei City", "桃園": "Taoyuan",
    "台中": "Taichung", "台南": "Tainan", "高雄": "Kaohsiung",
    "基隆": "Keelung", "新竹": "Hsinchu", "花蓮": "Hualien",
    "宜蘭": "Yilan", "屏東": "Pingtung",
    "東京": "Tokyo", "大阪": "Osaka", "京都": "Kyoto",
    "首爾": "Seoul", "北京": "Beijing", "上海": "Shanghai",
    "香港": "Hong Kong", "新加坡": "Singapore",
    "紐約": "New York", "洛杉磯": "Los Angeles", "倫敦": "London",
    "巴黎": "Paris", "雪梨": "Sydney",
}

_OWM_API_URL = settings.OPENWEATHERMAP_API_URL


def _weather_desc_to_emoji(description: str) -> str:
    desc = description.lower()
    if any(k in desc for k in ["clear", "sunny", "晴"]): return "☀️"
    if any(k in desc for k in ["cloud", "陰", "多雲"]): return "☁️"
    if any(k in desc for k in ["rain", "shower", "drizzle", "雨"]): return "🌧️"
    if any(k in desc for k in ["storm", "thunder", "雷"]): return "⛈️"
    if any(k in desc for k in ["snow", "雪"]): return "❄️"
    if any(k in desc for k in ["fog", "mist", "haze", "霧"]): return "🌫️"
    return "🌤️"


class WeatherTool(BaseTool):
    """即時天氣查詢，雪莉用語氣播報。"""

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """取得或惰性初始化共用的 aiohttp.ClientSession 連線池。"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=settings.WEATHER_EXTRACT_TIMEOUT)
            )
        return self._session

    @property
    def intent_name(self) -> str:
        return "WEATHER"

    @property
    def llm_description(self) -> str:
        return "查詢某個城市的即時天氣、溫度、濕度、降雨狀況等氣象資訊"

    def can_handle_by_regex(self, text: str) -> bool:
        return bool(_WEATHER_REGEX.search(text))

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
            return ToolResult.error("BotBrain 未就緒。", is_direct_rendered=not silent)
        brain: Any = brain_cog

        api_key = getattr(settings, "OPENWEATHERMAP_API_KEY", "")
        if not api_key:
            err = "嗚嗚，我沒有設定天氣 API Key 啦！請在 .env 加入 OPENWEATHERMAP_API_KEY 哦～"
            if not silent:
                await message.reply(err, mention_author=True)
            return ToolResult.error(err, is_direct_rendered=not silent)

        # ── Step 1: LLM 萃取城市名稱 ─────────────────────────────────────
        city_future: asyncio.Future[str] = bot.loop.create_future()

        async def _on_city_extract(raw: str) -> None:
            if not city_future.done():
                city_future.set_result(raw.strip())

        city_extract_task = InferenceTask(
            priority=0,
            system_prompt=(
                "[PARSER MODE]\n"
                "請從使用者輸入萃取出「城市名稱」的英文拼音，"
                "只輸出英文城市名（例如 Taipei / Tokyo / New York），"
                "不要有任何解釋或標點。"
                "若無明確城市，輸出：Taipei"
            ),
            user_prompt=user_query,
            callback=_on_city_extract,
            model=settings.CLASSIFIER_MODEL,
            task_id=f"weather-extract-{message.id}",
        )
        await brain.enqueue_task(city_extract_task)

        try:
            raw_city = await asyncio.wait_for(city_future, timeout=settings.WEATHER_EXTRACT_TIMEOUT)
        except asyncio.TimeoutError:
            raw_city = "Taipei"

        # 嘗試從常見對照表解析，否則使用 LLM 的結果
        city = raw_city
        for cn, en in _CITY_MAP.items():
            if cn in user_query:
                city = en
                break

        logger.info("WeatherTool: 查詢城市 = %s", city)

        # ── Step 2: 呼叫 OpenWeatherMap API ─────────────────────────────
        weather_data: dict[str, Any] | None = None
        try:
            session = await self._get_session()
            params = {
                "q": city,
                "appid": api_key,
                "units": "metric",
                "lang": "zh_tw",
            }
            async with session.get(_OWM_API_URL, params=params) as resp:
                if resp.status == 200:
                    weather_data = await resp.json()
                elif resp.status == 404:
                    err = f"找不到「{city}」這個城市耶...確認一下有沒有打錯？"
                    if not silent:
                        await message.reply(err, mention_author=True)
                    return ToolResult.no_data(err, is_direct_rendered=not silent)
                else:
                    logger.warning("WeatherTool: API 回應 %d", resp.status)
                    err = "天氣服務暫時無法回應，等等再試試看喔～"
                    if not silent:
                        await message.reply(err, mention_author=True)
                    return ToolResult.error(err, is_direct_rendered=not silent)
        except Exception as exc:
            logger.error("WeatherTool: HTTP 請求失敗: %s", exc)
            err = "天氣查詢失敗了...網路或 API 出問題了嗎？"
            if not silent:
                await message.reply(err, mention_author=True)
            return ToolResult.error(err, is_direct_rendered=not silent)

        if not weather_data:
            err = "未能取得有效的天氣資料。"
            if not silent:
                await message.reply(err, mention_author=True)
            return ToolResult.error(err, is_direct_rendered=not silent)

        # ── Step 3: 解析天氣資料 ──────────────────────────────────────────
        main_dict = weather_data.get("main", {})
        temp = float(main_dict.get("temp", 0.0))
        feels_like = float(main_dict.get("feels_like", 0.0))
        humidity = main_dict.get("humidity", 0)
        weather_list = weather_data.get("weather", [{}])
        description = weather_list[0].get("description", "") if weather_list else ""
        wind_dict = weather_data.get("wind", {})
        wind_speed = wind_dict.get("speed", 0)
        city_name = weather_data.get("name", city)
        emoji = _weather_desc_to_emoji(description)

        weather_summary = (
            f"【{city_name} 即時天氣】{emoji}\n"
            f"天氣狀況：{description}\n"
            f"溫度：{temp:.1f}°C（體感 {feels_like:.1f}°C）\n"
            f"濕度：{humidity}%\n"
            f"風速：{wind_speed} m/s"
        )

        # 若為 Agent 靜默調度模式，直接回傳純資料，交由 Agent 統一發言合成
        if silent:
            return ToolResult.success(
                weather_summary,
                is_direct_rendered=False,
                extra_data={"city": city_name, "temp": temp, "humidity": humidity},
            )

        # ── Step 4: 雪莉語氣播報（獨立執行時） ─────────────────────────────
        reply_future: asyncio.Future[str] = bot.loop.create_future()

        async def _on_weather_reply(raw: str) -> None:
            if not reply_future.done():
                reply_future.set_result(raw.strip())

        from handlers.social.persona import build_dynamic_persona
        head_persona, _tail = build_dynamic_persona(user_query, self.intent_name, channel_id=message.channel.id)
        persona = head_persona

        reply_task = InferenceTask(
            priority=settings.PRIORITY_TAG_RESPONSE,
            system_prompt=(
                f"{persona}\n\n"
                "根據以下天氣資料，用輕鬆活潑的語氣播報天氣。"
                "太熱就抱怨熱，太冷就抱怨冷，下雨就抱怨要帶傘，"
                "可以加入偵探雪莉風格的吐槽，但要保持可愛。"
            ),
            user_prompt=f"{user_query}\n\n{weather_summary}",
            callback=_on_weather_reply,
            model=settings.PRIMARY_MODEL,
            task_id=f"weather-reply-{message.id}",
        )
        await brain.enqueue_task(reply_task)

        try:
            reply_text = await asyncio.wait_for(reply_future, timeout=settings.WEATHER_REPLY_TIMEOUT)
        except asyncio.TimeoutError:
            reply_text = weather_summary

        try:
            await message.reply(reply_text, mention_author=True)
        except Exception as exc:
            logger.error("WeatherTool: 回覆失敗: %s", exc)

        log_event(
            "weather_query",
            message_id=message.id,
            user=message.author.display_name,
            channel_id=message.channel.id,
            raw_input=user_query,
            intent="WEATHER",
            intent_source="regex_fast",
            reply_preview=reply_text[:120],
            model_used=settings.PRIMARY_MODEL,
            latency_ms=elapsed(),
            extra_city=city_name,
            extra_temp=temp,
            extra_humidity=humidity,
        )
        return ToolResult.success(reply_text, is_direct_rendered=True)
