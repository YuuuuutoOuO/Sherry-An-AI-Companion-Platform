"""handlers/memory/retrieval.py
================================
長期記憶檢索 API（R14：FTS5 基礎）。

本模組提供「關鍵字召回 + 權重骨架」的唯讀檢索層：

1. ``facts_fts``（FTS5 外部內容表）負責 BM25 關鍵字召回；短詞（<3 字元）
   或 FTS5 不可用時，自動降級為 ``LIKE`` 子字串查詢。
2. R15：提供查詢向量時，額外掃描 ``fact_embeddings`` 產生語意候選，並以
   ``cos_sim`` 參與融合排序；未安裝 sqlite-vec 時以 Python cosine 降級。
3. 一律以 ``user_facts.is_deleted = 0`` 且 ``user_profiles.is_deleted = 0``
   為硬性過濾，已軟刪資料永不召回。
4. 融合分數＝bm25 ＋ cos_sim ＋ recency ＋ affinity ＋ importance ＋
   source_priority，權重自動正規化。

所有函式皆為同步 SQLite 讀取；在 async 路徑請使用 ``asearch_facts``。
"""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

import settings
from core.db import get_db_connection
from handlers.memory import embeddings, storage

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(
    r"[0-9A-Za-z_]+"
    r"|[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]+"
)
_MIN_FTS_TERM_CHARS = 3
_DEFAULT_LIMIT = 8
_MAX_LIMIT = 50
_MAX_CANDIDATES = 200
_LIKE_ONLY_BM25 = 0.45

_SOURCE_PRIORITY: dict[str, float] = {
    "rule": 1.0,
    "identity": 0.8,
    "preference": 0.6,
    "habit": 0.6,
    "social": 0.5,
}


