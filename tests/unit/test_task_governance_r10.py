"""R10 任務治理與併發隔離回歸測試。

涵蓋：
1. 雲端任務全域／per-user 雙層 semaphore。
2. 本地重試排回佇列（不 sleep 佔住 GPU worker）與 attempt 續跑。
3. Worker 指數退避與降級標記。
4. 觀測寫入佇列有界、溢出計數與關機 flush。
5. 插話背景任務強引用與卸載取消。
6. 專用執行器（heavy_io_pool/db_pool）與裸 run_in_executor(None) 掃描。
"""

from __future__ import annotations

import asyncio
import queue as pyqueue
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import settings
from core.concurrency import (
    db_pool,
    heavy_io_pool,
    pending_background_writes,
    schedule_in_pool,
)
from handlers.bot_brain import cog as bot_brain_cog
from handlers.bot_brain.cog import BotBrain, _worker_backoff_delay
from handlers.bot_brain.models import InferenceTask

ROOT = Path(__file__).resolve().parents[2]


def _make_task(user_id: int | str | None = None, model: str = "openai:gpt-4o") -> InferenceTask:
    return InferenceTask(
        priority=1,
        system_prompt="system",
        user_prompt="user",
        callback=AsyncMock(),
        task_id=f"task-{user_id}",
        model=model,
        user_id=user_id,
    )


def test_cloud_tasks_respect_global_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    """雲端任務同時執行數不得超過 BOT_BRAIN_CLOUD_MAX_CONCURRENCY。"""
    monkeypatch.setattr(settings, "BOT_BRAIN_CLOUD_MAX_CONCURRENCY", 2)
    monkeypatch.setattr(settings, "BOT_BRAIN_CLOUD_PER_USER_CONCURRENCY", 4)

    async def _run() -> None:
        brain = BotBrain(MagicMock())
        active = 0
        peak = 0

        async def fake_process(task: InferenceTask, max_retries: int, retry_delay: float, **kwargs: object) -> None:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.02)
            finally:
                active -= 1

        brain._process_task = fake_process  # type: ignore[method-assign]
        tasks = [_make_task(user_id=i) for i in range(6)]
        await asyncio.wait_for(
            asyncio.gather(*(brain._run_cloud_task(t, 3, 0.0) for t in tasks)),
            timeout=5.0,
        )
        assert peak == 2

    asyncio.run(_run())


