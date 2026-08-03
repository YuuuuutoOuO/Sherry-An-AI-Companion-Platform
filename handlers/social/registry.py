"""
handlers/social/registry.py
"""

from handlers.social.tool_base import BaseTool
from handlers.social.tools.chat_tools import ChatTool
from handlers.social.tools.draw_tool import DrawTool
from handlers.social.tools.image_tool import ImageSearchTool
from handlers.social.tools.music_tool import MusicTool
from handlers.social.tools.schedule_tool import ScheduleTool
from handlers.social.tools.search_tools import AdvancedSearchTool, URLSummaryTool
from handlers.social.tools.stock_tool import StockTool
from handlers.social.tools.weather_tool import WeatherTool

TOOL_LIST: list[BaseTool] = [
    URLSummaryTool(),  # 丟了生連結要第一位解析，不能落到 Chat
    MusicTool(),  # 行為指派控制或 YT連結處理
    ScheduleTool(),  # 排程提醒 /schedule / 提醒我
    DrawTool(),  # 優先於圖庫觸發
    ImageSearchTool(),  # 圖庫觸發捕捉
    StockTool(),  # 股票/加密/指數 K線繪圖專用查詢
    WeatherTool(),  # 天氣查詢
    AdvancedSearchTool(),  # 相容性搜尋（引導至 ChatTool 聯網對話）
    ChatTool(),  # 防禦兜底，日常社交與聯網檢索智慧對話
]


def get_fallback_tool() -> BaseTool:
    return ChatTool()