"""R6：軟刪核心（記憶寫入模型）測試。

對應計畫 R6 與報告 §5.2.1／§5.2.2／§5.2.9、§7.1.2 B1/B3、P0-3：
  - /forget 交易級清除：facts/profiles 軟刪＋relations 政策處置＋chat_messages
    標記＋JSON/archive 檔案清除＋稽核；**全成功才回覆「已清除」**。
  - `upsert_user_facts` 差異化 UPSERT 取代全表 `DELETE FROM user_facts`。
  - unique index `(user_id, fact)`＋去重遷移。
  - 刪除路徑同步化（移除 fire-and-forget）。
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

import handlers.memory.chat_logger as chat_logger
import handlers.memory.relationship as rel_module
import handlers.memory.storage as mem_storage
from core.db import db_transaction, get_db_connection
from handlers.memory.storage import (
    delete_user_memory,
    load_user_memory,
    make_fact,
    save_single_user_memory,
    save_user_memory,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# 隔離環境 fixture：所有路徑導向 tmp_path（不觸碰真實 data/）
# ---------------------------------------------------------------------------
@pytest.fixture()
def isolated_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    mem_db = tmp_path / "sherry_memory.db"
    chat_db = tmp_path / "chat_history.db"
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    monkeypatch.setattr(mem_storage, "MEMORY_DB_PATH", mem_db)
    monkeypatch.setattr(rel_module, "MEMORY_DB_PATH", mem_db)
    monkeypatch.setattr(mem_storage, "_INITIALIZED_MEMORY_DBS", set())
    monkeypatch.setattr(rel_module, "_INITIALIZED_RELATIONS_DBS", set())
    monkeypatch.setattr(chat_logger, "DB_PATH", chat_db)
    monkeypatch.setattr(chat_logger, "_CHAT_DB_INITIALIZED", False)
    monkeypatch.setattr(rel_module.settings, "USER_RELATIONS_PATH", data_dir / "user_relations.json")
    return {"mem_db": mem_db, "chat_db": chat_db, "data_dir": data_dir}


def _seed_chat_messages(chat_db: Path, rows: list[tuple[str, str, str]]) -> None:
    """rows: [(user_id, date_str, content)]"""
    chat_logger._init_chat_db()
    with db_transaction(chat_db) as cur:
        for i, (uid, date_str, content) in enumerate(rows):
            cur.execute(
                "INSERT INTO chat_messages (timestamp, date_str, user_id, display_name, content, server_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (f"2026-10-18T0{i}:00:00+08:00", date_str, uid, f"user_{uid}", content, ""),
            )


def _seed_files(data_dir: Path, uid: str, other: str) -> None:
    (data_dir / "chat_logs").mkdir(exist_ok=True)
    (data_dir / "archive_legacy_json" / "chat_logs").mkdir(parents=True, exist_ok=True)

    (data_dir / "user_memory.json").write_text(
        json.dumps({uid: {"aliases": ["阿明"], "facts": []}, other: {"aliases": ["小華"], "facts": []}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (data_dir / "user_relations.json").write_text(
        json.dumps({uid: {"affinity": 80.0}, other: {"affinity": 10.0}}, ensure_ascii=False),
        encoding="utf-8",
    )
    lines = [
        json.dumps({"ts": "2026-10-18T01:00:00", "user_id": uid, "display_name": "阿明", "content": "被遺忘的訊息"}, ensure_ascii=False),
        json.dumps({"ts": "2026-10-18T01:01:00", "user_id": other, "display_name": "小華", "content": "別人的訊息"}, ensure_ascii=False),
    ]
    for rel in ("chat_logs/2026-10-18.jsonl", "archive_legacy_json/chat_logs/2026-10-18.jsonl"):
        (data_dir / rel).write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. 全範圍清除（成功語意）
# ---------------------------------------------------------------------------
def test_forget_full_purge_marks_all_scopes(isolated_memory: dict[str, Path]) -> None:
    tmp = isolated_memory
    uid, other = "111", "222"

    store = {
        uid: {"aliases": ["阿明"], "facts": [make_fact("喜歡紅茶", importance=4, category="preference")]},
        other: {"aliases": ["小華"], "facts": [make_fact("喜歡綠茶", importance=3, category="preference")]},
    }
    save_user_memory(store, tmp["data_dir"] / "user_memory.json")

    mgr = rel_module.RelationshipManager()
    mgr.store[uid] = {"affinity": 80.0, "tier": "close", "interaction_count": 5, "last_interaction": "2026-10-01"}
    mgr.store[other] = {"affinity": 10.0, "tier": "stranger", "interaction_count": 1, "last_interaction": "2026-10-02"}
    mgr._do_write_sync(dict(mgr.store))

    _seed_chat_messages(tmp["chat_db"], [(uid, "2026-10-18", "訊息A"), (uid, "2026-10-18", "訊息B"), (other, "2026-10-18", "別人的")])
    _seed_files(tmp["data_dir"], uid, other)

    results = asyncio.run(mem_storage.purge_user_data(uid, relations=mgr, data_dir=tmp["data_dir"]))
    assert results["ok"] is True, f"清除應全數成功：{results}"

    # 1) profiles / facts：軟刪（保留軌跡）
    conn = get_db_connection(tmp["mem_db"])
    try:
        cur = conn.cursor()
        cur.execute("SELECT is_deleted FROM user_profiles WHERE user_id = ?", (uid,))
        assert cur.fetchone()["is_deleted"] == 1
        cur.execute("SELECT COUNT(*) AS n FROM user_facts WHERE user_id = ? AND is_deleted = 1", (uid,))
        assert cur.fetchone()["n"] == 1
        cur.execute("SELECT is_deleted FROM user_profiles WHERE user_id = ?", (other,))
        assert cur.fetchone()["is_deleted"] == 0
        cur.execute("SELECT COUNT(*) AS n FROM user_facts WHERE user_id = ? AND is_deleted = 0", (other,))
        assert cur.fetchone()["n"] == 1
    finally:
        conn.close()

    # 2) relations：匿名化＋軟刪
    assert uid not in mgr.store
    conn = get_db_connection(tmp["mem_db"])
    try:
        cur = conn.cursor()
        cur.execute("SELECT affinity, is_deleted FROM user_relations WHERE user_id = ?", (uid,))
        row = cur.fetchone()
        assert row["is_deleted"] == 1
        assert float(row["affinity"]) == 0.0
    finally:
        conn.close()

    # 3) chat_messages：標記軟刪、他人訊息保留
    conn = get_db_connection(tmp["chat_db"])
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM chat_messages WHERE user_id = ? AND is_deleted = 1", (uid,))
        assert cur.fetchone()["n"] == 2
        cur.execute("SELECT COUNT(*) AS n FROM chat_messages WHERE user_id = ? AND is_deleted = 0", (other,))
        assert cur.fetchone()["n"] == 1
    finally:
        conn.close()

    # 4) JSON/JSONL 檔案：僅移除當事人內容（原子替換、檔案仍有效）
    memory_json = json.loads((tmp["data_dir"] / "user_memory.json").read_text(encoding="utf-8"))
    assert uid not in memory_json and other in memory_json
    relations_json = json.loads((tmp["data_dir"] / "user_relations.json").read_text(encoding="utf-8"))
    assert uid not in relations_json and other in relations_json
    for rel in ("chat_logs/2026-10-18.jsonl", "archive_legacy_json/chat_logs/2026-10-18.jsonl"):
        lines = (tmp["data_dir"] / rel).read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        assert uid not in lines[0] and other in lines[0]

    # 5) 稽核：admin_audit 留痕且不落原始 UID（遮罩）
    audit_text = (tmp["data_dir"] / "admin_audit.jsonl").read_text(encoding="utf-8")
    assert "USER_DATA_PURGE" in audit_text
    assert uid not in audit_text

    # 6) 重載後不可檢索
    reloaded = load_user_memory(tmp["data_dir"] / "user_memory.json")
    assert uid not in reloaded


# ---------------------------------------------------------------------------
# 2. 部分失敗語意
# ---------------------------------------------------------------------------
def test_forget_partial_failure_semantics(
    isolated_memory: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(user_id: object) -> None:
        raise RuntimeError("db locked")

    monkeypatch.setattr(mem_storage, "soft_delete_all", _boom)

    results = asyncio.run(mem_storage.purge_user_data("111", data_dir=isolated_memory["data_dir"]))
    assert results["ok"] is False
    assert results["scopes"]["profiles_facts"]["ok"] is False
    assert "RuntimeError" in results["scopes"]["profiles_facts"]["error"]

    message = mem_storage.format_forget_result(results)
    assert "部分" in message
    assert "已成功清除" not in message

    audit_text = (isolated_memory["data_dir"] / "admin_audit.jsonl").read_text(encoding="utf-8")
    assert "partial" in audit_text


def test_format_forget_result_success_message() -> None:
    message = mem_storage.format_forget_result({"ok": True, "scopes": {}})
    assert "已成功清除雪莉對你的所有長期記憶特徵" in message


# ---------------------------------------------------------------------------
# 3. 寫入模型：UPSERT／唯一索引／同步刪除
# ---------------------------------------------------------------------------
def test_no_full_table_delete_from_user_facts() -> None:
    src = Path(mem_storage.__file__).read_text(encoding="utf-8")
    assert not re.search(r"DELETE FROM user_facts(?!\s+WHERE)", src), "不得再出現全表 DELETE FROM user_facts"


def test_upsert_preserves_soft_deleted_facts_and_dedupes(isolated_memory: dict[str, Path]) -> None:
    uid = "333"
    store_full = {
        uid: {
            "aliases": [],
            "facts": [
                make_fact("事實A", importance=4, category="preference"),
                make_fact("事實B", importance=3, category="preference"),
            ],
        }
    }
    save_user_memory(store_full, None)

    # 1) 全庫快照缺 A：A 差異化軟刪（保留軌跡），B 維持現役
    snapshot = {uid: {"aliases": [], "facts": [make_fact("事實B", importance=3, category="preference")]}}
    save_user_memory(snapshot, None)

    conn = get_db_connection(isolated_memory["mem_db"])
    try:
        cur = conn.cursor()
        cur.execute("SELECT is_deleted FROM user_facts WHERE user_id = ? AND fact = ?", (uid, "事實A"))
        row_a = cur.fetchone()
        assert row_a is not None, "軟刪軌跡應保留（不得硬刪）"
        assert row_a["is_deleted"] == 1, "快照中缺席的事實應差異化軟刪"
        cur.execute("SELECT is_deleted FROM user_facts WHERE user_id = ? AND fact = ?", (uid, "事實B"))
        assert cur.fetchone()["is_deleted"] == 0
    finally:
        conn.close()

    # 2) 使用者級遺忘後，全庫快照（含 B）仍不得復活（nightly 不復活）
    mem_storage.soft_delete_all(uid)
    save_user_memory(snapshot, None)

    conn = get_db_connection(isolated_memory["mem_db"])
    try:
        cur = conn.cursor()
        cur.execute("SELECT is_deleted FROM user_facts WHERE user_id = ? AND fact = ?", (uid, "事實B"))
        assert cur.fetchone()["is_deleted"] == 1, "快照寫入不得讓已遺忘的事實復活"
    finally:
        conn.close()

    # 3) 單一使用者增量（實際重新學到 A）→ 允許復活、更新權重、不產生重複列
    profile = {"aliases": [], "facts": [make_fact("事實A", importance=5, category="preference")]}
    save_single_user_memory(uid, profile, {uid: profile}, None)

    conn = get_db_connection(isolated_memory["mem_db"])
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n, MAX(importance) AS imp FROM user_facts WHERE user_id = ? AND fact = ?", (uid, "事實A"))
        row = cur.fetchone()
        assert row["n"] == 1, "唯一索引應防止重複列"
        assert row["imp"] == 5
        cur.execute("SELECT is_deleted FROM user_facts WHERE user_id = ? AND fact = ?", (uid, "事實A"))
        assert cur.fetchone()["is_deleted"] == 0
    finally:
        conn.close()


def test_unique_index_and_dedupe_migration(isolated_memory: dict[str, Path]) -> None:
    mem_db = isolated_memory["mem_db"]
    mem_db.parent.mkdir(parents=True, exist_ok=True)
    with db_transaction(mem_db) as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                fact TEXT NOT NULL,
                category TEXT NOT NULL,
                importance INTEGER NOT NULL,
                is_deleted INTEGER NOT NULL DEFAULT 0,
                deleted_at TEXT DEFAULT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            )
        """)
        cur.execute("INSERT INTO user_facts (user_id, fact, category, importance, is_deleted) VALUES ('u1', '同一件事', 'preference', 3, 1)")
        cur.execute("INSERT INTO user_facts (user_id, fact, category, importance, is_deleted) VALUES ('u1', '同一件事', 'preference', 4, 0)")

    mem_storage._init_memory_db()

    with db_transaction(mem_db) as cur:
        cur.execute("SELECT id, importance, is_deleted FROM user_facts WHERE user_id = 'u1' AND fact = '同一件事'")
        rows = cur.fetchall()
        assert len(rows) == 1, "去重遷移應合併同 (user_id, fact) 的重複列"
        assert rows[0]["importance"] == 4 and rows[0]["is_deleted"] == 0, "應保留最新（id 最大）列"

        cur.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND name = 'idx_user_fact'")
        assert cur.fetchone() is not None, "缺少 (user_id, fact) 唯一索引"


