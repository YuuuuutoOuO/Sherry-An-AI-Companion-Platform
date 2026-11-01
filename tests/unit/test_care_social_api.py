"""tests/unit/test_care_social_api.py
===================================
賽博群友 Social Core WebUI API 測試（方向 C）。

覆蓋：
1. `/api/care/social/overview` 回傳模式、persona、決策與統計。
2. `/api/care/social/decisions` 白名單驗證與統計。
3. `/api/care/social/modes`：沙盒總開關關閉時必須 409，開啟後才可寫入。
4. `/api/care/social/personas`：路徑穿越必須 400，合法 NPC 可新增／停用。
5. `/api/care/social/kill`：一鍵降回 SOLO 並停用 NPC。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from quart import Quart

import settings
from handlers.care.repository import CareRepository


@pytest.fixture
def care_repo(tmp_path: Path) -> CareRepository:
    return CareRepository(db_path=tmp_path / "care_social_api.db")


@pytest.fixture
def app(care_repo: CareRepository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from handlers.care import web

    engine = MagicMock()
    engine.repository = care_repo
    web.set_engine(engine)
    web.set_bot_instance(None)
    monkeypatch.setattr(web, "get_bot_instance", lambda: None)

    soul_dir = tmp_path / "soul"
    (soul_dir / "sandbox").mkdir(parents=True, exist_ok=True)
    (soul_dir / "sandbox" / "npc_a.md").write_text("你是 NPC A。", encoding="utf-8")
    (soul_dir / "sandbox" / "README.md").write_text("說明文件", encoding="utf-8")
    monkeypatch.setattr(settings, "SOUL_DIR", soul_dir)

    application = Quart(__name__)
    application.config["TESTING"] = True
    application.register_blueprint(web.care_bp)
    return application


@pytest.mark.asyncio
async def test_social_overview_returns_ledger_summary(app, care_repo: CareRepository):
    care_repo.update_surfer_config(enabled=True, sandbox_channel_id="123456")
    care_repo.record_decision(
        channel_id="123456",
        mode="SOLO",
        trigger_type="mind_pulse",
        action="SILENT",
        reasons=["generation_failed"],
    )

    client = app.test_client()
    resp = await client.get("/api/care/social/overview?hours=24&limit=10")
    assert resp.status_code == 200
    data = await resp.get_json()
    assert data["status"] == "success"
    assert "sandbox_enabled" in data
    assert data["limits"]["max_npcs"] >= 1
    assert len(data["decisions"]) == 1
    assert data["stats"]["SILENT"]["count"] == 1


@pytest.mark.asyncio
async def test_social_decisions_validates_action(app, care_repo: CareRepository):
    client = app.test_client()

    bad = await client.get("/api/care/social/decisions?action=SHOUT")
    assert bad.status_code == 400
    assert (await bad.get_json())["status"] == "error"

    care_repo.record_decision(
        channel_id="123456",
        mode="SOLO",
        trigger_type="surf",
        action="SPEAK",
        reasons=[],
    )
    ok = await client.get("/api/care/social/decisions?action=SPEAK")
    assert ok.status_code == 200
    payload = await ok.get_json()
    assert payload["status"] == "success"
    assert all(row["action"] == "SPEAK" for row in payload["decisions"])


@pytest.mark.asyncio
async def test_social_modes_requires_sandbox_switch(app, monkeypatch: pytest.MonkeyPatch):
    client = app.test_client()
    monkeypatch.setattr(settings, "SOCIAL_SANDBOX_ENABLED", False)

    blocked = await client.post(
        "/api/care/social/modes",
        json={"channel_id": "123456", "mode": "SANDBOX_GROUP", "enabled": True, "allow_webhooks": True},
    )
    assert blocked.status_code == 409

    invalid = await client.post("/api/care/social/modes", json={"channel_id": "abc", "mode": "SOLO"})
    assert invalid.status_code == 400

    monkeypatch.setattr(settings, "SOCIAL_SANDBOX_ENABLED", True)
    ok = await client.post(
        "/api/care/social/modes",
        json={"channel_id": "123456", "mode": "SANDBOX_GROUP", "enabled": True, "allow_webhooks": True},
    )
    assert ok.status_code == 200
    record = (await ok.get_json())["mode"]
    assert record["mode"] == "SANDBOX_GROUP"
    assert record["allow_webhooks"] is True


@pytest.mark.asyncio
async def test_social_personas_rejects_traversal_and_lists_souls(app, care_repo: CareRepository):
    client = app.test_client()

    evil = await client.post(
        "/api/care/social/personas",
        json={
            "channel_id": "123456",
            "persona_key": "npc_x",
            "display_name": "壞人",
            "soul_name": "../evil.md",
        },
    )
    assert evil.status_code == 400

    ok = await client.post(
        "/api/care/social/personas",
        json={
            "channel_id": "123456",
            "persona_key": "npc_a",
            "display_name": "NPC A",
            "soul_name": "npc_a.md",
            "speech_style": "短句",
        },
    )
    assert ok.status_code == 200
    assert (await ok.get_json())["persona"]["enabled"] is True

    listing = await client.get("/api/care/social/personas?channel_id=123456")
    assert listing.status_code == 200
    data = await listing.get_json()
    assert [p["persona_key"] for p in data["personas"]] == ["npc_a"]
    assert "npc_a.md" in data["available_souls"]
    assert all("README" not in name for name in data["available_souls"])

    deleted = await client.delete("/api/care/social/personas/npc_a")
    assert deleted.status_code == 200
    assert care_repo.get_persona("npc_a").enabled is False


@pytest.mark.asyncio
async def test_social_scene_and_kill_switch(app, care_repo: CareRepository):
    care_repo.upsert_channel_mode("123456", mode="SANDBOX_GROUP", enabled=True, allow_webhooks=True)
    care_repo.upsert_persona(
        persona_key="npc_a",
        channel_id="123456",
        soul_name="npc_a.md",
        display_name="NPC A",
        role="npc",
        enabled=True,
    )
    scene = care_repo.open_scene("123456", topic="測試")

    client = app.test_client()
    scene_resp = await client.get("/api/care/social/scene?channel_id=123456")
    assert scene_resp.status_code == 200
    scene_data = await scene_resp.get_json()
    assert scene_data["scene"]["scene_id"] == scene.scene_id

    killed = await client.post("/api/care/social/kill", json={"channel_id": "123456"})
    assert killed.status_code == 200
    assert (await killed.get_json())["status"] == "success"

    record = care_repo.get_channel_mode("123456")
    assert record.mode == "SOLO"
    assert record.enabled is False
    assert care_repo.get_open_scene("123456") is None
    assert care_repo.get_persona("npc_a").enabled is False
