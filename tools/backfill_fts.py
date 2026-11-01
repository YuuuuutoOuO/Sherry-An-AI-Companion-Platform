"""
tools/backfill_fts.py
=====================
長期記憶 FTS5 索引回填／修復工具（R14）。

用途：
1. 既有 ``sherry_memory.db`` 升級後，補建 ``facts_fts`` 外部內容表與同步 triggers。
2. 索引損毀、手動改寫 ``user_facts`` 或從備份還原後，以權威資料表重建索引。
3. 部署時可安全重複執行（idempotent）。

用法：
    python tools/backfill_fts.py                 # 回填預設 data/sherry_memory.db
    python tools/backfill_fts.py --db path.db    # 指定資料庫
    python tools/backfill_fts.py --dry-run       # 只檢查資料庫，不建立／重建索引
"""

from __future__ import annotations

import argparse
import sqlite3
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
    """解析目標資料庫路徑；未指定時使用專案預設記憶資料庫。"""
    if db_path:
        return Path(db_path).expanduser().resolve()
    from handlers.memory import storage

    return Path(storage.MEMORY_DB_PATH)


def _inspect(db_path: Path) -> dict[str, object]:
    """唯讀檢查資料庫現況，供 ``--dry-run`` 與輸出使用。"""
    conn = sqlite3.connect(str(db_path))
    try:
        fts_exists = (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'facts_fts'"
            ).fetchone()
            is not None
        )
        trigger_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' "
                "AND name IN ('trg_user_facts_ai', 'trg_user_facts_ad', 'trg_user_facts_au')"
            ).fetchone()[0]
        )
        total = int(conn.execute("SELECT COUNT(*) FROM user_facts").fetchone()[0])
        active = int(
            conn.execute("SELECT COUNT(*) FROM user_facts WHERE is_deleted = 0").fetchone()[0]
        )
    finally:
        conn.close()
    return {
        "fts_exists": fts_exists,
        "trigger_count": trigger_count,
        "total": total,
        "active": active,
    }


def main(argv: list[str] | None = None) -> int:
    """命令列入口；回傳 process exit code。"""
    parser = argparse.ArgumentParser(
        description="回填／重建雪莉長期記憶 FTS5 索引（facts_fts）。"
    )
    parser.add_argument(
        "--db",
        default=None,
        help="記憶資料庫路徑（預設 data/sherry_memory.db）。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只檢查現況，不建立或重建索引。",
    )
    args = parser.parse_args(argv)

    db_path = _resolve_db(args.db)
    if not db_path.exists():
        print(f"[ERROR] 找不到記憶資料庫：{db_path}")
        return 2

    try:
        before = _inspect(db_path)
    except sqlite3.DatabaseError as exc:
        print(f"[ERROR] 無法讀取記憶資料庫：{exc}")
        return 2

    print(f"[INFO] 目標資料庫：{db_path}")
    print(
        "[INFO] 現況：facts_fts={fts} triggers={triggers}/3 "
        "facts={total}（有效 {active}）".format(
            fts="存在" if before["fts_exists"] else "不存在",
            triggers=before["trigger_count"],
            total=before["total"],
            active=before["active"],
        )
    )

    if args.dry_run:
        print("[INFO] --dry-run：未變更資料庫。")
        return 0

    from handlers.memory import storage

    try:
        result = storage.rebuild_facts_fts(db_path)
    except Exception as exc:  # noqa: BLE001 - CLI 需完整回報底層錯誤
        print(f"[ERROR] 回填失敗：{exc}")
        return 1

    print(
        "[OK] FTS5 索引已重建：facts={total}（有效 {active}）".format(**result)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
