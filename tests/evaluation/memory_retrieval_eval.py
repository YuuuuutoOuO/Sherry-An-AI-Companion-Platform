"""
tests/evaluation/memory_retrieval_eval.py
=========================================
R14 長期記憶關鍵字檢索評測器（Memory Retrieval Evaluation v1）。

評測流程：
1. 讀取 ``memory_retrieval_v1.json``（事實、使用者與查詢案例）。
2. 在暫存 SQLite 建立隔離的記憶資料庫，寫入事實並由 triggers 同步 FTS5 索引。
3. 逐題呼叫 ``handlers.memory.retrieval.search_facts``，統計：
   - 關鍵字召回率（keyword_recall）
   - 誤召率（false_positive_rate）
   - 已刪資料召回率（deleted_recall，必須為 0）
   - 查詢延遲 P95（毫秒）
4. 依資料集門檻判定通過與否；可作為 CLI 或 pytest 回歸測試使用。

用法：
    python tests/evaluation/memory_retrieval_eval.py
    python tests/evaluation/memory_retrieval_eval.py --json
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import sys
import tempfile
import time
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.db import db_transaction  # noqa: E402
from handlers.memory import embeddings, retrieval, storage  # noqa: E402

DEFAULT_DATASET_PATH = Path(__file__).with_name("memory_retrieval_v1.json")
DEFAULT_LIMIT = 10

DEFAULT_THRESHOLDS: dict[str, float] = {
    "min_keyword_recall": 0.9,
    "max_false_positive_rate": 0.1,
    "max_deleted_recall": 0.0,
    "max_p95_latency_ms": 300.0,
}


@dataclass(frozen=True)
class EvalUser:
    """評測用使用者（可模擬已軟刪 Profile）。"""

    user_id: str
    profile_deleted: bool = False


@dataclass(frozen=True)
class EvalFact:
    """評測用長期記憶事實。"""

    key: str
    user_id: str
    fact: str
    category: str
    importance: int
    updated_at: str
    is_deleted: bool = False
    deleted_at: str | None = None
    embedding: tuple[float, ...] | None = None


@dataclass(frozen=True)
class EvalCase:
    """單一檢索查詢案例。"""

    id: str
    query: str
    user_id: str
    expected: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()
    expect_top1: str | None = None
    note: str = ""
    query_vector: tuple[float, ...] | None = None


@dataclass(frozen=True)
class MemoryRetrievalDataset:
    """完整評測資料集。"""

    version: int
    now: str | None
    users: tuple[EvalUser, ...]
    facts: tuple[EvalFact, ...]
    cases: tuple[EvalCase, ...]
    thresholds: dict[str, float]

    @classmethod
    def load(cls, path: Path | str = DEFAULT_DATASET_PATH) -> "MemoryRetrievalDataset":
        """從 JSON 載入並驗證評測資料集。"""
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        thresholds = dict(DEFAULT_THRESHOLDS)
        thresholds.update(
            {str(k): float(v) for k, v in payload.get("thresholds", {}).items()}
        )
        return cls(
            version=int(payload.get("version", 1)),
            now=payload.get("now"),
            users=tuple(
                EvalUser(
                    user_id=str(item["user_id"]),
                    profile_deleted=bool(item.get("profile_deleted", False)),
                )
                for item in payload.get("users", [])
            ),
            facts=tuple(
                EvalFact(
                    key=str(item["key"]),
                    user_id=str(item["user_id"]),
                    fact=str(item["fact"]),
                    category=str(item.get("category", "identity")),
                    importance=int(item.get("importance", 3)),
                    updated_at=str(item.get("updated_at", "2026-01-01T00:00:00+00:00")),
                    is_deleted=bool(item.get("is_deleted", False)),
                    deleted_at=item.get("deleted_at"),
                    embedding=(
                        tuple(float(value) for value in item["embedding"])
                        if item.get("embedding") is not None
                        else None
                    ),
                )
                for item in payload.get("facts", [])
            ),
            cases=tuple(
                EvalCase(
                    id=str(item["id"]),
                    query=str(item["query"]),
                    user_id=str(item["user_id"]),
                    expected=tuple(item.get("expected", [])),
                    forbidden=tuple(item.get("forbidden", [])),
                    expect_top1=item.get("expect_top1"),
                    note=str(item.get("note", "")),
                    query_vector=(
                        tuple(float(value) for value in item["query_vector"])
                        if item.get("query_vector") is not None
                        else None
                    ),
                )
                for item in payload.get("cases", [])
            ),
            thresholds=thresholds,
        )


@dataclass(frozen=True)
class CaseResult:
    """單題評測結果。"""

    case_id: str
    query: str
    user_id: str
    expected: tuple[str, ...]
    returned: tuple[str, ...]
    missing: tuple[str, ...]
    false_positives: tuple[str, ...]
    forbidden_hits: tuple[str, ...]
    deleted_hits: tuple[str, ...]
    top1_ok: bool
    recall: float
    latency_ms: float
    passed: bool


@dataclass
class MemoryRetrievalReport:
    """整份評測集彙總。"""

    dataset_version: int
    total_cases: int
    passed_cases: int
    keyword_recall: float
    false_positive_rate: float
    deleted_recall: float
    p95_latency_ms: float
    thresholds: dict[str, float] = field(default_factory=dict)
    results: list[CaseResult] = field(default_factory=list)
    metric_failures: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """所有逐題檢查與門檻均通過。"""
        return not self.metric_failures and self.passed_cases == self.total_cases

    def as_dict(self) -> dict[str, Any]:
        """轉為 JSON 友善結構（供 CLI 或報表使用）。"""
        return {
            "dataset_version": self.dataset_version,
            "passed": self.passed,
            "total_cases": self.total_cases,
            "passed_cases": self.passed_cases,
            "keyword_recall": round(self.keyword_recall, 4),
            "false_positive_rate": round(self.false_positive_rate, 4),
            "deleted_recall": round(self.deleted_recall, 4),
            "p95_latency_ms": round(self.p95_latency_ms, 2),
            "thresholds": self.thresholds,
            "metric_failures": self.metric_failures,
            "failures": [
                {
                    "case_id": result.case_id,
                    "query": result.query,
                    "missing": list(result.missing),
                    "false_positives": list(result.false_positives),
                    "forbidden_hits": list(result.forbidden_hits),
                    "deleted_hits": list(result.deleted_hits),
                    "top1_ok": result.top1_ok,
                }
                for result in self.results
                if not result.passed
            ],
        }


def _percentile(values: list[float], percentile: float) -> float:
    """以最近秩法計算百分位（空清單回傳 0）。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered))) - 1
    return ordered[min(rank, len(ordered) - 1)]


