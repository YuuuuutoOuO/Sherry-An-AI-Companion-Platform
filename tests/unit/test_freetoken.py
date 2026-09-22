"""
tests/unit/test_freetoken.py
============================
單元測試：驗證 FreeToken MoE 推理引擎支援、SSE 串流解析、多模態影像格式與逾時防護。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp

# 將專案根目錄加入 PYTHONPATH
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from handlers.bot_brain.client import OllamaClient
import settings


class TestFreeTokenClient(unittest.IsolatedAsyncioTestCase):
    """測試 OllamaClient 對 FreeToken 後端與 SSE 串流協議的整合。"""

    def test_parse_model_freetoken(self) -> None:
        """測試 freetoken: 前綴解析。"""
        # Arrange
        model_str = "freetoken:mistralai/Mixtral-8x7B-Instruct-v0.1"

        # Act
        backend, actual_model = OllamaClient._parse_model(model_str)

        # Assert
        self.assertEqual(backend, "freetoken")
        self.assertEqual(actual_model, "mistralai/Mixtral-8x7B-Instruct-v0.1")

    def test_parse_model_fallback_default_to_freetoken(self) -> None:
        """測試無前綴之原生 Hugging Face 模型 ID 預設回退至 freetoken。"""
        # Arrange
        model_str = "Qwen/Qwen2.5-72B-Instruct"

        # Act
        backend, actual_model = OllamaClient._parse_model(model_str)

        # Assert
        self.assertEqual(backend, "freetoken")
        self.assertEqual(actual_model, "Qwen/Qwen2.5-72B-Instruct")

    def test_freetoken_url_defaults(self) -> None:
        """測試 FreeToken Base URL 預設讀取 LLM_BASE_URL。"""
        # Arrange & Act
        client = OllamaClient()

        # Assert
        self.assertEqual(client._freetoken_base_url, getattr(settings, "LLM_BASE_URL", "http://localhost:1919/v1").rstrip("/"))

    def test_freetoken_custom_overrides(self) -> None:
        """測試建構子傳入 freetoken_base_url 覆寫。"""
        # Arrange & Act
        client = OllamaClient(freetoken_base_url="http://192.168.1.100:8080/v1")

        # Assert
        self.assertEqual(client._freetoken_base_url, "http://192.168.1.100:8080/v1")

    def test_build_openai_messages_text_only(self) -> None:
        """測試純文字訊息結構。"""
        # Act
        messages = OllamaClient._build_openai_messages("你是名偵探雪莉", "你好")

        # Assert
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0], {"role": "system", "content": "你是名偵探雪莉"})
        self.assertEqual(messages[1], {"role": "user", "content": "你好"})

    def test_build_openai_messages_multimodal(self) -> None:
        """測試多模態（Vision）圖片結構符合 OpenAI image_url 標準。"""
        # Arrange
        images = ["base64fakeimage1", "base64fakeimage2"]

        # Act
        messages = OllamaClient._build_openai_messages("你是名偵探雪莉", "請看這張圖", images=images)

        # Assert
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0], {"role": "system", "content": "你是名偵探雪莉"})
        user_content = messages[1]["content"]
        self.assertIsInstance(user_content, list)
        self.assertEqual(user_content[0], {"type": "text", "text": "請看這張圖"})
        self.assertEqual(
            user_content[1],
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,base64fakeimage1"}},
        )
        self.assertEqual(
            user_content[2],
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,base64fakeimage2"}},
        )

    async def test_stream_openai_compatible_sse_parsing(self) -> None:
        """測試 _stream_openai_compatible 正確逐行解析 SSE 數據流與 [DONE] 訊號。"""
        # Arrange
        client = OllamaClient()

        # 模擬 SSE 數據流
        sse_lines = [
            b'data: {"choices": [{"delta": {"content": "\xe6\xa9\x98"}}]}\n',  # "橘"
            b'data: {"choices": [{"delta": {"content": "\xe9\x9b\xaa"}}]}\n',  # "雪"
            b'data: {"choices": [{"delta": {"content": "\xe8\x8e\x89"}}]}\n',  # "莉"
            b'data: [DONE]\n',
        ]

        class FakeContent:
            def __aiter__(self):
                self._iter = iter(sse_lines)
                return self

            async def __anext__(self):
                try:
                    return next(self._iter)
                except StopIteration:
                    raise StopAsyncIteration

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.content = FakeContent()

        mock_session = MagicMock(spec=aiohttp.ClientSession)
        mock_session.post = MagicMock()
        mock_session.post.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_session.post.return_value.__aexit__ = AsyncMock()

        # Act
        tokens: list[str] = []
        async for token in client._stream_openai_compatible(
            mock_session,
            base_url="http://localhost:8080/v1",
            api_key="",
            actual_model="mistralai/Mixtral-8x7B-Instruct-v0.1",
            system_prompt="你是一個助手",
            user_prompt="你是誰？",
            backend_label="FreeToken",
        ):
            tokens.append(token)

        # Assert
        self.assertEqual(tokens, ["橘", "雪", "莉"])
        self.assertEqual("".join(tokens), "橘雪莉")

    async def test_stream_openai_compatible_error_frame_surfaces(self) -> None:
        """測試上游以 HTTP 200 回傳 SSE 錯誤幀時，不再靜默吞掉而拋出可診斷異常。"""
        # Arrange
        client = OllamaClient()

        sse_lines = [
            b'data: {"error": {"message": "Quota exceeded", "code": 429}}\n',
            b'data: [DONE]\n',
        ]

        class FakeContent:
            def __aiter__(self):
                self._iter = iter(sse_lines)
                return self

            async def __anext__(self):
                try:
                    return next(self._iter)
                except StopIteration:
                    raise StopAsyncIteration

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.content = FakeContent()

        mock_session = MagicMock(spec=aiohttp.ClientSession)
        mock_session.post.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_session.post.return_value.__aexit__ = AsyncMock(return_value=False)

        # Act & Assert
        with self.assertRaises(aiohttp.ClientError):
            async for _ in client._stream_openai_compatible(
                mock_session,
                base_url="http://localhost:8080/v1",
                api_key="",
                actual_model="mistralai/Mixtral-8x7B-Instruct-v0.1",
                system_prompt="你是一個助手",
                user_prompt="你是誰？",
                backend_label="FreeToken",
            ):
                pass

    async def test_chat_stream_ollama_error_frame_surfaces(self) -> None:
        """測試 Ollama api/chat 回傳錯誤幀時不再被靜默吞掉。"""
        # Arrange
        client = OllamaClient()
        sse_lines = [
            b'{"error": "model not loaded"}\n',
        ]

        class FakeContent:
            def __aiter__(self):
                self._iter = iter(sse_lines)
                return self

            async def __anext__(self):
                try:
                    return next(self._iter)
                except StopIteration:
                    raise StopAsyncIteration

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.content = FakeContent()

        mock_session = MagicMock(spec=aiohttp.ClientSession)
        mock_session.post.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_session.post.return_value.__aexit__ = AsyncMock(return_value=False)

        client._get_session = AsyncMock(return_value=mock_session)

        # Act & Assert
        with self.assertRaises(aiohttp.ClientError):
            async for _ in client.chat_stream(
                model="ollama:llama3.2",
                system_prompt="sys",
                user_prompt="usr",
            ):
                pass

    async def test_chat_stream_freetoken_end_to_end(self) -> None:
        """測試 chat_stream() 對 freetoken: 前綴發起串流生成。"""
        # Arrange
        client = OllamaClient()

        # 模擬 stream generator
        async def fake_stream(*args: object, **kwargs: object):
            yield "Hello"
            yield " "
            yield "World"

        with patch.object(client, "_stream_openai_compatible", side_effect=fake_stream):
            # Act
            chunks: list[str] = []
            async for chunk in client.chat_stream(
                model="freetoken:mistralai/Mixtral-8x7B-Instruct-v0.1",
                system_prompt="sys",
                user_prompt="usr",
            ):
                chunks.append(chunk)

            # Assert
            self.assertEqual(chunks, ["Hello", " ", "World"])
            self.assertEqual("".join(chunks), "Hello World")

    async def test_chat_full_text_assembly_via_stream(self) -> None:
        """測試 chat() 自動透過 stream 拼裝完整字串回傳。"""
        # Arrange
        client = OllamaClient()

        async def fake_stream(*args: object, **kwargs: object):
            yield "名"
            yield "偵"
            yield "探"

        with patch.object(client, "_stream_openai_compatible", side_effect=fake_stream):
            # Act
            result = await client.chat(
                model="freetoken:mistralai/Mixtral-8x7B-Instruct-v0.1",
                system_prompt="sys",
                user_prompt="usr",
            )

            # Assert
            self.assertEqual(result, "名偵探")


class TestFreeTokenWebUI(unittest.IsolatedAsyncioTestCase):
    """測試 Web UI 對 FreeToken 端點與服務狀態監控的整合。"""

    async def test_web_ui_models_endpoint(self) -> None:
        """測試 /api/models/freetoken 代理端點。"""
        from web_ui.server import app

        test_models = [{"id": "mistralai/Mixtral-8x7B-Instruct-v0.1"}, {"id": "Qwen/Qwen2.5-72B-Instruct"}]

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": test_models}
        mock_resp.status_code = 200

        with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
            async with app.test_client() as test_client:
                resp = await test_client.get("/api/models/freetoken")
                self.assertEqual(resp.status_code, 200)
                data = await resp.get_json()
                self.assertEqual(data["source"], "freetoken")
                self.assertEqual(len(data["models"]), 2)
                self.assertIn("mistralai/Mixtral-8x7B-Instruct-v0.1", data["models"])
