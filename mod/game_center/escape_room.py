"""
mod.game_center.escape_room
===========================
Detective Escape Room Text Adventure Engine.
"""

from __future__ import annotations

import uuid
from typing import Any

from mod.game_center.models import EscapeRoomState, GameResultStatus


class EscapeRoomEngine:
    """Escape Room adventure engine."""

    def __init__(self) -> None:
        self._active_games: dict[str, EscapeRoomState] = {}

    def start_game(self) -> EscapeRoomState:
        """Start escape room adventure."""
        game_id = str(uuid.uuid4())[:8]
        state = EscapeRoomState(
            game_id=game_id,
            current_room="密室中央",
            inventory=[],
            unlocked_clues=["門上刻著神秘符號"],
            status=GameResultStatus.ONGOING,
            sherry_narration="咔嚓！密室的大門突然鎖上了！哼哼～不過別慌，有本名偵探在！我們一起調查房間裡的『書桌』、『畫框』與『大門密碼鎖』吧 🍊✨",
        )
        self._active_games[game_id] = state
        return state

    def get_game(self, game_id: str) -> EscapeRoomState | None:
        """Get active game state."""
        return self._active_games.get(game_id)
