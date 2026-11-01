"""tests/unit/test_user_connection_tracker.py
============================================
測試一般使用者與訪客連線狀況追蹤體系 (User Connection & Visitor Tracker)。

涵蓋範圍：
1. UserConnectionTracker 生命週期（連線登記、心跳維持、時長計算、離線結算、雙軌落盤 SQLite/JSONL）
2. Web UI 管理員權限隔離（訪客阻擋 401/403，管理員正常查詢）
3. 訪客心跳與離線 Beacon 端點功能驗證
4. 連線池容量硬邊界、閒置逾期判定與記憶體 LRU 淘汰機制
5. 總覽儀表板 /api/system/dashboard 訪客指標整合
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from mod.user_tracker import UserConnectionTracker, get_client_ip, parse_ip_location
from web_ui.server import app


# ===========================================================================
# 1. UserConnectionTracker 核心生命週期與雙軌落盤測試
# ===========================================================================


def test_user_connection_tracker_lifecycle(tmp_path: Path) -> None:
    """測試訪客連線建立、心跳更新、時長計算、離線標記與 SQLite/JSONL 雙軌落盤。"""
    test_db = tmp_path / "chat_history.db"
    test_log = tmp_path / "user_connections.jsonl"

    tracker = UserConnectionTracker(db_path=test_db, log_path=test_log, max_active=50)

    # 1. 登記新訪客連線
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0"
    ip = "192.168.1.100"
    path = "/api/system/dashboard"

    vid = tracker.touch_connection(visitor_id=None, ip=ip, user_agent=ua, path=path)
    assert vid.startswith("vis_")
    assert vid in tracker._active_connections

    conn = tracker._active_connections[vid]
    assert conn["status"] == "online"
    assert conn["ip"] == ip
    assert conn["location"] == "區域網路 (LAN)"
    assert "Windows" in conn["device"]
    assert conn["last_path"] == path
    assert conn["hit_count"] == 1

    # 2. 心跳更新
    time.sleep(0.05)
    hb_success = tracker.record_heartbeat(vid)
    assert hb_success is True
    assert tracker.record_heartbeat("vis_nonexistent") is False
    assert tracker._active_connections[vid]["status"] == "online"

    # 3. 再次存取不同路徑 (touch_connection 刷新)
    tracker.touch_connection(visitor_id=vid, ip=ip, user_agent=ua, path="/api/logs")
    assert tracker._active_connections[vid]["hit_count"] == 2
    assert tracker._active_connections[vid]["last_path"] == "/api/logs"

    # 4. 離線結算
    tracker.record_disconnect(vid)
    assert tracker._active_connections[vid]["status"] == "offline"

    # 5. 驗證 SQLite 資料庫落盤
    conn_db = sqlite3.connect(test_db)
    conn_db.row_factory = sqlite3.Row
    cur = conn_db.cursor()
    cur.execute("SELECT * FROM user_connections WHERE visitor_id = ?", (vid,))
    row = cur.fetchone()
    assert row is not None
    assert row["visitor_id"] == vid
    assert row["status"] == "offline"
    assert row["ip"] == ip
    assert row["hit_count"] == 2
    conn_db.close()

    # 6. 驗證 JSONL 檔案備援落盤
    assert test_log.exists()
    lines = test_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 2  # 包含初始連線與離線結算
    records = [json.loads(line) for line in lines]
    assert any(r["visitor_id"] == vid and r["status"] == "offline" for r in records)

    # 7. 驗證 get_connection_history
    history = tracker.get_connection_history(limit=10)
    assert len(history) >= 1
    assert history[0]["visitor_id"] == vid


# ===========================================================================
# 2. 管理員權限隔離測試 (/api/audit/user-connections)
# ===========================================================================


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_user_connections_api_auth_guard(anyio_backend: str) -> None:
    """測試 /api/audit/user-connections 嚴格遵循管理員鑑權防線。"""
    client = app.test_client()

    # 1. 訪客未授權存取被阻斷 (401 或 403)
    with patch("web_ui.server._is_admin_request", return_value=False):
        resp = await client.get("/api/audit/user-connections")
        assert resp.status_code in (401, 403)
        data = await resp.get_json()
        assert data["status"] == "error"

    # 2. 登入管理員可成功取得彙總與歷史明細
    with patch("web_ui.server._is_admin_request", return_value=True):
        resp = await client.get("/api/audit/user-connections")
        assert resp.status_code == 200
        data = await resp.get_json()
        assert data["status"] == "success"
        assert "summary" in data
        assert "online_count" in data["summary"]
        assert "today_total" in data["summary"]
        assert "active_visitors" in data
        assert "history" in data


# ===========================================================================
# 3. 訪客心跳與離線 Beacon 端點測試
# ===========================================================================


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_visitor_heartbeat_and_beacon(anyio_backend: str) -> None:
    """測試一般訪客無須登入即可維持心跳與發送離線 Beacon。"""
    client = app.test_client()

    # 1. 首次心跳自動生成 visitor_id
    resp_hb = await client.post("/api/user/heartbeat", json={})
    assert resp_hb.status_code == 200
    data_hb = await resp_hb.get_json()
    assert data_hb["status"] == "success"
    vid = data_hb.get("visitor_id")
    assert vid is not None and vid.startswith("vis_")

    # 2. 後續心跳攜帶 visitor_id 維持在線
    resp_hb2 = await client.post("/api/user/heartbeat", json={"visitor_id": vid})
    assert resp_hb2.status_code == 200
    data_hb2 = await resp_hb2.get_json()
    assert data_hb2["visitor_id"] == vid
    assert data_hb2["state"] == "online"

    # 3. 離線 Beacon 正確標記結算
    resp_dc = await client.post("/api/user/disconnect", json={"visitor_id": vid})
    assert resp_dc.status_code == 200
    data_dc = await resp_dc.get_json()
    assert data_dc["status"] == "success"


# ===========================================================================
# 4. 連線池容量上限與 LRU 淘汰機制測試
# ===========================================================================


def test_connection_memory_sweep(tmp_path: Path) -> None:
    """測試超過容量上限時的 LRU 記憶體淘汰機制與逾期狀態轉換。"""
    test_db = tmp_path / "chat_history.db"
    test_log = tmp_path / "user_connections.jsonl"

    # 限制容量上限為 5
    tracker = UserConnectionTracker(db_path=test_db, log_path=test_log, max_active=5)

    vids = []
    for i in range(10):
        vid = tracker.touch_connection(
            visitor_id=f"vis_user_{i}",
            ip=f"10.0.0.{i}",
            user_agent="TestAgent",
            path=f"/page_{i}",
        )
        vids.append(vid)

    # 記憶體中的活躍清單不應超過上限
    assert len(tracker._active_connections) <= 5

    # 測試閒置與逾期狀態切換 (45s -> idle, 600s -> offline)
    target_vid = list(tracker._active_connections.keys())[0]
    conn = tracker._active_connections[target_vid]

    # 模擬 50 秒前最後活躍 -> 應轉為 idle
    conn["last_active_ts"] = time.time() - 50.0
    tracker.sweep_expired(max_idle_sec=600.0)
    assert tracker._active_connections[target_vid]["status"] == "idle"

    # 模擬 700 秒前最後活躍 -> 應轉為 offline
    conn["last_active_ts"] = time.time() - 700.0
    tracker.sweep_expired(max_idle_sec=600.0)
    assert tracker._active_connections[target_vid]["status"] == "offline"


# ===========================================================================
# 5. 總覽儀表板訪客數據整合測試
# ===========================================================================


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_dashboard_includes_user_metrics(anyio_backend: str) -> None:
    """測試 /api/system/dashboard 數據中成功整合 users 指標。"""
    client = app.test_client()

    resp = await client.get("/api/system/dashboard")
    assert resp.status_code == 200
    data = await resp.get_json()
    assert data["status"] == "success"
    assert "users" in data
    assert "online_count" in data["users"]
    assert "today_total" in data["users"]
    assert isinstance(data["users"]["online_count"], int)
    assert isinstance(data["users"]["today_total"], int)


# ===========================================================================
# 6. IP 與網路屬性解析單元測試
# ===========================================================================


def test_ip_location_parsing() -> None:
    """測試本機、區網、VPN 與公網 IP 正確解析與客戶端 IP 提取。"""
    assert parse_ip_location("127.0.0.1") == "本機端 (Localhost)"
    assert parse_ip_location("localhost") == "本機端 (Localhost)"
    assert parse_ip_location("::1") == "本機端 (Localhost)"
    assert parse_ip_location("192.168.1.1") == "區域網路 (LAN)"
    assert parse_ip_location("10.10.0.5") == "區域網路 (LAN)"
    assert parse_ip_location("172.16.0.1") == "區域網路 (LAN)"
    assert parse_ip_location("172.20.0.1") == "區域網路 (LAN)"
    assert parse_ip_location("172.31.255.254") == "區域網路 (LAN)"
    assert parse_ip_location("26.100.200.1") == "虛擬區域網路 (Radmin VPN)"
    assert parse_ip_location("1.1.1.1") == "外部網際網路 (Public IP)"

    # 測試 get_client_ip 提取字串與請求物件
    assert get_client_ip("192.168.1.1") == "192.168.1.1"

    class DummyReq:
        remote_addr = "10.0.0.2"
        headers: dict[str, str] = {}

    assert get_client_ip(DummyReq()) == "10.0.0.2"


# ===========================================================================
# 7. 斷線後重連：多會話歷史完整保留測試（防覆寫問題）
# ===========================================================================


def test_user_reconnection_preserves_distinct_sessions(tmp_path: Path) -> None:
    """測試訪客離線後再次連線（同一 visitor_id），歷史資料表保留所有獨立會話，不被覆寫。"""
    test_db = tmp_path / "chat_history.db"
    test_log = tmp_path / "user_connections.jsonl"
    tracker = UserConnectionTracker(db_path=test_db, log_path=test_log)

    vid = "vis_persistent_user_1"

    # 會話 1：連線與離線
    tracker.touch_connection(visitor_id=vid, ip="192.168.1.50", path="/page1")
    tracker.record_disconnect(vid)

    # 會話 2：同一訪客在一段時間後重新連線
    time.sleep(0.02)
    tracker.touch_connection(visitor_id=vid, ip="192.168.1.50", path="/page2")
    tracker.record_disconnect(vid)

    # 驗證 SQLite 中有 2 筆獨立紀錄
    conn_db = sqlite3.connect(test_db)
    cur = conn_db.cursor()
    cur.execute(
        "SELECT id, visitor_id, last_path, status FROM user_connections WHERE visitor_id = ? ORDER BY id ASC", (vid,)
    )
    rows = cur.fetchall()
    conn_db.close()

    assert len(rows) == 2, f"預期有 2 筆會話紀錄，實際僅有 {len(rows)} 筆（舊會話恐被覆寫）"
    assert rows[0][2] == "/page1"
    assert rows[1][2] == "/page2"


# ===========================================================================
# 8. 離線 Beacon text/plain 容錯與 flush_all_active 測試
# ===========================================================================


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_visitor_beacon_text_plain_fallback(anyio_backend: str) -> None:
    """測試以 text/plain 或 raw body 發送之離線 Beacon 亦能正確解析。"""
    client = app.test_client()

    # 先登記一個 visitor
    resp_hb = await client.post("/api/user/heartbeat", json={})
    data = await resp_hb.get_json()
    vid = data["visitor_id"]

    # 以 text/plain 發送 Beacon
    payload = json.dumps({"visitor_id": vid})
    resp_dc = await client.post(
        "/api/user/disconnect",
        data=payload,
        headers={"Content-Type": "text/plain"},
    )
    assert resp_dc.status_code == 200
    res_json = await resp_dc.get_json()
    assert res_json["status"] == "success"


def test_flush_all_active_and_live_duration(tmp_path: Path) -> None:
    """測試 flush_all_active 與 get_online_summary 活躍連線動態均值計算。"""
    test_db = tmp_path / "chat_history.db"
    test_log = tmp_path / "user_connections.jsonl"
    tracker = UserConnectionTracker(db_path=test_db, log_path=test_log)

    vid = tracker.touch_connection("vis_active_1", ip="127.0.0.1")
    time.sleep(0.05)

    summary = tracker.get_online_summary()
    assert summary["online_count"] == 1
    assert summary["avg_duration_seconds"] >= 0

    tracker.flush_all_active()

    conn_db = sqlite3.connect(test_db)
    cur = conn_db.cursor()
    cur.execute("SELECT duration_seconds, status FROM user_connections WHERE visitor_id = ?", (vid,))
    row = cur.fetchone()
    conn_db.close()
    assert row is not None
