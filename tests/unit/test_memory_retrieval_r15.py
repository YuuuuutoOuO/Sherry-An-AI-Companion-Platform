"""
tests/unit/test_memory_retrieval_r15.py
=======================================
R15：長期記憶檢索 Part 2（向量化、融合排序與 Prompt 動態尾段）回歸測試。

涵蓋：
1. Ollama embeddings client 的 timeout／重試與失敗傳播。
2. ``EmbeddingService`` 佇列、批次寫入、查詢快取與失敗後重試。
3. 向量語意召回、關鍵字＋向量融合、維度不符與 sqlite-vec 降級。
4. 事實／Profile 軟刪除對向量路徑同樣為 0 召回。
5. affinity 維度排序、關係分數讀取。
6. Prompt 動態尾段注入、重複排除與靜態前綴穩定性。
7. ``tools/backfill_embeddings.py`` CLI（dry-run／實際回填／錯誤碼）。
8. 向量清理 triggers 與評測集 v2 門檻。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import pytest

import handlers.memory.storage as mem_storage
from core.db import db_transaction
from handlers.memory import embeddings
from handlers.memory.retrieval import (
    RetrievalWeights,
    asearch_facts,
    load_affinity_scores,
    search_facts,
)
from handlers.social.dispatch.prompts import build_retrieved_memory_tail
from handlers.social.memory_builder import build_user_memory_block

_NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def memory_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔離的記憶資料庫（不觸碰真實 data/）。"""
    db_path = tmp_path / "sherry_memory.db"
    monkeypatch.setattr(mem_storage, "MEMORY_DB_PATH", db_path)
    monkeypatch.setattr(mem_storage, "_INITIALIZED_MEMORY_DBS", set())
    mem_storage.ensure_memory_db(db_path)
    embeddings.reset_for_tests()
    yield db_path
    embeddings.reset_for_tests()


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
    embedding: Sequence[float] | None = None,
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
        fact_id = int(cur.lastrowid)
    if embedding is not None:
        _store_embedding(db_path, fact_id, embedding, updated_at=updated_at)
    return fact_id


def _store_embedding(
    db_path: Path,
    fact_id: int,
    vector: Sequence[float],
    *,
    updated_at: str = "2026-10-15T00:00:00+00:00",
) -> None:
    normalized = embeddings.normalize_vector(vector)
    with db_transaction(db_path) as cur:
        cur.execute(
            "INSERT OR REPLACE INTO fact_embeddings (fact_id, model, dim, vector, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                fact_id,
                embeddings.model_name(),
                len(normalized),
                embeddings.vector_to_blob(normalized),
                updated_at,
            ),
        )


def _embedding_count(db_path: Path) -> int:
    with db_transaction(db_path) as cur:
        return int(cur.execute("SELECT COUNT(*) FROM fact_embeddings").fetchone()[0])


class FakeEmbeddingClient:
    """可控制失敗次數的 embedding client（不發出任何網路請求）。"""

    def __init__(
        self,
        *,
        vector: Sequence[float] = (1.0, 0.0, 0.0, 0.0),
        failures: int = 0,
    ) -> None:
        self.vector = tuple(float(value) for value in vector)
        self.failures = failures
        self.calls = 0
        self.closed = False

    async def embed(self, text: str) -> list[float]:
        self.calls += 1
        if self.failures > 0:
            self.failures -= 1
            raise embeddings.EmbeddingError("simulated failure")
        return list(self.vector)

    async def embed_many(
        self,
        texts: Sequence[str],
        *,
        concurrency: int | None = None,
    ) -> list[list[float] | Exception]:
        results: list[list[float] | Exception] = []
        for text in texts:
            try:
                results.append(await self.embed(text))
            except Exception as exc:  # noqa: BLE001 - 模擬批次個別失敗
                results.append(exc)
        return results

    async def close(self) -> None:
        self.closed = True


class FlakyOllamaClient(embeddings.OllamaEmbeddingClient):
    """覆寫 HTTP 單次請求，驗證指數退避重試。"""

    def __init__(self, failures: int, **kwargs: object) -> None:
        kwargs.setdefault("max_retries", 2)
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.failures = failures
        self.calls = 0

    async def _request_once(self, text: str) -> list[float]:
        self.calls += 1
        if self.failures > 0:
            self.failures -= 1
            raise embeddings.EmbeddingError("temporary")
        return [0.5, 0.5]


