"""tests/unit/test_care_context_awareness.py
===========================================
單元測試：雪莉主動發言之頻道上下文感知 (Context Awareness)、
自然轉折提示注入、對象識別、以及 Active Chat Window 熱聊接話對線。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from handlers.care.repository import CareRepository, CareSurferConfig
from handlers.care.surfer import CareSurfer


@pytest.fixture
def temp_repo(tmp_path: Path) -> CareRepository:
    return CareRepository(db_path=tmp_path / "test_context_care.db")


def test_get_surfer_config_env_fallback(temp_repo: CareRepository):
    """測試資料庫中 sandbox_channel_id 為 0 時自動回退取用 settings.CARE_SURFER_CHANNEL_ID。"""
    with patch("handlers.care.repository.settings") as mock_settings:
        mock_settings.CARE_SURFER_CHANNEL_ID = 1564914123966255205
        cfg = temp_repo.get_surfer_config()
        assert cfg.sandbox_channel_id == "1564914123966255205"


@pytest.mark.asyncio
async def test_surf_once_injects_channel_history_into_care_poke(temp_repo: CareRepository):
    """測試 care_poke 能取得頻道上下文與指名對象，並在 Prompt 中融入對話記錄。"""
    temp_repo.update_surfer_config(enabled=True, sandbox_channel_id="123456", tag_mode="interest_match")
    # 加入一筆待回訪話題
    topic_id = temp_repo.add_topic(user_id="999888", topic="提到最近在倒數日子，壓力很大")

    surfer = CareSurfer(temp_repo)

    mock_bot = MagicMock()
    mock_channel = MagicMock()
    mock_channel.id = 123456
    mock_channel.history = MagicMock()
    mock_channel.send = AsyncMock()
    mock_bot.get_channel.return_value = mock_channel

    mock_user = MagicMock()
    mock_user.display_name = "歐潤吉"
    mock_bot.get_user.return_value = mock_user

    # 模擬頻道近期歷史對話
    mock_msg1 = MagicMock()
    mock_msg1.author.bot = False
    mock_msg1.author.display_name = "歐潤吉"
    mock_msg1.clean_content = "這個是漢娜欸 你好壞喔"

    mock_msg2 = MagicMock()
    mock_msg2.author.bot = True
    mock_msg2.author.id = 77777  # bot id
    mock_msg2.clean_content = "雪莉才沒有壞呢！"

    async def mock_history(*args, **kwargs):
        for m in [mock_msg2, mock_msg1]:
            yield m

    mock_channel.history = mock_history

    captured_prompt = {}

    mock_brain = MagicMock()
    async def mock_enqueue(task):
        captured_prompt["system"] = task.system_prompt
        captured_prompt["user"] = task.user_prompt
        # 回傳假推論
        res = task.callback('{"thought": "先不鬧了，隨便問一下", "post_text": "好啦先不鬧了，歐潤吉，那堆爛事還撐得住嗎？"}')
        if asyncio.iscoroutine(res):
            await res

    mock_brain.enqueue_task = mock_enqueue
    mock_bot.get_cog.return_value = mock_brain

    res = await surfer.surf_once(mock_bot, mode="care_poke", dry_run=False)

    assert res["status"] == "idle"
    assert "歐潤吉" in captured_prompt["system"]
    assert "頻道近期對話上下文" in captured_prompt["system"]
    assert "好啦先不鬧了" in captured_prompt["system"] or "自然鋪墊" in captured_prompt["system"]
    assert "提到最近在倒數日子" in captured_prompt["user"]


@pytest.mark.asyncio
async def test_surf_once_injects_channel_history_into_surf_gossip(temp_repo: CareRepository):
    """測試 surf_gossip 能帶入頻道上下文，讓衝浪發文具備對話銜接與多元破冰能力。"""
    temp_repo.update_surfer_config(enabled=True, sandbox_channel_id="123456", tag_mode="none")
    surfer = CareSurfer(temp_repo)

    mock_bot = MagicMock()
    mock_channel = MagicMock()
    mock_channel.id = 123456
    mock_channel.send = AsyncMock()
    mock_bot.get_channel.return_value = mock_channel

    async def mock_history(*args, **kwargs):
        msg = MagicMock()
        msg.author.bot = False
        msg.author.display_name = "Yuuto"
        msg.clean_content = "這遊戲看起來真棒"
        yield msg

    mock_channel.history = mock_history

    captured_prompt = {}
    enqueued: list[dict[str, str]] = []
    mock_brain = MagicMock()
    async def mock_enqueue(task):
        enqueued.append({"system": task.system_prompt, "user": task.user_prompt})
        captured_prompt.setdefault("system", task.system_prompt)
        captured_prompt.setdefault("user", task.user_prompt)
        res = task.callback('{"selected_index": 0, "thought": "真有趣", "sherry_opinion": "推推", "new_interest": "ACG", "tagged_user_id": "", "tagged_user_name": "", "post_text": "剛看你們在聊遊戲，這篇新 RPG 也很扯！", "share_desire": 8}')
        if asyncio.iscoroutine(res):
            await res

    mock_brain.enqueue_task = mock_enqueue
    mock_bot.get_cog.return_value = mock_brain

    from handlers.care.surfer import CandidateArticle
    with patch.object(surfer, "fetch_candidates", AsyncMock(return_value=[
        CandidateArticle(index=0, title="新 RPG 報導", url="https://example.com/rpg", source="巴哈", summary="神級對談")
    ])):
        res = await surfer.surf_once(mock_bot, mode="surf_gossip", dry_run=False)

    assert res["status"] == "idle"
    # 第一次推論挑文章並寫觀點；第二次由大腦 INITIATE 決定要不要在這個頻道開口。
    assert len(enqueued) >= 2
    assert "頻道近期對話上下文" in enqueued[0]["system"]
    assert "直接丟瓜開聊" in enqueued[0]["system"]
    assert "主動開話題任務" in enqueued[-1]["system"]
    assert "【頻道最近的對話】" in enqueued[-1]["user"]


@pytest.mark.asyncio
async def test_daily_vibe_uses_brain_initiate_with_channel_context(temp_repo: CareRepository):
    """daily_vibe 應改走大腦 INITIATE，看得到頻道剛才的對話而不是重新破冰。"""
    temp_repo.update_surfer_config(
        enabled=True,
        sandbox_channel_id="123456",
        behavior_mode="daily_vibe",
    )
    surfer = CareSurfer(temp_repo)

    mock_bot = MagicMock()
    mock_channel = MagicMock()
    mock_channel.id = 123456
    mock_channel.send = AsyncMock()
    mock_bot.get_channel.return_value = mock_channel

    async def mock_history(*args, **kwargs):
        msg = MagicMock()
        msg.author.bot = False
        msg.author.display_name = "Yuuto"
        msg.clean_content = "等等要去吃拉麵"
        yield msg

    mock_channel.history = mock_history

    enqueued: list[dict[str, str]] = []
    mock_brain = MagicMock()

    async def mock_enqueue(task):
        enqueued.append({"system": task.system_prompt, "user": task.user_prompt})
        res = task.callback(
            '{"decision": "speak", "score": 7, "thought": "拉麵的話題", '
            '"reaction": "", "reply": "拉麵不加叉燒是在吃什麼"}'
        )
        if asyncio.iscoroutine(res):
            await res

    mock_brain.enqueue_task = mock_enqueue
    mock_bot.get_cog.return_value = mock_brain

    res = await surfer.surf_once(mock_bot, mode="daily_vibe", dry_run=False)

    assert res["status"] == "idle"
    assert res["post_text"] == "拉麵不加叉燒是在吃什麼"
    assert len(enqueued) == 1
    assert "主動開話題任務" in enqueued[0]["system"]
    assert "等等要去吃拉麵" in enqueued[0]["user"]


@pytest.mark.asyncio
async def test_care_cog_active_chat_window_reply_to_question(temp_repo: CareRepository):
    """測試群友在熱聊視窗內吐槽（如「你在公三小?」）時，CareCog 能以傲嬌生動口吻接話對線。"""
    from handlers.care.cog import CareCog

    mock_bot = MagicMock()
    mock_engine = MagicMock()
    mock_engine.repository = temp_repo
    temp_repo.update_surfer_config(
        sandbox_channel_id="123456",
        chat_reply_enabled=True,
        active_chat_window_seconds=300,
        active_hour_start=0,
        active_hour_end=0,
    )

    from handlers.care.surfer import update_bot_speak_timestamp
    # 模擬雪莉在 60 秒前剛在頻道說過話（在熱聊窗口 300 秒內）
    update_bot_speak_timestamp("123456")

    mock_channel = MagicMock()
    mock_channel.id = 123456
    mock_channel.send = AsyncMock()

    mock_message = MagicMock()
    mock_message.channel = mock_channel
    mock_message.author.bot = False
    mock_message.author.id = 999
    mock_message.author.display_name = "Yuuto"
    mock_message.content = "你在公三小?"
    mock_message.clean_content = "你在公三小?"
    mock_message.mentions = []
    mock_message.reference = None

    mock_brain = MagicMock()
    captured_chat = {}
    async def mock_enqueue(task):
        captured_chat["system"] = task.system_prompt
        captured_chat["user"] = task.user_prompt
        res = task.callback('{"thought": "敢質疑本名偵探！", "reply": "幹嘛啦！本名偵探是好心關心你，你居然說我在公三小！😤"}')
        if asyncio.iscoroutine(res):
            await res

    mock_brain.enqueue_task = mock_enqueue
    mock_bot.get_cog.side_effect = lambda name: mock_brain if name == "BotBrain" else None

    cog = CareCog(mock_bot, mock_engine)
    await cog.on_message(mock_message)

    # 檢查是否進行了熱聊推論：動態訊息在 user prompt，人格與規則在 system prompt。
    assert "你在公三小?" in captured_chat.get("user", "")
    assert "吐槽" in captured_chat.get("system", "")
    # 檢查是否已呼叫 send 發送回覆氣泡
    assert mock_channel.send.await_count >= 1
