"""tests/unit/test_self_memory_and_surfer.py
=============================================
單元測試：雪莉專屬自我記憶（Self-Memory）＋ 網活仔主動衝浪引擎 ＋ Port 8765 實況台 ＋ Port 8763 API 整合。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import settings
from handlers.care.repository import CareRepository, CareSurferConfig
from handlers.care.surfer import CandidateArticle, CareSurfer, SurferBroadcaster
from handlers.care.surfer_server import surfer_app
from handlers.care.web import care_bp
from handlers.memory import self_memory
from handlers.social.persona import build_dynamic_persona
from quart import Quart


@pytest.fixture
def temp_db_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def self_mem_db(temp_db_dir: Path) -> Path:
    db_path = temp_db_dir / "test_self_memory.db"
    self_memory.init_self_memory_db(db_path)
    return db_path


@pytest.fixture
def care_repo(temp_db_dir: Path) -> CareRepository:
    db_path = temp_db_dir / "test_care.db"
    return CareRepository(db_path=db_path)


# ---------------------------------------------------------------------------
# 1. self_memory.py 單元測試
# ---------------------------------------------------------------------------

def test_self_memory_episodes_crud(self_mem_db: Path) -> None:
    # 建立日記與興趣
    eid1 = self_memory.add_self_episode(
        category="diary",
        content="今天和群友聊了好多動漫，心情特別棒！",
        tags=["動漫", "生活"],
        importance=4,
        db_path=self_mem_db,
    )
    eid2 = self_memory.add_self_episode(
        category="interest",
        content="量子運算與邊緣推論",
        tags=["科技"],
        importance=3,
        db_path=self_mem_db,
    )
    assert eid1.startswith("ep-")
    assert eid2.startswith("ep-")

    # 查詢
    all_ep = self_memory.list_self_episodes(db_path=self_mem_db)
    assert len(all_ep) == 2

    diaries = self_memory.list_self_episodes(category="diary", db_path=self_mem_db)
    assert len(diaries) == 1
    assert diaries[0]["content"] == "今天和群友聊了好多動漫，心情特別棒！"
    assert "動漫" in diaries[0]["tags"]

    # 軟刪除
    ok = self_memory.delete_self_episode(eid1, hard_delete=False, db_path=self_mem_db)
    assert ok is True
    assert len(self_memory.list_self_episodes(db_path=self_mem_db)) == 1
    assert len(self_memory.list_self_episodes(include_deleted=True, db_path=self_mem_db)) == 2

    # 硬刪除
    ok_hard = self_memory.delete_self_episode(eid2, hard_delete=True, db_path=self_mem_db)
    assert ok_hard is True
    assert len(self_memory.list_self_episodes(include_deleted=True, db_path=self_mem_db)) == 1


def test_self_memory_reading_log_and_dedup(self_mem_db: Path) -> None:
    url = "https://gnn.gamer.com.tw/detail.php?sn=280001"
    title = "《某知名大作》最新大型改版正式上線"

    assert self_memory.is_article_seen(url, title, db_path=self_mem_db) is False

    rid = self_memory.record_reading_item(
        url=url,
        title=title,
        source="rss",
        summary="介紹大型改版內容與新角色",
        sherry_opinion="這次改版的新角色設計太香了吧！數值看起來也很保值～",
        channel_id="123456",
        tagged_user_id="9999",
        tagged_user_name="Yuuto",
        post_text="大家快看！這改版真的太香了 <@9999>",
        db_path=self_mem_db,
    )
    assert rid is not None
    assert rid.startswith("read-")

    # 重複記錄相同 URL 應回傳 None
    duplicate_rid = self_memory.record_reading_item(
        url=url,
        title="不同標題但相同網址",
        db_path=self_mem_db,
    )
    assert duplicate_rid is None

    # 重複記錄相同標題（即便 URL 帶有不同追蹤參數或不同網址）也應回傳 None（防重複發文）
    duplicate_title_rid = self_memory.record_reading_item(
        url="https://mobile.gamer.com.tw/detail.php?sn=280001",
        title=title,
        db_path=self_mem_db,
    )
    assert duplicate_title_rid is None

    # is_article_seen 去重檢查（支援 URL 追蹤參數標準化）
    assert self_memory.is_article_seen(url, db_path=self_mem_db) is True
    assert self_memory.is_article_seen(f"{url}&utm_source=rss&ref=test/", db_path=self_mem_db) is True
    assert self_memory.is_article_seen("https://other.com", title=title, db_path=self_mem_db) is True
    assert self_memory.is_article_seen("https://other.com", title="完全沒看過的文章", db_path=self_mem_db) is False

    # 列表查詢
    logs = self_memory.list_reading_logs(db_path=self_mem_db)
    assert len(logs) == 1
    assert logs[0]["title"] == title
    assert "這次改版的新角色設計太香了吧" in logs[0]["sherry_opinion"]

    # 刪除閱讀紀錄
    del_ok = self_memory.delete_reading_log(rid, db_path=self_mem_db)
    assert del_ok is True
    assert len(self_memory.list_reading_logs(db_path=self_mem_db)) == 0


def test_get_active_interests_and_snippet(self_mem_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "SELF_MEMORY_DB_PATH", self_mem_db)

    self_memory.add_self_episode(
        category="interest",
        content="太空望遠鏡最新觀測成果",
        tags=["天文", "物理"],
        importance=4,
        db_path=self_mem_db,
    )
    self_memory.add_self_episode(
        category="diary",
        content="深夜喝了杯熱可可，寫程式順暢許多。",
        importance=3,
        db_path=self_mem_db,
    )
    self_memory.record_reading_item(
        url="https://technews.tw/123",
        title="新型固態電池技術突破",
        sherry_opinion="以後手機是不是可以一週不充電了？！太神了吧！",
        db_path=self_mem_db,
    )

    interests = self_memory.get_active_interests(limit=5, db_path=self_mem_db)
    assert "太空望遠鏡最新觀測成果" in interests
    assert "天文" in interests

    # 一般召回
    snippet = self_memory.get_self_memory_snippet(db_path=self_mem_db)
    assert "💭【雪莉的自我心境與生活小劇場】" in snippet
    assert "熱可可" in snippet
    assert "📰【雪莉最近看過的新聞/文章與自身觀點】" in snippet
    assert "新型固態電池技術突破" in snippet
    assert "以後手機是不是可以一週不充電了" in snippet

    # 話題相關性召回排序驗證
    snippet_targeted = self_memory.get_self_memory_snippet(query_text="電池", db_path=self_mem_db)
    assert "固態電池" in snippet_targeted


# ---------------------------------------------------------------------------
# 2. persona.py 動態尾部注入驗證（Prefix Caching 不受影響）
# ---------------------------------------------------------------------------

def test_persona_dynamic_tail_injection(self_mem_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "SELF_MEMORY_DB_PATH", self_mem_db)

    self_memory.add_self_episode(
        category="diary",
        content="今天在測試伺服器測試衝浪功能，大家玩得好開心。",
        db_path=self_mem_db,
    )

    head1, tail1 = build_dynamic_persona("哈囉雪莉！", intent_name="CHAT")
    head2, tail2 = build_dynamic_persona("今天天氣如何？", intent_name="CHAT")

    # head_persona 必須完全靜態恆定，保證 Prefix Caching 100% 命中
    assert head1 == head2
    # tail_lore 包含自我記憶
    assert "💭【雪莉的自我心境與生活小劇場】" in tail1
    assert "今天在測試伺服器測試衝浪功能" in tail1


# ---------------------------------------------------------------------------
# 3. CareRepository 網活仔設定持久化
# ---------------------------------------------------------------------------

def test_care_repository_surfer_config(care_repo: CareRepository) -> None:
    cfg = care_repo.get_surfer_config()
    assert isinstance(cfg, CareSurferConfig)
    assert cfg.sandbox_channel_id == "0"
    assert cfg.enabled is False
    assert len(cfg.rss_feeds) >= 2

    # 更新設定
    updated = care_repo.update_surfer_config(
        sandbox_channel_id="987654321012345678",
        enabled=True,
        interval_minutes=60,
        tag_mode="interest_match",
        search_keywords=["AI 機器人", "獨立遊戲神作"],
    )
    assert updated.sandbox_channel_id == "987654321012345678"
    assert updated.enabled is True
    assert updated.interval_minutes == 60
    assert "獨立遊戲神作" in updated.search_keywords

    # 驗證持久化
    cfg2 = care_repo.get_surfer_config()
    assert cfg2.sandbox_channel_id == "987654321012345678"
    assert cfg2.enabled is True

    # 更新上次執行時間
    care_repo.set_surfer_last_run(now=123456789.0)
    assert care_repo.get_surfer_config().last_run_at == 123456789.0


# ---------------------------------------------------------------------------
# 4. SurferBroadcaster SSE 廣播器
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_surfer_broadcaster_pub_sub() -> None:
    broadcaster = SurferBroadcaster()
    queue = await broadcaster.subscribe()

    # 訂閱時收到初始 snapshot
    init_msg = await queue.get()
    assert "event: state" in init_msg

    # 廣播事件
    await broadcaster.broadcast("browsing", {"url": "https://example.com/test", "step": "navigating"})
    msg = await queue.get()
    assert "event: browsing" in msg
    data = json.loads(msg.split("data: ")[1].strip())
    assert data["url"] == "https://example.com/test"
    assert broadcaster.last_session_snapshot["current_url"] == "https://example.com/test"

    # 退訂
    await broadcaster.unsubscribe(queue)
    assert len(broadcaster._subscribers) == 0


# ---------------------------------------------------------------------------
# 5. CareSurfer 衝浪流程（候選、去重、匹配、推論、寫入記憶）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_care_surfer_flow(care_repo: CareRepository, self_mem_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "SELF_MEMORY_DB_PATH", self_mem_db)

    care_repo.update_surfer_config(
        sandbox_channel_id="1122334455",
        enabled=True,
        interval_minutes=30,
    )

    broadcaster = SurferBroadcaster()
    fake_rss = MagicMock()
    mock_rss_item = MagicMock(
        title="Nintendo Direct 驚喜發表全新薩爾達傳說",
        url="https://gnn.gamer.com.tw/zelda-new",
        source="巴哈姆特 GNN",
        summary="任天堂公開全新作品，帶來開放世界冒險全新機制。",
    )
    fake_rss.fetch_latest_items = AsyncMock(return_value=[mock_rss_item])

    fake_search = MagicMock()
    fake_search.search = AsyncMock(return_value=[
        {"title": "Python 3.13 正式發布釋出 Free-threaded GIL", "url": "https://python.org/313", "snippet": "效能大幅提升"}
    ])

    surfer = CareSurfer(
        repository=care_repo,
        broadcaster=broadcaster,
        rss_client=fake_rss,
        search_client=fake_search,
        self_memory_db_path=self_mem_db,
    )

    # 1. 抓取候選文章
    candidates = await surfer.fetch_candidates()
    assert len(candidates) >= 2
    assert any("薩爾達傳說" in c.title for c in candidates)
    assert any("Python 3.13" in c.title for c in candidates)

    # 2. 群友記憶匹配（驗證真實 UserFact 結構、別名讀取、以及停用詞防誤判）
    fake_bot = MagicMock()
    fake_mem_cog = MagicMock()
    fake_mem_cog.store = {
        "1001": {
            "aliases": ["Yuuto", "阿優"],
            "facts": [
                {"category": "preference", "fact": "熱愛薩爾達傳說系列與開放世界冒險遊戲"}
            ]
        },
        "1002": {
            "aliases": ["路人甲"],
            "facts": [
                {"category": "habit", "fact": "平時喜歡使用各種電腦軟體"}  # 僅有停用詞，不應誤配
            ]
        }
    }

    fake_brain_cog = MagicMock()
    async def _mock_enqueue(task: Any) -> None:
        if task.callback:
            reply_json = json.dumps({
                "selected_index": 0,
                "thought": "薩爾達新作太神啦！",
                "sherry_opinion": "薩爾達新作機制很酷，必玩！",
                "new_interest": "開放世界機制",
                "tagged_user_id": "1001",
                "tagged_user_name": "Yuuto",
                "post_text": "哇！薩爾達新作來了 <@1001> 快看！",
            })
            await task.callback(reply_json)

    fake_brain_cog.enqueue_task = AsyncMock(side_effect=_mock_enqueue)

    def _get_cog(name: str) -> Any:
        if name == "MemoryManager":
            return fake_mem_cog
        if name == "BotBrain":
            return fake_brain_cog
        return None

    fake_bot.get_cog.side_effect = _get_cog

    matches = surfer.match_user_interests(candidates, fake_bot)
    assert len(matches) >= 1
    assert matches[0]["user_id"] == "1001"
    assert matches[0]["user_name"] == "Yuuto"  # 驗證別名正確提取，非預設的 <@1001>
    assert not any(m["user_id"] == "1002" for m in matches)  # 驗證停用詞過濾成功

    # 3. 執行衝浪 (dry_run=True)
    res_dry = await surfer.surf_once(fake_bot, dry_run=True)
    assert res_dry["status"] == "idle"
    assert res_dry["step"] == "completed"
    assert res_dry["dry_run"] is True
    assert "薩爾達傳說" in res_dry["selected"]["title"]

    # 驗證自我記憶已寫入
    readings = self_memory.list_reading_logs(db_path=self_mem_db)
    assert len(readings) == 1
    assert "薩爾達傳說" in readings[0]["title"]
    assert self_memory.is_article_seen(mock_rss_item.url, db_path=self_mem_db) is True

    # 再次抓取候選應自動過濾已看過的文章
    cands_after = await surfer.fetch_candidates()
    assert not any(c.url == mock_rss_item.url for c in cands_after)

    # 4. 驗證 tag_mode="none" 模式下絕不 tag 任何人
    care_repo.update_surfer_config(tag_mode="none")
    res_none = await surfer.surf_once(fake_bot, dry_run=True)
    assert res_none["selected"]["tagged_user_id"] == ""
    assert "<@" not in res_none["post_text"]

    # 5. 執行衝浪 (dry_run=False，測試單一頻道沙盒投放)
    care_repo.update_surfer_config(tag_mode="interest_match")
    mock_rss_item_3 = MagicMock(
        title="開源 AI 助理全新更新",
        url="https://example.com/fresh-ai-post",
        source="TechNews",
        summary="開源 AI 助理推出新版本，支援自我記憶與主動分享。",
    )
    fake_rss.fetch_latest_items = AsyncMock(return_value=[mock_rss_item_3])
    fake_channel = MagicMock()
    fake_channel.send = AsyncMock(return_value=MagicMock(id="msg-999"))
    fake_bot.get_channel.return_value = fake_channel

    res_post = await surfer.surf_once(fake_bot, dry_run=False)
    assert res_post["dry_run"] is False
    assert fake_channel.send.called is True

    # 檢查稽核紀錄
    actions = care_repo.list_actions(limit=5)
    assert len(actions) >= 1
    assert actions[0]["source"] == "surfer"
    assert actions[0]["channel_id"] == "1122334455"


# ---------------------------------------------------------------------------
# 6. Port 8763 Web API 端點測試 (care_bp)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_care_web_api_surfer_and_self_memory(care_repo: CareRepository, self_mem_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "SELF_MEMORY_DB_PATH", self_mem_db)

    app = Quart(__name__)
    app.register_blueprint(care_bp)

    from handlers.care.web import set_engine
    fake_engine = MagicMock()
    fake_engine.repository = care_repo
    set_engine(fake_engine)

    client = app.test_client()

    # GET /api/care/surfer/config
    res = await client.get("/api/care/surfer/config")
    assert res.status_code == 200
    data = await res.get_json()
    assert data["status"] == "success"
    assert "sandbox_channel_id" in data["config"]

    # POST /api/care/surfer/config
    res_post = await client.post("/api/care/surfer/config", json={
        "sandbox_channel_id": "55667788",
        "enabled": True,
        "interval_minutes": 45,
    })
    assert res_post.status_code == 200
    data_post = await res_post.get_json()
    assert data_post["config"]["sandbox_channel_id"] == "55667788"
    assert data_post["config"]["interval_minutes"] == 45

    # POST /api/care/surfer/trigger
    res_trig = await client.post("/api/care/surfer/trigger", json={"dry_run": True})
    assert res_trig.status_code == 200
    data_trig = await res_trig.get_json()
    assert data_trig["status"] == "success"

    # POST /api/care/self-memory (新增 episode)
    res_mem_add = await client.post("/api/care/self-memory", json={
        "type": "episode",
        "category": "diary",
        "content": "WebUI 測試日記條目",
        "tags": ["測試"],
    })
    assert res_mem_add.status_code == 200
    data_mem_add = await res_mem_add.get_json()
    assert data_mem_add["status"] == "success"
    eid = data_mem_add["id"]

    # POST /api/care/self-memory (新增 reading，測試防重複 409)
    res_rd_add = await client.post("/api/care/self-memory", json={
        "type": "reading",
        "url": "https://example.com/unique-art-1",
        "title": "獨特文章測試",
    })
    assert res_rd_add.status_code == 200

    res_rd_dup = await client.post("/api/care/self-memory", json={
        "type": "reading",
        "url": "https://example.com/unique-art-1",
        "title": "獨特文章測試",
    })
    assert res_rd_dup.status_code == 409

    # GET /api/care/self-memory (查詢 episodes)
    res_mem_get = await client.get("/api/care/self-memory?type=episodes")
    assert res_mem_get.status_code == 200
    data_mem_get = await res_mem_get.get_json()
    assert any(item["episode_id"] == eid for item in data_mem_get["items"])

    # DELETE /api/care/self-memory（無 JSON body，僅帶 query string，附帶 Content-Type）
    res_mem_del = await client.delete(
        f"/api/care/self-memory?type=episode&id={eid}",
        headers={"Content-Type": "application/json"}
    )
    assert res_mem_del.status_code == 200
    data_mem_del = await res_mem_del.get_json()
    assert data_mem_del["status"] == "success"


# ---------------------------------------------------------------------------
# 7. Port 8765 實況伺服器端點測試 (surfer_app)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_surfer_server_port_8765_endpoints(care_repo: CareRepository, monkeypatch: pytest.MonkeyPatch) -> None:
    from handlers.care.repository import reset_care_repository
    reset_care_repository()
    monkeypatch.setattr("handlers.care.surfer_server.get_care_repository", lambda: care_repo)

    client = surfer_app.test_client()

    # GET /
    res_index = await client.get("/")
    assert res_index.status_code == 200
    html = await res_index.get_data(as_text=True)
    assert "Sherry NetSurfer" in html

    # GET /api/surfer/state
    res_state = await client.get("/api/surfer/state")
    assert res_state.status_code == 200
    data_state = await res_state.get_json()
    assert data_state["status"] == "success"
    assert "snapshot" in data_state
    assert "config" in data_state

    # POST /api/surfer/trigger
    res_trig = await client.post("/api/surfer/trigger", json={"dry_run": True})
    assert res_trig.status_code == 200
    data_trig = await res_trig.get_json()
    assert data_trig["status"] == "success"


# ---------------------------------------------------------------------------
# 8. 邊界與極端狀況測試 (Edge cases)
# ---------------------------------------------------------------------------

def test_self_memory_edge_cases(self_mem_db: Path) -> None:
    # 空字串 URL 應回傳 None
    assert self_memory.record_reading_item(url="", title="測試", db_path=self_mem_db) is None
    assert self_memory.is_article_seen("", db_path=self_mem_db) is False

    # 刪除不存在的 ID 應回傳 False
    assert self_memory.delete_self_episode("non-existent-id", db_path=self_mem_db) is False
    assert self_memory.delete_reading_log("non-existent-id", db_path=self_mem_db) is False


@pytest.mark.asyncio
async def test_surfer_no_candidates(care_repo: CareRepository, self_mem_db: Path) -> None:
    care_repo.update_surfer_config(enabled=True, active_hour_start=0, active_hour_end=0)
    fake_rss = MagicMock()
    fake_rss.fetch_latest_items = AsyncMock(return_value=[])
    fake_search = MagicMock()
    fake_search.search = AsyncMock(return_value=[])

    surfer = CareSurfer(
        repository=care_repo,
        broadcaster=SurferBroadcaster(),
        rss_client=fake_rss,
        search_client=fake_search,
        self_memory_db_path=self_mem_db,
    )
    result = await surfer.surf_once(None, dry_run=True)
    assert result["status"] == "no_candidates"


@pytest.mark.asyncio
async def test_surfer_server_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CARE_SURFER_TOKEN", "secret-token-12345")
    client = surfer_app.test_client()

    # 無 token 應被攔截
    res_unauth = await client.get("/")
    assert res_unauth.status_code == 401

    # 帶有效 token 應放行
    res_auth = await client.get("/?token=secret-token-12345")
    assert res_auth.status_code == 200
