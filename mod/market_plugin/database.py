"""
database.py  (mod/market_plugin)
==================================
已重構至 repository.py。
此模組保留以維持向後相容。
"""

from __future__ import annotations

from mod.market_plugin.repository import Database, MarketRepository

__all__ = ["Database", "MarketRepository"]

