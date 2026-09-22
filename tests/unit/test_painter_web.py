"""
tests/unit/test_painter_web.py
==============================
Unit tests for mod.painter.web Blueprint and dynamic plugin routing.
"""

from __future__ import annotations

from typing import Any
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import pytest
from quart import Quart

from mod.painter import MOD_INFO, config
from mod.painter.web import painter_bp, set_bot_instance


def test_mod_info_declaration() -> None:
    """Ensure MOD_INFO contains all required metadata for dynamic discovery."""
    assert MOD_INFO["id"] == "painter"
    assert MOD_INFO["has_web_ui"] is True
    assert MOD_INFO["nav_id"] == "painter"
    assert MOD_INFO["script_file"] == "painter.js"
    assert "icon" in MOD_INFO


def test_painter_blueprint_routes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test web blueprint endpoints: /view, /paintings, /status, /draw."""
    import asyncio

    async def _run() -> None:
        test_painting_dir = tmp_path / "painting"
        monkeypatch.setattr(config, "PAINTING_DIR", test_painting_dir)

        app = Quart(__name__)
        app.register_blueprint(painter_bp)
        client = app.test_client()

        # 1. Test /view
        resp_view = await client.get("/view")
        assert resp_view.status_code == 200
        text = await resp_view.get_data(as_text=True)
        assert "view-painter" in text

        # 2. Test /status
        resp_status = await client.get("/status")
        assert resp_status.status_code == 200
        data_status = await resp_status.get_json()
        assert "is_drawing" in data_status

        # 3. Test /paintings (empty)
        resp_list = await client.get("/paintings")
        assert resp_list.status_code == 200
        data_list = await resp_list.get_json()
        assert "paintings" in data_list
        assert data_list["total"] == 0

        # 4. Test /draw with mock bot
        bot = MagicMock()
        brain = MagicMock()

        async def fake_enqueue(task: Any) -> None:
            raw = """```json
            [{"action": "set_background", "params": {"color": "#FFFFFF"}}, {"action": "stamp", "params": {"text": "雪莉"}}]
            ```"""
            await task.callback(raw)

        brain.enqueue_task = AsyncMock(side_effect=fake_enqueue)
        bot.get_cog = MagicMock(side_effect=lambda name: brain if name == "BotBrain" else None)
        set_bot_instance(bot)

        resp_draw = await client.post("/draw", json={"prompt": "水墨小貓", "style": "ink_wash", "refine": True})
        assert resp_draw.status_code == 200
        draw_res = await resp_draw.get_json()
        assert draw_res["success"] is True
        assert draw_res["filename"].endswith(".jpg")
        assert draw_res["refined"] is True

        # 5. Test /paintings after draw
        resp_list2 = await client.get("/paintings")
        data_list2 = await resp_list2.get_json()
        assert data_list2["total"] == 1
        assert data_list2["paintings"][0]["prompt"] == "水墨小貓"

        # 6. Test file download
        filename = draw_res["filename"]
        resp_file = await client.get(f"/paintings/{filename}")
        assert resp_file.status_code == 200

    asyncio.run(_run())