def test_cloud_tasks_respect_per_user_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一使用者的雲端任務最多 1 個，不同使用者可並行。"""
    monkeypatch.setattr(settings, "BOT_BRAIN_CLOUD_MAX_CONCURRENCY", 4)
    monkeypatch.setattr(settings, "BOT_BRAIN_CLOUD_PER_USER_CONCURRENCY", 1)

    async def _run() -> None:
        brain = BotBrain(MagicMock())
        active_by_user: dict[str, int] = defaultdict(int)
        peak_by_user: dict[str, int] = defaultdict(int)
        total_active = 0
        total_peak = 0

        async def fake_process(task: InferenceTask, max_retries: int, retry_delay: float, **kwargs: object) -> None:
            nonlocal total_active, total_peak
            key = str(task.user_id)
            active_by_user[key] += 1
            peak_by_user[key] = max(peak_by_user[key], active_by_user[key])
            total_active += 1
            total_peak = max(total_peak, total_active)
            try:
                await asyncio.sleep(0.02)
            finally:
                active_by_user[key] -= 1
                total_active -= 1

        brain._process_task = fake_process  # type: ignore[method-assign]
        tasks = [_make_task(user_id=uid) for uid in (101, 101, 101, 202, 202, 202)]
        await asyncio.wait_for(
            asyncio.gather(*(brain._run_cloud_task(t, 3, 0.0) for t in tasks)),
            timeout=5.0,
        )
        assert peak_by_user == {"101": 1, "202": 1}
        assert total_peak <= 4

    asyncio.run(_run())


def test_local_retry_is_requeued_without_blocking_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """本地失敗重試應立即排程回佇列，_process_task 不得 sleep 佔住 worker。"""
    monkeypatch.setattr(settings, "FALLBACK_ENABLED", False)
    monkeypatch.setattr(settings, "FALLBACK_MODELS", [])
    monkeypatch.setattr(settings, "LIGHT_MODEL", "")

    async def _run() -> None:
        from handlers.social import observability

        monkeypatch.setattr(observability, "log_inference", lambda **kwargs: None)
        monkeypatch.setattr(observability, "log_error", lambda **kwargs: None)

        brain = BotBrain(MagicMock())
        task = _make_task(user_id=303, model="qwen2.5:7b")
        task.requeue_retries = True
        await brain._queue.put(task)
        # 模擬 worker 取出任務（unfinished_tasks 仍為 1，由 _process_task 的 finally 釋放）。
        assert brain._queue.get_nowait() is task

        async def empty_chat(**kwargs: object) -> str:
            return ""

        monkeypatch.setattr(brain._ollama, "chat", empty_chat)
        # 退避延遲縮到最短，但傳入的 retry_delay 刻意設很長，證明 sleep 已移出熱路徑。
        monkeypatch.setattr(bot_brain_cog, "_retry_delay_seconds", lambda *args, **kwargs: 0.01)

        started = time.monotonic()
        await brain._process_task(task, max_retries=3, retry_delay=5.0)
        elapsed = time.monotonic() - started

        assert elapsed < 1.0
        assert task.attempt == 1
        assert len(brain._retry_tasks) == 1
        assert brain._queue.qsize() == 0
        task.callback.assert_not_awaited()

        await asyncio.gather(*list(brain._retry_tasks))
        await asyncio.sleep(0)
        assert brain._retry_tasks == set()
        assert brain._queue.qsize() == 1
        requeued = brain._queue.get_nowait()
        assert requeued is task

        async def ok_chat(**kwargs: object) -> str:
            return "ok"

        monkeypatch.setattr(brain._ollama, "chat", ok_chat)
        await brain._process_task(task, max_retries=3, retry_delay=0.01)

        assert task.attempt == 2
        task.callback.assert_awaited_once_with("ok")

    asyncio.run(_run())


def test_worker_backoff_is_exponential_and_capped() -> None:
    """Worker 失敗退避應遞增、具抖動且不超過上限。"""
    assert _worker_backoff_delay(0) == 0.0

    first = _worker_backoff_delay(1, base=0.5, cap=60.0)
    second = _worker_backoff_delay(2, base=0.5, cap=60.0)
    assert 0.42 <= first <= 0.58
    assert 0.85 <= second <= 1.15

    capped = _worker_backoff_delay(20, base=0.5, cap=60.0)
    assert 51.0 <= capped <= 69.0


def test_worker_marks_degraded_after_failure_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """連續失敗達上限後應標記降級，且仍持續退避而非停擺。"""
    monkeypatch.setattr(settings, "BOT_BRAIN_WORKER_MAX_FAILURES", 2)

    async def _run() -> None:
        brain = BotBrain(MagicMock())
        calls = 0

        class _FailingQueue:
            async def get(self) -> InferenceTask:
                nonlocal calls
                calls += 1
                if calls <= 3:
                    raise RuntimeError("simulated worker failure")
                raise asyncio.CancelledError

        brain._queue = _FailingQueue()  # type: ignore[assignment]
        delays: list[int] = []
        monkeypatch.setattr(
            bot_brain_cog,
            "_worker_backoff_delay",
            lambda failures, base=0.5, cap=60.0: (delays.append(failures), 0.0)[1],
        )

        with pytest.raises(asyncio.CancelledError):
            await brain._inference_worker()

        assert delays == [1, 2, 3]
        assert brain._worker_degraded is True
        assert brain._worker_failures == 3

    asyncio.run(_run())


def test_observability_write_queue_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """觀測佇列滿載時應丟棄並累計，不得無上限成長或在熱路徑同步落盤。"""
    from handlers.social import observability

    bounded = pyqueue.Queue(maxsize=1)
    monkeypatch.setattr(observability, "_WRITE_QUEUE", bounded)
    monkeypatch.setattr(observability, "_ensure_writer_thread", lambda: None)
    monkeypatch.setattr(observability, "_WRITE_SHUTDOWN", False)
    monkeypatch.setattr(observability, "_WRITE_OVERFLOW", 0)

    observability._write_line(Path("runtime.jsonl"), {"event": "first"})
    observability._write_line(Path("runtime.jsonl"), {"event": "second"})

    stats = observability.get_observability_stats()
    assert stats["queue_size"] == 1
    assert stats["queue_maxsize"] == 1
    assert stats["dropped"] == 1

    bounded.get_nowait()
    bounded.task_done()


def _stop_real_writer() -> None:
    """測試前停止既有 writer 執行緒，避免與替換後的佇列互相競爭。"""
    from handlers.social import observability

    if observability._WRITE_THREAD_STARTED:
        observability.shutdown_observability_logs(timeout=2.0)
    observability._WRITE_THREAD_STARTED = False
    observability._WRITE_THREAD = None
    observability._WRITE_SHUTDOWN = False


def test_observability_shutdown_flushes_pending_events(monkeypatch: pytest.MonkeyPatch) -> None:
    """關機時應排空佇列、寫完所有事件後才結束 writer 執行緒。"""
    from handlers.social import observability

    _stop_real_writer()
    fake_queue: "pyqueue.Queue[tuple[Path, dict] | None]" = pyqueue.Queue(maxsize=10)
    flushed: list[tuple[Path, dict]] = []

    monkeypatch.setattr(observability, "_WRITE_QUEUE", fake_queue)
    monkeypatch.setattr(observability, "_WRITE_THREAD_STARTED", False)
    monkeypatch.setattr(observability, "_WRITE_THREAD", None)
    monkeypatch.setattr(observability, "_WRITE_SHUTDOWN", False)
    monkeypatch.setattr(observability, "_WRITE_OVERFLOW", 0)
    monkeypatch.setattr(observability, "_flush_batch", lambda batch: flushed.extend(batch))

    observability._write_line(Path("runtime.jsonl"), {"event": "first"})
    observability._write_line(Path("runtime.jsonl"), {"event": "second"})
    observability.shutdown_observability_logs(timeout=2.0)

    assert [entry["event"] for _, entry in flushed] == ["first", "second"]
    assert fake_queue.qsize() == 0
    assert observability.get_observability_stats()["writer_started"] is True


def test_social_background_tasks_keep_strong_reference() -> None:
    """插話評估等背景任務必須保留強引用，完成後自動移除。"""
    from handlers.social import SocialChat

    async def _run() -> None:
        cog = SocialChat(MagicMock())
        started = asyncio.Event()
        release = asyncio.Event()

        async def _job() -> None:
            started.set()
            await release.wait()

        task = cog._spawn_background(_job(), name="interject-eval-test")
        await started.wait()
        assert task in cog._background_tasks
        assert not task.done()

        release.set()
        await task
        await asyncio.sleep(0)
        assert task not in cog._background_tasks

    asyncio.run(_run())


def test_social_cog_unload_cancels_background_tasks() -> None:
    """卸載 SocialChat 時應取消所有仍在執行的背景任務。"""
    from handlers.social import SocialChat

    async def _run() -> None:
        cog = SocialChat(MagicMock())

        async def _forever() -> None:
            await asyncio.Event().wait()

        task = cog._spawn_background(_forever(), name="interject-eval-forever")
        await asyncio.sleep(0)
        await cog.cog_unload()

        assert task.cancelled()
        assert cog._background_tasks == set()

    asyncio.run(_run())


def test_dedicated_executors_are_thread_pools() -> None:
    """core.concurrency 應提供兩個獨立且具名稱的執行器。"""
    assert isinstance(heavy_io_pool, ThreadPoolExecutor)
    assert isinstance(db_pool, ThreadPoolExecutor)
    assert heavy_io_pool is not db_pool


def test_schedule_in_pool_tracks_future() -> None:
    """schedule_in_pool 應回傳 Future、完成後釋放強引用。"""
    async def _run() -> None:
        future = schedule_in_pool(db_pool, lambda value: value + 1, 41)
        assert future is not None
        assert await future == 42
        for _ in range(10):
            if pending_background_writes() == 0:
                break
            await asyncio.sleep(0)
        assert pending_background_writes() == 0

    asyncio.run(_run())


def test_schedule_in_pool_sync_fallback() -> None:
    """沒有 event loop 時應同步執行，維持既有 CLI 行為。"""
    calls: list[int] = []
    result = schedule_in_pool(heavy_io_pool, lambda: calls.append(1))
    assert result is None
    assert calls == [1]


def test_no_bare_default_executor_in_governed_paths() -> None:
    """任務治理涉及的熱路徑不得再使用預設 executor（run_in_executor(None, ...)）。"""
    governed = [
        "handlers/music/cog.py",
        "handlers/memory/storage.py",
        "handlers/memory/relationship.py",
        "handlers/memory/chat_logger.py",
        "handlers/social/tools/stock_tool.py",
        "handlers/social/tools/search_tools.py",
        "mod/clients/gemini_image_client.py",
        "mod/stock_trader/cog.py",
        "mod/market_plugin/reports/morning_verify.py",
    ]
    for rel_path in governed:
        source = (ROOT / rel_path).read_text(encoding="utf-8")
        assert "run_in_executor(None" not in source, f"{rel_path} 仍使用預設 executor"
