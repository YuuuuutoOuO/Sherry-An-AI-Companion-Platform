"""
mod.game_center.minesweeper
===========================
Minesweeper game board logic engine.
"""

from __future__ import annotations

import random
import uuid
from typing import Any

from mod.game_center.models import GameResultStatus, MinesweeperCell, MinesweeperState


class MinesweeperEngine:
    """Minesweeper board manager."""

    def __init__(self) -> None:
        self._active_games: dict[str, MinesweeperState] = {}

    def start_game(self, rows: int = 8, cols: int = 8, mine_count: int = 10) -> MinesweeperState:
        """Create a new Minesweeper game board."""
        game_id = str(uuid.uuid4())[:8]
        board = [
            [MinesweeperCell(row=r, col=c) for c in range(cols)]
            for r in range(rows)
        ]

        # Place mines randomly
        all_positions = [(r, c) for r in range(rows) for c in range(cols)]
        mine_positions = set(random.sample(all_positions, min(mine_count, len(all_positions))))

        for r, c in mine_positions:
            board[r][c].is_mine = True

        # Calculate neighbor mine counts
        for r in range(rows):
            for c in range(cols):
                if not board[r][c].is_mine:
                    count = 0
                    for dr in (-1, 0, 1):
                        for dc in (-1, 0, 1):
                            if dr == 0 and dc == 0:
                                continue
                            nr, nc = r + dr, c + dc
                            if 0 <= nr < rows and 0 <= nc < cols and board[nr][nc].is_mine:
                                count += 1
                    board[r][c].neighbor_mines = count

        state = MinesweeperState(
            game_id=game_id,
            rows=rows,
            cols=cols,
            mine_count=len(mine_positions),
            board=board,
            status=GameResultStatus.ONGOING,
        )
        self._active_games[game_id] = state
        return state

    def reveal_cell(self, game_id: str, row: int, col: int) -> MinesweeperState | None:
        """Reveal cell at (row, col) and expand zero-neighbor cells."""
        state = self._active_games.get(game_id)
        if not state or state.status != GameResultStatus.ONGOING:
            return state

        if not (0 <= row < state.rows and 0 <= col < state.cols):
            return state

        target = state.board[row][col]
        if target.is_flagged or target.is_revealed:
            return state

        if target.is_mine:
            target.is_revealed = True
            state.status = GameResultStatus.LOSE
            return state

        # BFS flood fill for zero neighbors
        queue = [(row, col)]
        while queue:
            curr_r, curr_c = queue.pop(0)
            cell = state.board[curr_r][curr_c]

            if cell.is_revealed or cell.is_flagged:
                continue

            cell.is_revealed = True
            state.revealed_count += 1

            if cell.neighbor_mines == 0:
                for dr in (-1, 0, 1):
                    for dc in (-1, 0, 1):
                        if dr == 0 and dc == 0:
                            continue
                        nr, nc = curr_r + dr, curr_c + dc
                        if 0 <= nr < state.rows and 0 <= nc < state.cols:
                            neighbor = state.board[nr][nc]
                            if not neighbor.is_revealed and not neighbor.is_mine and not neighbor.is_flagged:
                                queue.append((nr, nc))

        # Check win condition (all safe cells revealed)
        safe_cells_total = (state.rows * state.cols) - state.mine_count
        if state.revealed_count >= safe_cells_total:
            state.status = GameResultStatus.WIN

        return state

    def toggle_flag(self, game_id: str, row: int, col: int) -> MinesweeperState | None:
        """Toggle flag marker on a cell."""
        state = self._active_games.get(game_id)
        if not state or state.status != GameResultStatus.ONGOING:
            return state

        if 0 <= row < state.rows and 0 <= col < state.cols:
            cell = state.board[row][col]
            if not cell.is_revealed:
                cell.is_flagged = not cell.is_flagged

        return state

    def get_game(self, game_id: str) -> MinesweeperState | None:
        """Get active game state."""
        return self._active_games.get(game_id)
