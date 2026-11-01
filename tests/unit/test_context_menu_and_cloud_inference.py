"""tests/unit/test_context_menu_and_cloud_inference.py
=====================================================
針對 Discord 右鍵訊息應用程式與雲端推論客戶端之單元測試。
遵循 AAA (Arrange-Act-Assert) 測試架構，使用 asyncio.run() 原生支援非同步測試。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from core.contracts import SherryRequest, SherryResponse
from app.orchestrator import SherryCore
from mod.clients.cloud_inference_client import CloudInferenceClient
from handlers.context_menu.cog import ContextMenuCog


# ---------------------------------------------------------------------------
# 1. 核心合約與 Core 調度測試
# ---------------------------------------------------------------------------

def test_context_menu_request_contract() -> None:
    """Arrange & Act: 建立 context_menu 類型之 SherryRequest 並驗證屬性。"""
    req = SherryRequest(
        trace_id="ctx-test-001",
        source_type="context_menu",
        user_id=999,
        user_name="TestUser",
        guild_id=111,
        channel_id=222,
        raw_content="這是一篇落落長的文章需要被摘要",
        options={"action": "summarize"},
    )
    # Assert
    assert req.source_type == "context_menu"
    assert req.options["action"] == "summarize"
    assert req.user_id == 999


def test_sherry_core_handles_context_menu_actions() -> None:
    """測試 SherryCore 接收四大 context_menu 動作並正確呼叫雲端客戶端。"""
    async def _run() -> None:
        # Arrange
        core = SherryCore(bot=None)
        mock_cloud = MagicMock()
        mock_cloud.summarize_text = AsyncMock(return_value="【摘要】核心要點第一點")
        mock_cloud.translate_text = AsyncMock(return_value="【翻譯】Hello -> 你好")
        mock_cloud.analyze_image = AsyncMock(return_value="【看圖】這是一張程式報錯截圖")
        mock_cloud.summarize_url = AsyncMock(return_value="【網址摘要】這支 YouTube 影片講述 AI 架構")
        mock_cloud.extend_ask = AsyncMock(return_value="【延伸回答】後天的天氣預計是多雲轉晴喔！")
        core.cloud_client = mock_cloud

        # Act 1: 摘要動作 (純文字)
        req_sum = SherryRequest(
            trace_id="ctx-01",
            source_type="context_menu",
            user_id=1,
            user_name="Alice",
            guild_id=10,
            channel_id=20,
            raw_content="測試長文",
            options={"action": "summarize"},
        )
        resp_sum = await core.handle_request(req_sum)

        # Assert 1
        assert resp_sum.success is True
        assert "核心要點" in resp_sum.reply_text
        assert "CONTEXT_SUMMARIZE" in resp_sum.capabilities_used
        mock_cloud.summarize_text.assert_awaited_once_with("測試長文")

        # Act 2: 翻譯動作
        req_trans = SherryRequest(
            trace_id="ctx-02",
            source_type="context_menu",
            user_id=2,
            user_name="Bob",
            guild_id=10,
            channel_id=20,
            raw_content="Hello world",
            options={"action": "translate", "target_lang": "繁體中文"},
        )
        resp_trans = await core.handle_request(req_trans)

        # Assert 2
        assert resp_trans.success is True
        assert "Hello -> 你好" in resp_trans.reply_text
        assert "CONTEXT_TRANSLATE" in resp_trans.capabilities_used
        mock_cloud.translate_text.assert_awaited_once_with("Hello world", target_lang="繁體中文")

        # Act 3: 視覺圖片摘要動作 (整合至 summarize)
        req_vis = SherryRequest(
            trace_id="ctx-03",
            source_type="context_menu",
            user_id=3,
            user_name="Charlie",
            guild_id=10,
            channel_id=20,
            raw_content="看這張圖",
            attachments=["https://cdn.discordapp.com/test.png"],
            options={"action": "summarize", "image_url": "https://cdn.discordapp.com/test.png", "mime_type": "image/png"},
        )
        resp_vis = await core.handle_request(req_vis)

        # Assert 3
        assert resp_vis.success is True
        assert "核心要點" in resp_vis.reply_text
        assert mock_cloud.summarize_text.await_count == 2

        # Act 4: 網址摘要動作 (整合至 summarize)
        req_url = SherryRequest(
            trace_id="ctx-04",
            source_type="context_menu",
            user_id=4,
            user_name="Dave",
            guild_id=10,
            channel_id=20,
            raw_content="https://www.youtube.com/watch?v=12345",
            options={"action": "summarize"},
        )
        resp_url = await core.handle_request(req_url)

        # Assert 4
        assert resp_url.success is True
        assert mock_cloud.summarize_text.await_count == 3

        # Act 5: 延伸發問動作
        req_ext = SherryRequest(
            trace_id="ctx-05",
            source_type="context_menu",
            user_id=5,
            user_name="Eve",
            guild_id=10,
            channel_id=20,
            raw_content="明天的天氣",
            options={
                "action": "extend_ask",
                "question": "那後天的天氣呢？",
                "context_history": "[Eve]: 明天的天氣",
                "user_name": "Eve",
            },
        )
        resp_ext = await core.handle_request(req_ext)

        # Assert 5
        assert resp_ext.success is True
        assert "後天的天氣" in resp_ext.reply_text
        assert "CONTEXT_EXTEND_ASK" in resp_ext.capabilities_used
        mock_cloud.extend_ask.assert_awaited_once_with(
            target_text="明天的天氣",
            question="那後天的天氣呢？",
            context_history="[Eve]: 明天的天氣",
            user_name="Eve",
        )

    asyncio.run(_run())




def test_sherry_core_rate_limit_blocks_rapid_clicks() -> None:
    """測試 CoreGuard 對 context_menu 狂點進行頻率阻擋。"""
    async def _run() -> None:
        # Arrange
        core = SherryCore(bot=None)
        mock_cloud = MagicMock()
        mock_cloud.summarize_text = AsyncMock(return_value="摘要完成")
        core.cloud_client = mock_cloud

        user_id = 8888
        # Act: 快速發送 6 次請求 (超過上限 5 次)
        responses = []
        for i in range(6):
            req = SherryRequest(
                trace_id=f"rapid-{i}",
                source_type="context_menu",
                user_id=user_id,
                user_name="Spammer",
                guild_id=1,
                channel_id=1,
                raw_content="測試文字",
                options={"action": "summarize"},
            )
            resp = await core.handle_request(req)
            responses.append(resp)

        # Assert: 前幾次成功，第 6 次必被 Rate Limit 攔截
        assert responses[0].success is True
        assert responses[-1].success is False
        assert responses[-1].error_message == "Rate limit exceeded"
        assert len(responses[-1].reply_text) > 0

    asyncio.run(_run())


def test_sherry_core_length_defense() -> None:
    """測試 Core 針對超長回覆文字進行 1950 字元截斷防禦。"""
    async def _run() -> None:
        # Arrange
        core = SherryCore(bot=None)
        mock_cloud = MagicMock()
        mock_cloud.summarize_text = AsyncMock(return_value="A" * 3000)
        core.cloud_client = mock_cloud

        req = SherryRequest(
            trace_id="length-test",
            source_type="context_menu",
            user_id=100,
            user_name="Tester",
            guild_id=1,
            channel_id=1,
            raw_content="產生超長文字",
            options={"action": "summarize"},
        )
        # Act
        resp = await core.handle_request(req)

        # Assert: 長度被安全限縮在 2000 以內且包含截斷說明
        assert len(resp.reply_text) <= 1980
        assert "已達 Discord 上限" in resp.reply_text

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 2. CloudInferenceClient 客戶端測試
# ---------------------------------------------------------------------------

def test_cloud_inference_client_model_resolution() -> None:
    """測試動態解析 settings.CLOUD_SEARCH_MODEL 前綴邏輯。"""
    client = CloudInferenceClient()

    with patch("settings.CLOUD_SEARCH_MODEL", "gemini:gemini-3.5-flash-lite"):
        assert client.get_resolved_model_name() == "gemini-3.5-flash-lite"

    with patch("settings.CLOUD_SEARCH_MODEL", "gemini:gemini-3.8-flash"):
        assert client.get_resolved_model_name() == "gemini-3.8-flash"

    # 若設定為非 Gemini 體系模型 (如 deepseek:)，自動後備至預設 Gemini 模型 (gemini-3.5-flash-lite)
    with patch("settings.CLOUD_SEARCH_MODEL", "deepseek:deepseek-v4-flash"):
        assert client.get_resolved_model_name() == "gemini-3.5-flash-lite"

    with patch("settings.CLOUD_SEARCH_MODEL", ""):
        assert client.get_resolved_model_name() == "gemini-3.5-flash-lite"


def test_cloud_inference_client_503_fallback_cascade() -> None:
    """測試當首選模型遭遇 503 尖峰過載時，自動降級切換至備援模型。"""
    async def _run() -> None:
        client = CloudInferenceClient(api_key="fake-key")

        # Mock: 第一次呼叫 (gemini-3.5-flash-lite) 拋出 503 過載異常，第二次備援模型成功返回
        call_count = 0

        def _fake_sync_call(contents: any, model_name: str, system_instruction: any = None) -> str:
            nonlocal call_count
            call_count += 1
            if "3.5-flash-lite" in model_name:
                raise RuntimeError("503 UNAVAILABLE. This model is currently experiencing high demand.")
            return f"來自備援模型 {model_name} 的成功回覆"

        with patch.object(client, "_call_generate_sync", side_effect=_fake_sync_call):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await client.summarize_text("需要摘要的文章")

        assert "來自備援模型" in result
        assert call_count >= 2

    asyncio.run(_run())


def test_cloud_inference_client_safety_settings_block_only_high() -> None:
    """測試 CloudInferenceClient 同步呼叫時帶入 BLOCK_ONLY_HIGH 安全過濾設定。"""
    client = CloudInferenceClient(api_key="fake-key")

    mock_genai_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "生成的摘要"
    mock_genai_client.models.generate_content.return_value = mock_response

    with patch.object(client, "_get_genai_client", return_value=mock_genai_client):
        res = client._call_generate_sync("測試內容", "gemini-3.5-flash-lite", system_instruction="測試指令")

    assert res == "生成的摘要"
    mock_genai_client.models.generate_content.assert_called_once()
    _, kwargs = mock_genai_client.models.generate_content.call_args
    config = kwargs.get("config")
    assert config is not None
    assert hasattr(config, "safety_settings") and config.safety_settings is not None
    # 驗證包含 4 項危害類別且 threshold 皆為 BLOCK_ONLY_HIGH
    assert len(config.safety_settings) == 4
    for setting in config.safety_settings:
        assert "BLOCK_ONLY_HIGH" in str(setting.threshold)


def test_cloud_inference_client_404_fallback_cascade() -> None:
    """測試當模型遭遇 404 NOT_FOUND (如 2.0 下線) 時，自動自適應推薦並動態插入新模型 (如 3.9) 成功回傳。"""
    async def _run() -> None:
        client = CloudInferenceClient(api_key="fake-key", default_model="gemini-2.0-flash")

        call_log = []

        def _fake_sync_call(contents: any, model_name: str, system_instruction: any = None) -> str:
            call_log.append(model_name)
            if "2.0-flash" in model_name:
                raise RuntimeError(
                    "404 NOT_FOUND. This model models/gemini-2.0-flash is no longer available. "
                    "Please update your code to use models/gemini-3.9-pro for the latest features."
                )
            return f"成功由 {model_name} 產出摘要"

        with patch("settings.CLOUD_SEARCH_MODEL", "gemini:gemini-2.0-flash"):
            with patch.object(client, "_call_generate_sync", side_effect=_fake_sync_call):
                result = await client.summarize_text("測試下線自動遷移")

        assert "gemini-3.9-pro" in result
        assert "gemini-2.0-flash" in call_log
        assert "gemini-3.9-pro" in call_log

    asyncio.run(_run())



def test_cloud_inference_client_empty_inputs() -> None:
    """測試傳入空字串或空資料時的邊界防禦。"""
    async def _run() -> None:
        client = CloudInferenceClient()

        res1 = await client.summarize_text("")
        assert "無可分析之文字" in res1

        res2 = await client.translate_text("   ")
        assert "無可翻譯之文字內容" in res2

        res3 = await client.analyze_image(image_bytes=b"", image_url="")
        assert "未能取得有效之圖片數據" in res3

        res4 = await client.summarize_url("沒有網址的純文字")
        assert "未在訊息中找到任何有效的網址" in res4

        res5 = await client.extend_ask("", "")
        assert "目標訊息與延伸問題皆為空" in res5

    asyncio.run(_run())


def test_cloud_inference_client_summarize_and_translate_success() -> None:
    """測試 Mock 生成下文字摘要、翻譯、圖片與延伸發問產出。"""
    async def _run() -> None:
        client = CloudInferenceClient(api_key="test-fake-key")

        with patch.object(client, "_generate_async", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = "這是摘要成果"
            res = await client.summarize_text("目標文章內容")
            assert res == "這是摘要成果"
            mock_gen.assert_awaited_once()

        # 測試網址自動偵測並調用 summarize_url
        with patch.object(client, "summarize_url", new_callable=AsyncMock) as mock_url_sum:
            mock_url_sum.return_value = "這是網址摘要"
            res_url = await client.summarize_text("https://example.com/test")
            assert res_url == "這是網址摘要"
            mock_url_sum.assert_awaited_once_with("https://example.com/test")

        with patch.object(client, "_generate_async", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = "這是繁中翻譯"
            res = await client.translate_text("Hello World", target_lang="繁體中文")
            assert res == "這是繁中翻譯"
            mock_gen.assert_awaited_once()

        with patch.object(client, "_generate_async", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = "這是延伸發問回覆"
            res = await client.extend_ask(
                target_text="明天天氣如何？",
                question="後天呢？",
                context_history="[Alice]: 明天好熱",
                user_name="Bob",
            )
            assert res == "這是延伸發問回覆"
            mock_gen.assert_awaited_once()
            call_prompt = mock_gen.call_args[0][0]
            assert "明天天氣如何？" in call_prompt
            assert "後天呢？" in call_prompt
            assert "[Alice]: 明天好熱" in call_prompt

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 3. ContextMenuCog 適配器層測試
# ---------------------------------------------------------------------------

def test_context_menu_cog_registration_and_unload() -> None:
    """測試 Cog 初始化時正確註冊 3 個精簡指令，卸載時全數移除。"""
    async def _run() -> None:
        mock_bot = MagicMock()
        mock_tree = MagicMock()
        mock_bot.tree = mock_tree

        cog = ContextMenuCog(mock_bot)
        # Assert: 註冊 3 次 (摘要/延伸發問/翻譯)
        assert mock_tree.add_command.call_count == 3

        # Act: 卸載 Cog
        await cog.cog_unload()

        # Assert: 移除 3 次
        assert mock_tree.remove_command.call_count == 3

    asyncio.run(_run())


def test_context_menu_cog_handle_extend_ask_opens_modal() -> None:
    """測試點選「雪莉延伸發問」時正確彈出 ExtendAskModal。"""
    from handlers.context_menu.cog import ExtendAskModal

    async def _run() -> None:
        mock_bot = MagicMock()
        mock_bot.tree = MagicMock()
        cog = ContextMenuCog(mock_bot)

        interaction = AsyncMock()
        interaction.response = AsyncMock()

        message = MagicMock()
        message.clean_content = "原訊息內容"

        # Act
        await cog.handle_extend_ask_context(interaction, message)

        # Assert
        interaction.response.send_modal.assert_awaited_once()
        modal_arg = interaction.response.send_modal.call_args[0][0]
        assert isinstance(modal_arg, ExtendAskModal)
        assert modal_arg.target_message == message

    asyncio.run(_run())


def test_extend_ask_modal_empty_input_guard() -> None:
    """測試 ExtendAskModal 使用者輸入空白字串時的防呆提示。"""
    from handlers.context_menu.cog import ExtendAskModal

    async def _run() -> None:
        mock_bot = MagicMock()
        mock_bot.tree = MagicMock()
        cog = ContextMenuCog(mock_bot)

        interaction = AsyncMock()
        interaction.response = AsyncMock()
        interaction.followup = AsyncMock()

        message = MagicMock()
        message.clean_content = "測試訊息"

        modal = ExtendAskModal(cog=cog, target_message=message)
        modal.question_input = MagicMock()
        modal.question_input.value = "   "

        # Act
        await modal.on_submit(interaction)

        # Assert
        interaction.response.defer.assert_awaited_once_with(ephemeral=False)
        interaction.followup.send.assert_awaited_once()
        assert "不能為空" in interaction.followup.send.call_args[0][0]

    asyncio.run(_run())


def test_extend_ask_modal_submit_success() -> None:
    """測試 ExtendAskModal 成功提交並派發至 core。"""
    from handlers.context_menu.cog import ExtendAskModal

    async def _run() -> None:
        mock_bot = MagicMock()
        mock_bot.tree = MagicMock()
        cog = ContextMenuCog(mock_bot)
        cog._dispatch_to_core = AsyncMock()

        interaction = AsyncMock()
        interaction.user.display_name = "TestUser"

        message = MagicMock()
        message.clean_content = "原訊息內容"
        message.channel = MagicMock()

        modal = ExtendAskModal(cog=cog, target_message=message)
        modal.question_input = MagicMock()
        modal.question_input.value = "請問後續發展？"

        # Act
        await modal.on_submit(interaction)

        # Assert
        cog._dispatch_to_core.assert_awaited_once()
        kwargs = cog._dispatch_to_core.call_args[1]
        assert kwargs["action"] == "extend_ask"
        assert kwargs["extra_options"]["question"] == "請問後續發展？"
        assert kwargs["extra_options"]["user_name"] == "TestUser"

    asyncio.run(_run())


def test_context_menu_cog_empty_text_guard() -> None:
    """測試點選摘要但訊息無文字與圖片時的直接防禦回覆。"""
    async def _run() -> None:
        mock_bot = MagicMock()
        mock_bot.tree = MagicMock()
        cog = ContextMenuCog(mock_bot)

        interaction = AsyncMock()
        interaction.response = AsyncMock()
        interaction.followup = AsyncMock()

        message = MagicMock()
        message.clean_content = ""
        message.content = ""
        message.attachments = []

        # Act
        await cog.handle_summarize_context(interaction, message)

        # Assert
        interaction.response.defer.assert_awaited_once_with(ephemeral=False)
        interaction.followup.send.assert_awaited_once()
        assert "無任何文字、網址或圖片附件" in interaction.followup.send.call_args[0][0]

    asyncio.run(_run())


def test_context_menu_cog_summarize_with_image_attachment() -> None:
    """測試點選摘要且訊息帶有圖片附件時，自動提取 image_url 進行多模態派發。"""
    async def _run() -> None:
        mock_bot = MagicMock()
        mock_bot.tree = MagicMock()
        cog = ContextMenuCog(mock_bot)
        cog._dispatch_to_core = AsyncMock()

        interaction = AsyncMock()
        message = MagicMock()
        message.clean_content = "這是報錯截圖"
        mock_att = MagicMock()
        mock_att.url = "https://cdn.discordapp.com/error.png"
        mock_att.content_type = "image/png"
        message.attachments = [mock_att]

        # Act
        await cog.handle_summarize_context(interaction, message)

        # Assert
        cog._dispatch_to_core.assert_awaited_once()
        kwargs = cog._dispatch_to_core.call_args[1]
        assert kwargs["action"] == "summarize"
        assert kwargs["extra_options"]["image_url"] == "https://cdn.discordapp.com/error.png"
        assert kwargs["extra_options"]["mime_type"] == "image/png"

    asyncio.run(_run())



def test_context_menu_cog_dispatches_to_core_successfully() -> None:
    """測試完整從 Interaction 派發至 bot.core 並回傳 followup。"""
    async def _run() -> None:
        mock_bot = MagicMock()
        mock_core = MagicMock()
        mock_core.handle_request = AsyncMock(
            return_value=SherryResponse(trace_id="test", reply_text="成功摘要！", success=True)
        )
        mock_bot.core = mock_core
        cog = ContextMenuCog(mock_bot)

        interaction = AsyncMock()
        interaction.user.id = 123
        interaction.user.display_name = "User"
        interaction.guild_id = 456
        interaction.channel_id = 789
        interaction.response = AsyncMock()
        interaction.followup = AsyncMock()

        message = MagicMock()
        message.clean_content = "這是一則測試訊息"
        message.attachments = []

        # Act
        await cog.handle_summarize_context(interaction, message)

        # Assert
        interaction.response.defer.assert_awaited_once_with(ephemeral=False)
        mock_core.handle_request.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()
        assert "成功摘要！" in interaction.followup.send.call_args[0][0]

    asyncio.run(_run())


def test_cloud_inference_client_custom_provider_priority() -> None:
    """測試當設定為自訂 provider（如 deepseek:）時，文字任務優先走 OllamaClient。"""
    async def _run() -> None:
        client = CloudInferenceClient(api_key="fake-key")

        # Mock OllamaClient
        mock_ollama_instance = AsyncMock()
        mock_ollama_instance.chat = AsyncMock(return_value="由 DeepSeek 產生的摘要結果")

        with patch("settings.CLOUD_SEARCH_MODEL", "deepseek:deepseek-v4-flash"):
            with patch("handlers.bot_brain.client.OllamaClient", return_value=mock_ollama_instance):
                result = await client.summarize_text("測試內文")

        assert result == "由 DeepSeek 產生的摘要結果"
        mock_ollama_instance.chat.assert_awaited_once()
        call_kwargs = mock_ollama_instance.chat.call_args[1]
        assert call_kwargs["model"] == "deepseek:deepseek-v4-flash"

    asyncio.run(_run())


def test_sherry_core_context_cache_dedup() -> None:
    """測試相同輸入於 TTL 窗口內重複請求時直接命中快取，不重複呼叫雲端。"""
    async def _run() -> None:
        # Arrange
        core = SherryCore(bot=None)
        mock_cloud = MagicMock()
        calls = {"n": 0}

        async def fake_summarize(text: str) -> str:
            calls["n"] += 1
            return f"摘要結果 #{calls['n']}"

        mock_cloud.summarize_text = fake_summarize
        core.cloud_client = mock_cloud

        def make_req() -> SherryRequest:
            return SherryRequest(
                trace_id="cache-test",
                source_type="context_menu",
                user_id=50,
                user_name="Tester",
                guild_id=1,
                channel_id=1,
                raw_content="相同內文",
                options={"action": "summarize"},
            )

        # Act: 相同內容連續請求 3 次
        r1 = await core.handle_request(make_req())
        r2 = await core.handle_request(make_req())
        r3 = await core.handle_request(make_req())

        # Assert: 僅第一次真正呼叫雲端，後續命中快取
        assert calls["n"] == 1
        assert r1.reply_text == r2.reply_text == r3.reply_text
        assert "CACHE_HIT" in r2.capabilities_used
        assert "CACHE_HIT" not in r1.capabilities_used

    asyncio.run(_run())


def test_sherry_core_context_cache_distinct_inputs() -> None:
    """測試不同內容各自獨立快取，互不污染。"""
    async def _run() -> None:
        core = SherryCore(bot=None)
        mock_cloud = MagicMock()
        mock_cloud.summarize_text = AsyncMock(side_effect=["摘要A", "摘要B"])
        core.cloud_client = mock_cloud

        def make_req(content: str) -> SherryRequest:
            return SherryRequest(
                trace_id="cache-distinct",
                source_type="context_menu",
                user_id=60,
                user_name="Tester",
                guild_id=1,
                channel_id=1,
                raw_content=content,
                options={"action": "summarize"},
            )

        r1 = await core.handle_request(make_req("內文A"))
        await core.handle_request(make_req("內文B"))
        r3 = await core.handle_request(make_req("內文A"))

        assert mock_cloud.summarize_text.await_count == 2
        assert r1.reply_text == r3.reply_text

    asyncio.run(_run())


def test_core_guard_scoped_rate_limit() -> None:
    """測試 scoped 限流：default 與 context_menu 兩個窗口互相獨立。"""
    async def _run() -> None:
        from core.guard import CoreGuard

        guard = CoreGuard()
        user = 777

        # context_menu 快速連發超過獨立的 5 次上限
        blocked_ctx = False
        for _ in range(6):
            ok, _ = guard.check_rate_limit(user, max_calls=5, window_sec=10.0, scope="context_menu")
            if not ok:
                blocked_ctx = True

        # default scope 不受 context_menu 連發影響，仍可通過
        ok_default, _ = guard.check_rate_limit(user, max_calls=5, window_sec=10.0, scope="default")
        assert blocked_ctx is True
        assert ok_default is True

    asyncio.run(_run())


def test_sherry_core_context_cache_lru_cap() -> None:
    """測試快取超出容量上限時逐出最舊項目 (LRU)，被逐出之輸入會重新呼叫雲端。"""
    from app import orchestrator as _orchestrator

    async def _run() -> None:
        # Arrange: 收緊容量為 3
        core = SherryCore(bot=None)
        mock_cloud = MagicMock()
        calls = {"n": 0}

        async def fake_summarize(text: str) -> str:
            calls["n"] += 1
            return f"內容{text} => 第{calls['n']}次"

        mock_cloud.summarize_text = fake_summarize
        core.cloud_client = mock_cloud

        def make_req(content: str) -> SherryRequest:
            return SherryRequest(
                trace_id="cache-lru",
                source_type="context_menu",
                user_id=70,
                user_name="Tester",
                guild_id=1,
                channel_id=1,
                raw_content=content,
                options={"action": "summarize"},
            )

        with patch.object(_orchestrator, "CONTEXT_RESULT_CACHE_MAX_SIZE", 3):
            # 依序插入 4 筆不同內容，最舊的 A 應在插入第 4 筆時被逐出
            r_a = await core.handle_request(make_req("A"))
            await core.handle_request(make_req("B"))
            await core.handle_request(make_req("C"))
            await core.handle_request(make_req("D"))

            assert len(core._context_result_cache) == 3

            # 重新請求 A：因已逐出，應再次呼叫雲端 (產生不同回覆)
            r_a2 = await core.handle_request(make_req("A"))

        assert r_a.reply_text != r_a2.reply_text
        assert calls["n"] == 5

    asyncio.run(_run())


def test_sherry_core_context_cache_ttl_expiry() -> None:
    """測試快取項目逾時後自動失效，重新呼叫雲端。"""
    from app import orchestrator as _orchestrator

    async def _run() -> None:
        core = SherryCore(bot=None)
        mock_cloud = MagicMock()
        calls = {"n": 0}

        async def fake_summarize(text: str) -> str:
            calls["n"] += 1
            return f"結果#{calls['n']}"

        mock_cloud.summarize_text = fake_summarize
        core.cloud_client = mock_cloud

        def make_req() -> SherryRequest:
            return SherryRequest(
                trace_id="cache-ttl",
                source_type="context_menu",
                user_id=80,
                user_name="Tester",
                guild_id=1,
                channel_id=1,
                raw_content="內文X",
                options={"action": "summarize"},
            )

        r1 = await core.handle_request(make_req())
        key = core._context_cache_key("SUMMARIZE", "內文X")

        # 將快取項目時間戳模擬成已超過 TTL
        core._context_result_cache[key] = (
            core._context_result_cache[key][0] - (_orchestrator.CONTEXT_RESULT_CACHE_TTL_SEC + 1.0),
            r1.reply_text,
        )

        # Act: 再次請求，應判定逾期並重新呼叫
        r2 = await core.handle_request(make_req())

        assert calls["n"] == 2
        assert "CACHE_HIT" not in r2.capabilities_used
        assert r1.reply_text != r2.reply_text

    asyncio.run(_run())


def test_sherry_core_context_cache_vision_image_bytes_distinguishes() -> None:
    """測試 vision 動作以 bytes 提供畫像時，不同圖片內容不會誤撞同一快取鍵。"""
    async def _run() -> None:
        core = SherryCore(bot=None)
        mock_cloud = MagicMock()
        mock_cloud.analyze_image = AsyncMock(side_effect=["視覺A", "視覺B", "視覺A"])
        core.cloud_client = mock_cloud

        def make_req(img_bytes: bytes) -> SherryRequest:
            return SherryRequest(
                trace_id="cache-vision",
                source_type="context_menu",
                user_id=90,
                user_name="Tester",
                guild_id=1,
                channel_id=1,
                raw_content="看圖",
                options={
                    "action": "vision",
                    "image_bytes": img_bytes,
                    "image_url": "https://cdn.example.com/1.png",
                    "prompt": "分析",
                    "mime_type": "image/png",
                },
            )

        r_a = await core.handle_request(make_req(b"\x00\x01"))
        await core.handle_request(make_req(b"\x00\x02"))
        r_c = await core.handle_request(make_req(b"\x00\x01"))

        # 不同 bytes 產生不同鍵；相同 bytes 命中快取
        assert mock_cloud.analyze_image.await_count == 2
        assert r_a.reply_text == r_c.reply_text
        assert "CACHE_HIT" in r_c.capabilities_used
        assert len(core._context_result_cache) == 2

    asyncio.run(_run())


def test_context_cache_lookup_touches_lru_order() -> None:
    """測試查詢命中時會刷新 LRU 順序，最舊項目隨訪問順序正確逐出。"""
    from app import orchestrator as _orchestrator

    async def _run() -> None:
        core = SherryCore(bot=None)
        mock_cloud = MagicMock()
        calls = {"n": 0}

        async def fake_summarize(text: str) -> str:
            calls["n"] += 1
            return f"結果#{calls['n']}"

        mock_cloud.summarize_text = fake_summarize
        core.cloud_client = mock_cloud

        def make_req(content: str, uid: int) -> SherryRequest:
            return SherryRequest(
                trace_id="cache-lru-order",
                source_type="context_menu",
                user_id=uid,
                user_name="Tester",
                guild_id=1,
                channel_id=1,
                raw_content=content,
                options={"action": "summarize"},
            )

        with patch.object(_orchestrator, "CONTEXT_RESULT_CACHE_MAX_SIZE", 3):
            await core.handle_request(make_req("A", 95))
            await core.handle_request(make_req("B", 95))
            await core.handle_request(make_req("C", 95))
            # 再次訪問 A，使 A 刷新為最新；此時最舊者應為 B
            await core.handle_request(make_req("A", 95))
            # 插入 D，逐出最舊的 B
            await core.handle_request(make_req("D", 95))

        assert len(core._context_result_cache) == 3

        # B 已逐出，以不同使用者重新請求 B 應再呼叫雲端一次 (避免觸發頻率限制)
        await core.handle_request(make_req("B", 96))
        assert calls["n"] == 5

    asyncio.run(_run())


def test_sherry_core_context_menu_capability_not_found() -> None:
    """測試當 capability 完全未註冊或為 None 時，安全回傳失敗 SherryResponse 而非崩潰。"""
    async def _run() -> None:
        core = SherryCore(bot=None)
        # 清空所有能力使 CONTEXT_SUMMARIZE 亦為 None
        core.registry._capabilities.clear()

        req = SherryRequest(
            trace_id="ctx-none-cap-01",
            source_type="context_menu",
            user_id=101,
            user_name="Tester",
            guild_id=1,
            channel_id=1,
            raw_content="測試文字",
            options={"action": "unknown_action"},
        )

        resp = await core.handle_request(req)
        assert resp.success is False
        assert "not found or has no handler" in resp.error_message
        assert "❌ 處理失敗" in resp.reply_text

    asyncio.run(_run())


