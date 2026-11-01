"""tests/unit/test_vision_reaction_r23.py
======================================
R23: 多模態 M1（主動視覺反應）與前置安全性修復單元測試。
驗證：
1. chat_tools.py 頻道 NSFW 隔離與他人上下文污染防護；
2. image_tool.py search 與 execute 之 allow_nsfw 安全閘門；
3. 主動視覺反應附件判定、NSFW 檔名攔截、冷卻時間與 Opt-out 機制；
4. 主動視覺反應端到端管線與防崩壞整合。
"""
from __future__ import annotations

import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock, patch

import settings
from core.risk_engine import ActionRiskLevel, RiskPolicyEngine
from handlers.social.tools.chat_tools import ChatTool
from handlers.social.tools.image_tool import ImageSearchTool
from handlers.social.vision_reaction import (
    execute_vision_reaction,
    is_image_attachment,
    is_vision_reaction_enabled,
    record_vision_reaction,
    should_trigger_vision_reaction,
)


# ---------------------------------------------------------------------------
# 1. 前置安全修復：chat_tools.py
# ---------------------------------------------------------------------------

def test_chat_tools_nsfw_channel_guard():
    tool = ChatTool()

    # 模擬非 NSFW 頻道
    safe_channel = MagicMock()
    safe_channel.is_nsfw.return_value = False
    safe_channel.guild = MagicMock()

    msg_safe = MagicMock()
    msg_safe.channel = safe_channel
    msg_safe.clean_content = "好想色色喔"

    # 1. 當前查詢含色情詞彙，但在全年齡/非限制級頻道 -> 必須走公眾合規守則，嚴禁解除性愛特權
    # 測試執行前之 can_handle_by_regex
    assert tool.can_handle_by_regex("好想色色喔")

    # 2. 他人歷史上下文污染防護：即使 full_context 歷史含有色色詞彙，但當前使用者輸入乾淨，不得觸發 NSFW
    clean_query = "今天晚餐要吃什麼？"

    # 驗證 clean_query 本身不被判定為 NSFW
    assert not tool.can_handle_by_regex(clean_query)


# ---------------------------------------------------------------------------
# 2. 前置安全修復：image_tool.py
# ---------------------------------------------------------------------------

