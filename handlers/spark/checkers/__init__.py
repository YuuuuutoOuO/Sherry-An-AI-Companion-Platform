"""mod/spark/checkers - Spark 觸發判定策略套件。"""

from __future__ import annotations

from .base import BaseChecker
from .price_checker import PriceChecker, is_market_open
from .rss_checker import RssChecker
from .schedule_checker import ScheduleChecker
from .weather_checker import WeatherChecker
from .web_monitor_checker import WebMonitorChecker, WebMonitorClientProtocol

__all__ = [
    "BaseChecker",
    "PriceChecker",
    "is_market_open",
    "ScheduleChecker",
    "RssChecker",
    "WeatherChecker",
    "WebMonitorChecker",
    "WebMonitorClientProtocol",
]
