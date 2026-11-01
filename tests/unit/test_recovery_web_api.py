"""
tests/unit/test_recovery_web_api.py
===================================
Unit tests for WebUI Recovery & Resilience Center API endpoints:
  - GET /api/recovery/status
  - GET /api/recovery/ai-tasks
  - GET /api/recovery/music
  - GET /api/system/tasks (backward compatibility)
  - Security authentication checks (401 when unauthenticated)
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import MagicMock, patch
import pytest

from handlers.social.task_registry import get_in_flight_registry
import web_ui.server as server_mod
from web_ui.server import app


@pytest.fixture(autouse=True)
def clean_in_flight_registry() -> Any:
    """Ensure in-flight task registry is clean before and after each test."""
    registry = get_in_flight_registry()
    registry.clear()
    yield
    registry.clear()


@pytest.mark.asyncio
async def test_recovery_endpoints_require_admin_auth() -> None:
    """Verify that unauthenticated requests to recovery endpoints return 401."""
    client = app.test_client()
    with patch.object(server_mod, "_is_admin_request", return_value=False):
        for endpoint in ("/api/recovery/status", "/api/recovery/ai-tasks", "/api/recovery/music"):
            resp = await client.get(endpoint)
            assert resp.status_code == 401, f"{endpoint} should return 401 for unauthenticated requests"


@pytest.mark.asyncio
async def test_recovery_status_endpoint() -> None:
    """Test /api/recovery/status returns recovery state and snapshot statistics."""
    client = app.test_client()
    with patch.object(server_mod, "_is_admin_request", return_value=True):
        resp = await client.get("/api/recovery/status")
        assert resp.status_code == 200
        data = await resp.get_json()

        assert data["status"] == "success"
        assert "enabled" in data
        assert isinstance(data["registered_providers"], list)
        assert "snapshots" in data
        snapshots = data["snapshots"]
        assert "total" in snapshots
        assert "active" in snapshots
        assert "quarantined" in snapshots
        assert "db_path" in snapshots


@pytest.mark.asyncio
async def test_recovery_ai_tasks_empty() -> None:
    """Test /api/recovery/ai-tasks when no in-flight tasks exist."""
    client = app.test_client()
    with patch.object(server_mod, "_is_admin_request", return_value=True):
        resp = await client.get("/api/recovery/ai-tasks")
        assert resp.status_code == 200
        data = await resp.get_json()

        assert data["tasks"] == []
        assert data["total"] == 0


@pytest.mark.asyncio
async def test_recovery_ai_tasks_with_active_tasks() -> None:
    """Test /api/recovery/ai-tasks when tasks are active in registry."""
    registry = get_in_flight_registry()
    registry.register(
        task_id="ai-test-123",
        channel_id=987654321,
        message_id=1122334455,
        prompt="請分析這道謎題",
        user_id=556677,
        task_type="chat",
        origin_message_id=99887766,
    )

    client = app.test_client()
    with patch.object(server_mod, "_is_admin_request", return_value=True):
        resp = await client.get("/api/recovery/ai-tasks")
        assert resp.status_code == 200
        data = await resp.get_json()

        assert data["total"] == 1
        assert len(data["tasks"]) == 1
        item = data["tasks"][0]
        assert item["task_id"] == "ai-test-123"
        assert item["channel_id"] == "987654321"
        assert item["message_id"] == "1122334455"
        assert item["prompt"] == "請分析這道謎題"
        assert item["user_id"] == "556677"
        assert item["task_type"] == "chat"
        assert item["origin_message_id"] == "99887766"
        assert item["elapsed_seconds"] >= 0.0


@pytest.mark.asyncio
async def test_recovery_music_no_bot() -> None:
    """Test /api/recovery/music returns empty when bot is not initialized."""
    with patch.object(server_mod, "_bot_instance", None), \
         patch.object(server_mod, "_is_admin_request", return_value=True):
        client = app.test_client()
        resp = await client.get("/api/recovery/music")
        assert resp.status_code == 200
        data = await resp.get_json()
        assert data["guilds"] == []
        assert data["total"] == 0


@pytest.mark.asyncio
async def test_recovery_music_with_active_player() -> None:
    """Test /api/recovery/music returns guild player details, timeline, and queue."""
    mock_bot = MagicMock()
    mock_cog = MagicMock()

    guild_id = 999888777
    mock_cog.queues = {
        guild_id: [
            {"title": "Next Song", "duration": 180, "requester": "Tester", "webpage_url": "https://yt.com/2"}
        ]
    }
    mock_cog.current_song = {
        guild_id: {
            "title": "Now Playing Song",
            "duration": 240,
            "requester": "Alice",
            "webpage_url": "https://yt.com/1",
            "thumbnail": "https://img.com/thumb.jpg",
        }
    }
    mock_cog.is_paused = {guild_id: False}
    mock_cog.loop_modes = {guild_id: "all"}
    mock_cog.current_message = {}
    mock_cog.get_elapsed_time.return_value = 60

    # Guild and voice client
    mock_guild = MagicMock()
    mock_guild.id = guild_id
    mock_guild.name = "Music Paradise"

    mock_vc = MagicMock()
    mock_vc_channel = MagicMock()
    mock_vc_channel.id = 111222
    mock_vc_channel.name = "General Voice"
    mock_vc.channel = mock_vc_channel
    mock_vc.is_playing.return_value = True
    mock_vc.guild = mock_guild
    mock_guild.voice_client = mock_vc

    mock_bot.get_cog.side_effect = lambda name: mock_cog if name == "MusicHandler" else None
    mock_bot.get_guild.side_effect = lambda gid: mock_guild if gid == guild_id else None
    mock_bot.voice_clients = [mock_vc]

    with patch.object(server_mod, "_bot_instance", mock_bot), \
         patch.object(server_mod, "_is_admin_request", return_value=True):
        client = app.test_client()
        resp = await client.get("/api/recovery/music")
        assert resp.status_code == 200
        data = await resp.get_json()

        assert data["total"] == 1
        g = data["guilds"][0]
        assert g["guild_id"] == str(guild_id)
        assert g["guild_name"] == "Music Paradise"
        assert g["voice_channel_id"] == "111222"
        assert g["voice_channel_name"] == "General Voice"
        assert g["is_paused"] is False
        assert g["is_playing"] is True
        assert g["elapsed_seconds"] == 60
        assert g["duration_seconds"] == 240.0
        assert g["loop_mode"] == "all"
        assert g["current_song"]["title"] == "Now Playing Song"
        assert g["queue_count"] == 1
        assert g["queue"][0]["title"] == "Next Song"
        assert g["recovery_ready"] is True


@pytest.mark.asyncio
async def test_system_tasks_backward_compatibility() -> None:
    """Test legacy /api/system/tasks endpoint remains functional."""
    client = app.test_client()
    with patch.object(server_mod, "_is_admin_request", return_value=True):
        resp = await client.get("/api/system/tasks")
        assert resp.status_code == 200
        data = await resp.get_json()
        assert "tasks" in data
        assert "total" in data
        assert isinstance(data["tasks"], list)


@pytest.mark.asyncio
async def test_recovery_status_fallback_on_db_error(tmp_path: Any) -> None:
    """Test /api/recovery/status gracefully returns 0 stats when db query fails."""
    # Create an empty db file without tables
    empty_db = tmp_path / "corrupted_snapshots.db"
    empty_db.write_bytes(b"")

    mock_storage = MagicMock()
    mock_storage.db_path = empty_db
    mock_manager = MagicMock()
    mock_manager.is_enabled.return_value = True
    mock_manager.list_providers.return_value = ["ai_task"]
    mock_manager.storage = mock_storage

    with patch("core.recovery.manager.get_recovery_manager", return_value=mock_manager), \
         patch.object(server_mod, "_is_admin_request", return_value=True):
        client = app.test_client()
        resp = await client.get("/api/recovery/status")
        assert resp.status_code == 200
        data = await resp.get_json()
        assert data["status"] == "success"
        assert data["snapshots"]["total"] == 0
        assert data["snapshots"]["active"] == 0
        assert data["snapshots"]["quarantined"] == 0
        assert data["snapshots"]["last_saved_at"] is None


@pytest.mark.asyncio
async def test_recovery_music_malformed_queue_and_song() -> None:
    """Test /api/recovery/music handles non-dict song and malformed queue items without crashing."""
    mock_bot = MagicMock()
    mock_cog = MagicMock()

    guild_id = 12345678
    # Malformed queue: contains non-dict items (strings, None) and malformed durations
    mock_cog.queues = {
        guild_id: ["not-a-dict", None, {"title": "Valid Song", "duration": "invalid_number"}]
    }
    # Non-dict current song
    mock_cog.current_song = {guild_id: "not-a-dict-song"}
    mock_cog.is_paused = {guild_id: False}
    mock_cog.loop_modes = {guild_id: "none"}
    mock_cog.current_message = {}
    mock_cog.get_elapsed_time.side_effect = Exception("device busy")

    mock_guild = MagicMock()
    mock_guild.id = guild_id
    mock_guild.name = "Edge Guild"
    mock_vc = MagicMock()
    mock_vc.channel = MagicMock(id=999, name="Test VC")
    mock_vc.is_playing.return_value = False
    mock_guild.voice_client = mock_vc

    mock_bot.get_cog.side_effect = lambda name: mock_cog if name == "MusicHandler" else None
    mock_bot.get_guild.side_effect = lambda gid: mock_guild if gid == guild_id else None
    mock_bot.voice_clients = [mock_vc]

    with patch.object(server_mod, "_bot_instance", mock_bot), \
         patch.object(server_mod, "_is_admin_request", return_value=True):
        client = app.test_client()
        resp = await client.get("/api/recovery/music")
        assert resp.status_code == 200
        data = await resp.get_json()
        assert data["total"] == 1
        g = data["guilds"][0]
        # Current song should be None because it wasn't a dict
        assert g["current_song"] is None
        # Elapsed should fall back to 0
        assert g["elapsed_seconds"] == 0
        # Malformed queue items filtered out, valid one kept with fallback duration 0.0
        assert len(g["queue"]) == 1
        assert g["queue"][0]["title"] == "Valid Song"
        assert g["queue"][0]["duration"] == 0.0

