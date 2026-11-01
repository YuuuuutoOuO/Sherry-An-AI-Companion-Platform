"""tests/unit/test_self_monitoring.py
====================================
自我監控模組 (mod/self_monitoring) 單元測試套件。

測試範圍：
1. MOD_INFO 模組元資料規範
2. LogRepository 5 分鐘時間窗口過濾與日誌聚合
3. CloudAnalysisClient 雲端搜尋模型 Prompt 組裝、解析與降級兜底
4. AuditDBRepository SQLite 資料庫 CRUD、分頁與 KPI 統計
5. SelfMonitoringService 慢速 (90s)、失敗 (120s) 與阻塞檢測及 DB 自動持久化
6. build_diagnostic_embed Discord 卡片格式與字數防禦
7. SelfMonitoringCog 生命週期、後台巡邏與 /self-monitoring Slash 指令
8. Quart Web Blueprint (/view, /stats, /records, /diagnose) API 端點測試
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from discord.ext import commands

from core.sys_inspector import (
    AuditDBRepository,
    AuditService,
    DiagnosticResult,
    IncidentReport,
    IncidentSeverity,
    IncidentType,
    LogRepository,
    LogSnapshot,
    MicroSpanMetrics,
    QualityScoreResult,
    SelfHealingAction,
    SelfHealingStrategy,
    SelfMonitoringService,
    SpanTracker,
    CloudAnalysisClient,
    get_audit_db_path,
    notify_failure_sync,
    notify_slow_response_sync,
)
from core.sys_inspector.audit_service import SelfHealingAdvisor, audit_service
from handlers.sys_inspector import (
    SelfMonitoringCog,
    SysInspectorCog,
    build_diagnostic_embed,
    get_self_monitoring_service,
)
from web_ui.server import app

_TZ_TW = timezone(timedelta(hours=8))


# ---------------------------------------------------------------------------
# 0. 核心匯出與服務單例驗證
# ---------------------------------------------------------------------------

def test_self_monitoring_core_exports() -> None:
    """驗證 core.sys_inspector 具備完整的系統監控與延遲稽核能力。"""
    assert AuditDBRepository is not None
    assert AuditService is not None
    assert audit_service is not None
    assert SelfMonitoringService is AuditService
    assert SysInspectorCog is not None
    assert SelfMonitoringCog is SysInspectorCog
    assert get_audit_db_path().name == "audit.db"
    assert MicroSpanMetrics is not None
    assert QualityScoreResult is not None
    assert SelfHealingAction is not None
    assert SelfHealingStrategy is not None
    assert SpanTracker is not None
    assert SelfHealingAdvisor is not None


# ---------------------------------------------------------------------------
# 1. LogRepository 測試
# ---------------------------------------------------------------------------

def test_log_repository_filters_5_minute_window(tmp_path: Path) -> None:
    """測試 LogRepository 能精確過濾出 5 分鐘內的日誌，排除舊紀錄。"""
    async def _test() -> None:
        bot_log_file = tmp_path / "bot.log"
        runtime_file = tmp_path / "runtime_log.jsonl"
        error_file = tmp_path / "error_log.jsonl"

        now = datetime.now(_TZ_TW)
        time_now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        time_2m_ago_str = (now - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S")
        time_10m_ago_str = (now - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")

        # 寫入 bot.log
        bot_content = (
            f"{time_10m_ago_str} [INFO] [tr-old] bot: 這是10分鐘前的舊日誌\n"
            f"{time_2m_ago_str} [WARNING] [tr-recent] bot: 這是2分鐘前之警告日誌\n"
            f"{time_now_str} [ERROR] [tr-now] bot: 這是當前的錯誤日誌\n"
            "  File 'router.py', line 123, in route_and_execute_tag\n"
        )
        bot_log_file.write_text(bot_content, encoding="utf-8")

        # 寫入 runtime_log.jsonl
        runtime_content = (
            f'{{"ts": "{time_10m_ago_str}", "trace_id": "tr-old", "event": "chat_reply", "latency_ms": 1200, "user": "alice"}}\n'
            f'{{"ts": "{time_2m_ago_str}", "trace_id": "tr-recent", "event": "chat_reply", "latency_ms": 95000, "user": "bob"}}\n'
        )
        runtime_file.write_text(runtime_content, encoding="utf-8")

        # 寫入 error_log.jsonl
        error_content = (
            f'{{"ts": "{time_now_str}", "trace_id": "tr-now", "event": "error", "error_type": "TimeoutError", "message": "Inference timed out"}}\n'
        )
        error_file.write_text(error_content, encoding="utf-8")

        repo = LogRepository(
            bot_log_path=bot_log_file,
            runtime_log_path=runtime_file,
            error_log_path=error_file,
        )

        snapshot = await repo.get_recent_snapshot(window_minutes=5)

        assert snapshot.window_minutes == 5
        assert "這是10分鐘前的舊日誌" not in snapshot.bot_log_snippet
        assert "這是2分鐘前之警告日誌" in snapshot.bot_log_snippet
        assert "這是當前的錯誤日誌" in snapshot.bot_log_snippet
        assert snapshot.error_count >= 2  # 包含 bot.log 的 ERROR 與 error_log.jsonl
        assert "⚡SLOW" in snapshot.runtime_event_snippet
        assert "bob" in snapshot.runtime_event_snippet

    asyncio.run(_test())


def test_log_repository_handles_missing_files(tmp_path: Path) -> None:
    """測試檔案不存在時安全回傳空快照而不崩潰。"""
    async def _test() -> None:
        repo = LogRepository(
            bot_log_path=tmp_path / "not_exist_bot.log",
            runtime_log_path=tmp_path / "not_exist_runtime.jsonl",
            error_log_path=tmp_path / "not_exist_error.jsonl",
        )
        snapshot = await repo.get_recent_snapshot(window_minutes=5)
        assert snapshot.total_lines == 0
        assert snapshot.error_count == 0
        assert "(無主程式日誌輸出)" in snapshot.combined_text

    asyncio.run(_test())


# ---------------------------------------------------------------------------
# 2. CloudAnalysisClient 測試
# ---------------------------------------------------------------------------

def test_cloud_analysis_client_parsing() -> None:
    """測試 CloudAnalysisClient 對模型格式化回應之解析能力。"""
    async def _test() -> None:
        mock_cloud = MagicMock()
        mock_cloud._generate_async = AsyncMock(return_value="""【瓶頸分類】：GPU_LOCK
