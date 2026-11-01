"""
handlers/social/router.py
"""
import base64
import logging
import re
import time
from typing import Any

import discord
from discord.ext import commands

import settings
from handlers.social.action_recorder import record_tool_action
from handlers.social.observability import log_event
from handlers.social.rate_limiter import rate_limiter
from handlers.social.registry import TOOL_LIST
from handlers.social.utils import (
    build_reply_quote_context,
    fetch_context_script,
    remove_hourglass_reaction,
)

logger = logging.getLogger(__name__)

async def route_and_execute_tag(bot: commands.Bot, message: discord.Message, from_core: bool = False) -> None:
    brain_cog = bot.get_cog("BotBrain")
    if not brain_cog:
        return

    _msg_id = message.id
    _user = message.author.display_name
    _channel_id = message.channel.id
    _raw_input = message.clean_content

    # Phase 1: Rate Limiter 檢查（若請求由微核心轉發，已由 CoreGuard 驗證過，跳過二次扣除）
    if not from_core:
        uid = message.author.id
        if not rate_limiter.is_allowed(uid):
            cooldown = rate_limiter.get_remaining_cooldown(uid)
            reply_msg = settings.RATE_LIMIT_REPLY_MESSAGE.format(cooldown=cooldown)
            await message.reply(reply_msg, mention_author=True)
            return

    try:
        await message.add_reaction("⏳")
    except Exception:
        pass

    context_script = await fetch_context_script(message.channel, message, bot=bot)

    # 回覆引用：resolved 常因重啟／舊訊息而失效，交由 utils 補抓並轉成明確語境。
    quote_context = await build_reply_quote_context(
        message,
        bot_user_id=getattr(getattr(bot, "user", None), "id", None),
    )

    # 組裝純淨對話歷史（當前發言由 user_prompt 獨立承載，消除 System Prompt 內的重複輸入）
    if context_script and quote_context:
        full_context = f"{context_script}\n\n{quote_context}"
    elif context_script:
        full_context = context_script
    elif quote_context:
        full_context = quote_context
    else:
        full_context = ""

    vibe_mgr: Any = bot.get_cog("VibeManager")
    vibe_snippet = vibe_mgr.get_top_topics_snippet(message.channel.id) if vibe_mgr else ""

    mem_mgr: Any = bot.get_cog("MemoryManager")
    if mem_mgr:
        # ❤️ 被 Tag 是一次明確的社交互動，好感度 +2
        mem_mgr.relations.update_affinity(message.author.id, 2)
        # ✅ [修復] 移除此處的記憶重複注入 —— 長期記憶統一由 chat_tools.py 的
        # build_user_memory_block() 注入，防止同一份記憶在 Prompt 中出現兩次
        # (原本 router.py 注入一次 + chat_tools.py 又注入一次，浪費 10-15% Token)

    # 多模態圖片辨識處理（具備張數與檔案容量上限防護）
    images_b64: list[str] = []
    max_images = getattr(settings, "VISION_MAX_IMAGES", 3)
    max_file_bytes = getattr(settings, "VISION_MAX_FILE_BYTES", 8 * 1024 * 1024)  # 8MB
    for att in message.attachments:
        if len(images_b64) >= max_images:
            break
        if att.content_type and att.content_type.startswith('image/'):
            att_size = getattr(att, "size", 0)
            if isinstance(att_size, (int, float)) and att_size > max_file_bytes:
                logger.warning("social_chat: 圖片 %s 體積過大 (%s bytes)，超過上限略過", getattr(att, "filename", "unnamed"), att_size)
                continue
            try:
                img_bytes = await att.read()
                images_b64.append(base64.b64encode(img_bytes).decode('utf-8'))
            except Exception as e:
                logger.error(f"social_chat: Failed to download image: {e}")

    bot_name = bot.user.display_name if bot.user else "雪莉"
    bot_mention = f"@{bot_name}"
    base_query = message.clean_content.replace(bot_mention, "").strip()
    base_query = re.sub(r"<@!?\d+>", "", base_query).strip()

    visual_context = ""
    vision_model_setting = str(getattr(settings, "VISION_MODEL", "") or "").strip()
    images_to_pass: list[str] | None = None

    def _is_valid_vision_model(m_str: str) -> bool:
        if not m_str:
            return False
        parts = m_str.split(":", 1)
        actual = parts[1].strip() if len(parts) > 1 else parts[0].strip()
        return bool(actual)

    if images_b64:
        images_to_pass = images_b64
        visual_context = "【原生端到端視覺】：妳正親眼直接注視著眼前這張圖片。"
        if _is_valid_vision_model(vision_model_setting):
            logger.info("social_chat: 收到圖片，啟用單階段視覺模型直出模式 (VISION_MODEL: %s)", vision_model_setting)
            log_event(
                "vision_analysis",
                message_id=_msg_id,
                user=_user,
                channel_id=_channel_id,
                vision_hit_count=len(images_b64),
                reply_preview=f"[單階段視覺模型直出 ({vision_model_setting})]",
            )
        else:
            logger.info("social_chat: VISION_MODEL 為空或未指定模型名，啟用主力模型原生多模態直出！")
            log_event(
                "vision_analysis",
                message_id=_msg_id,
                user=_user,
                channel_id=_channel_id,
                vision_hit_count=0,
                reply_preview="[端到端原生多模態，直接交由主力模型看圖]",
            )

    # 組合最終的 user_query（保持純淨的使用者輸入，視覺情報完全交由 visual_context 獨立傳遞）
    user_query = base_query.strip()

    if not user_query and not visual_context:
        user_query = "（不說話戳了你一下）"

    # 圖片解析結果只存在於 user_query（開頭），不重複注入 full_context，避免 prompt 內同一視覺描述出現兩次

    # ── [極速直通快路徑 Fast Path] YouTube 網址與純音樂播放控制 ─────────
    sq = user_query
    regex_target = base_query if base_query else sq
    youtube_pattern = re.compile(
        r"https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)[\w-]+",
        re.IGNORECASE,
    )
    summary_pattern = re.compile(
        r"(解析|總結|大意|文章|內容|新聞|看過|重點|這部|這張|這篇|介紹|心得|說明|翻譯)",
        re.IGNORECASE,
    )
    if not images_to_pass and youtube_pattern.search(regex_target):
        if not summary_pattern.search(regex_target):
            from handlers.social.tools.music_tool import MusicTool

            music_tool = MusicTool()
            logger.info("social_chat: [YouTube 快路徑] 偵測到 YouTube 音樂連結，直接進入 MusicTool。")
            log_event(
                "intent_classify",
                message_id=_msg_id,
                user=_user,
                channel_id=_channel_id,
                raw_input=sq,
                intent="MUSIC",
                intent_source="youtube_fastpath",
            )
            try:
                music_result = await music_tool.execute(
                    bot, message, sq, vibe_snippet, full_context
                )
                music_obs = getattr(music_result, "summary_text", "") or str(music_result or "")
                record_tool_action(
                    message, "MUSIC", sq, music_obs or "已接收播放指令", status="success"
                )
            finally:
                await remove_hourglass_reaction(message, bot.user)
            return

    # 決定調度模式：INTEGRATED (預設單階段整合自主調度) | PARALLEL (舊版雙階段並行) | REACT (多步驟 ReAct)
    dispatch_mode = getattr(settings, "AGENT_DISPATCH_MODE", "INTEGRATED").upper()
    if dispatch_mode not in ("INTEGRATED", "PARALLEL", "REACT"):
        dispatch_mode = "INTEGRATED"

    from handlers.social.dispatch_agent import DispatchAgentTool

    dispatch_agent = DispatchAgentTool(TOOL_LIST, mode=dispatch_mode)  # type: ignore[arg-type]

    logged_raw_input = user_query if user_query else ("（上傳了一張圖片）" if visual_context else "（不說話戳了你一下）")
    log_event(
        "intent_classify",
        message_id=_msg_id,
        user=_user,
        channel_id=_channel_id,
        raw_input=logged_raw_input,
        intent=f"{dispatch_mode}_AGENT",
        intent_source=f"{dispatch_mode.lower()}_router",
    )

    logger.info(
        "social_chat: [%s Router] 訊息 → DispatchAgentTool(%s)  user=%s query=%s visual=%s",
        dispatch_mode.capitalize(),
        dispatch_mode,
        _user,
        (user_query or "（無文字）")[:80],
        bool(visual_context),
    )

    try:
        await dispatch_agent.execute(
            bot,
            message,
            user_query,
            vibe_snippet,
            full_context,
            images=images_to_pass,
            visual_context=visual_context,
        )
    finally:
        await remove_hourglass_reaction(message, bot.user)
        try:
            from handlers.interaction import get_turn_gateway

            gateway = get_turn_gateway()
            if gateway is not None:
                await gateway.note_external_bot_spoke(
                    message.channel.id,
                    sent_at=time.time(),
                    target_author_id=message.author.id,
                )
        except Exception:
            pass
