"""
tests.unit.test_game_center
============================
Unit tests for Sherry Game Center (.mod).
"""

from __future__ import annotations

import pytest

from mod.game_center.blackjack import BlackjackEngine
from mod.game_center.minesweeper import MinesweeperEngine
from mod.game_center.models import GameResultStatus, RPSChoice
from mod.game_center.rock_paper_scissors import RockPaperScissorsEngine


def test_rock_paper_scissors_rules() -> None:
    """Test Rock Paper Scissors engine outcome logic."""
    res = RockPaperScissorsEngine.play_round("rock")
    assert "player_choice" in res
    assert res["player_choice"] == RPSChoice.ROCK.value
    assert res["sherry_choice"] in [c.value for c in RPSChoice]
    assert res["outcome"] in [s.value for s in GameResultStatus]


def test_blackjack_engine_flow() -> None:
    """Test Blackjack card dealing, hitting, and standing."""
    engine = BlackjackEngine()
    state = engine.start_game()

    assert state.game_id is not None
    assert len(state.player_cards) == 2
    assert len(state.dealer_cards) == 2
    assert state.player_score > 0

    if state.status == GameResultStatus.ONGOING:
        updated = engine.hit(state.game_id)
        assert updated is not None
        assert len(updated.player_cards) == 3

        stood = engine.stand(state.game_id)
        assert stood is not None
        assert stood.status != GameResultStatus.ONGOING


def test_minesweeper_engine_flow() -> None:
    """Test Minesweeper board generation and reveal/flag interactions."""
    engine = MinesweeperEngine()
    state = engine.start_game(rows=6, cols=6, mine_count=5)

    assert state.game_id is not None
    assert state.rows == 6
    assert state.cols == 6
    assert state.mine_count == 5

    # Toggle flag test
    state_flagged = engine.toggle_flag(state.game_id, 0, 0)
    assert state_flagged is not None
    assert state_flagged.board[0][0].is_flagged is True

    # Untoggle flag
    engine.toggle_flag(state.game_id, 0, 0)
    assert state_flagged.board[0][0].is_flagged is False

    # Reveal a safe non-mine cell
    safe_r, safe_c = None, None
    for r in range(6):
        for c in range(6):
            if not state.board[r][c].is_mine:
                safe_r, safe_c = r, c
                break
        if safe_r is not None:
            break

    assert safe_r is not None and safe_c is not None
    revealed_state = engine.reveal_cell(state.game_id, safe_r, safe_c)
    assert revealed_state is not None
    assert revealed_state.board[safe_r][safe_c].is_revealed is True
