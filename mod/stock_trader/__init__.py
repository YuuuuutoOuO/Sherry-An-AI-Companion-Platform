"""
mod.stock_trader
================
雪莉美股虛擬量化交易模組 (Sherry US Stock Quant Paper Trader).
"""

from __future__ import annotations

MOD_INFO = {
    "id": "stock_trader",
    "name": "雪莉美股虛擬量化交易",
    "icon": "📈",
    "description": "23:00~04:00 自動看盤、03:50 AM 強看盤、$10,000 美金虛擬量化下單與 Web UI 1個月盈餘率走勢圖。",
    "category": "金融 / Quant Trading",
    "version": "1.0.0",
    "author": "Sherry Team",
    "has_web_ui": True,
    "nav_id": "stock-trader",
    "nav_title": "雪莉虛擬炒股",
    "nav_icon": "📈",
    "view_title": "📈 橘雪莉 美股虛擬量化交易控制台",
    "admin_only": False,
    "order": 40,
    "script_file": "stock_trader.js",
}

__all__ = ["MOD_INFO"]
