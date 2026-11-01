"""R20 主動關懷：灰度、自適應額度、回饋報表、指令與 Web API。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from quart import Quart

import handlers.care.web as care_web
from core.autonomy_guard import AutonomyGuard
from handlers.care import (
    CareCandidate,
    CareCog,
    CareEngine,
    CareRepository,
    build_care_engine,
)
from handlers.care.repository import rollout_bucket
from handlers.care.rollout import CareRollout

NOW = 10_000_000.0


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture()
def repo(tmp_path: Path) -> CareRepository:
    return CareRepository(tmp_path / "care.db")


@pytest.fixture()
def guard() -> AutonomyGuard:
    return AutonomyGuard(max_actions_per_hour=100, max_actions_per_day=200)


@pytest.fixture()
def engine(repo: CareRepository, guard: AutonomyGuard) -> CareEngine:
    return CareEngine(
        repository=repo,
        guard=guard,
        rollout=CareRollout(repo),
        weekly_limit=2,
        daily_limit=20,
        adaptive_budget=True,
        feedback_window_days=30,
        min_feedback_samples=3,
        silence_downgrade_threshold=0.6,
        silence_stop_threshold=0.9,
    )


def _candidate(user_id: int = 1, guild_id: int = 10, topic: str = "近況") -> CareCandidate:
    return CareCandidate(
        user_id=user_id,
        guild_id=guild_id,
        channel_id=100,
        source="manual",
        source_ref=str(user_id),
        topic=topic,
        score=50.0,
    )


def _seed_actions(
    repo: CareRepository,
    user_id: int = 1,
    *,
    silent: int = 0,
    responded: int = 0,
    guild_id: int = 10,
    now: float = NOW,
) -> None:
    for _ in range(silent):
        repo.record_action(
            user_id=user_id,
            guild_id=guild_id,
            source="manual",
            topic="沉默",
            status="silent",
            now=now,
        )
    for _ in range(responded):
        repo.record_action(
            user_id=user_id,
            guild_id=guild_id,
            source="manual",
            topic="回覆",
            status="responded",
            now=now,
        )


# ---------------------------------------------------------------------------
# 灰度
# ---------------------------------------------------------------------------
def test_unopened_guild_never_triggers_and_consumes_no_budget(
    engine: CareEngine, repo: CareRepository
) -> None:
    repo.set_opt_in(1, 10, opt_in=True, now=NOW)

    decision = engine.evaluate(_candidate(), now=NOW)

    assert decision.allowed is False
    assert "尚未開啟" in decision.reason
    assert repo.budget_snapshot() == []
    assert repo.list_actions(1, statuses=("rejected",))[0]["reason"] == decision.reason


def test_enabled_guild_allows_and_disabled_revokes(
    engine: CareEngine, repo: CareRepository
) -> None:
    repo.set_opt_in(1, 10, opt_in=True, now=NOW)
    repo.set_guild_rollout(10, enabled=True, rollout_percent=100, now=NOW)
    assert engine.evaluate(_candidate(), now=NOW).allowed is True

    repo.set_guild_rollout(10, enabled=False, rollout_percent=100, now=NOW)
    revoked = engine.evaluate(_candidate(topic="第二條"), now=NOW)
    assert revoked.allowed is False
    assert "尚未開啟" in revoked.reason


def test_partial_rollout_is_deterministic_and_zero_leak(
    engine: CareEngine, repo: CareRepository
) -> None:
    in_bucket = next(uid for uid in range(1, 200) if rollout_bucket(10, uid) < 50)
    out_bucket = next(uid for uid in range(1, 200) if rollout_bucket(10, uid) >= 50)
    repo.set_guild_rollout(10, enabled=True, rollout_percent=50, now=NOW)
    repo.set_opt_in(in_bucket, 10, opt_in=True, now=NOW)
    repo.set_opt_in(out_bucket, 10, opt_in=True, now=NOW)

    inside = engine.evaluate(_candidate(user_id=in_bucket), now=NOW)
    outside = engine.evaluate(_candidate(user_id=out_bucket), now=NOW)

    assert inside.allowed is True
    assert outside.allowed is False
    assert "未命中" in outside.reason
    assert repo.is_guild_enabled(10, in_bucket) is True
    assert repo.is_guild_enabled(10, out_bucket) is False


def test_direct_message_is_not_gray_gated(
    engine: CareEngine, repo: CareRepository
) -> None:
    repo.set_opt_in(1, 0, opt_in=True, now=NOW)
    decision = engine.evaluate(_candidate(guild_id=0), now=NOW)
    assert decision.allowed is True


def test_engine_without_rollout_is_ungated_for_offline_evaluation(
    repo: CareRepository, guard: AutonomyGuard
) -> None:
    engine = CareEngine(repository=repo, guard=guard, weekly_limit=2)
    repo.set_opt_in(1, 10, opt_in=True, now=NOW)
    assert engine.evaluate(_candidate(), now=NOW).allowed is True


def test_build_care_engine_injects_rollout(repo: CareRepository) -> None:
    engine = build_care_engine(repository=repo)
    assert isinstance(engine.rollout, CareRollout)
    assert engine.rollout.repository is repo


def test_rollout_validation_and_listing(repo: CareRepository) -> None:
    with pytest.raises(ValueError):
        repo.set_guild_rollout("", enabled=True)

    record = repo.set_guild_rollout(42, enabled=True, rollout_percent=150, now=NOW)
    assert record.rollout_percent == 100
    assert [item.guild_id for item in repo.list_guild_rollouts()] == ["42"]

    disabled = repo.set_guild_rollout(42, enabled=False, rollout_percent=80, now=NOW)
    assert disabled.rollout_percent == 0


# ---------------------------------------------------------------------------
# 自適應額度 / 回饋
# ---------------------------------------------------------------------------
def test_adaptive_budget_downgrades_then_stops(
    engine: CareEngine, repo: CareRepository
) -> None:
    repo.set_opt_in(1, 10, opt_in=True, now=NOW)
    repo.set_guild_rollout(10, enabled=True, rollout_percent=100, now=NOW)
    _seed_actions(repo, silent=2, responded=1)

    limit, reason = engine.effective_weekly_limit(1, now=NOW)
    assert limit == 1
    assert reason == ""
    assert engine.evaluate(_candidate(topic="一"), now=NOW).allowed is True
    second = engine.evaluate(_candidate(topic="二"), now=NOW)
    assert second.allowed is False
    assert "1/1" in second.reason

    repo2 = CareRepository(repo.db_path.parent / "stop.db")
    engine2 = CareEngine(
        repository=repo2,
        guard=AutonomyGuard(),
        rollout=CareRollout(repo2),
        weekly_limit=2,
        adaptive_budget=True,
        min_feedback_samples=3,
        silence_stop_threshold=0.9,
    )
    repo2.set_opt_in(2, 10, opt_in=True, now=NOW)
    repo2.set_guild_rollout(10, enabled=True, rollout_percent=100, now=NOW)
    _seed_actions(repo2, user_id=2, silent=3)

    limit2, reason2 = engine2.effective_weekly_limit(2, now=NOW)
    assert limit2 == 0
    assert "沉默率" in reason2
    stopped = engine2.evaluate(_candidate(user_id=2), now=NOW)
    assert stopped.allowed is False
    assert "本週暫停" in stopped.reason


def test_adaptive_budget_keeps_base_when_samples_are_positive(
    engine: CareEngine, repo: CareRepository
) -> None:
    _seed_actions(repo, silent=1, responded=3)
    limit, reason = engine.effective_weekly_limit(1, now=NOW)
    assert limit == 2
    assert reason == ""


def test_feedback_stats_use_only_resolved_actions(
    repo: CareRepository,
) -> None:
    _seed_actions(repo, silent=1, responded=3)
    repo.record_action(
        user_id=1, guild_id=10, source="manual", topic="拒絕", status="rejected", now=NOW
    )
    repo.record_action(
        user_id=1, guild_id=10, source="manual", topic="待回", status="approved", now=NOW
    )

    stats = repo.feedback_stats(user_id=1, since=NOW - 1)

    assert stats.approved == 5
    assert stats.rejected == 1
    assert stats.responded == 3
    assert stats.silent == 1
    assert stats.pending == 1
    assert stats.resolved == 4
    assert stats.response_rate == 0.75
    assert stats.silence_rate == 0.25


def test_settle_silence_marks_only_stale_pending_actions(
    engine: CareEngine, repo: CareRepository
) -> None:
    old_id = repo.record_action(
        user_id=1, guild_id=10, source="manual", topic="舊", status="approved", now=NOW - 300_000
    )
    recent_id = repo.record_action(
        user_id=1, guild_id=10, source="manual", topic="新", status="approved", now=NOW
    )
    answered_id = repo.record_action(
        user_id=1, guild_id=10, source="manual", topic="已回", status="approved", now=NOW - 300_000
    )
    repo.update_action(answered_id, status="responded", now=NOW - 250_000)

    settled = engine.settle_silence(hours=48, now=NOW)

    assert settled == 1
    rows = {row["action_id"]: row for row in repo.list_actions(1)}
    assert rows[old_id]["status"] == "silent"
    assert rows[recent_id]["status"] == "approved"
    assert rows[answered_id]["status"] == "responded"


def test_feedback_report_contains_guild_breakdown_and_limits(
    engine: CareEngine, repo: CareRepository
) -> None:
    repo.set_opt_in(1, 10, opt_in=True, now=NOW)
    repo.set_opt_in(2, 20, opt_in=True, now=NOW)
    repo.set_guild_rollout(10, enabled=True, rollout_percent=100, now=NOW)
    repo.set_guild_rollout(20, enabled=True, rollout_percent=100, now=NOW)
    _seed_actions(repo, user_id=1, silent=1, responded=1, guild_id=10)
    _seed_actions(repo, user_id=2, silent=2, guild_id=20)
    repo.add_topic(user_id=1, guild_id=10, topic="待回訪", now=NOW)

    report = engine.feedback_report(days=30, now=NOW)

    assert report["overall"]["resolved"] == 4
    assert report["overall"]["silence_rate"] == 0.75
    assert {item["guild_id"] for item in report["by_guild"]} == {"10", "20"}
    assert report["opt_in_users"] == 2
    assert report["pending_topics"] == 1
    assert report["limits"]["weekly_per_user"] == 2
    assert report["limits"]["adaptive_enabled"] is True


def test_preference_status_reports_guild_rollout(
    engine: CareEngine, repo: CareRepository
) -> None:
    repo.set_opt_in(1, 10, opt_in=True, frequency="daily", now=NOW)
    status = engine.preference_status(1, 10)
    assert status["opt_in"] is True
    assert status["frequency"] == "daily"
    assert status["guild_enabled"] is False
    assert status["effective_weekly_limit"] == 2


# ---------------------------------------------------------------------------
# /care 指令
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_care_commands_are_registered() -> None:
    names = {command.name for command in CareCog.care.commands}
    assert names == {"opt-in", "opt-out", "frequency", "status", "report"}


@pytest.mark.anyio
async def test_extension_setup_loads_care_cog(
    engine: CareEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    import handlers.care as care_package

    monkeypatch.setattr(
        care_package, "build_care_engine", lambda **kwargs: engine
    )
    added: list[object] = []

    class _FakeBot:
        def get_cog(self, name: str) -> None:
            return None

        async def add_cog(self, cog: object) -> None:
            added.append(cog)

    bot = _FakeBot()
    await care_package.setup(bot)

    assert isinstance(added[0], CareCog)
    assert added[0].engine is engine
    assert bot.care_engine is engine


class _FakeResponse:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send_message(self, content: str | None = None, **kwargs: object) -> None:
        self.messages.append({"content": content, **kwargs})


@pytest.mark.anyio
async def test_opt_out_command_writes_preference(
    repo: CareRepository, engine: CareEngine
) -> None:
    repo.set_opt_in(7, 10, opt_in=True, now=NOW)
    cog = CareCog(SimpleNamespace(), engine)
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=7),
        guild_id=10,
        guild=SimpleNamespace(id=10),
        response=_FakeResponse(),
    )

    command = CareCog.care.get_command("opt-out")
    assert command is not None
    await command.callback(cog, interaction)

    assert repo.is_opted_in(7, 10) is False
    assert interaction.response.messages[0]["ephemeral"] is True


@pytest.mark.anyio
async def test_report_command_rejects_non_manager(
    repo: CareRepository, engine: CareEngine
) -> None:
    cog = CareCog(SimpleNamespace(), engine)
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=7, guild_permissions=SimpleNamespace(manage_guild=False)),
        guild_id=10,
        guild=SimpleNamespace(id=10),
        response=_FakeResponse(),
    )

    command = CareCog.care.get_command("report")
    assert command is not None
    await command.callback(cog, interaction, 30)

    assert "管理伺服器" in interaction.response.messages[0]["content"]


# ---------------------------------------------------------------------------
# Web API
# ---------------------------------------------------------------------------
@pytest.fixture()
def care_app(engine: CareEngine) -> Quart:
    app = Quart(__name__)
    app.register_blueprint(care_web.care_bp)
    app.config["TESTING"] = True
    care_web.set_engine(engine)
    return app


@pytest.mark.anyio
async def test_web_overview_and_guild_rollout(
    care_app: Quart, repo: CareRepository
) -> None:
    async with care_app.test_client() as client:
        response = await client.get("/api/care/overview")
        assert response.status_code == 200
        data = await response.get_json()
        assert data["status"] == "success"
        assert data["overview"]["limits"]["weekly_per_user"] == 2

        saved = await client.post(
            "/api/care/guilds",
            json={"guild_id": 99, "enabled": True, "rollout_percent": 25},
        )
        assert saved.status_code == 200
        saved_data = await saved.get_json()
        assert saved_data["guild"]["rollout_percent"] == 25

        listed = await client.get("/api/care/guilds")
        listed_data = await listed.get_json()
        assert listed_data["guilds"][0]["guild_id"] == "99"


@pytest.mark.anyio
async def test_web_rejects_invalid_guild_payload(care_app: Quart) -> None:
    async with care_app.test_client() as client:
        for payload in (
            {"guild_id": "abc"},
            {"guild_id": 0},
            {"guild_id": 1, "rollout_percent": 101},
            {"guild_id": 1, "rollout_percent": "many"},
        ):
            response = await client.post("/api/care/guilds", json=payload)
            assert response.status_code == 400


@pytest.mark.anyio
async def test_web_actions_status_and_settle(
    care_app: Quart, engine: CareEngine, repo: CareRepository
) -> None:
    repo.set_opt_in(3, 10, opt_in=True, now=NOW)
    repo.set_guild_rollout(10, enabled=True, rollout_percent=100, now=NOW)
    decision = engine.evaluate(_candidate(user_id=3), now=NOW)
    assert decision.allowed is True

    async with care_app.test_client() as client:
        status = await client.get("/api/care/status?user_id=3&guild_id=10")
        assert status.status_code == 200
        status_data = await status.get_json()
        assert status_data["preference"]["opt_in"] is True

        actions = await client.get("/api/care/actions?user_id=3")
        actions_data = await actions.get_json()
        assert actions_data["actions"][0]["action_id"] == decision.action_id

        silent = await client.post(
            f"/api/care/actions/{decision.action_id}/response",
            json={"responded": False},
        )
        assert silent.status_code == 200
        assert (await silent.get_json())["state"] == "silent"

        settled = await client.post("/api/care/settle", json={"hours": 0})
        assert settled.status_code == 200
        assert (await settled.get_json())["settled"] == 0

        missing = await client.post(
            "/api/care/actions/not-found/response", json={"responded": True}
        )
        assert missing.status_code == 404