def prepare_database(
    dataset: MemoryRetrievalDataset,
    db_path: Path | str,
) -> tuple[dict[str, int], set[str]]:
    """建立評測資料庫並寫入事實。

    Args:
        dataset: 評測資料集。
        db_path: 目標資料庫（會先刪除既有檔案，僅供評測用）。

    Returns:
        tuple[dict[str, int], set[str]]: ``key -> fact_id`` 對照，以及已刪事實 key 集合。
    """
    target = Path(db_path)
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(target) + suffix)
        if candidate.exists():
            candidate.unlink()

    storage.ensure_memory_db(target)

    deleted_keys: set[str] = set()
    user_deleted = {user.user_id: user.profile_deleted for user in dataset.users}
    with db_transaction(target) as cur:
        for user in dataset.users:
            cur.execute(
                """
                INSERT INTO user_profiles (user_id, aliases_json, is_deleted, deleted_at, updated_at)
                VALUES (?, '[]', ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    is_deleted = excluded.is_deleted,
                    deleted_at = excluded.deleted_at,
                    updated_at = excluded.updated_at
                """,
                (
                    user.user_id,
                    1 if user.profile_deleted else 0,
                    "2026-10-01T00:00:00+00:00" if user.profile_deleted else None,
                    "2026-10-01T00:00:00+00:00",
                ),
            )

        key_by_id: dict[str, int] = {}
        for fact in dataset.facts:
            cur.execute(
                """
                INSERT INTO user_facts
                    (user_id, fact, category, importance, is_deleted, deleted_at,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fact.user_id,
                    fact.fact,
                    fact.category,
                    fact.importance,
                    1 if fact.is_deleted else 0,
                    fact.deleted_at,
                    fact.updated_at,
                    fact.updated_at,
                ),
            )
            fact_id = int(cur.lastrowid)
            key_by_id[fact.key] = fact_id
            if fact.embedding is not None:
                normalized = embeddings.normalize_vector(fact.embedding)
                cur.execute(
                    "INSERT INTO fact_embeddings (fact_id, model, dim, vector, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        fact_id,
                        embeddings.model_name(),
                        len(normalized),
                        embeddings.vector_to_blob(normalized),
                        fact.updated_at,
                    ),
                )
            if fact.is_deleted or user_deleted.get(fact.user_id, False):
                deleted_keys.add(fact.key)
    return key_by_id, deleted_keys


def run_evaluation(
    dataset: MemoryRetrievalDataset | None = None,
    *,
    dataset_path: Path | str = DEFAULT_DATASET_PATH,
    limit: int = DEFAULT_LIMIT,
) -> MemoryRetrievalReport:
    """執行整份記憶檢索評測。"""
    data = dataset or MemoryRetrievalDataset.load(dataset_path)
    now = retrieval._parse_updated_at(data.now) if data.now else None

    with tempfile.TemporaryDirectory(prefix="sherry_mem_eval_") as temp_dir:
        db_path = Path(temp_dir) / "memory_eval.db"
        key_by_id, deleted_keys = prepare_database(data, db_path)
        id_by_key = {fact_id: key for key, fact_id in key_by_id.items()}

        results: list[CaseResult] = []
        total_expected = 0
        total_matched = 0
        total_returned = 0
        total_false_positive = 0
        total_deleted_hits = 0

        for case in data.cases:
            started = time.perf_counter()
            matches = retrieval.search_facts(
                case.query,
                user_id=case.user_id,
                limit=limit,
                db_path=db_path,
                now=now,
                query_vector=case.query_vector,
            )
            latency_ms = (time.perf_counter() - started) * 1000.0
            returned = tuple(id_by_key.get(item.fact_id, str(item.fact_id)) for item in matches)

            expected_set = set(case.expected)
            returned_set = set(returned)
            missing = tuple(key for key in case.expected if key not in returned_set)
            false_positives = tuple(
                key for key in returned if key not in expected_set and key not in case.forbidden
            )
            forbidden_hits = tuple(key for key in returned if key in case.forbidden)
            deleted_hits = tuple(key for key in returned if key in deleted_keys)
            top1_ok = case.expect_top1 is None or (returned[:1] == (case.expect_top1,))
            recall = (
                (len(expected_set & returned_set) / len(expected_set))
                if expected_set
                else (1.0 if not returned else 0.0)
            )
            passed = not missing and not forbidden_hits and top1_ok

            total_expected += len(expected_set)
            total_matched += len(expected_set & returned_set)
            total_returned += len(returned)
            total_false_positive += len(false_positives)
            total_deleted_hits += len(deleted_hits)

            results.append(
                CaseResult(
                    case_id=case.id,
                    query=case.query,
                    user_id=case.user_id,
                    expected=case.expected,
                    returned=returned,
                    missing=missing,
                    false_positives=false_positives,
                    forbidden_hits=forbidden_hits,
                    deleted_hits=deleted_hits,
                    top1_ok=top1_ok,
                    recall=recall,
                    latency_ms=latency_ms,
                    passed=passed,
                )
            )

    keyword_recall = (total_matched / total_expected) if total_expected else 1.0
    false_positive_rate = (
        total_false_positive / total_returned if total_returned else 0.0
    )
    deleted_recall = (total_deleted_hits / len(deleted_keys)) if deleted_keys else 0.0
    p95 = _percentile([result.latency_ms for result in results], 0.95)
    metric_failures: list[str] = []
    if keyword_recall < data.thresholds["min_keyword_recall"]:
        metric_failures.append(
            f"keyword_recall {keyword_recall:.4f} < {data.thresholds['min_keyword_recall']}"
        )
    if false_positive_rate > data.thresholds["max_false_positive_rate"]:
        metric_failures.append(
            f"false_positive_rate {false_positive_rate:.4f} > "
            f"{data.thresholds['max_false_positive_rate']}"
        )
    if deleted_recall > data.thresholds["max_deleted_recall"]:
        metric_failures.append(
            f"deleted_recall {deleted_recall:.4f} > {data.thresholds['max_deleted_recall']}"
        )
    if p95 > data.thresholds["max_p95_latency_ms"]:
        metric_failures.append(
            f"p95_latency_ms {p95:.2f} > {data.thresholds['max_p95_latency_ms']}"
        )

    return MemoryRetrievalReport(
        dataset_version=data.version,
        total_cases=len(results),
        passed_cases=sum(1 for result in results if result.passed),
        keyword_recall=keyword_recall,
        false_positive_rate=false_positive_rate,
        deleted_recall=deleted_recall,
        p95_latency_ms=p95,
        thresholds=dict(data.thresholds),
        results=results,
        metric_failures=metric_failures,
    )


def main(argv: list[str] | None = None) -> int:
    """命令列入口；評測通過回傳 0，否則 1。"""
    parser = argparse.ArgumentParser(description="執行雪莉長期記憶檢索評測集 v1。")
    parser.add_argument(
        "--dataset",
        default=str(DEFAULT_DATASET_PATH),
        help="評測資料集 JSON 路徑。",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 輸出完整報告。")
    args = parser.parse_args(argv)

    report = run_evaluation(dataset_path=args.dataset)
    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"Memory Retrieval Eval v{report.dataset_version}")
        print(
            f"  cases={report.total_cases} passed={report.passed_cases} "
            f"recall={report.keyword_recall:.3f} "
            f"fp_rate={report.false_positive_rate:.3f} "
            f"deleted_recall={report.deleted_recall:.3f} "
            f"p95={report.p95_latency_ms:.1f}ms"
        )
        for failure in report.metric_failures:
            print(f"  [METRIC] {failure}")
        for result in report.results:
            if not result.passed:
                print(
                    f"  [CASE] {result.case_id} query={result.query!r} "
                    f"returned={result.returned} missing={result.missing} "
                    f"forbidden_hits={result.forbidden_hits} top1_ok={result.top1_ok}"
                )
        print("  RESULT:", "PASS" if report.passed else "FAIL")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
