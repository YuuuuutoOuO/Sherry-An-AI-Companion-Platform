"""
tests/unit/test_architecture_optimization.py
============================================
驗證 Claude 最終仲裁優化項目之單元測試：
1. P1-A: draw_tool 與 BotBrain sd_generating property setter 協調機制
2. P1-B: Core 與 CloudInferenceProtocol 依賴解耦與建構子注入
3. P1-C: core/db checkpoint_all_dbs 關機資料落盤
4. P2-A: CoreGuard 移除未接線死碼 _gpu_lock
5. N4: MarketRepository 獨立封裝與 market_plugin/cog.py 分層乾淨度
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.contracts import SherryRequest
from core.db import checkpoint_all_dbs, get_db_connection
from core.guard import CoreGuard
from app.orchestrator import SherryCore
from handlers.bot_brain.cog import BotBrain
from mod.market_plugin.repository import MarketRepository


class TestSDEventPropertySetter:
    """測試 P1-A: BotBrain sd_generating property setter 正確驅動 _sd_event 狀態。"""

    def test_setter_clears_and_sets_event(self) -> None:
        """驗證當 sd_generating 為 True 時 clear event，為 False 時 set event。"""
        mock_bot = MagicMock()
        brain = BotBrain(mock_bot)

        # 初始狀態：SD 未在生圖，event 應為 set (可推論)
        assert brain.sd_generating is False
        assert brain._sd_event.is_set() is True

        # 設定正在生圖 -> 應 clear event (阻塞本地推論)
        brain.sd_generating = True
        assert brain.sd_generating is True
        assert brain._sd_event.is_set() is False

        # 生圖完畢 -> 應 set event (恢復本地推論)
        brain.sd_generating = False
        assert brain.sd_generating is False
        assert brain._sd_event.is_set() is True


class TestCoreArchitectureDecoupling:
    """測試 P1-B: SherryCore 依賴反轉與介面協定注入。"""

    def test_cloud_client_dependency_injection(self) -> None:
        """驗證 SherryCore 支援透過建構子注入自訂 CloudInferenceProtocol 實例。"""
        async def _run() -> None:
            mock_cloud_client = AsyncMock()
            mock_cloud_client.summarize_text.return_value = "測試結構化摘要回覆"

            core = SherryCore(bot=None, cloud_client=mock_cloud_client)
            assert core.cloud_client is mock_cloud_client

            # 派發 CONTEXT_SUMMARIZE
            req = SherryRequest(
                trace_id="test-ctx-01",
                source_type="context_menu",
                user_id=12345,
                user_name="Tester",
                guild_id=10,
                channel_id=67890,
                raw_content="長篇待摘要文章",
                options={"action": "summarize"},
            )
            resp = await core.handle_request(req)
            assert resp.success is True
            assert "測試結構化摘要回覆" in resp.reply_text
            mock_cloud_client.summarize_text.assert_awaited_once()

        asyncio.run(_run())

    def test_core_has_no_static_mod_imports(self) -> None:
        """靜態驗證 core 模組中無任何頂層 static 'from mod.' 或 'import mod.'。"""
        import app.orchestrator
        import core.contracts
        import core.dispatcher
        import core.guard
        import core.registry

        for mod in (app.orchestrator, core.contracts, core.dispatcher, core.guard, core.registry):
            source = inspect.getsource(mod)
            for line in source.splitlines():
                stripped = line.strip()
                # 排除函式內部延遲載入或註解
                if (stripped.startswith("from mod.") or stripped.startswith("import mod.")) and not line.startswith(" "):
                    pytest.fail(f"模組 {mod.__name__} 存在頂層反向依賴: {line}")


class TestCoreGuardDeadCodeCleanup:
    """測試 P2-A: CoreGuard 死碼清理。"""

    def test_gpu_lock_removed(self) -> None:
        """確認 CoreGuard 已移除未接線死碼 _gpu_lock。"""
        guard = CoreGuard()
        assert not hasattr(guard, "_gpu_lock"), "CoreGuard._gpu_lock 應已作為死碼移除"

    def test_guard_rate_limit_and_tool_loop_still_work(self) -> None:
        """確認 CoreGuard 核心安全限流與死循環熔斷功能正常運作。"""
        guard = CoreGuard()
        guard.clear_user_history(999)

        # 正常呼叫通過
        ok, wait = guard.check_rate_limit(user_id=999, max_calls=2, window_sec=10.0)
        assert ok is True
        assert wait == 0

        # 工具循環熔斷檢查
        assert guard.check_tool_loop(user_id=999, tool_name="SEARCH", tool_input="query1", max_consecutive=3) is False
        assert guard.check_tool_loop(user_id=999, tool_name="SEARCH", tool_input="query1", max_consecutive=3) is False
        # 第 3 次相同輸入觸發熔斷
        assert guard.check_tool_loop(user_id=999, tool_name="SEARCH", tool_input="query1", max_consecutive=3) is True


class TestDBCleanupCheckpoint:
    """測試 P1-C: core/db checkpoint_all_dbs 關機落盤。"""

    def test_checkpoint_all_dbs_executes_cleanly(self, tmp_path: Path) -> None:
        """驗證 checkpoint_all_dbs 能對已註冊的 WAL 資料庫順利執行 TRUNCATE checkpoint。"""
        test_db = tmp_path / "test_checkpoint.db"
        conn = get_db_connection(test_db, wal_mode=True)
        conn.execute("CREATE TABLE sample (id INT, text TEXT);")
        conn.execute("INSERT INTO sample VALUES (1, 'hello');")
        conn.commit()
        conn.close()

        # 執行 checkpoint_all_dbs 應無異常並成功刷盤
        checkpoint_all_dbs()


class TestMarketPluginCleanArchitecture:
    """測試 N4: MarketRepository 獨立封裝與 market_plugin 分層乾淨度。"""

    def test_repository_watchlists_and_cards(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """驗證 MarketRepository 完整封裝 watchlist 與 cards 之讀寫。"""
        test_db = tmp_path / "test_market.db"
        monkeypatch.setattr("mod.market_plugin.config.DB_PATH", test_db)
        monkeypatch.setattr("mod.market_plugin.config.DATA_DIR", tmp_path)

        repo = MarketRepository()

        # 1. Watchlists
        default_wl = repo.load_watchlists()
        assert "ai_list" in default_wl
        assert "NVDA" in default_wl["ai_list"]

        new_wl = {"ai_list": ["TSM", "AAPL"], "raw_list": [], "etf_list": ["0050.TW"]}
        saved = repo.save_watchlists(new_wl)
        assert saved is True

        loaded_wl = repo.load_watchlists()
        assert loaded_wl["ai_list"] == ["TSM", "AAPL"]
        assert loaded_wl["etf_list"] == ["0050.TW"]

        # 2. Cards
        cards_data = {"皮卡丘 25週年": {"url": "https://example.com/card1", "last_prices": {"PSA10": 15000}}}
        saved_card = repo.save_cards(cards_data)
        assert saved_card is True

        loaded_cards = repo.load_cards()
        assert "皮卡丘 25週年" in loaded_cards
        assert loaded_cards["皮卡丘 25週年"]["last_prices"]["PSA10"] == 15000

    def test_market_cog_does_not_import_sqlite3(self) -> None:
        """靜態檢驗 mod/market_plugin/cog.py 完全不 import sqlite3。"""
        from mod.market_plugin import cog as market_cog_mod

        source = inspect.getsource(market_cog_mod)
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("import sqlite3") or stripped.startswith("from sqlite3"):
                pytest.fail(f"market_plugin/cog.py 不應 import sqlite3: {line}")
