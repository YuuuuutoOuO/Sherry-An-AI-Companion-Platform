"""
tests/unit/test_db_manager.py
=============================
測試 tools/db_manager.py 獨立資料庫管理與結構遷移工具。
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import pytest

from tools import db_manager


@pytest.fixture
def temp_db_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """建立測試用臨時 data 與 backups 目錄環境。"""
    test_data = tmp_path / "data"
    test_data.mkdir(parents=True, exist_ok=True)
    test_backups = test_data / "backups"
    test_backups.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(db_manager, "DATA_DIR", test_data)
    monkeypatch.setattr(db_manager, "BACKUP_DIR", test_backups)
    return test_data


def test_db_manager_migration_and_check(temp_db_env: Path) -> None:
    """測試全新環境下的 migrate 與 check 流程。"""
    # 1. 初始狀態：尚未建立任何 DB，check 應列出所有缺少項目
    diffs_before = db_manager.check_schema()
    assert len(diffs_before) > 0

    # 2. 執行遷移
    changes = db_manager.run_migration()
    assert changes > 0

    # 3. 遷移後再次 check：應該 0 差異
    diffs_after = db_manager.check_schema()
    assert len(diffs_after) == 0


def test_soft_delete_columns_exist(temp_db_env: Path) -> None:
    """驗證所有表格皆已正確建立軟刪除四本柱欄位。"""
    db_manager.run_migration()

    for t_def in db_manager.CANONICAL_TABLES:
        db_path = temp_db_env / t_def.db_relative_path
        assert db_path.exists()

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute(f"PRAGMA table_info({t_def.table_name});")
        cols = {row["name"] for row in cur.fetchall()}

        # 斷言必須含有審計與軟刪除四本柱
        assert "is_deleted" in cols, f"{t_def.table_name} 缺少 is_deleted"
        assert "deleted_at" in cols, f"{t_def.table_name} 缺少 deleted_at"
        assert "created_at" in cols, f"{t_def.table_name} 缺少 created_at"
        assert "updated_at" in cols, f"{t_def.table_name} 缺少 updated_at"

        conn.close()


def test_soft_delete_cleanup(temp_db_env: Path) -> None:
    """測試軟刪除資料清理功能。"""
    db_manager.run_migration()

    # 插入一筆正常資料與一筆軟刪除過期資料
    db_path = temp_db_env / "chat_history.db"
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # 正常資料
    cur.execute("""
        INSERT INTO chat_messages (timestamp, date_str, user_id, display_name, content, is_deleted)
        VALUES ('2026-09-17 00:00:00', '2026-09-17', 'u1', 'Alice', 'Hello', 0)
    """)

    # 軟刪除過期資料 (40天前已刪除)
    cur.execute("""
        INSERT INTO chat_messages (timestamp, date_str, user_id, display_name, content, is_deleted, deleted_at)
        VALUES ('2026-08-01 00:00:00', '2026-08-01', 'u2', 'Bob', 'Spam', 1, '2026-08-01 00:00:00')
    """)
    conn.commit()

    # 驗證插入成功
    cur.execute("SELECT COUNT(*) FROM chat_messages;")
    assert cur.fetchone()[0] == 2
    conn.close()

    # 執行清理 (清理 >30 天)
    purged = db_manager.cleanup_soft_deleted(days=30)
    assert purged == 1

    # 再次檢查資料庫：應只剩 1 筆正常資料
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM chat_messages;")
    assert cur.fetchone()[0] == 1
    cur.execute("SELECT user_id FROM chat_messages;")
    assert cur.fetchone()[0] == "u1"
    conn.close()


def test_import_legacy_json(temp_db_env: Path) -> None:
    """測試舊版 user_memory.json 與 roast_rules.json 匯入至 SQLite。"""
    # 建立 mock user_memory.json
    mock_user_mem = {
        "123456": {
            "aliases": ["TestUser"],
            "facts": [
                {"fact": "喜歡寫 Python", "category": "hobby", "importance": 5}
            ]
        }
    }
    # 建立 mock roast_rules.json 與 user_relations.json
    mock_roast = {
        "rules": [
            {"id": "r1", "keyword": "測試", "reply_guide": "吐槽測試", "probability": 100}
        ]
    }
    mock_rel = {
        "123456": {
            "affinity": 50.0,
            "tier": "friend",
            "interaction_count": 10,
            "last_interaction": "2026-09-17T00:00:00",
            "updated_at": "2026-09-17 00:00:00",
        }
    }
    (temp_db_env / "user_memory.json").write_text(json.dumps(mock_user_mem, ensure_ascii=False), encoding="utf-8")
    (temp_db_env / "roast_rules.json").write_text(json.dumps(mock_roast, ensure_ascii=False), encoding="utf-8")
    (temp_db_env / "user_relations.json").write_text(json.dumps(mock_rel, ensure_ascii=False), encoding="utf-8")

    # 執行匯入
    stats = db_manager.import_legacy_json()
    assert stats["user_profiles"] == 1
    assert stats["user_facts"] == 1
    assert stats["user_aliases"] == 1
    assert stats["roast_rules"] == 1
    assert stats["user_relations"] == 1

    # 驗證資料庫內容
    db_path = temp_db_env / "sherry_memory.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM user_profiles WHERE user_id = '123456';")
    row = cur.fetchone()
    assert row is not None
    assert row["display_name"] == "TestUser"
    assert row["is_deleted"] == 0

    cur.execute("SELECT * FROM user_facts WHERE user_id = '123456';")
    fact_row = cur.fetchone()
    assert fact_row is not None
    assert fact_row["fact"] == "喜歡寫 Python"
    assert fact_row["is_deleted"] == 0

    cur.execute("SELECT * FROM user_relations WHERE user_id = '123456';")
    rel_row = cur.fetchone()
    assert rel_row is not None
    assert rel_row["affinity"] == 50.0
    assert rel_row["tier"] == "friend"
    assert rel_row["is_deleted"] == 0

    conn.close()

    # 驗證 roast_rules
    chat_db_path = temp_db_env / "chat_history.db"
    conn_chat = sqlite3.connect(str(chat_db_path))
    conn_chat.row_factory = sqlite3.Row
    cur_chat = conn_chat.cursor()
    cur_chat.execute("SELECT * FROM roast_rules WHERE id = 'r1';")
    roast_row = cur_chat.fetchone()
    assert roast_row is not None
    assert roast_row["keyword"] == "測試"
    assert roast_row["is_deleted"] == 0
    conn_chat.close()


def test_db_manager_cli_entrypoint(temp_db_env: Path) -> None:
    """測試 CLI 指令進入點各子指令無崩潰。"""
    assert db_manager.main(["migrate"]) == 0
    assert db_manager.main(["check"]) == 0
    assert db_manager.main(["import-legacy"]) == 0
    assert db_manager.main(["stats"]) == 0
    assert db_manager.main(["cleanup", "--days", "30"]) == 0
    assert db_manager.main(["vacuum"]) == 0