# ---------------------------------------------------------------------------
# 1. Ollama client 重試
# ---------------------------------------------------------------------------
def test_ollama_client_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FlakyOllamaClient(failures=2)

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(embeddings.asyncio, "sleep", _no_sleep)

    vector = asyncio.run(client.embed("紅茶"))
    assert vector == [0.5, 0.5]
    assert client.calls == 3


def test_ollama_client_raises_after_retries_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FlakyOllamaClient(failures=10)

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(embeddings.asyncio, "sleep", _no_sleep)

    with pytest.raises(embeddings.EmbeddingError):
        asyncio.run(client.embed("紅茶"))
    assert client.calls == 3


def test_ollama_client_rejects_blank_text() -> None:
    with pytest.raises(embeddings.EmbeddingError):
        asyncio.run(embeddings.OllamaEmbeddingClient().embed("   "))


# ---------------------------------------------------------------------------
# 2. EmbeddingService 佇列與查詢快取
# ---------------------------------------------------------------------------
def test_embedding_service_queue_batches_and_stores(memory_db: Path) -> None:
    _add_profile(memory_db, "u1")
    first = _add_fact(memory_db, "u1", "喜歡喝紅茶")
    second = _add_fact(memory_db, "u1", "週末去爬山")
    client = FakeEmbeddingClient(vector=(3.0, 4.0))

    async def _run() -> None:
        service = embeddings.EmbeddingService(memory_db, client=client, batch_size=2)  # type: ignore[arg-type]
        try:
            assert await service.enqueue([first, second]) == 2
            await service.wait_idle(timeout=5)
        finally:
            await service.close()

    asyncio.run(_run())

    stored = embeddings.load_embeddings([first, second], memory_db)
    assert set(stored) == {first, second}
    assert stored[first] == pytest.approx((0.6, 0.8), abs=1e-6)
    assert embeddings.count_embeddings(memory_db) == 2
    assert client.closed is True


def test_embedding_service_retries_failed_items_on_next_refresh(
    memory_db: Path,
) -> None:
    _add_profile(memory_db, "u1")
    fact_id = _add_fact(memory_db, "u1", "喜歡喝紅茶")
    client = FakeEmbeddingClient(failures=1)

    async def _run() -> None:
        service = embeddings.EmbeddingService(memory_db, client=client)  # type: ignore[arg-type]
        try:
            assert await service.enqueue_missing() == 1
            await service.wait_idle(timeout=5)
            assert embeddings.count_embeddings(memory_db) == 0
            assert embeddings.missing_fact_ids(memory_db) == [fact_id]

            # 下一次刷新（例如每分鐘的 _flush_loop）會再次嘗試缺失項目。
            assert await service.enqueue_missing() == 1
            await service.wait_idle(timeout=5)
        finally:
            await service.close()

    asyncio.run(_run())
    assert embeddings.count_embeddings(memory_db) == 1


def test_embedding_service_query_cache_and_normalization(memory_db: Path) -> None:
    _add_profile(memory_db, "u1")
    _add_fact(memory_db, "u1", "喜歡喝紅茶", embedding=(1.0, 0.0, 0.0, 0.0))
    client = FakeEmbeddingClient(vector=(0.0, 5.0, 0.0, 0.0))

    async def _run() -> None:
        service = embeddings.EmbeddingService(memory_db, client=client)  # type: ignore[arg-type]
        try:
            first = await service.embed_query("想喝飲料")
            second = await service.embed_query("想喝飲料")
        finally:
            await service.close()
        assert first == pytest.approx((0.0, 1.0, 0.0, 0.0))
        assert first == second
        assert client.calls == 1
        assert embeddings.get_cached_query_embedding("想喝飲料") == first
        assert embeddings.get_cached_query_embedding("   ") is None

    asyncio.run(_run())


def test_embedding_service_skips_when_no_vectors_exist(memory_db: Path) -> None:
    _add_profile(memory_db, "u1")
    _add_fact(memory_db, "u1", "喜歡喝紅茶")
    client = FakeEmbeddingClient()

    async def _run() -> None:
        service = embeddings.EmbeddingService(memory_db, client=client)  # type: ignore[arg-type]
        try:
            assert await service.embed_query("紅茶") is None
        finally:
            await service.close()

    asyncio.run(_run())
    assert client.calls == 0


