"""
tests/unit/test_autonomy_guard.py
=================================
針對 core.autonomy_guard 模組之單元測試，驗證全域熔斷開關與時間窗預算消耗機制。
"""

from __future__ import annotations


from core.autonomy_guard import AutonomyGuard


def test_autonomy_guard_kill_switch() -> None:
    # Arrange
    guard = AutonomyGuard(max_actions_per_hour=5)

    # Act & Assert (Normal)
    allowed, msg = guard.check_and_consume_budget("test_action")
    assert allowed is True

    # Act (Trigger kill switch)
    guard.trigger_kill_switch(reason="異常高頻推播警報")

    # Assert (Blocked)
    assert guard.is_kill_switch_active is True
    blocked, reason = guard.check_and_consume_budget("test_action")
    assert blocked is False
    assert "全域自主熔斷已啟動" in reason

    # Act (Release)
    guard.release_kill_switch()
    assert guard.is_kill_switch_active is False
    allowed_again, _ = guard.check_and_consume_budget("test_action_2")
    assert allowed_again is True


def test_autonomy_guard_rate_limit_budget() -> None:
    # Arrange
    guard = AutonomyGuard(max_actions_per_hour=3, max_actions_per_day=5)

    # Act
    res1, _ = guard.check_and_consume_budget("action_1")
    res2, _ = guard.check_and_consume_budget("action_2")
    res3, _ = guard.check_and_consume_budget("action_3")
    res4, reason4 = guard.check_and_consume_budget("action_4")

    # Assert
    assert res1 is True
    assert res2 is True
    assert res3 is True
    assert res4 is False
    assert "已達每小時自主行動上限" in reason4
