"""R19 主動關懷引擎：同意閘門、額度、候選來源與稽核回歸測試。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

import handlers.care as care_package
from core.autonomy_guard import AutonomyGuard
from core.risk_engine import ActionRiskLevel, RiskPolicyEngine
from handlers.care import CareCandidate, CareEngine, CareRepository
from handlers.memory.nightly_cog import _remember_care_topics

NOW = 10_000_000.0


@pytest.fixture()
def repo(tmp_path: Path) -> CareRepository:
    return CareRepository(tmp_path / "care.db")


@pytest.fixture()
def guard() -> AutonomyGuard:
    return AutonomyGuard(max_actions_per_hour=50, max_actions_per_day=100)


@pytest.fixture()
def engine(repo: CareRepository, guard: AutonomyGuard) -> CareEngine:
    return CareEngine(
        repository=repo,
        guard=guard,
        weekly_limit=2,
        daily_limit=10,
        min_affinity=60.0,
        inactive_days=14.0,
    )


def _candidate(
    *,
    user_id: int = 1,
    guild_id: int = 10,
    channel_id: int = 100,
    source: str = "manual",
    topic: str = "最近過得好嗎",
    score: float = 50.0,
    topic_id: int | None = None,
) -> CareCandidate:
    return CareCandidate(
        user_id=user_id,
        guild_id=guild_id,
        channel_id=channel_id,
        source=source,
        source_ref=str(user_id),
        topic=topic,
        score=score,
        topic_id=topic_id,
    )


def _budget_rows(db_path: Path) -> list[dict[str, object]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM care_budget").fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# 同意 / 隱私
# ---------------------------------------------------------------------------
def test_opt_out_blocks_care_even_through_direct_guard_call(
    engine: CareEngine, repo: CareRepository, guard: AutonomyGuard
) -> None:
    denied = engine.evaluate(_candidate())
    assert denied.allowed is False
    assert "未 opt-in" in denied.reason
    assert denied.action_id

    # 直接呼叫治理入口也必須被同一個同意閘門擋下（不能只靠 engine 自律）。
    direct = guard.check("proactive_care", {"user_id": 1, "guild_id": 10})
    assert direct.allowed is False
    assert "未 opt-in" in direct.reason

    # 非關懷行動不受 care 閘門影響。
    unrelated = guard.check("sys_inspect")
    assert unrelated.allowed is True

    repo.set_opt_in(1, 10, opt_in=True, frequency="weekly", now=NOW)
    allowed = engine.evaluate(_candidate())
    assert allowed.allowed is True
    assert "額度核准" in allowed.reason


def test_opt_in_is_scoped_to_guild_with_global_fallback(repo: CareRepository) -> None:
    repo.set_opt_in(7, 20, opt_in=True, now=NOW)
    assert repo.is_opted_in(7, 20) is True
    assert repo.is_opted_in(7, 30) is False

    repo.set_opt_in(7, "", opt_in=True, now=NOW)
    assert repo.is_opted_in(7, 30) is True
    assert repo.is_opted_in(8, 30) is False


def test_frequency_off_disables_opt_in_and_rejects_unknown_values(
    repo: CareRepository,
) -> None:
    repo.set_opt_in(1, opt_in=True, frequency="daily", now=NOW)
    assert repo.get_preference(1).frequency == "daily"

    off = repo.set_frequency(1, frequency="off", now=NOW)
    assert off.opt_in is False
    assert off.frequency == "off"
    assert repo.is_opted_in(1) is False

    with pytest.raises(ValueError):
        repo.set_opt_in(1, opt_in=True, frequency="hourly")


def test_forget_user_removes_preferences_topics_actions_and_budget(
    engine: CareEngine, repo: CareRepository, tmp_path: Path
) -> None:
    repo.set_opt_in(5, opt_in=True, now=NOW)
    topic_id = repo.add_topic(user_id=5, topic="專題進度", now=NOW)
    assert topic_id is not None
    decision = engine.evaluate(_candidate(user_id=5))
    assert decision.allowed is True

    removed = repo.forget_user(5)

    assert removed >= 3
    assert repo.is_opted_in(5) is False
    assert repo.list_actions(5) == []
    assert repo.list_pending_topics() == []
    user_budgets = [
        row
        for row in _budget_rows(tmp_path / "care.db")
        if row["scope"] == "user"
    ]
    assert user_budgets == []


# ---------------------------------------------------------------------------
# 額度
# ---------------------------------------------------------------------------
def test_weekly_budget_is_a_hard_stop(
    engine: CareEngine, repo: CareRepository, tmp_path: Path
) -> None:
    repo.set_opt_in(1, 10, opt_in=True, now=NOW)

    decisions = [
        engine.evaluate(_candidate(topic=f"話題 {index}"), now=NOW)
        for index in range(3)
    ]

    assert [item.allowed for item in decisions] == [True, True, False]
    assert "每人每週" in decisions[2].reason
    assert decisions[2].action_id

    rows = {row["scope"]: row for row in _budget_rows(tmp_path / "care.db")}
    assert rows["user"]["used"] == 2
    assert rows["user"]["limit_value"] == 2
    assert rows["global"]["used"] == 2


def test_global_daily_budget_blocks_other_users_without_charging_them(
    repo: CareRepository, guard: AutonomyGuard, tmp_path: Path
) -> None:
    engine = CareEngine(
        repository=repo, guard=guard, weekly_limit=5, daily_limit=1
    )
    repo.set_opt_in(1, opt_in=True, now=NOW)
    repo.set_opt_in(2, opt_in=True, now=NOW)

    first = engine.evaluate(_candidate(user_id=1), now=NOW)
    second = engine.evaluate(_candidate(user_id=2), now=NOW)

    assert first.allowed is True
    assert second.allowed is False
    assert "全域每日" in second.reason
    users = {
        row["scope_id"]: row
        for row in _budget_rows(tmp_path / "care.db")
        if row["scope"] == "user"
    }
    assert users["1"]["used"] == 1
    assert "2" not in users


def test_zero_limits_reject_before_creating_budget_rows(tmp_path: Path) -> None:
    repo = CareRepository(tmp_path / "care.db")
    result = repo.consume_budget(1, weekly_limit=0, daily_limit=10, now=NOW)
    assert result.allowed is False
    assert "每週" in result.reason
    assert _budget_rows(tmp_path / "care.db") == []


# ---------------------------------------------------------------------------
# 候選來源與深夜線索
# ---------------------------------------------------------------------------
def test_three_candidate_sources_are_merged_and_sorted(
    repo: CareRepository, guard: AutonomyGuard
) -> None:
    relations = SimpleNamespace(
        store={
            "1": {"affinity": 80, "last_interaction": 0},
            "2": {"affinity": 50, "last_interaction": 0},
            "3": {"affinity": 90, "last_interaction": NOW - 86400},
        }
    )
    spark_repo = SimpleNamespace(
        list_all=lambda: [
            SimpleNamespace(
                trigger_id="t1",
                trigger_type="schedule",
                is_active=True,
                user_id=4,
                channel_id=40,
                condition={"care": True},
                extra_data={"guild_id": 400, "care_score": 70},
                action={},
                message="SPARK 定時關心",
            ),
            SimpleNamespace(
                trigger_id="t2",
                trigger_type="schedule",
                is_active=True,
                user_id=5,
                channel_id=50,
                condition={},
                extra_data={},
                action={"target": "STOCK"},
                message="非關懷",
            ),
            SimpleNamespace(
                trigger_id="t3",
                trigger_type="schedule",
                is_active=False,
                user_id=6,
                channel_id=60,
                condition={"care": True},
                extra_data={},
                action={},
                message="已停用",
            ),
        ]
    )
    repo.add_topic(user_id=7, guild_id=700, topic="考試加油", score=65, now=NOW)
    engine = CareEngine(
        repository=repo,
        guard=guard,
        relationship_manager=relations,
        spark_repository=spark_repo,
    )

    candidates = engine.candidates(now=NOW)

    assert [item.source for item in candidates] == ["relationship", "spark", "nightly"]
    assert [item.user_id for item in candidates] == [1, 4, 7]
    assert [item.score for item in candidates] == [80.0, 70.0, 65.0]


def test_duplicate_topics_keep_the_higher_score(
    repo: CareRepository, guard: AutonomyGuard
) -> None:
    relations = SimpleNamespace(
        store={"1": {"affinity": 80, "last_interaction": 0}}
    )
    spark_repo = SimpleNamespace(
        list_all=lambda: [
            SimpleNamespace(
                trigger_id="t1",
                trigger_type="schedule",
                is_active=True,
                user_id=1,
                channel_id=0,
                condition={"care": True},
                extra_data={"care_score": 95},
                action={},
                message="好久沒聊聊了，想關心一下近況",
            )
        ]
    )
    engine = CareEngine(
        repository=repo,
        guard=guard,
        relationship_manager=relations,
        spark_repository=spark_repo,
    )

    candidates = engine.candidates(now=NOW)

    assert len(candidates) == 1
    assert candidates[0].source == "spark"
    assert candidates[0].score == 95.0


def test_nightly_topic_is_consumed_only_when_approved(
    engine: CareEngine, repo: CareRepository
) -> None:
    repo.set_opt_in(1, opt_in=True, now=NOW)
    topic_id = repo.add_topic(user_id=1, topic="專題進度", score=70, now=NOW)
    assert topic_id is not None

    pending = engine.nightly_candidates()
    assert len(pending) == 1
    assert pending[0].topic_id == topic_id

    decision = engine.evaluate(pending[0], now=NOW)

    assert decision.allowed is True
    assert repo.list_pending_topics() == []


def test_denied_nightly_topic_stays_pending(
    engine: CareEngine, repo: CareRepository
) -> None:
    topic_id = repo.add_topic(user_id=9, topic="尚未同意", now=NOW)
    assert topic_id is not None

    decision = engine.evaluate(engine.nightly_candidates()[0], now=NOW)

    assert decision.allowed is False
    assert [row["topic_id"] for row in repo.list_pending_topics()] == [topic_id]


def test_nightly_hook_writes_at_most_two_topics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = CareRepository(tmp_path / "care.db")
    engine = CareEngine(repository=repo, guard=AutonomyGuard(), weekly_limit=2)
    monkeypatch.setattr(care_package, "_engine", engine)

    _remember_care_topics(
        "42",
        "2026-10-20",
        {"care_topics": ["考試", "加班", "", "第三條不該寫入"]},
    )

    rows = repo.list_pending_topics()
    assert len(rows) == 2
    assert {row["user_id"] for row in rows} == {"42"}
    assert {row["source_ref"] for row in rows} == {"2026-10-20"}


def test_add_topic_is_idempotent(repo: CareRepository) -> None:
    first = repo.add_topic(user_id=1, topic="同一條", now=NOW)
    second = repo.add_topic(user_id=1, topic="同一條", now=NOW)
    assert first is not None
    assert second is None
    assert len(repo.list_pending_topics()) == 1


# ---------------------------------------------------------------------------
# 稽核 / 治理
# ---------------------------------------------------------------------------
def test_rejected_action_is_audited_with_reason(
    engine: CareEngine, repo: CareRepository
) -> None:
    decision = engine.evaluate(_candidate(user_id=8), now=NOW)

    rows = repo.list_actions(8, statuses=("rejected",))
    assert len(rows) == 1
    assert rows[0]["action_id"] == decision.action_id
    assert "未 opt-in" in rows[0]["reason"]
    assert rows[0]["created_at"] == NOW


def test_gate_exception_fails_closed_and_consumes_no_budget(
    engine: CareEngine, repo: CareRepository, guard: AutonomyGuard, tmp_path: Path
) -> None:
    repo.set_opt_in(1, opt_in=True, now=NOW)

    def broken_gate(action_type: str, params: dict[str, object]) -> tuple[bool, str]:
        raise RuntimeError("gate backend down")

    guard.register_gate("broken", broken_gate)

    decision = engine.evaluate(_candidate(), now=NOW)

    assert decision.allowed is False
    assert "例外" in decision.reason
    assert _budget_rows(tmp_path / "care.db") == []
    assert repo.list_actions(1, statuses=("rejected",))[0]["status"] == "rejected"


def test_unregistered_gate_is_removed(guard: AutonomyGuard) -> None:
    guard.register_gate("temporary", lambda action_type, params: (False, "no"))
    assert guard.check("proactive_care", {"user_id": 1}).allowed is False

    guard.unregister_gate("temporary")
    guard.register_gate("care_consent", lambda action_type, params: (True, ""))
    assert guard.check("proactive_care", {"user_id": 1}).allowed is True


def test_response_feedback_updates_audit_status(
    engine: CareEngine, repo: CareRepository
) -> None:
    repo.set_opt_in(1, opt_in=True, now=NOW)
    decision = engine.evaluate(_candidate(), now=NOW)
    assert decision.allowed is True

    assert engine.record_response(decision.action_id, responded=True) is True
    rows = repo.list_actions(1, statuses=("responded",))
    assert len(rows) == 1
    assert engine.record_response("missing", responded=False) is False


def test_proactive_care_is_medium_risk() -> None:
    risk = RiskPolicyEngine().classify_risk("proactive_care")
    assert risk is ActionRiskLevel.MEDIUM
