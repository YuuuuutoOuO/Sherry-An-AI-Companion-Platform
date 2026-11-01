"""
mod.game_center.web
===================
Quart Blueprint and Web routes for Sherry Game Center Mod.
Provides endpoints for Rock-Paper-Scissors, Blackjack, Minesweeper,
Turtle Soup, Akinator 20Q, Escape Room, and Tic-Tac-Toe.
"""

from __future__ import annotations

import ipaddress
import logging
from pathlib import Path
import time
from typing import Any

from quart import Blueprint, Response, jsonify, request
from core.error_report import safe_error_payload

import settings

logger = logging.getLogger(__name__)


def _error_response(exc: BaseException) -> tuple[Response, int]:
    """複審 1：例外細節只留在日誌，對外回應僅附 error_id。"""
    payload = safe_error_payload("mod.game_center.web", exc)
    return jsonify({"error": payload["message"], "error_id": payload["error_id"]}), 500

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_STATIC_DIR = Path(__file__).resolve().parent / "static"

game_center_bp = Blueprint(
    "game_center",
    __name__,
    template_folder=str(_TEMPLATE_DIR),
    static_folder=str(_STATIC_DIR),
)

_IP_RATE_LIMITS: dict[str, list[float]] = {}
_MAX_POST_CALLS_PER_MIN = 30
_RATE_LIMIT_WINDOW_SEC = 60.0
# R3（報告 §5.3.8）：追蹤表硬上限，避免偽造來源灌爆記憶體。
_MAX_TRACKED_IPS = 2000


def _resolve_client_ip(remote_addr: str, forwarded_for: str | None) -> str:
    """R3（報告 §5.3.3）：僅信任受控代理（WEB_UI_TRUSTED_PROXIES）並取 XFF 最右合法值。

    與 web_ui.server._get_client_ip 語意同步：非受控來源一律忽略 XFF；
    受控代理連線時取最右值（最後一跳受控代理所見之對端，左側值可被客戶端偽造）。
    """
    remote = str(remote_addr or "127.0.0.1")
    configured = getattr(settings, "WEB_UI_TRUSTED_PROXIES", None) or [
        "127.0.0.1", "::1", "localhost", "testclient"
    ]
    if remote not in set(configured):
        return remote
    if not forwarded_for:
        return remote
    for candidate in reversed([p.strip() for p in str(forwarded_for).split(",") if p.strip()]):
        try:
            ipaddress.ip_address(candidate)
            return candidate
        except ValueError:
            continue
    return remote


def _check_ip_rate_limit(ip: str) -> bool:
    """檢查客戶端 IP 是否在滑動窗口內超出頻率限制 (30 次 / 分鐘)。"""
    now = time.monotonic()
    history = _IP_RATE_LIMITS.get(ip, [])
    history = [t for t in history if now - t < _RATE_LIMIT_WINDOW_SEC]
    if not history:
        _IP_RATE_LIMITS.pop(ip, None)
    else:
        _IP_RATE_LIMITS[ip] = history

    if len(history) >= _MAX_POST_CALLS_PER_MIN:
        return False

    if ip not in _IP_RATE_LIMITS:
        _IP_RATE_LIMITS[ip] = [now]
    else:
        _IP_RATE_LIMITS[ip].append(now)

    # R3：超出上限時淘汰最久未活動的 IP（近似 LRU）
    if len(_IP_RATE_LIMITS) > _MAX_TRACKED_IPS:
        oldest_ip = min(
            _IP_RATE_LIMITS,
            key=lambda k: _IP_RATE_LIMITS[k][-1] if _IP_RATE_LIMITS[k] else 0.0,
        )
        _IP_RATE_LIMITS.pop(oldest_ip, None)
    return True


