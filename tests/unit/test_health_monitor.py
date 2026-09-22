"""tests/unit/test_health_monitor.py
==================================
驗證健康監控系統 (handlers/bot_brain/health_monitor) 與 DiscordChannelHandler
在日誌推播、管理員私訊發送、異常安全降級與設定去重邏輯上的正確性。
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import settings
from handlers.bot_brain.health_monitor import DiscordChannelHandler, HealthMonitor


@pytest.fixture
def anyio_backend() -> str:
    """設定 pytest-anyio 後端為 asyncio。

    Returns:
        str: 後端名稱。
    """
    return "asyncio"


def test_get_alert_recipient_user_ids_deduplication() -> None:
    """測試 settings.get_alert_recipient_user_ids 正確合併並去除重複項。"""
    # Arrange
    with patch.object(settings, "HEALTH_ALERT_USER_IDS", [101, 102]), \
         patch.object(settings, "ADMIN_USER_IDS", [102, 103]):
        # Act
        recipients = settings.get_alert_recipient_user_ids()

        # Assert
        assert recipients == [101, 102, 103]


def test_get_alert_recipient_user_ids_empty() -> None:
    """測試當未設定任何管理員與私訊接收者時回傳空列表。"""
    # Arrange
    with patch.object(settings, "HEALTH_ALERT_USER_IDS", []), \
         patch.object(settings, "ADMIN_USER_IDS", []):
        # Act
        recipients = settings.get_alert_recipient_user_ids()

        # Assert
        assert recipients == []


def test_get_alert_recipient_user_ids_aliases() -> None:
    """測試支援 DC_USER_ID, DISCORD_USER_ID, ADMIN_USER_ID 等別名環境變數。"""
    # Arrange
    with patch.object(settings, "HEALTH_ALERT_USER_IDS", []), \
         patch.object(settings, "ADMIN_USER_IDS", []), \
         patch.dict("os.environ", {"DC_USER_ID": "425311733503229972", "ADMIN_USER_ID": "111222333"}):
        # Act
        recipients = settings.get_alert_recipient_user_ids()

        # Assert
        assert 425311733503229972 in recipients
        assert 111222333 in recipients



def test_discord_channel_handler_emit_no_targets() -> None:
    """測試當頻道為 0 且無接收者時不發送任何日誌 Task。"""
    # Arrange
    mock_bot = MagicMock()
    mock_bot.loop = MagicMock()
    handler = DiscordChannelHandler(mock_bot, level=logging.ERROR)
    record = logging.LogRecord(
        name="test_logger",
        level=logging.ERROR,
        pathname="test.py",
        lineno=10,
        msg="測試錯誤訊息",
        args=(),
        exc_info=None,
    )

    # Act
    with patch.object(settings, "HEALTH_ALERT_CHANNEL_ID", 0), \
         patch.object(settings, "HEALTH_ALERT_USER_IDS", []), \
         patch.object(settings, "ADMIN_USER_IDS", []):
        handler.emit(record)

    # Assert
    mock_bot.loop.create_task.assert_not_called()


def test_discord_channel_handler_emit_creates_task() -> None:
    """測試當有管理員時 emit 會建立發送非同步 Task。"""
    # Arrange
    mock_bot = MagicMock()
    created_coro = None

    def fake_create_task(coro: object) -> MagicMock:
        nonlocal created_coro
        created_coro = coro
        return MagicMock()

    mock_loop = MagicMock()
    mock_loop.is_running.return_value = True
    mock_loop.create_task.side_effect = fake_create_task
    mock_bot.loop = mock_loop
    handler = DiscordChannelHandler(mock_bot, level=logging.ERROR)
    record = logging.LogRecord(
        name="test_logger",
        level=logging.ERROR,
        pathname="test.py",
        lineno=10,
        msg="連線異常",
        args=(),
        exc_info=None,
    )

    # Act
    with patch.object(settings, "HEALTH_ALERT_CHANNEL_ID", 0), \
         patch.object(settings, "ADMIN_USER_IDS", [999]):
        handler.emit(record)

    # Assert
    mock_loop.create_task.assert_called_once()
    if created_coro and asyncio.iscoroutine(created_coro):
        created_coro.close()


@pytest.mark.anyio
async def test_discord_channel_handler_send_log_channel_and_user() -> None:
    """測試 _send_log 同步發送至 Discord 頻道與使用者私訊。"""
    # Arrange
    mock_bot = MagicMock()
    mock_channel = MagicMock()
    mock_channel.send = AsyncMock()
    mock_bot.get_channel.return_value = mock_channel

    mock_cached_user = MagicMock()
    mock_cached_user.send = AsyncMock()
    mock_fetched_user = MagicMock()
    mock_fetched_user.send = AsyncMock()

    # get_user 對 101 命中，對 102 回傳 None 需透過 fetch_user 取得
    mock_bot.get_user.side_effect = lambda uid: mock_cached_user if uid == 101 else None
    mock_bot.fetch_user = AsyncMock(return_value=mock_fetched_user)

    handler = DiscordChannelHandler(mock_bot, level=logging.WARNING)

    # Act
    await handler._send_log(channel_id=12345, user_ids=[101, 102], msg="系統警報測試")

    # Assert
    mock_channel.send.assert_awaited_once_with("系統警報測試")
    mock_cached_user.send.assert_awaited_once_with("系統警報測試")
    mock_bot.fetch_user.assert_awaited_once_with(102)
    mock_fetched_user.send.assert_awaited_once_with("系統警報測試")


@pytest.mark.anyio
async def test_discord_channel_handler_send_log_safe_exception_handling() -> None:
    """測試發送失敗（如私訊被封鎖）時安全吞吐，不拋出未捕獲例外。"""
    # Arrange
    mock_bot = MagicMock()
    mock_channel = MagicMock()
    mock_channel.send = AsyncMock(side_effect=RuntimeError("Discord 連線中斷"))
    mock_bot.get_channel.return_value = mock_channel

    mock_user = MagicMock()
    mock_user.send = AsyncMock(side_effect=Exception("無法傳送私訊"))
    mock_bot.get_user.return_value = mock_user

    handler = DiscordChannelHandler(mock_bot, level=logging.WARNING)

    # Act & Assert（不應拋出例外）
    await handler._send_log(channel_id=12345, user_ids=[101], msg="測試失敗保護")
    mock_channel.send.assert_awaited_once()
    mock_user.send.assert_awaited_once()


@pytest.mark.anyio
async def test_health_monitor_notify_admin_broadcast() -> None:
    """測試 HealthMonitor.notify_admin 能將警報推播至頻道與管理員私訊。"""
    # Arrange
    mock_bot = MagicMock()
    mock_channel = MagicMock()
    mock_channel.send = AsyncMock()
    mock_bot.get_channel.return_value = mock_channel

    mock_user = MagicMock()
    mock_user.send = AsyncMock()
    mock_bot.get_user.return_value = mock_user

    monitor = HealthMonitor(mock_bot)

    # Act
    with patch.object(settings, "HEALTH_ALERT_CHANNEL_ID", 777), \
         patch.object(settings, "ADMIN_USER_IDS", [888]), \
         patch.object(settings, "HEALTH_ALERT_USER_IDS", []):
        await monitor.notify_admin("Ollama 離線警報")

    # Assert
    mock_channel.send.assert_awaited_once()
    assert "Ollama 離線警報" in mock_channel.send.call_args[0][0]
    mock_user.send.assert_awaited_once()
    assert "Ollama 離線警報" in mock_user.send.call_args[0][0]
