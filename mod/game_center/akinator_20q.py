"""
mod.game_center.akinator_20q
=============================
20 Questions (Sherry Akinator) game engine.
"""

from __future__ import annotations

import uuid
from typing import Any

from mod.game_center.models import AkinatorState, GameResultStatus


class AkinatorEngine:
    """Engine managing 20 Questions game flow."""

    def __init__(self) -> None:
        self._active_games: dict[str, AkinatorState] = {}

    def start_game(self) -> AkinatorState:
        """Start a 20 Questions round."""
        game_id = str(uuid.uuid4())[:8]
        state = AkinatorState(
            game_id=game_id,
            question_count=1,
            current_question="他在心中想的是一個真實存在的人，還是二次元/虛構角色呢？",
            status=GameResultStatus.ONGOING,
        )
        self._active_games[game_id] = state
        return state

    def record_answer(self, game_id: str, answer: str) -> AkinatorState | None:
        """Record player's answer (yes, no, unknown)."""
        state = self._active_games.get(game_id)
        if not state or state.status != GameResultStatus.ONGOING:
            return state

        state.history.append({
            "q_num": str(state.question_count),
            "question": state.current_question,
            "answer": answer,
        })
        state.question_count += 1
        if state.question_count > state.max_questions:
            state.status = GameResultStatus.LOSE

        return state

    def get_game(self, game_id: str) -> AkinatorState | None:
        """Get game state."""
        return self._active_games.get(game_id)