@game_center_bp.before_request
async def _limit_game_requests() -> Response | tuple[Response, int] | None:
    """針對所有 POST 遊戲請求進行客戶端 IP 限流保護，防止濫用。"""
    if request.method == "POST":
        client_ip = _resolve_client_ip(
            request.remote_addr or "127.0.0.1",
            request.headers.get("X-Forwarded-For"),
        )
        if not _check_ip_rate_limit(client_ip):
            logger.warning("[game_center] 客戶端 IP %s 觸發遊戲操作頻率限制", client_ip)
            return jsonify({"error": "請求過於頻繁，請稍後再試", "status": 429}), 429
    return None


_blackjack_engine: Any = None
_minesweeper_engine: Any = None
_turtle_soup_engine: Any = None
_akinator_engine: Any = None
_escape_room_engine: Any = None
_ttt_engine: Any = None


def _get_blackjack_engine() -> Any:
    global _blackjack_engine
    if _blackjack_engine is None:
        from mod.game_center.blackjack import BlackjackEngine
        _blackjack_engine = BlackjackEngine()
    return _blackjack_engine


def _get_minesweeper_engine() -> Any:
    global _minesweeper_engine
    if _minesweeper_engine is None:
        from mod.game_center.minesweeper import MinesweeperEngine
        _minesweeper_engine = MinesweeperEngine()
    return _minesweeper_engine


@game_center_bp.route("/view", methods=["GET"])
async def get_view() -> Response | tuple[Response, int] | Any:
    """Return the HTML snippet for Game Center view."""
    html_file = _TEMPLATE_DIR / "games.html"
    if html_file.exists():
        content = html_file.read_text(encoding="utf-8")
        return Response(content, mimetype="text/html")
    return Response("<div class='view hidden' id='view-games'><h3>遊戲遊樂場視圖遺失</h3></div>", mimetype="text/html")


# ── Rock Paper Scissors ───────────────────────────────────

@game_center_bp.route("/api/games/rps/play", methods=["POST"])
@game_center_bp.route("/rps/play", methods=["POST"])
async def play_rock_paper_scissors() -> Response | tuple[Response, int] | Any:
    """Play Rock Paper Scissors against Sherry with LLM persona commentary."""
    try:
        data = await request.get_json() or {}
        player_choice = data.get("choice", "rock")

        from mod.game_center.rock_paper_scissors import RockPaperScissorsEngine
        from mod.game_center.sherry_ai import SherryGameAIService

        res = RockPaperScissorsEngine.play_round(player_choice)
        ai_service = SherryGameAIService()

        dialogue = await ai_service.generate_rps_dialogue(
            player_choice=res["player_choice"],
            sherry_choice=res["sherry_choice"],
            outcome=res["outcome"],
        )
        res["sherry_dialogue"] = dialogue
        return jsonify(res)
    except Exception as exc:
        return _error_response(exc)


# ── Blackjack ──────────────────────────────────────────────

@game_center_bp.route("/api/games/blackjack/start", methods=["POST"])
@game_center_bp.route("/blackjack/start", methods=["POST"])
async def start_blackjack() -> Response | tuple[Response, int] | Any:
    """Start a new Blackjack game against Sherry."""
    try:
        engine = _get_blackjack_engine()
        state = engine.start_game()

        from mod.game_center.sherry_ai import SherryGameAIService
        ai_service = SherryGameAIService()

        thought, dialogue = await ai_service.generate_blackjack_thought_and_dialogue(
            player_score=state.player_score,
            dealer_cards_str=", ".join(f"{c.suit}{c.rank}" for c in state.dealer_cards),
            dealer_score=state.dealer_score,
            action="start",
            status=state.status.value,
        )
        state.sherry_thought = thought
        state.sherry_dialogue = dialogue
        return jsonify(state.to_dict())
    except Exception as exc:
        return _error_response(exc)


