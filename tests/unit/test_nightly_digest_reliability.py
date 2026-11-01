"""
tests/unit/test_nightly_digest_reliability.py
==============================================
深夜記憶整合可靠性測試。

守衛兩項無聲資料遺失風險：
  1. 全部使用者的上游推論皆失敗（空白回應／逾時）時，不得標記日期為已處理。
  2. 錯過凌晨主視窗後必須具備補跑機制，且補跑需節流避免每分鐘重打。
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import settings
from handlers.memory import nightly_cog as nc
from handlers.memory.nightly_cog import NightlyDigest

_TZ_TW = timezone(timedelta(hours=8))
_DATE = "2026-09-29"


def _records(user_ids: list[str], per_user: int = 6) -> list[dict[str, Any]]:
    """建立符合門檻的假聊天記錄。"""
    out: list[dict[str, Any]] = []
    for uid in user_ids:
        for i in range(per_user):
            out.append({
                "user_id": uid,
                "display_name": f"user-{uid}",
                "server_id": "111",
                "content": f"訊息 {i}",
                "ts": f"{_DATE}T12:0{i}:00+08:00",
            })
    return out


@contextmanager
def _safe_pipeline() -> Iterator[dict[str, MagicMock]]:
    """阻擋整條後續管線（世界記憶/歸檔/資料保留）的真實副作用。"""
    with ExitStack() as stack:
        mocks = {
            "log_world_event": stack.enter_context(
                patch("handlers.memory.world_memory.log_world_event")
            ),
            "compress_daily_events": stack.enter_context(
                patch("handlers.memory.world_memory.compress_daily_events", return_value=0)
            ),
            "cleanup_old_logs": stack.enter_context(
                patch("handlers.memory.world_memory.cleanup_old_logs", return_value=0)
            ),
            "archive_finished_week": stack.enter_context(
                patch("handlers.memory.memory_archive.archive_finished_week", return_value=None)
            ),
            "save_user_memory": stack.enter_context(patch.object(nc, "save_user_memory")),
            "mark_date_processed": stack.enter_context(patch.object(nc, "mark_date_processed")),
            "record_world_retry": stack.enter_context(patch.object(nc, "_record_world_retry")),
            "decay_memories": stack.enter_context(
                patch.object(nc, "decay_memories", return_value=0)
            ),
            "consolidate": stack.enter_context(
                patch.object(
                    nc,
                    "consolidate_all_user_memories",
                    return_value={"facts_removed": 0, "aliases_removed": 0, "users_cleaned": 0},
                )
            ),
        }
        retention_report = MagicMock(
            cleaned_files_count=0, purged_log_entries=0, reclaimed_bytes=0
        )
        stack.enter_context(
            patch(
                "handlers.memory.retention_worker.DataRetentionWorker.run_retention_cycle",
                return_value=retention_report,
            )
        )
        yield mocks


class _FakeBrain:
    """假的 BotBrain，直接以指定回覆觸發推論任務回呼。"""

    def __init__(self, reply_for: Any) -> None:
        self._reply_for = reply_for
        self.submitted: list[Any] = []

    async def enqueue_task(self, task: Any) -> None:
        self.submitted.append(task)
        await task.callback(self._reply_for(task))


class _HangingBrain(_FakeBrain):
    """排隊成功但模型永遠不回覆的 BotBrain（對應上游推論卡死）。"""

    async def enqueue_task(self, task: Any) -> None:
        self.submitted.append(task)


def _build_cog(reply_for: Any, brain_cls: type[_FakeBrain] = _FakeBrain) -> tuple[NightlyDigest, _FakeBrain]:
    brain = brain_cls(reply_for)
    bot = MagicMock()
    bot.get_cog.side_effect = lambda name: brain if name == "BotBrain" else None
    bot.guilds = []
    return NightlyDigest(bot), brain


@contextmanager
def _capture_logs() -> Iterator[list[str]]:
    """擷取 NightlyDigest 的 WARNING 以上日誌訊息。"""
    messages: list[str] = []

    class _Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    logger = logging.getLogger(nc.__name__)
    handler = _Handler()
    prev_level = logger.level
    logger.setLevel(logging.WARNING)
    logger.addHandler(handler)
    try:
        yield messages
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev_level)


class TestNightlyDigestDataLossGuard:
    """驗證全數失敗時不會把整天資料標記為已整合。"""

    def test_all_users_empty_reply_does_not_mark_processed(self) -> None:
        """所有使用者拿到空白回應時，不得寫入 processed 標記。"""
        async def scenario() -> dict[str, Any] | None:
            cog, _ = _build_cog(lambda task: "")
            with patch.object(nc, "read_chat_log", return_value=_records(["u1", "u2"])), \
                 patch.object(nc, "load_user_memory", return_value={}), \
                 _safe_pipeline() as mocks:
                stats = await cog._run_digest(_DATE)
                mocks["mark_date_processed"].assert_not_called()
                mocks["save_user_memory"].assert_not_called()
            return stats

        stats = asyncio.run(scenario())

        assert stats is not None
        assert stats["status"] == "all_users_failed"
        assert stats["users_eligible"] == 2
        assert stats["users_processed"] == 0
        assert sorted(stats["failed_users"]) == ["u1", "u2"]

    def test_all_users_timeout_does_not_mark_processed(self) -> None:
        """所有使用者逾時時同樣不得標記已處理。"""
        async def scenario() -> dict[str, Any] | None:
            cog, _ = _build_cog(lambda task: "", brain_cls=_HangingBrain)
            with patch.object(nc, "read_chat_log", return_value=_records(["u1"])), \
                 patch.object(nc, "load_user_memory", return_value={}), \
                 patch.object(settings, "NIGHTLY_DIGEST_TIMEOUT", 0.05), \
                 _safe_pipeline() as mocks:
                stats = await cog._run_digest(_DATE)
                mocks["mark_date_processed"].assert_not_called()
            return stats

        stats = asyncio.run(scenario())

        assert stats is not None
        assert stats["status"] == "all_users_failed"
        assert stats["timed_out_users"] == ["u1"]

    def test_partial_failure_marks_processed_and_reports(self) -> None:
        """部分使用者失敗時仍標記已處理，但需記錄失敗者供人工補做。"""
        def reply_for(task: Any) -> str:
            if "u2" in task.task_id:
                return ""
            return (
                '{"aliases": ["阿貓"], "facts": '
                '[{"fact": "喜歡貓", "importance": 3, "category": "preference"}]}'
            )

        async def scenario() -> dict[str, Any] | None:
            cog, _ = _build_cog(reply_for)
            with patch.object(nc, "read_chat_log", return_value=_records(["u1", "u2"])), \
                 patch.object(nc, "load_user_memory", return_value={}), \
                 _safe_pipeline() as mocks:
                stats = await cog._run_digest(_DATE)
                mocks["mark_date_processed"].assert_called_once()
                assert mocks["mark_date_processed"].call_args[0][0] == _DATE
            return stats

        stats = asyncio.run(scenario())

        assert stats is not None
        assert stats["status"] == "completed"
        assert stats["users_processed"] == 1
        assert stats["failed_users"] == ["u2"]

    def test_no_eligible_users_still_completes(self) -> None:
        """所有使用者都低於門檻時屬正常無資料，不視為全數失敗。"""
        async def scenario() -> tuple[dict[str, Any] | None, list[Any]]:
            cog, brain = _build_cog(lambda task: "")
            with patch.object(nc, "read_chat_log", return_value=_records(["u1"], per_user=1)), \
                 patch.object(nc, "load_user_memory", return_value={}), \
                 _safe_pipeline() as mocks:
                stats = await cog._run_digest(_DATE)
                mocks["mark_date_processed"].assert_called_once()
            return stats, brain.submitted

        stats, submitted = asyncio.run(scenario())

        assert submitted == []
        assert stats is not None
        assert stats["status"] == "completed"
        assert stats["users_eligible"] == 0

    def test_empty_reply_logged_distinctly_from_json_error(self) -> None:
        """空白回應需記錄為獨立訊息，而非混為 JSON 解析失敗。"""
        async def scenario() -> list[str]:
            cog, _ = _build_cog(lambda task: "")
            with patch.object(nc, "read_chat_log", return_value=_records(["u1"])), \
                 patch.object(nc, "load_user_memory", return_value={}), \
                 _safe_pipeline(), \
                 _capture_logs() as logs:
                await cog._run_digest(_DATE)
            return logs

        output = "\n".join(asyncio.run(scenario()))
        assert "收到空白回應" in output
        assert "JSON 解析失敗" not in output

    def test_malformed_json_still_reported_as_parse_error(self) -> None:
        """非空但非法的回應仍應記為 JSON 解析失敗。"""
        async def scenario() -> tuple[dict[str, Any] | None, list[str]]:
            cog, _ = _build_cog(lambda task: "這不是 JSON")
            with patch.object(nc, "read_chat_log", return_value=_records(["u1"])), \
                 patch.object(nc, "load_user_memory", return_value={}), \
                 _safe_pipeline(), \
                 _capture_logs() as logs:
                stats = await cog._run_digest(_DATE)
            return stats, logs

        stats, logs = asyncio.run(scenario())
        output = "\n".join(logs)

        assert stats is not None
        assert stats["status"] == "all_users_failed"
        assert "JSON 解析失敗" in output
        assert "收到空白回應" not in output


class TestNightlyDigestWorldRetryQueue:
    """驗證世界記憶失敗會被記錄待補跑，而非靜默遺失。"""

    def test_empty_world_reply_queues_retry(self) -> None:
        """世界記憶回空字串時，該伺服器必須進入待補跑清單。"""

        def reply_for(task: Any) -> str:
            if str(getattr(task, "task_id", "")).startswith("world-"):
                return ""  # 模擬 provider 安全過濾導致的空回應
            return '[{"summary": "使用者記憶測試摘要，長度足夠通過檢查。", "importance": 3}]'

        async def scenario() -> dict[str, Any]:
            cog, _ = _build_cog(reply_for)
            with patch.object(nc, "read_chat_log", return_value=_records(["u1"])), \
                 patch.object(nc, "load_user_memory", return_value={}), \
                 _safe_pipeline() as mocks:
                await cog._run_digest(_DATE)
                mocks["mark_date_processed"].assert_called_once()
                mocks["record_world_retry"].assert_called_once()
                call_args = mocks["record_world_retry"].call_args[0]
                return {"args": call_args}

        result = asyncio.run(scenario())

        assert result["args"][0] == _DATE
        assert list(result["args"][1]) == ["111"]

    def test_world_success_does_not_queue_retry(self) -> None:
        """世界記憶正常產出時不應寫入待補跑清單。"""

        def reply_for(task: Any) -> str:
            if str(getattr(task, "task_id", "")).startswith("world-"):
                return '[{"summary": "世界記憶測試摘要，長度足夠通過檢查。", "importance": 3}]'
            return '[{"summary": "使用者記憶測試摘要，長度足夠通過檢查。", "importance": 3}]'

        async def scenario() -> dict[str, MagicMock]:
            cog, _ = _build_cog(reply_for)
            with patch.object(nc, "read_chat_log", return_value=_records(["u1"])), \
                 patch.object(nc, "load_user_memory", return_value={}), \
                 _safe_pipeline() as mocks:
                await cog._run_digest(_DATE)
                return mocks

        mocks = asyncio.run(scenario())

        mocks["record_world_retry"].assert_not_called()
        mocks["log_world_event"].assert_called_once()

    def test_world_timeout_queues_retry(self) -> None:
        """世界記憶逾時同樣需進入待補跑清單。"""

        class _WorldHangingBrain(_FakeBrain):
            async def enqueue_task(self, task: Any) -> None:
                self.submitted.append(task)
                if not str(getattr(task, "task_id", "")).startswith("world-"):
                    await task.callback('[{"summary": "使用者記憶測試摘要，長度足夠。", "importance": 3}]')

        async def scenario() -> dict[str, MagicMock]:
            cog, _ = _build_cog(lambda task: "", brain_cls=_WorldHangingBrain)
            with patch.object(nc, "read_chat_log", return_value=_records(["u1"])), \
                 patch.object(nc, "load_user_memory", return_value={}), \
                 patch.object(settings, "NIGHTLY_DIGEST_TIMEOUT", 0.05), \
                 _safe_pipeline() as mocks:
                await cog._run_digest(_DATE)
                return mocks

        mocks = asyncio.run(scenario())

        mocks["record_world_retry"].assert_called_once()

    def test_world_memory_content_filtered_not_queued_for_retry(self) -> None:
        """當世界記憶遭遇內容安全過濾 (is_content_filtered=True) 時，不得排入 world_retry_queue。"""

        class _ContentFilteredBrain(_FakeBrain):
            async def enqueue_task(self, task: Any) -> None:
                self.submitted.append(task)
                if str(getattr(task, "task_id", "")).startswith("world-"):
                    task.is_content_filtered = True
                    await task.callback("")
                else:
                    await task.callback('{"aliases": ["user-u1"], "facts": [{"fact": "測試記憶", "scope": "PRIVATE"}]}')

        async def scenario() -> dict[str, MagicMock]:
            cog, _ = _build_cog(lambda task: "", brain_cls=_ContentFilteredBrain)
            with patch.object(nc, "read_chat_log", return_value=_records(["u1"])), \
                 patch.object(nc, "load_user_memory", return_value={}), \
                 _safe_pipeline() as mocks:
                await cog._run_digest(_DATE)
                return mocks

        mocks = asyncio.run(scenario())
        # 遭遇 content_filter 的伺服器禁止被排入補跑隊列
        mocks["record_world_retry"].assert_not_called()



class TestNightlyDigestWorldRetryExecution:
    """驗證 world_retry_queue 的補跑流程真的會重新提煉並標記完成。"""

    def test_retry_runs_inference_and_resolves_queue(self) -> None:
        """待補跑項目應重新送出推論，成功後呼叫 resolve 標記完成。"""
        world_reply = '[{"summary": "補跑產生的世界記憶摘要，長度足夠通過檢查。", "importance": 3}]'

        async def scenario() -> dict[str, Any]:
            cog, brain = _build_cog(lambda task: world_reply)
            resolved: list[tuple[str, str]] = []
            with patch.object(nc, "get_pending_world_retries", return_value=[(_DATE, "111")]), \
                 patch.object(nc, "read_chat_log", return_value=_records(["u1"])), \
                 patch.object(nc, "resolve_world_retry", side_effect=lambda d, s: resolved.append((d, s))), \
                 patch("handlers.memory.world_memory.log_world_event") as log_world:
                await cog._retry_world_memory()
                return {"brain": brain, "resolved": resolved, "log_world": log_world}

        result = asyncio.run(scenario())

        assert len(result["brain"].submitted) == 1
        assert result["brain"].submitted[0].task_id == f"world-retry-{_DATE}-111"
        assert result["resolved"] == [(_DATE, "111")]
        result["log_world"].assert_called_once()

    def test_retry_increments_attempt_when_still_failing(self) -> None:
        """補跑仍失敗時遞增計數並保留佇列項目，不標記完成。"""

        async def scenario() -> tuple[dict[str, int], list[tuple[str, str]]]:
            cog, _ = _build_cog(lambda task: "")
            resolved: list[tuple[str, str]] = []
            with patch.object(nc, "get_pending_world_retries", return_value=[(_DATE, "111")]), \
                 patch.object(nc, "read_chat_log", return_value=_records(["u1"])), \
                 patch.object(nc, "resolve_world_retry", side_effect=lambda d, s: resolved.append((d, s))), \
                 patch("handlers.memory.world_memory.log_world_event"):
                await cog._retry_world_memory()
                return cog._world_retry_attempts, resolved

        attempts, resolved = asyncio.run(scenario())

        assert attempts[f"{_DATE}|111"] == 1
        assert resolved == []

    def test_retry_gives_up_after_max_rounds(self) -> None:
        """重試次數用盡後放棄並標記完成，避免無限重試同一份內容。"""

        async def scenario() -> tuple[int, list[tuple[str, str]], list[Any]]:
            cog, brain = _build_cog(lambda task: "")
            resolved: list[tuple[str, str]] = []
            cog._world_retry_attempts[f"{_DATE}|111"] = nc._WORLD_RETRY_MAX_ROUNDS
            with patch.object(nc, "get_pending_world_retries", return_value=[(_DATE, "111")]), \
                 patch.object(nc, "read_chat_log", return_value=_records(["u1"])), \
                 patch.object(nc, "resolve_world_retry", side_effect=lambda d, s: resolved.append((d, s))):
                await cog._retry_world_memory()
                return len(brain.submitted), resolved, list(cog._world_retry_attempts)

        submitted, resolved, remaining = asyncio.run(scenario())

        assert submitted == 0  # 放棄時不應再送推論
        assert resolved == [(_DATE, "111")]
        assert remaining == []

    def test_retry_content_filtered_removes_from_queue(self) -> None:
        """補跑遭遇內容安全過濾 (is_content_filtered=True) 時，應立即標記完成 (resolve) 並移出重試佇列。"""

        class _ContentFilteredRetryBrain(_FakeBrain):
            async def enqueue_task(self, task: Any) -> None:
                self.submitted.append(task)
                task.is_content_filtered = True
                await task.callback("")

        async def scenario() -> tuple[dict[str, int], list[tuple[str, str]]]:
            cog, _ = _build_cog(lambda task: "", brain_cls=_ContentFilteredRetryBrain)
            resolved: list[tuple[str, str]] = []
            cog._world_retry_attempts[f"{_DATE}|111"] = 1
            with patch.object(nc, "get_pending_world_retries", return_value=[(_DATE, "111")]), \
                 patch.object(nc, "read_chat_log", return_value=_records(["u1"])), \
                 patch.object(nc, "resolve_world_retry", side_effect=lambda d, s: resolved.append((d, s))), \
                 patch("handlers.memory.world_memory.log_world_event"):
                await cog._retry_world_memory()
                return cog._world_retry_attempts, resolved

        attempts, resolved = asyncio.run(scenario())

        assert f"{_DATE}|111" not in attempts
        assert resolved == [(_DATE, "111")]


    def test_retry_skips_when_records_below_threshold(self) -> None:
        """記錄不足時直接略過並標記完成，不做無意義推論。"""

        async def scenario() -> tuple[int, list[tuple[str, str]]]:
            cog, brain = _build_cog(lambda task: "")
            resolved: list[tuple[str, str]] = []
            thin = [{
                "user_id": "u1", "display_name": "user-u1", "server_id": "111",
                "content": "只有一則", "ts": f"{_DATE}T12:00:00+08:00",
            }]
            with patch.object(nc, "get_pending_world_retries", return_value=[(_DATE, "111")]), \
                 patch.object(nc, "read_chat_log", return_value=thin), \
                 patch.object(nc, "resolve_world_retry", side_effect=lambda d, s: resolved.append((d, s))):
                await cog._retry_world_memory()
                return len(brain.submitted), resolved

        submitted, resolved = asyncio.run(scenario())

        assert submitted == 0
        assert resolved == [(_DATE, "111")]

    def test_parse_world_events_handles_fenced_json(self) -> None:
        """補跑與主流程共用的解析器需處理 markdown 包裹的 JSON。"""
        events = nc._parse_world_events(
            '```json\n[{"summary": "測試摘要，長度足夠通過檢查。", "importance": 3}]\n```'
        )
        assert events is not None
        assert events[0]["summary"].startswith("測試摘要")
        assert nc._parse_world_events("") is None
        assert nc._parse_world_events("{\"summary\": \"物件非陣列\"}") is None


class TestNightlyDigestCatchup:
    """驗證錯過主視窗後的補跑節流行為。"""

    def test_catchup_throttles_repeat_within_interval(self) -> None:
        cog, _ = _build_cog(lambda task: "")
        with patch.object(nc, "datetime") as mock_dt, \
             patch("handlers.memory.nightly_cog.time.monotonic", side_effect=[0.0, 10.0, 5000.0, 6000.0]):
            mock_dt.now.return_value = datetime(2026, 9, 30, 18, 0, tzinfo=_TZ_TW)
            assert cog._should_attempt(_DATE) is True
            assert cog._should_attempt(_DATE) is False
            assert cog._should_attempt(_DATE) is True

    def test_catchup_stops_after_max_attempts(self) -> None:
        cog, _ = _build_cog(lambda task: "")
        with patch.object(nc, "datetime") as mock_dt, \
             patch("handlers.memory.nightly_cog.time.monotonic", side_effect=[0.0, 5000.0, 10000.0, 15000.0, 20000.0]):
            mock_dt.now.return_value = datetime(2026, 9, 30, 18, 0, tzinfo=_TZ_TW)
            results = [cog._should_attempt(_DATE) for _ in range(5)]
        assert results == [True, True, True, False, False]

    def test_main_window_always_allowed(self) -> None:
        """主視窗內不受補跑次數限制。"""
        cog, _ = _build_cog(lambda task: "")
        with patch.object(nc, "datetime") as mock_dt, \
             patch.object(settings, "NIGHTLY_DIGEST_HOUR", 4), \
             patch.object(settings, "NIGHTLY_DIGEST_MINUTE", 30):
            mock_dt.now.return_value = datetime(2026, 9, 30, 4, 32, tzinfo=_TZ_TW)
            for _ in range(nc._CATCHUP_MAX_ATTEMPTS + 2):
                assert cog._should_attempt(_DATE) is True

    def test_before_window_not_allowed(self) -> None:
        """主視窗之前不得提前觸發。"""
        cog, _ = _build_cog(lambda task: "")
        with patch.object(nc, "datetime") as mock_dt, \
             patch.object(settings, "NIGHTLY_DIGEST_HOUR", 4), \
             patch.object(settings, "NIGHTLY_DIGEST_MINUTE", 30):
            mock_dt.now.return_value = datetime(2026, 9, 30, 3, 50, tzinfo=_TZ_TW)
            assert cog._should_attempt(_DATE) is False

    def test_loop_skips_already_processed_date(self) -> None:
        """已標記完成的日期不應再觸發任何整合。"""
        async def scenario() -> tuple[Any, list[Any]]:
            cog, brain = _build_cog(lambda task: "")
            mock_run = AsyncMock()
            with patch.object(nc, "is_date_processed", return_value=True), \
                 patch.object(nc, "get_yesterday_str", return_value=_DATE), \
                 patch.object(cog, "_run_digest", new=mock_run):
                await cog._nightly_loop.coro(cog)
            mock_run.assert_not_called()
            return mock_run, brain.submitted

        _, submitted = asyncio.run(scenario())
        assert submitted == []

    def test_loop_catchup_runs_after_window_missed(self) -> None:
        """錯過視窗後巡邏仍應自動補跑昨日資料。"""
        async def scenario() -> list[str]:
            cog, _ = _build_cog(lambda task: "")
            mock_run = AsyncMock(return_value={"status": "completed"})
            with patch.object(nc, "datetime") as mock_dt, \
                 patch.object(nc, "is_date_processed", return_value=False), \
                 patch.object(nc, "get_yesterday_str", return_value=_DATE), \
                 patch("handlers.memory.nightly_cog.time.monotonic", return_value=0.0), \
                 patch.object(cog, "_run_digest", new=mock_run):
                mock_dt.now.return_value = datetime(2026, 9, 30, 15, 0, tzinfo=_TZ_TW)
                await cog._nightly_loop.coro(cog)
            mock_run.assert_awaited_once_with(_DATE)
            return list(cog._catchup_attempts)

        tracked = asyncio.run(scenario())
        assert _DATE not in tracked
