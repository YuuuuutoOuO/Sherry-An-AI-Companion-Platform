"""
mod.game_center.models
======================
Data structures and dataclasses for Sherry Game Center.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class GameType(str, Enum):
    """Supported game types in Sherry Game Center."""
    RPS = "rps"
    BLACKJACK = "blackjack"
    MINESWEEPER = "minesweeper"


class RPSChoice(str, Enum):
    """Rock Paper Scissors choices."""
    ROCK = "rock"
    PAPER = "paper"
    SCISSORS = "scissors"


class GameResultStatus(str, Enum):
    """Outcome status of a game round."""
    WIN = "win"
    LOSE = "lose"
    DRAW = "draw"
    ONGOING = "ongoing"


@dataclass(frozen=True)
class Card:
    """Playing card representation for Blackjack."""
    suit: str  # ♠, ♥, ♦, ♣
    rank: str  # A, 2-10, J, Q, K
    value: int  # 1-11

    def to_dict(self) -> dict[str, Any]:
        """Convert card to dictionary."""
        return {
            "suit": self.suit,
            "rank": self.rank,
            "value": self.value,
        }


@dataclass
class BlackjackState:
    """State tracking for a Blackjack game round."""
    game_id: str
    player_cards: list[Card] = field(default_factory=list)
    dealer_cards: list[Card] = field(default_factory=list)
    status: GameResultStatus = GameResultStatus.ONGOING
    player_score: int = 0
    dealer_score: int = 0
    sherry_thought: str = ""
    sherry_dialogue: str = ""
    is_player_turn: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Serialize state for JSON responses."""
        return {
            "game_id": self.game_id,
            "player_cards": [c.to_dict() for c in self.player_cards],
            "dealer_cards": [c.to_dict() for c in self.dealer_cards],
            "player_score": self.player_score,
            "dealer_score": self.dealer_score,
            "status": self.status.value,
            "sherry_thought": self.sherry_thought,
            "sherry_dialogue": self.sherry_dialogue,
            "is_player_turn": self.is_player_turn,
        }


@dataclass
class MinesweeperCell:
    """Single cell representation in Minesweeper board."""
    row: int
    col: int
    is_mine: bool = False
    is_revealed: bool = False
    is_flagged: bool = False
    neighbor_mines: int = 0

    def to_dict(self, reveal_all: bool = False) -> dict[str, Any]:
        """Serialize cell info without leaking mine location unless revealed."""
        return {
            "row": self.row,
            "col": self.col,
            "is_revealed": self.is_revealed,
            "is_flagged": self.is_flagged,
            "neighbor_mines": self.neighbor_mines if (self.is_revealed or reveal_all) else 0,
            "is_mine": self.is_mine if (reveal_all or self.is_revealed) else False,
        }


@dataclass
class MinesweeperState:
    """State for Minesweeper game board."""
    game_id: str
    rows: int = 8
    cols: int = 8
    mine_count: int = 10
    board: list[list[MinesweeperCell]] = field(default_factory=list)
    status: GameResultStatus = GameResultStatus.ONGOING
    revealed_count: int = 0
    sherry_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize Minesweeper state."""
        reveal_all = (self.status != GameResultStatus.ONGOING)
        return {
            "game_id": self.game_id,
            "rows": self.rows,
            "cols": self.cols,
            "mine_count": self.mine_count,
            "status": self.status.value,
            "revealed_count": self.revealed_count,
            "sherry_hint": self.sherry_hint,
            "board": [
                [cell.to_dict(reveal_all=reveal_all) for cell in row_cells]
                for row_cells in self.board
            ],
        }
