"""
tests/unit/test_sqlite_migration_and_repositories.py
====================================================
針對全系統 SQLite 基礎建設、外掛模組倉儲與遷移工具的完整單元測試。
"""

from __future__ import annotations

import pytest
from pathlib import Path

from core.db import get_db_connection, db_transaction
from mod.stock_trader.domain.models import (
    PortfolioAccount, StockPosition, ThoughtLog, TradeRecord,
)
from mod.stock_trader.repository import StockRepository
from handlers.memory.storage import UserProfile, UserFact
from handlers.memory.relationship import RelationshipManager
from mod.dnd.models import Character, CharacterStats, Inventory, GameTime
from mod.dnd import storage as dnd_storage

from mod.deep_search.domain.models import ResearchSession
from mod.deep_search.repositories.session_repository import SqliteSessionRepository
from mod.feed_sentinel.models import SharedPost
from mod.feed_sentinel.repository import SentinelRepository
from handlers.spark.models import Trigger
from handlers.spark.repository import SparkRepository
from handlers import schedule_storage
from handlers.vibe_manager import storage as vibe_storage
from handlers.roast.manager import RoastManager


class TestCoreDB:
    """測試 core/db.py 基礎連線與交易管理。"""

    def test_db_connection_and_wal(self, tmp_path: Path) -> None:
        db_file = tmp_path / "test.db"
        conn = get_db_connection(db_file)
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode;")
        row = cur.fetchone()
        assert row[0].upper() == "WAL"
        conn.close()

    def test_db_transaction_commit_and_rollback(self, tmp_path: Path) -> None:
        db_file = tmp_path / "test_tx.db"
        with db_transaction(db_file) as cur:
            cur.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);")
            cur.execute("INSERT INTO users (name) VALUES ('Alice');")

        conn = get_db_connection(db_file)
        cur = conn.cursor()
        cur.execute("SELECT name FROM users WHERE id = 1;")
        assert cur.fetchone()[0] == "Alice"
        conn.close()

        # 測試 Rollback
        with pytest.raises(RuntimeError):
            with db_transaction(db_file) as cur:
                cur.execute("INSERT INTO users (name) VALUES ('Bob');")
                raise RuntimeError("Force Rollback")

        conn = get_db_connection(db_file)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users;")
        assert cur.fetchone()[0] == 1  # Bob 不應被寫入
        conn.close()


class TestStockRepositorySQLite:
    """測試 mod/stock_trader/repository.py SQLite 實作。"""

    def test_portfolio_save_and_load(self, tmp_path: Path) -> None:
        repo = StockRepository(data_dir=tmp_path)
        account = PortfolioAccount(
            cash=80000.0,
            initial_capital=100000.0,
            positions={
                "NVDA": StockPosition(ticker="NVDA", shares=10.0, avg_cost=120.0, current_price=130.0)
            },
            realized_pnl=500.0,
            total_trades_count=2,
            winning_trades_count=2,
            created_at="2026-08-30 12:00:00",
            updated_at="2026-08-30 12:00:00",
        )
        assert repo.save_portfolio(account) is True

        loaded = repo.load_portfolio()
        assert loaded.cash == 80000.0
        assert "NVDA" in loaded.positions
        assert loaded.positions["NVDA"].shares == 10.0
        assert loaded.positions["NVDA"].avg_cost == 120.0

    def test_trade_and_thought_append(self, tmp_path: Path) -> None:
        repo = StockRepository(data_dir=tmp_path)
        trade = TradeRecord(
            trade_id="t1",
            timestamp="2026-08-30 12:00:00",
            ticker="AAPL",
            action="BUY",
            shares=5.0,
            execution_price=220.0,
            fee=0.0,
            total_amount=1100.0,
            realized_pnl=0.0,
            reason="Breakout",
        )
        assert repo.append_trade(trade) is True
        trades = repo.load_trades(limit=10)
        assert len(trades) == 1
        assert trades[0].ticker == "AAPL"

        thought = ThoughtLog(
            timestamp="2026-08-30 12:05:00",
            trigger_type="ROUTINE",
            thought_process="Looking good",
            decision_summary="Hold all",
            recommended_actions=["HOLD"],
        )
        assert repo.append_thought(thought) is True
        thoughts = repo.load_thoughts(limit=10)
        assert len(thoughts) == 1
        assert thoughts[0].decision_summary == "Hold all"