【根因剖析】：多模態圖像處理與主模型共用顯存鎖，造成推論佇列嚴重排隊延遲達 95 秒。
【關鍵日誌跡證】：
- 2026-09-08 15:40:00 [WARNING] [tr-1] 等候 GPU 鎖逾期
- 2026-09-08 15:41:00 [ERROR] [tr-2] TimeoutError
【具體修復建議】：
1. 重啟本地 LLM 引擎以清除 VRAM 碎屑
2. 調高並行推理批次容量至 2048""")

        client = CloudAnalysisClient(cloud_client=mock_cloud)

        incident = IncidentReport(
            incident_type=IncidentType.SLOW_RESPONSE_90S,
            severity=IncidentSeverity.WARNING,
            latency_ms=95000,
            trigger_reason="回覆超過 90 秒",
            trace_id="tr-test-1",
            channel_id=123456,
            user_name="Alice",
        )
        snapshot = LogSnapshot(
            window_minutes=5,
            total_lines=10,
            error_count=1,
            warning_count=1,
            bot_log_snippet="sample bot log",
            runtime_event_snippet="sample event log",
            combined_text="combined log content",
        )

        result = await client.analyze_incident(incident, snapshot)

        assert result.bottleneck_category == "GPU_LOCK"
        assert "推論佇列嚴重排隊延遲" in result.root_cause
        assert len(result.key_evidence) == 2
        assert len(result.recommendations) == 2
        assert "重啟本地 LLM 引擎" in result.recommendations[0]

    asyncio.run(_test())


def test_cloud_analysis_client_fallback_on_error() -> None:
    """測試當雲端模型異常時能自動切換至規則兜底。"""
    async def _test() -> None:
        mock_cloud = MagicMock()
        mock_cloud._generate_async = AsyncMock(side_effect=RuntimeError("Cloud API 503 Unavailable"))

        client = CloudAnalysisClient(cloud_client=mock_cloud)

        incident = IncidentReport(
            incident_type=IncidentType.FAILURE_120S,
            severity=IncidentSeverity.CRITICAL,
            latency_ms=125000,
            trigger_reason="回覆超時 120 秒",
        )
        snapshot = LogSnapshot(
            window_minutes=5,
            total_lines=5,
            error_count=2,
            warning_count=0,
            bot_log_snippet="error in line",
            runtime_event_snippet="",
            combined_text="combined error text",
        )

        result = await client.analyze_incident(incident, snapshot)

        assert result.bottleneck_category == "EXTERNAL_API_TIMEOUT"
        assert "雲端分析模型調用中斷" in result.root_cause
        assert len(result.recommendations) > 0

    asyncio.run(_test())


def test_content_filter_incident_is_classified_separately() -> None:
    """內容過濾放棄不應被誤分類為 EXTERNAL_API_TIMEOUT。"""
    async def _test() -> None:
        mock_cloud = MagicMock()
        mock_cloud._generate_async = AsyncMock(side_effect=RuntimeError("Cloud API unavailable"))
        client = CloudAnalysisClient(cloud_client=mock_cloud)

        incident = IncidentReport(
            incident_type=IncidentType.INFERENCE_FAILURE,
            severity=IncidentSeverity.CRITICAL,
            latency_ms=750,
            trigger_reason=(
                "InferenceGiveUp: Task memory-sync-1 在 1 次嘗試後徹底失敗放棄: "
                "content_filtered: 無替代模型"
            ),
        )
        snapshot = LogSnapshot(
            window_minutes=5,
            total_lines=9,
            error_count=3,
            warning_count=3,
            bot_log_snippet="Gemini 回傳空白字串",
            runtime_event_snippet="",
            combined_text="finish_reason=content_filter: PROHIBITED_CONTENT",
        )

        result = await client.analyze_incident(incident, snapshot)

        assert result.bottleneck_category == "CONTENT_FILTER"
        assert "EXTERNAL_API_TIMEOUT" != result.bottleneck_category
        assert "content_filter" in result.root_cause

    asyncio.run(_test())


def test_non_filter_inference_failure_still_external_timeout() -> None:
    """非過濾類的推論失敗維持原本的 EXTERNAL_API_TIMEOUT 分類。"""
    async def _test() -> None:
        mock_cloud = MagicMock()
        mock_cloud._generate_async = AsyncMock(side_effect=RuntimeError("Cloud API unavailable"))
        client = CloudAnalysisClient(cloud_client=mock_cloud)

        incident = IncidentReport(
            incident_type=IncidentType.INFERENCE_FAILURE,
            severity=IncidentSeverity.CRITICAL,
            latency_ms=750,
            trigger_reason="InferenceGiveUp: Task x 徹底失敗放棄: TimeoutError",
        )
        snapshot = LogSnapshot(
            window_minutes=5, total_lines=5, error_count=2, warning_count=0,
            bot_log_snippet="", runtime_event_snippet="", combined_text="",
        )

        result = await client.analyze_incident(incident, snapshot)

        assert result.bottleneck_category == "EXTERNAL_API_TIMEOUT"

    asyncio.run(_test())


def test_content_filter_recommends_no_self_healing() -> None:
    """內容過濾不應觸發降級模型等自動處置（重試與同廠備援皆無效）。"""
    async def _test() -> None:
        mock_cloud = MagicMock()
        mock_cloud._generate_async = AsyncMock(side_effect=RuntimeError("Cloud API unavailable"))
        client = CloudAnalysisClient(cloud_client=mock_cloud)

        incident = IncidentReport(
            incident_type=IncidentType.INFERENCE_FAILURE,
            severity=IncidentSeverity.CRITICAL,
            latency_ms=750,
            trigger_reason="InferenceGiveUp: content_filtered: 無替代模型",
        )
        snapshot = LogSnapshot(
            window_minutes=5, total_lines=5, error_count=2, warning_count=0,
            bot_log_snippet="", runtime_event_snippet="", combined_text="",
        )

        result = await client.analyze_incident(incident, snapshot)
        action = SelfHealingAdvisor.evaluate_action(incident, result)

        assert result.bottleneck_category == "CONTENT_FILTER"
        assert action.strategy == SelfHealingStrategy.NONE

    asyncio.run(_test())


def test_privacy_and_no_chat_leakage(tmp_path: Path) -> None:
    """測試日誌快照與 Prompt 建構嚴格脫敏，絕不洩漏對話原文與 reply_preview。"""
    async def _test() -> None:
        bot_log_file = tmp_path / "bot.log"
        runtime_file = tmp_path / "runtime_log.jsonl"
        error_file = tmp_path / "error_log.jsonl"

        now = datetime.now(_TZ_TW)
        time_now_str = now.strftime("%Y-%m-%d %H:%M:%S")

        bot_log_file.write_text(f"{time_now_str} [INFO] bot: 系統初始化正常\n", encoding="utf-8")
        error_file.write_text("", encoding="utf-8")

        # 寫入含有使用者具體問話與私密 reply_preview 的 runtime 事件
        runtime_content = (
            f'{{"ts": "{time_now_str}", "trace_id": "tr-secret-999", "event": "chat_reply", '
            f'"latency_ms": 75000, "user": "TraderUser", "intent": "stock_query", '
            f'"raw_input": "請問台積電明天會漲到多少？請幫我分析2330與輝達", '
            f'"reply_preview": "台積電（2330）目前技術面看好...", "tools_invoked": ["stock_query"]}}\n'
        )
        runtime_file.write_text(runtime_content, encoding="utf-8")

        repo = LogRepository(
            bot_log_path=bot_log_file,
            runtime_log_path=runtime_file,
            error_log_path=error_file,
        )

        snapshot = await repo.get_recent_snapshot(window_minutes=5)

        # 1. 斷言日誌快照不包含具體聊天文字或 reply_preview
        assert "請問台積電明天會漲到多少" not in snapshot.runtime_event_snippet
        assert "台積電（2330）目前技術面看好" not in snapshot.runtime_event_snippet
        assert "preview=" not in snapshot.runtime_event_snippet
        assert "[Trace:tr-secret-999]" in snapshot.runtime_event_snippet
        assert "intent=stock_query" in snapshot.runtime_event_snippet

        # 2. 斷言發給雲端模型的 Prompt 已去識別化，不直接嵌入對話原文
        incident = IncidentReport(
            incident_type=IncidentType.SLOW_RESPONSE_90S,
            severity=IncidentSeverity.WARNING,
            latency_ms=75000,
            trigger_reason="單次回覆耗時過長",
            trace_id="tr-secret-999",
            user_name="TraderUser",
            raw_prompt="請問台積電明天會漲到多少？請幫我分析2330與輝達",
        )

        client = CloudAnalysisClient()
        prompt = client._build_prompt(incident, snapshot)

        assert "請問台積電明天會漲到多少" not in prompt
        assert "請求長度" in prompt
        assert "已自動脫敏遮蔽" in prompt
        assert "切記禁止輸出任何使用者聊天原文" in prompt

    asyncio.run(_test())


def test_multi_request_stock_congestion_diagnosis() -> None:
    """測試前一人過多股票請求導致阻塞時，診斷報告使用去識別化與標號表述。"""
    async def _test() -> None:
        mock_cloud = MagicMock()
        mock_cloud._generate_async = AsyncMock(return_value="""【瓶頸分類】：QUEUE_CONGESTION
