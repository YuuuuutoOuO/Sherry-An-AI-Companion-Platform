"""v1：runtime state 資料表（session／限流／leader 租約）。"""

from __future__ import annotations

import sqlite3

from core.migrations import Migration


def _apply(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS state_kv (
            scope TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (scope, key)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS leader_leases (
            name TEXT PRIMARY KEY,
            owner TEXT NOT NULL,
            expires_at REAL NOT NULL
        )
        """
    )


MIGRATION = Migration(
    version=1,
    name="runtime_state_tables",
    apply=_apply,
    applies_to=("runtime_state.db",),
)
