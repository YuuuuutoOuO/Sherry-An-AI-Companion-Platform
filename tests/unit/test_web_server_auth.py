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
  9. （R1）mod view 端點收斂、Cookie Secure 旗標、CSRF double-submit 與安全標頭。
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


class TestWebUIPerformanceAndAsync:
    """測試 Web UI 非同步執行、防阻塞與快取機制。"""

    def test_log_files_listing_and_cache(self) -> None:
        """測試日誌檔案清單取得與 TTL 快取機制（R3：需管理員權限）。"""
        async def _run() -> None:
            client = app.test_client()
            with patch("web_ui.server._is_admin_request", return_value=True):
                resp = await client.get("/api/logs/files")
                assert resp.status_code == 200
                data = await resp.get_json()
                assert isinstance(data, list)

                # 二次請求命中快取
                resp2 = await client.get("/api/logs/files")
                assert resp2.status_code == 200
                data2 = await resp2.get_json()
                assert len(data) == len(data2)

        asyncio.run(_run())

    def test_dynamic_modules_and_toggle_cache(self) -> None:
        """測試動態模組探索、TTL 快取與 toggle 模組快取失效重置。"""
        async def _run() -> None:
            client = app.test_client()
            resp = await client.get("/api/modules")
            assert resp.status_code == 200
            data = await resp.get_json()
            assert "modules" in data
            assert isinstance(data["modules"], dict)

            # 再次請求應命中快取
            resp2 = await client.get("/api/modules")
            assert resp2.status_code == 200

        asyncio.run(_run())

    def test_services_status_probe_and_cache(self) -> None:
        """測試地端服務探測與快取機制。"""
        async def _run() -> None:
            client = app.test_client()
            resp = await client.get("/api/services/status")
            assert resp.status_code == 200
            data = await resp.get_json()
            assert "services" in data
            assert isinstance(data["services"], list)
            assert len(data["services"]) == 5

            # 短時間內再次請求應命中快取
            resp2 = await client.get("/api/services/status")
            assert resp2.status_code == 200
            data2 = await resp2.get_json()
            assert data["checked_at"] == data2["checked_at"]

        asyncio.run(_run())

    def test_gzip_compression_and_cache_headers(self) -> None:
        """測試 Gzip 壓縮處理與靜態資源快取標頭。"""
        async def _run() -> None:
            client = app.test_client()
            # 1. 帶 Accept-Encoding: gzip 請求靜態 JS
            resp = await client.get("/static/app.js", headers={"Accept-Encoding": "gzip"})
            assert resp.status_code == 200
            assert resp.headers.get("Content-Encoding") == "gzip"
            assert "public, max-age=" in resp.headers.get("Cache-Control", "")
            raw_data = await resp.get_data()
            assert len(raw_data) < 100000  # 原始 167KB 壓縮後應小於 100KB

            # 2. 不帶 Accept-Encoding 請求不應有 Content-Encoding: gzip
            resp_plain = await client.get("/static/app.js")
            assert resp_plain.status_code == 200
            assert resp_plain.headers.get("Content-Encoding") is None

        asyncio.run(_run())

    def test_service_worker_and_version_api(self) -> None:
        """測試 PWA Service Worker 腳本提供與 /api/version 版本端點。"""
        async def _run() -> None:
            client = app.test_client()
            # 1. 測試 /sw.js
            resp_sw = await client.get("/sw.js")
            assert resp_sw.status_code == 200
            assert "javascript" in resp_sw.headers.get("Content-Type", "")
            assert resp_sw.headers.get("Service-Worker-Allowed") == "/"
            sw_code = await resp_sw.get_data()
            assert b"sherry-console" in sw_code

            # 2. 測試 /api/version
            resp_ver = await client.get("/api/version")
            assert resp_ver.status_code == 200
            data = await resp_ver.get_json()
            assert data["status"] == "success"
            assert "version" in data
            assert "commit_hash" in data

        asyncio.run(_run())