@game_center_bp.route("/api/games/blackjack/hit", methods=["POST"])
@game_center_bp.route("/blackjack/hit", methods=["POST"])
async def hit_blackjack() -> Response | tuple[Response, int] | Any:
    """Hit a card in Blackjack."""
    try:
        data = await request.get_json() or {}
        game_id = data.get("game_id", "")
        engine = _get_blackjack_engine()
        state = engine.hit(game_id)
        if not state:
            return jsonify({"error": "Game not found or finished"}), 404

        from mod.game_center.sherry_ai import SherryGameAIService
        ai_service = SherryGameAIService()

        thought, dialogue = await ai_service.generate_blackjack_thought_and_dialogue(
            player_score=state.player_score,
            dealer_cards_str=", ".join(f"{c.suit}{c.rank}" for c in state.dealer_cards),
            dealer_score=state.dealer_score,
            action="hit",
            status=state.status.value,
        )
        state.sherry_thought = thought
        state.sherry_dialogue = dialogue
        return jsonify(state.to_dict())
    except Exception as exc:
        return _error_response(exc)


@game_center_bp.route("/api/games/blackjack/stand", methods=["POST"])
@game_center_bp.route("/blackjack/stand", methods=["POST"])
async def stand_blackjack() -> Response | tuple[Response, int] | Any:
    """Stand turn in Blackjack."""
    try:
        data = await request.get_json() or {}
        game_id = data.get("game_id", "")
        engine = _get_blackjack_engine()
        state = engine.stand(game_id)
        if not state:
            return jsonify({"error": "Game not found or finished"}), 404

        from mod.game_center.sherry_ai import SherryGameAIService
        ai_service = SherryGameAIService()

        thought, dialogue = await ai_service.generate_blackjack_thought_and_dialogue(
            player_score=state.player_score,
            dealer_cards_str=", ".join(f"{c.suit}{c.rank}" for c in state.dealer_cards),
            dealer_score=state.dealer_score,
            action="stand",
            status=state.status.value,
        )
        state.sherry_thought = thought
        state.sherry_dialogue = dialogue
        return jsonify(state.to_dict())
    except Exception as exc:
        return _error_response(exc)


# ── Minesweeper ───────────────────────────────────────────

@game_center_bp.route("/api/games/minesweeper/start", methods=["POST"])
@game_center_bp.route("/minesweeper/start", methods=["POST"])
async def start_minesweeper() -> Response | tuple[Response, int] | Any:
    """Start a Minesweeper game."""
    try:
        data = await request.get_json() or {}
        rows = int(data.get("rows", 8))
        cols = int(data.get("cols", 8))
        mines = int(data.get("mines", 10))

        engine = _get_minesweeper_engine()
        state = engine.start_game(rows=rows, cols=cols, mine_count=mines)
        return jsonify(state.to_dict())
    except Exception as exc:
        return _error_response(exc)


@game_center_bp.route("/api/games/minesweeper/reveal", methods=["POST"])
@game_center_bp.route("/minesweeper/reveal", methods=["POST"])
async def reveal_minesweeper() -> Response | tuple[Response, int] | Any:
    """Reveal a Minesweeper cell."""
    try:
        data = await request.get_json() or {}
        game_id = data.get("game_id", "")
        row = int(data.get("row", 0))
        col = int(data.get("col", 0))

        engine = _get_minesweeper_engine()
        state = engine.reveal_cell(game_id, row, col)
        if not state:
            return jsonify({"error": "Game not found"}), 404

        return jsonify(state.to_dict())
    except Exception as exc:
        return _error_response(exc)


@game_center_bp.route("/api/games/minesweeper/flag", methods=["POST"])
@game_center_bp.route("/minesweeper/flag", methods=["POST"])
async def flag_minesweeper() -> Response | tuple[Response, int] | Any:
    """Toggle flag on Minesweeper cell."""
    try:
        data = await request.get_json() or {}
        game_id = data.get("game_id", "")
        row = int(data.get("row", 0))
        col = int(data.get("col", 0))

        engine = _get_minesweeper_engine()
        state = engine.toggle_flag(game_id, row, col)
        if not state:
            return jsonify({"error": "Game not found"}), 404

        return jsonify(state.to_dict())
    except Exception as exc:
        return _error_response(exc)


