"""
mod.game_center.rock_paper_scissors
===================================
Rock Paper Scissors game logic engine.
"""

from __future__ import annotations

import random
from typing import Any

from mod.game_center.models import GameResultStatus, RPSChoice


class RockPaperScissorsEngine:
    """Engine for managing Rock Paper Scissors rounds against Sherry."""

    CHOICE_MAP: dict[str, RPSChoice] = {
        "rock": RPSChoice.ROCK,
        "paper": RPSChoice.PAPER,
        "scissors": RPSChoice.SCISSORS,
        "石頭": RPSChoice.ROCK,
        "布": RPSChoice.PAPER,
        "剪刀": RPSChoice.SCISSORS,
    }

    WIN_MATRIX: dict[tuple[RPSChoice, RPSChoice], GameResultStatus] = {
        (RPSChoice.ROCK, RPSChoice.SCISSORS): GameResultStatus.WIN,
        (RPSChoice.ROCK, RPSChoice.PAPER): GameResultStatus.LOSE,
        (RPSChoice.PAPER, RPSChoice.ROCK): GameResultStatus.WIN,
        (RPSChoice.PAPER, RPSChoice.SCISSORS): GameResultStatus.LOSE,
        (RPSChoice.SCISSORS, RPSChoice.PAPER): GameResultStatus.WIN,
        (RPSChoice.SCISSORS, RPSChoice.ROCK): GameResultStatus.LOSE,
    }

    @classmethod
    def play_round(cls, player_choice_str: str) -> dict[str, Any]:
        """Play a round of RPS against Sherry.

        Args:
            player_choice_str: Player's choice ("rock", "paper", "scissors" or Chinese equivalents).

        Returns:
            Dict containing round outcome details.
        """
        normalized_str = player_choice_str.strip().lower()
        player_choice = cls.CHOICE_MAP.get(normalized_str, RPSChoice.ROCK)

        # Sherry randomly picks her choice
        sherry_choice = random.choice([RPSChoice.ROCK, RPSChoice.PAPER, RPSChoice.SCISSORS])

        if player_choice == sherry_choice:
            outcome = GameResultStatus.DRAW
        else:
            outcome = cls.WIN_MATRIX[(player_choice, sherry_choice)]

        return {
            "player_choice": player_choice.value,
            "sherry_choice": sherry_choice.value,
            "outcome": outcome.value,
        }
