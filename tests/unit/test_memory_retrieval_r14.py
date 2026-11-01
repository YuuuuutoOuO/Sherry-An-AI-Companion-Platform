"""
tests/unit/test_memory_retrieval_r14.py
=======================================
R14：長期記憶檢索 Part 1（FTS5 基礎）回歸測試。

涵蓋：
1. FTS5 trigram（長詞）與 LIKE（短詞）雙路召回。
2. 事實／Profile 軟刪除後 0 召回。
3. 使用者隔離與回傳上限。
4. 權重骨架：重要性、時效、來源優先序排序。
5. FTS triggers 與 INSERT / UPDATE / DELETE 同步。
6. ``rebuild_facts_fts`` 與 ``tools/backfill_fts.py`` CLI。
7. async 包裝與評測集 v1 門檻。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

import handlers.memory.storage as mem_storage
from core.db import db_transaction
from handlers.memory.retrieval import (
    RetrievalWeights,
    asearch_facts,
    search_facts,
    search_facts_for_user,
)

_NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)


@pytest.fixture()
def memory_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔離的記憶資料庫（不觸碰真實 data/）。"""
    db_path = tmp_path / "sherry_memory.db"
    monkeypatch.setattr(mem_storage, "MEMORY_DB_PATH", db_path)
    monkeypatch.setattr(mem_storage, "_INITIALIZED_MEMORY_DBS", set())
    mem_storage.ensure_memory_db(db_path)
    return db_path


def _add_profile(db_path: Path, user_id: str, *, deleted: bool = False) -> None:
    with db_transaction(db_path) as cur:
        cur.execute(
            """
            INSERT INTO user_profiles (user_id, aliases_json, is_deleted, deleted_at, updated_at)
            VALUES (?, '[]', ?, ?, ?)
            """,
            (
                user_id,
                1 if deleted else 0,
                "2026-10-01T00:00:00+00:00" if deleted else None,
                "2026-10-01T00:00:00+00:00",
            ),
        )


