"""tests/unit/test_openrouter.py
==============================
單元測試：驗證 OpenRouter 後端支援、模型前綴解析與雲端任務派發。
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

# 將專案根目錄加入 PYTHONPATH
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from handlers.bot_brain.client import OllamaClient
from handlers.bot_brain.models import InferenceTask


class TestOpenRouterClient(unittest.IsolatedAsyncioTestCase):
    """測試 OllamaClient 對 openrouter: 前綴的支援。"""

    def test_parse_model_openrouter(self) -> None:
        """測試 openrouter: 前綴解析。"""
        # Arrange
        model_str = "openrouter:gryphe/mythomax-l2-13b"

        # Act
        backend, actual_model = OllamaClient._parse_model(model_str)

        # Assert
        self.assertEqual(backend, "openrouter")
        self.assertEqual(actual_model, "gryphe/mythomax-l2-13b")

    def test_openrouter_url_and_key_defaults(self) -> None:
        """測試 OpenRouter Base URL 與 API Key 的讀取。"""
        # Arrange & Act
        client = OllamaClient()

        # Assert
        self.assertEqual(client._openrouter_base_url, "https://openrouter.ai/api/v1")
        self.assertIsInstance(client._openrouter_api_key, str)

    def test_openrouter_custom_overrides(self) -> None:
        """測試建構子覆寫 OpenRouter 設定。"""
        # Arrange & Act
        client = OllamaClient(
            openrouter_base_url="https://custom.openrouter.ai/v1",
            openrouter_api_key="sk-or-v1-custom-key",
        )

        # Assert
        self.assertEqual(client._openrouter_base_url, "https://custom.openrouter.ai/v1")
        self.assertEqual(client._openrouter_api_key, "sk-or-v1-custom-key")

    async def test_openrouter_chat_routing(self) -> None:
        """測試 chat() 使用 openrouter: 前綴時呼叫 OpenAI 相容請求並帶有 OpenRouter 標頭。"""
        # Arrange
        client = OllamaClient(
            openrouter_base_url="https://openrouter.ai/api/v1",
            openrouter_api_key="sk-or-v1-test-key",
        )

        # Act
        with patch.object(
            client,
            "_request_openai_compatible",
            new_callable=AsyncMock,
            return_value="這是來自 OpenRouter 的回覆！",
        ) as mock_req:
            try:
                reply = await client.chat(
                    model="openrouter:sao10k/l3-euryale-70b",
                    system_prompt="你是一個助手",
                    user_prompt="你好",
                )

                # Assert
                self.assertEqual(reply, "這是來自 OpenRouter 的回覆！")
                mock_req.assert_awaited_once()
                call_kwargs = mock_req.call_args.kwargs
                self.assertEqual(call_kwargs["base_url"], "https://openrouter.ai/api/v1")
                self.assertEqual(call_kwargs["api_key"], "sk-or-v1-test-key")
                self.assertEqual(call_kwargs["actual_model"], "sao10k/l3-euryale-70b")
                self.assertEqual(call_kwargs["backend_label"], "OpenRouter")
            finally:
                await client.close()

    async def test_openrouter_http_headers(self) -> None:
        """測試 _request_openai_compatible 在 OpenRouter 後端時帶入 Referer 與 X-Title。"""
        # Arrange
        client = OllamaClient()
        mock_session = MagicMock()
        mock_post_ctx = AsyncMock()
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.raise_for_status = MagicMock()

        sse_data = [
            b'data: {"choices": [{"delta": {"content": "OpenRouter \xe5\x9b\x9e\xe8\xa6\x86\xe6\x88\x90\xe5\x8a\x9f"}}]}\n',
            b'data: [DONE]\n',
        ]

        class FakeContent:
            def __aiter__(self) -> Any:
                self._iter = iter(sse_data)
                return self

            async def __anext__(self) -> bytes:
                try:
                    return next(self._iter)
                except StopIteration:
                    raise StopAsyncIteration

        mock_resp.content = FakeContent()
        mock_post_ctx.__aenter__.return_value = mock_resp
        mock_session.post.return_value = mock_post_ctx

        # Act
        result = await client._request_openai_compatible(
            session=mock_session,
            base_url="https://openrouter.ai/api/v1",
            api_key="sk-or-v1-test",
            actual_model="gryphe/mythomax-l2-13b",
            system_prompt="sys",
            user_prompt="usr",
            backend_label="OpenRouter",
        )

        # Assert
        self.assertEqual(result, "OpenRouter 回覆成功")
        mock_session.post.assert_called_once()
        _, call_kwargs = mock_session.post.call_args
        headers = call_kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer sk-or-v1-test")
        self.assertEqual(headers["HTTP-Referer"], "https://github.com/YuuuuutoOuO/Sherry-An-AI-Companion-Platform")
        self.assertEqual(headers["X-Title"], "Sherry AI Companion")

    async def test_gemini_payload_excludes_unsupported_params(self) -> None:
        """測試 _stream_openai_compatible 在 Gemini 後端時過濾 presence_penalty 與 frequency_penalty。"""
        # Arrange
        client = OllamaClient()
        mock_session = MagicMock()
        mock_post_ctx = AsyncMock()
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.raise_for_status = MagicMock()

        sse_data = [
            b'data: {"choices": [{"delta": {"content": "Gemini \xe5\x9b\x9e\xe8\xa6\x86\xe6\x88\x90\xe5\x8a\x9f"}}]}\n',
            b'data: [DONE]\n',
        ]

        class FakeContent:
            def __aiter__(self) -> Any:
                self._iter = iter(sse_data)
                return self

            async def __anext__(self) -> bytes:
                try:
                    return next(self._iter)
                except StopIteration:
                    raise StopAsyncIteration

        mock_resp.content = FakeContent()
        mock_post_ctx.__aenter__.return_value = mock_resp
        mock_session.post.return_value = mock_post_ctx

        # Act
        result = await client._request_openai_compatible(
            session=mock_session,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            api_key="AIzaSyTestKey",
            actual_model="gemini-3.6-flash",
            system_prompt="sys",
            user_prompt="usr",
            backend_label="Gemini",
        )

        # Assert
        self.assertEqual(result, "Gemini 回覆成功")
        mock_session.post.assert_called_once()
        _, call_kwargs = mock_session.post.call_args
        payload = call_kwargs["json"]
        self.assertNotIn("presence_penalty", payload)
        self.assertNotIn("frequency_penalty", payload)
        self.assertNotIn("stream_options", payload)
        self.assertEqual(payload["model"], "gemini-3.6-flash")

    async def test_gemini_deprecated_model_alias_migration(self) -> None:
        """測試當傳入已下線的舊版 Gemini 模型名稱 (如 gemini-2.0-flash) 時，自動映射為 gemini-3.5-flash-lite。"""
        client = OllamaClient()
        mock_session = MagicMock()

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.raise_for_status = MagicMock()

        sse_data = [
            b'data: {"choices": [{"delta": {"content": "\xe6\xb8\xac\xe8\xa9\xa6\xe6\x88\x90\xe5\x8a\x9f"}}]}\n',
            b'data: [DONE]\n',
        ]

        class FakeContent:
            def __aiter__(self) -> Any:
                self._iter = iter(sse_data)
                return self

            async def __anext__(self) -> bytes:
                try:
                    return next(self._iter)
                except StopIteration:
                    raise StopAsyncIteration

        mock_resp.content = FakeContent()
        mock_post_ctx = AsyncMock()
        mock_post_ctx.__aenter__.return_value = mock_resp
        mock_session.post.return_value = mock_post_ctx

        result = await client._request_openai_compatible(
            session=mock_session,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            api_key="AIzaSyTestKey",
            actual_model="gemini-2.0-flash",
            system_prompt="sys",
            user_prompt="usr",
            backend_label="Gemini",
        )

        self.assertEqual(result, "測試成功")
        mock_session.post.assert_called_once()
        _, call_kwargs = mock_session.post.call_args
        payload = call_kwargs["json"]
        self.assertEqual(payload["model"], "gemini-3.5-flash-lite")


class TestBotBrainCloudDetection(unittest.IsolatedAsyncioTestCase):
    """測試 BotBrain 正確將 openrouter: 識別為雲端模型（不佔用本地 GPU Lock，並行放行）。"""

    async def test_is_cloud_detection(self) -> None:
        """測試 BotBrain Worker 正確識別雲端前綴並異步放行，不阻塞 Worker。"""
        from handlers.bot_brain.cog import BotBrain

        bot = MagicMock()
        brain = BotBrain(bot)

        # 模擬 _process_task
        processed_tasks: list[str] = []

        async def fake_process(task: InferenceTask, max_retries: int, delay: float) -> None:
            processed_tasks.append(task.model)

        brain._process_task = fake_process  # type: ignore

        # 1. 注入包含 openrouter 與其他雲端前綴之任務
        cloud_prefixes = ["openrouter:anthropic/claude-3", "openai:gpt-4o", "gemini:gemini-1.5-pro"]
        for model_name in cloud_prefixes:
            task = InferenceTask(
                priority=1,
                system_prompt="sys",
                user_prompt="usr",
                callback=AsyncMock(),
                model=model_name,
                task_id=f"task_{model_name}",
            )
            await brain.enqueue_task(task)

        # 執行一個 worker step: 驗證雲端模型被放行至 _background_tasks
        # 取出一個任務測試 worker 邏輯
        worker_task = asyncio.create_task(brain._inference_worker())
        await asyncio.sleep(0.05)
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

        # 驗證所有排入的雲端任務皆已被 worker 提取並放行執行
        self.assertEqual(len(processed_tasks), len(cloud_prefixes))
        self.assertIn("openrouter:anthropic/claude-3", processed_tasks)


class TestGroqClient(unittest.IsolatedAsyncioTestCase):
    """測試 OllamaClient 對 groq: 前綴的支援與相容性。"""

    def test_parse_model_groq(self) -> None:
        """測試 groq: 前綴解析。"""
        # Arrange
        model_str = "groq:llama-3.3-70b-versatile"

        # Act
        backend, actual_model = OllamaClient._parse_model(model_str)

        # Assert
        self.assertEqual(backend, "groq")
        self.assertEqual(actual_model, "llama-3.3-70b-versatile")

    def test_groq_url_and_key_defaults(self) -> None:
        """測試 Groq Base URL 與 API Key 的預設讀取。"""
        # Arrange & Act
        client = OllamaClient()

        # Assert
        self.assertEqual(client._groq_base_url, "https://api.groq.com/openai/v1")
        self.assertIsInstance(client._groq_api_key, str)

    def test_groq_custom_overrides(self) -> None:
        """測試建構子覆寫 Groq 設定。"""
        # Arrange & Act
        client = OllamaClient(
            groq_base_url="https://custom.groq.com/v1",
            groq_api_key="gsk_custom_test_key",
        )

        # Assert
        self.assertEqual(client._groq_base_url, "https://custom.groq.com/v1")
        self.assertEqual(client._groq_api_key, "gsk_custom_test_key")

    async def test_groq_chat_routing(self) -> None:
        """測試 chat() 使用 groq: 前綴時呼叫 OpenAI 相容請求。"""
        # Arrange
        client = OllamaClient(
            groq_base_url="https://api.groq.com/openai/v1",
            groq_api_key="gsk_test_key",
        )

        # Act
        with patch.object(
            client,
            "_request_openai_compatible",
            new_callable=AsyncMock,
            return_value="這是來自 Groq 的回覆！",
        ) as mock_req:
            try:
                reply = await client.chat(
                    model="groq:llama-3.3-70b-versatile",
                    system_prompt="你是一個助手",
                    user_prompt="你好",
                )

                # Assert
                self.assertEqual(reply, "這是來自 Groq 的回覆！")
                mock_req.assert_awaited_once()
                call_kwargs = mock_req.call_args.kwargs
                self.assertEqual(call_kwargs["base_url"], "https://api.groq.com/openai/v1")
                self.assertEqual(call_kwargs["api_key"], "gsk_test_key")
                self.assertEqual(call_kwargs["actual_model"], "llama-3.3-70b-versatile")
                self.assertEqual(call_kwargs["backend_label"], "Groq")
            finally:
                await client.close()

    async def test_groq_stream_diagnostics_cleared_on_http_error(self) -> None:
        """測試當遭遇 HTTP 429/500 等錯誤時，stream_diagnostics 必須清空 finish_reason，避免汙染為 content_filter。"""
        # Arrange
        client = OllamaClient()
        diagnostics: dict[str, Any] = {"finish_reason": "content_filter"}

        mock_resp = AsyncMock()
        mock_resp.status = 429
        mock_resp.text = AsyncMock(return_value="Rate limit exceeded")
        mock_resp.raise_for_status = MagicMock(side_effect=Exception("429 Too Many Requests"))

        mock_post_ctx = AsyncMock()
        mock_post_ctx.__aenter__.return_value = mock_resp
        mock_session = MagicMock()
        mock_session.post.return_value = mock_post_ctx

        # Act & Assert
        with self.assertRaises(Exception):
            async for _ in client._stream_openai_compatible(
                session=mock_session,
                base_url="https://api.groq.com/openai/v1",
                api_key="gsk_test",
                actual_model="llama-3.3-70b-versatile",
                system_prompt="sys",
                user_prompt="usr",
                backend_label="Groq",
                stream_diagnostics=diagnostics,
            ):
                pass

        self.assertIsNone(diagnostics["finish_reason"])
        self.assertEqual(diagnostics["http_status"], 429)

    def test_cloud_detection_and_canonical_name_for_groq(self) -> None:
        """測試 _is_cloud_model 與 _canonical_model_name 正確支援 groq: 前綴。"""
        from handlers.bot_brain.cog import _canonical_model_name, _is_cloud_model

        self.assertTrue(_is_cloud_model("groq:llama-3.3-70b-versatile"))
        self.assertEqual(_canonical_model_name("groq:llama-3.3-70b-versatile"), "llama-3.3-70b-versatile")

