"""
tests/unit/test_bot_brain_worker.py
===================================
BotBrain 推論隊列、優先級排程、任務取消與 GPU/SD 顯存互斥單元測試套件。
驗證：
  1. PriorityQueue 優先級排程：高優先級任務（數字小，如 Priority 0）優先於低優先級（Priority 2）出列。
  2. 幽靈任務取消防護 (Task Cancellation Guard)：排隊期間若 task.is_cancelled 被設為 True，Worker 出列時自動捨棄不推論。
  3. GPU / SD 生圖顯存互斥鎖 (_sd_event)：當 _sd_generating=True 且 _sd_event 未 set 時，本地模型推論任務保持阻塞等待，待生圖完畢後方解除鎖定。
  4. 雲端模型並行分流 (Cloud Model Non-blocking)：帶有 openrouter: 等前綴之任務不佔用本地隊列，以背景 Task 異步放行。
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock
import aiohttp
import pytest

from handlers.bot_brain.client import _GEMINI_THINKING_BUDGET
from handlers.bot_brain.cog import BotBrain
from handlers.bot_brain.models import InferenceTask


@pytest.fixture
def bot_mock() -> MagicMock:
    return MagicMock()


@pytest.fixture
def brain(bot_mock: MagicMock) -> BotBrain:
    b = BotBrain(bot_mock)
    # 取消自動在 __init__ 啟動的真實背景 worker
    if hasattr(b, "_worker_task") and b._worker_task:
        b._worker_task.cancel()
    return b


class TestBotBrainPriorityAndCancellation:
    """測試 BotBrain 優先級搶占與排隊任務取消機制。"""

    def test_priority_queue_ordering(self, brain: BotBrain) -> None:
        """驗證 Priority 0 任務即使較晚入列，也能排在 Priority 2 任務之前被處理。"""
        async def _run() -> None:
            processed_order: list[str] = []

            async def fake_process(task: InferenceTask, max_retries: int, delay: float) -> None:
                processed_order.append(task.task_id)
                brain._queue.task_done()

            brain._process_task = fake_process  # type: ignore

            # 1. 依序放入: Priority 2 (低), Priority 1 (中), Priority 0 (高)
            t_low = InferenceTask(priority=2, system_prompt="s", user_prompt="u", callback=AsyncMock(), task_id="task_low", model="local:qwen")
            t_mid = InferenceTask(priority=1, system_prompt="s", user_prompt="u", callback=AsyncMock(), task_id="task_mid", model="local:qwen")
            t_high = InferenceTask(priority=0, system_prompt="s", user_prompt="u", callback=AsyncMock(), task_id="task_high", model="local:qwen")

            await brain.enqueue_task(t_low)
            await brain.enqueue_task(t_mid)
            await brain.enqueue_task(t_high)

            # 2. 啟動 worker 依序取出 3 個任務
            worker = asyncio.create_task(brain._inference_worker())
            await brain._queue.join()
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

            # 3. 驗證執行順序嚴格為: task_high -> task_mid -> task_low
            assert processed_order == ["task_high", "task_mid", "task_low"]

        asyncio.run(_run())

    def test_cancelled_task_is_discarded_before_inference(self, brain: BotBrain) -> None:
        """排隊中若被標記 is_cancelled=True，Worker 取出時應主動捨棄，不調用 _process_task。"""
        async def _run() -> None:
            processed_tasks: list[str] = []

            async def fake_process(task: InferenceTask, max_retries: int, delay: float) -> None:
                processed_tasks.append(task.task_id)
                brain._queue.task_done()

            brain._process_task = fake_process  # type: ignore

            t_normal = InferenceTask(priority=1, system_prompt="s", user_prompt="u", callback=AsyncMock(), task_id="task_normal", model="local:qwen")
            t_cancelled = InferenceTask(priority=0, system_prompt="s", user_prompt="u", callback=AsyncMock(), task_id="task_cancelled", model="local:qwen")

            # 在佇列中將高優先級任務標記為取消（模擬上層超時或用戶刪除訊息）
            t_cancelled.is_cancelled = True

            await brain.enqueue_task(t_cancelled)
            await brain.enqueue_task(t_normal)

            worker = asyncio.create_task(brain._inference_worker())
            await brain._queue.join()
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

            # 驗證被取消的任務未被送入 _process_task
            assert "task_cancelled" not in processed_tasks
            assert processed_tasks == ["task_normal"]

        asyncio.run(_run())


class TestBotBrainGPUMutexAndCloudDispatch:
    """測試本地模型與 SD 生圖顯存互斥鎖、以及雲端模型並行機制。"""

    def test_gpu_sd_mutex_blocks_local_inference_until_set(self, brain: BotBrain) -> None:
        """當 SD 正在生圖時（_sd_generating=True 且 _sd_event 被 clear），本地推論應阻塞等待。"""
        async def _run() -> None:
            brain._sd_generating = True
            brain._sd_event.clear()

            inference_executed = False

            async def fake_chat(*args: Any, **kwargs: Any) -> str:
                nonlocal inference_executed
                inference_executed = True
                return "回覆完成"

            brain._ollama.chat = fake_chat  # type: ignore


            task = InferenceTask(
                priority=1,
                system_prompt="s",
                user_prompt="u",
                callback=AsyncMock(),
                task_id="task_local_gpu",
                model="qwen2.5:7b",
            )
            await brain.enqueue_task(task)

            # 啟動 process_task 任務
            t = asyncio.create_task(brain._process_task(task, max_retries=1, retry_delay=0.1))

            # 讓事件循環轉動，此時因 SD 鎖清空，推論應被阻塞
            await asyncio.sleep(0.05)
            assert inference_executed is False

            # 解除 SD 生圖鎖，推論任務應隨即恢復並完成
            brain._sd_generating = False
            brain._sd_event.set()

            await asyncio.wait_for(t, timeout=1.0)
            assert inference_executed is True

        asyncio.run(_run())

    def test_cloud_models_bypass_sequential_gpu_queue(self, brain: BotBrain) -> None:
        """雲端模型（如 openrouter:）應由 Worker 建立非同步背景任務並行放行，不阻塞主循環。"""
        async def _run() -> None:
            processed: list[str] = []

            async def fake_process(task: InferenceTask, max_retries: int, delay: float) -> None:
                # 模擬耗時網路調用
                await asyncio.sleep(0.05)
                processed.append(task.task_id)
                brain._queue.task_done()

            brain._process_task = fake_process  # type: ignore

            # 注入兩個雲端任務
            t1 = InferenceTask(priority=1, system_prompt="s", user_prompt="u", callback=AsyncMock(), task_id="cloud_1", model="openrouter:anthropic/claude-3")
            t2 = InferenceTask(priority=1, system_prompt="s", user_prompt="u", callback=AsyncMock(), task_id="cloud_2", model="openai:gpt-4o")

            await brain.enqueue_task(t1)
            await brain.enqueue_task(t2)

            worker = asyncio.create_task(brain._inference_worker())
            # 等待佇列清空（雲端任務取出後立即觸發 background task，佇列瞬間歸零）
            await brain._queue.join()
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

            # 等待背景任務全部收尾
            if brain._background_tasks:
                await asyncio.gather(*list(brain._background_tasks), return_exceptions=True)

            assert "cloud_1" in processed
            assert "cloud_2" in processed

        asyncio.run(_run())


class TestBotBrainGeminiFallbackAndTokenProtection:
    """測試 Gemini 視覺模型容錯備援、503 退避與輸出 Token 空間保障。"""

    def test_gemini_token_elevation_in_client(self) -> None:
        """驗證 Gemini 的 max_tokens 會自動高於下限保護與當前思考預算，避免思考吃光額度。"""
        from handlers.bot_brain.client import OllamaClient
        import settings

        client = OllamaClient()
        recorded_payloads: list[dict[str, Any]] = []

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
            def post(self, url: str, json: dict[str, Any], **kwargs: Any) -> Any:
                recorded_payloads.append(json)
                return FakeResponse()

        async def _run() -> None:
            fake_session = FakeSession()
            # 1. 傳入偏低額度 (1024) 時，應自動拉高到足以容納思考 + 輸出
            async for _ in client._stream_openai_compatible(
                fake_session,  # type: ignore
                base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                api_key="test_key",
                actual_model="gemini-3.5-flash-lite",
                system_prompt="sys",
                user_prompt="usr",
                backend_label="Gemini",
                max_tokens=1024,
            ):
                pass

            # 2. 明確指定 16384 時，額度已高於任何思考預算，應原樣保留，不被覆寫
            async for _ in client._stream_openai_compatible(
                fake_session,  # type: ignore
                base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                api_key="test_key",
                actual_model="gemini-3.5-flash-lite",
                system_prompt="sys",
                user_prompt="usr",
                backend_label="Gemini",
                max_tokens=16384,
            ):
                pass

            assert len(recorded_payloads) == 2
            # 額度必須同時滿足兩個條件：不少於下限保護，且高於當前思考預算
            # （否則思考會吃光額度導致空輸出或回覆被截斷）。
            effort = settings.GEMINI_REASONING_EFFORT
            thinking_budget = _GEMINI_THINKING_BUDGET.get(effort)
            assert recorded_payloads[0]["max_tokens"] >= settings.GEMINI_MIN_OUTPUT_TOKENS
            if thinking_budget is not None:
                assert recorded_payloads[0]["max_tokens"] > thinking_budget
            # 額度已高於任何思考預算時，應原樣保留
            assert recorded_payloads[1]["max_tokens"] == 16384

        asyncio.run(_run())

    def test_gemini_multimodal_dynamic_fallback_on_failure(self, brain: BotBrain) -> None:
        """驗證含有圖片的多模態 Gemini 任務在 attempt 1 失敗時，attempt 2 能自動切換至備援模型並成功回傳。"""
        async def _run() -> None:
            models_called: list[str] = []

            async def fake_chat(model: str, *args: Any, **kwargs: Any) -> str:
                models_called.append(model)
                if "3.5-flash-lite" in model:
                    # 模擬第一次呼叫 3.5-flash-lite 遇到 503 尖峰
                    return ""
                # 第二次呼叫備援模型 3.8-flash 成功回傳
                return "圖片解析成功：少女正在喝咖啡"

            brain._ollama.chat = fake_chat  # type: ignore

            callback_mock = AsyncMock()
            task = InferenceTask(
                priority=1,
                system_prompt="sys",
                user_prompt="usr",
                callback=callback_mock,
                task_id="gemini_img_task",
                model="gemini:gemini-3.5-flash-lite",
                images=["data:image/jpeg;base64,..."],
            )

            await brain.enqueue_task(task)
            await brain._process_task(task, max_retries=3, retry_delay=0.01)

            # 驗證第一次呼叫原模型，第二次自動切換至備援模型 gemini:gemini-3.8-flash
            assert len(models_called) == 2
            assert models_called[0] == "gemini:gemini-3.5-flash-lite"
            assert models_called[1] == "gemini:gemini-3.8-flash"
            callback_mock.assert_awaited_once_with("圖片解析成功：少女正在喝咖啡")

        asyncio.run(_run())

    def test_gemini_three_stage_consecutive_fallback(self, brain: BotBrain) -> None:
        """驗證當 attempt 1 與 attempt 2 皆失敗時，attempt 3 能切換至第三備援模型 (gemini-3.6-flash)。"""
        async def _run() -> None:
            models_called: list[str] = []

            async def fake_chat(model: str, *args: Any, **kwargs: Any) -> str:
                models_called.append(model)
                if "3.5-flash-lite" in model or "3.8-flash" in model:
                    return ""
                return "第三次備援成功：gemini-3.6-flash"

            brain._ollama.chat = fake_chat  # type: ignore

            callback_mock = AsyncMock()
            task = InferenceTask(
                priority=1,
                system_prompt="sys",
                user_prompt="usr",
                callback=callback_mock,
                task_id="gemini_three_stage_task",
                model="gemini:gemini-3.5-flash-lite",
                images=["data:image/jpeg;base64,..."],
            )

            await brain.enqueue_task(task)
            await brain._process_task(task, max_retries=3, retry_delay=0.01)

            assert len(models_called) == 3
            assert models_called[0] == "gemini:gemini-3.5-flash-lite"
            assert models_called[1] == "gemini:gemini-3.8-flash"
            assert models_called[2] == "gemini:gemini-3.6-flash"
            callback_mock.assert_awaited_once_with("第三次備援成功：gemini-3.6-flash")

        asyncio.run(_run())

    def test_local_model_offline_fallback_to_cloud(self, brain: BotBrain) -> None:
        """驗證當本地模型連線失敗 (Port 1919 未啟動) 時，BotBrain 能安全降級切換至雲端備援模型。"""
        async def _run() -> None:
            models_called: list[str] = []

            async def fake_chat(model: str, *args: Any, **kwargs: Any) -> str:
                models_called.append(model)
                if model.startswith("freetoken:"):
                    raise aiohttp.ClientConnectorError(
                        connection_key=MagicMock(),
                        os_error=OSError(10061, "Connect call failed ('127.0.0.1', 1919)"),
                    )
                return "雲端備援成功回覆"

            brain._ollama.chat = fake_chat  # type: ignore

            callback_mock = AsyncMock()
            task = InferenceTask(
                priority=1,
                system_prompt="sys",
                user_prompt="usr",
                callback=callback_mock,
                task_id="local_offline_fallback_task",
                model="freetoken:Gemma-4-26B-A4B-NVFP4",
            )

            await brain.enqueue_task(task)
            await brain._process_task(task, max_retries=3, retry_delay=0.01)

            assert len(models_called) >= 2
            assert models_called[0] == "freetoken:Gemma-4-26B-A4B-NVFP4"
            assert models_called[1].startswith("gemini:")
            callback_mock.assert_awaited_once_with("雲端備援成功回覆")

        asyncio.run(_run())

    def test_content_filter_fails_fast_without_fallback_retry(self, brain: BotBrain) -> None:
        """驗證當遭遇供應商安全過濾 (PROHIBITED_CONTENT) 時，BotBrain 立即終止重試且不切換 fallback。"""
        async def _run() -> None:
            models_called: list[str] = []

            async def fake_chat(model: str, *args: Any, **kwargs: Any) -> str:
                models_called.append(model)
                brain._ollama.last_stream_diagnostics = {
                    "finish_reason": "content_filter: PROHIBITED_CONTENT",
                }
                return ""

            brain._ollama.chat = fake_chat  # type: ignore

            callback_mock = AsyncMock()
            task = InferenceTask(
                priority=1,
                system_prompt="sys",
                user_prompt="usr with prohibited words",
                callback=callback_mock,
                task_id="content_filtered_task",
                model="gemini:gemini-3.6-flash",
            )

            await brain.enqueue_task(task)
            await brain._process_task(task, max_retries=3, retry_delay=0.01)

            # 驗證只嘗試了 1 次即中斷，沒有浪費次數重試或切換 fallback 模型
            assert len(models_called) == 1
            assert models_called[0] == "gemini:gemini-3.6-flash"
            assert task.is_content_filtered is True
            callback_mock.assert_awaited_once_with("")

        asyncio.run(_run())



