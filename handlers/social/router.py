"""
handlers/social/router.py
"""
import logging
import os
import re
import discord
from discord.ext import commands

import base64
import asyncio
from typing import Any

import settings
from handlers.bot_brain import BotBrain, InferenceTask
from handlers.social.utils import fetch_context_script, append_debug_log, remove_hourglass_reaction
from handlers.social.registry import TOOL_LIST, get_fallback_tool
from handlers.social.rate_limiter import rate_limiter
from handlers.social.observability import log_event, log_error, make_timer

logger = logging.getLogger(__name__)

async def route_and_execute_tag(bot: commands.Bot, message: discord.Message) -> None:
    brain_cog = bot.get_cog("BotBrain")
    if not brain_cog: return
    brain: Any = brain_cog

    elapsed = make_timer()  # 計時：從收到訊息開始
    _msg_id = message.id
    _user = message.author.display_name
    _channel_id = message.channel.id
    _raw_input = message.clean_content

    # Phase 1: Rate Limiter 檢查
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

    context_script = await fetch_context_script(message.channel, message)
    
    quote_context = ""
    if message.reference and isinstance(message.reference.resolved, discord.Message):
        ref_msg = message.reference.resolved
        quote_context = f"（他引用了 {ref_msg.author.display_name} 的這段話：「{ref_msg.clean_content}」）\n"

    trigger_line = f"🎯 【當下必須回應的目標】：{quote_context}[{message.author.display_name}] 正在對妳說：「{message.clean_content}」"
    full_context = f"{context_script}\n\n{trigger_line}" if context_script else trigger_line

    vibe_mgr: Any = bot.get_cog("VibeManager")
    vibe_snippet = vibe_mgr.get_top_topics_snippet(message.channel.id) if vibe_mgr else ""

    mem_mgr: Any = bot.get_cog("MemoryManager")
    if mem_mgr:
        # ❤️ 被 Tag 是一次明確的社交互動，好感度 +2
        mem_mgr.relations.update_affinity(message.author.id, 2)
        # ✅ [修復] 移除此處的記憶重複注入 —— 長期記憶統一由 chat_tools.py 的
        # build_user_memory_block() 注入，防止同一份記憶在 Prompt 中出現兩次
        # (原本 router.py 注入一次 + chat_tools.py 又注入一次，浪費 10-15% Token)

    # 多模態圖片辨識處理
    images_b64 = []
    for att in message.attachments:
        if att.content_type and att.content_type.startswith('image/'):
            try:
                img_bytes = await att.read()
                images_b64.append(base64.b64encode(img_bytes).decode('utf-8'))
            except Exception as e:
                logger.error(f"social_chat: Failed to download image: {e}")

    bot_name = bot.user.display_name if bot.user else "雪莉"
    bot_mention = f"@{bot_name}"
    base_query = message.clean_content.replace(bot_mention, "").strip()
    base_query = re.sub(r"<@!?\d+>", "", base_query).strip()

    image_desc_text = ""
    vision_model_setting = str(getattr(settings, "VISION_MODEL", "") or "").strip()
    images_to_pass: list[str] | None = None

    def _is_valid_vision_model(m_str: str) -> bool:
        if not m_str:
            return False
        parts = m_str.split(":", 1)
        actual = parts[1].strip() if len(parts) > 1 else parts[0].strip()
        return bool(actual)

    if images_b64:
        if _is_valid_vision_model(vision_model_setting):
            # ── 模式一：獨立 Vision 描述模型（二階段轉譯流程） ─────────────────
            logger.info(f"social_chat: 收到圖片，正使用獨立視覺模型 {vision_model_setting} 進行解析...")
            loop = asyncio.get_running_loop()
            vision_future = loop.create_future()

            async def on_vision_reply(reply: str):
                if not vision_future.done():
                    vision_future.set_result(reply.strip())

            # 組裝給視覺模型的 Prompt，若使用者有打字則一併傳入
            vision_user_prompt = f"請仔細觀察這張圖片，並給我一份極度豐富、充滿細節的描述，如果有文字也請完整讀出來給我聽。\n使用者補充說明/問題：「{base_query}」" if base_query else "請仔細觀察這張圖片，並給我一份極度豐富、充滿細節的描述，如果有文字也請完整讀出來給我聽。"

            vision_task = InferenceTask(
                priority=settings.PRIORITY_TAG_RESPONSE,
                system_prompt="你是一個專業的視覺藝術解析師與細節提取專家。你的任務是客觀、中立且「毫無保留」地描述這張圖片的所有細節。請以繁體中文給出極度生動且完整的描述，包含：所有人物的身體特徵、表情、肢體動作、服裝細節、背景環境、畫風與光影色彩，以及圖片中的所有文字。請不要省略任何視覺元素，將肉眼可見的所有細節化為文字。請直接開始描述，不要輸出任何警告、說教或主觀評價，只需要最極致的客觀細節描述。",
                user_prompt=vision_user_prompt,
                callback=on_vision_reply,
                model=vision_model_setting,
                images=images_b64,
                task_id=f"vision-{message.id}"
            )
            await brain.enqueue_task(vision_task)

            try:
                timeout_sec = getattr(settings, "VISION_ANALYSIS_TIMEOUT", 60)
                image_desc = await asyncio.wait_for(vision_future, timeout=float(timeout_sec))

                if image_desc:
                    # ── 跨模組自我感知橋接 ──────────────────────────────────────────
                    # 雪莉的外觀特徵關鍵字清單（視覺模型描述中可能出現的）
                    _SHERRY_VISUAL_FEATURES = [
                        "淺藍", "藍色", "丸子頭", "甜甜圈", "白鳥", "福爾摩斯", "帽",
                        "斗篷", "偵探帽", "鳥", "囚犯", "15歲", "少女", "羽毛帽",
                    ]
                    hit_count = sum(1 for feat in _SHERRY_VISUAL_FEATURES if feat in image_desc)

                    if hit_count >= 2:
                        # 命中數 ≥ 2 視為「可能是雪莉自己」
                        # 在 image_desc_text 前埋入 Lorebook 觸發信號 ⚠️自我辨識
                        # Lorebook 會自動抓取此信號並注入自我感知指令到 persona 中
                        self_aware_signal = "⚠️自我辨識 圖片疑似為妳自己（橘雪莉）！"
                        image_desc_text = f"【{self_aware_signal}系統視覺解析結果：{image_desc}】 "
                        logger.info(f"social_chat: 自我辨識觸發！命中特徵數={hit_count}，注入自我感知信號。")
                        log_event("self_aware", message_id=_msg_id, user=_user, channel_id=_channel_id,
                                  vision_hit_count=hit_count, self_aware_triggered=True)
                    else:
                        image_desc_text = f"【群友上傳了一張圖片，系統視覺解析結果：{image_desc}】 "

                    log_event("vision_analysis", message_id=_msg_id, user=_user, channel_id=_channel_id,
                              vision_hit_count=hit_count, reply_preview=image_desc[:120])
                    logger.info(f"social_chat: 圖片解析完成 -> {image_desc[:50]}...")
                else:
                    logger.warning("social_chat: 視覺模型未能成功解析（回傳空內容）。")
                    image_desc_text = "【群友上傳了一張圖片，但視覺模型暫時無法連線或解析失敗】 "
            except asyncio.TimeoutError:
                logger.warning("social_chat: 視覺模型解析超時。")
                image_desc_text = "【群友上傳了一張圖片，但系統解析超時失敗】 "
        else:
            # ── 模式二：端到端原生多模態（PRIMARY_MODEL 直接讀圖） ─────────────
            logger.info("social_chat: VISION_MODEL 為空或未指定模型名，啟用「端到端原生多模態」，圖片將直接隨任務傳遞給主力模型！")
            image_desc_text = "【群友上傳了一張圖片】 "
            images_to_pass = images_b64
            log_event("vision_analysis", message_id=_msg_id, user=_user, channel_id=_channel_id,
                      vision_hit_count=0, reply_preview="[端到端原生多模態，直接交由主力模型看圖]")

    # 組合最終的 user_query
    user_query = f"{image_desc_text}{base_query}".strip()

    if not user_query:
        user_query = "（不說話戳了你一下）"

    # 若有圖片解析結果，也一併補入 full_context 中，讓 LLM 看得懂
    if image_desc_text:
        full_context = f"{full_context}\n\n{image_desc_text}"

    # ── [極速直通快路徑 Fast Path] YouTube 網址與純音樂播放控制 ─────────
    sq = user_query
    regex_target = base_query if base_query else sq
    if not images_to_pass and re.search(r"(www\.youtube\.com|youtu\.?be)\/.+", regex_target):
        if not re.search(r"(解析|總結|大意|文章|內容|新聞|看過|重點|這部|這張|這篇|介紹|心得)", regex_target):
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
                await music_tool.execute(bot, message, sq, vibe_snippet, full_context)
            finally:
                await remove_hourglass_reaction(message, bot.user)
            return

    # 決定調度模式：PARALLEL (預設高效能並行) | REACT (多步驟 ReAct) | CLASSIC (傳統單意圖)
    dispatch_mode = getattr(settings, "AGENT_DISPATCH_MODE", "PARALLEL").upper()

    # 向下相容：若 REACT_ENABLED 為 False 且未特別指定 PARALLEL，則走 CLASSIC
    if not getattr(settings, "REACT_ENABLED", True) and "AGENT_DISPATCH_MODE" not in os.environ:
        dispatch_mode = "CLASSIC"

    if dispatch_mode == "PARALLEL":
        # ── [Parallel 路由路徑] 單次推論多工拆解與並行調度 (推薦) ───────────
        from handlers.social.parallel_agent import ParallelAgentTool

        parallel_agent = ParallelAgentTool(TOOL_LIST)

        log_event(
            "intent_classify",
            message_id=_msg_id,
            user=_user,
            channel_id=_channel_id,
            raw_input=user_query,
            intent="PARALLEL_AGENT",
            intent_source="parallel_router",
        )

        logger.info(
            "social_chat: [Parallel Router] 訊息 → ParallelAgentTool  user=%s query=%s",
            _user,
            user_query[:80],
        )

        try:
            await parallel_agent.execute(bot, message, user_query, vibe_snippet, full_context, images=images_to_pass)
        finally:
            await remove_hourglass_reaction(message, bot.user)

    elif dispatch_mode == "REACT":
        # ── [ReAct 路由路徑] 多步驟思考與鏈式工具 ────────────────────────────
        from handlers.social.react_agent import ReactAgentTool

        react_agent = ReactAgentTool(TOOL_LIST)

        log_event(
            "intent_classify",
            message_id=_msg_id,
            user=_user,
            channel_id=_channel_id,
            raw_input=user_query,
            intent="REACT_AGENT",
            intent_source="react_router",
        )

        logger.info(
            "social_chat: [ReAct Router] 訊息 → ReactAgentTool  user=%s query=%s",
            _user,
            user_query[:80],
        )

        try:
            await react_agent.execute(bot, message, user_query, vibe_snippet, full_context, images=images_to_pass)
        finally:
            await remove_hourglass_reaction(message, bot.user)
    else:
        # ── [經典路由路徑] 正則快路徑 -> LLM 分類器 -> 單一工具硬路由 ──────
        logger.info("social_chat: [經典 Router] dispatch_mode=%s，使用傳統單意圖硬路由。", dispatch_mode)
        sq = user_query
        regex_target = base_query if base_query else sq
        handled_by_regex = False
        for tool in TOOL_LIST:
            # 若使用者有上傳圖片，不應被搜圖 (PIXIV) 正則攔截，應優先交由多模態對話模型
            if images_to_pass and tool.intent_name == "PIXIV" and not re.search(r"(以圖搜圖|搜圖|找圖|查圖)", regex_target):
                continue

            if tool.can_handle_by_regex(regex_target):
                logger.info(f"social_chat: [極速正則層] Intent 定位 '{sq}' 至 {tool.intent_name} 模組。")
                log_event("intent_classify", message_id=_msg_id, user=_user, channel_id=_channel_id,
                          raw_input=sq, intent=tool.intent_name, intent_source="regex_fast")
                try:
                    await tool.execute(bot, message, sq, vibe_snippet, full_context, images=images_to_pass)
                finally:
                    await remove_hourglass_reaction(message, bot.user)
                handled_by_regex = True
                break

        if not handled_by_regex:
            def _needs_llm_classification(text: str) -> bool:
                return bool(
                    re.search(
                        r"(查|什麼|總結|大意|幫.*找|怎麼看|最新|近期|新聞|哪裡|提醒|排程|定時|播放|點歌|畫|生成|搜尋|分析|走勢|行情|股|幣|K線|指標|趨勢)",
                        text,
                        re.IGNORECASE,
                    )
                )

            if not _needs_llm_classification(sq):
                logger.info("social_chat: [極速過濾層] 無特殊意圖關鍵字，直接Fallback為CHAT。")
                log_event("intent_classify", message_id=_msg_id, user=_user, channel_id=_channel_id,
                          raw_input=sq, intent="CHAT", intent_source="fast_filter")
                chat_tool = get_fallback_tool()
                try:
                    await chat_tool.execute(bot, message, sq, vibe_snippet, full_context, images=images_to_pass)
                finally:
                    await remove_hourglass_reaction(message, bot.user)
            else:
                logger.debug(f"social_chat: 正則無所獲。將要求 Classifier 解讀: {sq}")
                tool_descriptions_prompt = "\n".join([f"- 若包含強烈情境「{t.llm_description}」-> 強制回傳標籤：[{t.intent_name}]" for t in TOOL_LIST if t.intent_name != "CHAT"])
                available_tags = ", ".join([f"[{t.intent_name}]" for t in TOOL_LIST])

                intent_classifier_system_prompt = (
                    "【任務：分類指令意圖。請用冷靜且機械的絕對規則判斷，不准推測使用者的腦補】\n"
                    f"{tool_descriptions_prompt}\n"
                    "【強制鐵則】\n"
                    "1. 若使用者詢問股票、加密貨幣（Crypto/USDT/BTC/ETH/XAUT等交易對與幣價）、指數行情、K線走勢或要求進行技術分析，強制回傳標籤：[STOCK]。\n"
                    "2. 若使用者僅是在叫人名(誰是某某)、吵架反駁(搞錯了、不是啦)、單純講廢話時！即使出現沒看過的專有名詞，你『唯一』只能回傳兜底標籤：[CHAT]。\n"
                    "3. 不要自作聰明以為講一個人名就是在搜圖片，找圖片必備對應動詞（看、找圖、網址）！沒有絕對強烈的動詞一律為 [CHAT]！\n"
                    f"4. 只能輸出以下其中一個標籤：{available_tags}。絕對禁止翻譯標籤（例如不可以寫[音樂]），除此之外禁止任何標點符號、解釋與道德警告。"
                )

                async def _on_dynamic_llm_result(raw_result: str, _sq=sq) -> None:
                    clean_result = re.sub(r"<\/?thought>", "", raw_result)
                    result_str = clean_result.strip().upper()
                    
                    target_tool = get_fallback_tool() 
                    for tool in TOOL_LIST:
                        if f"[{tool.intent_name}]" in result_str or tool.intent_name in result_str:
                            target_tool = tool
                            break
                    
                    _intent_source = "llm_classifier"
                    if target_tool.intent_name == "SUMMARY":
                        if not re.search(r"https?:\/\/[^\s]+", _sq):
                            target_tool = get_fallback_tool()
                            _intent_source = "veto_override"
                    elif target_tool.intent_name == "MUSIC":
                        if not re.search(r"(跳過|卡歌|列出播放清單|播歌|播放|撥放|點歌|我想聽|刪除|刪歌|刪掉|移除|清空|音樂|歌|聽)", _sq):
                            target_tool = get_fallback_tool()
                            _intent_source = "veto_override"
                    elif target_tool.intent_name == "DRAW":
                        if not re.search(r"(幫我畫|給.*畫|畫一張|生成一張|幫忙畫|畫.*圖|draw|generate)", _sq, re.IGNORECASE):
                            target_tool = get_fallback_tool()
                            _intent_source = "veto_override"
                    elif target_tool.intent_name == "PIXIV":
                        if not re.search(r"(寫真|照片|畫像|pixiv|本子|給我看|我要看|找.*圖|給.*圖|色圖|搜圖|來張|找張|show me|find.*image|get.*pic)", _sq, re.IGNORECASE):
                            target_tool = get_fallback_tool()
                            _intent_source = "veto_override"

                    log_event("intent_classify", message_id=_msg_id, user=_user, channel_id=_channel_id,
                              raw_input=_sq, intent=target_tool.intent_name, intent_source=_intent_source,
                              veto_fired=(_intent_source == "veto_override"),
                              latency_ms=elapsed())
                    logger.info(f"social_chat: [經典硬路由] 神經判定：將使用者導向模塊 ({target_tool.intent_name})")
                    try:
                        await target_tool.execute(bot, message, _sq, vibe_snippet, full_context, images=images_to_pass)
                    finally:
                        await remove_hourglass_reaction(message, bot.user)

                intent_task = InferenceTask(
                    priority=settings.PRIORITY_TAG_RESPONSE,
                    system_prompt=intent_classifier_system_prompt,
                    user_prompt=sq,
                    callback=_on_dynamic_llm_result,
                    model=settings.CLASSIFIER_MODEL,
                    task_id=f"intent-dynamic-route-{message.id}",
                )
                await brain.enqueue_task(intent_task)