"""
tests.unit.test_module_registry
================================
Unit tests for Sherry dynamic optional module discovery and metadata.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from mod.deep_search import MOD_INFO as DEEP_SEARCH_INFO
from mod.feed_sentinel import MOD_INFO as FEED_SENTINEL_INFO
from mod.game_center import MOD_INFO as GAME_CENTER_INFO
from mod.livestream import MOD_INFO as LIVESTREAM_INFO
from handlers.spark import MOD_INFO as SPARK_INFO
from mod.stock_trader import MOD_INFO as STOCK_TRADER_INFO
from mod.video_clipper import MOD_INFO as VIDEO_CLIPPER_INFO
from web_ui.server import _EXCLUDED_MOD_DIRS, app


def test_mod_info_exports() -> None:
    """Test that optional modules export rich and valid MOD_INFO dictionaries."""
    # spark
    assert SPARK_INFO["id"] == "spark"
    assert "📡" in SPARK_INFO["name"] or "Spark" in SPARK_INFO["name"]
    assert SPARK_INFO.get("has_web_ui") is True
    assert SPARK_INFO.get("category") == "工具 / Tools"

    # feed_sentinel
    assert FEED_SENTINEL_INFO["id"] == "feed_sentinel"
    assert "📡" in FEED_SENTINEL_INFO["name"] or "哨兵" in FEED_SENTINEL_INFO["name"]
    assert "HuggingFace" in FEED_SENTINEL_INFO["description"]
    assert FEED_SENTINEL_INFO.get("category") == "情報 / Intelligence"

    # livestream
    assert LIVESTREAM_INFO["id"] == "livestream"
    assert "實況" in LIVESTREAM_INFO["name"]
    assert "YouTube" in LIVESTREAM_INFO["description"] or "大螢幕" in LIVESTREAM_INFO["description"]
    assert LIVESTREAM_INFO.get("category") == "影音 / Livestream"

    # video_clipper
    assert VIDEO_CLIPPER_INFO["id"] == "video_clipper"
    assert "GIF" in VIDEO_CLIPPER_INFO["name"] or "影片" in VIDEO_CLIPPER_INFO["name"]
    assert "/video" in VIDEO_CLIPPER_INFO["description"]
    assert VIDEO_CLIPPER_INFO.get("category") == "工具 / Tools"

    # game_center
    assert GAME_CENTER_INFO["id"] == "game_center"
    assert GAME_CENTER_INFO.get("has_web_ui") is True
    assert len(GAME_CENTER_INFO["games"]) >= 3

    # stock_trader
    assert STOCK_TRADER_INFO["id"] == "stock_trader"
    assert STOCK_TRADER_INFO.get("has_web_ui") is True

    # deep_search
    assert DEEP_SEARCH_INFO["id"] == "deep_search"
    assert DEEP_SEARCH_INFO.get("has_web_ui") is True


def test_excluded_mod_dirs() -> None:
    """Test that architectural I/O and cache directories are excluded from plugin scan."""
    assert "clients" in _EXCLUDED_MOD_DIRS
    assert "repositories" in _EXCLUDED_MOD_DIRS
    assert "__pycache__" in _EXCLUDED_MOD_DIRS


def test_get_dynamic_modules_endpoint() -> None:
    """Test the /api/modules endpoint returns valid metadata for all discovered modules dynamically."""
    async def _run() -> None:
        async with app.test_client() as test_client:
            response = await test_client.get("/api/modules")
            assert response.status_code == 200
            data = await response.get_json()
            assert "modules" in data

            modules = data["modules"]
            # Verify architectural directories are NOT in modules
            assert "clients" not in modules
            assert "repositories" not in modules

            # Verify key modules are present with valid dynamic metadata
            for mod_key in ["dnd", "game_center", "stock_trader", "deep_search", "feed_sentinel", "livestream", "video_clipper"]:
                assert mod_key in modules, f"Module '{mod_key}' not returned by /api/modules"
                mod_data = modules[mod_key]
                assert mod_data["description"] != "未登記的自訂模組。"
                assert len(mod_data["description"]) > 5
                assert mod_data["display_name"]
                assert mod_data["category"]
                if mod_key in ("game_center", "stock_trader", "deep_search"):
                    assert mod_data["has_web_ui"] is True
                    assert mod_data["view_url"] == f"/api/mod/{mod_key}/view"
                    assert mod_data["script_url"] is not None

            # Verify spark is promoted to core handler extension
            import main
            assert "handlers.spark" in main.EXTENSIONS

    asyncio.run(_run())


def test_discover_optional_extensions_respects_mod_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """測試 main.py 中的 _discover_optional_extensions 會正確依據 mod_config.json 過濾已停用模組。"""
    import json
    import main

    test_config_path = tmp_path / "mod_config.json"
    # 設定 feed_sentinel 停用，其餘預設啟用
    test_config_path.write_text(json.dumps({"feed_sentinel": False}), encoding="utf-8")
    monkeypatch.setattr(main, "MOD_CONFIG_PATH", test_config_path)

    exts = main._discover_optional_extensions()
    assert "mod.feed_sentinel.cog" not in exts
    assert any("mod.dnd.cog" in e for e in exts)
    assert "handlers.spark" in main.EXTENSIONS


def test_toggle_module_api_and_bot_integration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """測試 /api/modules/toggle 端點與 Discord Bot 動態載入/卸載之連動。"""
    import json
    from unittest.mock import AsyncMock, MagicMock
    import web_ui.server as server

    test_config_path = tmp_path / "mod_config.json"
    monkeypatch.setattr(server, "_MOD_CONFIG_PATH", test_config_path)
    monkeypatch.setattr(server, "_is_admin_request", lambda req: True)

    mock_bot = MagicMock()
    mock_bot.extensions = {"mod.feed_sentinel.cog": MagicMock()}
    mock_bot.unload_extension = AsyncMock()
    mock_bot.load_extension = AsyncMock()
    monkeypatch.setattr(server, "_bot_instance", mock_bot)

    async def _run() -> None:
        async with server.app.test_client() as client:
            # 1. 停用 feed_sentinel
            resp = await client.post(
                "/api/modules/toggle",
                json={"mod_key": "feed_sentinel", "enabled": False},
            )
            assert resp.status_code == 200
            data = await resp.get_json()
            assert data["status"] == "success"
            assert data["enabled"] is False
            mock_bot.unload_extension.assert_awaited_once_with("mod.feed_sentinel.cog")

            # 驗證 mod_config.json 寫入
            saved_config = json.loads(test_config_path.read_text(encoding="utf-8"))
            assert saved_config.get("feed_sentinel") is False

            # 2. 啟用 feed_sentinel
            mock_bot.extensions.clear()
            resp2 = await client.post(
                "/api/modules/toggle",
                json={"mod_key": "feed_sentinel", "enabled": True},
            )
            assert resp2.status_code == 200
            data2 = await resp2.get_json()
            assert data2["status"] == "success"
            assert data2["enabled"] is True
            mock_bot.load_extension.assert_awaited_once_with("mod.feed_sentinel.cog")

    asyncio.run(_run())


def test_feed_sentinel_dynamic_is_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """測試 feed_sentinel.config.is_enabled 動態判斷狀態。"""
    import json
    from mod.feed_sentinel import config as fs_config

    monkeypatch.setattr(fs_config, "PROJECT_ROOT", tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    config_file = data_dir / "mod_config.json"

    # 預設無設定檔時為 True
    assert fs_config.is_enabled() is True

    # 設為停用
    config_file.write_text(json.dumps({"feed_sentinel": False}), encoding="utf-8")
    assert fs_config.is_enabled() is False

    # 設為啟用
    config_file.write_text(json.dumps({"feed_sentinel": True}), encoding="utf-8")
    assert fs_config.is_enabled() is True

