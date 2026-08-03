"""
mod.game_center
===============
動態小遊戲遊樂場模組 (Plug & Play Game Center Mod).

本模組提供雪莉 Web UI 的小遊戲遊戲邏輯（猜拳、21點、踩地雷）
以及結合地端 LLM (Primary Model) 的雪莉名偵探個性互動系統。
"""

from __future__ import annotations

MOD_INFO = {
    "id": "game_center",
    "name": "小遊戲遊樂場",
    "icon": "🎮",
    "description": "與雪莉名偵探使用地端 LLM 進行 21 點、猜拳、踩地雷等對弈互動！",
    "version": "1.0.0",
    "games": [
        {"id": "rps", "name": "猜拳大對決", "icon": "✌️"},
        {"id": "blackjack", "name": "21 點撲克", "icon": "🃏"},
        {"id": "minesweeper", "name": "踩地雷大冒險", "icon": "💣"},
    ],
}

__all__ = ["MOD_INFO"]
