"""core.sys_inspector
==================
雪莉微核心系統與硬體監控服務。
提供主機硬體 (CPU/RAM/Network/GPU)、雪莉主進程狀態採樣、TTL 防抖快取與高密度 LLM 友善格式化。
"""

from __future__ import annotations

from .audit_repository import AuditDBRepository
from .audit_service import (
    AuditService,
    CloudAnalysisClient,
    LogRepository,
    set_cloud_inference_client,
)
from .config import ensure_migrated_databases, get_audit_db_path, get_sys_monitor_db_path
from .formatter import format_compact_system_report
from .metrics import ComponentMetricsCollector, component_metrics
from .models import (
    DiagnosticResult,
    IncidentReport,
    IncidentSeverity,
    IncidentType,
    LogSnapshot,
    MicroSpanMetrics,
    QualityScoreResult,
    SelfHealingAction,
    SelfHealingStrategy,
)
from .notifications import notify_failure_sync, notify_slow_response_sync
from .sampler import get_system_metrics, get_system_metrics_async
from .service import SysInspectorService
from .span_tracker import SpanTracker

# 相容別名
SelfMonitoringService = AuditService

__all__ = [
    "get_system_metrics",
    "get_system_metrics_async",
    "format_compact_system_report",
    "SysInspectorService",
    "AuditDBRepository",
    "AuditService",
    "SelfMonitoringService",
    "CloudAnalysisClient",
    "LogRepository",
    "set_cloud_inference_client",
    "ComponentMetricsCollector",
    "component_metrics",
    "IncidentType",
    "IncidentSeverity",
    "IncidentReport",
    "LogSnapshot",
    "DiagnosticResult",
    "MicroSpanMetrics",
    "QualityScoreResult",
    "SelfHealingStrategy",
    "SelfHealingAction",
    "SpanTracker",
    "notify_slow_response_sync",
    "notify_failure_sync",
    "ensure_migrated_databases",
    "get_audit_db_path",
    "get_sys_monitor_db_path",
]
