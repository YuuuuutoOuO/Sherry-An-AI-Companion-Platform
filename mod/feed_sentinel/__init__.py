"""
mod.feed_sentinel
=================
AI 社群情報哨兵模組 (Feed Sentinel Plugin).

本模組定時監聽 HuggingFace、arXiv、GitHub 等熱門 AI 開源專案與論文，
由橘雪莉深入閱讀後提煉「3 大繁中核心重點」與「名偵探銳評」推播至指定 Discord 頻道。
"""

from __future__ import annotations

MOD_INFO = {
    "id": "feed_sentinel",
    "name": "📡 AI 社群情報哨兵",
    "icon": "📡",
    "description": "監聽 HuggingFace / arXiv / GitHub 最新 AI 突破，提煉 3 點繁中精華與雪莉銳評推播至 Discord。",
    "category": "情報 / Intelligence",
    "version": "1.1.0",
    "author": "Sherry Team",
    "has_web_ui": True,
    "nav_id": "feed-sentinel",
    "nav_title": "社群情報哨兵",
    "nav_icon": "📡",
    "view_title": "📡 AI 社群情報哨兵主控台",
    "admin_only": False,
    "order": 55,
    "script_file": "feed_sentinel.js",
}

__all__ = ["MOD_INFO"]
