"""handlers/social/vision_reaction.py
==================================
多模態 M1：主動視覺反應引擎 (Active Vision Reaction Engine)。
當頻道群友貼圖或分享截圖時，經過安全檢查後以低優先級任務生成第一人稱簡短反應（可 opt-out 關閉）。
"""
from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path
import re
import time
from typing import Any

import discord

from handlers.social.persona_guard import sanitize_reply
import settings

logger = logging.getLogger(__name__)

# 支援的圖片副檔名
_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"})

# 頻道層級冷卻紀錄 (channel_id -> last_timestamp)
_CHANNEL_LAST_REACTION: dict[int, float] = {}

# NSFW 敏感檔名與文字特徵
_NSFW_PATTERN = re.compile(
    r"(?:nsfw|r18|nude|naked|色圖|做愛|自慰|露點|流出|無碼|下體|大奶|巨乳)",
    re.IGNORECASE,
)


def is_image_attachment(attachment: discord.Attachment) -> bool:
    """檢查附件是否為圖片檔案。"""
    content_type = getattr(attachment, "content_type", None)
    if isinstance(content_type, str) and content_type.startswith("image/"):
        return True
    filename = getattr(attachment, "filename", "") or ""
    ext = Path(str(filename)).suffix.lower()
    return ext in _IMAGE_EXTENSIONS


def is_vision_reaction_enabled(channel_id: int | None = None) -> bool:
    """檢查主動視覺反應是否啟用（支援全域與頻道層級 opt-out）。"""
    global_enabled = getattr(settings, "VISION_REACTION_ENABLED", True)
    if not global_enabled:
        return False
    disabled_channels = getattr(settings, "VISION_REACTION_DISABLED_CHANNELS", [])
    if channel_id and channel_id in disabled_channels:
        return False
    return True


def should_trigger_vision_reaction(
    message: discord.Message,
    *,
    bot_user: discord.User | discord.ClientUser | None = None,
    now: float | None = None,
) -> bool:
    """評估訊息是否應觸發主動視覺反應。

    安全與門檻條件：
    1. 主動視覺反應未被 opt-out 關閉；
    2. 發話者非 Bot，且訊息未 @Mention 本身（避免與 Tag 路由雙重觸發）；
    3. 訊息包含有效圖片附件；
    4. 頻道冷卻期已過（預設 60 秒）；
    5. 非 NSFW 頻道中，若圖片檔名或文字含敏感詞則安全跳過；
    6. 非 NSFW 頻道中，標記為劇透（spoiler）的圖片不主動放大討論。
    """
    if not is_vision_reaction_enabled(message.channel.id):
        return False

    if getattr(message.author, "bot", False):
        return False

    if bot_user and bot_user in getattr(message, "mentions", []):
        return False

    attachments = getattr(message, "attachments", [])
    if not attachments:
        return False

    image_attachments = [a for a in attachments if is_image_attachment(a)]
    if not image_attachments:
        return False

    # 頻道安全狀態檢查
    is_channel_nsfw = bool(getattr(message.channel, "is_nsfw", lambda: False)())
    if not is_channel_nsfw and hasattr(message.channel, "guild") and message.channel.guild is None:
        is_channel_nsfw = True

    # 若非限制級頻道，檢驗檔名與內容安全性
    if not is_channel_nsfw:
        clean_content = getattr(message, "clean_content", "") or ""
        filenames = " ".join(str(getattr(a, "filename", "") or "") for a in image_attachments)
        if _NSFW_PATTERN.search(f"{clean_content} {filenames}"):
            logger.info("[VISION_REACTION] 檔名或內容含 NSFW 關鍵字，非 NSFW 頻道安全跳過: %s", message.channel.id)
            return False

        # 劇透圖片可能含敏感內容；在公開頻道不主動將它送進模型或放大討論。
        if any(getattr(a, "is_spoiler", False) is True for a in image_attachments):
            logger.info("[VISION_REACTION] 公開頻道中的劇透圖片，安全跳過: %s", message.channel.id)
            return False

    # 冷卻時間檢查
    current_ts = now if now is not None else time.time()
    cooldown = float(getattr(settings, "VISION_REACTION_COOLDOWN_SECONDS", 60.0))
    last_ts = _CHANNEL_LAST_REACTION.get(message.channel.id, 0.0)
    if current_ts - last_ts < cooldown:
        return False

    return True


def record_vision_reaction(channel_id: int, now: float | None = None) -> None:
    """記錄頻道的視覺反應觸發時間戳記。"""
    _CHANNEL_LAST_REACTION[channel_id] = now if now is not None else time.time()


