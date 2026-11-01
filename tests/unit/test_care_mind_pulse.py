"""tests/unit/test_care_mind_pulse.py
======================================
單元測試：防半夜擾民（活躍時段限制）＋ 心智狀態池（社交、好奇、活力）自然與強制遞減 ＋
自發思考（mind_monologue）＋ Web API (/api/care/surfer/mind-tick & config).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from quart import Quart

from handlers.care.repository import CareRepository, CareSurferConfig, is_within_active_hours
from handlers.care.surfer import CareSurfer, get_mind_state, decay_mind_state, replenish_mind_state
from handlers.care.web import care_bp, set_engine
from handlers.memory import self_memory


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def care_repo(temp_dir: Path) -> CareRepository:
    return CareRepository(db_path=temp_dir / "test_care.db")


@pytest.fixture
def self_mem_db(temp_dir: Path) -> Path:
    db = temp_dir / "test_self_mem.db"
    self_memory.init_self_memory_db(db)
    return db


@pytest.fixture
def app():
    app = Quart(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(care_bp)
    return app


@pytest.fixture(autouse=True)
def reset_mind_state():
    from handlers.care.surfer import _mind_state
    _mind_state["social_satiety"] = 72.0
    _mind_state["curiosity_satiety"] = 68.0
    _mind_state["energy_level"] = 90.0
    _mind_state["social_threshold"] = 30.0
    _mind_state["curiosity_threshold"] = 25.0
    _mind_state["last_thought"] = ""
    yield


# ---------------------------------------------------------------------------
# 1. 測試 is_within_active_hours 輔助函式
# ---------------------------------------------------------------------------

def test_is_within_active_hours_normal_daytime():
    """正常日間時段：9:00 ~ 23:00"""
    # 活躍內：9, 12, 22
    assert is_within_active_hours(9, 9, 23) is True
    assert is_within_active_hours(12, 9, 23) is True
    assert is_within_active_hours(22, 9, 23) is True

    # 活躍外：23 (剛好截止), 0 (午夜), 8 (清晨)
    assert is_within_active_hours(23, 9, 23) is False
    assert is_within_active_hours(0, 9, 23) is False
    assert is_within_active_hours(8, 9, 23) is False


def test_is_within_active_hours_overnight():
    """跨夜時段：22:00 ~ 02:00"""
    # 活躍內：22, 23, 0, 1
    assert is_within_active_hours(22, 22, 2) is True
    assert is_within_active_hours(23, 22, 2) is True
    assert is_within_active_hours(0, 22, 2) is True
    assert is_within_active_hours(1, 22, 2) is True

    # 活躍外：2 (截止), 3, 12, 21
    assert is_within_active_hours(2, 22, 2) is False
    assert is_within_active_hours(3, 22, 2) is False
    assert is_within_active_hours(12, 22, 2) is False
    assert is_within_active_hours(21, 22, 2) is False


def test_is_within_active_hours_full_day():
    """全日 24 小時活躍：start == end"""
    for hour in range(24):
        assert is_within_active_hours(hour, 9, 9) is True
        assert is_within_active_hours(hour, 0, 0) is True


def test_is_within_active_hours_boundary_and_normalization():
    """數值範圍保護（小於 0 或大於 23）"""
    assert is_within_active_hours(25, 9, 23) == is_within_active_hours(1, 9, 23)
    assert is_within_active_hours(-1, 22, 2) == is_within_active_hours(23, 22, 2)


# ---------------------------------------------------------------------------
# 2. 測試 CareRepository 對 active_hour_start/end 的存取與持久化
# ---------------------------------------------------------------------------

def test_care_repository_active_hours_persistence(care_repo: CareRepository):
    # 預設值檢查
    cfg = care_repo.get_surfer_config()
    assert cfg.active_hour_start == 9
    assert cfg.active_hour_end == 23

    # 更新為夜貓子模式 20:00 ~ 04:00
    care_repo.update_surfer_config(active_hour_start=20, active_hour_end=4)
    cfg2 = care_repo.get_surfer_config()
    assert cfg2.active_hour_start == 20
    assert cfg2.active_hour_end == 4

    # 檢查 to_dict
    d = cfg2.to_dict()
    assert d["active_hour_start"] == 20
    assert d["active_hour_end"] == 4


# ---------------------------------------------------------------------------
# 3. 測試 CareSurfer 心智狀態池遞減、回補與強制消耗
# ---------------------------------------------------------------------------

def test_mind_state_decay_and_force(care_repo: CareRepository):
    surfer = CareSurfer(care_repo)

    # 取得初始狀態
    initial_state = surfer.get_mind_state(9, 23)
    assert initial_state["social_satiety"] >= 0
    assert initial_state["curiosity_satiety"] >= 0

    # 先補滿
    surfer.replenish_mind_state(social_boost=100.0, curiosity_boost=100.0)
    full_state = surfer.get_mind_state(9, 23)
    assert full_state["social_satiety"] == 100.0
    assert full_state["curiosity_satiety"] == 100.0

    # 強制遞減 20% (社交 -20%, 好奇 -20% * 1.15 = -23%)
    state_after_drop = surfer.decay_mind_state(force_drop=20.0)
    assert state_after_drop["social_satiety"] == 80.0
    assert state_after_drop["curiosity_satiety"] == 77.0

    # 再次強制遞減 100%（下限截斷為 0）
    state_zero = surfer.decay_mind_state(force_drop=100.0)
    assert state_zero["social_satiety"] == 0.0
    assert state_zero["curiosity_satiety"] == 0.0

    # 回補測試：回補社交至 >= 95%
    surfer.replenish_mind_state(replenish_social=True)
    state_replenished = surfer.get_mind_state(9, 23)
    assert state_replenished["social_satiety"] >= 95.0
    assert state_replenished["curiosity_satiety"] == 0.0


# ---------------------------------------------------------------------------
# 4. 測試 tick_mind_pulse 自動觸發心智行為
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tick_mind_pulse_active_hours_curiosity_trigger(care_repo: CareRepository):
    """白天活躍時段：好奇心降至 <= 25% 觸發好奇探索 (curiosity_explore)"""
    surfer = CareSurfer(care_repo)

    surfer.surf_once = AsyncMock(return_value={
        "status": "success",
        "thought": "我想到了好玩的宇宙探秘！",
        "bubbles": ["宇宙真大呀～"],
    })

    # 設定時段為全日活躍
    care_repo.update_surfer_config(enabled=True, active_hour_start=0, active_hour_end=0)

    # 強制心智降至 <= 25% (好奇)，社交保持充足 (> 30%)
    surfer._mind_state["curiosity_satiety"] = 20.0
    surfer._mind_state["social_satiety"] = 90.0

    mock_bot = MagicMock()
    result = await surfer.tick_mind_pulse(mock_bot)

    assert result["status"] == "success"
    assert result.get("triggered_action") == "curiosity_explore"
    assert surfer.surf_once.await_count == 1
    # 觸發後好奇心應被回補至 95% 以上
    assert result["mind_state"]["curiosity_satiety"] >= 95.0


@pytest.mark.asyncio
async def test_tick_mind_pulse_night_silent_curiosity_trigger(care_repo: CareRepository):
    """深夜非活躍時段：好奇心降至 <= 25% 觸發時為 silent_curiosity 模式"""
    surfer = CareSurfer(care_repo)

    surfer.surf_once = AsyncMock(return_value={
        "status": "success",
        "thought": "半夜思考哲學問題，大家都在睡覺就不吵人了...",
        "bubbles": [],
        "silent": True,
    })

    # 設定活躍時段為白天 9~17 點
    care_repo.update_surfer_config(enabled=True, active_hour_start=9, active_hour_end=17)

    surfer._mind_state["curiosity_satiety"] = 15.0
    surfer._mind_state["social_satiety"] = 90.0

    mock_bot = MagicMock()

    # 模擬本地時間為凌晨 3 點 (非活躍時段)
    with patch("handlers.care.surfer.dt") as mock_dt:
        mock_now = MagicMock()
        mock_now.hour = 3
        mock_dt.now.return_value = mock_now

        result = await surfer.tick_mind_pulse(mock_bot)

    assert result["status"] == "success"
    assert result["is_active_hours"] is False
    assert result.get("triggered_action") == "silent_curiosity"
    surfer.surf_once.assert_awaited_once_with(mock_bot, dry_run=False, mode="mind_monologue")
    # 觸發後好奇心應被回補
    assert result["mind_state"]["curiosity_satiety"] >= 95.0


@pytest.mark.asyncio
async def test_tick_mind_pulse_night_freezes_social_trigger(care_repo: CareRepository):
    """深夜非活躍時段：社交飢渴值降至 <= 30% 時，不可發言吵人（凍結發言）"""
    surfer = CareSurfer(care_repo)
    surfer.surf_once = AsyncMock()

    # 設定白天 10~18 點
    care_repo.update_surfer_config(enabled=True, active_hour_start=10, active_hour_end=18)
    surfer._mind_state["social_satiety"] = 10.0
    surfer._mind_state["curiosity_satiety"] = 90.0

    mock_bot = MagicMock()
    with patch("handlers.care.surfer.dt") as mock_dt:
        mock_now = MagicMock()
        mock_now.hour = 2
        mock_dt.now.return_value = mock_now

        result = await surfer.tick_mind_pulse(mock_bot)

    assert result["status"] == "success"
    assert result["is_active_hours"] is False
    # 社交觸發在深夜凍結，故 triggered_action 為 None
    assert result.get("triggered_action") is None
    # 絕不可呼叫 surf_once 發言騷擾 Discord！
    surfer.surf_once.assert_not_awaited()


# ---------------------------------------------------------------------------
# 5. 測試自發思考 (mind_monologue) 模式在 surf_once 中的日記寫入
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_surf_once_mind_monologue(care_repo: CareRepository, self_mem_db: Path):
    """自發思考模式應從自我記憶檢索並寫入日記與行動，且在非活躍時段時絕不發送到 Discord"""
    # 設定當前時段為非活躍 (深夜)
    care_repo.update_surfer_config(enabled=True, active_hour_start=9, active_hour_end=17, sandbox_channel_id="123456")
    surfer = CareSurfer(care_repo, self_memory_db_path=self_mem_db)

    # 預先在 self_memory 注入前情提要
    self_memory.add_self_episode(
        category="interest",
        content="喜歡研究量子力學與賽博龐克風格的小說",
        importance=5,
        db_path=self_mem_db,
    )

    mock_bot = MagicMock()
    mock_bot.get_cog.return_value = None
    mock_channel = MagicMock()
    mock_channel.send = AsyncMock()
    mock_bot.get_channel.return_value = mock_channel

    # 模擬深夜 3 點 (非活躍)
    with patch("handlers.care.surfer.dt") as mock_dt:
        mock_now = MagicMock()
        mock_now.hour = 3
        mock_dt.now.return_value = mock_now

        res = await surfer.surf_once(mock_bot, mode="mind_monologue", dry_run=False)

    assert res["status"] == "idle"
    assert res.get("mode") == "mind_monologue"
    assert res.get("silent") is True
    # 絕不外發 Discord
    assert not res.get("posted_message_id")
    mock_channel.send.assert_not_awaited()

    # 檢查是否確實寫入 diary episode 到 self_memory.db
    episodes = self_memory.list_self_episodes(limit=5, category="diary", db_path=self_mem_db)
    assert len(episodes) >= 1
    assert "自發思考" in episodes[0]["tags"]


# ---------------------------------------------------------------------------
# 6. 測試 Web API 端點: /api/care/surfer/config & /api/care/surfer/mind-tick
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_care_web_api_active_hours_and_mind_tick(app, care_repo: CareRepository):
    # Mock CareEngine
    mock_engine = MagicMock()
    mock_engine.repository = care_repo
    mock_surfer = CareSurfer(care_repo)
    mock_engine.surfer = mock_surfer

    set_engine(mock_engine)

    client = app.test_client()

    # 1. 測試 POST /api/care/surfer/config 設定時段 08:00 ~ 22:00
    res_post = await client.post("/api/care/surfer/config", json={
        "sandbox_channel_id": "123456789",
        "active_hour_start": 8,
        "active_hour_end": 22,
    })
    assert res_post.status_code == 200
    data_post = await res_post.get_json()
    assert data_post["status"] == "success"

    # 2. 測試 GET /api/care/surfer/config 正確讀取時段
    res_get = await client.get("/api/care/surfer/config")
    assert res_get.status_code == 200
    data_get = await res_get.get_json()
    assert data_get["config"]["active_hour_start"] == 8
    assert data_get["config"]["active_hour_end"] == 22
    assert "mind_state" in data_get["live_stats"]
    assert "is_active_hours" in data_get["live_stats"]

    # 3. 測試 POST /api/care/surfer/mind-tick 推進心智消耗 (-20%)
    res_tick = await client.post("/api/care/surfer/mind-tick", json={
        "force_decay": 20.0
    })
    assert res_tick.status_code == 200
    data_tick = await res_tick.get_json()
    assert data_tick["status"] == "success"
    assert "mind_state" in data_tick
    assert data_tick["mind_state"]["curiosity_satiety"] <= 85.0


# ---------------------------------------------------------------------------
# 7. 測試深夜靜音時段對所有發言模式的嚴格靜音保護（100% 不外發 Discord）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("test_mode", ["daily_vibe", "lurk_chat", "care_poke"])
async def test_night_hours_suppress_all_speaking_modes(care_repo: CareRepository, test_mode: str):
    """深夜非活躍時段：任何發言模式（daily_vibe, lurk_chat, care_poke）都嚴格禁止向 Discord 發言。"""
    care_repo.update_surfer_config(enabled=True, active_hour_start=9, active_hour_end=23, sandbox_channel_id="99999")
    surfer = CareSurfer(care_repo)

    mock_bot = MagicMock()
    mock_bot.get_cog.return_value = None
    mock_channel = MagicMock()
    mock_channel.send = AsyncMock()
    mock_channel.history.return_value = []
    mock_bot.get_channel.return_value = mock_channel

    # 模擬凌晨 3 點 (非活躍時段)
    with patch("handlers.care.surfer.dt") as mock_dt:
        mock_now = MagicMock()
        mock_now.hour = 3
        mock_dt.now.return_value = mock_now

        res = await surfer.surf_once(mock_bot, mode=test_mode, dry_run=False)

    assert res["status"] == "idle"
    # 絕不可外發 Discord 訊息
    assert not res.get("posted_message_id")
    mock_channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_cog_surfer_loop_night_cruise_suppressed(care_repo: CareRepository):
    """測試 CareCog 的 _surfer_loop 在深夜時段跳過定時定額的巡航主動開口。"""
    from handlers.care.cog import CareCog

    mock_bot = MagicMock()
    mock_engine = MagicMock()
    mock_engine.repository = care_repo
    mock_surfer = MagicMock()
    mock_surfer.tick_mind_pulse = AsyncMock()
    mock_surfer.surf_once = AsyncMock()
    mock_engine.surfer = mock_surfer

    # 設定活躍時段為 9~23 點，且間隔已到期
    care_repo.update_surfer_config(
        enabled=True,
        sandbox_channel_id="123456",
        interval_minutes=30,
        active_hour_start=9,
        active_hour_end=23,
    )
    # 將 last_run_at 設為很久以前以滿足 interval
    conn = care_repo._connect()
    conn.execute("UPDATE care_surfer_config SET last_run_at = 0 WHERE id = 1")
    conn.commit()
    conn.close()

    cog = CareCog(mock_bot, mock_engine)

    # 模擬凌晨 4 點 (非活躍)
    with patch("handlers.care.cog.dt") as mock_dt:
        mock_now = MagicMock()
        mock_now.hour = 4
        mock_dt.now.return_value = mock_now

        # 執行迴圈本體（呼叫 _surfer_loop 協程）
        await cog._surfer_loop.coro(cog)

    # 心智脈動仍會每分鐘推進
    mock_surfer.tick_mind_pulse.assert_awaited_once()
    # 但定時巡航絕不呼叫 surf_once 主動開口
    mock_surfer.surf_once.assert_not_awaited()


@pytest.mark.asyncio
async def test_cog_surfer_loop_defers_cruise_when_gate_blocks(care_repo: CareRepository):
    """閘門擋下巡航時要推遲 last_run_at，避免每分鐘重試洗一筆 SILENT。"""
    from handlers.care.cog import CareCog

    mock_bot = MagicMock()
    mock_engine = MagicMock()
    mock_engine.repository = care_repo
    mock_surfer = MagicMock()
    mock_surfer.tick_mind_pulse = AsyncMock()
    mock_surfer.surf_once = AsyncMock()
    mock_engine.surfer = mock_surfer

    care_repo.update_surfer_config(
        enabled=True,
        sandbox_channel_id="123456",
        interval_minutes=30,
        active_hour_start=0,
        active_hour_end=0,
    )
    conn = care_repo._connect()
    conn.execute("UPDATE care_surfer_config SET last_run_at = 0 WHERE id = 1")
    conn.commit()
    conn.close()

    cog = CareCog(mock_bot, mock_engine)
    blocked_decision = MagicMock()
    blocked_decision.allowed = False
    blocked_decision.gate.reasons = ["daily_budget=63/60"]
    cog._social_director_instance = MagicMock()
    cog._social_director_instance.decide.return_value = blocked_decision

    await cog._surfer_loop.coro(cog)

    mock_surfer.surf_once.assert_not_awaited()
    assert care_repo.get_surfer_config().last_run_at > 0


# ---------------------------------------------------------------------------
# 8. 測試 mind_pace 心智累積節奏與冷場加速度模型
# ---------------------------------------------------------------------------

def test_care_repository_mind_pace_persistence(care_repo: CareRepository):
    """測試 mind_pace 欄位預設為 normal，並可持久化更新為 fast 或 chill。"""
    cfg = care_repo.get_surfer_config()
    assert cfg.mind_pace == "normal"

    care_repo.update_surfer_config(mind_pace="fast")
    cfg_fast = care_repo.get_surfer_config()
    assert cfg_fast.mind_pace == "fast"
    assert cfg_fast.to_dict()["mind_pace"] == "fast"

    care_repo.update_surfer_config(mind_pace="chill")
    cfg_chill = care_repo.get_surfer_config()
    assert cfg_chill.mind_pace == "chill"


def test_accumulate_mind_state_silence_acceleration(care_repo: CareRepository):
    """測試冷場時間越長，寂寞無聊度遞加速度越快。"""
    from handlers.care.surfer import accumulate_mind_state, _mind_state

    # 1. 歸零無聊度 (社交值 100%)
    _mind_state["social_satiety"] = 100.0
    _mind_state["curiosity_satiety"] = 100.0

    # 0 分鐘冷場（基準增長）
    accumulate_mind_state(silence_minutes=0.0, pace="normal", is_active_hours=True)
    boredom_0 = _mind_state["boredom_level"]
    assert boredom_0 > 0

    # 重設並測試 30 分鐘冷場（加速累積）
    _mind_state["social_satiety"] = 100.0
    _mind_state["curiosity_satiety"] = 100.0
    accumulate_mind_state(silence_minutes=30.0, pace="normal", is_active_hours=True)
    boredom_30 = _mind_state["boredom_level"]

    # 30 分鐘冷場的單次遞增幅度必定顯著大於 0 分鐘冷場（加速因子）
    assert boredom_30 > boredom_0 * 2.0


def test_chatter_relieves_boredom_and_bot_speak_resets(care_repo: CareRepository):
    """測試群友熱聊可緩解無聊度，雪莉開口發言後無聊度直接清零重置。"""
    from handlers.care.surfer import update_chatter_timestamp, update_bot_speak_timestamp, _mind_state

    # 模擬雪莉無聊度 50% (社交值 50%)
    _mind_state["social_satiety"] = 50.0
    _mind_state["boredom_level"] = 50.0

    # 群友發言，無聊感得到撫慰（無聊度下降、社交值上升）
    update_chatter_timestamp(123456)
    assert _mind_state["boredom_level"] < 50.0
    assert _mind_state["social_satiety"] > 50.0

    # 雪莉主動開口發言，無聊度完全釋放清零重置 (0%)，社交值拉滿 (100%)
    update_bot_speak_timestamp(123456)
    assert _mind_state["boredom_level"] == 0.0
    assert _mind_state["social_satiety"] == 100.0


@pytest.mark.asyncio
async def test_care_web_api_mind_pace_and_force_boost(app, care_repo: CareRepository):
    """測試 Web API 儲存 mind_pace 設定以及呼叫 /mind-tick 時傳入 force_boost。"""
    mock_engine = MagicMock()
    mock_engine.repository = care_repo
    mock_surfer = CareSurfer(care_repo)
    mock_engine.surfer = mock_surfer

    set_engine(mock_engine)
    client = app.test_client()

    # 1. POST /api/care/surfer/config 設定 mind_pace="fast"
    res_cfg = await client.post("/api/care/surfer/config", json={
        "mind_pace": "fast"
    })
    assert res_cfg.status_code == 200
    data_cfg = await res_cfg.get_json()
    assert data_cfg["config"]["mind_pace"] == "fast"

    # 2. POST /api/care/surfer/mind-tick 傳入 force_boost: 25.0
    from handlers.care.surfer import _mind_state
    _mind_state["social_satiety"] = 100.0
    _mind_state["boredom_level"] = 0.0

    res_tick = await client.post("/api/care/surfer/mind-tick", json={
        "force_boost": 25.0
    })
    assert res_tick.status_code == 200
    data_tick = await res_tick.get_json()
    assert data_tick["status"] == "success"
    assert data_tick["boost"] == 25.0
    assert data_tick["mind_state"]["boredom_level"] >= 25.0