【根因剖析】：前一位使用者短時間內送出多筆股票數據查詢請求，其中請求 1 (標號/Trace ID: `tr-stock-01`) 執行耗時達 68.2 秒，佔用推論資源，導致後續任務排隊阻塞。
【關鍵日誌跡證】：
- [RT_EVENT] [Trace:tr-stock-01] ⚡SLOW chat_reply user=TraderUser lat=68200ms intent=stock_query tools=stock_query qsize=4
- [RT_EVENT] [Trace:tr-stock-02] chat_reply user=UserB lat=45000ms qsize=3
【具體修復建議】：
1. 針對單一使用者之高頻股票查詢啟用動態限流 (Rate Limiting)
2. 將股票與外部工具 API 調用改為非同步背景非阻塞執行""")

        client = CloudAnalysisClient(cloud_client=mock_cloud)

        incident = IncidentReport(
            incident_type=IncidentType.SLOW_RESPONSE_90S,
            severity=IncidentSeverity.WARNING,
            latency_ms=68200,
            trigger_reason="佇列排隊導致回覆延遲",
            trace_id="tr-stock-01",
        )
        snapshot = LogSnapshot(
            window_minutes=5,
            total_lines=15,
            error_count=0,
            warning_count=1,
            bot_log_snippet="",
            runtime_event_snippet="[RT_EVENT] [Trace:tr-stock-01] ⚡SLOW chat_reply lat=68200ms intent=stock_query",
            combined_text="",
        )

        result = await client.analyze_incident(incident, snapshot)

        assert result.bottleneck_category == "QUEUE_CONGESTION"
        assert "前一位使用者短時間內送出多筆股票數據查詢請求" in result.root_cause
        assert "請求 1" in result.root_cause
        assert "tr-stock-01" in result.root_cause
        assert len(result.key_evidence) == 2
        assert len(result.recommendations) == 2

    asyncio.run(_test())


# ---------------------------------------------------------------------------
# 3. AuditDBRepository 測試
# ---------------------------------------------------------------------------

def test_audit_db_repository_crud(tmp_path: Path) -> None:
    """測試 SQLite 稽核資料庫寫入、多條件查詢與 KPI 統計計算。"""
    db_file = tmp_path / "self-monitoring" / "audit.db"
    repo = AuditDBRepository(db_path=db_file)

    inc1 = IncidentReport(
        incident_type=IncidentType.SLOW_RESPONSE_90S,
        severity=IncidentSeverity.WARNING,
        latency_ms=92000,
        trigger_reason="回覆 92 秒",
        trace_id="tr-db-1",
        user_name="UserA",
    )
    diag1 = DiagnosticResult(
        root_cause="GPU 鎖排隊",
        bottleneck_category="GPU_LOCK",
        key_evidence=["log line 1"],
        recommendations=["重啟 LLM"],
        raw_model_response="raw",
        model_name="deepseek:deepseek-v4-flash",
        diagnosis_latency_ms=120,
    )
    snap1 = LogSnapshot(5, 20, 1, 2, "bot log", "rt log", "combined log")

    inc2 = IncidentReport(
        incident_type=IncidentType.FAILURE_120S,
        severity=IncidentSeverity.CRITICAL,
        latency_ms=125000,
        trigger_reason="超時 125 秒失敗",
        trace_id="tr-db-2",
        user_name="UserB",
    )
    diag2 = DiagnosticResult(
        root_cause="外部 API 逾時",
        bottleneck_category="EXTERNAL_API_TIMEOUT",
        key_evidence=["log line 2"],
        recommendations=["檢查網路連線"],
        raw_model_response="raw 2",
        model_name="deepseek:deepseek-v4-flash",
        diagnosis_latency_ms=150,
    )

    # 1. 寫入紀錄
    id1 = repo.insert_audit_record(inc1, diag1, snap1)
    id2 = repo.insert_audit_record(inc2, diag2, snap1)

    assert id1 > 0
    assert id2 > id1

    # 2. 依條件查詢
    all_records = repo.get_audit_records(limit=10)
    assert len(all_records) == 2
    assert all_records[0]["id"] == id2  # 時間降序，最新在前

    crit_records = repo.get_audit_records(severity="CRITICAL")
    assert len(crit_records) == 1
    assert crit_records[0]["trace_id"] == "tr-db-2"

    # 3. 查詢單筆詳情
    detail = repo.get_audit_record_by_id(id1)
    assert detail is not None
    assert detail["user_name"] == "UserA"
    assert detail["bottleneck_category"] == "GPU_LOCK"
    assert detail["recommendations"] == ["重啟 LLM"]
    assert detail["key_evidence"] == ["log line 1"]

    # 4. 統計 KPI
    stats = repo.get_stats_summary()
    assert stats["total_audits"] == 2
    assert stats["warning_count"] == 1
    assert stats["critical_count"] == 1
    assert stats["avg_latency_ms"] == int((92000 + 125000) / 2)


# ---------------------------------------------------------------------------
# 4. SelfMonitoringService 測試
# ---------------------------------------------------------------------------

def test_self_monitoring_service_triggers_and_cooldown(tmp_path: Path) -> None:
    """測試 Service 對 90s 慢速、120s 失敗、阻塞檢查、防抖與 DB 自動落盤。"""
    async def _test() -> None:
        db_file = tmp_path / "self-monitoring" / "audit.db"
        db_repo = AuditDBRepository(db_path=db_file)

        mock_repo = MagicMock(spec=LogRepository)
        mock_repo.get_recent_snapshot = AsyncMock(
            return_value=LogSnapshot(5, 10, 0, 0, "", "", "log")
        )
        mock_client = MagicMock(spec=CloudAnalysisClient)
        mock_client.analyze_incident = AsyncMock(
            return_value=DiagnosticResult(
                root_cause="測試根因",
                bottleneck_category="TEST",
                key_evidence=["證據1"],
                recommendations=["建議1"],
                raw_model_response="raw",
                model_name="test-model",
                diagnosis_latency_ms=50,
            )
        )

        service = SelfMonitoringService(
            log_repository=mock_repo,
            analysis_client=mock_client,
            db_repository=db_repo,
        )

        # 1. 測試慢速事件建立
        slow_inc = service.create_slow_response_incident(
            latency_ms=92000, trace_id="tr-slow", channel_id=111, user_name="Bob"
        )
        assert slow_inc.incident_type == IncidentType.SLOW_RESPONSE_90S
        assert slow_inc.severity == IncidentSeverity.WARNING

        # 2. 測試失敗事件建立
        fail_inc = service.create_failure_incident(
            latency_ms=122000, error_msg="Timeout", trace_id="tr-fail"
        )
        assert fail_inc.incident_type == IncidentType.FAILURE_120S
        assert fail_inc.severity == IncidentSeverity.CRITICAL

        # 3. 測試阻塞健康檢查
        healthy = service.check_system_health(queue_size=1, oldest_task_age_sec=10.0, event_loop_lag_sec=0.1)
        assert healthy is None

        blocked_lag = service.check_system_health(queue_size=0, oldest_task_age_sec=0.0, event_loop_lag_sec=4.5)
        assert blocked_lag is not None
        assert blocked_lag.incident_type == IncidentType.SYSTEM_BLOCKING

        blocked_queue = service.check_system_health(queue_size=3, oldest_task_age_sec=70.0, event_loop_lag_sec=0.1)
        assert blocked_queue is not None
        assert "任務佇列阻塞" in blocked_queue.trigger_reason

        # 4. 測試診斷流程並驗證自動寫入 SQLite
        assert not service.should_suppress_alert(IncidentType.SLOW_RESPONSE_90S)
        diag, snap = await service.diagnose_incident(slow_inc)
        assert diag.root_cause == "測試根因"
        assert service.should_suppress_alert(IncidentType.SLOW_RESPONSE_90S)

        # 檢查資料庫是否有一筆紀錄
        db_records = service.get_audit_records()
        assert len(db_records) == 1
        assert db_records[0]["trace_id"] == "tr-slow"

    asyncio.run(_test())


# ---------------------------------------------------------------------------
# 5. build_diagnostic_embed 測試
# ---------------------------------------------------------------------------

def test_build_diagnostic_embed() -> None:
    """測試卡片建構器能產出符合 Discord 規範的美觀 Embed。"""
    incident = IncidentReport(
        incident_type=IncidentType.SLOW_RESPONSE_90S,
        severity=IncidentSeverity.WARNING,
        latency_ms=95000,
        trigger_reason="單次回覆耗時達 95.0 秒",
        trace_id="tr-card-001",
        channel_id=987654321,
    )
    diagnostic = DiagnosticResult(
        root_cause="GPU 算力被長文字 prefill 佔用。",
        bottleneck_category="PROMPT_OVERFLOW",
        key_evidence=["[WARNING] prompt token size exceeds 4000"],
        recommendations=["啟用對話歷史濃縮 (CONTEXT_COMPRESS)", "檢查 Prompt Caching 是否開啟"],
        raw_model_response="raw text",
        model_name="deepseek:deepseek-v4-flash",
        diagnosis_latency_ms=350,
    )
    snapshot = LogSnapshot(
        window_minutes=5,
        total_lines=42,
        error_count=2,
        warning_count=3,
        bot_log_snippet="snippet",
        runtime_event_snippet="event snippet",
        combined_text="combined",
    )

    embed = build_diagnostic_embed(incident, diagnostic, snapshot)

    assert isinstance(embed, discord.Embed)
    assert "⚠️ 【慢速警告】" in (embed.title or "")
    assert embed.color == discord.Color.gold()
    assert len(embed.fields) >= 4

    total_chars = len(embed.title or "") + len(embed.description or "")
    for f in embed.fields:
        total_chars += len(f.name or "") + len(f.value or "")
    assert total_chars < 4000


# ---------------------------------------------------------------------------
# 6. SelfMonitoringCog & Slash 指令測試
# ---------------------------------------------------------------------------

def test_self_monitoring_cog_and_slash_command() -> None:
    """測試 Cog 生命週期與 /self-monitoring action:check 指令交互。"""
    async def _test() -> None:
        bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())

        mock_service = MagicMock(spec=SelfMonitoringService)
        mock_service.diagnose_incident = AsyncMock(
            return_value=(
                DiagnosticResult(
                    root_cause="手動體檢完成，各項指標正常",
                    bottleneck_category="HEALTHY",
                    key_evidence=[],
                    recommendations=["系統運作良好"],
                    raw_model_response="ok",
                    model_name="test-cloud",
                    diagnosis_latency_ms=100,
                ),
                LogSnapshot(5, 10, 0, 0, "", "", "log"),
            )
        )
        mock_service.create_manual_check_incident = MagicMock(
            return_value=IncidentReport(
                incident_type=IncidentType.MANUAL_CHECK,
                severity=IncidentSeverity.INFO,
                latency_ms=0,
                trigger_reason="手動檢查",
            )
        )
        mock_service.create_slow_response_incident = MagicMock(
            return_value=IncidentReport(
                incident_type=IncidentType.SLOW_RESPONSE_90S,
                severity=IncidentSeverity.WARNING,
                latency_ms=95500,
                trigger_reason="模擬慢速",
            )
        )
        mock_service.get_recent_incident_history = MagicMock(return_value=[])

        cog = SelfMonitoringCog(bot, service=mock_service)
        await cog.cog_load()

        assert get_self_monitoring_service() is not None

        # 1. 測試 action: check (手動體檢)
        interaction_check = AsyncMock(spec=discord.Interaction)
        interaction_check.response.defer = AsyncMock()
        interaction_check.followup.send = AsyncMock()
        interaction_check.user.display_name = "AdminUser"
        interaction_check.channel_id = 12345

        await cog.self_monitoring_cmd.callback(cog, interaction_check, action="check")

        interaction_check.response.defer.assert_called_once()
        interaction_check.followup.send.assert_called_once()
        call_args = interaction_check.followup.send.call_args[1]
        assert "embed" in call_args
        assert "【體檢報告】" in call_args["embed"].title

        # 2. 測試 action: status
        interaction_status = AsyncMock(spec=discord.Interaction)
        interaction_status.response.defer = AsyncMock()
        interaction_status.followup.send = AsyncMock()

        await cog.self_monitoring_cmd.callback(cog, interaction_status, action="status")
        status_args = interaction_status.followup.send.call_args[1]
        assert "🛡️ 雪莉自我監控狀態" in status_args["embed"].title

        # 3. 測試 action: test_alert
        interaction_alert = AsyncMock(spec=discord.Interaction)
        interaction_alert.response.defer = AsyncMock()
        interaction_alert.followup.send = AsyncMock()
        interaction_alert.user.display_name = "AdminUser"
        interaction_alert.channel_id = 12345

        await cog.self_monitoring_cmd.callback(cog, interaction_alert, action="test_alert")
        alert_args = interaction_alert.followup.send.call_args[1]
        assert "⚠️ 【慢速警告】" in alert_args["embed"].title

        await cog.cog_unload()
        await bot.close()
        assert get_self_monitoring_service() is None

    asyncio.run(_test())


# ---------------------------------------------------------------------------
# 7. Web Blueprint REST API 測試
# ---------------------------------------------------------------------------

def test_web_blueprint_routes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """測試 Quart Web Blueprint 端點：/view, /api/self_monitoring/stats, /records, /diagnose。"""
    async def _test() -> None:
        db_file = tmp_path / "self-monitoring" / "audit.db"
        db_repo = AuditDBRepository(db_path=db_file)

        mock_client = MagicMock(spec=CloudAnalysisClient)
        mock_client.analyze_incident = AsyncMock(
            return_value=DiagnosticResult(
                root_cause="Web 端手動稽核完成",
                bottleneck_category="MANUAL_AUDIT",
                key_evidence=["evidence line"],
                recommendations=["維持定期檢視"],
                raw_model_response="raw",
                model_name="deepseek:deepseek-v4-flash",
                diagnosis_latency_ms=80,
            )
        )
        mock_log_repo = MagicMock(spec=LogRepository)
        mock_log_repo.get_recent_snapshot = AsyncMock(
            return_value=LogSnapshot(5, 15, 0, 1, "bot log", "rt log", "combined log")
        )

        service = SelfMonitoringService(
            log_repository=mock_log_repo,
            analysis_client=mock_client,
            db_repository=db_repo,
        )

        import core.sys_inspector.audit_service as audit_svc_mod
        monkeypatch.setattr(audit_svc_mod, "audit_service", service)

        client = app.test_client()

        # 1. 測試 GET /api/system/audit/kpi (R3：需管理員權限，未登入 401)
        resp_stats = await client.get("/api/system/audit/kpi")
        assert resp_stats.status_code == 401

        # 2. 測試未登入時查看詳細報告與手動診斷 (回傳 401)
        with patch("web_ui.server._is_admin_request", return_value=False):
            resp_diag_unauth = await client.post("/api/system/audit/diagnose")
            assert resp_diag_unauth.status_code == 401
            unauth_diag_json = await resp_diag_unauth.get_json()
            assert unauth_diag_json["status"] == "error"

            resp_detail_unauth = await client.get("/api/system/audit/records/1")
            assert resp_detail_unauth.status_code == 401
            unauth_detail_json = await resp_detail_unauth.get_json()
            assert unauth_detail_json["status"] == "error"
            assert "需要管理員權限" in unauth_detail_json["message"]

        # 3. 測試管理員登入後 (回傳 200 SUCCESS)
        with patch("web_ui.server._is_admin_request", return_value=True):
            # 3a. 測試 POST /api/system/audit/diagnose
            resp_diag = await client.post("/api/system/audit/diagnose")
            assert resp_diag.status_code == 200
            diag_json = await resp_diag.get_json()
            assert diag_json["status"] == "success"
            assert diag_json["diagnosis"]["bottleneck_category"] == "MANUAL_AUDIT"

            # 3b. 測試 GET /api/system/audit/records (清單摘要)
            resp_rec = await client.get("/api/system/audit/records")
            assert resp_rec.status_code == 200
            rec_json = await resp_rec.get_json()
            assert rec_json["status"] == "success"
            assert rec_json["count"] >= 1
            rec_id = rec_json["records"][0]["id"]

            # 3c. 測試 GET /api/system/audit/records/<id> (登入後解鎖詳細報告)
            resp_detail = await client.get(f"/api/system/audit/records/{rec_id}")
            assert resp_detail.status_code == 200
            detail_json = await resp_detail.get_json()
            assert detail_json["record"]["bottleneck_category"] == "MANUAL_AUDIT"
            assert "維持定期檢視" in detail_json["record"]["recommendations"]

            # 3d. 測試 GET /api/system/audit/kpi (R3：登入後解鎖 KPI 摘要)
            resp_stats_admin = await client.get("/api/system/audit/kpi")
            assert resp_stats_admin.status_code == 200
            stats_admin_json = await resp_stats_admin.get_json()
            assert stats_admin_json["status"] == "success"
            assert "total_audits" in stats_admin_json["kpi"]

    asyncio.run(_test())


def test_notify_sync_helpers() -> None:
    """測試全域 notify_slow_response_sync 與 notify_failure_sync 鉤子。"""
    async def dummy_coro(*args: Any, **kwargs: Any) -> None:
        pass

    mock_cog = MagicMock()
    mock_cog.handle_slow_response = MagicMock(side_effect=dummy_coro)
    mock_cog.handle_failure = MagicMock(side_effect=dummy_coro)

    with patch("core.sys_inspector.notifications._GLOBAL_COG", mock_cog):
        # 1. 低於門檻 (例如 10000ms < 90000ms) 不應觸發
        notify_slow_response_sync(10000, trace_id="tr-fast")
        mock_cog.handle_slow_response.assert_not_called()

        # 2. 超過 90 秒門檻應觸發 handle_slow_response
        notify_slow_response_sync(95000, trace_id="tr-test", channel_id=12345)
        mock_cog.handle_slow_response.assert_called_once()
        assert mock_cog.handle_slow_response.call_args.kwargs["latency_ms"] == 95000
        assert mock_cog.handle_slow_response.call_args.kwargs["trace_id"] == "tr-test"

        # 3. 失敗事件通知應觸發 handle_failure
        notify_failure_sync(125000, error_msg="Timeout", trace_id="tr-fail")
        mock_cog.handle_failure.assert_called_once()
        assert mock_cog.handle_failure.call_args.kwargs["error_msg"] == "Timeout"


def test_content_filter_failure_does_not_notify() -> None:
    """內容過濾屬預期行為，不應觸發嚴重異常通知（即使命中 give-up 關鍵字）。"""
    async def dummy_coro(*args: Any, **kwargs: Any) -> None:
        pass

    mock_cog = MagicMock()
    mock_cog.handle_failure = MagicMock(side_effect=dummy_coro)

    with patch("core.sys_inspector.notifications._GLOBAL_COG", mock_cog):
        notify_failure_sync(
            750,
            error_msg=(
                "InferenceGiveUp: Task memory-sync-1 在 1 次嘗試後徹底失敗放棄: "
                "content_filtered: 無替代模型"
            ),
            trace_id="tr-filtered",
        )

    mock_cog.handle_failure.assert_not_called()


def test_non_filter_giveup_still_notifies() -> None:
    """非過濾類的 give-up 仍應維持原本的嚴重異常通知。"""
    async def dummy_coro(*args: Any, **kwargs: Any) -> None:
        pass

    mock_cog = MagicMock()
    mock_cog.handle_failure = MagicMock(side_effect=dummy_coro)

    with patch("core.sys_inspector.notifications._GLOBAL_COG", mock_cog):
        notify_failure_sync(
            750,
            error_msg="InferenceGiveUp: Task x 徹底失敗放棄: TimeoutError",
            trace_id="tr-timeout",
        )

    mock_cog.handle_failure.assert_called_once()




def test_inference_failure_incident_creation() -> None:
    """測試 SelfMonitoringService 建立 INFERENCE_FAILURE 事故結構與原因文字。"""
    service = SelfMonitoringService(
        log_repository=MagicMock(spec=LogRepository),
        analysis_client=MagicMock(spec=CloudAnalysisClient),
        db_repository=MagicMock(spec=AuditDBRepository),
    )
    incident = service.create_inference_failure_incident(
        latency_ms=2500,
        error_msg="Cannot connect to host localhost:1919",
        trace_id="tr-infer-fail",
        channel_id=123,
        user_name="Yuuto",
        raw_prompt="query test",
        task_id="integrated-pass1-999",
    )
    assert incident.incident_type == IncidentType.INFERENCE_FAILURE
    assert incident.severity == IncidentSeverity.CRITICAL
    assert incident.latency_ms == 2500
    assert "integrated-pass1-999" in incident.trigger_reason
    assert "Cannot connect to host" in incident.trigger_reason


def test_notify_failure_sync_zero_latency_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    """測試當 is_fatal=True 或包含連線斷線關鍵字時，即使耗時遠小於 120 秒也能 0 秒即時派發。"""
    mock_service = MagicMock(spec=SelfMonitoringService)
    mock_service.should_suppress_alert.return_value = False
    mock_service.diagnose_incident = AsyncMock()

    import core.sys_inspector.notifications as notif_mod
    monkeypatch.setattr(notif_mod, "_GLOBAL_SERVICE", mock_service)
    monkeypatch.setattr(notif_mod, "_GLOBAL_COG", None)

    # 1. 測試 is_fatal=True，耗時僅 3 秒
    notify_failure_sync(
        latency_ms=3000,
        error_msg="ConnectionResetError: Connection reset by peer",
        trace_id="tr-fatal-1",
        is_fatal=True,
    )
    mock_service.create_inference_failure_incident.assert_called_once()

    # 2. 測試無 is_fatal 但錯誤訊息含 'give-up' 致命關鍵字
    mock_service.reset_mock()
    notify_failure_sync(
        latency_ms=4500,
        error_msg="BotBrain 徹查無獲(give-up)放棄 task: task-123",
        trace_id="tr-fatal-2",
    )
    mock_service.create_inference_failure_incident.assert_called_once()

    # 3. 測試非致命錯誤且小於 120 秒 -> 應被過濾不派發
    mock_service.reset_mock()
    notify_failure_sync(
        latency_ms=5000,
        error_msg="Minor warning",
        trace_id="tr-ignored",
    )
    mock_service.create_inference_failure_incident.assert_not_called()
    mock_service.create_failure_incident.assert_not_called()


def test_observability_log_error_fatal_dispatch() -> None:
    """測試 handlers.social.observability.log_error 在推論致命錯誤時主動呼叫 notify_failure_sync。"""
    from handlers.social.observability import log_error

    with patch("handlers.social.observability.notify_failure_sync") as mock_notify:
        log_error(
            event="inference_failure",
            task_id="task-test-giveup",
            trace_id="tr-obs-fatal",
            error_type="InferenceGiveUp",
            message="Task failed after 3 retries: Cannot connect to host localhost:1919",
            latency_ms=6700,
            is_fatal=True,
        )
        mock_notify.assert_called_once()
        args, kwargs = mock_notify.call_args
        assert kwargs["task_id"] == "task-test-giveup"
        assert kwargs["is_fatal"] is True
        assert "Cannot connect" in kwargs["error_msg"]


# ---------------------------------------------------------------------------
# 9. 現代化升級測試：獨立 Model 設定、微觀計時、品質審查與自癒顧問
# ---------------------------------------------------------------------------

def test_self_monitoring_model_configuration() -> None:
    """測試 settings 中獨立之 SELF_MONITORING_MODEL 配置存在且獨立於 CLOUD_SEARCH_MODEL。"""
    # Arrange & Act
    import settings
    model_name = getattr(settings, "SELF_MONITORING_MODEL", "")

    # Assert
    assert model_name != ""
    assert "gemini:" in model_name or "claude:" in model_name or "deepseek:" in model_name or "openai:" in model_name


def test_span_tracker_measures_metrics_correctly() -> None:
    """測試 SpanTracker 能夠正確追蹤微觀分段計時與輸出 MicroSpanMetrics。"""
    # Arrange
    tracker = SpanTracker()

    # Act
    with tracker.time_span("rag_fetch"):
        import time
        time.sleep(0.02)

    with tracker.time_span("prompt_build"):
        time.sleep(0.02)

    tracker.record_ttft(320)
    tracker.record_cache_hit(True)
    tracker.record_token_count(150)
    metrics = tracker.export_metrics()

    # Assert
    assert metrics.rag_fetch_ms >= 0
    assert metrics.prompt_build_ms >= 0
    assert metrics.ttft_ms == 320
    assert metrics.cache_hit is True
    assert metrics.token_count == 150
    assert "rag_fetch" in metrics.spans
    assert metrics.total_latency_ms >= 0
    as_dict = metrics.to_dict()
    assert as_dict["ttft_ms"] == 320
    assert as_dict["cache_hit"] is True


def test_detect_action_loop() -> None:
    """測試 AuditService 能精確辨識連續 3 次相同動作與 2 步週期循環死循環。"""
    # Arrange
    service = AuditService()

    # Act & Assert
    # 1. 正常非循環
    assert service.detect_action_loop(["search_web", "read_url", "generate_reply"]) is False
    assert service.detect_action_loop(["search_web", "search_web"]) is False

    # 2. 連續 3 次相同動作
    assert service.detect_action_loop(["action_a", "action_a", "action_a"]) is True

    # 3. 雙步驟週期循環 (A, B, A, B)
    assert service.detect_action_loop(["step1", "step2", "step1", "step2"]) is True


def test_ttft_warning_and_action_loop_incident_creation() -> None:
    """測試建立 SLOW_TTFT_WARNING 與 ACTION_LOOP 之 IncidentReport 格式。"""
    # Arrange
    service = AuditService()

    # Act - TTFT 預警
    ttft_inc = service.create_ttft_warning_incident(
        ttft_ms=5600,
        trace_id="tr-ttft-1",
        channel_id=12345,
        user_name="Tester",
        raw_prompt="你好雪莉",
    )

    # Assert - TTFT
    assert ttft_inc.incident_type == IncidentType.SLOW_TTFT_WARNING
    assert ttft_inc.severity == IncidentSeverity.WARNING
    assert ttft_inc.latency_ms == 5600
    assert "5.0 秒預警門檻" in ttft_inc.trigger_reason
    assert ttft_inc.extra_metrics["ttft_ms"] == 5600

    # Act - Action Loop 事故
    loop_inc = service.create_action_loop_incident(
        loop_actions=["read_file(foo)", "read_file(foo)", "read_file(foo)"],
        trace_id="tr-loop-1",
        channel_id=12345,
        user_name="Tester",
    )

    # Assert - Action Loop
    assert loop_inc.incident_type == IncidentType.ACTION_LOOP
    assert loop_inc.severity == IncidentSeverity.CRITICAL
    assert "死循環" in loop_inc.trigger_reason


def test_audit_quality_async_and_fallback() -> None:
    """測試 CloudAnalysisClient 品質審查之解析與離線降級。"""
    async def _test() -> None:
        # Arrange 1: 正常雲端回覆
        mock_cloud = AsyncMock()
        mock_cloud._generate_async.return_value = """【人設評分】：5
