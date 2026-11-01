"""
tests/unit/test_system_state.py
===============================
針對 core.system_state 模組之單元測試，驗證指標聚合與問題診斷邏輯。
"""

from __future__ import annotations


from core.system_state import SystemHealthLevel, SystemMetrics, SystemStateNormalizer


def test_system_state_healthy() -> None:
    # Arrange
    normalizer = SystemStateNormalizer()
    metrics = SystemMetrics(
        cpu_percent=25.0,
        memory_percent=40.0,
        error_count_1m=0,
        queue_backlog=1,
        discord_latency_ms=120.0,
    )

    # Act
    health = normalizer.evaluate_health(metrics)
    diagnostics = normalizer.diagnose_issues(metrics)

    # Assert
    assert health == SystemHealthLevel.HEALTHY
    assert len(diagnostics) == 0


def test_system_state_warning_and_incident() -> None:
    # Arrange
    normalizer = SystemStateNormalizer()
    warning_metrics = SystemMetrics(
        cpu_percent=88.0,
        memory_percent=60.0,
        error_count_1m=6,
        queue_backlog=2,
    )
    incident_metrics = SystemMetrics(
        cpu_percent=96.0,
        memory_percent=98.0,
        error_count_1m=20,
    )

    # Act
    warning_level = normalizer.evaluate_health(warning_metrics)
    incident_level = normalizer.evaluate_health(incident_metrics)
    warning_diagnostics = normalizer.diagnose_issues(warning_metrics)

    # Assert
    assert warning_level == SystemHealthLevel.WARNING
    assert incident_level == SystemHealthLevel.INCIDENT
    assert any("CPU 使用率過高" in d for d in warning_diagnostics)
    assert any("錯誤數激增" in d for d in warning_diagnostics)