def _add_fact(
    db_path: Path,
    user_id: str,
    fact: str,
    *,
    category: str = "identity",
    importance: int = 3,
    updated_at: str = "2026-10-15T00:00:00+00:00",
    deleted: bool = False,
) -> int:
    with db_transaction(db_path) as cur:
        cur.execute(
            """
            INSERT INTO user_facts
                (user_id, fact, category, importance, is_deleted, deleted_at,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                fact,
                category,
                importance,
                1 if deleted else 0,
                "2026-10-01T00:00:00+00:00" if deleted else None,
                updated_at,
                updated_at,
            ),
        )
        return int(cur.lastrowid)


# ---------------------------------------------------------------------------
# 1. 關鍵字召回
# ---------------------------------------------------------------------------


def test_fts_trigram_and_like_short_term_recall(memory_db: Path) -> None:
    _add_profile(memory_db, "u1")
    _add_fact(memory_db, "u1", "養了一隻叫小雪的白色貓咪", category="identity", importance=5)
    _add_fact(memory_db, "u1", "喜歡喝紅茶，尤其是伯爵紅茶", category="preference", importance=4)
    _add_fact(memory_db, "u1", "最近在學 Python 的非同步程式設計", category="habit")

    long_results = search_facts("白色貓咪", user_id="u1", db_path=memory_db, now=_NOW)
    assert [item.fact for item in long_results] == ["養了一隻叫小雪的白色貓咪"]
    assert long_results[0].matched_by == "fts"

    short_results = search_facts("紅茶", user_id="u1", db_path=memory_db, now=_NOW)
    assert [item.fact for item in short_results] == ["喜歡喝紅茶，尤其是伯爵紅茶"]
    assert short_results[0].matched_by == "like"

    ascii_results = search_facts("Python", user_id="u1", db_path=memory_db, now=_NOW)
    assert [item.fact for item in ascii_results] == ["最近在學 Python 的非同步程式設計"]


def test_multi_term_or_recall(memory_db: Path) -> None:
    _add_profile(memory_db, "u1")
    _add_fact(memory_db, "u1", "養了一隻叫小雪的白色貓咪")
    _add_fact(memory_db, "u1", "在台北的軟體公司當後端工程師")

    results = search_facts("貓咪 工程師", user_id="u1", db_path=memory_db, now=_NOW)
    assert {item.fact for item in results} == {
        "養了一隻叫小雪的白色貓咪",
        "在台北的軟體公司當後端工程師",
    }


def test_empty_or_unknown_query_returns_empty(memory_db: Path) -> None:
    _add_profile(memory_db, "u1")
    _add_fact(memory_db, "u1", "喜歡喝紅茶")

    assert search_facts("   ", user_id="u1", db_path=memory_db) == []
    assert search_facts("量子力學", user_id="u1", db_path=memory_db, now=_NOW) == []
    assert search_facts("...", user_id="u1", db_path=memory_db) == []


# ---------------------------------------------------------------------------
# 2. 軟刪硬過濾
# ---------------------------------------------------------------------------


def test_soft_deleted_fact_zero_recall(memory_db: Path) -> None:
    _add_profile(memory_db, "u1")
    _add_fact(memory_db, "u1", "舊地址是台北市信義區松高路 77 號", deleted=True)
    _add_fact(memory_db, "u1", "目前住在台北市大安區")

    results = search_facts("信義區", user_id="u1", db_path=memory_db, now=_NOW)
    assert results == []

    broader = search_facts("台北", user_id="u1", db_path=memory_db, now=_NOW)
    assert [item.fact for item in broader] == ["目前住在台北市大安區"]


def test_soft_deleted_profile_zero_recall(memory_db: Path) -> None:
    _add_profile(memory_db, "u_gone", deleted=True)
    _add_fact(memory_db, "u_gone", "住在高雄")

    assert search_facts("高雄", user_id="u_gone", db_path=memory_db, now=_NOW) == []
    assert search_facts("高雄", db_path=memory_db, now=_NOW) == []


# ---------------------------------------------------------------------------
# 3. 使用者隔離與上限
# ---------------------------------------------------------------------------


def test_user_scoping_and_limit(memory_db: Path) -> None:
    _add_profile(memory_db, "u_alice")
    _add_profile(memory_db, "u_bob")
    _add_fact(memory_db, "u_alice", "喜歡喝紅茶")
    _add_fact(memory_db, "u_bob", "喜歡喝黑咖啡")
    _add_fact(memory_db, "u_bob", "喜歡週末去看電影", importance=5)

    bob_only = search_facts("喜歡", user_id="u_bob", db_path=memory_db, now=_NOW)
    assert {item.user_id for item in bob_only} == {"u_bob"}
    assert len(bob_only) == 2

    limited = search_facts("喜歡", user_id="u_bob", limit=1, db_path=memory_db, now=_NOW)
    assert len(limited) == 1
    assert limited[0].importance == 5

    everyone = search_facts_for_user("u_alice", "喜歡", db_path=memory_db, now=_NOW)
    assert [item.fact for item in everyone] == ["喜歡喝紅茶"]


# ---------------------------------------------------------------------------
# 4. 權重骨架排序
# ---------------------------------------------------------------------------


def test_importance_weight_ranks_higher_fact_first(memory_db: Path) -> None:
    _add_profile(memory_db, "u1")
    _add_fact(memory_db, "u1", "喜歡喝紅茶", importance=2)
    _add_fact(memory_db, "u1", "喜歡喝抹茶", importance=5)

    weights = RetrievalWeights(
        bm25=0.0, cos_sim=0.0, recency=0.0, affinity=0.0, importance=1.0, source_priority=0.0
    )
    results = search_facts(
        "喜歡", user_id="u1", db_path=memory_db, now=_NOW, weights=weights
    )
    assert [item.fact for item in results] == ["喜歡喝抹茶", "喜歡喝紅茶"]


def test_recency_weight_ranks_recent_fact_first(memory_db: Path) -> None:
    _add_profile(memory_db, "u1")
    _add_fact(memory_db, "u1", "喜歡喝紅茶", updated_at="2025-01-01T00:00:00+00:00")
    _add_fact(memory_db, "u1", "喜歡喝抹茶", updated_at="2026-10-19T00:00:00+00:00")

    weights = RetrievalWeights(
        bm25=0.0, cos_sim=0.0, recency=1.0, affinity=0.0, importance=0.0, source_priority=0.0
    )
    results = search_facts(
        "喜歡", user_id="u1", db_path=memory_db, now=_NOW, weights=weights
    )
    assert [item.fact for item in results] == ["喜歡喝抹茶", "喜歡喝紅茶"]


def test_source_priority_weights_rule_above_social(memory_db: Path) -> None:
    _add_profile(memory_db, "u1")
    _add_fact(memory_db, "u1", "喜歡喝紅茶", category="social", importance=3)
    _add_fact(memory_db, "u1", "喜歡喝抹茶", category="rule", importance=3)

    weights = RetrievalWeights(
        bm25=0.0, cos_sim=0.0, recency=0.0, affinity=0.0, importance=0.0, source_priority=1.0
    )
    results = search_facts(
        "喜歡", user_id="u1", db_path=memory_db, now=_NOW, weights=weights
    )
    assert [item.fact for item in results] == ["喜歡喝抹茶", "喜歡喝紅茶"]
    assert results[0].source_priority == 1.0


def test_weights_normalize_and_affinity_placeholder(memory_db: Path) -> None:
    _add_profile(memory_db, "u1")
    _add_fact(memory_db, "u1", "喜歡喝紅茶")

    weights = RetrievalWeights(
        bm25=2.0, cos_sim=0.0, recency=1.0, affinity=0.0, importance=1.0, source_priority=0.0
    ).normalized()
    assert weights.bm25 + weights.recency + weights.importance == pytest.approx(1.0)

    results = search_facts(
        "喜歡",
        user_id="u1",
        db_path=memory_db,
        now=_NOW,
        weights=weights,
        affinity_by_user={"u1": 0.8},
    )
    # 權重為 0 時，placeholder 維度只回報觀測值、不影響分數。
    assert results[0].cos_sim == 0.0
    assert results[0].affinity == pytest.approx(0.8)
    assert results[0].score == pytest.approx(
        weights.bm25 * results[0].bm25_norm
        + weights.recency * results[0].recency
        + weights.importance * (results[0].importance - 1) / 4.0
    )


# ---------------------------------------------------------------------------
# 5. FTS triggers / rebuild
# ---------------------------------------------------------------------------


def test_fts_triggers_sync_insert_update_delete(memory_db: Path) -> None:
    _add_profile(memory_db, "u1")
    fact_id = _add_fact(memory_db, "u1", "喜歡喝伯爵紅茶")

    assert len(search_facts("伯爵紅茶", user_id="u1", db_path=memory_db, now=_NOW)) == 1

    with db_transaction(memory_db) as cur:
        cur.execute(
            "UPDATE user_facts SET fact = ?, updated_at = ? WHERE id = ?",
            ("喜歡喝抹茶拿鐵", "2026-10-19T00:00:00+00:00", fact_id),
        )

    assert search_facts("伯爵紅茶", user_id="u1", db_path=memory_db, now=_NOW) == []
    assert [
        item.fact for item in search_facts("抹茶拿鐵", user_id="u1", db_path=memory_db)
    ] == ["喜歡喝抹茶拿鐵"]

    with db_transaction(memory_db) as cur:
        cur.execute("DELETE FROM user_facts WHERE id = ?", (fact_id,))

    assert search_facts("抹茶拿鐵", user_id="u1", db_path=memory_db) == []


def test_ensure_facts_fts_creates_triggers_and_rebuild_flag(memory_db: Path) -> None:
    with db_transaction(memory_db) as cur:
        assert mem_storage.ensure_facts_fts(cur) is False
        names = {
            row[0]
            for row in cur.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            ).fetchall()
        }
    assert set(mem_storage._FACTS_FTS_TRIGGER_NAMES) <= names


def test_rebuild_facts_fts_recovers_lost_index(memory_db: Path) -> None:
    _add_profile(memory_db, "u1")
    _add_fact(memory_db, "u1", "喜歡喝伯爵紅茶")
    _add_fact(memory_db, "u1", "週末去陽明山爬山")

    with db_transaction(memory_db) as cur:
        cur.execute("INSERT INTO facts_fts(facts_fts) VALUES('delete-all')")

    assert search_facts("伯爵紅茶", user_id="u1", db_path=memory_db) == []
    assert search_facts("陽明山", user_id="u1", db_path=memory_db) == []

    stats = mem_storage.rebuild_facts_fts(memory_db)
    assert stats == {"total": 2, "active": 2}

    assert [
        item.fact for item in search_facts("伯爵紅茶", user_id="u1", db_path=memory_db)
    ] == ["喜歡喝伯爵紅茶"]
    assert [
        item.fact for item in search_facts("陽明山", user_id="u1", db_path=memory_db)
    ] == ["週末去陽明山爬山"]


def test_backfill_cli_rebuilds_and_dry_run(memory_db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools import backfill_fts

    _add_profile(memory_db, "u1")
    _add_fact(memory_db, "u1", "喜歡喝伯爵紅茶")

    with db_transaction(memory_db) as cur:
        cur.execute("INSERT INTO facts_fts(facts_fts) VALUES('delete-all')")

    assert backfill_fts.main(["--db", str(memory_db), "--dry-run"]) == 0
    assert search_facts("伯爵紅茶", user_id="u1", db_path=memory_db) == []

    assert backfill_fts.main(["--db", str(memory_db)]) == 0
    assert "重建" in capsys.readouterr().out
    assert [
        item.fact for item in search_facts("伯爵紅茶", user_id="u1", db_path=memory_db)
    ] == ["喜歡喝伯爵紅茶"]

    assert backfill_fts.main(["--db", str(memory_db.parent / "missing.db")]) == 2
    assert "找不到" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# 6. Async 與評測集
# ---------------------------------------------------------------------------


def test_asearch_facts_runs_off_event_loop(memory_db: Path) -> None:
    _add_profile(memory_db, "u1")
    _add_fact(memory_db, "u1", "喜歡喝紅茶")

    results = asyncio.run(
        asearch_facts("紅茶", user_id="u1", db_path=memory_db, now=_NOW)
    )
    assert [item.fact for item in results] == ["喜歡喝紅茶"]


def test_retrieval_eval_dataset_v1_passes() -> None:
    from tests.evaluation.memory_retrieval_eval import run_evaluation

    report = run_evaluation()
    assert report.total_cases >= 10
    assert report.passed, report.as_dict()
    assert report.keyword_recall == pytest.approx(1.0)
    assert report.deleted_recall == 0.0
    assert report.false_positive_rate == 0.0
    assert report.p95_latency_ms < 300.0
