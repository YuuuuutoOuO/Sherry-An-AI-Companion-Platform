"""mod/spark/__init__.py - Spark 泛用推播系統模組入口。"""

from __future__ import annotations

from typing import Any

MOD_INFO: dict[str, Any] = {
    "id": "spark",
    "name": "📡 Spark 泛用推播",
    "icon": "📡",
    "description": "泛用排程與條件推播通知系統（價格 / 定時 / RSS / 天氣）。",
    "category": "工具 / Tools",
    "version": "1.0.0",
    "author": "Sherry Developer",
    "has_web_ui": True,
    "nav_id": "spark",
    "nav_title": "Spark 推播",
    "nav_icon": "📡",
    "view_title": "📡 Spark 推播主控台",
    "admin_only": False,
    "order": 60,
    "script_file": "spark.js",
}

__all__ = ["MOD_INFO"]