# ---------------------------------------------------------------------------
# 3. 向量召回與融合
# ---------------------------------------------------------------------------
def test_vector_semantic_recall_without_keyword_overlap(memory_db: Path) -> None:
    _add_profile(memory_db, "u1")
    tea = _add_fact(
        memory_db,
        "u1",
        "喜歡喝紅茶，尤其是伯爵紅茶",
        category="preference",
        importance=4,
        embedding=(1.0, 0.0, 0.0, 0.0),
    )
    _add_fact(
        memory_db,
        "u1",
        "週末去爬山",
        category="habit",
        embedding=(0.0, 1.0, 0.0, 0.0),
    )

    results = search_facts(
        "想喝點熱的飲料",
        user_id="u1",
        db_path=memory_db,
        now=_NOW,
        query_vector=(0.95, 0.05, 0.0, 0.0),
        min_cos_sim=0.25,
    )
    assert [item.fact_id for item in results] == [tea]
    assert results[0].matched_by == "vector"
    assert results[0].cos_sim > 0.9


def test_fusion_combines_keyword_and_vector_candidates(memory_db: Path) -> None:
    _add_profile(memory_db, "u1")
    tea = _add_fact(
        memory_db,
        "u1",
        "喜歡喝紅茶，尤其是伯爵紅茶",
        category="preference",
        importance=4,
        embedding=(1.0, 0.0, 0.0, 0.0),
    )
    hike = _add_fact(
        memory_db,
        "u1",
        "週末去爬山",
        category="habit",
        embedding=(0.0, 1.0, 0.0, 0.0),
    )

    weights = RetrievalWeights(
        bm25=0.5, cos_sim=0.5, recency=0.0, affinity=0.0,
        importance=0.0, source_priority=0.0,
    )
    results = search_facts(
        "喝茶 爬山",
        user_id="u1",
        db_path=memory_db,
        now=_NOW,
        weights=weights,
        query_vector=(0.7, 0.7, 0.0, 0.0),
    )
    by_id = {item.fact_id: item for item in results}
    assert set(by_id) == {tea, hike}
    # 爬山由 LIKE 命中、紅茶由語意候選補回。
    assert by_id[hike].matched_by == "like"
    assert by_id[tea].matched_by == "vector"


def test_deleted_facts_and_profiles_never_returned_via_vectors(memory_db: Path) -> None:
    _add_profile(memory_db, "u_alive")
    _add_profile(memory_db, "u_gone", deleted=True)
    deleted_fact = _add_fact(
        memory_db,
        "u_alive",
        "舊地址是台北市信義區松高路 77 號",
        deleted=True,
        embedding=(1.0, 0.0, 0.0, 0.0),
    )
    deleted_profile_fact = _add_fact(
        memory_db,
        "u_gone",
        "住在高雄",
        embedding=(1.0, 0.0, 0.0, 0.0),
    )
    alive = _add_fact(
        memory_db,
        "u_alive",
        "目前住在台北市大安區",
        embedding=(1.0, 0.0, 0.0, 0.0),
    )

    results = search_facts(
        "地址",
        user_id="u_alive",
        db_path=memory_db,
        now=_NOW,
        query_vector=(1.0, 0.0, 0.0, 0.0),
    )
    assert [item.fact_id for item in results] == [alive]
    assert deleted_fact not in {item.fact_id for item in results}
    assert deleted_profile_fact not in {item.fact_id for item in results}


def test_mismatched_vector_dimension_is_ignored(memory_db: Path) -> None:
    _add_profile(memory_db, "u1")
    _add_fact(memory_db, "u1", "喜歡喝紅茶", embedding=(1.0, 0.0, 0.0))

    results = search_facts(
        "想喝飲料",
        user_id="u1",
        db_path=memory_db,
        now=_NOW,
        query_vector=(1.0, 0.0),
    )
    assert results == []
    assert embeddings.cosine_similarity((1.0, 0.0), (1.0, 0.0, 0.0)) == 0.0


def test_python_cosine_backend_when_sqlite_vec_missing(
    memory_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib.util

    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: None)
    assert embeddings.sqlite_vec_available() is False
    assert embeddings.vector_backend() == "python-cosine"

    _add_profile(memory_db, "u1")
    fact_id = _add_fact(
        memory_db, "u1", "喜歡喝紅茶", embedding=(1.0, 0.0, 0.0, 0.0)
    )
    results = search_facts(
        "想喝飲料",
        user_id="u1",
        db_path=memory_db,
        now=_NOW,
        query_vector=(1.0, 0.0, 0.0, 0.0),
    )
    assert [item.fact_id for item in results] == [fact_id]


