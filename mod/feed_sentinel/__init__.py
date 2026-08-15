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
    "version": "1.0.0",
}

__all__ = ["MOD_INFO"]
