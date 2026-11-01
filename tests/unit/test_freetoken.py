"""
tests/unit/test_freetoken.py
============================
單元測試：驗證 FreeToken MoE 推理引擎支援、SSE 串流解析、多模態影像格式與逾時防護。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp

# 將專案根目錄加入 PYTHONPATH
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from handlers.bot_brain.client import (
    OllamaClient,
    _is_content_filtered,
    _normalized_finish_reason,
)
from handlers.bot_brain.cog import _alternative_models, _fallback_candidates
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

    @staticmethod
    def _build_fake_session(sse_lines: list[bytes]) -> MagicMock:
        """建立可回放指定 SSE 行的假 aiohttp session。"""

        class FakeContent:
            def __aiter__(self) -> FakeContent:
                self._iter = iter(sse_lines)
                return self

            async def __anext__(self) -> bytes:
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
        return mock_session

    @staticmethod
    def _build_capturing_session(sse_lines: list[bytes]) -> tuple[MagicMock, dict[str, Any]]:
        """建立假 session，並把實際送出的 JSON payload 記錄到回傳的 box。"""
        box: dict[str, Any] = {}

        class FakeContent:
            def __aiter__(self) -> FakeContent:
                self._iter = iter(sse_lines)
                return self

            async def __anext__(self) -> bytes:
                try:
                    return next(self._iter)
                except StopIteration:
                    raise StopAsyncIteration

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.content = FakeContent()

        session = MagicMock(spec=aiohttp.ClientSession)

        def _post(*args: object, **kwargs: object) -> MagicMock:
            box["payload"] = kwargs.get("json")
            ctx = MagicMock()
            ctx.__aenter__ = AsyncMock(return_value=mock_resp)
            ctx.__aexit__ = AsyncMock(return_value=False)
            return ctx

        session.post.side_effect = _post
        return session, box

    @staticmethod
    def _ok_sse() -> list[bytes]:
        return [
            b'data: {"choices":[{"delta":{"content":"ok"}}]}\n',
            b"data: [DONE]\n",
        ]

    async def test_gemini_request_sends_low_reasoning_effort(self) -> None:
        """Gemini 預設應帶 low 思考強度，避免思考吃光 max_tokens 導致空回應。"""
        client = OllamaClient()
        session, box = self._build_capturing_session(self._ok_sse())
        client._get_session = AsyncMock(return_value=session)

        with patch.object(settings, "GEMINI_REASONING_EFFORT", "low"), \
             patch.object(settings, "GEMINI_MIN_OUTPUT_TOKENS", 4096):
            result = await client.chat(
                model="gemini:gemini-3.5-flash-lite",
                system_prompt="sys",
                user_prompt="usr",
            )

        self.assertEqual(result, "ok")
        payload = box["payload"]
        self.assertEqual(payload["reasoning_effort"], "low")
        # max_tokens 必須大於 low 對應的 1024 思考 Token，否則思考會吃光輸出額度
        self.assertGreater(payload["max_tokens"], 1024)

    async def test_gemini_request_raises_max_tokens_above_thinking_budget(self) -> None:
        """reasoning_effort=medium（8192 思考）時 max_tokens 必須被拉高到思考之上。"""
        client = OllamaClient()
        session, box = self._build_capturing_session(self._ok_sse())
        client._get_session = AsyncMock(return_value=session)

        with patch.object(settings, "GEMINI_REASONING_EFFORT", "medium"), \
             patch.object(settings, "GEMINI_MIN_OUTPUT_TOKENS", 4096):
            await client.chat(
                model="gemini:gemini-3.5-flash-lite",
                system_prompt="sys",
                user_prompt="usr",
            )

        payload = box["payload"]
        self.assertEqual(payload["reasoning_effort"], "medium")
        self.assertGreater(payload["max_tokens"], 8192)

    async def test_gemini_default_reasoning_effort_is_medium(self) -> None:
        """出貨預設思考強度應為 medium，維持雪莉的推論深度與回應篇幅。

        額度不足的問題改由自動拉高 max_tokens 解決，不應以降思考強度來換取額度。
        """
        client = OllamaClient()
        session, box = self._build_capturing_session(self._ok_sse())
        client._get_session = AsyncMock(return_value=session)

        with patch.object(settings, "GEMINI_MIN_OUTPUT_TOKENS", 4096):
            await client.chat(
                model="gemini:gemini-3.5-flash-lite",
                system_prompt="sys",
                user_prompt="usr",
            )

        payload = box["payload"]
        self.assertEqual(payload["reasoning_effort"], "medium")
        # 額度 = 8192 思考 + 輸出餘裕，必須留出可觀的輸出空間
        self.assertGreaterEqual(payload["max_tokens"], 8192 + 4096)

    async def test_gemini_reasoning_effort_can_be_disabled(self) -> None:
        """設為空字串時不送 reasoning_effort，沿用模型預設。"""
        client = OllamaClient()
        session, box = self._build_capturing_session(self._ok_sse())
        client._get_session = AsyncMock(return_value=session)

        with patch.object(settings, "GEMINI_REASONING_EFFORT", ""):
            await client.chat(
                model="gemini:gemini-3.5-flash-lite",
                system_prompt="sys",
                user_prompt="usr",
            )

        self.assertNotIn("reasoning_effort", box["payload"])

    async def test_non_gemini_request_omits_reasoning_effort(self) -> None:
        """非 Gemini 後端不應收到 Gemini 專屬參數。"""
        client = OllamaClient()
        session, box = self._build_capturing_session(self._ok_sse())
        client._get_session = AsyncMock(return_value=session)

        with patch.object(settings, "GEMINI_REASONING_EFFORT", "low"):
            await client.chat(
                model="freetoken:mistralai/Mixtral-8x7B-Instruct-v0.1",
                system_prompt="sys",
                user_prompt="usr",
            )

        self.assertNotIn("reasoning_effort", box["payload"])

    async def test_content_filter_finish_reason_is_normalized(self) -> None:
        """Gemini 會回傳帶原因後綴的 finish_reason，需正規化後才能判斷。"""
        self.assertEqual(
            _normalized_finish_reason("content_filter: PROHIBITED_CONTENT"),
            "content_filter",
        )
        self.assertEqual(_normalized_finish_reason("stop"), "stop")
        self.assertIsNone(_normalized_finish_reason(None))

    async def test_is_content_filtered_detects_prohibited_content(self) -> None:
        """content_filter 診斷應被辨識為確定性過濾失敗。"""
        self.assertTrue(_is_content_filtered({"finish_reason": "content_filter: PROHIBITED_CONTENT"}))
        self.assertFalse(_is_content_filtered({"finish_reason": "stop"}))
        self.assertFalse(_is_content_filtered({"finish_reason": None}))
        self.assertFalse(_is_content_filtered({}))

    async def test_content_filter_empty_reply_is_explained(self) -> None:
        """空回應且被安全過濾時，應明確輸出成因說明而非靜默。"""
        client = OllamaClient()
        sse_lines = [
            b'data: {"choices":[{"delta":{},"finish_reason":"content_filter: PROHIBITED_CONTENT"}]}\n',
            b"data: [DONE]\n",
        ]
        session = self._build_fake_session(sse_lines)
        client._get_session = AsyncMock(return_value=session)

        with self.assertLogs("handlers.bot_brain.client", level="WARNING") as captured:
            result = await client.chat(
                model="gemini:gemini-3.5-flash-lite",
                system_prompt="sys",
                user_prompt="usr",
            )

        self.assertEqual(result, "")
        joined = "\n".join(captured.output)
        self.assertIn("空回應主因為安全過濾", joined)
        self.assertIn("PROHIBITED_CONTENT", joined)
        # 診斷資訊需保留給 BotBrain 判斷是否值得重試
        self.assertTrue(_is_content_filtered(client.last_stream_diagnostics))

    async def test_successful_reply_clears_blocked_flag(self) -> None:
        """成功回應不應殘留過濾診斷，避免誤判後續重試策略。"""
        client = OllamaClient()
        session = self._build_fake_session(self._ok_sse())
        client._get_session = AsyncMock(return_value=session)
        client.last_stream_diagnostics = {"finish_reason": "content_filter"}

        await client.chat(
            model="gemini:gemini-3.5-flash-lite",
            system_prompt="sys",
            user_prompt="usr",
        )

        self.assertFalse(_is_content_filtered(client.last_stream_diagnostics))

    async def test_alternative_models_excludes_current(self) -> None:
        """替代模型清單應排除與任務/當前模型同名的項目。"""
        with patch.object(
            settings,
            "FALLBACK_ENABLED",
            True,
        ), patch.object(
            settings,
            "FALLBACK_MODELS",
            ["gemini:gemini-3.5-flash-lite", "gemini:gemini-3.8-flash"],
        ), patch.object(settings, "LIGHT_MODEL", "gemini:gemini-3.8-flash"):
            models = _alternative_models(
                "gemini:gemini-3.5-flash-lite", "gemini:gemini-3.5-flash-lite"
            )

        self.assertEqual(models, ["gemini:gemini-3.8-flash"])

    async def test_alternative_models_empty_when_all_duplicates(self) -> None:
        """所有候選都與當前模型同名時，應回傳空清單（不值得重試）。"""
        with patch.object(
            settings,
            "FALLBACK_ENABLED",
            True,
        ), patch.object(
            settings,
            "FALLBACK_MODELS",
            ["gemini-3.5-flash-lite", "gemini:gemini-3.5-flash-lite"],
        ), patch.object(settings, "LIGHT_MODEL", "gemini-3.5-flash-lite"):
            self.assertEqual(
                _alternative_models(
                    "gemini:gemini-3.5-flash-lite", "gemini:gemini-3.5-flash-lite"
                ),
                [],
            )

    async def test_fallback_excludes_equivalent_model_name(self) -> None:
        """備援模型須剔除與原模型同名（僅前綴不同）的項目。"""
        with patch.object(
            settings,
            "FALLBACK_ENABLED",
            True,
        ), patch.object(
            settings,
            "FALLBACK_MODELS",
            [
                "gemini:gemini-3.5-flash-lite",
                "gemini-3.5-flash-lite",
                "gemini:gemini-3.8-flash",
                "gemini:gemini-3.6-flash",
            ],
        ):
            candidates = _fallback_candidates(
                "gemini:gemini-3.5-flash-lite", "gemini:gemini-3.5-flash-lite"
            )

        self.assertEqual(
            candidates, ["gemini:gemini-3.8-flash", "gemini:gemini-3.6-flash"]
        )

    async def test_fallback_empty_when_all_duplicates(self) -> None:
        """備援清單若全為同一模型的不同前綴寫法，應回傳空清單。"""
        with patch.object(
            settings,
            "FALLBACK_ENABLED",
            True,
        ), patch.object(
            settings,
            "FALLBACK_MODELS",
            ["gemini-3.5-flash-lite", "gemini:gemini-3.5-flash-lite"],
        ):
            self.assertEqual(
                _fallback_candidates(
                    "gemini:gemini-3.5-flash-lite", "gemini:gemini-3.5-flash-lite"
                ),
                [],
            )

    async def test_fallback_disabled_returns_empty(self) -> None:
        """FALLBACK_ENABLED=False 時應無備援，純地端部署不會外送內容。"""
        with patch.object(
            settings,
            "FALLBACK_ENABLED",
            False,
        ), patch.object(
            settings,
            "FALLBACK_MODELS",
            ["gemini:gemini-3.5-flash-lite", "ollama:llama3.2:3b"],
        ):
            self.assertEqual(
                _fallback_candidates("ollama:llama3.1:8b", "ollama:llama3.1:8b"),
                [],
            )

    async def test_fallback_supports_mixed_local_then_cloud(self) -> None:
        """備援序列可混用本地與雲端，地端優先雲端救援。"""
        with patch.object(
            settings,
            "FALLBACK_ENABLED",
            True,
        ), patch.object(
            settings,
            "FALLBACK_MODELS",
            ["ollama:qwen2.5:3b", "gemini:gemini-3.5-flash-lite"],
        ):
            candidates = _fallback_candidates("ollama:llama3.1:8b", "ollama:llama3.1:8b")

        self.assertEqual(
            candidates, ["ollama:qwen2.5:3b", "gemini:gemini-3.5-flash-lite"]
        )

    async def test_fallback_handles_none_and_empty_entries(self) -> None:
        """備援清單含 None 或空字串時應安全略過。"""
        with patch.object(
            settings,
            "FALLBACK_ENABLED",
            True,
        ), patch.object(
            settings,
            "FALLBACK_MODELS",
            ["", "   ", "gemini:gemini-3.5-flash-lite"],
        ):
            candidates = _fallback_candidates("ollama:llama3.1:8b", "ollama:llama3.1:8b")

        self.assertEqual(candidates, ["gemini:gemini-3.5-flash-lite"])

    async def _collect_diagnostics(
        self,
        sse_lines: list[bytes],
        backend_label: str,
        base_url: str,
    ) -> dict[str, object]:
        """跑一次串流並回傳 _request_openai_compatible 寫入的診斷資訊。"""
        client = OllamaClient()
        session = self._build_fake_session(sse_lines)
        captured: dict[str, dict[str, object]] = {}

        original = client._stream_openai_compatible

        async def spy(*args: object, **kwargs: object):
            captured["sink"] = kwargs.get("stream_diagnostics")
            async for chunk in original(*args, **kwargs):  # type: ignore[arg-type]
                yield chunk

        with patch.object(client, "_stream_openai_compatible", side_effect=spy):
            result = await client._request_openai_compatible(
                session,
                base_url=base_url,
                api_key="key",
                actual_model="gemini-3.5-flash-lite",
                system_prompt="sys",
                user_prompt="usr",
                backend_label=backend_label,
            )
        self.assertEqual(result, "")
        sink = captured["sink"]
        self.assertIsInstance(sink, dict)
        return sink

    async def test_empty_reply_diagnostics_capture_safety_block(self) -> None:
        """空回應時應回報 finish_reason、usage 與被封鎖的安全類別。"""
        sse_lines = [
            b'data: {"choices":[{"delta":{},"finish_reason":"content_filter"}],'
            b'"usage":{"prompt_tokens":120,"completion_tokens":0,"total_tokens":120},'
            b'"safety_ratings":[{"category":"HARM_CATEGORY_DANGEROUS_CONTENT","blocked":true}]}\n',
            b"data: [DONE]\n",
        ]

        with self.assertLogs("handlers.bot_brain.client", level="WARNING") as captured:
            diagnostics = await self._collect_diagnostics(
                sse_lines,
                backend_label="Gemini",
                base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            )

        joined = "\n".join(captured.output)
        self.assertEqual(diagnostics["finish_reason"], "content_filter")
        self.assertEqual(diagnostics["completion_tokens"], 0)
        self.assertEqual(
            diagnostics["blocked_safety_categories"], ["HARM_CATEGORY_DANGEROUS_CONTENT"]
        )
        self.assertIn("finish_reason=content_filter", joined)
        self.assertIn("HARM_CATEGORY_DANGEROUS_CONTENT", joined)

    async def test_empty_reply_diagnostics_capture_length_truncation(self) -> None:
        """思考 Token 耗盡 max_tokens 時應記錄 finish_reason=length 與 0 completion。"""
        sse_lines = [
            b'data: {"choices":[{"delta":{},"finish_reason":"length"}],'
            b'"usage":{"prompt_tokens":900,"completion_tokens":0,"total_tokens":900}}\n',
            b"data: [DONE]\n",
        ]

        with self.assertLogs("handlers.bot_brain.client", level="WARNING") as captured:
            diagnostics = await self._collect_diagnostics(
                sse_lines,
                backend_label="Gemini",
                base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            )

        self.assertEqual(diagnostics["finish_reason"], "length")
        self.assertEqual(diagnostics["completion_tokens"], 0)
        self.assertIn("finish_reason=length", "\n".join(captured.output))
        self.assertIn("思考 Token 耗盡", "\n".join(captured.output))

    async def test_empty_reply_diagnostics_count_empty_and_malformed_frames(self) -> None:
        """空回應時應計數空白 delta 與畸形 SSE 幀以區分異常來源。"""
        sse_lines = [
            b'data: {"choices":[{"delta":{}}]}\n',
            b'data: {"choices":[{"delta":{}}]}\n',
            b"data: {not json}\n",
            b"data: [DONE]\n",
        ]

        with self.assertLogs("handlers.bot_brain.client", level="WARNING"):
            diagnostics = await self._collect_diagnostics(
                sse_lines,
                backend_label="Gemini",
                base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            )

        self.assertIsNone(diagnostics["finish_reason"])
        self.assertEqual(diagnostics["empty_delta_frames"], 2)
        self.assertEqual(diagnostics["malformed_frames"], 1)

    async def test_prompt_feedback_safety_ratings_are_captured(self) -> None:
        """串流中的 prompt_feedback 與 safety_ratings 應被解析進診斷資訊。"""
        sse_lines = [
            b'data: {"prompt_feedback":{"blockReason":"SAFETY"},'
            b'"safety_ratings":[{"category":"HARM_CATEGORY_HATE_SPEECH","blocked":true}]}\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"content_filter"}]}\n',
            b"data: [DONE]\n",
        ]

        with self.assertLogs("handlers.bot_brain.client", level="WARNING"):
            diagnostics = await self._collect_diagnostics(
                sse_lines,
                backend_label="Gemini",
                base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            )

        feedback = diagnostics["prompt_feedback"]
        self.assertIsInstance(feedback, dict)
        self.assertEqual(feedback.get("blockReason"), "SAFETY")
        self.assertEqual(
            diagnostics["blocked_safety_categories"], ["HARM_CATEGORY_HATE_SPEECH"]
        )

    async def test_reasoning_only_output_yields_thought_tag(self) -> None:
        """僅有 reasoning_content 時應輸出 thought 標籤而非誤判為空回應。"""
        client = OllamaClient()
        sse_lines = [
            b'data: {"choices":[{"delta":{"reasoning_content":"'
            + "思考中".encode("utf-8")
            + b'"}}]}\n',
            b"data: [DONE]\n",
        ]
        session = self._build_fake_session(sse_lines)
        client._get_session = AsyncMock(return_value=session)

        result = await client.chat(
            model="gemini:gemini-3.5-flash-lite",
            system_prompt="sys",
            user_prompt="usr",
        )

        self.assertIn("thought", result)

    async def test_whitespace_only_output_is_flagged_as_degenerate(self) -> None:
        """只吐出空白字元（有串流但無實質內容）應記錄為退化輸出。"""
        client = OllamaClient()
        sse_lines = [
            b'data: {"choices":[{"delta":{"content":"   \\n  "}}]}\n',
            b"data: [DONE]\n",
        ]
        session = self._build_fake_session(sse_lines)
        client._get_session = AsyncMock(return_value=session)

        with self.assertLogs("handlers.bot_brain.client", level="WARNING") as captured:
            result = await client.chat(
                model="gemini:gemini-3.5-flash-lite",
                system_prompt="sys",
                user_prompt="usr",
            )

        self.assertEqual(result, "")
        self.assertIn("回傳退化內容", "\n".join(captured.output))
        self.assertNotIn("回傳空白字串", "\n".join(captured.output))

    async def test_successful_reply_emits_no_warning(self) -> None:
        """正常回覆不應觸發任何空回應告警。"""
        client = OllamaClient()
        sse_lines = [
            b'data: {"choices":[{"delta":{"content":"' + "雪莉".encode("utf-8") + b'"}}]}\n',
            b'data: {"choices":[{"delta":{"content":"' + "在這裡".encode("utf-8")
            + b'"},"finish_reason":"stop"}],'
            b'"usage":{"prompt_tokens":10,"completion_tokens":4,"total_tokens":14}}\n',
            b"data: [DONE]\n",
        ]
        session = self._build_fake_session(sse_lines)
        client._get_session = AsyncMock(return_value=session)

        logger_name = "handlers.bot_brain.client"
        with self.assertLogs(logger_name, level="INFO") as captured:
            result = await client.chat(
                model="gemini:gemini-3.5-flash-lite",
                system_prompt="sys",
                user_prompt="usr",
            )

        self.assertEqual(result, "雪莉在這裡")
        self.assertNotIn("回傳空白字串", "\n".join(captured.output))
        self.assertNotIn("回傳退化內容", "\n".join(captured.output))

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
