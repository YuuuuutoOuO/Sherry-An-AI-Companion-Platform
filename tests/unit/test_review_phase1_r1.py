"""
tests/unit/test_review_phase1_r1.py
===================================
複審 1（Phase 1 完成後）：P0/P1 複掃的迴歸守門。

背景：R2/A8 將計畫內端點的 `str(exc)` 全面改為 `error_id`，但將
`mod/stock_trader/web.py`、`mod/feed_sentinel/web.py`、
`mod/deep_search/web.py` 三個計畫外同類站點留待複審 1。本檔：
1. 靜態掃描 `mod/*/web.py`，禁止 JSON 回應再次拼接例外字串。
2. 對四個模組各打一個強制例外端點，確認回應不含 marker、且附 `error_id`。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

from quart import Quart

import mod.deep_search.web as deep_search_web
import mod.feed_sentinel.web as feed_sentinel_web
import mod.game_center.web as game_center_web
import mod.stock_trader.web as stock_trader_web

_MARKER = "REVIEW1-LEAK-MARKER-4f2c9a"


def _client(blueprint: object) -> object:
    app = Quart(__name__)
    app.register_blueprint(blueprint)  # type: ignore[arg-type]
    return app.test_client()


def _assert_safe(payload: dict[str, object], text: str, *, extra: tuple[str, ...] = ()) -> None:
    assert _MARKER not in text
    assert payload.get("error_id")
    for key in extra:
        assert key in payload


def test_stock_trader_web_does_not_leak_exception() -> None:
    async def _run() -> None:
        client = _client(stock_trader_web.stock_trader_bp)
        with patch(
            "mod.stock_trader.repository.StockRepository",
            side_effect=RuntimeError(_MARKER),
        ):
            resp = await client.get("/api/stock_trader/overview")  # type: ignore[attr-defined]
        text = await resp.get_data(as_text=True)
        payload = await resp.get_json()
        assert resp.status_code == 500
        _assert_safe(payload, text)

    asyncio.run(_run())


def test_feed_sentinel_web_does_not_leak_exception() -> None:
    async def _run() -> None:
        client = _client(feed_sentinel_web.feed_sentinel_bp)
        with patch.object(
            feed_sentinel_web,
            "SentinelRepository",
            side_effect=RuntimeError(_MARKER),
        ):
            resp = await client.get("/api/feed_sentinel/posts")  # type: ignore[attr-defined]
        text = await resp.get_data(as_text=True)
        payload = await resp.get_json()
        assert resp.status_code == 500
        _assert_safe(payload, text, extra=("posts", "total"))

    asyncio.run(_run())


def test_deep_search_web_does_not_leak_exception() -> None:
    async def _run() -> None:
        client = _client(deep_search_web.deep_search_bp)
        with patch(
            "mod.deep_search.repositories.session_repository.FileSessionRepository",
            side_effect=RuntimeError(_MARKER),
        ):
            resp = await client.get("/api/deep_search/sessions")  # type: ignore[attr-defined]
        text = await resp.get_data(as_text=True)
        payload = await resp.get_json()
        assert resp.status_code == 500
        _assert_safe(payload, text, extra=("sessions",))

    asyncio.run(_run())


def test_game_center_web_does_not_leak_exception() -> None:
    async def _run() -> None:
        client = _client(game_center_web.game_center_bp)
        with patch.object(
            game_center_web,
            "_get_blackjack_engine",
            side_effect=RuntimeError(_MARKER),
        ):
            resp = await client.post("/api/games/blackjack/start")  # type: ignore[attr-defined]
        text = await resp.get_data(as_text=True)
        payload = await resp.get_json()
        assert resp.status_code == 500
        _assert_safe(payload, text, extra=("error",))

    asyncio.run(_run())


def test_mod_web_routes_do_not_interpolate_exceptions() -> None:
    """靜態守門：`mod/*/web.py` 的 jsonify 行不得再出現例外字串。"""
    mod_dir = Path(__file__).resolve().parents[2] / "mod"
    offenders: list[str] = []
    for path in sorted(mod_dir.glob("*/web.py")):
        for line_no, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if "jsonify" not in line:
                continue
            if any(token in line for token in ("str(exc)", "str(e)", "{exc}")):
                offenders.append(f"{path.relative_to(mod_dir.parent)}:{line_no}")
    assert offenders == []
