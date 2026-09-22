"""
mod.game_center.tic_tac_toe
===========================
Tic-Tac-Toe (3x3 Grid) Game Engine.
"""

from __future__ import annotations

import random
import uuid

from mod.game_center.models import GameResultStatus, TicTacToeState


class TicTacToeEngine:
    """Tic-Tac-Toe game manager."""

    WIN_LINES: list[tuple[int, int, int]] = [
        (0, 1, 2), (3, 4, 5), (6, 7, 8),  # Rows
        (0, 3, 6), (1, 4, 7), (2, 5, 8),  # Cols
        (0, 4, 8), (2, 4, 6),              # Diagonals
    ]

    def __init__(self) -> None:
        self._active_games: dict[str, TicTacToeState] = {}

    def start_game(self) -> TicTacToeState:
        """Start new Tic-Tac-Toe game."""
        game_id = str(uuid.uuid4())[:8]
        state = TicTacToeState(
            game_id=game_id,
            board=[""] * 9,
            status=GameResultStatus.ONGOING,
            sherry_dialogue="圈圈叉叉大對決！你手拿 ❌，本名偵探手拿 ⭕～你先下吧！✨",
        )
        self._active_games[game_id] = state
        return state

    def check_winner(self, board: list[str]) -> str:
        """Check if X or O won, or if it's a draw/ongoing."""
        for a, b, c in self.WIN_LINES:
            if board[a] and board[a] == board[b] == board[c]:
                return board[a]
        if all(cell != "" for cell in board):
            return "draw"
        return "ongoing"

    def player_move(self, game_id: str, index: int) -> TicTacToeState | None:
        """Process player move and trigger Sherry's AI response move."""
        state = self._active_games.get(game_id)
        if not state or state.status != GameResultStatus.ONGOING:
            return state

        if 0 <= index < 9 and state.board[index] == "":
            state.board[index] = "X"
            res = self.check_winner(state.board)
            if res == "X":
                state.status = GameResultStatus.WIN
                return state
            elif res == "draw":
                state.status = GameResultStatus.DRAW
                return state

            # Sherry (O) moves
            empty_cells = [i for i, val in enumerate(state.board) if val == ""]
            if empty_cells:
                # Try winning move or blocking player move
                sherry_pick = None
                for i in empty_cells:
                    temp = list(state.board)
                    temp[i] = "O"
                    if self.check_winner(temp) == "O":
                        sherry_pick = i
                        break
                if sherry_pick is None:
                    for i in empty_cells:
                        temp = list(state.board)
                        temp[i] = "X"
                        if self.check_winner(temp) == "X":
                            sherry_pick = i
                            break
                if sherry_pick is None:
                    sherry_pick = random.choice(empty_cells)

                state.board[sherry_pick] = "O"
                res_after_sherry = self.check_winner(state.board)
                if res_after_sherry == "O":
                    state.status = GameResultStatus.LOSE
                elif res_after_sherry == "draw":
                    state.status = GameResultStatus.DRAW

        return state

    def get_game(self, game_id: str) -> TicTacToeState | None:
        """Get game state."""
        return self._active_games.get(game_id)
