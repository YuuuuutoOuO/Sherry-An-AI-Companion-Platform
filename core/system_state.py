"""
core/system_state.py
====================
系統運維狀態聚合與診斷標準化模組。

將底層離散的監控指標 (CPU、記憶體、日誌錯誤率、佇列堆積、Discord 延遲)
標準化聚合為四級系統健康狀態 (HEALTHY / DEGRADED / WARNING / INCIDENT)，
並提供故障關聯分析與自癒診斷建議。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import logging

import math

logger = logging.getLogger(__name__)


class SystemHealthLevel(str, Enum):
    """系統健康狀態等級。"""

    HEALTHY = "HEALTHY"  # 系統運行良好，各項指標處於基準範圍內
    DEGRADED = "DEGRADED"  # 效能略微下降 (例如延遲增加或隊列微量堆積)，仍可正常服務
    WARNING = "WARNING"  # 資源高負載或錯誤率上升，需限制高消耗自主任務
    INCIDENT = "INCIDENT"  # 系統發生重大故障或超出安全臨界，需觸發熔斷與降級保護


@dataclass(frozen=True)
class SystemMetrics:
    """系統運維指標快照。"""

    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    error_count_1m: int = 0
    queue_backlog: int = 0
    discord_latency_ms: float = 0.0
    disk_usage_percent: float = 0.0


class SystemStateNormalizer:
    """系統狀態聚合與診斷器。"""

    def __init__(
        self,
        cpu_warning_threshold: float = 85.0,
        cpu_incident_threshold: float = 95.0,
        memory_warning_threshold: float = 85.0,
        memory_incident_threshold: float = 95.0,
        error_warning_threshold: int = 5,
        error_incident_threshold: int = 15,
        queue_warning_threshold: int = 20,
        queue_incident_threshold: int = 50,
        latency_warning_ms: float = 800.0,
        latency_incident_ms: float = 2000.0,
    ) -> None:
        """初始化診斷門檻設定。"""
        self.cpu_warning_threshold = cpu_warning_threshold
        self.cpu_incident_threshold = cpu_incident_threshold
        self.memory_warning_threshold = memory_warning_threshold
        self.memory_incident_threshold = memory_incident_threshold
        self.error_warning_threshold = error_warning_threshold
        self.error_incident_threshold = error_incident_threshold
        self.queue_warning_threshold = queue_warning_threshold
        self.queue_incident_threshold = queue_incident_threshold
        self.latency_warning_ms = latency_warning_ms
        self.latency_incident_ms = latency_incident_ms

    def evaluate_health(self, metrics: SystemMetrics) -> SystemHealthLevel:
        """根據輸入指標評估當前全域健康等級。

        Args:
            metrics: 系統當前指標。

        Returns:
            SystemHealthLevel: 聚合後之健康等級。
        """
        # 0. 感測器異常 / NaN 防禦
        all_floats = [metrics.cpu_percent, metrics.memory_percent, metrics.discord_latency_ms, metrics.disk_usage_percent]
        if any(math.isnan(v) or math.isinf(v) for v in all_floats):
            logger.error("[SystemState] 偵測到異常指標數值 (NaN/Inf)，強制標記為 INCIDENT")
            return SystemHealthLevel.INCIDENT

        # 1. 檢查是否達到重大事件 (INCIDENT)
        if (
            metrics.cpu_percent >= self.cpu_incident_threshold
            or metrics.memory_percent >= self.memory_incident_threshold
            or metrics.error_count_1m >= self.error_incident_threshold
            or metrics.queue_backlog >= self.queue_incident_threshold
            or metrics.discord_latency_ms >= self.latency_incident_ms
            or metrics.disk_usage_percent >= 95.0
        ):
            return SystemHealthLevel.INCIDENT

        # 2. 檢查是否達到警告 (WARNING)
        if (
            metrics.cpu_percent >= self.cpu_warning_threshold
            or metrics.memory_percent >= self.memory_warning_threshold
            or metrics.error_count_1m >= self.error_warning_threshold
            or metrics.queue_backlog >= self.queue_warning_threshold
            or metrics.discord_latency_ms >= self.latency_warning_ms
            or metrics.disk_usage_percent >= 85.0
        ):
            return SystemHealthLevel.WARNING

        # 3. 檢查是否達到降級 (DEGRADED)
        if (
            metrics.cpu_percent >= (self.cpu_warning_threshold * 0.8)
            or metrics.memory_percent >= (self.memory_warning_threshold * 0.8)
            or metrics.error_count_1m >= 2
            or metrics.queue_backlog >= 5
            or metrics.discord_latency_ms >= 400.0
            or metrics.disk_usage_percent >= 70.0
        ):
            return SystemHealthLevel.DEGRADED

        return SystemHealthLevel.HEALTHY

    def diagnose_issues(self, metrics: SystemMetrics) -> list[str]:
        """產出系統問題之具體診斷清單與修復建議。

        Args:
            metrics: 系統當前指標。

        Returns:
            問題診斷字串清單。
        """
        issues: list[str] = []

        if metrics.cpu_percent >= self.cpu_warning_threshold:
            issues.append(f"CPU 使用率過高 ({metrics.cpu_percent:.1f}%)，建議暫緩非關鍵背景工作")
        if metrics.memory_percent >= self.memory_warning_threshold:
            issues.append(f"記憶體使用率過高 ({metrics.memory_percent:.1f}%)，建議執行垃圾回收或釋放快取")
        if metrics.error_count_1m >= self.error_warning_threshold:
            issues.append(f"近 1 分鐘錯誤數激增 ({metrics.error_count_1m} 次)，請檢視 error_log.jsonl")
        if metrics.queue_backlog >= self.queue_warning_threshold:
            issues.append(f"任務佇列積壓嚴重 ({metrics.queue_backlog} 件)，推理工作線程可能阻塞")
        if metrics.discord_latency_ms >= self.latency_warning_ms:
            issues.append(f"Discord 網路延遲異常 ({metrics.discord_latency_ms:.1f}ms)，可能遭遇網路抖動或 Gateway 限速")
        if metrics.disk_usage_percent >= 90.0:
            issues.append(f"磁碟空間告急 ({metrics.disk_usage_percent:.1f}%)，需立即啟動日誌保留清理")

        return issues