【忠實評分】：4
【存在幻覺】：否
【審判短評】：完全符合雪莉溫柔且略帶調皮的陪伴語氣。
【改進建議】：可保持現狀。"""

        client = CloudAnalysisClient(cloud_client=mock_cloud)

        # Act 1
        res = await client.audit_quality(user_msg="雪莉早安！", bot_response="早安呀！今天也要一起加油喔～")

        # Assert 1
        assert res.persona_score == 5
        assert res.faithfulness_score == 4
        assert res.is_hallucination is False
        assert "符合雪莉" in res.critique

        # Arrange 2: 雲端斷線 -> 本地啟發式降級
        broken_cloud = AsyncMock()
        broken_cloud._generate_async.side_effect = ConnectionError("API Offline")
        client_broken = CloudAnalysisClient(cloud_client=broken_cloud)

        # Act 2 (回覆過短)
        res_fallback = await client_broken.audit_quality(user_msg="哈囉", bot_response="好")

        # Assert 2
        assert res_fallback.persona_score == 2
        assert "長度過短" in res_fallback.critique

    asyncio.run(_test())


def test_self_healing_advisor_decisions() -> None:
    """測試 SelfHealingAdvisor 針對不同異常產出正確的自癒策略動作。"""
    # Arrange
    service = AuditService()

    # 1. 測試死循環 -> INJECT_REFLECTION
    loop_incident = IncidentReport(
        incident_type=IncidentType.ACTION_LOOP,
        severity=IncidentSeverity.CRITICAL,
    )
    diag_loop = DiagnosticResult(
        root_cause="連續重複工具調用",
        bottleneck_category="ACTION_LOOP",
        key_evidence=[],
        recommendations=[],
        raw_model_response="",
        model_name="test-model",
    )
    action_loop = service.evaluate_self_healing(loop_incident, diag_loop)
    assert action_loop.strategy == SelfHealingStrategy.INJECT_REFLECTION
    assert action_loop.parameters.get("force_reflection") is True

    # 2. 測試 PROMPT_OVERFLOW -> CONTEXT_COMPRESSION
    prompt_incident = IncidentReport(
        incident_type=IncidentType.SLOW_RESPONSE_90S,
        severity=IncidentSeverity.WARNING,
    )
    diag_prompt = DiagnosticResult(
        root_cause="Prefill 上下文過長",
        bottleneck_category="PROMPT_OVERFLOW",
        key_evidence=[],
        recommendations=[],
        raw_model_response="",
        model_name="test-model",
    )
    action_prompt = service.evaluate_self_healing(prompt_incident, diag_prompt)
    assert action_prompt.strategy == SelfHealingStrategy.CONTEXT_COMPRESSION
    assert action_prompt.parameters.get("gc_short_term") is True

    # 3. 測試 GPU_LOCK -> FALLBACK_MODEL
    gpu_incident = IncidentReport(
        incident_type=IncidentType.FAILURE_120S,
        severity=IncidentSeverity.CRITICAL,
    )
    diag_gpu = DiagnosticResult(
        root_cause="顯卡資源鎖定超時",
        bottleneck_category="GPU_LOCK",
        key_evidence=[],
        recommendations=[],
        raw_model_response="",
        model_name="test-model",
    )
    action_gpu = service.evaluate_self_healing(gpu_incident, diag_gpu)
    assert action_gpu.strategy == SelfHealingStrategy.FALLBACK_MODEL