@dataclass(frozen=True)
class RetrievalWeights:
    """檢索融合權重；權重會自動正規化為總和 1。"""

    bm25: float
    cos_sim: float
    recency: float
    affinity: float
    importance: float
    source_priority: float

    @classmethod
    def from_settings(cls) -> "RetrievalWeights":
        """由 ``settings`` 建立權重（支援 WebUI 熱改後重讀）。"""
        return cls(
            bm25=float(getattr(settings, "MEMORY_RETRIEVAL_W_BM25", 0.40)),
            cos_sim=float(getattr(settings, "MEMORY_RETRIEVAL_W_COS_SIM", 0.25)),
            recency=float(getattr(settings, "MEMORY_RETRIEVAL_W_RECENCY", 0.10)),
            affinity=float(getattr(settings, "MEMORY_RETRIEVAL_W_AFFINITY", 0.10)),
            importance=float(getattr(settings, "MEMORY_RETRIEVAL_W_IMPORTANCE", 0.10)),
            source_priority=float(
                getattr(settings, "MEMORY_RETRIEVAL_W_SOURCE_PRIORITY", 0.05)
            ),
        )

    def normalized(self) -> "RetrievalWeights":
        """將所有權重除以總和，確保最終分數落在 0–1。"""
        total = (
            self.bm25
            + self.cos_sim
            + self.recency
            + self.affinity
            + self.importance
            + self.source_priority
        )
        if total <= 0:
            return RetrievalWeights(1.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        return RetrievalWeights(
            bm25=self.bm25 / total,
            cos_sim=self.cos_sim / total,
            recency=self.recency / total,
            affinity=self.affinity / total,
            importance=self.importance / total,
            source_priority=self.source_priority / total,
        )


@dataclass(frozen=True)
class RetrievedFact:
    """單筆檢索結果（含各維度分數供觀測與 R15 除錯）。"""

    fact_id: int
    user_id: str
    fact: str
    category: str
    importance: int
    updated_at: str
    bm25_norm: float
    cos_sim: float
    recency: float
    affinity: float
    source_priority: float
    score: float
    matched_by: str


def _extract_terms(query: str, max_terms: int) -> list[str]:
    """從查詢字串萃取去重後的關鍵字（CJK 連續字串視為一個詞）。"""
    terms: list[str] = []
    seen: set[str] = set()
    for match in _TOKEN_RE.finditer(query or ""):
        term = match.group(0)
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        terms.append(term)
        if len(terms) >= max_terms:
            break
    return terms


def _fts_match_query(terms: list[str]) -> str:
    """組裝安全的 FTS5 MATCH 字串（每個詞加引號並以 OR 串接）。"""
    return " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)


def _like_pattern(term: str) -> str:
    """轉義 LIKE 萬用字元並回傳包含式 pattern。"""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _vector_candidates(
    conn: sqlite3.Connection,
    user_id: str | int | None,
    query_vector: Sequence[float] | None,
    limit: int,
    min_cos_sim: float,
) -> dict[int, float]:
    """以 Python cosine 掃描既有事實向量，回傳相似度達門檻的候選。

    ``sqlite-vec`` 未安裝時即為此路徑（純 Python 降級）；由於單一使用者
    事實數有上限（預設 25 筆），全量掃描成本極低。
    """
    if not query_vector:
        return {}

    sql = (
        "SELECT e.fact_id, e.dim, e.vector FROM fact_embeddings e "
        "JOIN user_facts f ON f.id = e.fact_id "
        "JOIN user_profiles p ON p.user_id = f.user_id "
        "WHERE f.is_deleted = 0 AND p.is_deleted = 0"
    )
    params: list[object] = []
    if user_id is not None:
        sql += " AND f.user_id = ?"
        params.append(str(user_id))
    sql += " ORDER BY f.importance DESC, e.fact_id ASC"

    scored: list[tuple[float, int]] = []
    query_size = len(query_vector)
    for row in conn.execute(sql, params).fetchall():
        if int(row["dim"]) != query_size:
            # 忽略維度不符的舊向量（例如已切換 embedding 模型）。
            continue
        vector = embeddings.blob_to_vector(row["vector"], int(row["dim"]))
        similarity = embeddings.cosine_similarity(query_vector, vector)
        if similarity >= min_cos_sim:
            scored.append((similarity, int(row["fact_id"])))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return {fact_id: similarity for similarity, fact_id in scored[:limit]}


def _collect_candidates(
    conn: sqlite3.Connection,
    user_id: str | int | None,
    fts_terms: list[str],
    like_terms: list[str],
    limit: int,
    query_vector: Sequence[float] | None = None,
    min_cos_sim: float = 0.25,
) -> tuple[dict[int, float], list[int], dict[int, float]]:
    """收集 FTS、LIKE 與向量三路候選 id。

    FTS5 的 ``bm25()`` 僅能在直接掃描 FTS 表的查詢區塊使用（不可再被外層
    聚合／CTE 展平），因此候選 id 與分數於此分開查詢，再於 Python 合併。
    """
    fts_scores: dict[int, float] = {}

    if fts_terms:
        cursor = conn.execute(
            "SELECT rowid AS fact_id, bm25(facts_fts) AS bm25_raw "
            "FROM facts_fts WHERE facts_fts MATCH ? "
            "ORDER BY bm25(facts_fts) ASC, rowid ASC LIMIT ?",
            (_fts_match_query(fts_terms), limit),
        )
        for row in cursor.fetchall():
            fact_id = int(row["fact_id"])
            raw = row["bm25_raw"]
            value = float(raw) if raw is not None else 0.0
            current = fts_scores.get(fact_id)
            if current is None or value < current:
                fts_scores[fact_id] = value

    like_ids: list[int] = []
    if like_terms:
        conditions = " OR ".join(["fact LIKE ? ESCAPE '\\'"] * len(like_terms))
        params: list[object] = [_like_pattern(term) for term in like_terms]
        params.append(limit)
        cursor = conn.execute(
            "SELECT id FROM user_facts WHERE (" + conditions + ") "
            "ORDER BY importance DESC, updated_at DESC, id ASC LIMIT ?",
            params,
        )
        like_ids = [int(row["id"]) for row in cursor.fetchall()]

    vector_scores = _vector_candidates(
        conn, user_id, query_vector, limit, min_cos_sim
    )
    return fts_scores, like_ids, vector_scores


def _fetch_fact_rows(
    conn: sqlite3.Connection,
    user_id: str | int | None,
    fact_ids: list[int],
) -> list[sqlite3.Row]:
    """依 id 取回事實列，並硬性過濾事實／Profile 軟刪（分批避開變數上限）。"""
    unique_ids = list(dict.fromkeys(fact_ids))
    if not unique_ids:
        return []

    rows: list[sqlite3.Row] = []
    for start in range(0, len(unique_ids), 500):
        chunk = unique_ids[start:start + 500]
        placeholders = ",".join("?" for _ in chunk)
        params: list[object] = list(chunk)
        sql = (
            "SELECT f.id, f.user_id, f.fact, f.category, f.importance, f.updated_at "
            "FROM user_facts f "
            "JOIN user_profiles p ON p.user_id = f.user_id "
            f"WHERE f.id IN ({placeholders}) AND f.is_deleted = 0 AND p.is_deleted = 0"
        )
        if user_id is not None:
            sql += " AND f.user_id = ?"
            params.append(str(user_id))
        rows.extend(conn.execute(sql, params).fetchall())
    return rows


def _run_search(
    db_path: Path,
    user_id: str | int | None,
    fts_terms: list[str],
    like_terms: list[str],
    limit: int,
    query_vector: Sequence[float] | None = None,
    min_cos_sim: float = 0.25,
) -> list[dict[str, object]]:
    """執行一次 FTS／LIKE／向量混合召回，回傳帶分數的中介列（dict）。"""
    conn = get_db_connection(db_path)
    try:
        fts_scores, like_ids, vector_scores = _collect_candidates(
            conn,
            user_id,
            fts_terms,
            like_terms,
            limit,
            query_vector,
            min_cos_sim,
        )
        ordered_ids = list(fts_scores.keys())
        ordered_ids.extend(
            fact_id for fact_id in like_ids if fact_id not in fts_scores
        )
        ordered_ids.extend(
            fact_id
            for fact_id in vector_scores
            if fact_id not in fts_scores and fact_id not in like_ids
        )
        raw_rows = _fetch_fact_rows(conn, user_id, ordered_ids)
    finally:
        conn.close()

    rows_by_id = {int(row["id"]): row for row in raw_rows}
    results: list[dict[str, object]] = []
    for fact_id in ordered_ids:
        row = rows_by_id.get(fact_id)
        if row is None:
            continue
        if fact_id in fts_scores:
            matched_by = "fts"
        elif fact_id in like_ids:
            matched_by = "like"
        else:
            matched_by = "vector"
        results.append(
            {
                "id": int(row["id"]),
                "user_id": str(row["user_id"]),
                "fact": str(row["fact"]),
                "category": str(row["category"]),
                "importance": int(row["importance"]),
                "updated_at": str(row["updated_at"]),
                "bm25_raw": fts_scores.get(fact_id),
                "cos_sim": vector_scores.get(fact_id, 0.0),
                "matched_by": matched_by,
            }
        )
    return results


def _parse_updated_at(value: str) -> datetime | None:
    """寬鬆解析 SQLite/ISO 時間字串，統一回傳 UTC aware datetime。"""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _recency_score(updated_at: str, now: datetime, half_life_days: float) -> float:
    """以半衰期計算時效分數（0–1）。"""
    parsed = _parse_updated_at(updated_at)
    if parsed is None:
        return 0.5
    half_life = max(0.1, half_life_days)
    age_days = max(0.0, (now - parsed).total_seconds() / 86400.0)
    return float(0.5 ** (age_days / half_life))


def _importance_score(importance: int) -> float:
    """將 1–5 的重要性線性映射到 0–1。"""
    return max(0.0, min(1.0, (int(importance) - 1) / 4.0))


def _source_priority(category: str) -> float:
    """依事實類別給予來源優先序（規則類最高）。"""
    return _SOURCE_PRIORITY.get(str(category).strip().lower(), 0.4)


def _bm25_normalize(rows: list[Mapping[str, object]]) -> dict[int, float]:
    """將 FTS5 bm25（越小越好、通常為負）正規化到 0–1。"""
    raw_values = [float(r["bm25_raw"]) for r in rows if r["bm25_raw"] is not None]
    max_relevance = max((max(0.0, -value) for value in raw_values), default=0.0)

    normalized: dict[int, float] = {}
    for row in rows:
        fact_id = int(row["id"])
        raw = row["bm25_raw"]
        if raw is None:
            normalized[fact_id] = (
                0.0 if row.get("matched_by") == "vector" else _LIKE_ONLY_BM25
            )
        elif max_relevance > 0:
            normalized[fact_id] = max(0.0, -float(raw)) / max_relevance
        else:
            normalized[fact_id] = 1.0
    return normalized


def load_affinity_scores(db_path: Path | None = None) -> dict[str, float]:
    """讀取未軟刪使用者的關係親和度並正規化到 0–1（0–100 → 0–1）。"""
    target = Path(db_path) if db_path is not None else storage.MEMORY_DB_PATH
    storage.ensure_memory_db(target)
    conn = get_db_connection(target)
    try:
        rows = conn.execute(
            "SELECT user_id, affinity FROM user_relations WHERE is_deleted = 0"
        ).fetchall()
    except sqlite3.OperationalError:
        # 尚未建立 user_relations（例如只跑記憶檢索的環境）。
        return {}
    finally:
        conn.close()
    return {
        str(row["user_id"]): max(0.0, min(1.0, float(row["affinity"]) / 100.0))
        for row in rows
    }


def search_facts(
    query: str,
    *,
    user_id: str | int | None = None,
    limit: int = _DEFAULT_LIMIT,
    db_path: Path | None = None,
    now: datetime | None = None,
    weights: RetrievalWeights | None = None,
    affinity_by_user: Mapping[str, float] | None = None,
    query_vector: Sequence[float] | None = None,
    min_cos_sim: float | None = None,
) -> list[RetrievedFact]:
    """以關鍵字＋向量檢索長期記憶事實。

    Args:
        query: 使用者查詢文字；空白時回傳空清單。
        user_id: 限定單一使用者；``None`` 表示跨使用者檢索。
        limit: 回傳數量上限（1–50）。
        db_path: 記憶資料庫路徑；``None`` 時使用 ``storage.MEMORY_DB_PATH``。
        now: 計算時效分數的基準時間（測試可注入）。
        weights: 融合權重；``None`` 時讀取 settings。
        affinity_by_user: 使用者親和度分數（0–1）；``None`` 時讀取
            ``user_relations``（已軟刪者排除）。
        query_vector: 查詢向量；提供時加入語意候選並啟用 ``cos_sim`` 維度。
        min_cos_sim: 語意候選最低 cosine 門檻；``None`` 時讀取 settings。

    Returns:
        list[RetrievedFact]: 依融合分數由高到低排序的結果。
    """
    clean_query = (query or "").strip()
    if not clean_query:
        return []

    safe_limit = max(1, min(int(limit), _MAX_LIMIT))
    max_terms = max(1, int(getattr(settings, "MEMORY_RETRIEVAL_MAX_TERMS", 8)))
    terms = _extract_terms(clean_query, max_terms)
    if not terms and not query_vector:
        return []

    fts_terms = [term for term in terms if len(term) >= _MIN_FTS_TERM_CHARS]
    like_terms = [term for term in terms if len(term) < _MIN_FTS_TERM_CHARS]

    target = Path(db_path) if db_path is not None else storage.MEMORY_DB_PATH
    storage.ensure_memory_db(target)

    half_life = float(
        getattr(settings, "MEMORY_RETRIEVAL_RECENCY_HALFLIFE_DAYS", 30.0)
    )
    reference_time = now or datetime.now(timezone.utc)
    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=timezone.utc)
    reference_time = reference_time.astimezone(timezone.utc)

    normalized_weights = (weights or RetrievalWeights.from_settings()).normalized()
    candidate_limit = min(_MAX_CANDIDATES, max(safe_limit * 4, 40))
    similarity_floor = (
        float(getattr(settings, "MEMORY_EMBEDDING_MIN_SIMILARITY", 0.25))
        if min_cos_sim is None
        else float(min_cos_sim)
    )
    affinity_scores = (
        load_affinity_scores(target)
        if affinity_by_user is None
        else affinity_by_user
    )

    try:
        rows = _run_search(
            target,
            user_id,
            fts_terms,
            like_terms,
            candidate_limit,
            query_vector,
            similarity_floor,
        )
    except sqlite3.OperationalError as exc:
        # FTS5 不可用、索引損毀或查詢語法異常 → 全部詞改用 LIKE，確保仍可召回。
        logger.debug("[Retrieval] FTS 查詢失敗，降級 LIKE: %s", exc)
        rows = _run_search(
            target,
            user_id,
            [],
            terms,
            candidate_limit,
            query_vector,
            similarity_floor,
        )

    if not rows:
        return []

    bm25_scores = _bm25_normalize(rows)
    results: list[RetrievedFact] = []

    for row in rows:
        fact_id = int(row["id"])
        uid = str(row["user_id"])
        category = str(row["category"])
        importance = int(row["importance"])
        affinity = max(
            0.0,
            min(1.0, float(affinity_scores.get(uid, 0.0))),
        )
        bm25_norm = bm25_scores.get(fact_id, 0.0)
        recency = _recency_score(str(row["updated_at"]), reference_time, half_life)
        importance_norm = _importance_score(importance)
        source = _source_priority(category)
        cos_sim = max(0.0, min(1.0, float(row.get("cos_sim", 0.0))))

        score = (
            normalized_weights.bm25 * bm25_norm
            + normalized_weights.cos_sim * cos_sim
            + normalized_weights.recency * recency
            + normalized_weights.affinity * affinity
            + normalized_weights.importance * importance_norm
            + normalized_weights.source_priority * source
        )

        results.append(
            RetrievedFact(
                fact_id=fact_id,
                user_id=uid,
                fact=str(row["fact"]),
                category=category,
                importance=importance,
                updated_at=str(row["updated_at"]),
                bm25_norm=round(bm25_norm, 6),
                cos_sim=cos_sim,
                recency=round(recency, 6),
                affinity=round(affinity, 6),
                source_priority=round(source, 6),
                score=round(score, 6),
                matched_by=str(row["matched_by"]),
            )
        )

    results.sort(key=lambda item: (-item.score, -item.importance, item.fact_id))
    return results[:safe_limit]


