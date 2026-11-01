"""
tests/unit/test_security_fixes.py
=================================
資安弱點深度修補單元測試套件。
驗證：
  1. 靜態路由路徑遍歷防禦 (Path Traversal / Arbitrary File Read)。
  2. 客戶端 IP 偽造防護 (_get_client_ip 僅在反向代理下信任 X-Forwarded-For)。
  3. URLSummaryTool 與 fetch_and_extract_url 伺服器端請求偽造 (SSRF) 防護。
  4. is_admin_user 預設拒絕原則 (Fail-Closed)。
  5. /debug 指令管理員存取控制。
  6. Web UI 設定端點與登入安全審計 (GET /api/settings 與 audit logging)。
"""

from __future__ import annotations

import asyncio
import socket
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import discord
from discord import app_commands

import settings
from handlers.admin.cog import AdminCog
from handlers.social.tools.search_tools import (
    URLSummaryTool,
    fetch_and_extract_url,
    is_safe_public_url,
)
from handlers.social.utils import is_admin_user
from mod.audit_manager import audit_manager
from web_ui.server import (
    _admin_sessions,
    _get_client_ip,
    app,
    serve_handler_or_mod_static,
)


# ===========================================================================
# 1. 靜態檔案路徑遍歷防禦測試
# ===========================================================================
class TestStaticPathTraversal:
    """驗證靜態路由對路徑遍歷、Windows 磁碟代號與目錄逃逸之防禦。"""

    def test_path_traversal_blocked(self) -> None:
        """測試各種路徑遍歷特徵字串皆被嚴格阻擋並回傳 403。"""

        async def _run() -> None:
            # 測試包含 ..
            res_any: Any = await serve_handler_or_mod_static("music", "../../settings.py")
            assert hasattr(res_any, "status_code") and res_any.status_code == 403

            # 測試 Windows 磁碟代號絕對路徑
            res_any = await serve_handler_or_mod_static("music", "C:/Windows/win.ini")
            assert hasattr(res_any, "status_code") and res_any.status_code == 403

            # 測試反斜線與 Windows 絕對路徑
            res_any = await serve_handler_or_mod_static("music", "D:\\boot.ini")
            assert hasattr(res_any, "status_code") and res_any.status_code == 403

            # 測試 Linux 根目錄絕對路徑
            res_any = await serve_handler_or_mod_static("music", "/etc/passwd")
            assert hasattr(res_any, "status_code") and res_any.status_code == 403

            # 測試空位元組注入 (Null Byte)
            res_any = await serve_handler_or_mod_static("music", "test.js\0.png")
            assert hasattr(res_any, "status_code") and res_any.status_code == 403

            # 測試非法 handler_key (含路徑分隔符或非法前綴)
            res_any = await serve_handler_or_mod_static("_internal", "test.js")
            assert hasattr(res_any, "status_code") and res_any.status_code == 403

            res_any = await serve_handler_or_mod_static("../handlers", "test.js")
            assert hasattr(res_any, "status_code") and res_any.status_code == 403

        asyncio.run(_run())

    def test_nonexistent_safe_file_returns_404(self) -> None:
        """測試合法路徑格式但檔案不存在時回傳 404 Not Found。"""

        async def _run() -> None:
            res_any: Any = await serve_handler_or_mod_static("music", "non_existent_file_12345.png")
            assert hasattr(res_any, "status_code") and res_any.status_code == 404

        asyncio.run(_run())


# ===========================================================================
# 2. 客戶端 IP 偽造防護測試
# ===========================================================================
class TestClientIPSpoofing:
    """驗證 _get_client_ip 僅在來源為受信賴反向代理時解析 X-Forwarded-For。"""

    def test_direct_external_connection_ignores_forwarded_header(self) -> None:
        """外部直接連線偽造 X-Forwarded-For 標頭時應被忽略，直接取用 remote_addr。"""
        req = MagicMock()
        req.remote_addr = "203.0.113.195"
        req.headers = {"X-Forwarded-For": "1.1.1.1"}

        client_ip = _get_client_ip(req)
        assert client_ip == "203.0.113.195"

    def test_loopback_proxy_trusts_forwarded_header(self) -> None:
        """本機反向代理 (127.0.0.1) 連線時應正常信任並解析 X-Forwarded-For。"""
        req = MagicMock()
        req.remote_addr = "127.0.0.1"
        req.headers = {"X-Forwarded-For": "198.51.100.55"}

        client_ip = _get_client_ip(req)
        assert client_ip == "198.51.100.55"

    def test_ipv6_loopback_proxy_uses_rightmost_forwarded_value(self) -> None:
        """IPv6 迴路 (::1) 反向代理連線時，XFF 鏈取最右值（最後一跳受控代理所見之對端）。"""
        req = MagicMock()
        req.remote_addr = "::1"
        req.headers = {"X-Forwarded-For": "198.51.100.60, 10.0.0.1"}

        client_ip = _get_client_ip(req)
        assert client_ip == "10.0.0.1"

    def test_no_forwarded_header_returns_remote_addr(self) -> None:
        """無轉發標頭時回傳 remote_addr。"""
        req = MagicMock()
        req.remote_addr = "192.168.1.100"
        req.headers = {}

        client_ip = _get_client_ip(req)
        assert client_ip == "192.168.1.100"

    def test_invalid_forwarded_header_falls_back_to_remote(self) -> None:
        """轉發標頭包含惡意代碼或無效 IP 字串時，應安全回退為 remote_addr。"""
        req = MagicMock()
        req.remote_addr = "127.0.0.1"
        req.headers = {"X-Forwarded-For": "<script>alert(1)</script>"}

        client_ip = _get_client_ip(req)
        assert client_ip == "127.0.0.1"