# ---------------------------------------------------------------------------
# 4. affinity 排序與關係讀取
# ---------------------------------------------------------------------------
def test_affinity_weight_ranks_closer_user_first(memory_db: Path) -> None:
    _add_profile(memory_db, "u_close")
    _add_profile(memory_db, "u_far")
    _add_fact(memory_db, "u_close", "喜歡喝紅茶", embedding=(1.0, 0.0))
    _add_fact(memory_db, "u_far", "喜歡喝紅茶", embedding=(1.0, 0.0))

    weights = RetrievalWeights(
        bm25=0.0, cos_sim=0.0, recency=0.0, affinity=1.0,
        importance=0.0, source_priority=0.0,
    )
    results = search_facts(
        "紅茶",
        db_path=memory_db,
        now=_NOW,
        weights=weights,
        affinity_by_user={"u_close": 0.9, "u_far": 0.1},
        query_vector=(1.0, 0.0),
    )
    assert [item.user_id for item in results] == ["u_close", "u_far"]
    assert results[0].affinity == pytest.approx(0.9)


def test_load_affinity_scores_excludes_deleted_relations(memory_db: Path) -> None:
    with db_transaction(memory_db) as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS user_relations ("
            "user_id TEXT PRIMARY KEY, affinity REAL NOT NULL DEFAULT 0, "
            "is_deleted INTEGER NOT NULL DEFAULT 0)"
        )
        cur.execute(
            "INSERT INTO user_relations (user_id, affinity, is_deleted) VALUES (?, ?, ?)",
            ("u1", 80.0, 0),
        )
        cur.execute(
            "INSERT INTO user_relations (user_id, affinity, is_deleted) VALUES (?, ?, ?)",
            ("u2", 100.0, 1),
        )

    scores = load_affinity_scores(memory_db)
    assert scores == {"u1": pytest.approx(0.8)}


# ---------------------------------------------------------------------------
# 5. Prompt 動態尾段
# ---------------------------------------------------------------------------
def test_retrieved_memory_tail_formats_and_deduplicates(memory_db: Path) -> None:
    _add_profile(memory_db, "1")
    _add_fact(
        memory_db,
        "1",
        "喜歡喝紅茶，尤其是伯爵紅茶",
        category="preference",
    )
    _add_fact(memory_db, "1", "週末去爬山", category="habit")

    tail = build_retrieved_memory_tail(
        "爬山 紅茶", 1, exclude_text="喜歡喝紅茶，尤其是伯爵紅茶"
    )
    assert tail.startswith("【🔎 依當前話題即時檢索的相關長期記憶】")
    assert "週末去爬山" in tail
    assert "喜歡喝紅茶" not in tail
    assert tail.count("週末去爬山") == 1

    assert build_retrieved_memory_tail("", 1) == ""
    assert build_retrieved_memory_tail("爬山", 0) == ""
    assert build_retrieved_memory_tail("爬山", 1, limit=1).count("- ") == 1


def test_memory_block_appends_tail_without_touching_prefix(
    memory_db: Path,
) -> None:
    from unittest.mock import MagicMock

    _add_profile(memory_db, "1")
    _add_fact(memory_db, "1", "週末去爬山", category="habit")
    _add_fact(memory_db, "1", "喜歡喝紅茶", category="preference")

    bot = MagicMock()
    mem_mgr = MagicMock()
    mem_mgr._store = {}
    mem_mgr.get_user_memory_snippet = MagicMock(return_value="📌 喜歡喝紅茶")
    bot.get_cog.return_value = mem_mgr

    first = build_user_memory_block(
        bot, user_id=1, display_name="小明", query_text="爬山"
    )
    second = build_user_memory_block(
        bot, user_id=1, display_name="小明", query_text="爬山 紅茶"
    )

    marker = "【🔎 依當前話題即時檢索的相關長期記憶】"
    assert marker in first and marker in second
    # 動態尾段只會接在既有內容尾端；切換查詢不會改動前面的記憶片段。
    first_prefix = first[: first.index(marker)]
    assert second.startswith(first_prefix)
    assert "週末去爬山" in first


def test_build_retrieved_memory_tail_empty_when_all_in_snippet(
    memory_db: Path,
) -> None:
    _add_profile(memory_db, "1")
    _add_fact(memory_db, "1", "喜歡喝紅茶", category="preference")

    assert build_retrieved_memory_tail("紅茶", 1, exclude_text="喜歡喝紅茶") == ""