def test_delete_user_memory_is_synchronous_and_traced(isolated_memory: dict[str, Path]) -> None:
    src = Path(mem_storage.__file__).read_text(encoding="utf-8")
    assert "run_in_executor(None, _do_sqlite_delete)" not in src, "刪除路徑不得 fire-and-forget"

    uid = "444"
    save_user_memory({uid: {"aliases": [], "facts": [make_fact("要刪的事", importance=3, category="preference")]}}, None)
    delete_user_memory(uid)

    conn = get_db_connection(isolated_memory["mem_db"])
    try:
        cur = conn.cursor()
        cur.execute("SELECT is_deleted, deleted_at FROM user_facts WHERE user_id = ?", (uid,))
        row = cur.fetchone()
        assert row["is_deleted"] == 1, "呼叫返回時即應完成軟刪（同步語意）"
        assert row["deleted_at"], "軟刪需留下 deleted_at 軌跡"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 4. 指令／管理路徑接線（靜態守門）
# ---------------------------------------------------------------------------
def test_admin_clear_paths_use_soft_delete_api() -> None:
    src = (_REPO_ROOT / "handlers" / "admin" / "cog.py").read_text(encoding="utf-8")
    assert "save_user_memory" not in src
    assert "purge_user_data" in src


def test_forget_command_uses_purge_pipeline() -> None:
    src = (_REPO_ROOT / "handlers" / "memory" / "cog.py").read_text(encoding="utf-8")
    assert "purge_user_data" in src
    assert "delete_user_memory(user_id)" not in src
    assert "_working_memory_buffers.pop" in src, "清除時需同步丟棄 RAM 工作緩衝區（防再度抽取復活）"


