"""tests/unit/test_sys_inspector.py
==================================
驗證微核心系統監控 (core/sys_inspector) 採樣、格式化、服務門面與對話工具。
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch

import discord
import pytest

from core.sys_inspector.formatter import format_compact_system_report
from core.sys_inspector.sampler import get_system_metrics, get_system_metrics_async
from core.sys_inspector.service import SysInspectorService
from handlers.social.contracts import ToolExecutionStatus, ToolResult
from handlers.social.tools.sys_inspect_tool import SystemInspectTool


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_sampler_sync_structure() -> None:
    """測試同步採樣回傳字典具備所有預期鍵值與正確型別。"""
    metrics = get_system_metrics()
    assert "cpu" in metrics
    assert "percent" in metrics["cpu"]
    assert "count" in metrics["cpu"]

    assert "ram" in metrics
    assert "used_gb" in metrics["ram"]
    assert "total_gb" in metrics["ram"]
    assert "percent" in metrics["ram"]

    assert "network" in metrics
    assert "sent_rate_kbps" in metrics["network"]

    assert "gpu" in metrics
    assert isinstance(metrics["gpu"], list)

    assert "process" in metrics
    assert "pid" in metrics["process"]
    assert "memory_rss_mb" in metrics["process"]
    assert "threads" in metrics["process"]

    assert "timestamp" in metrics
    assert metrics["timestamp"] > 0


@pytest.mark.anyio
async def test_sampler_async_cache() -> None:
    """測試非同步採樣具備 TTL 防抖快取機制。"""
    m1 = await get_system_metrics_async(ttl=5.0)
    m2 = await get_system_metrics_async(ttl=5.0)
    # 在 TTL 內應命中同一個快取字典物件
    assert m1 is m2

    # 模擬超過 TTL
    with patch("core.sys_inspector.sampler._LAST_METRICS_TIME", time.time() - 10.0):
        m3 = await get_system_metrics_async(ttl=5.0)
        assert isinstance(m3, dict)


@pytest.mark.anyio
async def test_sampler_async_coalesces_concurrent_misses() -> None:
    """測試並行快取 misses 僅執行一次系統採樣。"""
    import core.sys_inspector.sampler as sampler

    sampler._CACHED_SYSTEM_METRICS = {}
    sampler._LAST_METRICS_TIME = 0.0
    sampler._METRICS_SAMPLE_LOCK = None
    call_count = 0

    def sample() -> dict[str, float]:
        nonlocal call_count
        call_count += 1
        time.sleep(0.05)
        return {"timestamp": float(call_count)}

    try:
        with patch("core.sys_inspector.sampler.get_system_metrics", side_effect=sample):
            results = await asyncio.gather(*(get_system_metrics_async(ttl=60.0) for _ in range(8)))
    finally:
        sampler._CACHED_SYSTEM_METRICS = {}
        sampler._LAST_METRICS_TIME = 0.0
        sampler._METRICS_SAMPLE_LOCK = None

    assert call_count == 1
    assert all(result is results[0] for result in results)


def test_formatter_public_and_admin() -> None:
    """測試高密度格式化器在管理員與一般使用者模式下的輸出差異。"""
    fake_metrics = {
        "cpu": {"percent": 15.5, "count": 16},
        "ram": {"used_gb": 6.5, "total_gb": 32.0, "percent": 20.3},
        "gpu": [
            {
                "name": "NVIDIA GeForce RTX 4090",
                "utilization": 30,
                "memory_used_mb": 6144,
                "memory_total_mb": 24576,
                "temperature_c": 52,
            }
        ],
        "process": {
            "pid": 4321,
            "memory_rss_mb": 1250.0,
            "threads": 18,
        },
    }

    # 一般使用者：輸出精簡，不含 PID 等機敏除錯資訊
    pub_rep = format_compact_system_report(fake_metrics, is_admin=False)
    assert "CPU: 15.5%" in pub_rep
    assert "RTX 4090" in pub_rep
    assert "PID" not in pub_rep
    assert "運作正常" in pub_rep

    # 管理員：輸出完整進程 PID、RSS 與執行緒
    adm_rep = format_compact_system_report(fake_metrics, is_admin=True)
    assert "CPU: 15.5%" in adm_rep
    assert "PID: 4321" in adm_rep
    assert "1250.0MB" in adm_rep
    assert "執行緒: 18" in adm_rep


@pytest.mark.anyio
async def test_service_facade() -> None:
    """測試 SysInspectorService 門面方法調用正確。"""
    report = await SysInspectorService.get_compact_report(is_admin=True)
    assert isinstance(report, str)
    assert "【主機硬體】" in report
    assert "【雪莉進程】" in report

    raw = await SysInspectorService.get_metrics_async()
    assert isinstance(raw, dict)
    assert "cpu" in raw


@pytest.mark.anyio
async def test_sys_inspect_tool_execution() -> None:
    """測試 SystemInspectTool 執行與回傳 ToolResult。"""
    tool = SystemInspectTool()
    assert tool.intent_name == "SYSTEM_INSPECT"

    bot = MagicMock()
    msg = MagicMock(spec=discord.Message)
    msg.author = MagicMock(spec=discord.Member)
    msg.author.id = 12345
    msg.guild = None

    with patch("handlers.social.tools.sys_inspect_tool.is_admin_user", return_value=False):
        res = await tool.execute(bot, msg, user_query="看看系統狀態", vibe_snippet="", full_context="")
        assert isinstance(res, ToolResult)
        assert res.status == ToolExecutionStatus.SUCCESS
        assert "【主機硬體】" in res.summary_text
        assert "【雪莉狀態】" in res.summary_text


@pytest.mark.anyio
async def test_web_ui_server_delegation() -> None:
    """驗證 web_ui/server.py 成功委派至 core.sys_inspector.sampler。"""
    from web_ui.server import _async_get_system_metrics

    assert _async_get_system_metrics is get_system_metrics_async

    m_async = await _async_get_system_metrics()
    assert "cpu" in m_async
    assert "gpu" in m_async
