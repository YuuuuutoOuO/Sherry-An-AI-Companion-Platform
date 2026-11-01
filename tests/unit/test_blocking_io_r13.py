"""tests/unit/test_blocking_io_r13.py
=====================================
R13（§7.2 D8 後半）tools／背景群阻塞 I/O 回歸測試。

驗證重點：
1. RSS 與 search_tools 的 DNS/SSRF 檢查不再同步卡住事件循環。
2. trafilatura 使用專用池＋bounded slot；逾時結果丟棄且 slot 於執行緒結束後釋放。
3. yfinance／matplotlib 抓取受全域併發上限保護。
4. vibe 落盤改為批次／單頻道寫入並移到工作執行緒。
5. heartbeat 每輪任務數受上限約束。
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import threading
import time
from pathlib import Path
from typing import Any, Awaitable
from unittest.mock import MagicMock, patch

import handlers.heartbeat.cog as heartbeat_cog
import handlers.social.tools.search_tools as search_tools
import handlers.social.tools.stock_tool as stock_tool
import handlers.spark.clients.rss_client as rss_client
import handlers.vibe_manager.cog as vibe_cog
import handlers.vibe_manager.storage as vibe_storage

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


# ---------------------------------------------------------------------------
# RSS / search_tools：DNS 與 SSRF 檢查
# ---------------------------------------------------------------------------
def test_rss_dns_lookup_offloaded_from_event_loop() -> None:
    """RSS 的 getaddrinfo 必須在 event loop 之外執行，且安全判定結果不變。"""
    worker_threads: list[threading.Thread] = []

    def _slow_getaddrinfo(host: str, port: Any, *args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
        worker_threads.append(threading.current_thread())
        time.sleep(_SLOW_IO_SEC)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    async def _run() -> tuple[bool, float]:
        with patch.object(rss_client.socket, "getaddrinfo", side_effect=_slow_getaddrinfo):
            return await _run_with_loop_gap(
                rss_client.is_safe_feed_url_async("https://example.com/feed.xml")
            )

    safe, gap = asyncio.run(_run())
    assert safe is True
    assert gap < _LOOP_GAP_THRESHOLD_SEC
    assert worker_threads and worker_threads[0] is not threading.main_thread()


def test_rss_fetch_uses_async_safety_check() -> None:
    """fetch_latest_items 不得再呼叫同步 is_safe_feed_url。"""
    calls: list[str] = []

    async def _fake_async_check(url: str) -> bool:
        calls.append(url)
        return False

    client = rss_client.RssClient()
    with patch.object(rss_client, "is_safe_feed_url_async", side_effect=_fake_async_check), \
         patch.object(rss_client, "is_safe_feed_url") as mock_sync:
        items = asyncio.run(client.fetch_latest_items("https://example.com/feed.xml"))

    assert items == []
    assert calls == ["https://example.com/feed.xml"]
    mock_sync.assert_not_called()


def test_search_ssrf_check_offloaded_from_event_loop() -> None:
    """search_tools 的非同步 SSRF 檢查須在 worker thread 執行。"""
    worker_threads: list[threading.Thread] = []

    def _slow_check(url: str) -> bool:
        worker_threads.append(threading.current_thread())
        time.sleep(_SLOW_IO_SEC)
        return True

    async def _run() -> tuple[bool, float]:
        with patch.object(search_tools, "is_safe_public_url", side_effect=_slow_check):
            return await _run_with_loop_gap(
                search_tools.is_safe_public_url_async("https://example.com/article")
            )

    safe, gap = asyncio.run(_run())
    assert safe is True
    assert gap < _LOOP_GAP_THRESHOLD_SEC
    assert worker_threads and worker_threads[0] is not threading.main_thread()


def test_fetch_and_extract_url_async_ssrf_check_keeps_loop_responsive() -> None:
    """fetch_and_extract_url 的 SSRF 預檢不得阻塞事件循環，且仍會攔截。"""
    worker_threads: list[threading.Thread] = []

    def _slow_blocked_check(url: str) -> bool:
        worker_threads.append(threading.current_thread())
        time.sleep(_SLOW_IO_SEC)
        return False

    async def _run() -> tuple[tuple[str, str, str], float]:
        with patch.object(search_tools, "is_safe_public_url", side_effect=_slow_blocked_check):
            return await _run_with_loop_gap(
                search_tools.fetch_and_extract_url("https://example.com/article", timeout_sec=5)
            )

    (title, content, source_type), gap = asyncio.run(_run())
    assert source_type == "blocked"
    assert "安全" in content
    assert title == "https://example.com/article"
    assert gap < _LOOP_GAP_THRESHOLD_SEC
    assert worker_threads and worker_threads[0] is not threading.main_thread()


def test_concurrent_ssrf_checks_keep_loop_responsive() -> None:
    """壓力抽測：多個慢速 SSRF 檢查同時進行時，事件循環仍維持 <50ms。"""
    def _slow_check(url: str) -> bool:
        time.sleep(0.05)
        return True

    async def _run() -> tuple[list[bool], float]:
        with patch.object(search_tools, "is_safe_public_url", side_effect=_slow_check):
            return await _run_with_loop_gap(
                asyncio.gather(
                    *(
                        search_tools.is_safe_public_url_async(f"https://example.com/{i}")
                        for i in range(12)
                    )
                )
            )

    results, gap = asyncio.run(_run())
    assert results == [True] * 12
    assert gap < _LOOP_GAP_THRESHOLD_SEC


# ---------------------------------------------------------------------------
# trafilatura：專用池、bounded slot、逾時丟棄
# ---------------------------------------------------------------------------
def test_trafilatura_timeout_discards_result_and_releases_slot() -> None:
    """trafilatura 逾時後丟棄結果；底層執行緒結束才釋放 slot。"""
    worker_threads: list[threading.Thread] = []

    def _slow_extract(target: str) -> tuple[str | None, str, str]:
        worker_threads.append(threading.current_thread())
        time.sleep(_SLOW_IO_SEC)
        return "<html></html>", "內容" * 40, "慢速標題"

    async def _run() -> tuple[tuple[str | None, str, str], float, tuple[str | None, str, str], float]:
        with patch.object(search_tools, "_fetch_trafilatura", side_effect=_slow_extract), \
             patch.object(search_tools, "trafilatura", object()):
            first, gap = await _run_with_loop_gap(
                search_tools._extract_with_trafilatura("https://example.com/slow", 0.05)
            )
            # 等待底層執行緒結束與完成回呼釋放 slot。
            await asyncio.sleep(_SLOW_IO_SEC + 0.1)
            second, second_gap = await _run_with_loop_gap(
                search_tools._extract_with_trafilatura("https://example.com/fast", 5)
            )
        return first, gap, second, second_gap

    first, gap, second, second_gap = asyncio.run(_run())
    assert first == (None, "", "")  # 逾時結果被丟棄
    assert second[0] == "<html></html>"
    assert second[1]
    assert gap < _LOOP_GAP_THRESHOLD_SEC
    assert second_gap < _LOOP_GAP_THRESHOLD_SEC
    assert worker_threads and all(t is not threading.main_thread() for t in worker_threads)
    assert worker_threads[0].name.startswith("sherry-web-extract")


def test_trafilatura_uses_dedicated_pool_not_heavy_io() -> None:
    """trafilatura 不得佔用 heavy_io_pool 的 worker。"""
    seen: list[str] = []

    def _fast_extract(target: str) -> tuple[str | None, str, str]:
        seen.append(threading.current_thread().name)
        return "<html></html>", "內容" * 40, "標題"

    async def _run() -> tuple[str | None, str, str]:
        with patch.object(search_tools, "_fetch_trafilatura", side_effect=_fast_extract), \
             patch.object(search_tools, "trafilatura", object()):
            return await search_tools._extract_with_trafilatura("https://example.com/a", 5)

    result = asyncio.run(_run())
    assert result[0] == "<html></html>"
    assert seen and seen[0].startswith("sherry-web-extract")


# ---------------------------------------------------------------------------
# 股市抓取：全域併發上限
# ---------------------------------------------------------------------------
def test_stock_fetch_concurrency_capped(monkeypatch: Any) -> None:
    """多個股市抓取同時進行時，在途數量不得超過設定上限。"""
    monkeypatch.setattr(stock_tool.settings, "STOCK_MAX_CONCURRENT_FETCHES", 2)
    stock_tool._STOCK_FETCH_SEMAPHORE = None
    stock_tool._STOCK_FETCH_SEMAPHORE_LOOP = None

    lock = threading.Lock()
    active = 0
    max_active = 0

    def _fake_fetch(ticker: str) -> tuple[None, dict[str, Any]]:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.05)
            return None, {"close": ticker}
        finally:
            with lock:
                active -= 1

    async def _run() -> list[tuple[str, bytes | None, dict[str, Any] | None]]:
        with patch.object(stock_tool, "_fetch_and_plot", side_effect=_fake_fetch):
            return await asyncio.gather(
                *(stock_tool.fetch_and_plot_bounded(t, 5) for t in ("A", "B", "C", "D"))
            )

    results = asyncio.run(_run())
    assert [r[0] for r in results] == ["A", "B", "C", "D"]
    assert [r[2] for r in results] == [{"close": t} for t in ("A", "B", "C", "D")]
    assert max_active <= 2


# ---------------------------------------------------------------------------
# Vibe 落盤：批次寫入與 to_thread
# ---------------------------------------------------------------------------
class _FakeCursor:
    def __init__(self) -> None:
        self.execute_calls: list[tuple[str, Any]] = []
        self.executemany_calls: list[tuple[str, list[Any]]] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.execute_calls.append((sql, params))

    def executemany(self, sql: str, rows: list[Any]) -> None:
        self.executemany_calls.append((sql, list(rows)))


def test_save_vibe_store_batches_rows(monkeypatch: Any) -> None:
    """save_vibe_store 以單一 executemany 寫入所有頻道。"""
    captured: dict[str, _FakeCursor] = {}

    @contextlib.contextmanager
    def _fake_transaction(path: Path):
        cursor = _FakeCursor()
        captured["cursor"] = cursor
        yield cursor

    monkeypatch.setattr(vibe_storage, "db_transaction", _fake_transaction)
    store = {
        f"ch_{i}": {"topic": {"summary": f"s{i}", "weight": i + 1}}
        for i in range(3)
    }
    vibe_storage.save_vibe_store(store, Path("unused.json"))

    cursor = captured["cursor"]
    assert len(cursor.executemany_calls) == 1
    assert len(cursor.executemany_calls[0][1]) == 3
    insert_executes = [c for c in cursor.execute_calls if "INSERT" in c[0].upper()]
    assert insert_executes == []


def test_save_vibe_channel_writes_single_row(monkeypatch: Any) -> None:
    """save_vibe_channel 只寫入指定頻道，不重寫整張表。"""
    captured: dict[str, _FakeCursor] = {}

    @contextlib.contextmanager
    def _fake_transaction(path: Path):
        cursor = _FakeCursor()
        captured["cursor"] = cursor
        yield cursor

    monkeypatch.setattr(vibe_storage, "db_transaction", _fake_transaction)
    vibe_storage.save_vibe_channel("ch_9", {"topic": {"summary": "s", "weight": 7}})

    cursor = captured["cursor"]
    assert cursor.executemany_calls == []
    insert_executes = [c for c in cursor.execute_calls if "INSERT" in c[0].upper()]
    assert len(insert_executes) == 1
    assert insert_executes[0][1][0] == "ch_9"


def test_save_vibe_channel_roundtrip(tmp_path: Path, monkeypatch: Any) -> None:
    """單頻道寫入可被 load_vibe_store 讀回。"""
    monkeypatch.setattr(vibe_storage, "MEMORY_DB_PATH", tmp_path / "sherry_memory.db")
    vibe_storage.save_vibe_channel("ch_9", {"topic": {"summary": "s", "weight": 7}})

    loaded = vibe_storage.load_vibe_store(tmp_path / "dummy.json")
    assert loaded["ch_9"]["topic"]["weight"] == 7
    assert loaded["ch_9"]["topic"]["summary"] == "s"


def test_vibe_persist_all_offloaded_from_event_loop() -> None:
    """全量落盤須在 worker thread 執行，且使用快照避免與 decay 競爭。"""
    worker_threads: list[threading.Thread] = []

    def _slow_save(store: Any, path: Any) -> None:
        worker_threads.append(threading.current_thread())
        time.sleep(_SLOW_IO_SEC)

    bot = MagicMock()
    cog = vibe_cog.VibeManager(bot)
    cog._store = {"ch_1": {"topic": {"summary": "s", "weight": 3}}}

    async def _run() -> float:
        with patch.object(vibe_cog, "save_vibe_store", side_effect=_slow_save):
            _result, gap = await _run_with_loop_gap(cog._persist_all())
        return gap

    gap = asyncio.run(_run())
    assert gap < _LOOP_GAP_THRESHOLD_SEC
    assert worker_threads and worker_threads[0] is not threading.main_thread()


def test_vibe_persist_channel_offloaded_from_event_loop() -> None:
    """單頻道落盤須在 worker thread 執行。"""
    worker_threads: list[threading.Thread] = []

    def _slow_save(channel_id: str, topics: Any) -> None:
        worker_threads.append(threading.current_thread())
        time.sleep(_SLOW_IO_SEC)

    bot = MagicMock()
    cog = vibe_cog.VibeManager(bot)
    cog._store = {"ch_1": {"topic": {"summary": "s", "weight": 3}}}

    async def _run() -> float:
        with patch.object(vibe_cog, "save_vibe_channel", side_effect=_slow_save):
            _result, gap = await _run_with_loop_gap(cog._persist_channel("ch_1"))
        return gap

    gap = asyncio.run(_run())
    assert gap < _LOOP_GAP_THRESHOLD_SEC
    assert worker_threads and worker_threads[0] is not threading.main_thread()


def test_vibe_cog_load_offloads_initial_read() -> None:
    """cog_load 的初始 SQLite 讀取不得阻塞事件循環。"""
    worker_threads: list[threading.Thread] = []
    store = {"ch_1": {"topic": {"summary": "s", "weight": 3}}}

    def _slow_load(path: Path) -> dict[str, Any]:
        worker_threads.append(threading.current_thread())
        time.sleep(_SLOW_IO_SEC)
        return store

    class _FakeBot:
        async def wait_until_ready(self) -> None:
            return None

    cog = vibe_cog.VibeManager(_FakeBot())  # type: ignore[arg-type]

    async def _run() -> float:
        with patch.object(vibe_cog, "load_vibe_store", side_effect=_slow_load):
            _result, gap = await _run_with_loop_gap(cog.cog_load())
        cog._decay_loop.cancel()
        await asyncio.sleep(0)
        return gap

    gap = asyncio.run(_run())
    assert cog._store == store
    assert gap < _LOOP_GAP_THRESHOLD_SEC
    assert worker_threads and worker_threads[0] is not threading.main_thread()


# ---------------------------------------------------------------------------
# Heartbeat：每輪任務數上限
# ---------------------------------------------------------------------------
def test_heartbeat_caps_tasks_per_round(monkeypatch: Any) -> None:
    """每輪心跳最多處理設定的任務數，避免任務過多時卡住或灌爆佇列。"""
    monkeypatch.setattr(heartbeat_cog.settings, "HEARTBEAT_MAX_TASKS_PER_ROUND", 2)
    tasks = [
        MagicMock(task_type="time", title=f"task_{i}")
        for i in range(5)
    ]
    evaluated: list[str] = []

    class _FakeBot:
        async def wait_until_ready(self) -> None:
            return None

    cog = heartbeat_cog.HeartbeatCog(_FakeBot())  # type: ignore[arg-type]

    async def _evaluate(task: Any, now_tw: Any, today_str: str) -> None:
        evaluated.append(task.title)

    cog._evaluate_time_task = _evaluate  # type: ignore[method-assign]

    async def _run() -> None:
        with patch.object(heartbeat_cog, "parse_heartbeat_markdown", return_value=tasks):
            await heartbeat_cog.HeartbeatCog._heartbeat_loop.coro(cog)

    asyncio.run(_run())
    assert evaluated == ["task_0", "task_1"]
