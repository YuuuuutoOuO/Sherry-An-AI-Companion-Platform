"""tests/unit/test_webui_blocking_io_r12.py
=========================================
R12（§7.2 D8 前半）WebUI／sys_inspector 阻塞 I/O 回歸測試。

驗證重點：
1. 事件循環阻塞計時器：慢速同步工作（gzip、設定讀寫、指標摘要、git、審計寫入）
   必須被移到工作執行緒，事件循環心跳最大間隔 < 50ms。
2. `/sw.js` 內容快取與 `/api/settings`、組件指標端點行為不變。
3. 延遲重啟/關閉動作以 async task 執行，訊號寫入與 checkpoint 不阻塞事件循環。
"""

from __future__ import annotations

import asyncio
import gzip
import threading
import time
from pathlib import Path
from typing import Any, Awaitable
from unittest.mock import patch

import pytest

import web_ui.server as server_mod
from core.sys_inspector import (
    AuditService,
    DiagnosticResult,
    IncidentReport,
    IncidentSeverity,
    IncidentType,
    LogSnapshot,
)

_LOOP_GAP_THRESHOLD_SEC = 0.05
_SLOW_IO_SEC = 0.2


async def _run_with_loop_gap(awaitable: Awaitable[Any]) -> tuple[Any, float]:
    """執行 awaitable 並回傳（結果, 事件循環心跳最大間隔秒數）。

    心跳每 5ms 醒來一次；若 awaitable 在事件循環上同步阻塞，最大間隔會趨近阻塞時間。
    """
    gaps: list[float] = []
    done = False

    async def _heartbeat() -> None:
        nonlocal done
        last = time.perf_counter()
        while not done:
            await asyncio.sleep(0.005)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    heartbeat = asyncio.create_task(_heartbeat())
    await asyncio.sleep(0)  # 讓心跳先進入等待狀態
    try:
        result = await awaitable
    finally:
        done = True
        await heartbeat
    return result, (max(gaps) if gaps else 0.0)


def test_gzip_compression_offloaded_from_event_loop() -> None:
    """`after_request` gzip 壓縮不得阻塞事件循環，且輸出仍可解壓。"""
    original_compress = gzip.compress
    worker_threads: list[threading.Thread] = []

    def _slow_compress(data: bytes, compresslevel: int = 6) -> bytes:
        worker_threads.append(threading.current_thread())
        time.sleep(_SLOW_IO_SEC)
        return original_compress(data, compresslevel=compresslevel)

    async def _run() -> tuple[int, str, bytes, float]:
        client = server_mod.app.test_client()
        with patch.object(server_mod.gzip, "compress", side_effect=_slow_compress):
            response, gap = await _run_with_loop_gap(
                client.get("/sw.js", headers={"Accept-Encoding": "gzip"})
            )
        raw = await response.get_data()
        return response.status_code, response.headers.get("Content-Encoding", ""), raw, gap

    status, encoding, raw, gap = asyncio.run(_run())
    assert status == 200
    assert encoding == "gzip"
    assert b"sherry-console" in gzip.decompress(raw)
    assert gap < _LOOP_GAP_THRESHOLD_SEC
    assert worker_threads and worker_threads[0] is not threading.main_thread()


def test_settings_get_offloads_blocking_read() -> None:
    """GET /api/settings 的設定掃描／人設檔讀取須在工作執行緒執行。"""
    worker_threads: list[threading.Thread] = []

    def _slow_get_editable_settings() -> dict[str, Any]:
        worker_threads.append(threading.current_thread())
        time.sleep(_SLOW_IO_SEC)
        return {"TEST_SETTING_R12": {"value": 1, "label": "R12", "type": "int"}}

    async def _run() -> tuple[int, dict[str, Any], float]:
        client = server_mod.app.test_client()
        with patch.object(server_mod, "_is_admin_request", return_value=True), \
             patch.object(server_mod.settings, "get_editable_settings", side_effect=_slow_get_editable_settings):
            response, gap = await _run_with_loop_gap(client.get("/api/settings"))
        return response.status_code, await response.get_json(), gap

    status, body, gap = asyncio.run(_run())
    assert status == 200
    assert body["TEST_SETTING_R12"]["value"] == 1
    assert gap < _LOOP_GAP_THRESHOLD_SEC
    assert worker_threads and worker_threads[0] is not threading.main_thread()


