"""tests/unit/test_followup_scanner.py
======================================
單元測試：賽博群友自主回覆漸進退避掃描追蹤器 (FollowupScanner)。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from handlers.care.followup_scanner import FollowupScanner, is_within_active_hours
from handlers.care.repository import CareRepository, CareSurferConfig


@pytest.fixture
def temp_repo(tmp_path: Path) -> CareRepository:
    return CareRepository(db_path=tmp_path / "test_scanner_care.db")


def test_surfer_config_followup_fields_default(temp_repo: CareRepository):
    """測試預設值：啟用漸進掃描且階梯為 [1, 3, 5, 7, 10]。"""
    cfg = temp_repo.get_surfer_config()
    assert cfg.followup_scan_enabled is True
    assert cfg.followup_scan_intervals == [1, 3, 5, 7, 10]


def test_surfer_config_followup_fields_update(temp_repo: CareRepository):
    """測試更新並持久化 followup_scan_intervals 與 followup_scan_enabled。"""
    updated = temp_repo.update_surfer_config(
        followup_scan_enabled=False,
        followup_scan_intervals=[2, 4, 8],
    )
    assert updated.followup_scan_enabled is False
    assert updated.followup_scan_intervals == [2, 4, 8]

    reloaded = temp_repo.get_surfer_config()
    assert reloaded.followup_scan_enabled is False
    assert reloaded.followup_scan_intervals == [2, 4, 8]


def test_is_within_active_hours():
    """測試時段判定輔助函式。"""
    # 正常區間 9 ~ 23
    assert is_within_active_hours(9, 9, 23) is True
    assert is_within_active_hours(15, 9, 23) is True
    assert is_within_active_hours(22, 9, 23) is True
    assert is_within_active_hours(23, 9, 23) is False
    assert is_within_active_hours(3, 9, 23) is False

    # 跨日區間 22 ~ 04
    assert is_within_active_hours(23, 22, 4) is True
    assert is_within_active_hours(2, 22, 4) is True
    assert is_within_active_hours(5, 22, 4) is False


@pytest.mark.asyncio
async def test_scanner_on_bot_spoke_launches_task(temp_repo: CareRepository):
    """測試發言後成功啟動 ladder task，再次發言時舊 task 自動被替換。"""
    temp_repo.update_surfer_config(
        enabled=True,
        chat_reply_enabled=True,
        followup_scan_enabled=True,
        sandbox_channel_id="123456",
        followup_scan_intervals=[1, 3],
    )
    mock_engine = MagicMock()
    mock_engine.repository = temp_repo

    scanner = FollowupScanner(mock_engine)
    mock_bot = MagicMock()

    with patch.object(scanner, "_run_ladder", new_callable=AsyncMock) as mock_ladder:
        await scanner.on_bot_spoke("123456", mock_bot, speak_timestamp=100.0)
        assert "123456" in scanner._tasks
        first_task = scanner._tasks["123456"]

        # 再次發言
        await scanner.on_bot_spoke("123456", mock_bot, speak_timestamp=200.0)
        second_task = scanner._tasks["123456"]
        assert first_task != second_task

    scanner.cancel_all()
    assert len(scanner._tasks) == 0


@pytest.mark.asyncio
async def test_scanner_ignores_non_sandbox_channel(temp_repo: CareRepository):
    """測試若發言頻道非設定之 sandbox_channel_id，不啟動巡檢。"""
    temp_repo.update_surfer_config(
        enabled=True,
        chat_reply_enabled=True,
        followup_scan_enabled=True,
        sandbox_channel_id="123456",
    )
    mock_engine = MagicMock()
    mock_engine.repository = temp_repo

    scanner = FollowupScanner(mock_engine)
    mock_bot = MagicMock()

    await scanner.on_bot_spoke("999999", mock_bot)
    assert "999999" not in scanner._tasks


@pytest.mark.asyncio
async def test_scanner_detects_human_message_and_replies(temp_repo: CareRepository):
    """測試梯級巡檢掃描到人類訊息時，正確調用 _generate_and_reply。"""
    temp_repo.update_surfer_config(
        enabled=True,
        chat_reply_enabled=True,
        followup_scan_enabled=True,
        sandbox_channel_id="123456",
        followup_scan_intervals=[1],
    )
    mock_engine = MagicMock()
    mock_engine.repository = temp_repo

    scanner = FollowupScanner(mock_engine)
    mock_bot = MagicMock()
    mock_bot.user.id = 9999

    mock_channel = MagicMock()
    mock_channel.id = 123456

    # 模擬歷史訊息：有一則人類訊息在發言時間之後
    human_msg = MagicMock()
    human_msg.author.bot = False
    human_msg.author.id = 1111
    human_msg.author.display_name = "群友小明"
    human_msg.created_at.timestamp.return_value = 150.0

    async def _mock_history(limit=25):
        yield human_msg

    mock_channel.history = _mock_history
    mock_bot.get_channel.return_value = mock_channel
    scanner._last_speak_times["123456"] = 100.0

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep, \
         patch("handlers.care.followup_scanner.is_within_active_hours", return_value=True), \
         patch.object(scanner, "_generate_and_reply", new_callable=AsyncMock, return_value=True) as mock_reply:

        # 執行梯級
        await scanner._run_ladder("123456", mock_bot, speak_timestamp=100.0, max_wait_seconds=0)

        # 驗證有偵測到人類並觸發回覆
        mock_reply.assert_called_once_with(mock_channel, human_msg, temp_repo.get_surfer_config(), mock_bot)


@pytest.mark.asyncio
async def test_scanner_stays_silent_when_no_human_response(temp_repo: CareRepository):
    """測試若無人回應，階梯巡檢保持安靜絕不主動發言。"""
    temp_repo.update_surfer_config(
        enabled=True,
        chat_reply_enabled=True,
        followup_scan_enabled=True,
        sandbox_channel_id="123456",
        followup_scan_intervals=[1],
    )
    mock_engine = MagicMock()
    mock_engine.repository = temp_repo

    scanner = FollowupScanner(mock_engine)
    mock_bot = MagicMock()
    mock_bot.user.id = 9999

    mock_channel = MagicMock()
    mock_channel.id = 123456

    # 模擬歷史訊息：只有舊訊息或機器人自己訊息，無任何新人類訊息
    bot_msg = MagicMock()
    bot_msg.author.bot = True
    bot_msg.author.id = 9999
    bot_msg.created_at.timestamp.return_value = 100.0

    async def _mock_history(limit=25):
        yield bot_msg

    mock_channel.history = _mock_history
    mock_bot.get_channel.return_value = mock_channel
    scanner._last_speak_times["123456"] = 100.0

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep, \
         patch("handlers.care.followup_scanner.is_within_active_hours", return_value=True), \
         patch.object(scanner, "_generate_and_reply", new_callable=AsyncMock, return_value=True) as mock_reply:

        await scanner._run_ladder("123456", mock_bot, speak_timestamp=100.0, max_wait_seconds=0)

        # 驗證完全沒有調用 _generate_and_reply，雪莉保持安靜
        mock_reply.assert_not_called()


@pytest.mark.asyncio
async def test_scanner_keeps_waiting_after_ladder_and_catches_later_reply(temp_repo: CareRepository):
    """走完階梯後仍會低頻巡檢；較晚才出現的人類回覆仍會被接住。"""
    temp_repo.update_surfer_config(
        enabled=True,
        chat_reply_enabled=True,
        followup_scan_enabled=True,
        sandbox_channel_id="123456",
        followup_scan_intervals=[1],
    )
    mock_engine = MagicMock()
    mock_engine.repository = temp_repo

    scanner = FollowupScanner(mock_engine)
    mock_bot = MagicMock()
    mock_bot.user.id = 9999

    mock_channel = MagicMock()
    mock_channel.id = 123456

    bot_msg = MagicMock()
    bot_msg.author.bot = True
    bot_msg.author.id = 9999
    bot_msg.created_at.timestamp.return_value = 100.0

    human_msg = MagicMock()
    human_msg.author.bot = False
    human_msg.author.id = 1111
    human_msg.author.display_name = "晚回的群友"
    human_msg.created_at.timestamp.return_value = 150.0

    polls = 0

    async def _mock_history(limit=25):
        nonlocal polls
        polls += 1
        # 前三次巡檢都沒人回（已超過設定的 1 階），第四次才出現人類訊息
        yield human_msg if polls >= 4 else bot_msg

    mock_channel.history = _mock_history
    mock_bot.get_channel.return_value = mock_channel
    scanner._last_speak_times["123456"] = 100.0

    with patch("asyncio.sleep", new_callable=AsyncMock), \
         patch("handlers.care.followup_scanner.is_within_active_hours", return_value=True), \
         patch.object(scanner, "_generate_and_reply", new_callable=AsyncMock, return_value=True) as mock_reply:

        await scanner._run_ladder("123456", mock_bot, speak_timestamp=100.0, max_wait_seconds=7200)

        assert polls == 4
        mock_reply.assert_awaited_once_with(
            mock_channel, human_msg, temp_repo.get_surfer_config(), mock_bot
        )


def test_normalize_followup_intervals_sorts_dedupes_and_caps():
    """階梯正規化：去重、遞增、夾在 1~120 分鐘、最多 8 階。"""
    from handlers.care.repository import normalize_followup_intervals

    assert normalize_followup_intervals([10, 1, 3, 3, 0, -5, "7"]) == [1, 3, 7, 10]
    assert normalize_followup_intervals([]) == [1, 3, 5, 7, 10]
    assert normalize_followup_intervals("bad") == [1, 3, 5, 7, 10]
    capped = normalize_followup_intervals(list(range(1, 50)))
    assert len(capped) == 8
    assert capped == list(range(1, 9))


@pytest.mark.asyncio
async def test_scanner_does_not_reevaluate_same_message_when_brain_silent(temp_repo: CareRepository):
    """大腦對同一則訊息選擇沉默後，後續階梯不可重複評估（避免燒多次 LLM）。"""
    temp_repo.update_surfer_config(
        enabled=True,
        chat_reply_enabled=True,
        followup_scan_enabled=True,
        sandbox_channel_id="123456",
        followup_scan_intervals=[1],
    )
    mock_engine = MagicMock()
    mock_engine.repository = temp_repo

    scanner = FollowupScanner(mock_engine)
    mock_bot = MagicMock()
    mock_bot.user.id = 9999

    mock_channel = MagicMock()
    mock_channel.id = 123456

    human_msg = MagicMock()
    human_msg.author.bot = False
    human_msg.author.id = 1111
    human_msg.author.display_name = "群友小明"
    human_msg.created_at.timestamp.return_value = 150.0

    async def _mock_history(limit=25):
        yield human_msg

    mock_channel.history = _mock_history
    mock_bot.get_channel.return_value = mock_channel
    scanner._last_speak_times["123456"] = 100.0

    with patch("asyncio.sleep", new_callable=AsyncMock), \
         patch("handlers.care.followup_scanner.is_within_active_hours", return_value=True), \
         patch.object(scanner, "_generate_and_reply", new_callable=AsyncMock, return_value=False) as mock_reply:

        await scanner._run_ladder("123456", mock_bot, speak_timestamp=100.0, max_wait_seconds=600)

        # ladder 有 3 階，但同一則訊息只會被評估一次
        assert mock_reply.await_count == 1


@pytest.mark.asyncio
async def test_scanner_stops_when_disabled_mid_ladder(temp_repo: CareRepository):
    """巡檢途中關閉功能後，下一個階梯醒來應立即停止，不再回覆。"""
    temp_repo.update_surfer_config(
        enabled=True,
        chat_reply_enabled=True,
        followup_scan_enabled=True,
        sandbox_channel_id="123456",
        followup_scan_intervals=[1],
    )
    mock_engine = MagicMock()
    mock_engine.repository = temp_repo

    scanner = FollowupScanner(mock_engine)
    mock_bot = MagicMock()
    mock_bot.user.id = 9999

    mock_channel = MagicMock()
    mock_channel.id = 123456

    human_msg = MagicMock()
    human_msg.author.bot = False
    human_msg.author.id = 1111
    human_msg.author.display_name = "群友小明"
    human_msg.created_at.timestamp.return_value = 150.0

    async def _mock_history(limit=25):
        yield human_msg

    mock_channel.history = _mock_history
    mock_bot.get_channel.return_value = mock_channel
    scanner._last_speak_times["123456"] = 100.0

    async def _sleep_then_disable(*_args, **_kwargs):
        temp_repo.update_surfer_config(followup_scan_enabled=False)

    with patch("asyncio.sleep", side_effect=_sleep_then_disable), \
         patch("handlers.care.followup_scanner.is_within_active_hours", return_value=True), \
         patch.object(scanner, "_generate_and_reply", new_callable=AsyncMock, return_value=True) as mock_reply:

        await scanner._run_ladder("123456", mock_bot, speak_timestamp=100.0, max_wait_seconds=600)

        mock_reply.assert_not_called()


@pytest.mark.asyncio
async def test_scanner_skips_group_mode_channel(temp_repo: CareRepository):
    """群像模式由 turn 排程負責，掃描器不應啟動。"""
    temp_repo.update_surfer_config(
        enabled=True,
        chat_reply_enabled=True,
        followup_scan_enabled=True,
        sandbox_channel_id="123456",
    )
    mock_engine = MagicMock()
    mock_engine.repository = temp_repo
    mock_engine.director.mode_for.return_value.is_group = True

    scanner = FollowupScanner(mock_engine)

    await scanner.on_bot_spoke("123456", MagicMock(), speak_timestamp=100.0)

    assert "123456" not in scanner._tasks
