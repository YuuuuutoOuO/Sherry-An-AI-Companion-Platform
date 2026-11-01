"""Unit tests verifying the Proactive Care WebUI refactoring, sub-tabs, and test channel status / selector enhancement."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from quart import Quart


@pytest.fixture
def repo_root():
    return Path(__file__).resolve().parent.parent.parent


@pytest.fixture
def app():
    app = Quart(__name__)
    app.config["TESTING"] = True
    from handlers.care.web import care_bp
    app.register_blueprint(care_bp)
    return app


@pytest.mark.asyncio
async def test_care_surfer_config_get_with_mock_bot(app, monkeypatch):
    from handlers.care import web
    from handlers.care.repository import CareSurferConfig

    # Mock engine & repository
    mock_engine = MagicMock()
    mock_surfer_cfg = CareSurferConfig(
        sandbox_channel_id="999888777",
        enabled=True,
        interval_minutes=60,
        tag_mode="interest_match",
    )
    mock_engine.repository.get_surfer_config.return_value = mock_surfer_cfg
    web.set_engine(mock_engine)

    # Mock Discord Bot
    mock_bot = MagicMock()

    # Channel 1: Target channel (valid permissions)
    mock_target_ch = MagicMock()
    mock_target_ch.id = 999888777
    mock_target_ch.name = "雪莉測試房"
    mock_guild = MagicMock()
    mock_guild.id = 111222333
    mock_guild.name = "雪莉秘密基地"
    mock_target_ch.guild = mock_guild

    mock_perms = MagicMock()
    mock_perms.send_messages = True
    mock_perms.view_channel = True
    mock_target_ch.permissions_for.return_value = mock_perms

    # Guild 1 with text channels
    mock_guild.text_channels = [mock_target_ch]
    mock_guild.me = MagicMock()

    mock_bot.guilds = [mock_guild]
    mock_bot.get_channel.side_effect = lambda cid: mock_target_ch if cid == 999888777 else None

    web.set_bot_instance(mock_bot)

    client = app.test_client()
    resp = await client.get("/api/care/surfer/config")
    assert resp.status_code == 200
    data = await resp.get_json()

    assert data["status"] == "success"
    assert "channel_info" in data
    assert "available_channels" in data
    assert "env_channel_id" in data

    ch_info = data["channel_info"]
    assert ch_info["id"] == "999888777"
    assert ch_info["name"] == "雪莉測試房"
    assert ch_info["guild_name"] == "雪莉秘密基地"
    assert ch_info["can_send"] is True
    assert ch_info["status"] == "valid"

    avail = data["available_channels"]
    assert len(avail) == 1
    assert avail[0]["guild_name"] == "雪莉秘密基地"
    assert len(avail[0]["channels"]) == 1
    assert avail[0]["channels"][0]["id"] == "999888777"
    assert avail[0]["channels"][0]["name"] == "雪莉測試房"


@pytest.mark.asyncio
async def test_care_surfer_config_get_bot_offline_graceful_fallback(app):
    from handlers.care import web
    from handlers.care.repository import CareSurferConfig

    mock_engine = MagicMock()
    mock_surfer_cfg = CareSurferConfig(
        sandbox_channel_id="0",
        enabled=False,
    )
    mock_engine.repository.get_surfer_config.return_value = mock_surfer_cfg
    web.set_engine(mock_engine)
    web.set_bot_instance(None)

    client = app.test_client()
    resp = await client.get("/api/care/surfer/config")
    assert resp.status_code == 200
    data = await resp.get_json()

    assert data["status"] == "success"
    assert data["channel_info"]["status"] == "unset"
    assert data["available_channels"] == []


def test_care_template_and_js_elements(repo_root):
    html_path = repo_root / "web_ui" / "templates" / "views" / "care.html"
    assert html_path.exists(), "care.html must exist"
    html = html_path.read_text(encoding="utf-8")

    # Verify Sub-tabs
    assert "care-tab-btn-overview" in html
    assert "care-tab-btn-surfer" in html
    assert "care-tab-btn-self" in html

    # Verify Panels
    assert "care-panel-overview" in html
    assert "care-panel-surfer" in html
    assert "care-panel-self" in html

    # Verify Channel Hero Card & Select elements
    assert "care-channel-status-pill" in html
    assert "care-channel-name-val" in html
    assert "care-guild-name-val" in html
    assert "care-surfer-channel-select" in html
    assert "care-surfer-channel" in html

    # Verify JavaScript
    js_path = repo_root / "handlers" / "care" / "static" / "care.js"
    assert js_path.exists(), "care.js must exist"
    js = js_path.read_text(encoding="utf-8")

    assert "switchTab" in js
    assert "renderChannelStatusCard" in js
    assert "care-surfer-channel-select" in js
    assert "localStorage.setItem('sherry_care_tab'" in js

    # Verify index.html cache busting
    idx_path = repo_root / "web_ui" / "templates" / "index.html"
    assert idx_path.exists()
    idx = idx_path.read_text(encoding="utf-8")
    assert "care.js?v=" in idx


@pytest.mark.asyncio
async def test_care_web_cyber_friend_config_and_templates(app, repo_root):
    from handlers.care import web
    from handlers.care.repository import CareSurferConfig

    # Mock engine
    mock_engine = MagicMock()
    updated_cfg = CareSurferConfig(
        sandbox_channel_id="123456789",
        behavior_mode="care_poke",
        chat_reply_enabled=False,
        interject_threshold=5,
        interject_score_threshold=8,
        active_chat_window_seconds=180,
        multi_message_enabled=True,
        emoji_reaction_enabled=True,
        include_url_mode="never",
    )
    mock_engine.repository.update_surfer_config.return_value = updated_cfg
    web.set_engine(mock_engine)

    client = app.test_client()

    # Test POST /api/care/surfer/config
    payload = {
        "sandbox_channel_id": "123456789",
        "behavior_mode": "care_poke",
        "chat_reply_enabled": False,
        "interject_threshold": 5,
        "interject_score_threshold": 8,
        "active_chat_window_seconds": 180,
        "multi_message_enabled": True,
        "emoji_reaction_enabled": True,
        "include_url_mode": "never",
    }
    res = await client.post("/api/care/surfer/config", json=payload)
    assert res.status_code == 200
    data = await res.get_json()
    assert data["status"] == "success"
    assert data["config"]["behavior_mode"] == "care_poke"
    assert data["config"]["chat_reply_enabled"] is False
    assert data["config"]["interject_threshold"] == 5

    # Test POST /api/care/surfer/trigger
    trig_res = await client.post(
        "/api/care/surfer/trigger",
        json={"mode": "daily_vibe", "dry_run": True},
    )
    assert trig_res.status_code == 200
    trig_data = await trig_res.get_json()
    assert trig_data["status"] == "success"
    assert "daily_vibe" in trig_data["message"]

    # Verify care.html contains new UI controls
    html_path = repo_root / "web_ui" / "templates" / "views" / "care.html"
    assert html_path.exists()
    html = html_path.read_text(encoding="utf-8")
    assert "care-chat-window-pill" in html
    assert "care-cyber-rice-pill" in html
    assert "care-trigger-auto" in html
    assert "care-trigger-lurk" in html
    assert "care-trigger-vibe" in html
    assert "care-trigger-poke" in html
    assert "care-trigger-gossip" in html
    assert "care-surfer-behavior-mode" in html
    assert "care-surfer-chat-reply" in html
    assert "care-surfer-interject-threshold" in html
    assert "care-surfer-interject-score" in html
    assert "care-surfer-active-window" in html
    assert "care-surfer-multi-msg" in html
    assert "care-surfer-emoji" in html
    assert "care-surfer-url-mode" in html

    # Verify care.js contains cyber friend handlers
    js_path = repo_root / "handlers" / "care" / "static" / "care.js"
    assert js_path.exists()
    js = js_path.read_text(encoding="utf-8")
    assert "triggerSurferMode" in js
    assert "care-chat-window-pill" in js
    assert "care-cyber-rice-pill" in js
    assert "care-surfer-behavior-mode" in js

    # Verify surfer_live.html contains live stream elements
    live_path = repo_root / "handlers" / "care" / "templates" / "surfer_live.html"
    assert live_path.exists()
    live_html = live_path.read_text(encoding="utf-8")
    assert "live-mode-select" in live_html
    assert "live-rice-stats" in live_html
    assert "discord-bubbles-box" in live_html
    assert "discord-reaction-badge" in live_html
    assert "renderBubbles" in live_html
