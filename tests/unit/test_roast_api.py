"""
tests.unit.test_roast_api
=========================
Unit tests for the Roast Web UI APIs in web_ui.server.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from web_ui.server import app, _admin_sessions


def test_web_ui_index_template_rendering() -> None:
    """Test GET / properly renders modularized template components and views."""
    async def _run() -> None:
        async with app.test_client() as test_client:
            resp = await test_client.get("/")
            assert resp.status_code == 200
            html = await resp.get_data(as_text=True)
            # Verify core view containers are present
            assert 'id="view-dashboard"' in html
            assert 'id="view-logs"' in html
            assert 'id="view-metrics"' in html
            assert 'id="view-tasks"' in html
            assert 'id="view-chat"' in html
            assert 'id="view-settings"' in html
            assert 'id="view-services"' in html
            assert 'id="view-bot-status"' in html
            assert 'id="view-audit"' in html
            assert 'id="view-mod-manager"' in html
            assert 'id="view-roast"' in html
            assert 'id="dynamic-mod-views"' in html
            assert 'id="sidebar-dynamic-mods"' in html
            # Verify sidebar, topbar, modals
            assert 'class="sidebar"' in html
            assert 'class="topbar"' in html
            assert 'id="roast-modal-backdrop"' in html
            assert 'id="login-modal-backdrop"' in html

    asyncio.run(_run())


def test_web_ui_live2d_resource_loading_toggle() -> None:
    """Test the main Web UI omits every Live 2D resource when disabled."""
    async def _run() -> None:
        async with app.test_client() as test_client:
            with patch("web_ui.server.settings.WEB_UI_LIVE2D_ENABLED", True):
                enabled_response = await test_client.get("/")
                assert enabled_response.status_code == 200
                enabled_html = await enabled_response.get_data(as_text=True)
                assert "/static/sherry_live2d.css" in enabled_html
                assert 'id="sherry-live2d-container"' in enabled_html
                assert "/static/sherry_live2d.js" in enabled_html

            with patch("web_ui.server.settings.WEB_UI_LIVE2D_ENABLED", False):
                disabled_response = await test_client.get("/")
                assert disabled_response.status_code == 200
                disabled_html = await disabled_response.get_data(as_text=True)
                assert "sherry_live2d" not in disabled_html
                assert 'id="sherry-live2d-container"' not in disabled_html

    asyncio.run(_run())


def test_api_roast_get_config() -> None:
    """Test GET /api/roast/config returns valid configuration."""
    async def _run() -> None:
        async with app.test_client() as test_client:
            resp = await test_client.get("/api/roast/config")
            assert resp.status_code == 200
            data = await resp.get_json()
            assert "enabled" in data
            assert "global_cooldown_seconds" in data
            assert "rules" in data
            assert isinstance(data["rules"], list)

    asyncio.run(_run())


def test_api_roast_crud_flow() -> None:
    """Test full CRUD cycle via Web UI REST API."""
    import uuid
    unique_kw = f"測試關鍵字_{uuid.uuid4().hex[:6]}"
    unique_guide = f"測試導引內容_{uuid.uuid4().hex[:6]}"

    async def _run() -> None:
        test_token = "mock_admin_token_for_tests"
        _admin_sessions[test_token] = 9999999999.0  # Far in future

        try:
            async with app.test_client() as test_client:
                headers = {"Authorization": f"Bearer {test_token}"}

                # 1. Add rule
                add_resp = await test_client.post(
                    "/api/roast/rules",
                    headers=headers,
                    json={
                        "keyword": unique_kw,
                        "reply_guide": unique_guide,
                        "probability": 85,
                        "match_mode": "contains",
                        "enabled": True,
                    },
                )
                assert add_resp.status_code == 200
                add_data = await add_resp.get_json()
                assert add_data["status"] == "success"
                rule_id = add_data["rule"]["id"]
                assert add_data["rule"]["keyword"] == unique_kw
                assert add_data["rule"]["probability"] == 85

                # 2. Update rule
                update_resp = await test_client.put(
                    f"/api/roast/rules/{rule_id}",
                    headers=headers,
                    json={
                        "probability": 60,
                        "reply_guide": f"更新後的{unique_guide}",
                    },
                )
                assert update_resp.status_code == 200
                update_data = await update_resp.get_json()
                assert update_data["rule"]["probability"] == 60
                assert update_data["rule"]["reply_guide"] == f"更新後的{unique_guide}"

                # 3. Test roast preview
                test_preview_resp = await test_client.post(
                    "/api/roast/test",
                    headers=headers,
                    json={
                        "message": f"這是一段包含{unique_kw}的發言",
                        "force": True,
                    },
                )
                assert test_preview_resp.status_code == 200
                preview_data = await test_preview_resp.get_json()
                assert preview_data["matched"] is True
                assert preview_data["triggered"] is True
                assert preview_data["rule"]["id"] == rule_id

                # 4. Delete rule
                del_resp = await test_client.delete(f"/api/roast/rules/{rule_id}", headers=headers)
                assert del_resp.status_code == 200
                del_data = await del_resp.get_json()
                assert del_data["status"] == "success"
        finally:
            _admin_sessions.pop(test_token, None)

    asyncio.run(_run())
