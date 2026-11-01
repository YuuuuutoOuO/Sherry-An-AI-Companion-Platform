"""
tests/unit/test_risk_engine.py
==============================
針對 core.risk_engine 模組之單元測試，驗證三級風險分類、自動執行許可與管理員審批流。
"""

from __future__ import annotations


from core.risk_engine import (
    ActionRiskLevel,
    ApprovalStatus,
    RiskPolicyEngine,
)


def test_risk_classification() -> None:
    # Arrange
    engine = RiskPolicyEngine()

    # Act & Assert
    assert engine.classify_risk("sys_inspect") == ActionRiskLevel.LOW
    assert engine.classify_risk("interject_speak") == ActionRiskLevel.MEDIUM
    assert engine.classify_risk("clear_memory") == ActionRiskLevel.HIGH
    # Unknown actions default to HIGH for defense-in-depth
    assert engine.classify_risk("unknown_arbitrary_action") == ActionRiskLevel.HIGH


def test_low_risk_auto_execution() -> None:
    # Arrange
    engine = RiskPolicyEngine()

    # Act
    req = engine.create_request(action_type="sys_inspect", description="常規狀態採集")

    # Assert
    assert req.risk_level == ActionRiskLevel.LOW
    assert req.status == ApprovalStatus.APPROVED
    assert engine.can_auto_execute(req) is True


def test_high_risk_approval_flow() -> None:
    # Arrange
    engine = RiskPolicyEngine()

    # Act - Create high risk request
    req = engine.create_request(action_type="clear_memory", description="清空用戶記憶資料")

    # Assert - Initial Pending
    assert req.risk_level == ActionRiskLevel.HIGH
    assert req.status == ApprovalStatus.PENDING
    assert engine.can_auto_execute(req) is False
    assert len(engine.get_pending_requests()) == 1

    # Act - Approve by admin
    approved = engine.approve_action(req.action_id, approver_id=123456, comment="經過用戶要求核准")

    # Assert - Approved
    assert approved is True
    assert req.status == ApprovalStatus.APPROVED
    assert req.approver_id == 123456
    assert len(engine.get_pending_requests()) == 0