@game_center_bp.route("/api/games/minesweeper/hint", methods=["POST"])
@game_center_bp.route("/minesweeper/hint", methods=["POST"])
async def hint_minesweeper() -> Response | tuple[Response, int] | Any:
    """Get Sherry LLM detective hint for Minesweeper."""
    try:
        data = await request.get_json() or {}
        game_id = data.get("game_id", "")
        engine = _get_minesweeper_engine()
        state = engine.get_game(game_id)
        if not state:
            return jsonify({"error": "Game not found"}), 404

        from mod.game_center.sherry_ai import SherryGameAIService
        ai_service = SherryGameAIService()

        summary_lines = []
        for r in range(min(5, state.rows)):
            row_str = " ".join("F" if c.is_flagged else (str(c.neighbor_mines) if c.is_revealed else "?") for c in state.board[r][:5])
            summary_lines.append(row_str)
        summary = "\n".join(summary_lines)

        hint = await ai_service.generate_minesweeper_hint(
            board_summary=summary,
            revealed_count=state.revealed_count,
            mine_count=state.mine_count,
        )
        state.sherry_hint = hint
        return jsonify(state.to_dict())
    except Exception as exc:
        return _error_response(exc)


# ── Turtle Soup ───────────────────────────────────────────

@game_center_bp.route("/api/games/turtle_soup/start", methods=["POST"])
@game_center_bp.route("/turtle_soup/start", methods=["POST"])
async def start_turtle_soup() -> Response | tuple[Response, int] | Any:
    """Start Turtle Soup game."""
    try:
        from mod.game_center.turtle_soup import TurtleSoupEngine
        global _turtle_soup_engine
        if _turtle_soup_engine is None:
            _turtle_soup_engine = TurtleSoupEngine()

        state = _turtle_soup_engine.start_game()
        return jsonify(state.to_dict())
    except Exception as exc:
        return _error_response(exc)


@game_center_bp.route("/api/games/turtle_soup/ask", methods=["POST"])
@game_center_bp.route("/turtle_soup/ask", methods=["POST"])
async def ask_turtle_soup() -> Response | tuple[Response, int] | Any:
    """Ask question to Sherry Turtle Soup host."""
    try:
        data = await request.get_json() or {}
        game_id = data.get("game_id", "")
        question = data.get("question", "")

        global _turtle_soup_engine
        if not _turtle_soup_engine:
            return jsonify({"error": "Game engine not initialized"}), 404

        state = _turtle_soup_engine.get_game(game_id)
        if not state:
            return jsonify({"error": "Game not found"}), 404

        from mod.game_center.sherry_ai import SherryGameAIService
        ai_service = SherryGameAIService()

        reply = await ai_service.generate_turtle_soup_reply(
            surface_story=state.surface_story,
            truth=state.truth,
            player_question=question,
        )
        state.dialogue_history.append({"q": question, "a": reply})
        state.sherry_reply = reply
        return jsonify(state.to_dict())
    except Exception as exc:
        return _error_response(exc)


# ── Akinator 20 Questions ──────────────────────────────────

@game_center_bp.route("/api/games/akinator/start", methods=["POST"])
@game_center_bp.route("/akinator/start", methods=["POST"])
async def start_akinator() -> Response | tuple[Response, int] | Any:
    """Start Akinator 20 Questions."""
    try:
        from mod.game_center.akinator_20q import AkinatorEngine
        global _akinator_engine
        if _akinator_engine is None:
            _akinator_engine = AkinatorEngine()

        state = _akinator_engine.start_game()
        return jsonify(state.to_dict())
    except Exception as exc:
        return _error_response(exc)