def test_image_tool_allow_nsfw_guard():
    tool = ImageSearchTool()

    async def _run():
        with patch.object(tool, "_tiered_booru_search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = ("http://example.com/img.png", ["tag1"], False, "safebooru")

            # 1. allow_nsfw=False 時，即使 query 包含色圖關鍵字，is_nsfw 仍強制為 False
            await tool.search("給我來張色圖", allow_nsfw=False)
            assert mock_search.call_args[1]["is_nsfw"] is False

            # 2. allow_nsfw=True 時，才允許為 True
            await tool.search("給我來張色圖", allow_nsfw=True)
            assert mock_search.call_args[1]["is_nsfw"] is True

            # 3. 正常一般圖片無論如何均為 False
            await tool.search("可愛的橘雪莉", allow_nsfw=True)
            assert mock_search.call_args[1]["is_nsfw"] is False

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 3. 主動視覺反應：觸發條件、冷卻與 Opt-out
# ---------------------------------------------------------------------------

def test_is_image_attachment():
    att_png = MagicMock(content_type="image/png", filename="photo.png")
    att_jpg = MagicMock(content_type=None, filename="screenshot.jpg")
    att_webp = MagicMock(content_type="image/webp", filename="sticker.webp")
    att_pdf = MagicMock(content_type="application/pdf", filename="doc.pdf")
    att_txt = MagicMock(content_type="text/plain", filename="readme.txt")

    assert is_image_attachment(att_png)
    assert is_image_attachment(att_jpg)
    assert is_image_attachment(att_webp)
    assert not is_image_attachment(att_pdf)
    assert not is_image_attachment(att_txt)


def test_should_trigger_vision_reaction_rules():
    channel = MagicMock()
    channel.id = 1001
    channel.is_nsfw.return_value = False
    channel.guild = MagicMock()

    author = MagicMock()
    author.bot = False

    msg = MagicMock()
    msg.channel = channel
    msg.author = author
    msg.mentions = []
    msg.clean_content = "看我今天拍的！"
    msg.attachments = [MagicMock(content_type="image/png", filename="cat.png")]

    bot_user = MagicMock()

    # 1. 正常圖片訊息 -> 應觸發
    assert should_trigger_vision_reaction(msg, bot_user=bot_user, now=1000.0)

    # 2. 發話者為 Bot -> 不觸發
    msg.author.bot = True
    assert not should_trigger_vision_reaction(msg, bot_user=bot_user, now=1000.0)
    msg.author.bot = False

    # 3. 訊息 @提及 Bot -> 不觸發（由 Tag 意圖路由接管）
    msg.mentions = [bot_user]
    assert not should_trigger_vision_reaction(msg, bot_user=bot_user, now=1000.0)
    msg.mentions = []

    # 4. 無任何圖片附件 -> 不觸發
    msg.attachments = []
    assert not should_trigger_vision_reaction(msg, bot_user=bot_user, now=1000.0)
    msg.attachments = [MagicMock(content_type="image/png", filename="cat.png")]

    # 5. 非限制級頻道中圖片檔名含 NSFW 關鍵字 -> 安全攔截不觸發
    msg.attachments = [MagicMock(content_type="image/png", filename="r18_nude_art.png")]
    assert not should_trigger_vision_reaction(msg, bot_user=bot_user, now=1000.0)
    msg.attachments = [MagicMock(content_type="image/png", filename="cat.png")]

    # 6. 冷卻時間（60秒）：觸發後立即再次檢查 -> 不觸發
    record_vision_reaction(channel.id, now=1000.0)
    assert not should_trigger_vision_reaction(msg, bot_user=bot_user, now=1030.0)
    # 超過 60 秒 -> 可再次觸發
    assert should_trigger_vision_reaction(msg, bot_user=bot_user, now=1061.0)


def test_vision_reaction_opt_out(monkeypatch):
    channel = MagicMock()
    channel.id = 2002
    channel.is_nsfw.return_value = False
    channel.guild = MagicMock()

    msg = MagicMock()
    msg.channel = channel
    msg.author = MagicMock(bot=False)
    msg.mentions = []
    msg.attachments = [MagicMock(content_type="image/png", filename="pic.png")]

    # 1. 全域 opt-out 關閉
    monkeypatch.setattr(settings, "VISION_REACTION_ENABLED", False)
    assert not is_vision_reaction_enabled(channel.id)
    assert not should_trigger_vision_reaction(msg, now=5000.0)

    # 2. 頻道專屬 opt-out
    monkeypatch.setattr(settings, "VISION_REACTION_ENABLED", True)
    monkeypatch.setattr(settings, "VISION_REACTION_DISABLED_CHANNELS", {2002})
    assert not is_vision_reaction_enabled(2002)
    assert not should_trigger_vision_reaction(msg, now=5000.0)

    # 其他頻道正常開啟
    assert is_vision_reaction_enabled(3003)


def test_vision_reaction_skips_spoiler_in_public_channel(monkeypatch):
    channel = MagicMock()
    channel.id = 4004
    channel.is_nsfw.return_value = False
    channel.guild = MagicMock()

    attachment = MagicMock(content_type="image/png", filename="pic.png")
    attachment.is_spoiler = True

    msg = MagicMock()
    msg.channel = channel
    msg.author = MagicMock(bot=False)
    msg.mentions = []
    msg.clean_content = "猜猜看這是什麼"
    msg.attachments = [attachment]

    monkeypatch.setattr(settings, "VISION_REACTION_ENABLED", True)
    assert not should_trigger_vision_reaction(msg, now=7000.0)


def test_proactive_vision_reaction_risk_is_medium():
    """視覺搭話必須有明確風險映射，避免被風險引擎預設為 HIGH 而永遠拒絕。"""
    engine = RiskPolicyEngine()
    assert (
        engine.classify_risk("proactive_vision_reaction")
        is ActionRiskLevel.MEDIUM
    )


def test_botbrain_gate_uses_task_governance_action():
    """BotBrain 必須以任務自訂的 governance_action 送審，而非一律泛用背景任務。"""
    from handlers.bot_brain.cog import BotBrain
    from handlers.bot_brain.models import InferenceTask

    guard = MagicMock()
    guard.check.return_value = MagicMock(allowed=True)
    brain = BotBrain(MagicMock(), autonomy_guard=guard)

    task = InferenceTask(
        priority=settings.PRIORITY_HEARTBEAT,
        system_prompt="s",
        user_prompt="u",
        callback=AsyncMock(),
        task_id="vision-governance",
        is_autonomous=True,
        governance_action="proactive_vision_reaction",
        governance_params={"channel_id": 1234},
    )

    async def _run():
        await brain.enqueue_task(task)
        assert brain._queue.qsize() == 1

    asyncio.run(_run())
    args, kwargs = guard.check.call_args
    assert args[0] == "proactive_vision_reaction"
    assert kwargs["params"]["channel_id"] == 1234
    assert kwargs["params"]["task_id"] == "vision-governance"


# ---------------------------------------------------------------------------
# 4. 端到端管線與生成流程
# ---------------------------------------------------------------------------

def _image_attachment(*, filename: str = "figure.jpg", payload: bytes = b"fake-jpeg"):
    attachment = MagicMock(content_type="image/jpeg", filename=filename)
    attachment.size = len(payload)
    attachment.is_spoiler = False
    attachment.read = AsyncMock(return_value=payload)
    return attachment


def test_execute_vision_reaction_pipeline(monkeypatch):
    channel = MagicMock()
    channel.id = 8888
    channel.is_nsfw.return_value = False
    channel.guild = MagicMock()
    channel.send = AsyncMock()

    attachment = _image_attachment(payload=b"fake-jpeg-bytes")

    msg = MagicMock()
    msg.channel = channel
    msg.author = MagicMock(id=123, bot=False)
    msg.mentions = []
    msg.clean_content = "看新買的模型！"
    msg.attachments = [attachment]

    bot = MagicMock()
    bot.user = MagicMock(id=999)

    brain = MagicMock()
    bot.get_cog.return_value = brain
    captured = {}

    async def fake_enqueue_task(task):
        captured["task"] = task
        # 模擬 LLM 回應
        await task.callback("哇！這隻模型塗裝好細緻喔，真有眼光～")

    brain.enqueue_task = AsyncMock(side_effect=fake_enqueue_task)

    monkeypatch.setattr(settings, "VISION_REACTION_ENABLED", True)
    monkeypatch.setattr(settings, "VISION_REACTION_COOLDOWN_SECONDS", 0.0)

    async def _run():
        res = await execute_vision_reaction(bot, msg)
        assert res == "哇！這隻模型塗裝好細緻喔，真有眼光～"
        channel.send.assert_awaited_once()
        sent_content = channel.send.await_args.args[0]
        assert sent_content == "哇！這隻模型塗裝好細緻喔，真有眼光～"

        task = captured["task"]
        assert task.images == [base64.b64encode(b"fake-jpeg-bytes").decode("ascii")]
        assert task.governance_action == "proactive_vision_reaction"
        assert task.governance_params["channel_id"] == channel.id
        assert task.is_autonomous is True

    asyncio.run(_run())


def test_execute_vision_reaction_aborts_when_image_unreadable(monkeypatch):
    channel = MagicMock()
    channel.id = 9999
    channel.is_nsfw.return_value = False
    channel.guild = MagicMock()
    channel.send = AsyncMock()

    attachment = _image_attachment()
    attachment.read = AsyncMock(side_effect=OSError("download failed"))

    msg = MagicMock()
    msg.channel = channel
    msg.author = MagicMock(id=123, bot=False)
    msg.mentions = []
    msg.clean_content = "看這個"
    msg.attachments = [attachment]

    bot = MagicMock()
    bot.user = MagicMock(id=999)
    brain = MagicMock()
    brain.enqueue_task = AsyncMock()
    bot.get_cog.return_value = brain

    monkeypatch.setattr(settings, "VISION_REACTION_ENABLED", True)
    monkeypatch.setattr(settings, "VISION_REACTION_COOLDOWN_SECONDS", 0.0)

    async def _run():
        assert await execute_vision_reaction(bot, msg) is None
        brain.enqueue_task.assert_not_awaited()
        channel.send.assert_not_awaited()

    asyncio.run(_run())
