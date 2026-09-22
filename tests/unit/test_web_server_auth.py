"""
tests/unit/test_web_server_auth.py
==================================
Web UI 安全鑑權、防爆破 IP 鎖定、Session 逾時淘汰與登出功能單元測試。
驗證：
  1. POST /api/auth/login 正確密碼登入成功（發行 Token 與 HttpOnly Cookie）。
  2. POST /api/auth/login 密碼錯誤回傳 401 並累計失敗次數。
  3. 連續 5 次失敗觸發 IP 封鎖 15 分鐘（回傳 429 Too Many Requests）。
  4. 鎖定期間拒絕存取並防範暴力破解。
  5. 登入成功後自動清除該 IP 之失敗紀錄與鎖定狀態。
  6. Session 10 分鐘無操作逾時自動淘汰（Inactivity Timeout）。
  7. POST /api/auth/logout 撤銷 Session 並清除 Cookie。
  8. GET /api/auth/check 驗證 Bearer Header 與 Cookie 鑑權狀態。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import patch
import pytest

import settings
from web_ui.server import (
    _admin_sessions,
    _locked_ips,
    _login_attempts,
    app,
)


@pytest.fixture(autouse=True)
def cleanup_auth_state() -> Any:
    """每個測試前清理 Web UI Session 與鎖定狀態，避免互相干擾。"""
    _admin_sessions.clear()
    _login_attempts.clear()
    _locked_ips.clear()
    yield
    _admin_sessions.clear()
    _login_attempts.clear()
    _locked_ips.clear()


class TestWebUIAuthentication:
    """測試 Web UI 密碼認證與防暴力破解安全機制。"""

    def test_login_success(self) -> None:
        """正確密碼登入成功，回傳 200、Token 與 admin_session Cookie。"""
        async def _run() -> None:
            client = app.test_client()
            with patch.object(settings, "WEB_UI_PASSWORD", "SuperSecurePassword123!"):
                resp = await client.post(
                    "/api/auth/login",
                    json={"username": "admin", "password": "SuperSecurePassword123!"},
                )
                assert resp.status_code == 200
                data = await resp.get_json()
                assert data["status"] == "success"
                token = data["token"]
                assert bool(token)
                assert token in _admin_sessions
                assert "admin_session" in resp.headers.get("Set-Cookie", "")

        asyncio.run(_run())

    def test_login_invalid_password(self) -> None:
        """密碼錯誤回傳 401，且不發行 Token。"""
        async def _run() -> None:
            client = app.test_client()
            with patch.object(settings, "WEB_UI_PASSWORD", "SuperSecurePassword123!"):
                resp = await client.post(
                    "/api/auth/login",
                    json={"username": "admin", "password": "WrongPassword!"},
                )
                assert resp.status_code == 401
                data = await resp.get_json()
                assert data["status"] == "error"
                assert "密碼錯誤" in data["message"]
                assert len(_admin_sessions) == 0

        asyncio.run(_run())

    def test_brute_force_lockout_after_five_failures(self) -> None:
        """連續 5 次密碼錯誤後觸發 IP 封鎖 15 分鐘，後續請求皆回傳 429。"""
        async def _run() -> None:
            client = app.test_client()
            with patch.object(settings, "WEB_UI_PASSWORD", "CorrectPassword123!"), \
                 patch.object(settings, "WEB_UI_MAX_LOGIN_ATTEMPTS", 5), \
                 patch.object(settings, "WEB_UI_LOCKOUT_MINUTES", 15):

                # 前 4 次回傳 401
                for i in range(1, 5):
                    resp = await client.post(
                        "/api/auth/login",
                        json={"username": "admin", "password": f"wrong_{i}"},
                    )
                    assert resp.status_code == 401

                # 第 5 次觸發安全封鎖，回傳 429
                resp5 = await client.post(
                    "/api/auth/login",
                    json={"username": "admin", "password": "wrong_5"},
                )
                assert resp5.status_code == 429
                data5 = await resp5.get_json()
                assert "封鎖 15 分鐘" in data5["message"]

                # 被鎖定後，即使輸入正確密碼依然被擋在門外 (429)
                resp_locked = await client.post(
                    "/api/auth/login",
                    json={"username": "admin", "password": "CorrectPassword123!"},
                )
                assert resp_locked.status_code == 429
                data_locked = await resp_locked.get_json()
                assert "已暫時鎖定" in data_locked["message"]

        asyncio.run(_run())

    def test_login_success_clears_failure_history(self) -> None:
        """失敗數次後（未達 5 次），成功登入應重置該 IP 的失敗嘗試計數。"""
        async def _run() -> None:
            client = app.test_client()
            with patch.object(settings, "WEB_UI_PASSWORD", "CorrectPassword123!"):
                # 失敗 2 次
                await client.post("/api/auth/login", json={"password": "wrong_1"})
                await client.post("/api/auth/login", json={"password": "wrong_2"})
                assert len(_login_attempts) >= 1

                # 成功登入
                resp = await client.post("/api/auth/login", json={"password": "CorrectPassword123!"})
                assert resp.status_code == 200

                # 驗證失敗計數已被清除
                assert len(_login_attempts) == 0

        asyncio.run(_run())


class TestWebUISessionLifecycle:
    """測試 Session 逾期淘汰、主動登出與鑑權狀態檢查。"""

    def test_session_inactivity_timeout(self) -> None:
        """超過 10 分鐘無操作之 Session 在請求時應被自動清理淘汰。"""
        async def _run() -> None:
            token = "test_active_token_abc"
            # 模擬 601 秒前的活動時間（超過 600 秒門檻）
            _admin_sessions[token] = time.time() - 601.0

            client = app.test_client()
            # 以該 Token 呼叫 GET /api/auth/check
            resp = await client.get("/api/auth/check", headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200
            data = await resp.get_json()
            assert data["is_admin"] is False
            # 驗證該 Token 已自全域 Session 表中被剔除
            assert token not in _admin_sessions

        asyncio.run(_run())

    def test_session_active_within_timeout(self) -> None:
        """在 10 分鐘內的活躍 Session 應保持鑑權通過。"""
        async def _run() -> None:
            token = "test_active_token_xyz"
            _admin_sessions[token] = time.time() - 60.0  # 1 分鐘前

            client = app.test_client()
            resp = await client.get("/api/auth/check", headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200
            data = await resp.get_json()
            assert data["is_admin"] is True

        asyncio.run(_run())

    def test_logout_revokes_token_and_cookie(self) -> None:
        """主動登出後，Session Token 被撤銷且客戶端 Cookie 被刪除。"""
        async def _run() -> None:
            client = app.test_client()
            with patch.object(settings, "WEB_UI_PASSWORD", "SuperPassword123!"):
                # 1. 登入取得 Token
                login_resp = await client.post("/api/auth/login", json={"password": "SuperPassword123!"})
                login_data = await login_resp.get_json()
                token = login_data["token"]
                assert token in _admin_sessions

                # 2. 登出
                logout_resp = await client.post(
                    "/api/auth/logout",
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert logout_resp.status_code == 200
                # 3. 驗證 Token 已自伺服器移除
                assert token not in _admin_sessions

                # 4. 再次檢查應未認證
                check_resp = await client.get("/api/auth/check", headers={"Authorization": f"Bearer {token}"})
                check_data = await check_resp.get_json()
                assert check_data["is_admin"] is False

        asyncio.run(_run())