def test_settings_post_offloads_update_and_audit() -> None:
    """POST /api/settings 的批次寫檔與審計寫入須在工作執行緒執行。"""
    update_threads: list[threading.Thread] = []
    audit_threads: list[threading.Thread] = []

    def _slow_apply(updates: list[tuple[str, Any]]) -> list[str]:
        update_threads.append(threading.current_thread())
        time.sleep(_SLOW_IO_SEC)
        return []

    def _slow_audit(*args: Any, **kwargs: Any) -> None:
        audit_threads.append(threading.current_thread())
        time.sleep(_SLOW_IO_SEC)

    async def _run() -> tuple[int, dict[str, Any], float]:
        from mod.audit_manager import audit_manager

        client = server_mod.app.test_client()
        with patch.object(server_mod, "_is_admin_request", return_value=True), \
             patch.object(server_mod, "_apply_setting_updates_sync", side_effect=_slow_apply), \
             patch.object(audit_manager, "record_action", side_effect=_slow_audit):
            response, gap = await _run_with_loop_gap(
                client.post("/api/settings", json={"LLM_MAX_OUTPUT_TOKENS": 4096})
            )
        return response.status_code, await response.get_json(), gap

    status, body, gap = asyncio.run(_run())
    assert status == 200
    assert body == {"status": "success"}
    assert gap < _LOOP_GAP_THRESHOLD_SEC
    assert update_threads and update_threads[0] is not threading.main_thread()
    assert audit_threads and audit_threads[0] is not threading.main_thread()


def test_component_metrics_endpoint_offloads_summary() -> None:
    """GET /api/system/components/metrics 的百分位統計須在工作執行緒執行。"""
    from core.sys_inspector.metrics import component_metrics

    worker_threads: list[threading.Thread] = []

    def _slow_summary() -> dict[str, Any]:
        worker_threads.append(threading.current_thread())
        time.sleep(_SLOW_IO_SEC)
        return {"components": {}, "totals": {"total_calls": 0}}

    async def _run() -> tuple[int, dict[str, Any], float]:
        client = server_mod.app.test_client()
        with patch.object(server_mod, "_is_admin_request", return_value=True), \
             patch.object(component_metrics, "get_summary", side_effect=_slow_summary):
            response, gap = await _run_with_loop_gap(
                client.get("/api/system/components/metrics")
            )
        return response.status_code, await response.get_json(), gap

    status, body, gap = asyncio.run(_run())
    assert status == 200
    assert body["totals"]["total_calls"] == 0
    assert gap < _LOOP_GAP_THRESHOLD_SEC
    assert worker_threads and worker_threads[0] is not threading.main_thread()


def test_audit_service_diagnose_offloads_sqlite_write() -> None:
    """AuditService.diagnose_incident 的同步 SQLite 寫入須在工作執行緒執行。"""
    incident = IncidentReport(
        incident_type=IncidentType.SLOW_RESPONSE_90S,
        severity=IncidentSeverity.WARNING,
        latency_ms=95000,
        trigger_reason="R12 測試",
        trace_id="tr-r12-audit",
    )
    snapshot = LogSnapshot(
        window_minutes=5,
        total_lines=0,
        error_count=0,
        warning_count=0,
        bot_log_snippet="",
        runtime_event_snippet="",
        combined_text="",
    )
    diagnostic = DiagnosticResult(
        root_cause="測試根因",
        bottleneck_category="IO_BLOCKING",
        key_evidence=[],
        recommendations=[],
        raw_model_response="",
        model_name="test",
    )

    class _FakeLogRepo:
        async def get_recent_snapshot(self, window_minutes: int = 5) -> LogSnapshot:
            return snapshot

    class _FakeAnalysisClient:
        async def analyze_incident(
            self, incident: IncidentReport, snapshot: LogSnapshot
        ) -> DiagnosticResult:
            return diagnostic

    class _FakeDbRepo:
        def __init__(self) -> None:
            self.thread: threading.Thread | None = None
            self.calls = 0

        def insert_audit_record(
            self,
            incident: IncidentReport,
            diagnostic: DiagnosticResult,
            snapshot: LogSnapshot,
        ) -> None:
            self.calls += 1
            self.thread = threading.current_thread()
            time.sleep(_SLOW_IO_SEC)

    db_repo = _FakeDbRepo()
    service = AuditService(
        log_repository=_FakeLogRepo(),
        analysis_client=_FakeAnalysisClient(),
        db_repository=db_repo,
    )

    async def _run() -> tuple[DiagnosticResult, float]:
        result, gap = await _run_with_loop_gap(service.diagnose_incident(incident))
        return result[0], gap

    result, gap = asyncio.run(_run())
    assert result.root_cause == "測試根因"
    assert db_repo.calls == 1
    assert gap < _LOOP_GAP_THRESHOLD_SEC
    assert db_repo.thread is not None and db_repo.thread is not threading.main_thread()


