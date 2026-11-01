"""
handlers/memory/embeddings.py
=============================
R15：長期記憶向量化管線（Ollama Embeddings＋可選 sqlite-vec 降級）。

設計重點：
1. 以 Ollama 原生 ``/api/embeddings`` 產生向量；請求具 timeout 與指數退避重試。
2. 向量以正規化 float32 BLOB 儲存於 ``fact_embeddings``，無需任何額外依賴即可
   以 Python cosine 相似度檢索；若環境安裝 ``sqlite-vec`` 則可作為未來加速後端，
   未安裝時自動維持純 Python 降級（``vector_backend()`` 可觀測）。
3. ``EmbeddingService`` 以 asyncio 佇列批次補算缺失向量，失敗僅記錄並保留缺失
   狀態，下次啟動／刷新會自動重試，不阻塞對話熱路徑。
4. 查詢向量具小型 LRU 快取，供同步 prompt 尾段注入重用，避免同一句話重複呼叫
   Ollama。

所有資料庫寫入皆透過 ``asyncio.to_thread`` 執行，避免阻塞事件循環。
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
import importlib.util
import logging
import math
from pathlib import Path
import struct
from typing import Iterable, Sequence
import weakref

import aiohttp

import settings
from core.db import db_transaction, get_db_connection
from handlers.memory import storage

logger = logging.getLogger(__name__)

_QUERY_CACHE: OrderedDict[tuple[str, str], tuple[float, ...]] = OrderedDict()
_BACKGROUND_TASKS: set[asyncio.Task[object]] = set()
_SERVICES: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, EmbeddingService]]" = (
    weakref.WeakKeyDictionary()
)


class EmbeddingError(RuntimeError):
    """向量化失敗（連線、HTTP 狀態或回應格式異常）。"""


# ---------------------------------------------------------------------------
# 向量工具
# ---------------------------------------------------------------------------
def normalize_vector(vector: Sequence[float]) -> tuple[float, ...]:
    """將向量正規化為單位長度；零向量回傳全 0。"""
    values = tuple(float(value) for value in vector)
    norm = math.sqrt(math.fsum(value * value for value in values))
    if norm <= 1e-12:
        return tuple(0.0 for _ in values)
    return tuple(value / norm for value in values)


def vector_to_blob(vector: Sequence[float]) -> bytes:
    """以 little-endian float32 序列化向量。"""
    values = tuple(float(value) for value in vector)
    return struct.pack(f"<{len(values)}f", *values)


def blob_to_vector(blob: bytes, dim: int | None = None) -> tuple[float, ...]:
    """反序列化 float32 BLOB；``dim`` 可用於驗證長度。"""
    if not blob:
        return ()
    if dim is None:
        dim = len(blob) // 4
    length = max(0, min(int(dim), len(blob) // 4))
    if length <= 0:
        return ()
    return struct.unpack(f"<{length}f", blob[: length * 4])


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """計算 cosine 相似度（0 向量或維度不一致視為不相關）。"""
    if len(left) != len(right):
        # 模型切換或舊向量維度不符時不可截斷比較，否則會產生錯誤的高相似度。
        return 0.0
    size = len(left)
    if size <= 0:
        return 0.0
    dot = math.fsum(float(left[index]) * float(right[index]) for index in range(size))
    norm_left = math.sqrt(
        math.fsum(float(left[index]) ** 2 for index in range(size))
    )
    norm_right = math.sqrt(
        math.fsum(float(right[index]) ** 2 for index in range(size))
    )
    if norm_left <= 1e-12 or norm_right <= 1e-12:
        return 0.0
    return max(-1.0, min(1.0, dot / (norm_left * norm_right)))


def sqlite_vec_available() -> bool:
    """回報環境是否安裝 ``sqlite-vec``（僅供觀測；未安裝時走 Python 降級）。"""
    try:
        return importlib.util.find_spec("sqlite_vec") is not None
    except (ImportError, ValueError):
        return False


def vector_backend() -> str:
    """回報實際使用的向量相似度後端。"""
    return "sqlite-vec" if sqlite_vec_available() else "python-cosine"


def is_enabled() -> bool:
    """向量化是否啟用（可由 WebUI／環境變數熱改）。"""
    return bool(getattr(settings, "MEMORY_EMBEDDING_ENABLED", True))


def _model() -> str:
    return str(getattr(settings, "MEMORY_EMBEDDING_MODEL", "nomic-embed-text"))


def model_name() -> str:
    """目前使用的 embedding 模型名稱。"""
    return _model()


def _db_path(db_path: Path | str | None = None) -> Path:
    return Path(db_path) if db_path is not None else storage.MEMORY_DB_PATH


# ---------------------------------------------------------------------------
# Ollama embeddings client
# ---------------------------------------------------------------------------
class OllamaEmbeddingClient:
    """Ollama ``/api/embeddings`` 非同步客戶端（含指數退避重試）。"""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._base_url = base_url
        self._model = model
        self._timeout = timeout
        self._max_retries = max_retries
        self._session = session
        self._owns_session = session is None

    @property
    def base_url(self) -> str:
        return str(
            self._base_url
            or getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434")
        ).rstrip("/")

    @property
    def model(self) -> str:
        return str(self._model or _model())

    @property
    def timeout(self) -> float:
        return float(
            self._timeout
            if self._timeout is not None
            else getattr(settings, "MEMORY_EMBEDDING_TIMEOUT", 8.0)
        )

    @property
    def max_retries(self) -> int:
        value = (
            self._max_retries
            if self._max_retries is not None
            else getattr(settings, "MEMORY_EMBEDDING_MAX_RETRIES", 2)
        )
        return max(0, int(value))

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(
                    total=self.timeout,
                    connect=min(3.0, self.timeout),
                )
            )
            self._owns_session = True
        return self._session

    async def _request_once(self, text: str) -> list[float]:
        """單次 HTTP 請求；測試可覆寫此方法模擬失敗。"""
        session = await self._get_session()
        payload = {"model": self.model, "prompt": text}
        async with session.post(
            f"{self.base_url}/api/embeddings", json=payload
        ) as response:
            if response.status != 200:
                body = await response.text()
                raise EmbeddingError(
                    f"Ollama embeddings HTTP {response.status}: {body[:200]}"
                )
            data = await response.json(content_type=None)

        embedding = data.get("embedding") if isinstance(data, dict) else None
        if not isinstance(embedding, list) or not embedding:
            raise EmbeddingError("Ollama embeddings 回應缺少 embedding 陣列")
        vector = [float(value) for value in embedding]
        if len(vector) < 2:
            raise EmbeddingError("Ollama embeddings 向量維度異常")
        return vector

    async def embed(self, text: str) -> list[float]:
        """取得單一文本向量；失敗時依設定重試。"""
        clean = (text or "").strip()
        if not clean:
            raise EmbeddingError("不可對空白文本產生向量")

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return await self._request_once(clean)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 統一轉為 EmbeddingError
                last_error = exc
                if attempt >= self.max_retries:
                    break
                delay = min(2.0, 0.25 * (2**attempt))
                logger.debug(
                    "[Embeddings] 第 %d 次請求失敗，%.2fs 後重試: %s",
                    attempt + 1,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
        raise EmbeddingError(str(last_error) if last_error else "向量化失敗")

    async def embed_many(
        self,
        texts: Sequence[str],
        *,
        concurrency: int | None = None,
    ) -> list[list[float] | Exception]:
        """並行取得多筆向量；個別失敗以 Exception 回傳，不中斷整批。"""
        if not texts:
            return []
        limit = max(
            1,
            int(
                concurrency
                if concurrency is not None
                else getattr(settings, "MEMORY_EMBEDDING_MAX_CONCURRENCY", 2)
            ),
        )
        semaphore = asyncio.Semaphore(limit)

        async def _one(text: str) -> list[float] | Exception:
            async with semaphore:
                try:
                    return await self.embed(text)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - 批次容忍個別失敗
                    return exc

        return list(await asyncio.gather(*(_one(text) for text in texts)))

    async def close(self) -> None:
        """關閉自行建立的 aiohttp session。"""
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None


# ---------------------------------------------------------------------------
# 資料庫存取（同步；呼叫端負責 to_thread）
# ---------------------------------------------------------------------------
def _missing_fact_ids_sync(
    db_path: Path,
    user_id: str | int | None,
    limit: int | None,
) -> list[int]:
    storage.ensure_memory_db(db_path)
    sql = (
        "SELECT f.id FROM user_facts f "
        "JOIN user_profiles p ON p.user_id = f.user_id "
        "LEFT JOIN fact_embeddings e ON e.fact_id = f.id "
        "WHERE f.is_deleted = 0 AND p.is_deleted = 0 AND e.fact_id IS NULL"
    )
    params: list[object] = []
    if user_id is not None:
        sql += " AND f.user_id = ?"
        params.append(str(user_id))
    sql += " ORDER BY f.importance DESC, f.id ASC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(max(1, int(limit)))

    conn = get_db_connection(db_path)
    try:
        return [int(row["id"]) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def missing_fact_ids(
    db_path: Path | str | None = None,
    *,
    user_id: str | int | None = None,
    limit: int | None = None,
) -> list[int]:
    """回傳尚未建立向量的事實 id（僅未軟刪事實與 Profile）。"""
    return _missing_fact_ids_sync(_db_path(db_path), user_id, limit)


def _load_fact_texts_sync(db_path: Path, fact_ids: Iterable[int]) -> dict[int, str]:
    ids = list(dict.fromkeys(int(fact_id) for fact_id in fact_ids))
    if not ids:
        return {}
    storage.ensure_memory_db(db_path)
    placeholders = ",".join("?" for _ in ids)
    conn = get_db_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT f.id, f.fact FROM user_facts f "
            "JOIN user_profiles p ON p.user_id = f.user_id "
            f"WHERE f.id IN ({placeholders}) AND f.is_deleted = 0 AND p.is_deleted = 0",
            ids,
        ).fetchall()
    finally:
        conn.close()
    return {int(row["id"]): str(row["fact"]) for row in rows}


def _store_vectors_sync(
    db_path: Path,
    vectors: Iterable[tuple[int, Sequence[float]]],
) -> int:
    rows = [
        (
            int(fact_id),
            _model(),
            len(vector),
            vector_to_blob(normalize_vector(vector)),
        )
        for fact_id, vector in vectors
        if len(vector) >= 2
    ]
    if not rows:
        return 0
    storage.ensure_memory_db(db_path)
    with db_transaction(db_path) as cur:
        cur.executemany(
            """
            INSERT INTO fact_embeddings (fact_id, model, dim, vector, updated_at)
            VALUES (?, ?, ?, ?, datetime('now', 'localtime'))
            ON CONFLICT(fact_id) DO UPDATE SET
                model = excluded.model,
                dim = excluded.dim,
                vector = excluded.vector,
                updated_at = excluded.updated_at
            """,
            rows,
        )
    return len(rows)


def load_embeddings(
    fact_ids: Iterable[int],
    db_path: Path | str | None = None,
) -> dict[int, tuple[float, ...]]:
    """讀取指定事實的向量（缺失者不在回傳中）。"""
    ids = list(dict.fromkeys(int(fact_id) for fact_id in fact_ids))
    if not ids:
        return {}
    target = _db_path(db_path)
    storage.ensure_memory_db(target)
    placeholders = ",".join("?" for _ in ids)
    conn = get_db_connection(target)
    try:
        rows = conn.execute(
            f"SELECT fact_id, dim, vector FROM fact_embeddings "
            f"WHERE fact_id IN ({placeholders})",
            ids,
        ).fetchall()
    finally:
        conn.close()
    return {
        int(row["fact_id"]): blob_to_vector(row["vector"], int(row["dim"]))
        for row in rows
    }


def has_any_embeddings(db_path: Path | str | None = None) -> bool:
    """是否已有任何事實向量；用於避免在尚未回填時呼叫 Ollama。"""
    target = _db_path(db_path)
    storage.ensure_memory_db(target)
    conn = get_db_connection(target)
    try:
        return conn.execute("SELECT 1 FROM fact_embeddings LIMIT 1").fetchone() is not None
    finally:
        conn.close()


def count_embeddings(db_path: Path | str | None = None) -> int:
    """目前向量筆數（供 CLI／診斷）。"""
    target = _db_path(db_path)
    storage.ensure_memory_db(target)
    conn = get_db_connection(target)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM fact_embeddings").fetchone()[0])
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 查詢向量快取
# ---------------------------------------------------------------------------
def _query_cache_limit() -> int:
    return max(0, int(getattr(settings, "MEMORY_EMBEDDING_QUERY_CACHE_SIZE", 128)))


def get_cached_query_embedding(text: str) -> tuple[float, ...] | None:
    """取得同一模型下已快取的查詢向量（供同步 prompt 尾段重用）。"""
    clean = (text or "").strip()
    if not clean:
        return None
    return _QUERY_CACHE.get((_model(), clean))


def _cache_query_embedding(text: str, vector: Sequence[float]) -> tuple[float, ...]:
    normalized = normalize_vector(vector)
    limit = _query_cache_limit()
    if limit <= 0:
        return normalized
    key = (_model(), text.strip())
    _QUERY_CACHE[key] = normalized
    _QUERY_CACHE.move_to_end(key)
    while len(_QUERY_CACHE) > limit:
        _QUERY_CACHE.popitem(last=False)
    return normalized


def clear_query_cache() -> None:
    """清空查詢向量快取（測試／模型切換使用）。"""
    _QUERY_CACHE.clear()


# ---------------------------------------------------------------------------
# 佇列服務
# ---------------------------------------------------------------------------
class EmbeddingService:
    """批次補算事實向量的非同步佇列服務。"""

    def __init__(
        self,
        db_path: Path | str | None = None,
        *,
        client: OllamaEmbeddingClient | None = None,
        batch_size: int | None = None,
    ) -> None:
        self._db_path = Path(db_path) if db_path is not None else None
        self._client = client or OllamaEmbeddingClient()
        self._batch_size = max(
            1,
            int(
                batch_size
                if batch_size is not None
                else getattr(settings, "MEMORY_EMBEDDING_BATCH_SIZE", 8)
            ),
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[int] | None = None
        self._pending: set[int] = set()
        self._worker: asyncio.Task[None] | None = None
        self._idle = asyncio.Event()
        self._idle.set()

    @property
    def db_path(self) -> Path:
        return self._db_path or storage.MEMORY_DB_PATH

    def _ensure_loop(self) -> asyncio.Queue[int]:
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            self._loop = loop
            self._queue = asyncio.Queue()
            self._pending = set()
            self._worker = None
            self._idle = asyncio.Event()
            self._idle.set()
        assert self._queue is not None
        return self._queue

    async def enqueue(self, fact_ids: Iterable[int]) -> int:
        """將事實 id 加入佇列（自動去重），回傳新加入筆數。"""
        queue = self._ensure_loop()
        added = 0
        for fact_id in fact_ids:
            numeric = int(fact_id)
            if numeric in self._pending:
                continue
            self._pending.add(numeric)
            queue.put_nowait(numeric)
            added += 1
        if added:
            self._idle.clear()
            if self._worker is None or self._worker.done():
                self._worker = asyncio.create_task(self._worker_run())
        return added

    async def enqueue_missing(
        self,
        *,
        user_id: str | int | None = None,
        limit: int | None = None,
    ) -> int:
        """掃描資料庫並將缺失向量的事實加入佇列。"""
        ids = await asyncio.to_thread(
            _missing_fact_ids_sync, self.db_path, user_id, limit
        )
        if not ids:
            return 0
        return await self.enqueue(ids)

    async def _worker_run(self) -> None:
        assert self._queue is not None
        while True:
            first = await self._queue.get()
            batch = [first]
            self._pending.discard(first)
            while len(batch) < self._batch_size:
                try:
                    nxt = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if nxt in batch:
                    continue
                self._pending.discard(nxt)
                batch.append(nxt)
            try:
                await self._process(batch)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 背景管線不得致命
                logger.warning("[Embeddings] 批次處理失敗: %s", exc)
            finally:
                for _ in batch:
                    self._queue.task_done()
                if self._queue.empty():
                    self._idle.set()

    async def _process(self, fact_ids: list[int]) -> None:
        texts = await asyncio.to_thread(_load_fact_texts_sync, self.db_path, fact_ids)
        pairs = [(fact_id, text) for fact_id, text in texts.items() if text.strip()]
        if not pairs:
            return
        results = await self._client.embed_many([text for _, text in pairs])
        vectors: list[tuple[int, list[float]]] = []
        for (fact_id, _text), result in zip(pairs, results):
            if isinstance(result, Exception):
                logger.warning(
                    "[Embeddings] 事實 %d 向量化失敗，保留待補: %s", fact_id, result
                )
                continue
            vectors.append((fact_id, result))
        if vectors:
            await asyncio.to_thread(_store_vectors_sync, self.db_path, vectors)

    async def embed_query(self, text: str) -> tuple[float, ...] | None:
        """取得查詢向量（含快取）；失敗回傳 None，呼叫端應降級為純關鍵字。"""
        clean = (text or "").strip()
        if not clean:
            return None
        cached = get_cached_query_embedding(clean)
        if cached is not None:
            return cached
        try:
            if not await asyncio.to_thread(has_any_embeddings, self.db_path):
                return None
        except Exception as exc:  # noqa: BLE001 - schema 未就緒時降級
            logger.debug("[Embeddings] 無法確認向量現況，降級關鍵字檢索: %s", exc)
            return None
        try:
            vector = await self._client.embed(clean)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 檢索不得因向量服務失敗而中斷
            logger.debug("[Embeddings] 查詢向量化失敗，降級關鍵字檢索: %s", exc)
            return None
        return _cache_query_embedding(clean, vector)

    async def wait_idle(self, timeout: float | None = None) -> None:
        """等待佇列處理完畢（CLI／測試用）。"""
        if self._loop is None:
            return
        await asyncio.wait_for(self._idle.wait(), timeout)

    async def close(self) -> None:
        """停止 worker 並關閉 HTTP session。"""
        if self._worker is not None and not self._worker.done():
            self._worker.cancel()
            try:
                await self._worker
            except (asyncio.CancelledError, Exception):
                pass
        self._worker = None
        await self._client.close()


def get_embedding_service(
    db_path: Path | str | None = None,
) -> EmbeddingService:
    """取得目前事件迴圈專屬的 ``EmbeddingService``。"""
    loop = asyncio.get_running_loop()
    key = str(_db_path(db_path).resolve())
    services = _SERVICES.setdefault(loop, {})
    service = services.get(key)
    if service is None:
        service = EmbeddingService(db_path)
        services[key] = service
    return service


async def embed_query(
    text: str,
    *,
    db_path: Path | str | None = None,
) -> tuple[float, ...] | None:
    """取得查詢向量；未啟用、無既有向量或 Ollama 失敗時回傳 None。"""
    if not is_enabled():
        return None
    if float(getattr(settings, "MEMORY_RETRIEVAL_W_COS_SIM", 0.0)) <= 0.0:
        return None
    service = get_embedding_service(db_path)
    return await service.embed_query(text)


def schedule_embedding_refresh(
    *,
    fact_ids: Iterable[int] | None = None,
    user_id: str | int | None = None,
    limit: int | None = None,
) -> bool:
    """在目前事件迴圈背景補算向量；非 async 環境或未啟用時回傳 False。"""
    if not is_enabled():
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False

    service = get_embedding_service()

    async def _job() -> None:
        try:
            if fact_ids is not None:
                await service.enqueue(fact_ids)
            else:
                await service.enqueue_missing(user_id=user_id, limit=limit)
        except Exception as exc:  # noqa: BLE001 - 背景刷新不得影響主流程
            logger.debug("[Embeddings] 背景刷新失敗: %s", exc)

    task = loop.create_task(_job())
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return True


def reset_for_tests() -> None:
    """清除模組級快取與服務（測試隔離用）。"""
    _QUERY_CACHE.clear()
    _SERVICES.clear()
    for task in list(_BACKGROUND_TASKS):
        task.cancel()
    _BACKGROUND_TASKS.clear()