@game_center_bp.route("/api/games/akinator/answer", methods=["POST"])
@game_center_bp.route("/akinator/answer", methods=["POST"])
async def answer_akinator() -> Response | tuple[Response, int] | Any:
    """Submit answer to 20 Questions."""
    try:
        data = await request.get_json() or {}
        game_id = data.get("game_id", "")
        answer = data.get("answer", "是")

        global _akinator_engine
        if not _akinator_engine:
            return jsonify({"error": "Game not initialized"}), 404

        state = _akinator_engine.record_answer(game_id, answer)
        if not state:
            return jsonify({"error": "Game not found"}), 404

        from mod.game_center.sherry_ai import SherryGameAIService
        ai_service = SherryGameAIService()

        next_q = await ai_service.generate_20q_next_question(
            history=state.history,
            q_count=state.question_count,
        )
        state.current_question = next_q
        return jsonify(state.to_dict())
    except Exception as exc:
        return _error_response(exc)


# ── Escape Room ────────────────────────────────────────────

@game_center_bp.route("/api/games/escape_room/start", methods=["POST"])
@game_center_bp.route("/escape_room/start", methods=["POST"])
async def start_escape_room() -> Response | tuple[Response, int] | Any:
    """Start Detective Escape Room."""
    try:
        from mod.game_center.escape_room import EscapeRoomEngine
        global _escape_room_engine
        if _escape_room_engine is None:
            _escape_room_engine = EscapeRoomEngine()

        state = _escape_room_engine.start_game()
        return jsonify(state.to_dict())
    except Exception as exc:
        return _error_response(exc)


@game_center_bp.route("/api/games/escape_room/action", methods=["POST"])
@game_center_bp.route("/escape_room/action", methods=["POST"])
async def action_escape_room() -> Response | tuple[Response, int] | Any:
    """Investigate target in escape room."""
    try:
        data = await request.get_json() or {}
        game_id = data.get("game_id", "")
        target = data.get("target", "書桌")

        global _escape_room_engine
        if not _escape_room_engine:
            return jsonify({"error": "Game not initialized"}), 404

        state = _escape_room_engine.get_game(game_id)
        if not state:
            return jsonify({"error": "Game not found"}), 404

        from mod.game_center.sherry_ai import SherryGameAIService
        ai_service = SherryGameAIService()

        if target not in state.inventory:
            state.inventory.append(f"從{target}獲得的線索")

        narration = await ai_service.generate_escape_room_narration(
            action=target,
            inventory=state.inventory,
        )
        state.sherry_narration = narration
        return jsonify(state.to_dict())
    except Exception as exc:
        return _error_response(exc)


# ── Tic Tac Toe ────────────────────────────────────────────

@game_center_bp.route("/api/games/ttt/start", methods=["POST"])
@game_center_bp.route("/ttt/start", methods=["POST"])
async def start_ttt() -> Response | tuple[Response, int] | Any:
    """Start Tic-Tac-Toe."""
    try:
        from mod.game_center.tic_tac_toe import TicTacToeEngine
        global _ttt_engine
        if _ttt_engine is None:
            _ttt_engine = TicTacToeEngine()

        state = _ttt_engine.start_game()
        return jsonify(state.to_dict())
    except Exception as exc:
        return _error_response(exc)


@game_center_bp.route("/api/games/ttt/move", methods=["POST"])
@game_center_bp.route("/ttt/move", methods=["POST"])
async def move_ttt() -> Response | tuple[Response, int] | Any:
    """Make move in Tic-Tac-Toe."""
    try:
        data = await request.get_json() or {}
        game_id = data.get("game_id", "")
        index = int(data.get("index", 0))

        global _ttt_engine
        if not _ttt_engine:
            return jsonify({"error": "Game not initialized"}), 404

        state = _ttt_engine.player_move(game_id, index)
        if not state:
            return jsonify({"error": "Game not found"}), 404

        from mod.game_center.sherry_ai import SherryGameAIService
        ai_service = SherryGameAIService()

        dialogue = await ai_service.generate_ttt_dialogue(state.status.value)
        state.sherry_dialogue = dialogue
        return jsonify(state.to_dict())
    except Exception as exc:
        return _error_response(exc)
