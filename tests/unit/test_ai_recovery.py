"""
tests/unit/test_ai_recovery.py
==============================
Unit tests for AI Task Recovery and InFlightTaskRegistry (Phase 3).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

import discord
from core.recovery.base import SnapshotMetadata
from handlers.social.task_registry import (
    InFlightTask,
    InFlightTaskRegistry,
    get_in_flight_registry,
)
from handlers.social.recovery_provider import AITaskRecoveryProvider
from handlers.social.streaming_thinking import LiveThinkingSession


# --- Test InFlightTask Data Structure ---


def test_in_flight_task_serialization() -> None:
    now = time.time()
    task = InFlightTask(
        task_id="test-task-1",
        channel_id=12345,
        message_id=67890,
        prompt="你好雪莉",
        user_id=111,
        start_time=now,
        task_type="chat",
        origin_message_id=99999,
    )
    d = task.to_dict()
    assert d["task_id"] == "test-task-1"
    assert d["channel_id"] == 12345
    assert d["message_id"] == 67890
    assert d["prompt"] == "你好雪莉"
    assert d["user_id"] == 111
    assert d["start_time"] == now
    assert d["task_type"] == "chat"
    assert d["origin_message_id"] == 99999

    restored = InFlightTask.from_dict(d)
    assert restored.task_id == task.task_id
    assert restored.channel_id == task.channel_id
    assert restored.message_id == task.message_id
    assert restored.prompt == task.prompt
    assert restored.user_id == task.user_id
    assert restored.start_time == task.start_time
    assert restored.task_type == task.task_type
    assert restored.origin_message_id == task.origin_message_id


# --- Test InFlightTaskRegistry ---


def test_registry_register_and_unregister() -> None:
    registry = InFlightTaskRegistry()
    assert len(registry.list_all()) == 0

    t1 = registry.register(
        task_id="task-1",
        channel_id=100,
        message_id=200,
        prompt="測試提問 1",
        user_id=300,
    )
    assert registry.get("task-1") == t1
    assert len(registry.list_all()) == 1

    t2 = registry.register(
        task_id="task-2",
        channel_id=101,
        message_id=201,
        prompt="測試提問 2",
    )
    assert len(registry.list_all()) == 2

    removed = registry.unregister("task-1")
    assert removed == t1
    assert registry.get("task-1") is None
    assert len(registry.list_all()) == 1

    registry.clear()
    assert len(registry.list_all()) == 0


@pytest.mark.asyncio
async def test_registry_triggers_recovery_manager_snapshot() -> None:
    registry = InFlightTaskRegistry()
    mock_mgr = MagicMock()
    mock_mgr.capture_provider = AsyncMock()

    with patch("core.recovery.manager.get_recovery_manager", return_value=mock_mgr):
        registry.register(
            task_id="task-sync",
            channel_id=123,
            message_id=456,
            prompt="同步快照測試",
        )
        # Yield to allow scheduled create_task to execute
        await asyncio.sleep(0.01)
        mock_mgr.capture_provider.assert_called_with("in_flight_ai_tasks")


# --- Test AITaskRecoveryProvider ---


@pytest.mark.asyncio
async def test_provider_capture_snapshot() -> None:
    registry = InFlightTaskRegistry()
    provider = AITaskRecoveryProvider()
    provider.registry = registry

    # 1. 查無活躍任務時回傳 None (使 storage 清理既有快照)
    assert await provider.capture_recovery_snapshot() is None

    # 2. 登記任務後，快照包含完整字典
    registry.register(
        task_id="t-1",
        channel_id=111,
        message_id=222,
        prompt="雪莉妳好！",
    )
    snapshot = await provider.capture_recovery_snapshot()
    assert snapshot is not None
    assert "tasks" in snapshot
    assert len(snapshot["tasks"]) == 1
    assert snapshot["tasks"][0]["task_id"] == "t-1"


@pytest.mark.asyncio
async def test_provider_restore_hot_restart_success() -> None:
    """測試短暫重啟 (<45s) 時接續推演並覆寫原佔位訊息。"""
    mock_bot = MagicMock()
    mock_channel = MagicMock()
    mock_msg = MagicMock()
    mock_msg.edit = AsyncMock()
    mock_channel.fetch_message = AsyncMock(return_value=mock_msg)
    mock_bot.get_channel.return_value = mock_channel

    mock_brain = MagicMock()
    mock_brain.generate_reply = AsyncMock(return_value="這是接續生成的回答！")
    mock_bot.get_cog.return_value = mock_brain

    provider = AITaskRecoveryProvider(bot=mock_bot)
    now = time.time()
    snapshot_data = {
        "tasks": [
            {
                "task_id": "hot-1",
                "channel_id": 999,
                "message_id": 888,
                "prompt": "天氣如何？",
                "user_id": 777,
                "start_time": now - 10.0,  # 10s 前，符合短暫重啟判定
                "task_type": "chat",
            }
        ]
    }
    meta = SnapshotMetadata(
        provider_name="in_flight_ai_tasks",
        snapshot_time=now - 10.0,
        ttl_seconds=300.0,
    )

    await provider.restore_from_snapshot(snapshot_data, meta)

    mock_channel.fetch_message.assert_called_once_with(888)
    mock_brain.generate_reply.assert_called_once_with("天氣如何？", user_id=777, channel_id=999)
    # 應有兩次 edit：一次過渡提示，一次最終解答
    assert mock_msg.edit.call_count == 2
    last_call_content = mock_msg.edit.call_args_list[-1][1]["content"]
    assert "這是接續生成的回答！" in last_call_content


@pytest.mark.asyncio
async def test_provider_restore_timeout_fallback() -> None:
    """測試超時重啟 (>45s) 時將原佔位訊息優雅替換為重啟說明，消除殭屍訊息。"""
    mock_bot = MagicMock()
    mock_channel = MagicMock()
    mock_msg = MagicMock()
    mock_msg.edit = AsyncMock()
    mock_channel.fetch_message = AsyncMock(return_value=mock_msg)
    mock_bot.get_channel.return_value = mock_channel

    provider = AITaskRecoveryProvider(bot=mock_bot)
    now = time.time()
    snapshot_data = {
        "tasks": [
            {
                "task_id": "timeout-1",
                "channel_id": 999,
                "message_id": 888,
                "prompt": "長時間任務",
                "user_id": 777,
                "start_time": now - 100.0,  # 100s 前，超過 45s
                "task_type": "chat",
            }
        ]
    }
    meta = SnapshotMetadata(
        provider_name="in_flight_ai_tasks",
        snapshot_time=now - 100.0,
        ttl_seconds=300.0,
    )

    await provider.restore_from_snapshot(snapshot_data, meta)

    mock_channel.fetch_message.assert_called_once_with(888)
    assert mock_msg.edit.call_count == 1
    content = mock_msg.edit.call_args[1]["content"]
    assert "雪莉剛剛因系統更新短暫重啟" in content


@pytest.mark.asyncio
async def test_provider_restore_deleted_message_ignored() -> None:
    """測試若 Discord 原訊息已被刪除 (NotFound)，安靜忽略不噴錯。"""
    mock_bot = MagicMock()
    mock_channel = MagicMock()
    mock_channel.fetch_message = AsyncMock(side_effect=discord.NotFound(MagicMock(), "Not Found"))
    mock_bot.get_channel.return_value = mock_channel

    provider = AITaskRecoveryProvider(bot=mock_bot)
    snapshot_data = {
        "tasks": [
            {
                "task_id": "del-1",
                "channel_id": 999,
                "message_id": 888,
                "prompt": "已刪除的訊息",
                "start_time": time.time(),
            }
        ]
    }
    meta = SnapshotMetadata("in_flight_ai_tasks", time.time(), 300.0)

    # 應順利完成且無例外拋出
    await provider.restore_from_snapshot(snapshot_data, meta)


# --- Test LiveThinkingSession Integration with InFlightTaskRegistry ---


@pytest.mark.asyncio
async def test_live_thinking_session_registers_and_unregisters_task() -> None:
    """測試 LiveThinkingSession 的 start -> finish 完整登記與註銷生命週期。"""
    placeholder = MagicMock(spec=discord.Message)
    placeholder.id = 5566
    placeholder.channel = MagicMock()
    placeholder.channel.id = 7788
    placeholder.edit = AsyncMock()

    target_msg = MagicMock(spec=discord.Message)
    target_msg.id = 1122
    target_msg.author = MagicMock()
    target_msg.author.id = 3344
    target_msg.clean_content = "請幫我分析這份資料"
    target_msg.reply = AsyncMock(return_value=placeholder)
    target_msg.channel = placeholder.channel

    registry = get_in_flight_registry()
    registry.clear()

    session = LiveThinkingSession(target_msg, prompt="請幫我分析這份資料")

    # 1. 呼叫 start()，建立 placeholder 並登記未結案任務
    await session.start("🔍 任務拆解中")
    task_id = f"ai-task-{placeholder.id}"
    task = registry.get(task_id)
    assert task is not None
    assert task.message_id == 5566
    assert task.channel_id == 7788
    assert task.user_id == 3344
    assert task.prompt == "請幫我分析這份資料"

    # 2. 呼叫 finish()，完成發送並從 registry 註銷
    await session.finish("分析完成，這是結論！", prompt="請幫我分析這份資料")
    assert registry.get(task_id) is None


@pytest.mark.asyncio
async def test_live_thinking_session_abort_unregisters_task() -> None:
    """測試 LiveThinkingSession.abort() 亦可安全註銷進行中的任務。"""
    placeholder = MagicMock(spec=discord.Message)
    placeholder.id = 9900
    placeholder.channel = MagicMock()
    placeholder.channel.id = 1122

    target_msg = MagicMock(spec=discord.Message)
    target_msg.id = 3344
    target_msg.author = MagicMock()
    target_msg.author.id = 5566
    target_msg.clean_content = "終止測試"
    target_msg.reply = AsyncMock(return_value=placeholder)
    target_msg.channel = placeholder.channel

    registry = get_in_flight_registry()
    registry.clear()

    session = LiveThinkingSession(target_msg)
    await session.start("意圖解析中")
    task_id = f"ai-task-{placeholder.id}"
    assert registry.get(task_id) is not None

    session.abort()
    assert registry.get(task_id) is None


@pytest.mark.asyncio
async def test_provider_restore_long_message_chunked() -> None:
    """測試接續運算產出超長訊息 (>2000字) 時，正確分段發送且不拋出 50035 異常或降級。"""
    mock_bot = MagicMock()
    mock_channel = MagicMock()
    mock_msg = MagicMock()
    mock_msg.id = 8881
    mock_msg.edit = AsyncMock()
    mock_msg.add_reaction = AsyncMock()
    mock_channel.fetch_message = AsyncMock(return_value=mock_msg)
    mock_channel.send = AsyncMock()
    mock_bot.get_channel.return_value = mock_channel

    mock_brain = MagicMock()
    # 產生超過 2500 字的長文字回覆
    long_reply = "【超長分析開始】" + ("雪莉的詳細推理過程與論證內容。" * 150) + "【推理結束】"
    assert len(long_reply) > 2000
    mock_brain.generate_reply = AsyncMock(return_value=long_reply)
    mock_bot.get_cog.return_value = mock_brain

    provider = AITaskRecoveryProvider(bot=mock_bot)
    now = time.time()
    snapshot_data = {
        "tasks": [
            {
                "task_id": "long-msg-1",
                "channel_id": 999,
                "message_id": 8881,
                "prompt": "請給我完整的大篇幅推理報告",
                "user_id": 777,
                "start_time": now - 5.0,
                "task_type": "chat",
            }
        ]
    }
    meta = SnapshotMetadata(
        provider_name="in_flight_ai_tasks",
        snapshot_time=now - 5.0,
        ttl_seconds=300.0,
    )

    await provider.restore_from_snapshot(snapshot_data, meta)

    # 驗證兩次 edit：一次過渡提示，一次首段 (<= 1900 字)
    assert mock_msg.edit.call_count == 2
    final_edit_content = mock_msg.edit.call_args_list[-1][1]["content"]
    assert len(final_edit_content) <= 1900
    # 驗證後續片段透過 channel.send 發送
    assert mock_channel.send.call_count >= 1
    # 驗證增加了互動按鈕反應
    assert mock_msg.add_reaction.call_count == 3


@pytest.mark.asyncio
async def test_provider_restore_concurrent_tasks() -> None:
    """測試多個任務同時中斷時，透過 Semaphore 並發恢復且彼此互不干擾。"""
    mock_bot = MagicMock()
    mock_channel = MagicMock()

    msg1 = MagicMock(id=101)
    msg1.edit = AsyncMock()
    msg1.add_reaction = AsyncMock()
    msg2 = MagicMock(id=102)
    msg2.edit = AsyncMock()
    msg2.add_reaction = AsyncMock()

    def _fetch_msg(mid: int) -> MagicMock:
        return msg1 if mid == 101 else msg2

    mock_channel.fetch_message = AsyncMock(side_effect=_fetch_msg)
    mock_bot.get_channel.return_value = mock_channel

    mock_brain = MagicMock()
    mock_brain.generate_reply = AsyncMock(return_value="接續成功！")
    mock_bot.get_cog.return_value = mock_brain

    provider = AITaskRecoveryProvider(bot=mock_bot)
    now = time.time()
    snapshot_data = {
        "tasks": [
            {"task_id": "t1", "channel_id": 1, "message_id": 101, "prompt": "問題1", "start_time": now - 2.0},
            {"task_id": "t2", "channel_id": 1, "message_id": 102, "prompt": "問題2", "start_time": now - 3.0},
        ]
    }
    meta = SnapshotMetadata("in_flight_ai_tasks", now - 2.0, 300.0)

    await provider.restore_from_snapshot(snapshot_data, meta)

    assert msg1.edit.call_count == 2
    assert msg2.edit.call_count == 2


@pytest.mark.asyncio
async def test_provider_restore_preserves_newly_registered_tasks() -> None:
    """測試自癒對帳執行期間或完成後，不應誤刪正在接收的新活躍任務。"""
    registry = get_in_flight_registry()
    registry.clear()

    # 模擬開機自癒期間，使用者剛發起新對話已登記到 registry
    new_task = registry.register(
        task_id="active-user-task",
        channel_id=888,
        message_id=999,
        prompt="剛進線的新提問",
    )

    mock_bot = MagicMock()
    mock_channel = MagicMock()
    mock_msg = MagicMock(id=111)
    mock_msg.edit = AsyncMock()
    mock_msg.add_reaction = AsyncMock()
    mock_channel.fetch_message = AsyncMock(return_value=mock_msg)
    mock_bot.get_channel.return_value = mock_channel

    mock_brain = MagicMock()
    mock_brain.generate_reply = AsyncMock(return_value="歷史任務恢復！")
    mock_bot.get_cog.return_value = mock_brain

    provider = AITaskRecoveryProvider(bot=mock_bot)
    now = time.time()
    snapshot_data = {
        "tasks": [
            {"task_id": "old-task", "channel_id": 888, "message_id": 111, "prompt": "舊提問", "start_time": now - 5.0}
        ]
    }
    meta = SnapshotMetadata("in_flight_ai_tasks", now - 5.0, 300.0)

    await provider.restore_from_snapshot(snapshot_data, meta)

    # 驗證新活躍任務完好無損，未被 clear() 清空
    assert registry.get("active-user-task") == new_task
    # 舊任務已被結案
    assert registry.get("old-task") is None


@pytest.mark.asyncio
async def test_registry_background_tasks_strong_reference() -> None:
    """測試 InFlightTaskRegistry 保持對快照保存任務的強引用，防範 asyncio GC。"""
    registry = InFlightTaskRegistry()
    mock_mgr = MagicMock()
    mock_mgr.capture_provider = AsyncMock()

    with patch("core.recovery.manager.get_recovery_manager", return_value=mock_mgr):
        registry.register(
            task_id="task-gc-test",
            channel_id=123,
            message_id=456,
            prompt="GC 防護測試",
        )
        assert len(registry._background_tasks) >= 1
        # 等待任務完成後回呼自動移除
        await asyncio.sleep(0.02)
        assert len(registry._background_tasks) == 0