# ---------------------------------------------------------------------------
# 5. 子範圍單元：relations／chat_messages
# ---------------------------------------------------------------------------
def test_relationship_manager_soft_delete(isolated_memory: dict[str, Path]) -> None:
    mgr = rel_module.RelationshipManager()
    mgr.store["u9"] = {"affinity": 90.0, "tier": "close", "interaction_count": 9, "last_interaction": "2026-10-01"}
    mgr._do_write_sync(dict(mgr.store))

    mgr.soft_delete_user("u9")
    assert "u9" not in mgr.store

    conn = get_db_connection(isolated_memory["mem_db"])
    try:
        cur = conn.cursor()
        cur.execute("SELECT affinity, is_deleted FROM user_relations WHERE user_id = 'u9'")
        row = cur.fetchone()
        assert row["is_deleted"] == 1
        assert float(row["affinity"]) == 0.0
    finally:
        conn.close()


def test_chat_messages_soft_delete_and_read_filter(isolated_memory: dict[str, Path]) -> None:
    uid, other = "555", "666"
    _seed_chat_messages(
        isolated_memory["chat_db"],
        [(uid, "2026-10-18", "要被刪的1"), (uid, "2026-10-18", "要被刪的2"), (other, "2026-10-18", "保留")],
    )

    removed = chat_logger.soft_delete_chat_messages(uid)
    assert removed == 2

    records = chat_logger.read_chat_log("2026-10-18")
    assert [r["user_id"] for r in records] == [other]
