"""Unit tests for the Cyber-Friend ("大肥魚" / Net-Surfer) Engine and Proactive Care Console overhaul."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from quart import Quart

from handlers.care.repository import CareSurferConfig
from handlers.care.surfer import (
    CareSurfer,
    _gather_sherry_live_stats,
    clean_cyber_bubble,
    get_last_bot_speak_timestamp,
    get_last_human_chatter_timestamp,
    is_channel_unanswered,
    split_into_bubbles,
    update_bot_speak_timestamp,
    update_chatter_timestamp,
)


def test_clean_cyber_bubble_edge_cases() -> None:
    # 1. Chinese quotation marks & trailing periods
    assert clean_cyber_bubble("「救命…今天好累。」") == "救命…今天好累"
    assert clean_cyber_bubble("“『真假！？』”") == "『真假！？』"
    assert clean_cyber_bubble("「「雙重引號」」") == "雙重引號"
    assert clean_cyber_bubble("這是一句話。") == "這是一句話"
    assert clean_cyber_bubble("這是一句驚嘆！") == "這是一句驚嘆！"
    assert clean_cyber_bubble("這是一句問句？") == "這是一句問句？"

    # 2. Markdown code fences must strip fences without wiping text content
    fenced_text = "```markdown\n今天已經燒了 2 萬多 Token 了！\n```"
    assert clean_cyber_bubble(fenced_text) == "今天已經燒了 2 萬多 Token 了！"

    json_fenced = "```json\n笑死，今天真的很扯\n```"
    assert clean_cyber_bubble(json_fenced) == "笑死，今天真的很扯"

    # 3. Thought tags
    thought_text = "<thought>內心偷偷想著摸魚</thought>誰都別叫我破案～"
    assert clean_cyber_bubble(thought_text) == "誰都別叫我破案～"


def test_split_into_bubbles_pacing() -> None:
    # 1. Natural newlines
    text_newlines = "第一句口語！\n第二句口語～\n第三句口語。"
    bubbles = split_into_bubbles(text_newlines, max_bubbles=3)
    assert len(bubbles) == 3
    assert bubbles[0] == "第一句口語！"
    assert bubbles[1] == "第二句口語～"
    assert bubbles[2] == "第三句口語"

    # 2. Long single sentence with punctuation
    long_punct = "救命今天已經燒了兩萬個Token的腦細胞了！本名偵探宣布進入摸魚模式～誰都別叫我破案！"
    bubbles2 = split_into_bubbles(long_punct, max_bubbles=3)
    assert 1 < len(bubbles2) <= 3

    # 3. Long sentence with commas
    long_comma = "今天在社群看到一個超搞笑的八卦，據說隔壁棚把資料庫當成白飯吃掉了，笑死"
    bubbles3 = split_into_bubbles(long_comma, max_bubbles=3)
    assert 1 < len(bubbles3) <= 3


def test_live_stats_includes_tokens_and_active_chat_window() -> None:
    channel_id = "888999"
    update_chatter_timestamp(channel_id, time.time() - 120)

    # Initial stats (no recent bot speak, chatter 2 minutes ago)
    stats = _gather_sherry_live_stats(None, channel_id)
    assert "tokens_consumed_today" in stats
    assert "tokens_today" in stats
    assert stats["tokens_consumed_today"] == stats["tokens_today"]
    assert "active_chat_window_open" in stats
    assert stats["active_chat_window_open"] is False
    assert stats["silence_minutes"] == 2

    # Simulate bot speaking in channel
    update_bot_speak_timestamp(channel_id)
    assert get_last_bot_speak_timestamp(channel_id) > 0

    stats_after = _gather_sherry_live_stats(None, channel_id)
    assert stats_after["active_chat_window_open"] is True
    assert stats_after["silence_minutes"] == 0


@pytest.mark.asyncio
async def test_surfer_is_busy_returns_409() -> None:
    app = Quart(__name__)
    app.config["TESTING"] = True
    from handlers.care.web import care_bp
    app.register_blueprint(care_bp)

    client = app.test_client()
    from handlers.care.surfer import get_care_surfer
    surfer = get_care_surfer()

    # Acquire the lock to simulate background surf in progress
    async with surfer._surf_lock:
        assert surfer.is_busy is True
        resp = await client.post("/api/care/surfer/trigger", json={"mode": "daily_vibe"})
        assert resp.status_code == 409
        data = await resp.get_json()
        assert data["status"] == "busy"
        assert "正在冒泡衝浪中" in data["message"]


@pytest.mark.asyncio
async def test_care_cog_unmentioned_reply_and_active_chat() -> None:
    import discord
    from handlers.care.cog import CareCog

    class _FakeBrain:
        """假 BotBrain：直接回呼生成結果，模擬模型正常輸出。"""

        async def enqueue_task(self, task):
            await task.callback('{"thought": "他居然接話了", "reply": "哈哈對啊，本名偵探才不會騙人"}')

    mock_bot = MagicMock()
    brain = _FakeBrain()
    mock_bot.get_cog.side_effect = lambda name: brain if name == "BotBrain" else None
    mock_bot_user = MagicMock(spec=discord.User)
    mock_bot_user.id = 12345
    mock_bot.user = mock_bot_user

    mock_engine = MagicMock()
    cfg = CareSurferConfig(
        sandbox_channel_id="777888",
        chat_reply_enabled=True,
        active_chat_window_seconds=300,
        active_hour_start=0,
        active_hour_end=0,
        interject_threshold=3,
        interject_score_threshold=6,
    )
    mock_engine.repository.get_surfer_config.return_value = cfg
    mock_engine.repository.list_actions.return_value = []
    mock_engine.repository.get_channel_mode.return_value = None

    cog = CareCog(mock_bot, mock_engine)

    # 1. Message from bot itself updates speak timestamp
    bot_msg = MagicMock(spec=discord.Message)
    bot_msg.channel = MagicMock(spec=discord.TextChannel)
    bot_msg.channel.id = 777888
    bot_msg.author = mock_bot_user
    await cog.on_message(bot_msg)
    assert get_last_bot_speak_timestamp(777888) > 0

    # 2. User unmentioned reply to bot message -> should trigger active chat without @mention
    user_msg = MagicMock(spec=discord.Message)
    user_msg.channel = MagicMock(spec=discord.TextChannel)
    user_msg.channel.id = 777888
    user_author = MagicMock(spec=discord.Member)
    user_author.id = 99999
    user_author.bot = False
    user_author.display_name = "測試群友"
    user_msg.author = user_author
    user_msg.content = "哈哈真的假的"
    user_msg.clean_content = "哈哈真的假的"
    user_msg.mentions = []

    # Mock reference to bot message
    ref_msg = MagicMock(spec=discord.Message)
    ref_msg.author = mock_bot_user
    user_msg.reference = MagicMock()
    user_msg.reference.resolved = ref_msg

    with patch("handlers.care.surfer.send_cyber_bubbles", AsyncMock(return_value=[MagicMock()])) as mock_send, \
         patch("handlers.social.utils.fetch_recent_script", AsyncMock(return_value="[測試群友]: 哈哈真的假的")):
        await cog.on_message(user_msg)
        assert mock_send.await_count == 1
        sent_bubbles = mock_send.await_args.args[1]
        assert "本名偵探" in "".join(sent_bubbles)

    # 3. 生成失敗時必須完全沉默，不得補任何罐頭台詞
    class _EmptyBrain:
        async def enqueue_task(self, task):
            await task.callback("")

    mock_bot.get_cog.side_effect = lambda name: _EmptyBrain() if name == "BotBrain" else None
    update_bot_speak_timestamp(777888)
    with patch("handlers.care.surfer.send_cyber_bubbles", AsyncMock(return_value=[MagicMock()])) as mock_send2:
        await cog.on_message(user_msg)
        assert mock_send2.await_count == 0

    rows = mock_engine.repository.record_decision.call_args_list
    assert rows, "生成失敗時應寫入決策帳本"


@pytest.mark.asyncio
async def test_dry_run_does_not_persist_real_actions() -> None:
    class _FakeBrain:
        async def enqueue_task(self, task):
            await task.callback('{"thought": "今天只想躺平", "post_text": "今天誰都別想叫我破案，本偵探要罷工"}')

    brain = _FakeBrain()
    mock_bot = MagicMock()
    mock_bot.get_cog.side_effect = lambda name: brain if name == "BotBrain" else None

    mock_repo = MagicMock()
    mock_repo.get_surfer_config.return_value = CareSurferConfig(
        enabled=True,
        sandbox_channel_id="777888",
        behavior_mode="daily_vibe",
        multi_message_enabled=True,
    )
    surfer = CareSurfer(repository=mock_repo)

    with patch("handlers.care.surfer.record_proactive_action_memory") as mock_record_mem, \
         patch("handlers.care.surfer.add_self_episode") as mock_add_ep:
        result = await surfer.surf_once(bot=mock_bot, dry_run=True, mode="daily_vibe")
        assert result["status"] == "idle"
        assert result["step"] == "completed"
        assert result["dry_run"] is True
        assert result["bubbles"], "dry-run 仍應回傳預覽內容"

        # Must not persist action memories or audit records during dry run
        mock_record_mem.assert_not_called()
        mock_add_ep.assert_not_called()
        mock_repo.record_action.assert_not_called()
        mock_repo.set_surfer_last_run.assert_not_called()


@pytest.mark.asyncio
async def test_daily_vibe_generation_failure_is_silent() -> None:
    """生成失敗時不得補罐頭台詞，直接回傳 silent。"""

    class _EmptyBrain:
        async def enqueue_task(self, task):
            await task.callback("")

    brain = _EmptyBrain()
    mock_bot = MagicMock()
    mock_bot.get_cog.side_effect = lambda name: brain if name == "BotBrain" else None

    mock_repo = MagicMock()
    mock_repo.get_surfer_config.return_value = CareSurferConfig(
        enabled=True,
        sandbox_channel_id="777888",
        behavior_mode="daily_vibe",
    )
    surfer = CareSurfer(repository=mock_repo)
    result = await surfer.surf_once(bot=mock_bot, dry_run=False, mode="daily_vibe")
    assert result["status"] == "idle"
    assert result["silent"] is True
    assert result["reason"] == "generation_failed"
    mock_repo.record_action.assert_not_called()


def test_clean_cyber_bubble_sparkle_and_pronoun_clamping() -> None:
    # 1. Clamp ✨ emoji count to at most 1 per bubble
    text_sparkles = "✨這是一個✨很酷的✨八卦✨！"
    assert clean_cyber_bubble(text_sparkles) == "✨這是一個很酷的八卦！"

    # Single sparkle is preserved
    single_sparkle = "今天心情好 ✨！"
    assert clean_cyber_bubble(single_sparkle) == "今天心情好 ✨！"

    # No sparkle is unchanged
    no_sparkle = "普通的口語對話"
    assert clean_cyber_bubble(no_sparkle) == "普通的口語對話"

    # 2. Detective pronoun replacement: if "本名偵探" or "本偵探" appears > 1, 2nd+ becomes "我"
    multiple_detective = "本名偵探覺得這件事很簡單，只要本偵探出手就搞定了，畢竟本名偵探很厲害！"
    assert clean_cyber_bubble(multiple_detective) == "本名偵探覺得這件事很簡單，只要我出手就搞定了，畢竟我很厲害！"

    mixed_detective = "本偵探今天不想出門，除非本偵探最愛的漢娜叫我"
    assert clean_cyber_bubble(mixed_detective) == "本偵探今天不想出門，除非我最愛的漢娜叫我"

    single_detective = "這件事就交給本名偵探吧！"
    assert clean_cyber_bubble(single_detective) == "這件事就交給本名偵探吧！"


def test_render_recent_self_anti_repetition_constraint() -> None:
    from handlers.social.brain.context import ContextMessage, ConversationContext
    from handlers.social.brain.prompts import render_recent_self

    # 1. Empty messages -> empty string
    ctx_empty = ConversationContext(channel_id="123")
    assert render_recent_self(ctx_empty) == ""

    # 2. Has self messages -> includes anti-repetition constraint
    ctx_with_self = ConversationContext(
        channel_id="123",
        bot_id="bot_999",
        messages=[
            ContextMessage(
                message_id="1",
                author_id="bot_999",
                author_name="雪莉",
                content="我剛剛在看那篇新聞",
                created_at=time.time() - 60,
                is_self=True,
            )
        ],
    )
    rendered = render_recent_self(ctx_with_self)
    assert "【妳最近在頻道說過的話】" in rendered
    assert "我剛剛在看那篇新聞" in rendered
    assert "防跳針約束" in rendered
    assert "前 6~8 個字" in rendered


def test_silence_minutes_not_stuck_at_60_after_bot_speaks() -> None:
    from handlers.care.surfer import _last_channel_chatter_time, _last_bot_speak_time

    channel_id = "test_silence_chan_999"

    # Case 1: Channel not in chatter time, bot speaks -> silence is 0
    _last_channel_chatter_time.pop(channel_id, None)
    _last_bot_speak_time.pop(channel_id, None)
    update_bot_speak_timestamp(channel_id)
    stats = _gather_sherry_live_stats(None, channel_id)
    assert stats["silence_minutes"] == 0

    # Case 2: Human chatted 2 hours ago, bot just spoke now -> silence must be 0, NOT 120!
    _last_channel_chatter_time[channel_id] = time.time() - 7200
    update_bot_speak_timestamp(channel_id)
    stats = _gather_sherry_live_stats(None, channel_id)
    assert stats["silence_minutes"] == 0

    # Case 3: Bot spoke 2 hours ago, human spoke 10 seconds ago -> silence is 0
    _last_bot_speak_time[channel_id] = time.time() - 7200
    _last_channel_chatter_time[channel_id] = time.time()
    stats = _gather_sherry_live_stats(None, channel_id)
    assert stats["silence_minutes"] == 0

    # Case 4: Neither has spoken in channel -> defaults to 60 mins
    _last_channel_chatter_time.pop(channel_id, None)
    _last_bot_speak_time.pop(channel_id, None)
    stats = _gather_sherry_live_stats(None, channel_id)
    assert stats["silence_minutes"] == 60


def test_render_recent_self_from_fallback_script() -> None:
    from handlers.social.brain.context import ConversationContext
    from handlers.social.brain.prompts import render_recent_self

    # Script mode with bot line in raw_script
    script = (
        "Alice: 今天天氣真好\n"
        "雪莉: 嗨嗨！本名偵探剛吃完早餐\n"
        "Bob: 真的嗎"
    )
    ctx = ConversationContext.from_script(script, bot_name="雪莉")
    rendered = render_recent_self(ctx)
    assert "【妳最近在頻道說過的話】" in rendered
    assert "嗨嗨！本名偵探剛吃完早餐" in rendered
    assert "防跳針約束" in rendered
    assert "前 6~8 個字" in rendered


def test_clean_cyber_bubble_space_collapse_and_multiple_replacements() -> None:
    # Multiple sparkles with spaces between them
    raw = "哇 ✨  真的嗎 ✨  太神了 ✨ ！"
    cleaned = clean_cyber_bubble(raw)
    assert "✨" in cleaned
    assert cleaned.count("✨") == 1
    assert "   " not in cleaned

    # 3+ occurrences of detective pronouns: only the 1st is kept
    triple = "本偵探先出發，本名偵探隨後到，本偵探保證破案！"
    cleaned_triple = clean_cyber_bubble(triple)
    assert cleaned_triple == "本偵探先出發，我隨後到，我保證破案！"


@pytest.mark.asyncio
async def test_is_channel_unanswered_logic() -> None:
    """測試未回覆自言自語狀態判定：Discord 歷史與記憶體降級。"""
    mock_bot = MagicMock()
    mock_bot.user.id = 9999
    mock_channel = MagicMock()
    mock_bot.get_channel.return_value = mock_channel

    # 1. 頻道最新訊息是機器人自己 -> 判定為 unanswered (True)
    bot_msg = MagicMock()
    bot_msg.author.id = 9999
    bot_msg.author.bot = True
    mock_channel.history.return_value = [bot_msg]

    assert await is_channel_unanswered(mock_bot, "555666") is True

    # 2. 頻道最新訊息是人類 -> 判定為已回覆 (False)
    human_msg = MagicMock()
    human_msg.author.id = 1111
    human_msg.author.bot = False
    mock_channel.history.return_value = [human_msg]

    assert await is_channel_unanswered(mock_bot, "555666") is False

    # 3. 頻道最新訊息是其他機器人（例如音樂機器人），但在那之前是人類 -> 判定為已回覆 (False)
    other_bot_msg = MagicMock()
    other_bot_msg.author.id = 7777
    other_bot_msg.author.bot = True
    mock_channel.history.return_value = [other_bot_msg, human_msg]

    assert await is_channel_unanswered(mock_bot, "555666") is False

    # 4. 頻道最新訊息是其他機器人，但在那之前是雪莉自己 -> 判定為未回覆 (True)
    mock_channel.history.return_value = [other_bot_msg, bot_msg]

    assert await is_channel_unanswered(mock_bot, "555666") is True

    # 5. 頻道無法讀取歷史訊息時降級為記憶體時間戳
    mock_channel.history.side_effect = Exception("Channel inaccessible")
    cid = "777000"
    update_bot_speak_timestamp(cid, ts=200.0)
    assert await is_channel_unanswered(mock_bot, cid) is True

    update_chatter_timestamp(cid, ts=250.0)
    assert await is_channel_unanswered(mock_bot, cid) is False


@pytest.mark.asyncio
async def test_is_channel_unanswered_heartbeat_allows_stale_bot_message() -> None:
    """雪莉最後發言已超過心跳間隔時，允許再主動開一次話題，不再永久靜音。"""
    import time as _time

    mock_bot = MagicMock()
    mock_bot.user.id = 9999
    mock_channel = MagicMock()
    mock_bot.get_channel.return_value = mock_channel

    bot_msg = MagicMock()
    bot_msg.author.id = 9999
    bot_msg.author.bot = True
    bot_msg.created_at.timestamp.return_value = _time.time() - 7200
    mock_channel.history.return_value = [bot_msg]

    cid = "555777"
    assert await is_channel_unanswered(mock_bot, cid) is True
    assert await is_channel_unanswered(mock_bot, cid, heartbeat_seconds=3600) is False
    assert await is_channel_unanswered(mock_bot, cid, heartbeat_seconds=9000) is True


@pytest.mark.asyncio
async def test_tick_mind_pulse_bot_last_speaker_no_post(tmp_path: Path) -> None:
    """測試當雪莉是頻道最後發言者（未回覆狀態）時，tick_mind_pulse 絕不對 Discord 發送新訊息。"""
    from handlers.care.repository import CareRepository

    repo = CareRepository(db_path=tmp_path / "care_test1.db")
    repo.update_surfer_config(
        enabled=True,
        sandbox_channel_id="888111",
        active_hour_start=0,
        active_hour_end=0,
    )
    surfer = CareSurfer(repo)

    mock_bot = MagicMock()
    mock_bot.user.id = 9999
    mock_channel = MagicMock()
    mock_channel.send = AsyncMock()
    mock_bot.get_channel.return_value = mock_channel

    # 模擬頻道最後一則訊息是機器人發送
    bot_msg = MagicMock()
    bot_msg.author.id = 9999
    bot_msg.author.bot = True
    mock_channel.history.return_value = [bot_msg]

    # 設定無聊度爆表
    from handlers.care.surfer import _mind_state
    _mind_state["social_satiety"] = 10.0
    _mind_state["boredom_level"] = 90.0

    # 攔截 surf_once
    surfer.surf_once = AsyncMock(return_value={"status": "idle", "mode": "mind_monologue", "silent": True})

    result = await surfer.tick_mind_pulse(mock_bot)

    # 驗證動作為 silent_boredom 且呼叫的是 mind_monologue(silent=True)
    assert result["status"] == "success"
    assert result["triggered_action"] == "silent_boredom"
    surfer.surf_once.assert_awaited_once_with(mock_bot, dry_run=False, mode="mind_monologue")
    mock_channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_mind_pulse_boredom_threshold_internal_monologue_when_ignored(tmp_path: Path) -> None:
    """測試在無人理會（被忽略）時，無聊度爆表會觸發內部心智自省（寫日記）而非頻道發文，且無聊度成功消解。"""
    from handlers.care.repository import CareRepository

    repo = CareRepository(db_path=tmp_path / "care_test2.db")
    repo.update_surfer_config(
        enabled=True,
        sandbox_channel_id="888222",
        active_hour_start=0,
        active_hour_end=0,
    )
    surfer = CareSurfer(repo)

    mock_bot = MagicMock()
    mock_bot.user.id = 9999
    mock_channel = MagicMock()
    mock_channel.send = AsyncMock()
    mock_bot.get_channel.return_value = mock_channel

    # 模擬機器人發言後無人回應
    update_bot_speak_timestamp("888222")

    bot_msg = MagicMock()
    bot_msg.author.id = 9999
    bot_msg.author.bot = True
    mock_channel.history.return_value = [bot_msg]

    # 無聊度門檻爆表 (boredom >= 80)
    from handlers.care.surfer import _mind_state
    _mind_state["social_satiety"] = 5.0
    _mind_state["boredom_level"] = 95.0

    surfer.surf_once = AsyncMock(return_value={"status": "idle", "mode": "mind_monologue", "silent": True})

    result = await surfer.tick_mind_pulse(mock_bot)

    assert result["triggered_action"] == "silent_boredom"
    # 心智狀態無聊度必須被回補重置 (social_satiety >= 95, boredom_level <= 5.0)
    assert result["mind_state"]["boredom_level"] <= 5.0
    assert result["mind_state"]["social_satiety"] >= 95.0
    surfer.surf_once.assert_awaited_once_with(mock_bot, dry_run=False, mode="mind_monologue")
    mock_channel.send.assert_not_awaited()


def test_proactive_gate_respects_cruise_interval(tmp_path: Path) -> None:
    """測試主動發文閘門嚴格遵守 config.interval_minutes (如 120 分鐘巡航間隔)。"""
    from handlers.care.repository import CareRepository

    repo = CareRepository(db_path=tmp_path / "care_test3.db")
    repo.update_surfer_config(
        enabled=True,
        sandbox_channel_id="888333",
        interval_minutes=120,
        active_hour_start=0,
        active_hour_end=0,
    )
    surfer = CareSurfer(repo)

    # 1. 人類剛講過話（非 unanswered），但距上次發文僅 5 分鐘 (< 120 分鐘)
    update_chatter_timestamp("888333")
    repo.set_surfer_last_run(time.time() - 300.0)
    cfg1 = repo.get_surfer_config()
    assert surfer._proactive_gate_allows(cfg1, "888333", is_unanswered=False) is False

    # 2. 距上次發文已超過 120 分鐘 (例如 125 分鐘) -> 允許發文
    repo.set_surfer_last_run(time.time() - 7500.0)
    cfg2 = repo.get_surfer_config()
    assert surfer._proactive_gate_allows(cfg2, "888333", is_unanswered=False) is True


@pytest.mark.asyncio
async def test_surf_once_mind_monologue_silent_does_not_post(tmp_path: Path) -> None:
    """測試在 silent=True 時執行 mind_monologue，絕不呼叫 send_cyber_bubbles 發文至 Discord。"""
    from handlers.care.repository import CareRepository

    repo = CareRepository(db_path=tmp_path / "care_test4.db")
    repo.update_surfer_config(
        enabled=True,
        sandbox_channel_id="888444",
        active_hour_start=0,
        active_hour_end=0,
    )
    surfer = CareSurfer(repo, self_memory_db_path=tmp_path / "self_mem.db")

    mock_bot = MagicMock()
    mock_bot.user.id = 9999
    mock_bot.get_cog.return_value = None
    mock_channel = MagicMock()
    mock_bot.get_channel.return_value = mock_channel

    with patch("handlers.care.surfer.send_cyber_bubbles", new_callable=AsyncMock) as mock_send:
        result = await surfer.surf_once(mock_bot, dry_run=False, mode="mind_monologue", silent=True)
        # 即使模式為 mind_monologue 且處於活躍時段，指定 silent=True 嚴禁發文
        mock_send.assert_not_called()
        assert result["status"] == "idle"
        assert result["silent"] is True
        assert result["mode"] == "mind_monologue"