class TestMemoryStorageSQLite:
    """測試 handlers/memory/storage.py 與 relationship.py。"""

    def test_memory_storage_roundtrip(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import handlers.memory.storage as mem_storage
        monkeypatch.setattr(mem_storage, "MEMORY_DB_PATH", tmp_path / "sherry_memory.db")

        store = {
            "user_123": UserProfile(
                aliases=["小明", "Ming"],
                facts=[
                    UserFact(fact="喜歡喝無糖綠", category="habit", importance=5, updated_at="2026-08-30T00:00:00"),
                    UserFact(fact="養了一隻橘貓", category="identity", importance=4, updated_at="2026-08-30T00:00:00"),
                ],
            )
        }
        json_path = tmp_path / "user_memory.json"
        mem_storage.save_user_memory(store, json_path)

        loaded = mem_storage.load_user_memory(json_path)
        assert "user_123" in loaded
        assert "小明" in loaded["user_123"]["aliases"]
        assert len(loaded["user_123"]["facts"]) == 2
        assert loaded["user_123"]["facts"][0]["importance"] >= loaded["user_123"]["facts"][1]["importance"]

    def test_relationship_manager(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import handlers.memory.relationship as rel_module
        monkeypatch.setattr(rel_module, "MEMORY_DB_PATH", tmp_path / "sherry_memory.db")
        monkeypatch.setattr(rel_module.settings, "USER_RELATIONS_PATH", tmp_path / "user_relations.json")

        mgr = RelationshipManager()
        mgr.store["user_999"] = {
            "affinity": 85.0,
            "tier": "close",
            "interaction_count": 12,
            "last_interaction": "2026-08-30T12:00:00",
            "updated_at": "2026-08-30T12:00:00",
        }
        mgr._do_write_sync(mgr.store)

        mgr2 = RelationshipManager()
        assert "user_999" in mgr2.store
        assert mgr2.store["user_999"]["affinity"] == 85.0


class TestDNDStorageSQLite:
    """測試 mod/dnd/storage.py SQLite 實作。"""

    def test_dnd_world_and_characters(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dnd_storage, "DND_DB_PATH", tmp_path / "dnd.db")
        monkeypatch.setattr(dnd_storage, "WORLD_FILE", tmp_path / "dnd_world.json")
        monkeypatch.setattr(dnd_storage, "CHARACTERS_FILE", tmp_path / "dnd_characters.json")
        monkeypatch.setattr(dnd_storage, "HISTORY_FILE", tmp_path / "dnd_history.jsonl")

        world = dnd_storage.load_world_state()
        world.game_time = GameTime(year=1250, month=5, day=20, hour=10, minute=30)
        dnd_storage.save_world_state(world)

        reloaded_world = dnd_storage.load_world_state()
        assert reloaded_world.game_time.year == 1250

        char = Character(
            user_id=12345,
            name="亞瑟",
            race="人類",
            character_class="聖騎士",
            level=3,
            experience=1500,
            hp_max=30,
            hp_current=28,
            ac=16,
            stats=CharacterStats(strength=16, dexterity=12, constitution=14, intelligence=10, wisdom=12, charisma=14),
            inventory=Inventory(items=[], gold=50, silver=10, copper=0),
            location_id="start_village",
            created_at="2026-08-30",
        )
        dnd_storage.save_characters({12345: char})

        chars = dnd_storage.load_characters()
        assert 12345 in chars
        assert chars[12345].name == "亞瑟"
        assert chars[12345].inventory.gold == 50


class TestDeepSearchSQLite:
    """測試 mod/deep_search/repositories/session_repository.py SQLite 實作。"""

    def test_deep_search_crud(self, tmp_path: Path) -> None:
        db_file = tmp_path / "deep_search.db"
        repo = SqliteSessionRepository(db_path=db_file)
        session = ResearchSession(
            session_id="rs-001",
            topic="量子運算與人工智慧",
            model="gemini-2.5-pro",
            status="COMPLETED",
            progress_pct=100,
            started_at="2026-08-30 12:00:00",
            finished_at="2026-08-30 12:10:00",
        )
        assert repo.save_session(session) is True

        loaded = repo.load_session("rs-001")
        assert loaded is not None
        assert loaded.topic == "量子運算與人工智慧"
        assert loaded.status == "COMPLETED"

        sessions = repo.list_sessions()
        assert len(sessions) == 1

        assert repo.delete_session("rs-001") is True
        assert repo.load_session("rs-001") is None


class TestFeedSentinelSQLite:
    """測試 mod/feed_sentinel/repository.py SQLite 實作。"""

    def test_feed_sentinel_crud(self, tmp_path: Path) -> None:
        db_file = tmp_path / "feed_sentinel.db"
        repo = SentinelRepository(db_path=db_file)
        post = SharedPost(
            post_id="post-123",
            source="HuggingFace",
            title="Llama-3 Fine-tuning Guide",
            url="https://huggingface.co/blog/llama3",
            original_summary="A comprehensive guide to fine-tuning.",
            sherry_analysis="值得參考的好文章！",
            author_or_repo="meta",
            tags=["LLM", "Fine-tuning"],
            canonical_keys=["url:https://huggingface.co/blog/llama3"],
            published_at="2026-08-30 10:00:00",
            posted_at="2026-08-30 10:05:00",
        )
        repo.save_post(post)

        posts = repo.load_posts()
        assert len(posts) == 1
        assert posts[0].title == "Llama-3 Fine-tuning Guide"
        assert repo.is_duplicate(post) is True

        stats = repo.get_stats()
        assert stats["total_posts"] == 1
        assert stats["source_counts"]["huggingface"] == 1

        assert repo.delete_post("post-123") is True
        assert len(repo.load_posts()) == 0


class TestSparkRepositorySQLite:
    """測試 handlers/spark/repository.py SQLite 實作。"""

    def test_spark_crud(self, tmp_path: Path) -> None:
        db_file = tmp_path / "spark.db"
        repo = SparkRepository(db_path=db_file)
        trigger = Trigger(
            trigger_id="trig-100",
            user_id=123456,
            channel_id=987654,
            trigger_type="price",
            condition={"ticker": "NVDA", "target_price": 150.0},
            message="NVDA 目標價 150 警報",
            is_active=True,
        )
        saved = repo.save(trigger)
        assert saved.trigger_id == "trig-100"

        loaded = repo.get("trig-100")
        assert loaded is not None
        assert loaded.message == "NVDA 目標價 150 警報"
        assert loaded.is_active is True

        active_list = repo.list_active()
        assert len(active_list) == 1

        assert repo.delete("trig-100") is True
        assert repo.get("trig-100") is None


class TestScheduleAndRulesSQLite:
    """測試 schedule_storage, vibe_storage 與 RoastManager SQLite 實作。"""

    @pytest.mark.anyio
    @pytest.mark.parametrize("anyio_backend", ["asyncio"])
    async def test_schedule_crud(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, anyio_backend: str) -> None:
        monkeypatch.setattr(schedule_storage, "DB_PATH", tmp_path / "schedules.db")
        schedule_storage._init_schedule_db()

        task = await schedule_storage.add_task(
            user_id="user_777",
            channel_id="ch_123",
            schedule_type="once",
            content="記得吃下午茶",
            display_name="Yuuto",
            trigger_time=1700000000,
        )
        assert task["user_id"] == "user_777"

        user_tasks = await schedule_storage.load_tasks_by_user("user_777")
        assert len(user_tasks) == 1
        assert user_tasks[0]["content"] == "記得吃下午茶"

        purged = await schedule_storage.purge_once_tasks_before(1700000005)
        assert len(purged) == 1

    def test_vibe_and_roast(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(vibe_storage, "MEMORY_DB_PATH", tmp_path / "sherry_memory.db")
        vibe_storage._init_vibe_db()

        store = {"ch_1": {"動漫": {"summary": "討論新番", "weight": 5}}}
        vibe_storage.save_vibe_store(store, tmp_path / "dummy.json")
        loaded_vibe = vibe_storage.load_vibe_store(tmp_path / "dummy.json")
        assert "ch_1" in loaded_vibe
        assert loaded_vibe["ch_1"]["動漫"]["weight"] == 5

        # Roast
        import handlers.roast.manager as rm_mod
        monkeypatch.setattr(rm_mod, "MEMORY_DB_PATH", tmp_path / "sherry_memory.db")
        roast_mgr = RoastManager(file_path=tmp_path / "roast_rules.json")
        cfg = roast_mgr.get_config()
        assert cfg.enabled is True


class TestAdminAuditManager:
    """測試 mod/audit_manager.py 管理員審計管理器與 SQLite 自動升級機制。"""

    def test_audit_manager_fresh_db(self, tmp_path: Path) -> None:
        db_file = tmp_path / "chat_history.db"
        jsonl_file = tmp_path / "admin_audit.jsonl"
        from mod.audit_manager import AdminAuditManager

        mgr = AdminAuditManager(log_path=jsonl_file, db_path=db_file)
        login_rec = mgr.record_login(
            session_id="sess-12345",
            username="admin_user",
            ip="127.0.0.1",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        )
        assert login_rec["session_id"] == "sess-12345"
        assert login_rec["status"] == "active"

        mgr.record_action("sess-12345", "修改系統設定", "更新 API Token")
        mgr.record_logout("sess-12345")

        history = mgr.get_audit_history(limit=10)
        assert len(history) >= 1
        sess_rec = next((r for r in history if r.get("session_id") == "sess-12345"), None)
        assert sess_rec is not None
        assert sess_rec["status"] == "logged_out"
        assert len(sess_rec.get("actions", [])) == 1
        assert sess_rec["actions"][0]["action"] == "修改系統設定"

    def test_audit_manager_legacy_table_upgrade(self, tmp_path: Path) -> None:
        """測試當 audit_logs 資料表為舊版且缺少 session_id 等欄位時，能自動升級而不拋出例外。"""
        db_file = tmp_path / "legacy_chat_history.db"
        jsonl_file = tmp_path / "legacy_audit.jsonl"

        # 模擬舊版只有 timestamp 與 level 的 audit_logs 表
        with db_transaction(db_file) as cur:
            cur.execute("""
                CREATE TABLE audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    level TEXT,
                    event TEXT
                )
            """)

        from mod.audit_manager import AdminAuditManager

        mgr = AdminAuditManager(log_path=jsonl_file, db_path=db_file)
        mgr.record_login(
            session_id="legacy-sess-001",
            username="legacy_admin",
            ip="192.168.1.100",
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        )
        mgr.record_action("legacy-sess-001", "系統操作", "重啟機器人")
        mgr.record_logout("legacy-sess-001")

        history = mgr.get_audit_history(limit=10)
        assert len(history) == 1
        assert history[0]["session_id"] == "legacy-sess-001"
        assert history[0]["status"] == "logged_out"
        assert len(history[0]["actions"]) == 1

