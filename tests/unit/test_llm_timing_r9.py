"""
tests/unit/test_llm_timing_r9.py
================================
R9 LLM 鏈路時序模型回歸測試（D1–D4）。

驗證：
1. 整體 deadline 與單 chunk 寬限解耦：aiohttp ``total=None`` + ``sock_read=chunk``，
   由外層 ``asyncio.timeout`` 掌握整體逾時。
2. 長生成不會被「total 涵蓋整個串流」硬切，但超過整體 deadline 仍會中止。
3. 取消是真實傳播：``InferenceTask.cancel()`` 會取消底層串流 Task，
   aiohttp 連線立即關閉（假 SSE server 觀測到斷線）。
4. 錯誤分類：4xx（401/403/429）與 SSE error frame 不再被誤判為可重試，
   內容過濾為確定性錯誤。
5. 重試採指數退避＋抖動，逾時重試上限獨立於一般重試。
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from aiohttp import web

import settings
from handlers.bot_brain.client import (
    LLMAuthError,
    LLMContentFiltered,
    LLMRateLimitError,
    LLMStreamError,
    OllamaClient,
)
from handlers.bot_brain.cog import BotBrain, _retry_delay_seconds
from handlers.bot_brain.models import InferenceTask


def _sse(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


def _chunk(content: str) -> bytes:
    return _sse({"choices": [{"delta": {"content": content}}]})


@asynccontextmanager
async def _fake_api(handler: Any) -> AsyncIterator[str]:
    """啟動只掛 /v1/chat/completions 的本機假 API，回傳 base_url。"""
    app = web.Application()
    app.router.add_post("/v1/chat/completions", handler)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    assert site._server is not None and site._server.sockets
    port = site._server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}/v1"
    finally:
        await runner.cleanup()


def _client(base_url: str) -> OllamaClient:
    return OllamaClient(openai_base_url=base_url, openai_api_key="test-key")


async def _consume(client: OllamaClient, **kwargs: Any) -> str:
    parts: list[str] = []
    async for part in client.chat_stream(
        model="openai:gpt-4o",
        system_prompt="sys",
        user_prompt="usr",
        **kwargs,
    ):
        parts.append(part)
    return "".join(parts)


class TestStreamingTimeoutModel:
    def test_request_timeout_decoupled_from_total(self) -> None:
        """aiohttp ClientTimeout.total 必須為 None；單 chunk 寬限等於設定值。"""

        async def _run() -> None:
            captured: dict[str, Any] = {}

            class FakeContent:
                def __aiter__(self) -> Any:
                    return self

                async def __anext__(self) -> bytes:
                    raise StopAsyncIteration

            class FakeResponse:
                status = 200
                content = FakeContent()

                async def __aenter__(self) -> Any:
                    return self

                async def __aexit__(self, *args: Any) -> None:
                    pass

            class FakeSession:
                def post(self, url: str, **kwargs: Any) -> Any:
                    captured["timeout"] = kwargs["timeout"]
                    return FakeResponse()

            client = OllamaClient()
            async for _ in client._stream_openai_compatible(  # type: ignore[arg-type]
                FakeSession(),
                base_url="http://localhost:9999/v1",
                api_key="",
                actual_model="gpt-4o",
                system_prompt="sys",
                user_prompt="usr",
                backend_label="Fake",
                timeout=30.0,
            ):
                pass

            timeout = captured["timeout"]
            assert timeout.total is None
            assert timeout.sock_read == pytest.approx(settings.LLM_STREAM_CHUNK_TIMEOUT)
            assert timeout.connect == pytest.approx(30.0)

        asyncio.run(_run())

    def test_chunk_gap_times_out_without_total_cap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """chunk 間隔超過 sock_read 時，即使整體 deadline 很寬也會中止。"""

        async def handler(request: web.Request) -> web.StreamResponse:
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(_chunk("a"))
            await asyncio.sleep(5)
            return response

        async def _run() -> None:
            monkeypatch.setattr(settings, "LLM_STREAM_CHUNK_TIMEOUT", 0.2)
            monkeypatch.setattr(settings, "LLM_TIMEOUT", 30)
            async with _fake_api(handler) as base_url:
                client = _client(base_url)
                started = asyncio.get_running_loop().time()
                try:
                    with pytest.raises((asyncio.TimeoutError, aiohttp.ClientError)):
                        await _consume(client)
                    elapsed = asyncio.get_running_loop().time() - started
                    assert elapsed < 3.0
                finally:
                    await client.close()

        asyncio.run(_run())

    def test_overall_deadline_terminates_long_generation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """持續有 chunk 的長生成仍受外層 asyncio.timeout 整體 deadline 約束。"""

        async def handler(request: web.Request) -> web.StreamResponse:
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            try:
                for _ in range(60):
                    await response.write(_chunk("x"))
                    await asyncio.sleep(0.05)
            except (ConnectionResetError, asyncio.CancelledError):
                pass
            return response

        async def _run() -> None:
            monkeypatch.setattr(settings, "LLM_STREAM_CHUNK_TIMEOUT", 2.0)
            monkeypatch.setattr(settings, "LLM_TIMEOUT", 0.35)
            async with _fake_api(handler) as base_url:
                client = _client(base_url)
                try:
                    started = asyncio.get_running_loop().time()
                    with pytest.raises(asyncio.TimeoutError):
                        await _consume(client)
                    elapsed = asyncio.get_running_loop().time() - started
                    # 0.35s deadline + 少量排程誤差；絕不能被 3 秒的長生成拖完
                    assert elapsed < 2.0
                finally:
                    await client.close()

        asyncio.run(_run())


class TestCancellationPropagation:
    def test_cancelling_consumer_closes_upstream_connection(self) -> None:
        """取消消費端 Task 時，上游 SSE 連線必須立即關閉（server 觀測到斷線）。"""
        disconnected = asyncio.Event()

        async def handler(request: web.Request) -> web.StreamResponse:
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            try:
                while True:
                    await response.write(_chunk("x"))
                    await asyncio.sleep(0.05)
            except (ConnectionResetError, asyncio.CancelledError):
                pass
            finally:
                disconnected.set()
            return response

        async def _run() -> None:
            async with _fake_api(handler) as base_url:
                client = _client(base_url)

                async def consume() -> None:
                    async for _ in client.chat_stream(
                        model="openai:gpt-4o",
                        system_prompt="sys",
                        user_prompt="usr",
                    ):
                        pass

                consumer = asyncio.create_task(consume())
                await asyncio.sleep(0.2)
                consumer.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await consumer
                await asyncio.wait_for(disconnected.wait(), timeout=3.0)
                await client.close()

        asyncio.run(_run())

    def test_inference_task_cancel_cancels_execution_task(self) -> None:
        """InferenceTask.cancel() 直接 cancel 底層 Task，而非只設旗標。"""

        async def _run() -> None:
            started = asyncio.Event()
            cancelled = asyncio.Event()

            async def request_worker() -> None:
                started.set()
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    cancelled.set()
                    raise

            task = InferenceTask(
                priority=1,
                system_prompt="sys",
                user_prompt="usr",
                callback=AsyncMock(),
                task_id="cancel-direct",
                model="openai:gpt-4o",
            )
            execution = asyncio.create_task(request_worker())
            task.attach_execution_task(execution)
            await asyncio.wait_for(started.wait(), timeout=1.0)

            task.cancel()

            assert task.is_cancelled
            with pytest.raises(asyncio.CancelledError):
                await execution
            assert cancelled.is_set()

        asyncio.run(_run())

    def test_process_task_cancel_stops_stream_and_replies_empty(self) -> None:
        """串流中被取消：底層 generator 收到 CancelledError，任務以空回覆收尾。"""
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def _run() -> None:
            brain = BotBrain(MagicMock())
            callback = AsyncMock()

            async def fake_stream(**kwargs: Any) -> AsyncIterator[str]:
                started.set()
                try:
                    yield "第一段"
                    await asyncio.sleep(10)
                    yield "不該出現"
                except asyncio.CancelledError:
                    cancelled.set()
                    raise

            brain._ollama.chat_stream = fake_stream  # type: ignore[assignment]

            task = InferenceTask(
                priority=1,
                system_prompt="sys",
                user_prompt="usr",
                callback=callback,
                stream_callback=AsyncMock(),
                task_id="cancel-stream",
                model="openai:gpt-4o",
            )
            await brain.enqueue_task(task)
            process = asyncio.create_task(brain._process_task(task, max_retries=1, retry_delay=0.01))
            await asyncio.wait_for(started.wait(), timeout=1.0)
            task.cancel()
            await asyncio.wait_for(process, timeout=2.0)

            assert cancelled.is_set()
            callback.assert_awaited_once_with("")

        asyncio.run(_run())


class TestStructuredErrorClassification:
    def test_http_401_maps_to_auth_error(self) -> None:
        async def handler(request: web.Request) -> web.Response:
            return web.Response(status=401, text='{"error": "invalid api key"}')

        async def _run() -> None:
            async with _fake_api(handler) as base_url:
                client = _client(base_url)
                try:
                    with pytest.raises(LLMAuthError) as exc_info:
                        await _consume(client)
                    assert exc_info.value.retryable is False
                    assert exc_info.value.status == 401
                finally:
                    await client.close()

        asyncio.run(_run())

    def test_http_429_maps_to_rate_limit_error(self) -> None:
        async def handler(request: web.Request) -> web.Response:
            return web.Response(status=429, text="rate limit exceeded")

        async def _run() -> None:
            async with _fake_api(handler) as base_url:
                client = _client(base_url)
                try:
                    with pytest.raises(LLMRateLimitError) as exc_info:
                        await _consume(client)
                    assert exc_info.value.retryable is True
                finally:
                    await client.close()

        asyncio.run(_run())

    def test_http_500_maps_to_retryable_stream_error(self) -> None:
        async def handler(request: web.Request) -> web.Response:
            return web.Response(status=500, text="upstream exploded")

        async def _run() -> None:
            async with _fake_api(handler) as base_url:
                client = _client(base_url)
                try:
                    with pytest.raises(LLMStreamError) as exc_info:
                        await _consume(client)
                    assert exc_info.value.retryable is True
                    assert exc_info.value.status == 500
                finally:
                    await client.close()

        asyncio.run(_run())

    def test_sse_content_filter_frame_is_deterministic(self) -> None:
        async def handler(request: web.Request) -> web.StreamResponse:
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(
                _sse({"error": {"message": "blocked by content policy", "code": 400}})
            )
            return response

        async def _run() -> None:
            async with _fake_api(handler) as base_url:
                client = _client(base_url)
                try:
                    with pytest.raises(LLMContentFiltered) as exc_info:
                        await _consume(client)
                    assert exc_info.value.retryable is False
                finally:
                    await client.close()

        asyncio.run(_run())

    def test_sse_rate_limit_frame_is_retryable(self) -> None:
        async def handler(request: web.Request) -> web.StreamResponse:
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(
                _sse({"error": {"message": "quota exceeded", "code": 429}})
            )
            return response

        async def _run() -> None:
            async with _fake_api(handler) as base_url:
                client = _client(base_url)
                try:
                    with pytest.raises(LLMRateLimitError):
                        await _consume(client)
                finally:
                    await client.close()

        asyncio.run(_run())


class TestRetryPolicy:
    def test_backoff_is_exponential_with_jitter_and_cap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("handlers.bot_brain.cog.random.uniform", lambda a, b: 1.0)
        assert _retry_delay_seconds(1, 2.0, 30.0) == pytest.approx(2.0)
        assert _retry_delay_seconds(2, 2.0, 30.0) == pytest.approx(4.0)
        assert _retry_delay_seconds(3, 2.0, 30.0) == pytest.approx(8.0)
        assert _retry_delay_seconds(10, 2.0, 30.0) == pytest.approx(30.0)

    def test_timeout_retry_cap_is_independent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """逾時重試上限 1 次：即使 max_retries=5 也只會嘗試 2 次。"""
        monkeypatch.setattr(settings, "BOT_BRAIN_MAX_TIMEOUT_RETRIES", 1)

        async def _run() -> None:
            brain = BotBrain(MagicMock())
            calls = 0

            async def fake_chat(**kwargs: Any) -> str:
                nonlocal calls
                calls += 1
                raise asyncio.TimeoutError("simulated timeout")

            brain._ollama.chat = fake_chat  # type: ignore[assignment]
            callback = AsyncMock()
            task = InferenceTask(
                priority=1,
                system_prompt="sys",
                user_prompt="usr",
                callback=callback,
                task_id="timeout-cap",
                model="openai:gpt-4o",
            )
            await brain.enqueue_task(task)
            await brain._process_task(task, max_retries=5, retry_delay=0.0)

            assert calls == 2
            callback.assert_awaited_once_with("")

        asyncio.run(_run())

    def test_auth_error_is_not_retried(self) -> None:
        async def _run() -> None:
            brain = BotBrain(MagicMock())
            calls = 0

            async def fake_chat(**kwargs: Any) -> str:
                nonlocal calls
                calls += 1
                raise LLMAuthError("401 invalid api key", status=401, retryable=False)

            brain._ollama.chat = fake_chat  # type: ignore[assignment]
            callback = AsyncMock()
            task = InferenceTask(
                priority=1,
                system_prompt="sys",
                user_prompt="usr",
                callback=callback,
                task_id="auth-no-retry",
                model="openai:gpt-4o",
            )
            await brain.enqueue_task(task)
            await brain._process_task(task, max_retries=3, retry_delay=0.0)

            assert calls == 1
            callback.assert_awaited_once_with("")

        asyncio.run(_run())
