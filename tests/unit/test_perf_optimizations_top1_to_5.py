"""
tests/unit/test_perf_optimizations_top1_to_5.py
================================================
驗證 Top 1 至 Top 5 效能優化項目之單元測試：
1. Top 1: Web UI /api/services/status 並行探測與快取機制
2. Top 2: SysMonitor 採樣隔離至非同步執行緒
3. Top 3: 補齊連線與資源釋放保護 (world_memory, web_ui total_cnt==0, plt.close finally, tts empty_cache)
4. Top 4: Pixiv 搜圖標籤候選詞滑動窗口哈希查找
5. Top 5: Web UI 日誌端點反向分頁讀取
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
import importlib.util
from unittest.mock import MagicMock, patch

import pytest

import settings
from handlers.memory.world_memory import compress_daily_events
from handlers.social.tools.image_tool import ImageSearchTool
from handlers.social.tools.stock_tool import _fetch_and_plot
from web_ui.server import (
    app,
    _count_file_lines,
    _read_lines_reversed,
    _LINE_COUNT_CACHE,
)


# ===========================================================================
# Top 1: Web UI /api/services/status 並行探測與快取
# ===========================================================================

@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_services_status_parallel_and_cache(anyio_backend: str) -> None:
    """測試 /api/services/status 使用並行探測且快取生效。"""
    import web_ui.server as server_mod

    # 重設快取
    server_mod._SERVICES_CACHE = None
    server_mod._SERVICES_CACHE_TIME = 0.0

    call_count = 0

    class DummyResponse:
        def __init__(self, status_code: int = 200, json_data: dict | None = None) -> None:
            self.status_code = status_code
            self._json_data = json_data or {}

        def json(self) -> dict:
            return self._json_data

    async def mock_get(url: str, *args: object, **kwargs: object) -> DummyResponse:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.01)
        if "tags" in url:
            return DummyResponse(200, {"models": [{"name": "qwen2.5:7b"}]})
        return DummyResponse(200, {"data": [{"id": "model-1"}]})

    client = app.test_client()

    with patch("httpx.AsyncClient.get", side_effect=mock_get):
        # 第一次呼叫：觸發並行探測
        resp1 = await client.get("/api/services/status")
        assert resp1.status_code == 200
        data1 = await resp1.get_json()
        assert "services" in data1
        assert len(data1["services"]) == 5
        assert call_count == 5

        # 第二次呼叫：在 TTL (5s) 內應命中快取，不再次發起 HTTP 請求
        resp2 = await client.get("/api/services/status")
        assert resp2.status_code == 200
        data2 = await resp2.get_json()
        assert data2 == data1
        assert call_count == 5  # 呼叫計數未增加，驗證快取命中


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_services_status_probe_exception_fallback(anyio_backend: str) -> None:
    """測試當單一服務探測發生例外時，該服務仍會以 offline 狀態返回，維持 5 大服務卡片完整。"""
    import web_ui.server as server_mod

    server_mod._SERVICES_CACHE = None
    server_mod._SERVICES_CACHE_TIME = 0.0

    client = app.test_client()
    with patch("web_ui.server._probe_freetoken", side_effect=RuntimeError("MoE connection reset by peer")):
        resp = await client.get("/api/services/status")
        assert resp.status_code == 200
        data = await resp.get_json()
        assert len(data["services"]) == 5
        freetoken_entry = next(s for s in data["services"] if s["id"] == "freetoken")
        assert freetoken_entry["status"] == "offline"
        assert "MoE connection reset" in freetoken_entry["details"]


# ===========================================================================
# Top 2: SysMonitor 採樣隔離至非同步執行緒
# ===========================================================================

@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_sys_monitor_sample_system_in_thread(anyio_backend: str) -> None:
    """測試 core.sys_inspector 採樣透過 asyncio.to_thread 隔離硬體 I/O。"""
    from core.sys_inspector.sampler import get_system_metrics_async
    dummy_sample = {
        "cpu": {"percent": 15.0},
        "ram": {"percent": 50.0},
        "network": {},
        "gpu": [],
        "timestamp": 123456.0,
    }

    with patch("core.sys_inspector.sampler.get_system_metrics", return_value=dummy_sample) as mock_sample:
        with patch("asyncio.to_thread", wraps=asyncio.to_thread) as mock_to_thread:
            # 清除快照強制採樣
            import core.sys_inspector.sampler as s
            s._CACHED_SYSTEM_METRICS = {}
            s._LAST_METRICS_TIME = 0.0

            res = await get_system_metrics_async()
            mock_to_thread.assert_called_once_with(mock_sample)
            assert res["cpu"]["percent"] == 15.0


# ===========================================================================
# Top 3: 補齊連線與資源釋放保護
# ===========================================================================

def test_world_memory_compress_connection_closed() -> None:
    """測試 world_memory.compress_daily_events 在執行後確實關閉 DB 連線。"""
    records = [{"event_id": "ev_1", "server_id": "srv_1", "ts": "2026-09-11", "summary": "test"}]

    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchall.return_value = []

    with patch("handlers.memory.world_memory.read_world_events", return_value=records):
        with patch("handlers.memory.world_memory.get_db_connection", return_value=mock_conn):
            with patch("handlers.memory.world_memory.db_transaction"):
                compress_daily_events("2026-09-11", "srv_1")

                # 確保 conn.close() 在 finally 區塊確實被呼叫
                mock_conn.close.assert_called_once()


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_web_ui_logs_total_cnt_zero_closes_connection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, anyio_backend: str) -> None:
    """測試 Web UI /api/logs 在 SQLite 表格為空 (total_cnt == 0) 時連線亦正常關閉且回傳正確。"""
    test_data_dir = tmp_path / "data"
    test_data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "DATA_DIR", test_data_dir)

    chat_db = test_data_dir / "chat_history.db"
    conn = sqlite3.connect(chat_db)
    conn.execute("""
        CREATE TABLE runtime_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            event TEXT
        )
    """)
    conn.close()

    mock_conn = MagicMock(wraps=sqlite3.connect(chat_db))
    with patch("core.db.get_db_connection", return_value=mock_conn):
        client = app.test_client()
        resp = await client.get("/api/logs?file=chat_history.db%20[runtime_events]&page=1&size=10")
        assert resp.status_code == 200
        data = await resp.get_json()
        assert data["total"] == 0
        assert data["entries"] == []
        # 驗證 conn.close() 確實被執行
        mock_conn.close.assert_called_once()


def test_stock_tool_plt_close_in_finally() -> None:
    """測試 StockTool._fetch_and_plot 在繪圖發生異常時，亦會在 finally 確實關閉 figure。"""
    import pandas as pd
    import numpy as np

    dates = pd.date_range("2026-01-01", periods=30)
    dummy_df = pd.DataFrame({
        "Open": np.linspace(100, 120, 30),
        "High": np.linspace(102, 122, 30),
        "Low": np.linspace(98, 118, 30),
        "Close": np.linspace(101, 121, 30),
        "Volume": np.full(30, 1000),
    }, index=dates)

    mock_fig = MagicMock()
    mock_fig.savefig.side_effect = RuntimeError("Plot failed")

    with patch("yfinance.download", return_value=dummy_df):
        with patch("mplfinance.plot", return_value=(mock_fig, MagicMock())):
            with patch("matplotlib.pyplot.close") as mock_close:
                img_bytes, indicators = _fetch_and_plot("2330")
                assert img_bytes is None
                assert indicators == {}
                mock_close.assert_any_call(mock_fig)


def test_tts_unload_cuda_empty_cache() -> None:
    """測試 TextToSpeech.unload() 正確嘗試釋放 CUDA 顯存。"""
    tts_path = Path(__file__).resolve().parent.parent.parent / "handlers" / "voice" / "tts.py"
    spec = importlib.util.spec_from_file_location("handlers.voice.tts", tts_path)
    assert spec is not None and spec.loader is not None
    tts_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tts_module)

    tts = tts_module.TextToSpeech.__new__(tts_module.TextToSpeech)
    tts._model = MagicMock()

    mock_torch = MagicMock()
    mock_torch.cuda.is_available.return_value = True

    with patch.dict("sys.modules", {"torch": mock_torch}):
        tts.unload()
        assert tts._model is None
        mock_torch.cuda.empty_cache.assert_called_once()


# ===========================================================================
# Top 4: Pixiv 搜圖標籤候選詞滑動窗口哈希查找
# ===========================================================================

def test_pixiv_candidate_hash_lookup() -> None:
    """測試 Pixiv 搜圖標籤候選詞滑動窗口哈希查找正確解析標籤。"""
    tool = ImageSearchTool()

    # 模擬輸入包含字典標籤字串
    query = "請幫我找一張芙莉蓮穿泳裝吃東西在雪地的圖"
    t1, t2, t3 = tool._resolve_tag_tiers(query)

    # 驗證 Tier 1 (角色) 包含芙莉蓮
    assert "frieren_(sousou_no_frieren)" in t1
    # 驗證 Tier 2 包含泳裝
    assert "swimsuit" in t2
    # 驗證 Tier 3 包含吃東西、雪地等修飾詞
    assert any(tag in ("snow", "eating") for tag in t3 or t2)


def test_pixiv_candidate_adjacent_matches_and_masking() -> None:
    """測試相鄰候選詞不會因字串縮短而漏檢，且長詞優先於子詞。"""
    tool = ImageSearchTool()

    # 注入假字典模擬相鄰詞與長詞
    tool._pixiv_dict = {
        "一二三四": "tag_one",
        "五六七八": "tag_two",
        "超長作品名測試": "tag_long_work",
        "作品名": "tag_short_work",
    }
    tool._tag_metadata = {
        "tag_one": {"category": 4},
        "tag_two": {"category": 4},
        "tag_long_work": {"category": 3},
    }

    t1, t2, t3 = tool._resolve_tag_tiers("請尋找一二三四五六七八還有超長作品名測試")

    # 驗證相鄰的兩個 4 字詞皆被命中，無漏檢
    assert "tag_one" in t1
    assert "tag_two" in t1
    # 驗證最長匹配優先，長詞命中後子詞不重複觸發
    assert "tag_long_work" in t1
    assert "tag_short_work" not in t1 and "tag_short_work" not in t2 and "tag_short_work" not in t3


# ===========================================================================
# Top 5: Web UI 日誌端點反向分頁讀取
# ===========================================================================

def test_read_lines_reversed_and_count_lines(tmp_path: Path) -> None:
    """測試 _count_file_lines 與 _read_lines_reversed 反向分頁正確性與邊界。"""
    log_file = tmp_path / "test.log"

    # 1. 測試空檔案
    log_file.write_text("", encoding="utf-8")
    assert _count_file_lines(log_file) == 0
    assert _read_lines_reversed(log_file, limit=10, skip=0) == []

    # 2. 測試 50 行日誌反向分頁
    lines = [f"Log Entry Line #{i}" for i in range(1, 51)]
    log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert _count_file_lines(log_file) == 50

    # 第 1 頁 (size=5, skip=0) 應為最新 5 行 (50, 49, 48, 47, 46)
    page1 = _read_lines_reversed(log_file, limit=5, skip=0)
    assert len(page1) == 5
    assert page1[0] == "Log Entry Line #50"
    assert page1[-1] == "Log Entry Line #46"

    # 第 2 頁 (size=5, skip=5) 應為 (45, 44, 43, 42, 41)
    page2 = _read_lines_reversed(log_file, limit=5, skip=5)
    assert len(page2) == 5
    assert page2[0] == "Log Entry Line #45"
    assert page2[-1] == "Log Entry Line #41"

    # 測試結尾無換行符之容錯
    no_trailing = tmp_path / "no_nl.log"
    no_trailing.write_bytes(b"A\nB\nC")
    assert _count_file_lines(no_trailing) == 3
    assert _read_lines_reversed(no_trailing, limit=3, skip=0) == ["C", "B", "A"]


def test_read_lines_reversed_blank_lines_and_indentation(tmp_path: Path) -> None:
    """測試日誌檔含空行時不產生幽靈分頁，且行首縮排完整保留。"""
    log_file = tmp_path / "indented.log"
    content = "Entry 1\n\n    Traceback at line 10\n\nEntry 2\n\n"
    log_file.write_text(content, encoding="utf-8")

    # 驗證總行數計數與讀取到的有效行數一致 (3 行有效)
    total_cnt = _count_file_lines(log_file)
    assert total_cnt == 3

    reversed_lines = _read_lines_reversed(log_file, limit=10, skip=0)
    assert len(reversed_lines) == 3
    assert reversed_lines[0] == "Entry 2"
    # 保留行首 4 個空格之縮排
    assert reversed_lines[1] == "    Traceback at line 10"
    assert reversed_lines[2] == "Entry 1"


def test_count_file_lines_cache(tmp_path: Path) -> None:
    """測試 _count_file_lines 快取機制在檔案 mtime/size 未改變時直接返回快取。"""
    log_file = tmp_path / "cache_test.log"
    log_file.write_text("Line 1\nLine 2\n", encoding="utf-8")

    cnt1 = _count_file_lines(log_file)
    assert cnt1 == 2

    cache_key = str(log_file.resolve())
    assert cache_key in _LINE_COUNT_CACHE

    # 驗證快取生效
    cnt2 = _count_file_lines(log_file)
    assert cnt2 == 2


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_web_ui_reverse_paginated_log_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, anyio_backend: str) -> None:
    """測試 GET /api/logs?file=bot.log 走反向分頁讀取端點流程。"""
    test_data_dir = tmp_path / "data"
    test_data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "DATA_DIR", test_data_dir)

    bot_log = test_data_dir / "bot.log"
    log_lines = [f"2026-09-11 10:00:{i:02d} INFO event message {i}" for i in range(1, 26)]
    bot_log.write_text("\n".join(log_lines) + "\n", encoding="utf-8")

    client = app.test_client()
    resp = await client.get("/api/logs?file=bot.log&page=1&size=5")
    assert resp.status_code == 200
    data = await resp.get_json()
    assert data["total"] == 25
    assert len(data["entries"]) == 5
    # 最新在第一筆
    assert "event message 25" in data["entries"][0]["message"]
    assert "event message 21" in data["entries"][-1]["message"]
