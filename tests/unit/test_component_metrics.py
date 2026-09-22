"""
tests/unit/test_component_metrics.py
====================================
全景組件指標收集器 (ComponentMetricsCollector)、資料庫集中化與 WebUI 管理員權限端點單元測試。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.sys_inspector.config import ensure_migrated_databases, get_audit_db_path, get_sys_monitor_db_path
from core.sys_inspector.metrics import ComponentMetricsCollector, component_metrics
from handlers.social.observability import log_inference, log_tool_exec
from web_ui.server import app


@pytest.fixture
def anyio_backend() -> str:
    """指定 anyio 使用 asyncio 引擎。"""
    return "asyncio"


def test_collector_basic_recording_and_p95() -> None:
    """測試組件指標收集器之基礎記錄與 P95 延遲計算。"""
    collector = ComponentMetricsCollector()
    collector.reset()

    # 記錄 10 次延遲: 10, 20, 30, ..., 100 ms
    for i in range(1, 11):
        collector.record_execution("SEARCH", latency_ms=float(i * 10), success=True)

    summary = collector.get_summary()
    assert "components" in summary
    assert "SEARCH" in summary["components"]

    c = summary["components"]["SEARCH"]
    assert c["call_count"] == 10
    assert c["error_count"] == 0
    assert c["avg_latency_ms"] == pytest.approx(55.0, 0.1)
    # 10 個數值中第 95 百分位數應接近 95.5 或 95
    assert c["p95_latency_ms"] >= 90.0


def test_collector_token_and_error_tracking() -> None:
    """測試 Token 累積與錯誤資訊紀錄。"""
    collector = ComponentMetricsCollector()
    collector.reset()

    collector.record_execution(
        "BOT_BRAIN",
        latency_ms=120.0,
        prompt_tokens=500,
        completion_tokens=150,
        success=True,
    )
    collector.record_execution(
        "BOT_BRAIN",
        latency_ms=50.0,
        prompt_tokens=100,
        completion_tokens=0,
        success=False,
        error_msg="ConnectionTimeout",
    )

    summary = collector.get_summary()
    c = summary["components"]["BOT_BRAIN"]
    assert c["call_count"] == 2
    assert c["error_count"] == 1
    assert c["prompt_tokens"] == 600
    assert c["completion_tokens"] == 150
    assert c["last_error_msg"] == "ConnectionTimeout"


def test_collector_active_tasks_context_manager() -> None:
    """測試 track_active 脈絡管理器之活躍任務計數與自動記錄。"""
    collector = ComponentMetricsCollector()
    collector.reset()

    assert collector.get_component("PAINTER").active_tasks == 0

    with collector.track_active("PAINTER") as comp:
        assert collector.get_component("PAINTER").active_tasks == 1
        comp.prompt_tokens = 50
        comp.completion_tokens = 25

    # 退出後 active_tasks 還原為 0，且呼叫次數 + 1
    comp = collector.get_component("PAINTER")
    assert comp.active_tasks == 0
    assert comp.call_count == 1
    assert comp.prompt_tokens == 50
    assert comp.completion_tokens == 25


def test_collector_reset() -> None:
    """測試清空所有組件指標計數。"""
    collector = ComponentMetricsCollector()
    collector.record_execution("STOCK", latency_ms=80.0)
    assert collector.get_summary()["totals"]["total_calls"] > 0

    collector.reset()
    assert collector.get_summary()["totals"]["total_calls"] == 0
    assert collector.get_component("STOCK").call_count == 0


def test_database_migration_centralization() -> None:
    """驗證集中化資料庫結構與路徑存在。"""
    ensure_migrated_databases()

    audit_path = get_audit_db_path()
    sys_path = get_sys_monitor_db_path()

    assert audit_path.parent == sys_path.parent
    assert audit_path.name == "audit.db"
    assert sys_path.name == "sys_monitor.db"
    assert audit_path.exists()
    assert sys_path.exists()


def test_observability_hooks_collect_metrics() -> None:
    """驗證 log_tool_exec 與 log_inference 會自動累積至 component_metrics。"""
    component_metrics.reset()

    # 測試 log_tool_exec
    log_tool_exec(
        tool_name="PIXIV",
        tool_input="query=genshin",
        observation="Found 3 results",
        latency_ms=150,
        status="success",
    )

    summary = component_metrics.get_summary()
    assert "PIXIV" in summary["components"]
    assert summary["components"]["PIXIV"]["call_count"] >= 1
    assert summary["components"]["PIXIV"]["avg_latency_ms"] == pytest.approx(150.0, 1.0)

    # 測試 log_inference
    log_inference(
        task_id="task-test-01",
        model="gemini-flash",
        prompt_length=200,
        completion_length=80,
        inference_latency_ms=320,
        status="success",
    )

    summary2 = component_metrics.get_summary()
    assert "BOT_BRAIN" in summary2["components"]
    assert summary2["components"]["BOT_BRAIN"]["call_count"] >= 1
    assert summary2["components"]["BOT_BRAIN"]["prompt_tokens"] >= 200


@pytest.mark.anyio
async def test_web_ui_component_metrics_endpoints() -> None:
    """測試 WebUI 組件指標端點之權限阻擋與資料回傳。"""
    component_metrics.reset()
    component_metrics.record_execution("TOOL_TEST", latency_ms=45.0)

    client = app.test_client()

    # 1. 未授權 GET /api/system/components/metrics 應回傳 401
    resp_unauth = await client.get("/api/system/components/metrics")
    assert resp_unauth.status_code == 401

    # 2. 授權 GET /api/system/components/metrics 應回傳 200
    with patch("web_ui.server._is_admin_request", return_value=True):
        resp_auth = await client.get("/api/system/components/metrics")
        assert resp_auth.status_code == 200
        data = await resp_auth.get_json()
        assert "components" in data
        assert "TOOL_TEST" in data["components"]

    # 3. 未授權 POST /api/system/components/metrics/reset 應回傳 401
    resp_reset_unauth = await client.post("/api/system/components/metrics/reset")
    assert resp_reset_unauth.status_code == 401

    # 4. 授權 POST /api/system/components/metrics/reset 應回傳 200 且清空指標
    with patch("web_ui.server._is_admin_request", return_value=True):
        resp_reset_auth = await client.post("/api/system/components/metrics/reset")
        assert resp_reset_auth.status_code == 200
        reset_data = await resp_reset_auth.get_json()
        assert reset_data["status"] == "success"

        # 再次檢查總呼叫數應已清空為 0
        resp_check = await client.get("/api/system/components/metrics")
        check_data = await resp_check.get_json()
        assert check_data["totals"]["total_calls"] == 0


@pytest.mark.anyio
async def test_web_ui_incidents_endpoint() -> None:
    """測試 WebUI 異常記錄端點之權限阻擋與查詢回傳。"""
    client = app.test_client()

    # 1. 未授權 GET /api/system/incidents 應回傳 401
    resp_unauth = await client.get("/api/system/incidents")
    assert resp_unauth.status_code == 401

    # 2. 授權 GET /api/system/incidents 應回傳 200
    with patch("web_ui.server._is_admin_request", return_value=True):
        resp_auth = await client.get("/api/system/incidents")
        assert resp_auth.status_code == 200
        data = await resp_auth.get_json()
        assert "incidents" in data
        assert isinstance(data["incidents"], list)


def test_apm_percentiles_extremes_and_kpis() -> None:
    """測試 APM 分位數 (P50/P90/P95/P99)、極值、Sparkline 走勢與全域 KPI。"""
    collector = ComponentMetricsCollector()
    collector.reset()

    # 模擬 20 次調用
    for i in range(1, 21):
        collector.record_execution(
            "SEARCH",
            latency_ms=i * 10,
            prompt_tokens=50,
            completion_tokens=20,
            success=(i != 20),
            error_msg="SearchError" if i == 20 else "",
            trace_id=f"tr-search-{i:02d}",
        )

    summary = collector.get_summary()
    assert "kpi" in summary
    kpi = summary["kpi"]
    assert kpi["total_calls"] == 20
    assert kpi["total_tokens"] == 20 * 70
    assert kpi["prompt_tokens"] == 20 * 50
    assert kpi["completion_tokens"] == 20 * 20
    assert kpi["health_score"] == pytest.approx(95.0, 0.1)  # 19/20 = 95%
    assert kpi["active_components_count"] >= 1

    c = summary["components"]["SEARCH"]
    assert c["min_latency_ms"] == 10
    assert c["max_latency_ms"] == 200
    assert c["p50_latency_ms"] == 110.0
    assert c["p95_latency_ms"] >= 180.0
    assert c["p99_latency_ms"] >= 190.0
    assert len(c["recent_latencies_trend"]) == 15  # 取近 15 筆
    assert len(c["recent_traces"]) == 20
    assert c["recent_traces"][-1]["trace_id"] == "tr-search-20"
    assert c["recent_traces"][-1]["error_msg"] == "SearchError"
    assert c["health_level"] == "DEGRADED"  # 95% is degraded


def test_bootstrap_from_db() -> None:
    """測試 ComponentMetricsCollector 從 chat_history.db 還原歷史執行度量。"""
    collector = ComponentMetricsCollector()
    collector.reset()

    # 執行歷史還原
    count = collector.bootstrap_from_db()
    # 只要資料庫存在，應能還原出歷史事件筆數
    assert count >= 0
    if count > 0:
        summary = collector.get_summary()
        assert summary["totals"]["total_calls"] > 0
        assert "BOT_BRAIN" in summary["components"]
        assert summary["components"]["BOT_BRAIN"]["call_count"] > 0


