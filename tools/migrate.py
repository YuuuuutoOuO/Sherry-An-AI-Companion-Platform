"""資料庫遷移 CLI（R18）。

用法：
    python -m tools.migrate --db data/runtime_state.db --status
    python -m tools.migrate --db data/runtime_state.db --dry-run
    python -m tools.migrate --db data/schedules.db --suite schedules --backup
    python -m tools.migrate --db data/schedules.db --rollback --yes

``--dry-run`` 只列出待套用步驟；``--rollback`` 還原最近一次備份（需 --yes）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from core.migrations import MigrationRunner
from migrations import MIGRATIONS


def _suite_filename(suite: str, db_path: Path) -> str:
    if suite == "runtime":
        return "runtime_state.db"
    if suite == "schedules":
        return "schedules.db"
    return db_path.name


def _latest_backup(db_path: Path, backup_dir: Path | None = None) -> Path | None:
    folder = Path(backup_dir) if backup_dir else db_path.parent / "backups"
    if not folder.exists():
        return None
    candidates = sorted(
        folder.glob(f"{db_path.stem}.v*.bak"),
        key=lambda path: path.stat().st_mtime,
    )
    return candidates[-1] if candidates else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sherry SQLite 版本化遷移工具")
    parser.add_argument("--db", required=True, help="目標 SQLite 檔案")
    parser.add_argument("--suite", choices=["auto", "runtime", "schedules"], default="auto")
    parser.add_argument("--status", action="store_true", help="顯示版本與待套用步驟")
    parser.add_argument("--dry-run", action="store_true", help="只列出待套用步驟，不寫入")
    parser.add_argument("--backup", action="store_true", help="套用前備份資料庫")
    parser.add_argument("--backup-dir", default="", help="備份目錄（預設 data/backups）")
    parser.add_argument("--rollback", action="store_true", help="還原最近一次備份")
    parser.add_argument("--yes", action="store_true", help="確認執行 rollback")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    backup_dir = Path(args.backup_dir) if args.backup_dir else None
    runner = MigrationRunner(
        db_path,
        MIGRATIONS,
        db_filename=_suite_filename(args.suite, db_path),
    )

    if args.rollback:
        if not args.yes:
            print("拒絕執行：rollback 需加上 --yes", file=sys.stderr)
            return 2
        backup = _latest_backup(db_path, backup_dir)
        if backup is None:
            print("找不到可還原的備份", file=sys.stderr)
            return 1
        try:
            safety = runner.restore(backup)
        except (OSError, RuntimeError, FileNotFoundError) as exc:
            print(f"rollback 失敗：{exc}", file=sys.stderr)
            return 1
        print(
            json.dumps(
                {
                    "status": "rolled_back",
                    "db": str(db_path),
                    "backup": str(backup),
                    "safety_backup": str(safety) if safety != db_path else "",
                },
                ensure_ascii=False,
            )
        )
        return 0

    if args.status:
        print(json.dumps(runner.status(), ensure_ascii=False, indent=2))
        return 0

    if args.backup and not args.dry_run:
        backup = runner.backup(backup_dir)
        if backup:
            print(f"已備份：{backup}")

    results = runner.run(dry_run=args.dry_run)
    payload = {
        "db": str(db_path),
        "dry_run": bool(args.dry_run),
        "results": [
            {
                "version": result.version,
                "name": result.name,
                "applied": result.applied,
                "error": result.error,
            }
            for result in results
        ],
        "user_version": runner.current_version(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if all(not result.error for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
