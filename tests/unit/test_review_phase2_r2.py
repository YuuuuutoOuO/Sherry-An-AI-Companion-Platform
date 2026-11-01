"""
tests/unit/test_review_phase2_r2.py
===================================
複審 2（Phase 2 完成後）：增量審的迴歸守門。

本檔補上複審時發現的取消傳播缺口：
1. ``DeepSearchCog._run_research_flow`` 例外/取消時必須移除 ``_active_tasks``，
   否則 Task 強引用永久滯留，Web UI 也無法再取消。
2. Spark 啟動的深度研究 Task 必須納入同一追蹤表，不能 fire-and-forget。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from handlers.spark.cog import SparkActionRunner
from handlers.spark.models import Trigger
from mod.deep_search.cog import DeepSearchCog


@pytest.fixture
def anyio_backend() -> str:
    """本檔使用 asyncio Task/Event，固定 anyio 後端為 asyncio。"""
    return "asyncio"


@pytest.mark.anyio
async def test_deep_search_active_task_cleaned_when_engine_fails() -> None:
    """引擎拋錯時仍要清掉追蹤項，避免 Task 引用與取消入口失效。"""
    cog = object.__new__(DeepSearchCog)
    cog._active_tasks = {"rs-fail": object()}  # type: ignore[dict-item]
    cog.engine = MagicMock()
    cog.engine.run_research = AsyncMock(side_effect=RuntimeError("engine boom"))
    cog._send_final_report = AsyncMock()

    with pytest.raises(RuntimeError, match="engine boom"):
        await cog._run_research_flow(
            topic="測試主題",
            model="test-model",
            rounds=2,
            session_id="rs-fail",
        )

    assert "rs-fail" not in cog._active_tasks


@pytest.mark.anyio
async def test_spark_deep_search_task_tracked_and_cleaned() -> None:
    """Spark 深度研究 Task 應可被取消入口找到，完成後自動移除。"""
    started = asyncio.Event()
    release = asyncio.Event()

    async def _fake_flow(**_kwargs: object) -> None:
        started.set()
        await release.wait()

    fake_cog = MagicMock()
    fake_cog._active_tasks = {}
    fake_cog._run_research_flow = _fake_flow

    fake_bot = MagicMock()
    fake_bot._deep_search_cog = fake_cog
    fake_bot.get_cog.return_value = fake_cog

    runner = object.__new__(SparkActionRunner)
    runner.bot = fake_bot
    runner.service = None

    trigger = Trigger(
        trigger_id="trig-review2",
        user_id=1,
        channel_id=2,
        trigger_type="schedule",
        condition={},
        message="測試主題",
        action={
            "action_type": "mod_deep_search",
            "payload": {"topic": "測試主題", "rounds": 2},
        },
    )
    channel = MagicMock()
    channel.id = 2
    initial_msg = MagicMock()
    initial_msg.id = 99
    channel.send = AsyncMock(return_value=initial_msg)

    assert await runner._run_deep_search(trigger, channel, trigger.action) is True
    await started.wait()

    assert len(fake_cog._active_tasks) == 1
    session_id, task = next(iter(fake_cog._active_tasks.items()))
    assert task.get_name() == f"spark-deep-search-{session_id}"

    release.set()
    await task
    assert session_id not in fake_cog._active_tasks
