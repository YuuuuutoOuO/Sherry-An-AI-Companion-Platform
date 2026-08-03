"""
mod.game_center.blackjack
=========================
Blackjack (21-point) game engine.
"""

from __future__ import annotations

import random
import uuid
from typing import Any

from mod.game_center.models import BlackjackState, Card, GameResultStatus


class BlackjackEngine:
    """Blackjack card game manager."""

    SUITS: list[str] = ["♠", "♥", "♦", "♣"]
    RANKS: list[tuple[str, int]] = [
        ("A", 11),
        ("2", 2), ("3", 3), ("4", 4), ("5", 5),
        ("6", 6), ("7", 7), ("8", 8), ("9", 9), ("10", 10),
        ("J", 10), ("Q", 10), ("K", 10),
    ]

    def __init__(self) -> None:
        self._active_games: dict[str, BlackjackState] = {}

    def _create_deck(self) -> list[Card]:
        """Generate a fresh shuffled 52-card deck."""
        deck: list[Card] = []
        for suit in self.SUITS:
            for rank, val in self.RANKS:
                deck.append(Card(suit=suit, rank=rank, value=val))
        random.shuffle(deck)
        return deck

    @staticmethod
    def calculate_score(cards: list[Card]) -> int:
        """Calculate total hand score accounting for Ace flex (1 or 11)."""
        score = sum(c.value for c in cards)
        ace_count = sum(1 for c in cards if c.rank == "A")
        while score > 21 and ace_count > 0:
            score -= 10
            ace_count -= 1
        return score

    def start_game(self) -> BlackjackState:
        """Initialize a new game round and deal initial 2 cards to player and Sherry dealer."""
        game_id = str(uuid.uuid4())[:8]
        deck = self._create_deck()

        player_cards = [deck.pop(), deck.pop()]
        dealer_cards = [deck.pop(), deck.pop()]

        p_score = self.calculate_score(player_cards)
        d_score = self.calculate_score(dealer_cards)

        status = GameResultStatus.ONGOING
        if p_score == 21:
            status = GameResultStatus.WIN if d_score != 21 else GameResultStatus.DRAW

        state = BlackjackState(
            game_id=game_id,
            player_cards=player_cards,
            dealer_cards=dealer_cards,
            status=status,
            player_score=p_score,
            dealer_score=d_score,
            is_player_turn=(status == GameResultStatus.ONGOING),
        )
        self._active_games[game_id] = state
        return state

    def hit(self, game_id: str) -> BlackjackState | None:
        """Player draws a card."""
        state = self._active_games.get(game_id)
        if not state or state.status != GameResultStatus.ONGOING or not state.is_player_turn:
            return state

        deck = self._create_deck()
        state.player_cards.append(deck.pop())
        state.player_score = self.calculate_score(state.player_cards)

        if state.player_score > 21:
            state.status = GameResultStatus.LOSE
            state.is_player_turn = False
        elif state.player_score == 21:
            state.is_player_turn = False

        return state

    def stand(self, game_id: str) -> BlackjackState | None:
        """Player stands; Sherry dealer plays her turn."""
        state = self._active_games.get(game_id)
        if not state or state.status != GameResultStatus.ONGOING:
            return state

        state.is_player_turn = False
        deck = self._create_deck()

        # Sherry hits until reaching 17 or higher
        while state.dealer_score < 17:
            state.dealer_cards.append(deck.pop())
            state.dealer_score = self.calculate_score(state.dealer_cards)

        if state.dealer_score > 21:
            state.status = GameResultStatus.WIN
        elif state.dealer_score > state.player_score:
            state.status = GameResultStatus.LOSE
        elif state.dealer_score < state.player_score:
            state.status = GameResultStatus.WIN
        else:
            state.status = GameResultStatus.DRAW

        return state

    def get_game(self, game_id: str) -> BlackjackState | None:
        """Retrieve existing active game state."""
        return self._active_games.get(game_id)
