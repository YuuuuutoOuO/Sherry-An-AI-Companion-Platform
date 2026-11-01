"""tests/unit/test_observability_and_drills_r26.py
=============================================
R26 驗收測試：觀測體系、PII 遮罩、背景指標與 D1-D3 災難還原演練。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3
import tempfile
import pytest

from core.autonomy_guard import global_autonomy_guard
from core.db import backup_database, restore_database, verify_database_integrity
from handlers.social.observability import (
    redact_pii,
    log_event,
    log_error,
    get_observability_stats,
    flush_observability_logs,
)
from tools.drill_recovery import run_d1_drill, run_d2_drill, run_d3_drill


def test_pii_redaction_rules():
    """驗證 PII 遮罩涵蓋 Email、Token/API Key、手機、身分證字號與信用卡號。"""
    # Email
    assert redact_pii("聯絡我: alice.bob@test.corp.tw 謝謝") == "聯絡我: [EMAIL_REDACTED] 謝謝"

    # Bearer Token
    assert redact_pii("Authorization: Bearer abcdef1234567890xyz") == "Authorization: Bearer [TOKEN_REDACTED]"

    # API Keys (OpenAI, GitHub, Discord)
    assert redact_pii("我的金鑰 sk-1234567890abcdef1234567890 請保管") == "我的金鑰 [API_KEY_REDACTED] 請保管"
    assert redact_pii("ghp_1234567890abcdef1234567890abcdef") == "[API_KEY_REDACTED]"
    assert redact_pii("discord_token=MTAxMjM0NTY3ODkwMTIzNDU2Nw.xxxx") == "[API_KEY_REDACTED]"

    # Taiwan Mobile
    assert redact_pii("電話 0912-345-678") == "電話 [PHONE_REDACTED]"
    assert redact_pii("手機 +886912345678") == "手機 [PHONE_REDACTED]"

    # Taiwan ID
    assert redact_pii("身分證號碼: A123456789 請勿外流") == "身分證號碼: [TW_ID_REDACTED] 請勿外流"

    # Credit Card
    assert redact_pii("卡號: 4111-2222-3333-4444") == "卡號: [CREDIT_CARD_REDACTED]"


def test_pii_redaction_nested_structures():
    """驗證 redact_pii 支援字典、列表、元組等巢狀結構過濾。"""
    payload = {
        "user_email": "user@example.com",
        "contacts": ["0988-111-222", "A234567890"],
        "metadata": {
            "token": "sk-1234567890123456789012345678",
            "count": 42,
            "active": True,
        },
    }
    redacted = redact_pii(payload)
    assert redacted["user_email"] == "[EMAIL_REDACTED]"
    assert redacted["contacts"] == ["[PHONE_REDACTED]", "[TW_ID_REDACTED]"]
    assert redacted["metadata"]["token"] == "[API_KEY_REDACTED]"
    assert redacted["metadata"]["count"] == 42
    assert redacted["metadata"]["active"] is True


def test_log_event_pii_redaction(tmp_path, monkeypatch):
    """驗證 log_event 寫入前自動完成 PII 遮罩，不殘留原始機敏字串（含中文相鄰情境）。"""
    test_log = tmp_path / "runtime_log.jsonl"
    test_db = tmp_path / "test_chat.db"
    monkeypatch.setattr("handlers.social.observability._RUNTIME_LOG_PATH", test_log)
    monkeypatch.setattr("handlers.social.observability._CHAT_DB_PATH", test_db)

    log_event(
        "chat_reply",
        raw_input="請將密鑰 sk-1234567890abcdef1234567890 寄給信箱alice@example.com",
        cot_thought="發現使用者身分證為我的身分證A123456789不用找了與a987654321，電話是0912345678",
        reply_preview="已為您處理，卡號4111222233334444繳費扣款完成",
        extra={"api_key": "ghp_abcdef1234567890abcdef1234567890"},
    )
    flush_observability_logs(timeout=5.0)

    assert test_log.exists()
    content = test_log.read_text(encoding="utf-8")
    assert "sk-1234567890" not in content
    assert "alice@example.com" not in content
    assert "A123456789" not in content
    assert "a987654321" not in content
    assert "0912345678" not in content
    assert "4111222233334444" not in content
    assert "ghp_abcdef" not in content

    # 確認遮罩代碼存在
    assert "[API_KEY_REDACTED]" in content
    assert "[EMAIL_REDACTED]" in content
    assert "[TW_ID_REDACTED]" in content
    assert "[PHONE_REDACTED]" in content
    assert "[CREDIT_CARD_REDACTED]" in content


def test_observability_stats_and_governance():
    """驗證觀測狀態與治理預算可正常查詢且結構完整。"""
    obs_stats = get_observability_stats()
    assert "queue_size" in obs_stats
    assert "queue_maxsize" in obs_stats
    assert "dropped" in obs_stats
    assert obs_stats.get("pii_redaction_enabled") is True

    gov_status = global_autonomy_guard.get_budget_status()
    assert "kill_switch_active" in gov_status
    assert "health_level" in gov_status
    assert "actions_last_hour" in gov_status
    assert "max_actions_per_hour" in gov_status


def test_database_backup_integrity_and_restore(tmp_path):
    """驗證 SQLite 備份、完整性偵測與損毀還原機制。"""
    src_db = tmp_path / "original.db"
    bkp_db = tmp_path / "backup.db"

    # 1. 建立測試資料庫
    with sqlite3.connect(src_db) as conn:
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);")
        conn.execute("INSERT INTO users (name) VALUES ('Sherry');")
        conn.commit()

    # 2. 備份
    assert backup_database(src_db, bkp_db) is True
    valid, msg = verify_database_integrity(bkp_db)
    assert valid is True
    assert msg == "ok"

    # 3. 故意損毀主資料庫
    with open(src_db, "wb") as f:
        f.write(b"CORRUPTED_BYTES_HERE_NOT_SQLITE_HEADER")

    is_valid, err_msg = verify_database_integrity(src_db)
    assert is_valid is False
    assert "損毀" in err_msg or "異常" in err_msg

    # 4. 還原並查驗完整性與資料
    assert restore_database(bkp_db, src_db) is True
    restored_valid, _ = verify_database_integrity(src_db)
    assert restored_valid is True

    with sqlite3.connect(src_db) as conn:
        cur = conn.cursor()
        cur.execute("SELECT name FROM users WHERE id = 1;")
        assert cur.fetchone()[0] == "Sherry"


def test_drills_d1_d2_d3(tmp_path):
    """驗證 D1、D2、D3 三項災難演練全數可自動化通過。"""
    # D1 drill
    d1_res = run_d1_drill()
    assert d1_res["passed"] is True

    # D2 drill
    d2_res = run_d2_drill()
    assert d2_res["passed"] is True

    # D3 drill
    d3_res = run_d3_drill(tmp_path)
    assert d3_res["passed"] is True
    assert d3_res["details"]["restored_rows"] == 2


def test_web_ui_background_metrics_endpoint():
    """驗證 WebUI 的 /api/system/background_metrics 與 /api/system/dashboard 包含治理與佇列深度。"""
    async def _run():
        from web_ui.server import app
        client = app.test_client()

        # 1. Background Metrics API
        resp = await client.get("/api/system/background_metrics")
        assert resp.status_code == 200
        data = await resp.get_json()
        assert data["status"] == "success"
        bg = data["data"]
        assert "governance" in bg
        assert "spark" in bg
        assert "care" in bg
        assert "memory" in bg
        assert "observability" in bg

        # 2. Dashboard API
        dash_resp = await client.get("/api/system/dashboard")
        assert dash_resp.status_code == 200
        dash = await dash_resp.get_json()
        assert "queues" in dash
        assert "observability_write" in dash["queues"]
        assert "governance" in dash
        assert "background_systems" in dash

    asyncio.run(_run())