# ===========================================================================
# 3. 伺服器端請求偽造 (SSRF) 防護測試
# ===========================================================================
class TestSSRFProtection:
    """驗證 URLSummaryTool 與 fetch_and_extract_url 對內部網路與敏感網段之防禦。"""

    def test_is_safe_public_url_blocks_private_and_loopback(self) -> None:
        """驗證 is_safe_public_url 阻擋本機、私有 IP、Link-Local 與雲端 Metadata。"""
        # 本機迴路
        assert not is_safe_public_url("http://127.0.0.1:8080/api/settings")
        assert not is_safe_public_url("http://localhost:8080/")
        assert not is_safe_public_url("http://[::1]/")

        # 私有區網 (RFC 1918)
        assert not is_safe_public_url("http://192.168.1.1/router")
        assert not is_safe_public_url("http://10.0.0.1/")
        assert not is_safe_public_url("http://172.16.0.1/")

        # 雲端元數據端點 (AWS/GCP/Azure Metadata: 169.254.169.254)
        assert not is_safe_public_url("http://169.254.169.254/latest/meta-data/")

        # 非 HTTP/HTTPS 協定
        assert not is_safe_public_url("file:///etc/passwd")
        assert not is_safe_public_url("gopher://127.0.0.1:6379/")
        assert not is_safe_public_url("ftp://ftp.example.com")

        # 畸形網址
        assert not is_safe_public_url("")
        assert not is_safe_public_url("javascript:alert(1)")

    def test_is_safe_public_url_allows_valid_public_domain(self) -> None:
        """驗證合法公網網域名稱通過檢查。"""
        with patch("socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
            assert is_safe_public_url("https://example.com/article/123")

    def test_is_safe_public_url_blocks_cgnat_and_benchmarks(self) -> None:
        """驗證 is_safe_public_url 阻擋電信級 CGNAT (RFC 6598) 與效能測試網段 (RFC 2544)。"""
        # RFC 6598: 100.64.0.0/10
        assert not is_safe_public_url("http://100.64.0.1/status")
        assert not is_safe_public_url("http://100.127.255.254/")

        # RFC 2544: 198.18.0.0/15
        assert not is_safe_public_url("http://198.18.0.1/")

        # RFC 5737: 192.0.2.0/24
        assert not is_safe_public_url("http://192.0.2.1/")

    def test_fetch_and_extract_url_blocks_redirect_to_private_ip(self) -> None:
        """驗證公網網址跳轉 (302 Redirect) 至私有/本機 IP 時被及時攔截並阻斷。"""

        async def _run() -> None:
            target_url = "https://attacker-redirector.com/ssrf"

            mock_resp = MagicMock()
            mock_resp.status = 302
            mock_resp.headers = {"Location": "http://127.0.0.1:8080/api/settings"}

            mock_session = MagicMock()
            mock_session.get.return_value.__aenter__.return_value = mock_resp

            with patch(
                "handlers.social.tools.search_tools.is_safe_public_url", side_effect=lambda u: "127.0.0.1" not in u
            ), patch("handlers.social.tools.search_tools._get_search_session", AsyncMock(return_value=mock_session)):
                title, content, source_type = await fetch_and_extract_url(target_url, timeout_sec=5)
                assert source_type == "blocked"
                assert "安全" in content

        asyncio.run(_run())

    def test_fetch_and_extract_url_blocks_ssrf(self) -> None:
        """驗證 fetch_and_extract_url 攔截內部 URL 且回傳 source_type 為 blocked。"""

        async def _run() -> None:
            url = "http://127.0.0.1:8080/api/settings"
            title, content, source_type = await fetch_and_extract_url(url, timeout_sec=5)
            assert source_type == "blocked"
            assert "安全" in content

        asyncio.run(_run())

    def test_url_summary_tool_handles_ssrf_without_llm_inference(self) -> None:
        """驗證 URLSummaryTool 遇到 SSRF 網址時直接中斷，不派發 LLM 推論任務。"""

        async def _run() -> None:
            tool = URLSummaryTool()
            bot = MagicMock()
            brain = MagicMock()
            brain.enqueue_task = AsyncMock()
            bot.get_cog.return_value = brain

            message = MagicMock(spec=discord.Message)
            message.id = 9999
            message.channel.id = 8888

            user_query = "請幫我總結 http://169.254.169.254/latest/meta-data/"

            with patch("handlers.social.tools.search_tools.simulate_typing_and_send", AsyncMock()) as mock_send:
                await tool.execute(bot, message, user_query, "", "")
                # 確保未將內部敏感資料送交 Brain 推論佇列
                brain.enqueue_task.assert_not_called()
                # 確保有發送警告提示
                mock_send.assert_called_once()
                assert "安全" in mock_send.call_args[0][1]

        asyncio.run(_run())


# ===========================================================================
# 4. is_admin_user 預設拒絕原則 (Fail-Closed) 測試
# ===========================================================================
class TestAdminAuthorizationFailClosed:
    """驗證未配置管理員清單時嚴格遵循 Fail-Closed，不可全員放行。"""

    def test_unconfigured_admins_defaults_to_false(self) -> None:
        """當 ADMIN_ROLE_IDS 與 ADMIN_USER_IDS 皆為空時，應回傳 False。"""
        user = MagicMock(spec=discord.User)
        user.id = 123456789
        user.roles = []

        with patch("settings.ADMIN_ROLE_IDS", []), patch("settings.ADMIN_USER_IDS", []):
            assert is_admin_user(user) is False

    def test_admin_user_id_allowed(self) -> None:
        """指定管理員 ID 應回傳 True。"""
        user = MagicMock(spec=discord.User)
        user.id = 987654321

        with patch("settings.ADMIN_ROLE_IDS", []), patch("settings.ADMIN_USER_IDS", [987654321]):
            assert is_admin_user(user) is True

    def test_admin_role_id_allowed(self) -> None:
        """具備指定管理員身分組之成員應回傳 True。"""
        role = MagicMock()
        role.id = 55555
        member = MagicMock(spec=discord.Member)
        member.id = 11111
        member.roles = [role]

        with patch("settings.ADMIN_ROLE_IDS", [55555]), patch("settings.ADMIN_USER_IDS", []):
            assert is_admin_user(member) is True

    def test_none_target_returns_false(self) -> None:
        """傳入 None 目標時應回傳 False。"""
        assert is_admin_user(None) is False

    def test_guild_owner_allowed(self) -> None:
        """伺服器擁有者應回傳 True。"""
        member = MagicMock(spec=discord.Member)
        member.id = 12345
        member.guild = MagicMock()
        member.guild.owner_id = 12345
        with patch("settings.ADMIN_ROLE_IDS", []), patch("settings.ADMIN_USER_IDS", []):
            assert is_admin_user(member) is True

    def test_guild_administrator_permission_allowed(self) -> None:
        """具備 Discord 原生 Administrator 權限之成員應回傳 True。"""
        member = MagicMock(spec=discord.Member)
        member.id = 67890
        member.guild = MagicMock()
        member.guild.owner_id = 99999
        perms = MagicMock()
        perms.administrator = True
        member.guild_permissions = perms
        with patch("settings.ADMIN_ROLE_IDS", []), patch("settings.ADMIN_USER_IDS", []):
            assert is_admin_user(member) is True


# ===========================================================================
# 5. /debug 指令管理員權限控制測試
# ===========================================================================
class TestDebugCommandAuthorization:
    """驗證 /debug 斜線指令未授權成員無法調用。"""

    def test_non_admin_cannot_access_debug(self) -> None:
        """非管理員成員執行 /debug 指令時應被拒絕並回傳權限不足。"""

        async def _run() -> None:
            bot = MagicMock()
            cog = AdminCog(bot)

            interaction = MagicMock(spec=discord.Interaction)
            interaction.user.id = 999000
            interaction.response = MagicMock()
            interaction.response.send_message = AsyncMock()

            action = app_commands.Choice(name="檢視狀態", value="status")

            with patch.object(cog, "_is_admin", return_value=False):
                callback: Any = cog.debug_command.callback
                await callback(cog, interaction, action=action)

                interaction.response.send_message.assert_called_once()
                args, kwargs = interaction.response.send_message.call_args
                assert "權限不足" in args[0]
                assert kwargs["ephemeral"] is True

        asyncio.run(_run())

    def test_admin_can_access_debug(self) -> None:
        """管理員成員執行 /debug 指令時應正常執行處理邏輯。"""

        async def _run() -> None:
            bot = MagicMock()
            cog = AdminCog(bot)

            interaction = MagicMock(spec=discord.Interaction)
            interaction.user.id = 111222
            interaction.response = MagicMock()
            interaction.response.send_message = AsyncMock()

            action = app_commands.Choice(name="檢視狀態", value="status")

            with patch.object(cog, "_is_admin", return_value=True), patch.object(
                cog, "_handle_debug_status", AsyncMock()
            ) as mock_status:
                callback: Any = cog.debug_command.callback
                await callback(cog, interaction, action=action)
                mock_status.assert_called_once_with(interaction)

        asyncio.run(_run())

    def test_admin_debug_tag_pipeline_executes_without_name_error(self) -> None:
        """驗證管理員調用 /debug action:tag 時，組裝 Markdown 報告正常，未因未定義 trigger_line 而拋出 NameError。"""

        async def _run() -> None:
            bot = MagicMock()
            brain = MagicMock()
            bot.get_cog = MagicMock(return_value=brain)
            cog = AdminCog(bot)

            interaction = MagicMock(spec=discord.Interaction)
            interaction.user.id = 111222
            interaction.user.display_name = "AdminTester"
            interaction.channel = MagicMock()
            interaction.channel.name = "admin-dev"
            interaction.channel.id = 777999
            interaction.id = 888888
            interaction.response = MagicMock()
            interaction.response.defer = AsyncMock()
            interaction.followup = MagicMock()
            interaction.followup.send = AsyncMock()

            with patch.object(cog, "_is_admin", return_value=True), patch(
                "handlers.social.utils.fetch_context_script", AsyncMock(return_value="對話歷史")
            ), patch("handlers.social.memory_builder.build_user_memory_block", return_value="記憶小抄"), patch(
                "handlers.social.persona.build_dynamic_persona", return_value=("頭人設", "尾小抄")
            ), patch(
                "handlers.social.dispatch_agent.DispatchAgentTool._call_llm", AsyncMock(return_value='{"tasks": []}')
            ), patch(
                "handlers.social.dispatch_agent.DispatchAgentTool._generate_final_response",
                AsyncMock(return_value=("測試回覆", "思考歷程", "SysPrompt", "UserPrompt")),
            ):
                callback: Any = cog.debug_command.callback
                action = app_commands.Choice(name="模擬 Prompt", value="tag")
                await callback(cog, interaction, action=action, context="妳覺得這案子該怎麼查？")

                interaction.response.defer.assert_called_once()
                interaction.followup.send.assert_called_once()
                kwargs = interaction.followup.send.call_args.kwargs
                assert "file" in kwargs
                assert "embed" in kwargs
                report_content = kwargs["file"].fp.getvalue().decode("utf-8")
                assert "雪莉除錯流程分析報告" in report_content
                assert "AdminTester" in report_content
                assert "當前鎖定之回應目標" in report_content

        asyncio.run(_run())


# ===========================================================================
# 6. Web UI 設定端點與登入安全審計測試
# ===========================================================================
class TestWebUISettingsAndAuditSecurity:
    """驗證 Web UI 設定端點存取控制與登入失敗審計日誌。"""

    def test_settings_get_requires_admin_auth(self) -> None:
        """未登入管理員無法讀取 GET /api/settings。"""

        async def _run() -> None:
            client = app.test_client()
            resp = await client.get("/api/settings")
            assert resp.status_code == 401
            data = await resp.get_json()
            assert data["status"] == "error"

        asyncio.run(_run())

    def test_settings_get_succeeds_with_admin_auth(self) -> None:
        """登入管理員可成功讀取 GET /api/settings。"""

        async def _run() -> None:
            token = "test-valid-admin-token-12345"
            _admin_sessions[token] = time.time()
            try:
                client = app.test_client()
                resp = await client.get("/api/settings", headers={"Authorization": f"Bearer {token}"})
                assert resp.status_code == 200
                data = await resp.get_json()
                assert isinstance(data, dict)
            finally:
                _admin_sessions.pop(token, None)

        asyncio.run(_run())

    def test_login_failure_records_security_audit(self) -> None:
        """登入密碼錯誤時應記錄安全審計事件至 audit_manager。"""

        async def _run() -> None:
            client = app.test_client()
            with patch.object(audit_manager, "record_security_event") as mock_audit, patch.object(
                settings, "WEB_UI_PASSWORD", "SuperSecurePassword123!"
            ):
                resp = await client.post(
                    "/api/auth/login",
                    json={"username": "admin", "password": "WrongPassword!"},
                )
                assert resp.status_code == 401
                mock_audit.assert_called_once()
                action_name = mock_audit.call_args.kwargs.get("action") or mock_audit.call_args[1].get("action")
                assert action_name == "LOGIN_FAILED"

        asyncio.run(_run())


# ===========================================================================
# 7. settings.py 環境變數解析與密碼設定安全測試
# ===========================================================================
class TestSettingsSecurity:
    """驗證 settings.py 環境變數解析與密碼標記安全邏輯。"""

    def test_parse_int_list_env_handles_quotes_and_brackets(self) -> None:
        """驗證 _parse_int_list_env 正確處理引號、中括號與空格格式。"""
        from settings import _parse_int_list_env

        with patch.dict("os.environ", {"TEST_IDS": "[123, 456, 789]"}):
            assert _parse_int_list_env("TEST_IDS") == [123, 456, 789]

        with patch.dict("os.environ", {"TEST_IDS": "'111', \"222\", 333"}):
            assert _parse_int_list_env("TEST_IDS") == [111, 222, 333]

        with patch.dict("os.environ", {"TEST_IDS": ""}):
            assert _parse_int_list_env("TEST_IDS", default=[999]) == [999]

    def test_update_setting_password_clears_generated_flag(self) -> None:
        """驗證更新 WEB_UI_PASSWORD 設定時，重置 _WEB_UI_PASSWORD_GENERATED 標記為 False。"""
        import settings

        with patch.dict(settings.__dict__, {"_WEB_UI_PASSWORD_GENERATED": True}):
            with patch("pathlib.Path.exists", return_value=False), patch("builtins.open", MagicMock()):
                success = settings.update_setting("WEB_UI_PASSWORD", "BrandNewSecurePassword123!")
                assert success is True
                assert settings._WEB_UI_PASSWORD_GENERATED is False


# ===========================================================================
# 8. R2：檔案寫入、秘密與外洩面硬化測試
# ===========================================================================
class TestR2SettingsWriteWhitelist:
    """R2：update_setting 白名單反轉與靈魂檔路徑邊界（報告 §5.1.2 A3 斷點）。"""

    def test_update_setting_rejects_non_schema_sensitive_keys(self, tmp_path: Path) -> None:
        """非 SETTINGS_META 登錄之敏感鍵（憑證/路徑/token/人格原文）一律拒絕寫入。"""
        import settings

        cases: dict[str, Any] = {
            "WEB_UI_SSL_CERT": "C:/evil/cert.pem",
            "DISCORD_BOT_TOKEN": "stolen-token",
            "CONFIG_OVERRIDE_PATH": "C:/evil/override.json",
            "DEFAULT_SOUL_PATH": "C:/evil/soul.md",
            "SOUL_DIR": "C:/evil",
            "CORE_PERSONA": "hacked persona",
        }
        snapshot = {k: getattr(settings, k) for k in cases}
        with patch.object(settings, "CONFIG_OVERRIDE_PATH", tmp_path / "config_override.json"):
            for key, payload in cases.items():
                assert settings.update_setting(key, payload) is False, f"{key} 不應可寫入"
        for key in cases:
            assert getattr(settings, key) == snapshot[key], f"{key} 不應被修改"

    def test_soul_name_whitelist_rejects_traversal_payloads(self, tmp_path: Path) -> None:
        """靈魂檔名白名單：路徑穿越／磁碟代號／多重副檔名等樣本皆被拒絕且不改動全域狀態。"""
        import settings

        payloads = [
            "../../evil.md",
            "..\\..\\evil.md",
            "evil/../../outside.md",
            "C:\\Windows\\win.ini",
            "D:/evil.md",
            "foo.md.exe",
            "evil trailing.md",
            "evil.md/../sneak.md",
            "sub/evil.md",
            "",
        ]
        orig_name = settings.CURRENT_SOUL_NAME
        orig_path = settings.DEFAULT_SOUL_PATH
        with patch.object(settings, "CONFIG_OVERRIDE_PATH", tmp_path / "config_override.json"):
            for payload in payloads:
                assert settings.update_setting("CURRENT_SOUL_NAME", payload) is False, f"payload 應被拒絕: {payload!r}"
        assert settings.CURRENT_SOUL_NAME == orig_name
        assert settings.DEFAULT_SOUL_PATH == orig_path

    def test_soul_name_whitelist_accepts_valid_name(self, tmp_path: Path) -> None:
        """合法的 *.md 靈魂檔名（含既有 fallback 命名）仍可正常切換。"""
        import settings

        orig_name = settings.CURRENT_SOUL_NAME
        orig_path = settings.DEFAULT_SOUL_PATH
        try:
            with patch.object(settings, "CONFIG_OVERRIDE_PATH", tmp_path / "config_override.json"):
                assert settings.update_setting("CURRENT_SOUL_NAME", "manosaba_sherry_default.md") is True
                assert settings.CURRENT_SOUL_NAME == "manosaba_sherry_default.md"
                assert settings.DEFAULT_SOUL_PATH.name == "manosaba_sherry_default.md"
        finally:
            settings.CURRENT_SOUL_NAME = orig_name
            settings.DEFAULT_SOUL_PATH = orig_path

    def test_soul_content_write_denied_outside_soul_dir(self, tmp_path: Path) -> None:
        """SOUL_CONTENT 寫入目標被解析到 SOUL_DIR 之外時必須拒絕，且不得動到檔案。"""
        import settings

        outside = tmp_path / "outside_soul.md"
        outside.write_text("SAFE-CONTENT", encoding="utf-8")
        with patch.object(settings, "CONFIG_OVERRIDE_PATH", tmp_path / "config_override.json"), patch.object(
            settings, "DEFAULT_SOUL_PATH", outside
        ):
            assert settings.update_setting("SOUL_CONTENT", "HACKED-CONTENT") is False
        assert outside.read_text(encoding="utf-8") == "SAFE-CONTENT"

    def test_soul_content_write_allowed_inside_soul_dir(self, tmp_path: Path) -> None:
        """SOUL_CONTENT 寫入目標位於 SOUL_DIR 內時仍可正常更新。"""
        import settings

        soul_dir = tmp_path / "soul"
        soul_dir.mkdir()
        target = soul_dir / "test_soul_default.md"
        target.write_text("orig", encoding="utf-8")
        with patch.object(settings, "CONFIG_OVERRIDE_PATH", tmp_path / "config_override.json"), patch.object(
            settings, "SOUL_DIR", soul_dir
        ), patch.object(settings, "DEFAULT_SOUL_PATH", target):
            assert settings.update_setting("SOUL_CONTENT", "NEW-CONTENT") is True
        assert target.read_text(encoding="utf-8") == "NEW-CONTENT"

    def test_web_settings_post_rejects_non_whitelist_keys(self, tmp_path: Path) -> None:
        """POST /api/settings 對非 schema（非白名單）鍵一律拒絕並回報 partial_error。"""
        import settings

        async def _run() -> None:
            token = "test-r2-settings-token-1"
            _admin_sessions[token] = time.time()
            orig_cert = settings.WEB_UI_SSL_CERT
            try:
                with patch.object(settings, "CONFIG_OVERRIDE_PATH", tmp_path / "config_override.json"):
                    client = app.test_client()
                    resp = await client.post(
                        "/api/settings",
                        json={"WEB_UI_SSL_CERT": "C:/evil.pem", "CONFIG_OVERRIDE_PATH": "C:/evil.json"},
                        headers={"X-Admin-Token": token},
                    )
                    assert resp.status_code == 400
                    data = await resp.get_json()
                    assert data["status"] == "partial_error"
                    assert set(data["failed_keys"]) == {"WEB_UI_SSL_CERT", "CONFIG_OVERRIDE_PATH"}
                    assert settings.WEB_UI_SSL_CERT == orig_cert
            finally:
                _admin_sessions.pop(token, None)

        asyncio.run(_run())

    def test_web_settings_post_accepts_schema_keys(self, tmp_path: Path) -> None:
        """POST /api/settings 對 schema 白名單鍵（含本回合補登錄者）仍可正常儲存。"""
        import settings

        async def _run() -> None:
            token = "test-r2-settings-token-2"
            _admin_sessions[token] = time.time()
            orig_timeout = settings.LLM_TIMEOUT
            orig_watch = settings.PERF_WATCH_ENABLED
            try:
                with patch.object(settings, "CONFIG_OVERRIDE_PATH", tmp_path / "config_override.json"):
                    client = app.test_client()
                    resp = await client.post(
                        "/api/settings",
                        json={"LLM_TIMEOUT": "123", "PERF_WATCH_ENABLED": False},
                        headers={"X-Admin-Token": token},
                    )
                    assert resp.status_code == 200
                    data = await resp.get_json()
                    assert data["status"] == "success"
                    assert settings.LLM_TIMEOUT == 123
                    assert settings.PERF_WATCH_ENABLED is False
            finally:
                settings.LLM_TIMEOUT = orig_timeout
                settings.PERF_WATCH_ENABLED = orig_watch
                _admin_sessions.pop(token, None)

        asyncio.run(_run())


class TestR2ErrorScrubbing:
    """R2：例外細節不得直送 API 回應，統一帶 error_id 供伺服器端對照（報告 §5.3.6）。"""

    def test_model_list_error_response_hides_exception(self) -> None:
        """模型清單探測失敗時，回應不含例外訊息、改附 error_id。"""
        import web_ui.server as server

        marker = "MODEL-LEAK-MARKER-9f8e7d"

        async def _run() -> None:
            client = server.app.test_client()
            with patch.object(server.httpx, "AsyncClient", side_effect=RuntimeError(marker)):
                resp = await client.get("/api/models/ollama")
            assert resp.status_code == 200
            text = await resp.get_data(as_text=True)
            assert marker not in text
            data = await resp.get_json()
            assert data.get("error_id")

        asyncio.run(_run())

    def test_soul_content_error_hides_path_and_stack(self, tmp_path: Path) -> None:
        """人設檔讀取失敗時，回應不得外洩檔案路徑或堆疊訊息，改附 error_id。"""
        import settings
        import web_ui.server as server

        async def _run() -> None:
            token = "test-r2-soul-token-1"
            server._admin_sessions[token] = time.time()
            soul_dir = tmp_path / "soul_err"
            soul_dir.mkdir()
            (soul_dir / "boom.md").mkdir()  # 以目錄冒充 .md 檔，強制讀取失敗
            try:
                client = server.app.test_client()
                with patch.object(settings, "SOUL_DIR", soul_dir):
                    resp = await client.get(
                        "/api/soul/content?file=boom.md",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                assert resp.status_code == 500
                text = await resp.get_data(as_text=True)
                assert str(soul_dir) not in text
                data = await resp.get_json()
                assert data.get("error_id")
            finally:
                server._admin_sessions.pop(token, None)

        asyncio.run(_run())


class TestR2PasswordPolicy:
    """R2：密碼策略——hash 注入優先、預設不生成不列印、未配置時 fail-closed。"""

    def test_password_hash_roundtrip_and_priority(self) -> None:
        """WEB_UI_PASSWORD_HASH 可驗證正確密碼、拒絕錯誤密碼，且優先於明文。"""
        import settings

        pw = "CorrectHorseBatteryStaple!42"
        digest = settings.hash_web_ui_password(pw)
        assert digest.startswith("pbkdf2_sha256$")
        with patch.object(settings, "WEB_UI_PASSWORD_HASH", digest), patch.object(
            settings, "WEB_UI_PASSWORD", "plaintext-fallback"
        ):
            assert settings.verify_web_ui_password(pw) is True
            assert settings.verify_web_ui_password("plaintext-fallback") is False
            assert settings.verify_web_ui_password("wrong-password") is False
        with patch.object(settings, "WEB_UI_PASSWORD_HASH", ""), patch.object(
            settings, "WEB_UI_PASSWORD", "plaintext-fallback"
        ):
            assert settings.verify_web_ui_password("plaintext-fallback") is True
            assert settings.verify_web_ui_password("nope") is False

    def test_invalid_hash_format_rejects_all(self) -> None:
        """格式無效的 WEB_UI_PASSWORD_HASH 一律拒絕（不得回退明文比對）。"""
        import settings

        with patch.object(settings, "WEB_UI_PASSWORD_HASH", "not-a-valid-hash"), patch.object(
            settings, "WEB_UI_PASSWORD", "plaintext-fallback"
        ):
            assert settings.verify_web_ui_password("plaintext-fallback") is False

    def test_login_fail_closed_when_password_unconfigured(self) -> None:
        """未配置 hash 與明文時不得自動生成；登入一律 403 並給出明確設定指引。"""
        import settings

        async def _run() -> None:
            client = app.test_client()
            with patch.object(settings, "WEB_UI_PASSWORD", ""), patch.object(
                settings, "WEB_UI_PASSWORD_HASH", ""
            ), patch.object(settings, "_WEB_UI_PASSWORD_GENERATED", True):
                assert settings.is_web_ui_password_configured() is False
                resp = await client.post("/api/auth/login", json={"password": ""})
                assert resp.status_code == 403
                data = await resp.get_json()
                assert data["status"] == "error"
                assert "設定" in data["message"]

        asyncio.run(_run())

    def test_gen_password_cli_outputs_usable_hash(self, capsys: Any) -> None:
        """`python -m web_ui.server --gen-password` 一次性輸出可用密碼與對應 hash（不寫檔）。"""
        import re as _re
        import settings
        from web_ui.server import _cli_main

        rc = _cli_main(["--gen-password"])
        assert rc == 0
        out = capsys.readouterr().out
        m_hash = _re.search(r"WEB_UI_PASSWORD_HASH=(\S+)", out)
        m_pw = _re.search(r"（對應明文密碼[^：:]*[：:]\s*(\S+?)[）)]", out)
        assert m_hash is not None, "輸出應包含 WEB_UI_PASSWORD_HASH"
        assert m_pw is not None, "輸出應包含一次性明文密碼"
        digest, password = m_hash.group(1), m_pw.group(1)
        assert digest.startswith("pbkdf2_sha256$")
        with patch.object(settings, "WEB_UI_PASSWORD_HASH", digest), patch.object(settings, "WEB_UI_PASSWORD", ""):
            assert settings.verify_web_ui_password(password) is True
            assert settings.verify_web_ui_password(password + "x") is False


class TestR2BindFailFast:
    """R2：Web UI 綁定失敗不得靜默回退 0.0.0.0（fail-fast；報告 §5.3.9）。"""

    def _fake_uvicorn(self, errors: list[BaseException | None]) -> tuple[Any, Any]:
        fake_server = MagicMock()
        fake_server.serve = AsyncMock(side_effect=errors)
        return fake_server, MagicMock()

    def test_bind_failure_does_not_fallback_without_flag(self) -> None:
        """未顯式允許時，綁定失敗必須直接拋出（不嘗試第二個 0.0.0.0 綁定）。"""
        import web_ui.server as server

        async def _run() -> None:
            fake_server, _ = self._fake_uvicorn([OSError("port in use"), OSError("fallback also failed")])
            with patch.object(server.settings, "WEB_UI_HTTPS", False), patch.object(
                server.settings, "WEB_UI_SSL_CERT", None
            ), patch.object(server.settings, "WEB_UI_SSL_KEY", None), patch.object(
                server.settings, "WEB_UI_HOST", "127.0.0.1"
            ), patch.dict("os.environ", {"ALLOW_FALLBACK_BIND": ""}), patch(
                "uvicorn.Config", return_value=MagicMock()
            ) as cfg_mock, patch("uvicorn.Server", return_value=fake_server):
                with pytest.raises(OSError):
                    await server.start_server_async(bot=None)
            assert cfg_mock.call_count == 1

        asyncio.run(_run())

    def test_bind_failure_fallback_when_explicitly_allowed(self) -> None:
        """顯式 ALLOW_FALLBACK_BIND=1 時，才允許回退 0.0.0.0 重試一次。"""
        import web_ui.server as server

        async def _run() -> None:
            fake_server, _ = self._fake_uvicorn([OSError("port in use"), None])
            with patch.object(server.settings, "WEB_UI_HTTPS", False), patch.object(
                server.settings, "WEB_UI_SSL_CERT", None
            ), patch.object(server.settings, "WEB_UI_SSL_KEY", None), patch.object(
                server.settings, "WEB_UI_HOST", "127.0.0.1"
            ), patch.dict("os.environ", {"ALLOW_FALLBACK_BIND": "1"}), patch(
                "uvicorn.Config", return_value=MagicMock()
            ) as cfg_mock, patch("uvicorn.Server", return_value=fake_server):
                await server.start_server_async(bot=None)
            assert cfg_mock.call_count == 2
            assert cfg_mock.call_args_list[1].kwargs["host"] == "0.0.0.0"

        asyncio.run(_run())


# ===========================================================================
# 9. R3：端點收斂、公開路由白名單化、來源與鎖定表治理
# ===========================================================================
class TestR3EndpointConvergence:
    """R3：敏感端點未認證矩陣掃描——一律需管理員權限（報告 §5.3.1、§7.1.1 A4）。"""

    SENSITIVE_ENDPOINTS: list[tuple[str, str]] = [
        ("GET", "/api/system/tasks"),
        ("GET", "/api/bot/status"),
        ("GET", "/api/system/audit/kpi"),
        ("GET", "/api/system/audit/records"),
        ("GET", "/api/system/audit/records/1"),
        ("GET", "/api/self_monitoring/kpi"),
        ("GET", "/api/self_monitoring/stats"),
        ("GET", "/api/logs"),
        ("GET", "/api/logs/files"),
        ("GET", "/api/spark/triggers"),
        ("GET", "/api/spark/stats"),
        ("GET", "/api/stock_trader/overview"),
        ("GET", "/api/stock_trader/trades"),
        ("GET", "/api/deep_search/sessions"),
        ("POST", "/api/games/rps/play"),
        ("POST", "/api/spark/triggers"),
    ]

    def test_sensitive_endpoint_matrix_requires_admin(self) -> None:
        """未認證存取上述端點全數 401（無任何 200/403/404 漏網）。"""

        async def _run() -> None:
            client = app.test_client()
            with patch("web_ui.server._is_admin_request", return_value=False):
                for method, url in self.SENSITIVE_ENDPOINTS:
                    if method == "POST":
                        resp = await client.post(url)
                    else:
                        resp = await client.get(url)
                    assert resp.status_code == 401, f"{method} {url} 應回傳 401（實得 {resp.status_code}）"

        asyncio.run(_run())

    def test_public_showcase_endpoints_stay_available_for_guests(self) -> None:
        """公開展示端點（服務狀態／模組清單／模型列表）維持免登入可讀。"""

        async def _run() -> None:
            client = app.test_client()
            with patch("web_ui.server._is_admin_request", return_value=False):
                for url in ("/api/modules", "/api/services/status", "/api/models/ollama"):
                    resp = await client.get(url)
                    assert resp.status_code == 200, f"{url} 應維持公開（實得 {resp.status_code}）"

        asyncio.run(_run())

    def test_game_center_and_livestream_no_longer_declare_public_routes(self) -> None:
        """R3：game_center／livestream 的外掛公開路由宣告已收斂（報告 §5.3.2）。"""
        from mod import game_center as game_center_pkg
        from mod import livestream as livestream_pkg

        assert list(game_center_pkg.MOD_INFO.get("public_routes", [])) == []
        assert list(livestream_pkg.MOD_INFO.get("public_routes", [])) == []


class TestR3PublicRouteStructuring:
    """R3：public_routes 結構化（{path, methods, auth}）與 default deny（報告 §5.3.2）。"""

    def test_normalization_default_deny_rules(self) -> None:
        """僅接受完整結構（合法 path＋非空 methods＋auth=public）；其餘一律拒絕。"""
        import web_ui.server as server

        declared = [
            {"path": "/api/ok/", "methods": ["get", "post"], "auth": "public"},
            {"path": "/api/adminonly/", "methods": ["GET"], "auth": "admin"},
            {"path": "/api/nomethods/"},
            {"path": "badpath", "methods": ["GET"], "auth": "public"},
            "/api/legacy-string/",
        ]
        normalized = server._normalize_public_route_declarations(declared)
        assert len(normalized) == 1
        entry = normalized[0]
        assert entry["path"] == "/api/ok/"
        assert entry["methods"] == frozenset({"GET", "POST"})
        assert entry["auth"] == "public"

    def test_structured_route_gate_allows_only_declared_method(self) -> None:
        """結構化宣告僅放行「宣告路徑＋宣告方法」；未宣告端點 default deny。"""
        import web_ui.server as server

        async def _run() -> None:
            client = app.test_client()
            with patch.object(
                server,
                "_public_mod_routes",
                [{"path": "/api/system/tasks", "methods": frozenset({"GET"}), "auth": "public"}],
            ), patch.object(server, "_PUBLIC_GET_PREFIXES", ()):
                resp = await client.get("/api/system/tasks")
                assert resp.status_code == 200
                resp_other = await client.get("/api/bot/status")
                assert resp_other.status_code == 401
            with patch.object(server, "_PUBLIC_GET_PREFIXES", ()):
                resp_default = await client.get("/api/system/tasks")
                assert resp_default.status_code == 401

        asyncio.run(_run())


class TestR3ForwardedChainAndLockoutBounds:
    """R3：XFF 僅取最右值且僅信任受控代理；鎖定表 TTL＋上限（§5.3.3、§5.3.8）。"""

    def test_rightmost_forwarded_value_used_for_attribution(self) -> None:
        """受控代理連線時，取 XFF 最右值（客戶端可偽造左側值）。"""
        req = MagicMock()
        req.remote_addr = "127.0.0.1"
        req.headers = {"X-Forwarded-For": "1.2.3.4, 93.184.216.34"}
        assert _get_client_ip(req) == "93.184.216.34"

    def test_untrusted_remote_ignores_forwarded_chain(self) -> None:
        """非受控代理來源一律忽略 XFF 內容。"""
        req = MagicMock()
        req.remote_addr = "192.0.2.9"
        req.headers = {"X-Forwarded-For": "1.2.3.4, 93.184.216.34"}
        assert _get_client_ip(req) == "192.0.2.9"

    def test_game_center_ip_resolution_matches_server_semantics(self) -> None:
        """game_center 限流來源解析與主站語意同步（僅受控代理＋最右值）。"""
        from mod.game_center.web import _resolve_client_ip

        assert _resolve_client_ip("127.0.0.1", "1.2.3.4, 93.184.216.34") == "93.184.216.34"
        assert _resolve_client_ip("192.0.2.9", "1.2.3.4") == "192.0.2.9"
        assert _resolve_client_ip("127.0.0.1", "not-an-ip, ") == "127.0.0.1"

    def test_login_tracker_sweep_bounds_and_expires(self) -> None:
        """鎖定/失敗追蹤表會清理過期項目並限制上限（防偽造來源灌爆記憶體）。"""
        import web_ui.server as server

        saved_attempts = dict(server._login_attempts)
        saved_locked = dict(server._locked_ips)
        saved_sweep = server._last_login_tracker_sweep
        try:
            now = time.time()
            server._login_attempts.clear()
            server._locked_ips.clear()
            server._locked_ips["198.51.100.1"] = now - 1.0  # 已過解鎖時間
            server._login_attempts["198.51.100.2"] = [now - 120.0]  # 超出 60 秒失敗窗口
            for i in range(server._MAX_LOGIN_TRACKED_IPS + 50):
                server._locked_ips[f"tracked-{i}"] = now + 3600.0
            server._sweep_login_tracker(now)
            assert "198.51.100.1" not in server._locked_ips
            assert "198.51.100.2" not in server._login_attempts
            assert len(server._locked_ips) <= server._MAX_LOGIN_TRACKED_IPS
        finally:
            server._login_attempts.clear()
            server._login_attempts.update(saved_attempts)
            server._locked_ips.clear()
            server._locked_ips.update(saved_locked)
            server._last_login_tracker_sweep = saved_sweep


class TestR3LogFileWhitelist:
    """R3：/api/logs?file= 檔名白名單＋resolve 邊界（報告 §5.3.5）。"""

    def test_unknown_or_traversal_filenames_rejected(self) -> None:
        """未知檔名、路徑穿越與未登錄之資料表一律拒絕（不得落入檔案讀取分支）。"""
        from urllib.parse import quote

        async def _run() -> None:
            client = app.test_client()
            bad_files = [
                "../../config_override.json",
                "config_override.json",
                "..\\..\\bot.log",
                "chat_history.db [sqlite_master]",
                "unknown_file_xyz.jsonl",
            ]
            with patch("web_ui.server._is_admin_request", return_value=True):
                for bad in bad_files:
                    resp = await client.get(f"/api/logs?file={quote(bad, safe='')}")
                    assert resp.status_code == 400, f"{bad} 應被白名單拒絕（實得 {resp.status_code}）"

        asyncio.run(_run())


# ===========================================================================
# 10. R4：前端 XSS 修復 A（轉義層＋熱點）
# ===========================================================================
_R4_REPO_ROOT = Path(__file__).resolve().parents[2]

# XSS payload 清單（人工／瀏覽器驗證程序見 Docs/plans/26.10.19/xss_payload_checklist.md）
R4_XSS_PAYLOADS: list[str] = [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    '"><svg onload=alert(1)>',
    "'><iframe srcdoc='<script>parent.alert(1)</script>'>",
    "javascript:alert(1)",
]


def _read_frontend_source(rel_path: str) -> str:
    """讀取前端原始碼（R4 靜態守門；本環境無 Node，無法執行 JS 轉義函式）。"""
    return (_R4_REPO_ROOT / rel_path).read_text(encoding="utf-8")


class TestR4EscapeLayer:
    """R4：escHtml 轉義覆蓋與工具函式統一（報告 §5.4.1）。"""

    def test_escHtml_covers_single_quote(self) -> None:
        """escHtml 函式本體需涵蓋單引號（原缺口：獨漏 '）。"""
        import re

        src = _read_frontend_source("web_ui/static/app.js")
        match = re.search(r"function escHtml\(str\) \{(.*?)\n\}", src, re.S)
        assert match, "app.js 缺少 escHtml 定義"
        assert ".replace(/'/g, '&#39;')" in match.group(1), "escHtml 未轉義單引號"

    def test_escape_helpers_delegate_to_single_implementation(self) -> None:
        """escAttr / escapeHtml 皆委派 escHtml（統一轉義工具、避免多份實作漂移）。"""
        import re

        src = _read_frontend_source("web_ui/static/app.js")
        for fn in ("escAttr", "escapeHtml"):
            match = re.search(rf"function {fn}\(str\) \{{(.*?)\n\}}", src, re.S)
            assert match, f"app.js 缺少 {fn} 定義"
            assert "return escHtml(str);" in match.group(1), f"{fn} 未委派 escHtml"

    def test_cross_file_escape_helpers_cover_single_quote(self) -> None:
        """外掛腳本之轉義函式需與 escHtml 同等涵蓋單引號（games/deep_search 缺口修補）。"""
        for rel in (
            "handlers/spark/static/spark.js",
            "mod/game_center/static/games.js",
            "mod/deep_search/static/deep_search.js",
        ):
            src = _read_frontend_source(rel)
            assert "&#39;" in src, f"{rel} 之轉義函式未涵蓋單引號"


class TestR4HotspotEscaping:
    """R4：列舉熱點之動態欄位一律經轉義再入 innerHTML（payload 注入不得成碼）。"""

    def test_buildLogHTML_escapes_dynamic_fields(self) -> None:
        """日誌檢視器：badgeLabel、channel_id、tools_invoked、score 等欄位需轉義。"""
        src = _read_frontend_source("web_ui/static/app.js")
        assert "${escHtml(badgeLabel)}" in src, "badgeLabel 未轉義（595-605→662）"
        assert "escHtml(log.channel_id)" in src, "channel_id 未轉義（637 區）"
        assert "log.tools_invoked.map(t => escHtml(String(t))).join(', ')" in src, "tools_invoked 未逐一轉義"
        assert "escHtml(score)" in src, "score 未轉義（649 區）"

    def test_renderModManager_escapes_fields(self) -> None:
        """模組管理員：display_name/description/category/key 等欄位需轉義；id/onclick 以 sanitizeId 通行。"""
        src = _read_frontend_source("web_ui/static/app.js")
        assert "escHtml(mod.display_name || key)" in src
        assert "escHtml(mod.description || '未登記的自訂模組。')" in src
        assert "escHtml(mod.category || '自訂')" in src
        assert "escHtml(key)" in src
        assert "sanitizeId(key)" in src

    def test_spark_escapes_error_and_trigger_fields(self) -> None:
        """Spark 管理器：錯誤訊息與觸發器欄位（message/condition/channel_id）需轉義。"""
        src = _read_frontend_source("handlers/spark/static/spark.js")
        assert "this._esc(data.message || '未知錯誤')" in src, "載入失敗訊息未轉義（54）"
        assert "this._esc(err.message)" in src, "例外訊息未轉義（66）"
        assert "this._esc(t.message)" in src, "觸發器訊息未轉義（137 區）"
        assert "this._esc(k)" in src and "this._esc(v)" in src, "觸發條件未轉義（137 區）"
        assert "this._esc(t.channel_id)" in src, "channel_id 未轉義（137 區）"


class TestR4ServerModuleViewEscaping:
    """R4：模組視圖端點之 mod_key 驗證與輸出轉義（伺服器端同步補強）。"""

    def test_invalid_mod_key_rejected_and_valid_key_ok(self) -> None:
        """模組鍵須符合 [A-Za-z0-9_-]+；否則 400（不落入 HTML 反射與路徑探測）。"""
        from urllib.parse import quote

        async def _run() -> None:
            client = app.test_client()
            with patch("web_ui.server._is_admin_request", return_value=True):
                # 命中路由之非法鍵 → 400
                for bad in ('x"onmouseover="alert(1)', "x<script>alert(1)", "..", "_internal"):
                    resp = await client.get(f"/api/mod/{quote(bad, safe='')}/view")
                    assert resp.status_code == 400, f"{bad!r} 應被拒（實得 {resp.status_code}）"

                # 含編碼斜線之鍵不命中路由 → 404（仍為安全拒絕、無反射）
                resp = await client.get(f"/api/mod/{quote('x</script><script>alert(1)', safe='')}/view")
                assert resp.status_code in (400, 404), "含斜線之鍵應被安全拒絕"
                assert b"alert(1)" not in await resp.get_data() or resp.status_code == 404

                ok = await client.get("/api/mod/game_center/view")
                assert ok.status_code == 200, "合法模組鍵應維持可用"

        asyncio.run(_run())


# ===========================================================================
# 11. R5：前端 XSS 修復 B（DOMPurify＋innerHTML 清點＋CSP 回歸配套）
# ===========================================================================
# 2026-10-22: 任務排程頁升級為即時工作與自癒中樞 (Active AI Tasks + Music Recovery UI)，並重構為並行載入與整合卡片渲染 (86 處)。
_R5_INNERHTML_BUDGET: dict[str, int] = {
    "web_ui/static/app.js": 88,
    "web_ui/static/sherry_live2d.js": 2,
    "mod/deep_search/static/deep_search.js": 9,
    "mod/feed_sentinel/static/feed_sentinel.js": 2,
    "mod/game_center/static/games.js": 6,
    "mod/painter/static/painter.js": 9,
    "mod/stock_trader/static/stock_trader.js": 10,
    "handlers/spark/static/spark.js": 8,
}

_R5_INTERNAL_HANDLER_BUDGET: dict[str, int] = {
    "web_ui/templates/components/modals.html": 39,
    "web_ui/templates/components/sidebar.html": 13,
    "web_ui/templates/components/topbar.html": 32,
    "web_ui/templates/views/chat.html": 3,
    "web_ui/templates/views/dashboard.html": 6,
    "web_ui/templates/views/logs.html": 2,
    "web_ui/templates/views/metrics.html": 9,
    "web_ui/templates/views/roast.html": 10,
    "web_ui/templates/views/self_monitoring.html": 13,
    "web_ui/templates/views/settings.html": 9,
    "web_ui/templates/views/spark.html": 12,
    "mod/deep_search/templates/deep_search.html": 8,
    "mod/feed_sentinel/templates/feed_sentinel.html": 10,
    "mod/game_center/templates/games.html": 27,
    "mod/livestream/templates/livestream.html": 4,
    "mod/painter/templates/painter.html": 26,
    "handlers/spark/templates/spark.html": 12,
    "web_ui/static/app.js": 30,
    "web_ui/static/sherry_live2d.js": 2,
    "mod/deep_search/static/deep_search.js": 1,
    "mod/feed_sentinel/static/feed_sentinel.js": 2,
    "mod/game_center/static/games.js": 2,
    "mod/painter/static/painter.js": 5,
    "handlers/spark/static/spark.js": 3,
}


class TestR5FrontendHardening:
    """R5：DOMPurify 導入、inline script 外置化與清點基線治理（§5.4.1 後半、§2.5）。"""

    def test_dompurify_vendored_and_offline_ready(self) -> None:
        """DOMPurify 需以 vendored 檔離線可用（不依賴外部 CDN）。"""
        vendor = _R4_REPO_ROOT / "web_ui" / "static" / "vendor" / "purify.min.js"
        assert vendor.exists(), "缺少 vendored DOMPurify"
        src = vendor.read_text(encoding="utf-8", errors="replace")
        assert "DOMPurify 3.1.6" in src, "vendored 版本標記不符（見 vendor/README.md）"
        assert len(src) > 20_000, "vendored 檔疑似不完整"
        assert src.rstrip().endswith("sourceMappingURL=purify.min.js.map"), "vendored 檔疑似被截斷"

    def test_index_externalizes_inline_script_and_loads_vendor(self) -> None:
        """index.html 之 inline <script> 已外置化；DOMPurify 以外部 script 載入。"""
        idx = (_R4_REPO_ROOT / "web_ui" / "templates" / "index.html").read_text(encoding="utf-8")
        assert "/static/vendor/purify.min.js" in idx, "index.html 未載入 vendored DOMPurify"
        assert "/static/sw_register.js" in idx, "index.html 未載入外置之 SW 註冊腳本"
        assert "serviceWorker.register" not in idx, "index.html 仍有 inline SW 註冊 script"
        sw = (_R4_REPO_ROOT / "web_ui" / "static" / "sw_register.js").read_text(encoding="utf-8")
        assert "serviceWorker.register" in sw, "sw_register.js 內容不完整"

    def test_app_sanitize_entrypoint_and_policy_usage(self) -> None:
        """app.js 需提供 sanitizeHtml 統一入口、匯出至 SherryUI，並用於高風險動態站點。"""
        src = _read_frontend_source("web_ui/static/app.js")
        assert "function sanitizeHtml(" in src, "缺少 sanitizeHtml 定義"
        assert "DOMPurify.sanitize(" in src, "sanitizeHtml 未使用 DOMPurify"
        assert "sanitizeHtml(buildLogHTML(log, event))" in src, "日誌列未經 sanitizeHtml"
        assert "msgDiv.innerHTML = sanitizeHtml(" in src, "聊天氣泡未經 sanitizeHtml"
        assert "\n  sanitizeHtml,\n" in src, "SherryUI 未匯出 sanitizeHtml"

    def test_stock_trader_escapes_dynamic_fields(self) -> None:
        """stock_trader.js（R4 遺留）之動態欄位需補齊轉義。"""
        src = _read_frontend_source("mod/stock_trader/static/stock_trader.js")
        assert "function _escHtml(" in src, "缺少 _escHtml 工具"
        assert ".replace(/'/g, '&#39;')" in src, "_escHtml 未涵蓋單引號"
        for field in ("h.ticker", "t.reason", "th.thought_process", "th.decision_summary"):
            assert f"_escHtml({field})" in src, f"{field} 未轉義"

    def test_innerhtml_and_inline_handler_budget_not_increased(self) -> None:
        """提交檢查：新增 innerHTML／inline handler 需附審查理由（基線僅可下降）。"""
        import re

        entry = re.compile(r"\.innerHTML\s*=")
        for rel, budget in _R5_INNERHTML_BUDGET.items():
            src = _read_frontend_source(rel)
            count = len(entry.findall(src))
            assert count <= budget, (
                f"{rel} 之 innerHTML 指派 {count} 處超過 R5 基線 {budget}；"
                "新增站點需轉義或 sanitize 並更新基線（附理由）。"
            )

        handler_re = re.compile(
            r"\bon(?:click|change|input|submit|mouseover|mouseout|mouseenter|mouseleave"
            r"|keyup|keydown|focus|blur|load|error)\s*="
        )
        for rel, budget in _R5_INTERNAL_HANDLER_BUDGET.items():
            src = _read_frontend_source(rel)
            count = len(handler_re.findall(src))
            assert count <= budget, (
                f"{rel} 之 inline handler {count} 處超過 R5 基線 {budget}；"
                "新增站點需改事件代理並更新基線（附理由）。"
            )
