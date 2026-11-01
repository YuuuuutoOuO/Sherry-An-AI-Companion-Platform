"""
tools/backfill_embeddings.py
============================
R15：長期記憶向量回填工具（Ollama embeddings）。

用途：
1. 既有 ``sherry_memory.db`` 升級後，為所有現役事實補算 embedding。
2. 更換 embedding 模型、向量表損毀或從備份還原後重建。
3. 可安全重複執行；已具向量者自動略過，只補算缺失部分。

用法：
    python tools/backfill_embeddings.py
    python tools/backfill_embeddings.py --db path.db --user 123456
    python tools/backfill_embeddings.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def _resolve_db(db_path: str | None) -> Path:
    if db_path:
        return Path(db_path).expanduser().resolve()
    from handlers.memory import storage

    return Path(storage.MEMORY_DB_PATH)


async def _run_backfill(
    db_path: Path,
    *,
    user_id: str | None,
    limit: int | None,
    batch_size: int | None,
    timeout: float | None,
) -> int:
    from handlers.memory import embeddings

    if not embeddings.is_enabled():
        print("[ERROR] 記憶向量化已停用（MEMORY_EMBEDDING_ENABLED=false）。")
        return 3

    before = await asyncio.to_thread(embeddings.count_embeddings, db_path)
    missing_before = await asyncio.to_thread(
        embeddings.missing_fact_ids, db_path, user_id=user_id, limit=limit
    )
    if not missing_before:
        print(f"[INFO] 無需回填；現有向量 {before} 筆。")
        return 0

    service = embeddings.EmbeddingService(db_path, batch_size=batch_size)
    try:
        await service.enqueue(missing_before)
        await service.wait_idle(timeout=timeout)
    finally:
        await service.close()

    after = await asyncio.to_thread(embeddings.count_embeddings, db_path)
    remaining = await asyncio.to_thread(
        embeddings.missing_fact_ids, db_path, user_id=user_id, limit=limit
    )
    print(
        f"[INFO] 向量後端：{embeddings.vector_backend()}；"
        f"回填前 {before} 筆 → 回填後 {after} 筆。"
    )
    if remaining:
        print(
            f"[WARN] 仍有 {len(remaining)} 筆事實缺少向量（Ollama 離線或模型未安裝？）。"
            "可稍後重跑本工具。"
        )
        return 1
    print(f"[OK] 已完成 {len(missing_before)} 筆事實向量回填。")
    return 0


def main(argv: list[str] | None = None) -> int:
    """命令列入口；成功回傳 0。"""
    parser = argparse.ArgumentParser(
        description="為雪莉長期記憶事實回填 Ollama embeddings。"
    )
    parser.add_argument("--db", default=None, help="記憶資料庫路徑（預設 data/sherry_memory.db）。")
    parser.add_argument("--user", default=None, help="只回填指定使用者 ID。")
    parser.add_argument("--limit", type=int, default=None, help="本次最多處理幾筆。")
    parser.add_argument("--batch-size", type=int, default=None, help="覆寫每批處理筆數。")
    parser.add_argument("--timeout", type=float, default=600.0, help="整體等待上限秒數。")
    parser.add_argument("--dry-run", action="store_true", help="只統計缺失向量，不呼叫 Ollama。")
    args = parser.parse_args(argv)

    db_path = _resolve_db(args.db)
    if not db_path.exists():
        print(f"[ERROR] 找不到記憶資料庫：{db_path}")
        return 2

    from handlers.memory import embeddings

    if args.dry_run:
        try:
            current = embeddings.count_embeddings(db_path)
            missing = embeddings.missing_fact_ids(
                db_path, user_id=args.user, limit=args.limit
            )
        except Exception as exc:  # noqa: BLE001 - CLI 需完整回報
            print(f"[ERROR] 無法讀取向量現況：{exc}")
            return 2
        print(
            f"[INFO] 目標資料庫：{db_path}\n"
            f"[INFO] 向量後端：{embeddings.vector_backend()}；"
            f"現有 {current} 筆，待補 {len(missing)} 筆。"
        )
        print("[INFO] --dry-run：未呼叫 Ollama。")
        return 0

    try:
        return asyncio.run(
            _run_backfill(
                db_path,
                user_id=args.user,
                limit=args.limit,
                batch_size=args.batch_size,
                timeout=args.timeout,
            )
        )
    except Exception as exc:  # noqa: BLE001 - CLI 需完整回報
        print(f"[ERROR] 向量回填失敗：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
