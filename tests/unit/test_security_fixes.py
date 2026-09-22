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
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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

    def test_ipv6_loopback_proxy_trusts_forwarded_header(self) -> None:
        """IPv6 迴路 (::1) 反向代理連線時應解析 X-Forwarded-For。"""
        req = MagicMock()
        req.remote_addr = "::1"
        req.headers = {"X-Forwarded-For": "198.51.100.60, 10.0.0.1"}

        client_ip = _get_client_ip(req)
        assert client_ip == "198.51.100.60"

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
            mock_dns.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
            ]
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

            with patch("handlers.social.tools.search_tools.is_safe_public_url", side_effect=lambda u: "127.0.0.1" not in u), \
                 patch("handlers.social.tools.search_tools._get_search_session", AsyncMock(return_value=mock_session)):
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

            with patch.object(cog, "_is_admin", return_value=True), \
                 patch.object(cog, "_handle_debug_status", AsyncMock()) as mock_status:
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

            with patch.object(cog, "_is_admin", return_value=True), \
                 patch("handlers.social.utils.fetch_context_script", AsyncMock(return_value="對話歷史")), \
                 patch("handlers.social.memory_builder.build_user_memory_block", return_value="記憶小抄"), \
                 patch("handlers.social.persona.build_dynamic_persona", return_value=("頭人設", "尾小抄")), \
                 patch("handlers.social.dispatch_agent.DispatchAgentTool._call_llm", AsyncMock(return_value='{"tasks": []}')), \
                 patch("handlers.social.dispatch_agent.DispatchAgentTool._generate_final_response", AsyncMock(return_value=("測試回覆", "思考歷程", "SysPrompt", "UserPrompt"))):
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
            with patch.object(audit_manager, "record_security_event") as mock_audit, \
                 patch.object(settings, "WEB_UI_PASSWORD", "SuperSecurePassword123!"):
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
            with patch("pathlib.Path.exists", return_value=False), \
                 patch("builtins.open", MagicMock()):
                success = settings.update_setting("WEB_UI_PASSWORD", "BrandNewSecurePassword123!")
                assert success is True
                assert settings._WEB_UI_PASSWORD_GENERATED is False