# ---------------------------------------------------------------------------
# 6. 向量清理 triggers
# ---------------------------------------------------------------------------
def test_embedding_cleanup_on_update_and_delete(memory_db: Path) -> None:
    _add_profile(memory_db, "u1")
    fact_id = _add_fact(
        memory_db, "u1", "喜歡喝伯爵紅茶", embedding=(1.0, 0.0)
    )
    assert _embedding_count(memory_db) == 1

    with db_transaction(memory_db) as cur:
        cur.execute(
            "UPDATE user_facts SET fact = ? WHERE id = ?",
            ("喜歡喝抹茶拿鐵", fact_id),
        )
    assert _embedding_count(memory_db) == 0

    _store_embedding(memory_db, fact_id, (1.0, 0.0))
    assert _embedding_count(memory_db) == 1
    with db_transaction(memory_db) as cur:
        cur.execute("DELETE FROM user_facts WHERE id = ?", (fact_id,))
    assert _embedding_count(memory_db) == 0


# ---------------------------------------------------------------------------
# 7. async 檢索與 backfill CLI
# ---------------------------------------------------------------------------
def test_asearch_uses_query_vector_and_degrades_to_keywords(
    memory_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _add_profile(memory_db, "u1")
    _add_fact(
        memory_db, "u1", "喜歡喝紅茶", embedding=(1.0, 0.0, 0.0, 0.0)
    )
    monkeypatch.setattr(
        embeddings, "schedule_embedding_refresh", lambda **_kwargs: False
    )

    async def _query_vector(_text: str, **_kwargs: object) -> tuple[float, ...]:
        return (1.0, 0.0, 0.0, 0.0)

    monkeypatch.setattr(embeddings, "embed_query", _query_vector)
    semantic = asyncio.run(
        asearch_facts("想喝點熱的飲料", user_id="u1", db_path=memory_db, now=_NOW)
    )
    assert [item.fact for item in semantic] == ["喜歡喝紅茶"]
    assert semantic[0].matched_by == "vector"

    async def _no_vector(_text: str, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(embeddings, "embed_query", _no_vector)
    keyword = asyncio.run(
        asearch_facts("紅茶", user_id="u1", db_path=memory_db, now=_NOW)
    )
    assert [item.fact for item in keyword] == ["喜歡喝紅茶"]


def test_backfill_embeddings_cli_dry_run_and_backfill(
    memory_db: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tools import backfill_embeddings

    _add_profile(memory_db, "u1")
    _add_fact(memory_db, "u1", "喜歡喝紅茶")
    _add_fact(memory_db, "u1", "週末去爬山")

    assert backfill_embeddings.main(["--db", str(memory_db), "--dry-run"]) == 0
    dry_output = capsys.readouterr().out
    assert "待補 2 筆" in dry_output
    assert "--dry-run" in dry_output
    assert embeddings.count_embeddings(memory_db) == 0

    real_service_cls = embeddings.EmbeddingService

    def _fake_service(db_path: Path, *, batch_size: int | None = None) -> embeddings.EmbeddingService:
        return real_service_cls(
            db_path,
            client=FakeEmbeddingClient(),  # type: ignore[arg-type]
            batch_size=batch_size,
        )

    monkeypatch.setattr(embeddings, "EmbeddingService", _fake_service)
    assert backfill_embeddings.main(["--db", str(memory_db), "--timeout", "5"]) == 0
    assert "已完成 2 筆" in capsys.readouterr().out
    assert embeddings.count_embeddings(memory_db) == 2

    missing = memory_db.parent / "missing.db"
    assert backfill_embeddings.main(["--db", str(missing)]) == 2
    assert "找不到" in capsys.readouterr().out


def test_backfill_embeddings_cli_disabled(
    memory_db: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tools import backfill_embeddings

    _add_profile(memory_db, "u1")
    _add_fact(memory_db, "u1", "喜歡喝紅茶")
    monkeypatch.setattr(
        embeddings, "is_enabled", lambda: False
    )

    assert backfill_embeddings.main(["--db", str(memory_db)]) == 3
    assert "已停用" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# 8. 評測集 v2
# ---------------------------------------------------------------------------
def test_retrieval_eval_dataset_v2_passes() -> None:
    from tests.evaluation.memory_retrieval_eval import run_evaluation

    dataset = Path(__file__).resolve().parent.parent / "evaluation" / "memory_retrieval_v2.json"
    report = run_evaluation(dataset_path=dataset)
    assert report.total_cases >= 8
    assert report.passed, report.as_dict()
    assert report.keyword_recall == pytest.approx(1.0)
    assert report.false_positive_rate == 0.0
    assert report.deleted_recall == 0.0
    assert report.p95_latency_ms < 300.0
