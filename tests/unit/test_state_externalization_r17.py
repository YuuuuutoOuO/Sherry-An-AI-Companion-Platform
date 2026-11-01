"""R17 狀態外置與 SPARK leader 租約的回歸測試。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

import settings
from core.guard import CoreGuard
from core.leader_lease import LeaderLease
from core.state_store import LoginLockout, RateLimitStore, SessionStore
from handlers.social.rate_limiter import RateLimiter


def test_session_store_survives_new_instance(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    first = SessionStore("admin_sessions", db)
    first["token-1"] = 123.0
    second = SessionStore("admin_sessions", db)
    assert second["token-1"] == 123.0
    assert len(second) == 1
    second.pop("token-1")
    assert "token-1" not in first


def test_rate_limit_store_is_shared_between_instances(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RATE_LIMIT_MAX_REQUESTS", 2)
    monkeypatch.setattr(settings, "RATE_LIMIT_WINDOW_SECONDS", 60.0)
    store = RateLimitStore("social_rate_limit", tmp_path / "state.db")
    first = RateLimiter(store)
    second = RateLimiter(store)

    assert first.is_allowed(42) is True
    assert second.is_allowed(42) is True
    assert first.is_allowed(42) is False


def test_core_guard_and_social_limiter_share_semantics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RATE_LIMIT_MAX_REQUESTS", 2)
    monkeypatch.setattr(settings, "RATE_LIMIT_WINDOW_SECONDS", 60.0)
    guard = CoreGuard()
    guard._user_call_history = RateLimitStore("core_guard_rate_test", tmp_path / "state.db")

    assert guard.check_rate_limit(7, max_calls=2, window_sec=60.0)[0] is True
    assert guard.check_rate_limit(7, max_calls=2, window_sec=60.0)[0] is True
    allowed, cooldown = guard.check_rate_limit(7, max_calls=2, window_sec=60.0)
    assert allowed is False
    assert cooldown > 0


def test_login_lockout_persists_and_enforces_budget(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    attempts = SessionStore("attempts", db)
    locked = SessionStore("locked", db)
    policy = LoginLockout(attempts, locked)

    for _ in range(4):
        count, locked_sec = policy.record_failure("203.0.113.9", max_attempts=5, lockout_sec=900)
        assert locked_sec == 0
    count, locked_sec = policy.record_failure("203.0.113.9", max_attempts=5, lockout_sec=900)
    assert count == 5
    assert locked_sec == 900

    # 另一個行程／實例讀同一 DB 仍看到鎖定
    policy2 = LoginLockout(SessionStore("attempts", db), SessionStore("locked", db))
    is_locked, remaining = policy2.is_locked("203.0.113.9")
    assert is_locked is True
    assert remaining > 0

    policy2.clear("203.0.113.9")
    assert policy2.is_locked("203.0.113.9") == (False, 0)


def test_leader_lease_single_owner_and_takeover(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    first = LeaderLease("spark_scheduler", "instance-a", db)
    second = LeaderLease("spark_scheduler", "instance-b", db)

    assert first.acquire(ttl_sec=60, now=1000.0) is True
    assert first.is_leader(now=1001.0) is True
    assert second.acquire(ttl_sec=60, now=1001.0) is False
    assert second.is_leader(now=1001.0) is False

    # 租約過期後可被接手
    assert second.acquire(ttl_sec=60, now=1100.0) is True
    assert first.is_leader(now=1100.0) is False
    assert second.renew(ttl_sec=60, now=1110.0) is True
    assert second.is_leader(now=1120.0) is True

    second.release()
    assert second.is_leader(now=1120.0) is False
    assert first.acquire(ttl_sec=60, now=1120.0) is True


def test_spark_cycle_skips_when_not_leader() -> None:
    from handlers.spark.cog import SparkCog

    cog = SparkCog.__new__(SparkCog)
    cog._lease_started = True
    cog._leader_lease = MagicMock(owner="other-instance")
    cog._leader_lease.is_leader.return_value = False
    cog.service = MagicMock()
    cog.service.list_active_triggers.return_value = [MagicMock()]

    import asyncio

    asyncio.run(cog._run_check_cycle())
    cog.service.check_trigger.assert_not_called()