def test_service_worker_source_is_cached() -> None:
    """`/sw.js` 讀檔結果依 mtime 快取；命中快取時不再讀取檔案內容。"""
    server_mod._SW_JS_CACHE.clear()
    try:
        first = server_mod._read_service_worker_source_sync()
        assert "sherry-console" in first
        with patch.object(Path, "read_text", side_effect=AssertionError("cache miss")):
            second = server_mod._read_service_worker_source_sync()
        assert second == first
    finally:
        server_mod._SW_JS_CACHE.clear()


def test_git_info_collection_is_offloaded_and_warmed_lazily() -> None:
    """git 子進程查詢須在工作執行緒執行，且啟動資訊由背景預熱填入。"""
    worker_threads: list[threading.Thread] = []
    fake_info = {
        "branch": "dev",
        "commit_hash": "abc1234",
        "full_hash": "abc1234" * 5,
        "commit_msg": "test",
        "commit_date": "2026-10-20 00:00:00",
        "commit_author": "test",
        "pull_log": "",
        "pull_time": "",
        "tag": "test",
    }

    def _slow_git_info() -> dict[str, Any]:
        worker_threads.append(threading.current_thread())
        time.sleep(_SLOW_IO_SEC)
        return dict(fake_info)

    saved_startup = server_mod._STARTUP_GIT_INFO
    saved_cached = server_mod._CACHED_GIT_INFO
    saved_last = server_mod._LAST_GIT_INFO_TIME
    server_mod._STARTUP_GIT_INFO = {}
    server_mod._CACHED_GIT_INFO = {}
    server_mod._LAST_GIT_INFO_TIME = 0.0
    try:
        async def _run() -> tuple[dict[str, Any], float]:
            _, gap = await _run_with_loop_gap(server_mod._warm_git_info())
            return server_mod._STARTUP_GIT_INFO, gap

        with patch.object(server_mod, "_get_git_info", side_effect=_slow_git_info):
            startup, gap = asyncio.run(_run())
        assert startup["commit_hash"] == "abc1234"
        assert gap < _LOOP_GAP_THRESHOLD_SEC
        assert worker_threads and worker_threads[0] is not threading.main_thread()
    finally:
        server_mod._STARTUP_GIT_INFO = saved_startup
        server_mod._CACHED_GIT_INFO = saved_cached
        server_mod._LAST_GIT_INFO_TIME = saved_last


def test_delayed_stop_action_offloads_signal_and_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """延遲關閉動作的訊號寫入／checkpoint 不阻塞事件循環，且於工作執行緒執行。"""
    calls: dict[str, Any] = {}

    def _slow_write(payload: str) -> None:
        calls["payload"] = payload
        calls["thread"] = threading.current_thread()
        time.sleep(_SLOW_IO_SEC)

    async def _fast_checkpoint(label: str) -> None:
        calls["checkpoint"] = label

    def _fake_flush() -> None:
        calls["flushed"] = True

    monkeypatch.setattr(server_mod, "_SHUTDOWN_DELAY_SECONDS", 0)
    monkeypatch.setattr(server_mod, "_write_shutdown_signal_sync", _slow_write)
    monkeypatch.setattr(server_mod, "_checkpoint_before_exit", _fast_checkpoint)
    monkeypatch.setattr(server_mod, "_flush_logs_and_exit", _fake_flush)

    async def _run() -> float:
        _, gap = await _run_with_loop_gap(server_mod._delayed_stop_action())
        return gap

    gap = asyncio.run(_run())
    assert gap < _LOOP_GAP_THRESHOLD_SEC
    assert str(calls["payload"]).startswith("stop:")
    assert calls["thread"] is not threading.main_thread()
    assert calls["checkpoint"] == "stop"
    assert calls["flushed"] is True


def test_stop_endpoint_schedules_action_without_blocking() -> None:
    """POST /api/stop 僅排程延遲動作即回應，不在請求路徑同步退出。"""
    scheduled: list[Any] = []

    def _capture(coro: Any) -> None:
        scheduled.append(coro)

    async def _run() -> tuple[int, dict[str, Any]]:
        client = server_mod.app.test_client()
        with patch.object(server_mod, "_is_admin_request", return_value=True), \
             patch.object(server_mod, "_schedule_process_action", side_effect=_capture):
            response = await client.post("/api/stop")
        return response.status_code, await response.get_json()

    status, body = asyncio.run(_run())
    for coro in scheduled:
        coro.close()
    assert status == 200
    assert body["status"] == "stopping"
    assert len(scheduled) == 1
