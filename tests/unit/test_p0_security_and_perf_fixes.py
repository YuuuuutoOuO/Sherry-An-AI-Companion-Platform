"""
tests/unit/test_p0_security_and_perf_fixes.py
=============================================
全面驗證 P0 緊急資安漏洞防護、效能優化與記憶體洩漏修復。

涵蓋項目：
1. SSRF 攻擊防禦 (core.guard.is_safe_public_url 與 TrafilaturaScraperClient)
2. Web UI 預設阻斷鑑權防線 (訪客公開讀取 vs 管理員機敏端點與寫入阻斷)
3. 遊戲遊樂場 IP 滑動窗口頻率限制 (Game Center IP Rate Limiter)
4. Spark Clients (StockClient, WeatherClient, RssClient) 持久連線池與重用
5. 記憶體洩漏修復 (RateLimiter, CoreGuard, StockTool Cache)
6. 繪圖與搜圖標籤候選詞滑動窗口哈希查找正確性
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from core.guard import CoreGuard, is_safe_public_url
from handlers.social.rate_limiter import RateLimiter
from handlers.social.tools.draw_tool import DrawTool
from handlers.social.tools.stock_tool import (
    _MAX_STOCK_CACHE_SIZE,
    _STOCK_DATA_CACHE,
    _store_stock_cache,
)
from handlers.spark.clients.rss_client import RssClient
from handlers.spark.clients.stock_client import StockClient
from handlers.spark.clients.weather_client import WeatherClient
from mod.deep_search.repositories.scraper_client import TrafilaturaScraperClient
from mod.game_center.web import _IP_RATE_LIMITS, _MAX_POST_CALLS_PER_MIN, _check_ip_rate_limit
from web_ui.server import app


# ===========================================================================
# 1. SSRF 攻擊防護測試
# ===========================================================================

def test_is_safe_public_url_blocks_internal_and_cloud_metadata() -> None:
    """測試 is_safe_public_url 嚴格阻擋 Localhost、私有網段、雲端 Metadata 與特殊協定。"""
    # 阻擋本機與回環位址
    assert not is_safe_public_url("http://127.0.0.1/admin")
    assert not is_safe_public_url("http://localhost:8000/metrics")
    assert not is_safe_public_url("http://[::1]/status")
    assert not is_safe_public_url("http://0.0.0.0/")

    # 阻擋私有 IP 網段
    assert not is_safe_public_url("http://192.168.1.1/router")
    assert not is_safe_public_url("http://10.0.0.5:8080/")
    assert not is_safe_public_url("http://172.16.0.1/")

    # 阻擋雲端 Metadata (AWS/GCP/Azure: 169.254.169.254)
    assert not is_safe_public_url("http://169.254.169.254/latest/meta-data/")
    assert not is_safe_public_url("http://100.64.0.1/")

    # 阻擋非 HTTP/HTTPS 協定
    assert not is_safe_public_url("file:///etc/passwd")
    assert not is_safe_public_url("gopher://127.0.0.1:6379/")
    assert not is_safe_public_url("ftp://example.com/test")

    # 允許正常合法公網 URL
    with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 80))]):
        assert is_safe_public_url("https://example.com/news")
        assert is_safe_public_url("http://google.com/search?q=test")


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_deep_search_scraper_client_blocks_ssrf(anyio_backend: str) -> None:
    """測試 TrafilaturaScraperClient 對內網或惡意 SSRF 網址直接拒絕抓取並返回 None。"""
    client = TrafilaturaScraperClient()
    
    # 測試對內網 URL 的阻擋
    res_local = await client.scrape("http://127.0.0.1:8080/secret")
    assert res_local is None

    res_meta = await client.scrape("http://169.254.169.254/latest/meta-data/")
    assert res_meta is None

    res_file = await client.scrape("file:///C:/Windows/win.ini")
    assert res_file is None


# ===========================================================================
# 2. Web UI 預設阻斷鑑權防線測試
# ===========================================================================

@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_web_ui_public_vs_admin_auth_guards(anyio_backend: str) -> None:
    """測試 Web UI 訪客模式允許公開模組讀取，但機敏端點與未授權寫入操作一律強制阻斷 (401)。"""
    client = app.test_client()

    with patch("web_ui.server._is_admin_request", return_value=False):
        # 1. 訪客公開讀取端點放行 (200 / 正常響應)
        resp_status = await client.get("/api/services/status")
        assert resp_status.status_code == 200

        resp_modules = await client.get("/api/modules")
        assert resp_modules.status_code == 200

        # 2. 訪客訪問機敏端點直接被 before_request 阻斷 (401)
        resp_kpi = await client.get("/api/system/audit/kpi")
        assert resp_kpi.status_code == 401

        resp_queue = await client.get("/api/brain/queue")
        assert resp_queue.status_code == 401
        data_queue = await resp_queue.get_json()
        assert data_queue["status"] == "error"
        assert "需要管理員權限" in data_queue["message"]

        resp_settings = await client.get("/api/settings")
        assert resp_settings.status_code == 401

        resp_soul = await client.get("/api/soul/content")
        assert resp_soul.status_code == 401

        resp_audit_records = await client.get("/api/system/audit/records/1")
        assert resp_audit_records.status_code == 401

        # 3. 訪客寫入操作 (POST/PUT/DELETE) 即使未在特定路由處理亦被全域攔截 (401)
        resp_update = await client.post("/api/update")
        assert resp_update.status_code == 401

        resp_restart = await client.post("/api/restart")
        assert resp_restart.status_code == 401


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_web_ui_guest_logs_blocked(anyio_backend: str) -> None:
    """R3：未登入訪客存取日誌端點（含 bot.log）一律被 before_request 阻斷 (401)。"""
    client = app.test_client()

    with patch("web_ui.server._is_admin_request", return_value=False):
        # 訪客讀取 bot.log 被拒絕（R3 起日誌面全面需管理員）
        resp_bot_log = await client.get("/api/logs?file=bot.log")
        assert resp_bot_log.status_code == 401
        data_bot_log = await resp_bot_log.get_json()
        assert data_bot_log["status"] == "error"


# ===========================================================================
# 3. 遊戲遊樂場 IP 頻率限制測試
# ===========================================================================

def test_game_center_ip_rate_limiting() -> None:
    """測試 Game Center IP 滑動窗口限流防護與過期自動回收。"""
    _IP_RATE_LIMITS.clear()
    test_ip = "192.0.2.1"

    # 在限制範圍內均允許
    for _ in range(_MAX_POST_CALLS_PER_MIN):
        assert _check_ip_rate_limit(test_ip) is True

    # 超過上限拒絕請求
    assert _check_ip_rate_limit(test_ip) is False

    # 模擬時間經過 61 秒後自動重設
    _IP_RATE_LIMITS[test_ip] = [time.monotonic() - 65.0]
    assert _check_ip_rate_limit(test_ip) is True


# ===========================================================================
# 4. Spark Clients 連線池與資源管理測試
# ===========================================================================

@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_spark_clients_session_reuse_and_close(anyio_backend: str) -> None:
    """測試 Spark Clients (Stock, Weather, RSS) 複用 ClientSession 並能安全關閉。"""
    # StockClient
    stock_cli = StockClient()
    session1 = await stock_cli._get_session()
    session2 = await stock_cli._get_session()
    assert session1 is session2  # 驗證 session 複用，不重複建立
    assert not session1.closed
    await stock_cli.close()
    assert session1.closed

    # WeatherClient
    weather_cli = WeatherClient()
    w_session1 = await weather_cli._get_session()
    w_session2 = await weather_cli._get_session()
    assert w_session1 is w_session2
    await weather_cli.close()
    assert w_session1.closed

    # RssClient
    rss_cli = RssClient()
    r_session1 = await rss_cli._get_session()
    r_session2 = await rss_cli._get_session()
    assert r_session1 is r_session2
    await rss_cli.close()
    assert r_session1.closed


# ===========================================================================
# 5. 記憶體洩漏修復驗證
# ===========================================================================

def test_rate_limiter_memory_leak_prevention() -> None:
    """測試 RateLimiter 與 CoreGuard 在使用者無近期請求時自動釋放字典鍵值。"""
    limiter = RateLimiter()
    uid = 999999

    # 1. 產生請求
    assert limiter.is_allowed(uid) is True
    assert uid in limiter._user_timestamps

    # 2. 模擬所有請求時間戳記過期
    limiter._user_timestamps[uid] = [time.time() - 3600.0]
    limiter.cleanup_expired(uid)

    # 3. 驗證 key 已被 pop，字典不發生無限增長
    assert uid not in limiter._user_timestamps
    assert limiter.get_remaining_cooldown(uid) == 0.0


def test_core_guard_rate_limit_memory_leak_prevention() -> None:
    """測試 CoreGuard.check_rate_limit 在請求過期後自動回收 dict key。"""
    guard = CoreGuard()
    uid = 888888
    bucket_key = (uid, "default")

    # 1. 呼叫計次
    allowed, cooldown = guard.check_rate_limit(uid, max_calls=3, window_sec=1.0)
    assert allowed is True
    assert bucket_key in guard._user_call_history

    # 2. 模擬時間過期
    guard._user_call_history[bucket_key] = [time.monotonic() - 10.0]
    allowed_after, _ = guard.check_rate_limit(uid, max_calls=3, window_sec=1.0)
    assert allowed_after is True
    assert len(guard._user_call_history[bucket_key]) == 1  # 舊記錄被清理，僅留新時間戳記


def test_stock_tool_cache_eviction_bounded_size() -> None:
    """測試 StockTool 的股市快取具有容量上限與自動淘汰機制。"""
    _STOCK_DATA_CACHE.clear()
    now = time.time()

    # 填滿快取至容量上限
    for i in range(_MAX_STOCK_CACHE_SIZE):
        _store_stock_cache(f"TICKER_{i}", now - float(i), b"png", {"price": 100})

    assert len(_STOCK_DATA_CACHE) == _MAX_STOCK_CACHE_SIZE

    # 再存入一筆，驗證不會超出上限且自動淘汰最舊項目
    _store_stock_cache("NEW_TICKER", now, b"png_new", {"price": 200})
    assert len(_STOCK_DATA_CACHE) <= _MAX_STOCK_CACHE_SIZE
    assert "NEW_TICKER" in _STOCK_DATA_CACHE


# ===========================================================================
# 6. 標籤候選詞滑動窗口哈希查找優化驗證
# ===========================================================================

def test_draw_tool_tag_extraction_sliding_window() -> None:
    """測試 DrawTool 的 _extract_danbooru_tags 透過滑動窗口哈希精準萃取標籤。"""
    tool = DrawTool()
    tool._pixiv_dict = {
        "星野": "hoshino_(blue_archive)",
        "蔚藍檔案": "blue_archive",
        "泳裝": "swimsuit",
        "水手服": "sailor_uniform",
    }

    query = "請幫我畫蔚藍檔案星野穿泳裝"
    tags = tool._extract_danbooru_tags(query)

    assert "blue_archive" in tags
    assert "hoshino_(blue_archive)" in tags
    assert "swimsuit" in tags