def search_facts_for_user(
    user_id: str | int,
    query: str,
    *,
    limit: int = _DEFAULT_LIMIT,
    db_path: Path | None = None,
    now: datetime | None = None,
    weights: RetrievalWeights | None = None,
    affinity_by_user: Mapping[str, float] | None = None,
    query_vector: Sequence[float] | None = None,
    min_cos_sim: float | None = None,
) -> list[RetrievedFact]:
    """``search_facts`` 的單一使用者便利包裝。"""
    return search_facts(
        query,
        user_id=user_id,
        limit=limit,
        db_path=db_path,
        now=now,
        weights=weights,
        affinity_by_user=affinity_by_user,
        query_vector=query_vector,
        min_cos_sim=min_cos_sim,
    )


async def asearch_facts(
    query: str,
    **kwargs: object,
) -> list[RetrievedFact]:
    """在專用工作執行緒執行混合檢索；必要時先取得查詢向量。

    向量服務失敗或尚無回填向量時自動降級為純關鍵字檢索，不影響回應。
    """
    if kwargs.get("query_vector") is None:
        raw_db_path = kwargs.get("db_path")
        try:
            vector = await embeddings.embed_query(
                query,
                db_path=raw_db_path if isinstance(raw_db_path, (str, Path)) else None,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 向量失敗不得中斷檢索
            logger.debug("[Retrieval] 查詢向量取得失敗，降級關鍵字: %s", exc)
            vector = None
        if vector is not None:
            kwargs["query_vector"] = vector

    results = await asyncio.to_thread(search_facts, query, **kwargs)

    user_id = kwargs.get("user_id")
    if user_id is not None:
        embeddings.schedule_embedding_refresh(user_id=user_id)
    return results
