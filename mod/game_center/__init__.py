"""
mod.game_center
===============
動態小遊戲遊樂場模組 (Plug & Play Game Center Mod).

本模組提供雪莉 Web UI 的小遊戲遊戲邏輯（猜拳、21點、踩地雷、海龜湯、讀心術、密室逃脫、圈圈叉叉）
以及結合地端 LLM (Primary Model) 的雪莉名偵探個性互動系統。
"""

from __future__ import annotations

MOD_INFO = {
    "id": "game_center",
    "name": "小遊戲遊樂場",
    "icon": "🎮",
    "description": "與雪莉名偵探使用地端 LLM 進行 21 點、猜拳、踩地雷等對弈互動！",
    "category": "娛樂 / Entertainment",
    "version": "1.1.0",
    "author": "Sherry Team",
    "has_web_ui": True,
    "nav_id": "games",
    "nav_title": "小遊戲遊樂場",
    "nav_icon": "🎮",
    "view_title": "🎮 雪莉小遊戲遊樂場 (Mod)",
    "admin_only": False,
    "order": 35,
    "template_file": "games.html",
    "script_file": "games.js",
    "public_routes": ["/api/games/"],
    "games": ["rps", "blackjack", "minesweeper", "turtle_soup", "akinator", "escape_room", "ttt"],
}

__all__ = ["MOD_INFO"]