class TestR1AuthHardeningAndSecurityHeaders:
    """R1 認證硬化：mod view 端點收斂、Cookie 強化、CSRF 與安全標頭。

    對應報告 §5.1.1／§5.1.2／§5.3.4；execution_plan R1 出口驗收。
    """

    TEST_PASSWORD = "SuperSecurePassword123!"

    @staticmethod
    def _set_cookie_for(resp: Any, name: str) -> str:
        """自回應的 Set-Cookie 標頭中取出指定 cookie 的原始字串。"""
        for raw in resp.headers.getlist("Set-Cookie"):
            if str(raw).startswith(f"{name}="):
                return str(raw)
        return ""

    @staticmethod
    def _jar_value(client: Any, name: str) -> str:
        """自測試客戶端 CookieJar 取出指定 cookie 的值。"""
        for cookie in client.cookie_jar:
            if cookie.name == name:
                return str(cookie.value)
        return ""

    async def _login(self, client: Any) -> Any:
        with patch.object(settings, "WEB_UI_PASSWORD", self.TEST_PASSWORD):
            return await client.post("/api/auth/login", json={"password": self.TEST_PASSWORD})

    def test_guest_mod_view_requires_admin_session(self) -> None:
        """未登入 GET /api/mod/<mod_key>/view 必須被拒（401），不得公開渲染模板。"""
        async def _run() -> None:
            client = app.test_client()
            resp = await client.get("/api/mod/spark/view")
            assert resp.status_code == 401, "公開 mod view 端點未收斂（報告 §5.1.1 P0）"

        asyncio.run(_run())

    def test_admin_cookie_session_can_load_mod_view(self) -> None:
        """登入後以 HttpOnly Cookie 鑑權可正常取得 mod view（瀏覽器流程不受影響）。"""
        async def _run() -> None:
            client = app.test_client()
            login_resp = await self._login(client)
            assert login_resp.status_code == 200

            resp = await client.get("/api/mod/spark/view")
            assert resp.status_code == 200
            assert "text/html" in resp.headers.get("Content-Type", "")

        asyncio.run(_run())

    def test_login_cookies_hardened_and_csrf_issued(self) -> None:
        """登入需發行 HttpOnly+Secure+SameSite 會話 Cookie 與 JS 可讀的 CSRF Cookie。"""
        async def _run() -> None:
            client = app.test_client()
            resp = await self._login(client)
            assert resp.status_code == 200

            session_cookie = self._set_cookie_for(resp, "admin_session")
            assert session_cookie, "登入未發行 admin_session Cookie"
            assert "HttpOnly" in session_cookie, "admin_session 應為 HttpOnly"
            assert "Secure" in session_cookie, "admin_session 應補 Secure 旗標（報告 §5.3.4）"
            assert "SameSite" in session_cookie, "admin_session 應具 SameSite"

            csrf_cookie = self._set_cookie_for(resp, "sherry_csrf")
            assert csrf_cookie, "登入未發行 sherry_csrf CSRF Cookie"
            assert "Secure" in csrf_cookie, "CSRF Cookie 應具 Secure"
            assert "HttpOnly" not in csrf_cookie, "CSRF Cookie 需為 JS 可讀（double-submit）"

            # Cookie 鑑權仍可通過 /api/auth/check
            check = await client.get("/api/auth/check")
            assert check.status_code == 200
            assert (await check.get_json())["is_admin"] is True

        asyncio.run(_run())

    def test_security_headers_on_responses(self) -> None:
        """所有回應需附安全標頭：CSP / nosniff / X-Frame-Options / Referrer-Policy；（HTTPS）HSTS。"""
        async def _run() -> None:
            client = app.test_client()

            with patch.object(settings, "WEB_UI_HTTPS", False):
                resp = await client.get("/")
            csp = resp.headers.get("Content-Security-Policy", "")
            assert "default-src 'self'" in csp, "缺少 CSP default-src（報告 §5.3.4）"
            assert "object-src 'none'" in csp
            assert "frame-ancestors 'none'" in csp
            assert resp.headers.get("X-Content-Type-Options") == "nosniff"
            assert resp.headers.get("X-Frame-Options") == "DENY"
            assert resp.headers.get("Referrer-Policy"), "缺少 Referrer-Policy"
            assert not resp.headers.get("Strict-Transport-Security"), "非 HTTPS 不應下 HSTS"

            with patch.object(settings, "WEB_UI_HTTPS", True):
                resp_https = await client.get("/")
            assert "max-age=" in resp_https.headers.get("Strict-Transport-Security", ""), "HTTPS 時應附 HSTS"

        asyncio.run(_run())

    def test_csrf_blocks_cookie_authenticated_mutation_without_token(self) -> None:
        """Cookie 鑑權之變更型請求缺少／錯誤 CSRF Token 時回 403；正確 Token 放行。"""
        async def _run() -> None:
            client = app.test_client()
            await self._login(client)

            # 1. 無 X-CSRF-Token → 403
            resp = await client.post("/api/system/components/metrics/reset")
            assert resp.status_code == 403, "CSRF 缺失未阻擋（報告 §5.1.2 攻擊鏈）"
            body = await resp.get_json()
            assert body["code"] == "CSRF_FAILED"

            # 2. 偽造 Token → 403
            resp_bad = await client.post(
                "/api/system/components/metrics/reset",
                headers={"X-CSRF-Token": "forged-token"},
            )
            assert resp_bad.status_code == 403

            # 3. 正確 double-submit Token → 放行
            csrf_value = self._jar_value(client, "sherry_csrf")
            assert csrf_value, "登入後 CookieJar 應持有 sherry_csrf"
            resp_ok = await client.post(
                "/api/system/components/metrics/reset",
                headers={"X-CSRF-Token": csrf_value},
            )
            assert resp_ok.status_code == 200

        asyncio.run(_run())

    def test_csrf_exempt_for_bearer_header_clients(self) -> None:
        """以 Bearer/X-Admin-Token 標頭鑑權之請求（非瀏覽器）不受 CSRF 限制。"""
        async def _run() -> None:
            client = app.test_client()
            login_resp = await self._login(client)
            token = (await login_resp.get_json())["token"]

            fresh_client = app.test_client()  # 不繼承任何 Cookie
            resp = await fresh_client.post(
                "/api/system/components/metrics/reset",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200

        asyncio.run(_run())

    def test_csrf_cookie_self_heals_on_authenticated_get(self) -> None:
        """CSRF Cookie 遺失（過期/清除）時，已登入的 GET 應自動補發新 Cookie。"""
        async def _run() -> None:
            client = app.test_client()
            await self._login(client)

            # 模擬 CSRF Cookie 過期消失（保留 admin_session）
            for cookie in list(client.cookie_jar):
                if cookie.name == "sherry_csrf":
                    client.cookie_jar.clear(cookie.domain, cookie.path, cookie.name)
            assert self._jar_value(client, "sherry_csrf") == ""

            resp = await client.get("/api/auth/check")
            assert resp.status_code == 200
            assert self._set_cookie_for(resp, "sherry_csrf"), "已登入 GET 應自動補發 CSRF Cookie"
            assert self._jar_value(client, "sherry_csrf"), "CookieJar 應重新持有 sherry_csrf"

        asyncio.run(_run())

    def test_logout_clears_session_and_csrf_cookies(self) -> None:
        """登出需同時撤銷 Session Cookie 與 CSRF Cookie。"""
        async def _run() -> None:
            client = app.test_client()
            await self._login(client)
            assert self._jar_value(client, "admin_session")
            assert self._jar_value(client, "sherry_csrf")

            resp = await client.post(
                "/api/auth/logout",
                headers={"X-CSRF-Token": self._jar_value(client, "sherry_csrf")},
            )
            assert resp.status_code == 200
            assert self._jar_value(client, "admin_session") == ""
            assert self._jar_value(client, "sherry_csrf") == ""

        asyncio.run(_run())

