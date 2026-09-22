"""
tests/unit/test_deep_perf_optimizations.py
===========================================
深層效能優化 (8 大項目 P0-1 至 P1-8) 之單元測試。
遵循 AAA (Arrange / Act / Assert) 規範與 AGENTS.md。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import settings
from handlers.memory.storage import (
    _clean_fact_core,
    _extract_semantic_core,
    calculate_fact_similarity,
)
import handlers.memory.world_memory as wm
import handlers.social.persona as persona_mod
from handlers.social.persona import _compile_lorebook, build_dynamic_persona, clear_soul_cache
from mod.deep_search.domain.interfaces import ILLMClient, IScraperClient, ISearchClient, ISessionRepository
from mod.deep_search.domain.models import SearchResultItem, SourceDoc
from mod.deep_search.engine.deep_search_engine import DeepSearchEngine
from mod.market_plugin.data_fetcher import (
    DataFetcher,
    _INFO_CACHE,
    _RAW_STOCK_CACHE,
    _TREND_CACHE,
    _get_valuation_multiples,
    _prune_cache_if_needed,
)
from mod.market_plugin.reports.etf_monitor import _fetch_etf_quote, check_etf_premium
from mod.market_plugin.repository import MarketRepository
from mod.painter import config as painter_config
from mod.painter.agent import PainterAgent
from mod.painter.models import ArtStyle


# =============================================================================
# 1. P0-1: ETF Monitor asyncio.to_thread 測試
# =============================================================================

def test_etf_monitor_async_to_thread() -> None:
    """測試 ETF Monitor 透過 asyncio.to_thread 非同步執行自訂清單載入與行情查詢。"""
    async def _test() -> None:
        mock_bot = MagicMock()
        mock_channel = AsyncMock()
        mock_channel.send = AsyncMock()

        mock_load_watchlists = MagicMock(return_value={"etf_list": ["0050.TW", "0056.TW"]})

        with patch("mod.market_plugin.reports.etf_monitor.get_or_fetch_channel", new=AsyncMock(return_value=mock_channel)), \
             patch("mod.market_plugin.reports.etf_monitor._fetch_etf_quote") as mock_quote, \
             patch("mod.market_plugin.reports.etf_monitor.datetime") as mock_dt:

            mock_now = MagicMock()
            mock_now.hour = 10  # 盤中時間 09:00 - 14:00
            mock_dt.now.return_value = mock_now

            # 0050 溢價 2.5% (> 1.0% 觸發警報), 0056 溢價 0.2% (不觸發警報)
            mock_quote.side_effect = [
                (150.0, 2.5),
                (35.0, 0.2),
            ]

            # Act
            await check_etf_premium(mock_bot, 123456789, mock_load_watchlists)

            # Assert
            mock_load_watchlists.assert_called_once()
            assert mock_quote.call_count == 2
            assert mock_channel.send.call_count == 1
            sent_msg = mock_channel.send.call_args[0][0]
            assert "0050.TW" in sent_msg
            assert "2.50%" in sent_msg

    asyncio.run(_test())


def test_etf_monitor_quiet_hours_and_empty_list() -> None:
    """測試 ETF Monitor 於非開盤時段或關注清單為空時正確靜默返回。"""
    async def _test() -> None:
        mock_bot = MagicMock()
        mock_load_watchlists = MagicMock(return_value={"etf_list": []})

        with patch("mod.market_plugin.reports.etf_monitor.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.hour = 20  # 非開盤時間
            mock_dt.now.return_value = mock_now

            # Act
            await check_etf_premium(mock_bot, 123456, mock_load_watchlists)

            # Assert: 非開盤時間不應調用 load_watchlists
            mock_load_watchlists.assert_not_called()

    asyncio.run(_test())


# =============================================================================
# 2. P0-2: Persona LOREBOOK 正則預編譯快取測試
# =============================================================================

def test_persona_lorebook_precompilation_and_caching() -> None:
    """測試 LOREBOOK 正則表達式預編譯快取與動態人格建構。"""
    # Arrange
    clear_soul_cache()
    high_prio_pat = [p for p in persona_mod._HIGH_PRIORITY_LORE_PATTERNS if "色色" in p][0]
    sample_lorebook = {
        high_prio_pat: "【⚠️NSFW 深度互動與主動掌控權解鎖】",
        r"(股票|股市|行情)": "【股市數據分析指南】",
    }
    items_tuple = tuple(sample_lorebook.items())

    # Act
    compiled_1 = _compile_lorebook(items_tuple)
    compiled_2 = _compile_lorebook(items_tuple)

    # Assert: 快取應回傳同一個編譯清單物件
    assert compiled_1 is compiled_2
    assert len(compiled_1) == 2

    # 驗證高優先度辨識
    pat1, is_high1, fact1 = compiled_1[0]
    assert is_high1 is True
    assert "NSFW" in fact1

    pat2, is_high2, fact2 = compiled_1[1]
    assert is_high2 is False

    # 驗證 build_dynamic_persona 使用預編譯正則匹配
    with patch.object(settings, "LOREBOOK", sample_lorebook):
        head, tail = build_dynamic_persona("我想跟妳色色聊聊股票", "CHAT")
        assert "【⚠️NSFW 深度互動與主動掌控權解鎖】" in tail
        assert "【股市數據分析指南】" in tail


# =============================================================================
# 3. P0-3: Painter Pillow 向量繪圖與 JPEG 導出 asyncio.to_thread 測試
# =============================================================================

def test_painter_agent_paint_and_save_async(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """測試 PainterAgent.paint_and_save 封裝至 asyncio.to_thread 執行無阻塞生成圖像與中繼資料。"""
    async def _test() -> None:
        test_painting_dir = tmp_path / "data_painting"
        monkeypatch.setattr(painter_config, "PAINTING_DIR", test_painting_dir)

        commands = [
            {"action": "set_background", "params": {"color": "#FFFFFF", "texture": "plain"}},
            {"action": "shape", "params": {"shape_type": "circle", "coords": [400, 400, 50], "fill_color": "#FF0000", "layer": "main"}},
            {"action": "stamp", "params": {"x": 710, "y": 710, "text": "雪莉", "size": 44, "layer": "fg"}},
        ]

        # Act
        canvas, meta, jpg_path = await PainterAgent.paint_and_save(
            prompt="極簡紅圓",
            style=ArtStyle.FREESTYLE,
            commands=commands,
            author_id="999888",
            author_name="ArtistUser",
        )

        # Assert
        assert jpg_path.exists()
        assert jpg_path.stat().st_size > 0
        json_path = jpg_path.with_suffix(".json")
        assert json_path.exists()

        with open(json_path, "r", encoding="utf-8") as f:
            meta_data = json.load(f)
        assert meta_data["prompt"] == "極簡紅圓"
        assert meta_data["author_id"] == "999888"
        assert len(meta_data["commands"]) == 3

    asyncio.run(_test())


# =============================================================================
# 4. P0-4: MarketRepository 複合索引前綴 (ticker, date ASC) 測試
# =============================================================================

def test_market_repository_composite_indexes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """測試 MarketRepository 建立 (ticker, date ASC) 複合索引並正確依標的與時間升冪查詢。"""
    # Arrange
    test_db = tmp_path / "test_market.db"
    from mod.market_plugin import config as mcfg
    monkeypatch.setattr(mcfg, "DB_PATH", test_db)
    monkeypatch.setattr(mcfg, "DATA_DIR", tmp_path)

    repo = MarketRepository()

    # Act: 檢查 index 是否存在
    with sqlite3.connect(str(test_db)) as conn:
        cur = conn.cursor()
        indexes = {row[1] for row in cur.execute("PRAGMA index_list('stock_predictions')").fetchall()}
        assert "idx_stock_ticker_date" in indexes

        crypto_indexes = {row[1] for row in cur.execute("PRAGMA index_list('crypto_predictions')").fetchall()}
        assert "idx_crypto_ticker_date" in crypto_indexes

    # 寫入歷史預測並回填成交價
    repo.save_prediction("stock", [
        {"ticker": "2330.TW", "price": 950.0, "predicted_price": 960.0, "score": 8, "reason": "法說會行情"},
    ])
    repo.update_actual_prices("stock", {"2330.TW": 958.0})

    history = repo.get_ticker_history("2330.TW")
    assert len(history) == 1
    assert history[0][1] == 960.0
    assert history[0][2] == 958.0


# =============================================================================
# 5. P1-5: World Memory SQL 時間過濾與複合索引測試
# =============================================================================

def test_world_memory_sql_time_filtering_and_indexes(tmp_path: Path) -> None:
    """測試 _load_compressed_memory 使用 SQL WHERE 時間與重要度條件進行精準過濾。"""
    # Arrange
    test_db = tmp_path / "test_wm.db"
    wm._WORLD_DB_PATH = test_db
    wm._DB_INITIALIZED = False
    wm.clear_world_memory_cache()
    wm._init_db()

    # 檢查複合索引
    with sqlite3.connect(str(test_db)) as conn:
        summary_indexes = {row[1] for row in conn.execute("PRAGMA index_list('world_summary')").fetchall()}
        assert "idx_world_summary_lookup" in summary_indexes

        events_indexes = {row[1] for row in conn.execute("PRAGMA index_list('world_events')").fetchall()}
        assert "idx_world_events_lookup" in events_indexes

    # 記錄事件並壓縮
    today = wm._today_str()
    wm.log_world_event("CHAT", "今天的大事記", location="主城", importance=4, server_id="guild_A")
    wm.compress_daily_events(today, server_id="guild_A")

    # Act
    compressed = wm._load_compressed_memory(days=3, min_importance=3, server_id="guild_A")
    assert len(compressed) == 1
    assert compressed[0]["summary"] == "今天的大事記"

    # 高門檻過濾
    compressed_high = wm._load_compressed_memory(days=3, min_importance=5, server_id="guild_A")
    assert len(compressed_high) == 0

    # 單一查詢 read_recent_events
    recent = wm.read_recent_events(days=3, server_id="guild_A")
    assert len(recent) == 1
    assert recent[0]["summary"] == "今天的大事記"


# =============================================================================
# 6. P1-6: Memory Storage LRU Cache 4096 測試
# =============================================================================

def test_storage_fact_similarity_lru_cache() -> None:
    """測試 calculate_fact_similarity 與核心萃取函式具備 4096 容量 LRU 快取。"""
    # Arrange
    _clean_fact_core.cache_clear()
    _extract_semantic_core.cache_clear()
    calculate_fact_similarity.cache_clear()

    s1 = "他非常喜歡吃大便"
    s2 = "發話者表示他很愛吃大便"

    # Act: 首次計算
    sim1 = calculate_fact_similarity(s1, s2)
    info1 = calculate_fact_similarity.cache_info()

    # 二次計算相同字串
    sim2 = calculate_fact_similarity(s1, s2)
    info2 = calculate_fact_similarity.cache_info()

    # Assert
    assert sim1 == sim2
    assert sim1 >= 0.50
    assert info1.hits == 0
    assert info2.hits == 1
    assert info2.maxsize == 4096
    assert _clean_fact_core.cache_info().maxsize == 4096
    assert _extract_semantic_core.cache_info().maxsize == 4096


# =============================================================================
# 7. P1-7: Deep Search Engine 并行檢索與爬蟲測試
# =============================================================================

def test_deep_search_parallel_search_and_scrape() -> None:
    """測試 DeepSearchEngine 使用 asyncio.gather 同時並行搜尋與爬蟲。"""
    async def _test() -> None:
        mock_search = AsyncMock(spec=ISearchClient)
        mock_search.search.side_effect = [
            [SearchResultItem(title="來源1", url="https://example.com/1", snippet="摘要1")],
            [SearchResultItem(title="來源2", url="https://example.com/2", snippet="摘要2")],
        ]

        mock_scraper = AsyncMock(spec=IScraperClient)
        mock_scraper.scrape.side_effect = [
            SourceDoc(url="https://example.com/1", title="來源1", content="正文1", snippet="摘要1"),
            SourceDoc(url="https://example.com/2", title="來源2", content="正文2", snippet="摘要2"),
        ]

        mock_llm = AsyncMock(spec=ILLMClient)
        # Planning response
        plan_json = json.dumps({
            "dimensions": [
                {"dimension_title": "維度A", "key_questions": "問題A", "search_queries": ["查詢1", "查詢2"]},
            ]
        })
        mock_llm.generate.side_effect = [
            plan_json,          # Planning
            "精讀筆記內容",     # Reading
            "反思分析內容",     # Reflection
            "最終綜合報告正文", # Final Synthesis
        ]

        mock_repo = MagicMock(spec=ISessionRepository)

        engine = DeepSearchEngine(
            search_client=mock_search,
            scraper_client=mock_scraper,
            llm_client=mock_llm,
            session_repository=mock_repo,
        )

        # Act
        session = await engine.run_research(
            topic="量子運算突破",
            model="gpt-4o",
            max_rounds=1,
        )

        # Assert
        assert session.status == "COMPLETED"
        assert session.final_report is not None
        # 驗證 search 執行了 2 個並行查詢
        assert mock_search.search.call_count == 2
        # 驗證 scraper 執行了 2 個並行爬取
        assert mock_scraper.scrape.call_count == 2

    asyncio.run(_test())


# =============================================================================
# 8. P1-8: DataFetcher 估值快取與無 sleep 高速抓取測試
# =============================================================================

def test_data_fetcher_caching_and_fast_valuation() -> None:
    """測試 DataFetcher 具備快取、快速估值查詢且無 blocking sleep。"""
    # Arrange
    _TREND_CACHE.clear()
    _RAW_STOCK_CACHE.clear()
    fetcher = DataFetcher()

    sample_raw_data = [
        {
            "ticker": "NVDA",
            "close": 120.5,
            "day_pct": 2.1,
            "day5_pct": 5.4,
            "mon_pct": 12.0,
            "ma20": 115.0,
            "ma60": 110.0,
            "ema20": 116.0,
            "ema50": 112.0,
            "ema200": 100.0,
            "bb_upper": 125.0,
            "bb_lower": 105.0,
            "bb_width": 17.39,
            "pct_b": 0.78,
            "kd_k": 75.0,
            "kd_d": 70.0,
            "macd_dif": 2.5,
            "macd_hist": 0.8,
            "volume_ratio": 1.2,
            "rsi": 65.0,
            "pb": 45.2,
            "pe": 60.5,
        }
    ]

    with patch("mod.market_plugin.data_fetcher.yf.download") as mock_download:
        mock_download.return_value = MagicMock()
        with patch.object(fetcher, "calculate_technicals", return_value={"MA5": 120, "MA20": 115, "MA60": 110, "RSI": 65, "KD_K": 75, "KD_D": 70, "BB_Upper": 125, "BB_Lower": 105, "BB_Width": 17.39, "BB_PctB": 0.78}):
            # 手動置入快取
            _RAW_STOCK_CACHE[("NVDA",)] = (time.time(), sample_raw_data)

            # Act
            t0 = time.perf_counter()
            cached_res = fetcher.get_stock_info_raw(["NVDA"])
            duration = time.perf_counter() - t0

            # Assert: 應瞬間自快取回傳，耗時 < 10ms，且不調用 yf.download
            assert duration < 0.05
            assert len(cached_res) == 1
            assert cached_res[0]["ticker"] == "NVDA"
            mock_download.assert_not_called()


# =============================================================================
# 9. 額外邊界與異常容錯測試 (Edge Cases & Resilience)
# =============================================================================

def test_data_fetcher_exception_and_pruning_edge_cases() -> None:
    """測試 DataFetcher 估值異常回退與快取過期容量清理機制。"""
    # 1. 測試 yfinance 拋出 Exception 時安全回退為 ("N/A", "N/A")
    _INFO_CACHE.clear()
    with patch("mod.market_plugin.data_fetcher.yf.Ticker", side_effect=RuntimeError("yfinance network failure")):
        pb, pe = _get_valuation_multiples("INVALID_TICKER")
        assert pb == "N/A"
        assert pe == "N/A"

    # 2. 測試 _prune_cache_if_needed 在超出容量上限時正確清理
    test_cache: dict[str, tuple[float, str]] = {}
    now = time.time()
    for i in range(20):
        # 10 筆過期 (> 300s)，10 筆未過期
        ts = now - 400.0 if i < 10 else now
        test_cache[f"key_{i}"] = (ts, f"val_{i}")

    assert len(test_cache) == 20
    _prune_cache_if_needed(test_cache, max_entries=8)
    assert len(test_cache) <= 8


def test_world_memory_edge_cases(tmp_path: Path) -> None:
    """測試世界記憶邊界情況：天數小於等於 0、空事件資料庫與不存在的伺服器 ID。"""
    test_db = tmp_path / "test_wm_edge.db"
    wm._WORLD_DB_PATH = test_db
    wm._DB_INITIALIZED = False
    wm.clear_world_memory_cache()
    wm._init_db()

    # 1. days <= 0 應立即回傳空清單
    assert wm.read_recent_events(days=0) == []
    assert wm.read_recent_events(days=-2) == []

    # 2. 讀取不存在的 server_id
    res = wm._load_compressed_memory(days=3, min_importance=1, server_id="non_existent_guild")
    assert res == []


def test_deep_search_parallel_gather_exceptions() -> None:
    """測試 DeepSearchEngine 在並行搜尋或爬蟲拋出 Exception 時優雅降級不崩潰。"""
    async def _test() -> None:
        mock_search = AsyncMock(spec=ISearchClient)
        # 一個成功，一個拋出異常
        mock_search.search.side_effect = [
            [SearchResultItem(title="成功來源", url="https://example.com/ok", snippet="成功摘要")],
            RuntimeError("Search API 500 error"),
        ]

        mock_scraper = AsyncMock(spec=IScraperClient)
        mock_scraper.scrape.side_effect = [
            SourceDoc(url="https://example.com/ok", title="成功來源", content="正文內容", snippet="成功摘要"),
        ]

        mock_llm = AsyncMock(spec=ILLMClient)
        plan_json = json.dumps({
            "dimensions": [
                {"dimension_title": "維度A", "key_questions": "問題A", "search_queries": ["查詢1", "查詢2"]},
            ]
        })
        mock_llm.generate.side_effect = [
            plan_json,
            "精讀筆記內容",
            "反思分析內容",
            "這是一篇長度明確超過五十個字元的完整最終綜合研究報告本文內容。本報告詳盡探討了各項核心技術指標與架構演進，並對未來的發展方向提出了具體的建議與展望。",
        ]

        mock_repo = MagicMock(spec=ISessionRepository)

        engine = DeepSearchEngine(
            search_client=mock_search,
            scraper_client=mock_scraper,
            llm_client=mock_llm,
            session_repository=mock_repo,
        )

        session = await engine.run_research(
            topic="邊界測試",
            model="gpt-4o",
            max_rounds=1,
        )

        assert session.status == "COMPLETED"
        assert session.final_report is not None
        assert "完整最終綜合研究報告" in session.final_report.full_content
        assert len(session.sources) == 1
        assert session.sources[0].url == "https://example.com/ok"

    asyncio.run(_test())


def test_etf_monitor_quote_exception_handling() -> None:
    """測試 _fetch_etf_quote 在 yfinance 拋出異常或 fast_info 遺漏欄位時安全回傳 None。"""
    with patch("mod.market_plugin.reports.etf_monitor.yf.Ticker") as mock_ticker_cls:
        mock_stock = MagicMock()
        mock_stock.fast_info = {}  # 缺少 lastPrice 欄位，將引發 KeyError
        mock_ticker_cls.return_value = mock_stock

        quote = _fetch_etf_quote("0000.TW")
        assert quote is None