def build_vision_reaction_prompt(image_count: int = 1, note: str = "") -> str:
    """建構供 LLM 生成視覺短反應之 Prompt。"""
    return (
        "【📸 多模態主動視覺搭話指令】\n"
        f"群友剛剛在頻道分享了 {image_count} 張圖片或截圖。\n"
        "請以妳身為橘雪莉的核心性格（可愛俏皮、帶點好奇傲嬌或犀利吐槽）給出一句第一人稱的即時搭話！\n"
        "【規則】：\n"
        "- 嚴禁長篇大論，嚴禁條列式分析，嚴禁客服腔！\n"
        "- 長度在 1～2 句話以內（約 15～35 字），就像平常跟朋友看到圖片時隨口說出的第一反應。\n"
        f"{note}"
    )


async def _load_image_payloads(
    attachments: list[Any],
) -> list[str]:
    """下載圖片附件並轉為 LLM 客戶端接受的 base64 字串列表。

    與社交路由的 ``chat_tools`` 使用相同的張數與容量上限。任一附件下載失敗
    時只略過該張；若全部失敗，呼叫端必須放棄本輪反應，避免模型在完全沒看到
    圖片的情況下產生幻覺式評論。
    """
    max_images = max(1, int(getattr(settings, "VISION_MAX_IMAGES", 3)))
    max_file_bytes = max(1, int(getattr(settings, "VISION_MAX_FILE_BYTES", 8 * 1024 * 1024)))
    payloads: list[str] = []

    for attachment in attachments:
        if len(payloads) >= max_images:
            break
        if not is_image_attachment(attachment):
            continue

        size = getattr(attachment, "size", 0)
        if isinstance(size, (int, float)) and size > max_file_bytes:
            logger.warning(
                "[VISION_REACTION] 圖片 %s 體積過大 (%s bytes)，超過上限略過",
                getattr(attachment, "filename", "unnamed"),
                size,
            )
            continue

        reader = getattr(attachment, "read", None)
        if not callable(reader):
            logger.debug("[VISION_REACTION] 附件缺少 read()，無法取得圖片內容")
            continue

        try:
            raw = await reader()
        except Exception as exc:
            logger.warning(
                "[VISION_REACTION] 讀取圖片 %s 失敗: %s",
                getattr(attachment, "filename", "unnamed"),
                exc,
            )
            continue

        if not isinstance(raw, (bytes, bytearray)) or not raw:
            continue
        if len(raw) > max_file_bytes:
            logger.warning(
                "[VISION_REACTION] 圖片 %s 解碼後超過容量上限，略過",
                getattr(attachment, "filename", "unnamed"),
            )
            continue

        payloads.append(base64.b64encode(bytes(raw)).decode("ascii"))

    return payloads


async def execute_vision_reaction(
    bot: Any,
    message: discord.Message,
) -> str | None:
    """以低優先級執行主動視覺反應並發送回頻道。"""
    if not should_trigger_vision_reaction(message, bot_user=bot.user):
        return None

    brain = bot.get_cog("BotBrain")
    if not brain or not hasattr(brain, "enqueue_task"):
        return None

    image_attachments = [a for a in message.attachments if is_image_attachment(a)]
    images = await _load_image_payloads(image_attachments)
    if not images:
        logger.info("[VISION_REACTION] 無可用圖片內容，取消本輪反應: %s", message.channel.id)
        return None

    # 先佔用冷卻，避免同一頻道在模型排隊期間被連續觸發。
    record_vision_reaction(message.channel.id)

    prompt = build_vision_reaction_prompt(len(images))
    try:
        reply_future: asyncio.Future[str] = asyncio.get_running_loop().create_future()

        async def _callback(text: str) -> None:
            if not reply_future.done():
                reply_future.set_result(text)

        # 低優先級任務（IDLE / TOOL_BACKGROUND）排入佇列。
        # 治理檢查統一由 BotBrain.enqueue_task 的 choke point 執行；在此不重複
        # check()，以免同一行動被扣兩次預算。governance_action 讓風險引擎套用
        # proactive_vision_reaction 的 MEDIUM 映射，而非泛用背景任務。
        priority = int(getattr(settings, "PRIORITY_VISION_REACTION", 30))
        from handlers.bot_brain.models import InferenceTask

        task = InferenceTask(
            priority=priority,
            system_prompt=prompt,
            user_prompt="[使用者分享了一張圖片，請給予第一人稱簡短即時反應]",
            callback=_callback,
            user_id=message.author.id,
            is_autonomous=True,
            images=images,
            governance_action="proactive_vision_reaction",
            governance_params={
                "channel_id": message.channel.id,
                "message_id": getattr(message, "id", None),
                "image_count": len(images),
            },
        )

        await brain.enqueue_task(task)
        raw_reply = await asyncio.wait_for(reply_future, timeout=30.0)
        clean_reply = (raw_reply or "").strip()

        # 人設防崩壞守門（與一般聊天出口共用同一套案例落盤邏輯）
        clean_reply = sanitize_reply(clean_reply, user_input="[image_shared]")

        if clean_reply:
            await message.channel.send(
                clean_reply,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return clean_reply
    except Exception as exc:
        logger.debug("[VISION_REACTION] 主動視覺反應執行失敗: %s", exc)
        return None

    return None
