"""R18 版本化遷移清單。

每個檔案一步；新增步驟請遞增版本並保持單向、冪等（重跑安全）。
"""

from __future__ import annotations

from core.migrations import Migration

from . import m001_runtime_state, m002_schedule_ledger, m003_schedule_sync

MIGRATIONS: list[Migration] = [
    m001_runtime_state.MIGRATION,
    m002_schedule_ledger.MIGRATION,
    m003_schedule_sync.MIGRATION,
]

__all__ = ["MIGRATIONS"]
