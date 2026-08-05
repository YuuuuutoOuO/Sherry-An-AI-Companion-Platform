"""
tests.unit.test_game_center
============================
Unit tests for Sherry Game Center (.mod).
"""

from __future__ import annotations

import pytest

from mod.game_center.akinator_20q import AkinatorEngine
from mod.game_center.blackjack import BlackjackEngine
from mod.game_center.escape_room import EscapeRoomEngine
from mod.game_center.minesweeper import MinesweeperEngine
from mod.game_center.models import GameResultStatus, RPSChoice
from mod.game_center.rock_paper_scissors import RockPaperScissorsEngine
from mod.game_center.tic_tac_toe import TicTacToeEngine
from mod.game_center.turtle_soup import TurtleSoupEngine


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


def test_new_games_flow() -> None:
    """Test Turtle Soup, Akinator, Escape Room, and Tic-Tac-Toe engines."""
    # Turtle Soup
    ts_engine = TurtleSoupEngine()
    ts_state = ts_engine.start_game(0)
    assert ts_state.game_id is not None
    assert ts_state.title == "海邊的餐館"

    # Akinator
    aki_engine = AkinatorEngine()
    aki_state = aki_engine.start_game()
    assert aki_state.question_count == 1
    recorded = aki_engine.record_answer(aki_state.game_id, "是")
    assert recorded is not None and recorded.question_count == 2

    # Escape Room
    er_engine = EscapeRoomEngine()
    er_state = er_engine.start_game()
    assert er_state.game_id is not None

    # Tic-Tac-Toe
    ttt_engine = TicTacToeEngine()
    ttt_state = ttt_engine.start_game()
    assert len(ttt_state.board) == 9
    moved = ttt_engine.player_move(ttt_state.game_id, 0)
    assert moved is not None
    assert moved.board[0] == "X"
