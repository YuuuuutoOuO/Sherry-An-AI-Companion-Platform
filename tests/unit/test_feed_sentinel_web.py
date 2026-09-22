"""
tests/unit/test_feed_sentinel_web.py
====================================
AI 社群情報哨兵 (mod.feed_sentinel) Web UI 與 REST API 藍圖測試。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest
from quart import Quart

from mod.feed_sentinel import MOD_INFO, config
from mod.feed_sentinel.models import SharedPost
from mod.feed_sentinel.repository import SentinelRepository
from mod.feed_sentinel.web import feed_sentinel_bp, set_bot_instance


def test_feed_sentinel_mod_info() -> None:
    """驗證 MOD_INFO 具備所有動態探索與 Web UI 所需之元資料。"""
    assert MOD_INFO["id"] == "feed_sentinel"
    assert MOD_INFO["has_web_ui"] is True
    assert MOD_INFO["nav_id"] == "feed-sentinel"
    assert MOD_INFO["script_file"] == "feed_sentinel.js"
    assert "icon" in MOD_INFO
    assert "description" in MOD_INFO


def test_feed_sentinel_web_blueprint_routes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """測試 Web Blueprint 路由端點：/view, /posts, /stats, /trigger, /posts/<id>。"""
    async def _run() -> None:
        test_posts_file = tmp_path / "feed_sentinel" / "posts.json"
        test_seen_file = tmp_path / "feed_sentinel" / "sentinel_seen.json"
        monkeypatch.setattr(config, "DATA_DIR", tmp_path / "feed_sentinel")
        monkeypatch.setattr(config, "POSTS_PATH", test_posts_file)
        monkeypatch.setattr(config, "SEEN_PATH", test_seen_file)

        # 預先存入測試文章
        repo = SentinelRepository(
            db_path=tmp_path / "feed_sentinel" / "feed_sentinel.db",
            posts_path=test_posts_file,
            seen_path=test_seen_file,
        )
        post = SharedPost(
            post_id="test_post_001",
            source="HuggingFace Daily Papers",
            title="Gemma 3 Architecture Report",
            url="https://huggingface.co/papers/2608.12345",
            original_summary="Original English abstract...",
            sherry_analysis="💡 3大重點\n1. 突破\n\n🕵️‍♀️ 雪莉銳評\n很厲害！",
            author_or_repo="Google DeepMind",
            tags=["AI", "Gemma"],
            published_at="2026-08-20",
            posted_at="2026-08-21 10:00:00",
        )
        repo.save_post(post)

        app = Quart(__name__)
        app.register_blueprint(feed_sentinel_bp)
        client = app.test_client()

        # 1. 測試 GET /view
        resp_view = await client.get("/view")
        assert resp_view.status_code == 200
        view_html = await resp_view.get_data(as_text=True)
        assert "view-feed-sentinel" in view_html
        assert "AI 社群情報哨兵主控台" in view_html

        # 2. 測試 GET /api/feed_sentinel/posts
        resp_posts = await client.get("/api/feed_sentinel/posts?page=1&limit=10")
        assert resp_posts.status_code == 200
        data_posts = await resp_posts.get_json()
        assert data_posts["status"] == "success"
        assert data_posts["total"] == 1
        assert len(data_posts["posts"]) == 1
        assert data_posts["posts"][0]["post_id"] == "test_post_001"

        # 3. 測試搜尋與來源篩選
        resp_filter = await client.get("/api/feed_sentinel/posts?source=arXiv")
        data_filter = await resp_filter.get_json()
        assert data_filter["total"] == 0

        resp_search = await client.get("/api/feed_sentinel/posts?q=Gemma")
        data_search = await resp_search.get_json()
        assert data_search["total"] == 1

        # 4. 測試 GET /api/feed_sentinel/stats
        mock_bot = MagicMock()
        mock_cog = MagicMock()
        mock_cog.get_status_info.return_value = {
            "enabled": True,
            "is_processing": False,
            "in_schedule_window": True,
        }
        mock_bot.get_cog.return_value = mock_cog
        set_bot_instance(mock_bot)

        resp_stats = await client.get("/api/feed_sentinel/stats")
        assert resp_stats.status_code == 200
        data_stats = await resp_stats.get_json()
        assert data_stats["status"] == "success"
        assert data_stats["stats"]["total_posts"] == 1
        assert data_stats["stats"]["source_counts"]["huggingface"] == 1
        assert data_stats["sentinel"]["attached"] is True

        # 5. 測試 POST /api/feed_sentinel/trigger
        mock_cog._is_processing = False
        mock_cog._process_feed_and_post = AsyncMock(return_value=True)

        resp_trigger = await client.post("/api/feed_sentinel/trigger")
        assert resp_trigger.status_code == 200
        data_trigger = await resp_trigger.get_json()
        assert data_trigger["status"] == "success"
        mock_cog._process_feed_and_post.assert_called_once_with(force=True)

        # 6. 測試 DELETE /api/feed_sentinel/posts/<post_id>
        resp_del = await client.delete("/api/feed_sentinel/posts/test_post_001")
        assert resp_del.status_code == 200
        data_del = await resp_del.get_json()
        assert data_del["status"] == "success"

        # 再次查詢應為 0
        resp_after_del = await client.get("/api/feed_sentinel/posts")
        data_after_del = await resp_after_del.get_json()
        assert data_after_del["total"] == 0

    asyncio.run(_run())
