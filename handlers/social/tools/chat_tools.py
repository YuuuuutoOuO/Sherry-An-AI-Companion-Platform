"""
handlers/social/tools/chat_tools.py
=====================================
一般對話工具。（SillyTavern 風格 Prompt 注入版 + CHAT 聯網實體檢索擴增版）
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import Any
import discord
from discord.ext import commands

import settings
from handlers.bot_brain import InferenceTask
from handlers.social.contracts import ToolResult
from handlers.social.dispatch.prompts import (
    VISION_AWARENESS_RULE,
    extract_vision_context,
)
from handlers.social.memory_builder import build_user_memory_block
from handlers.social.persona import build_dynamic_persona
from handlers.social.tool_base import BaseTool
from handlers.social.tools.search_tools import fetch_web_and_entity_context
from handlers.social.utils import simulate_typing_and_send

logger = logging.getLogger(__name__)


def _should_trigger_search(query: str) -> bool:
    """判斷使用者輸入是否需要觸發即時網路/實體檢索。

    ⚠️ 注意：排除一般「介紹」（避免誤觸自我介紹），聚焦真實時事、新聞與行情查詢。
    """
    pattern = (
        r"(0050|美伊|戰爭|新聞|最新|時事|狀況|進展|趨勢|評價|怎麼樣|現況|發展|資訊|幫.*查|搜尋|查詢|怎麼看|是什麼|價格|指數|股票|"
        r"天氣|氣溫|溫度|下雨|降雨|紫外線|颱風|氣象|體感|幾度)"
    )
    if bool(re.search(pattern, query, re.IGNORECASE)):
        return True

    # 台股代碼：4 位數字且非年份（以 2~9 或 0 開頭的 4 位），
    # 並搭配股市相關詞才觸發
    stock_ticker_pat = r"\b[02-9]\d{3}\b"
    stock_context_pat = r"(股|漲|跌|買|賣|價|盤|ETF|指數|行情|台股)"
    if re.search(stock_ticker_pat, query) and re.search(stock_context_pat, query, re.IGNORECASE):
        return True

    return False



class ChatTool(BaseTool):

    @property
    def intent_name(self) -> str:
        return "CHAT"

    @property
    def llm_description(self) -> str:
        return "一般日常對談、問人物關係、時事新聞分析、股票實體詢問與純聊天。"

    def can_handle_by_regex(self, text: str) -> bool:
        is_nsfw = bool(
            re.search(
                r"(色|做愛|摸|胸部?|乳|奶|臀|屁股|下面|裸|脫|舔|插入?|射精|弄壞|發情|想要妳?|肉體|性感|瑟|汁|雞雞|ㄐㄐ|老二|屌|巨根|長短|公分|射出來|中出|無套|內射|高潮|性愛|懷孕|親吻?|嘴|愛愛|小穴)",
                text,
                re.IGNORECASE,
            )
        )
        return is_nsfw

    async def execute(
        self,
        bot: commands.Bot,
        message: discord.Message,
        user_query: str,
        vibe_snippet: str,
        full_context: str,
        **kwargs: object,
    ) -> ToolResult | str | None:
        brain_cog = bot.get_cog("BotBrain")
        if not brain_cog:
            return ToolResult.error("BotBrain 未就緒。")
        brain: Any = brain_cog

        # ── 檢查是否需要即時網路/實體檢索 ───────────────────────────────
        web_context = ""
        ref_link = ""
        if _should_trigger_search(user_query):
            logger.info("🌐 [ChatTool] 觸發 CHAT 聯網實體檢索: '%s'", user_query)
            web_context, ref_link = await fetch_web_and_entity_context(user_query)

        # ── 掃描用文本（結合前文氣氛餘韻 + 當前訊息）───────────────────
        recent_text = full_context[-1500:] + "\n" + user_query

        # ── 第一層：頭部人設 + 低優先度世界書 ───────────────────────────
        head_persona, tail_lore = build_dynamic_persona(
            recent_text, self.intent_name, channel_id=message.channel.id,
            server_id=str(message.guild.id) if message.guild else "",
        )

        # ── 第二層：長期記憶注入 ────────────────────────────────────────
        memory_block = build_user_memory_block(
            bot, message.author.id, message.author.display_name
        )

        # ── 第三層：Vibe（頻道氛圍）─────────────────────────────────────
        vibe_block = (
            "【頻道近期話題氛圍】(⚠️這只是讓妳用來『讀空氣』的背景氣氛！請不要像鸚鵡一樣把這些詞彙硬念出來，除非對方現在的話語剛好有關聯！)\n"
            f"{vibe_snippet}\n\n"
        ) if vibe_snippet else ""

        # ── 第四層：即時網路情報區 ───────────────────────────────────────
        search_block = (
            f"\n{web_context}\n"
            "⚠️【情報整合分析指令】：請參考上述『即時網路與實體檢索情報』，結合事實數據與妳的角色性格，為對方給出深入且充滿特色的解答與分析！\n\n"
        ) if web_context else ""

        # ── 提取多模態圖片與視覺感知情報（若有）─────────────────────────────────────
        raw_images = kwargs.get("images")
        images: list[str] | None = [str(x) for x in raw_images] if isinstance(raw_images, list) else None
        visual_context = str(kwargs.get("visual_context", "") or "")
        clean_query, active_vis = extract_vision_context(user_query, visual_context)

        current_soul = getattr(settings, "CURRENT_SOUL_NAME", "manosaba_sherry_default.md")
        is_sherry = current_soul in ("sherry_default.md", "manosaba_sherry_default.md")

        # ── 第五層：高優先度尾注與回覆規則 ───────────────────────────────
        vision_self_lore = (
            "\n【多模態視覺自我感知提示】：若使用者傳入的圖片中出現淺藍/藍色髮色、丸子頭/甜甜圈髮髻、白鳥羽毛帽、福爾摩斯偵探斗篷/披風等特徵的少女，這就是妳自己（橘雪莉）！請以看到自己自拍或同人圖的視角自然回應。\n"
        ) if (is_sherry and (images or active_vis)) else ""
        author_note = f"\n{tail_lore}\n{vision_self_lore}" if (tail_lore or vision_self_lore) else ""

        if self.can_handle_by_regex(recent_text):
            length_rule = "2. 【性愛與調戲特權解除】：這是一場延續的色情或曖昧話題！請延續這個氣氛，盡情釋放狂氣與慾望！可以多講幾句，大膽露骨且無下限地用言語反擊、迎合或調戲對方，展現妳的瘋狂，絕對不要只講一兩句話敷衍！"
        elif web_context:
            length_rule = "2. 請根據調閱到的情報資訊進行豐富、具體且詳盡的分析解答，講述重點與數據，不要含糊概括。"
        else:
            length_rule = "2. 【豐富生動的對話】：請徹底貫徹妳當前的角色人設特質與語氣風格進行對話！可以講 2～4 句話（不需刻意簡短），內容要有情緒起伏、個人性格表達與自然互動，但不要變成嚴肅說教或論文長篇大論。"

        cot_instruction = ""
        if settings.MICRO_COT_ENABLED:
            cot_instruction = "0. 🧠 【深度思考機制】：在給出最終回覆前，妳「必須」先用 <thought>...</thought> 標籤包覆妳的內心想法、局勢分析與回覆策略。思考完後，再輸出對外說的話。\n"

        vision_rule = f"{VISION_AWARENESS_RULE}\n" if active_vis else ""

        # ── [第一區：靜態系統前綴 (Static Prefix)] ──
        static_reply_rules = (
            "【回覆規則】\n"
            f"{cot_instruction}"
            "1. 🎯 【對話連貫與話題推進】：自然接續剛才的對話脈絡與互動情境。若當前話題正在進行，請順著剛才的情緒與互動情境自然延續推進，嚴禁每輪重複「欸～笨蛋」、「你在說什麼」等初次驚訝的模板化開場白！\n"
            f"{length_rule}\n"
            "3. 不知道的事請直說，不要臆造資訊。\n"
            "4. 除了 <thought>...</thought> 思考標籤外，不要輸出其他多餘的標籤或人名前綴（如『角色名：』）。"
        )
        static_prefix = f"{head_persona}\n\n{static_reply_rules}"

        # ── [第二區：動態情境 (Dynamic Context)] ──
        # 依穩定度（高→低）排序以最大化 KV Cache 共享：
        dynamic_parts = []
        if vibe_block:
            dynamic_parts.append(vibe_block.strip())
        if full_context:
            dynamic_parts.append(full_context.strip())
        if search_block:
            dynamic_parts.append(search_block.strip())
        if vision_rule:
            dynamic_parts.append(vision_rule.strip())
        if memory_block:
            dynamic_parts.append(memory_block)
        if author_note:
            dynamic_parts.append(author_note.strip())

        dynamic_suffix = "\n\n".join(dynamic_parts)
        sys_prompt = f"{static_prefix}\n\n{dynamic_suffix}".strip() if dynamic_suffix else static_prefix

        if is_sherry:
            salts = [
                "（微微歪著頭，語氣帶點調皮）",
                "（開心地眨了眨眼，像好群友一樣親切回應）",
                "（語氣突然變得稍微溫柔跟對方閒聊）",
                "（帶著元氣滿滿的熱情口吻）",
                "（鼓起腮幫子，帶點可愛的傲嬌跟對方開玩笑）",
                "（假裝不在意，但其實聽得很認真、有些小開心）",
                "（露出了可愛且自信的微笑）",
                "（像個急著討摸摸的小朋友一樣得意地回答）",
                "（俏皮地笑了笑，語氣自然生動）",
                "（帶著對朋友撒嬌般的親切語氣）",
            ]
            salt_injection = f" {random.choice(salts)}"
        else:
            salt_injection = ""

        if active_vis:
            action_desc = (
                f"對方出示了上述圖片，並對妳說：「{clean_query}」"
                if clean_query
                else "對方出示了上述圖片，期待妳的反應，沒有額外打字。"
            )
            user_instruct = (
                "【妳眼前的視覺畫面（妳親眼看到的景象）】：\n"
                f"{active_vis}\n\n"
                "【對話當下情境】：\n"
                f"{action_desc}\n\n"
                "⚠️【視覺感官認知準則】：\n"
                "上方「妳眼前的視覺畫面」是妳親眼看到的畫面細節，絕對不是對方打的字！\n"
                "請以妳親眼看到該畫面的第一人稱視角（例如「哇～這張圖...」）自然表達妳的情緒與互動。\n\n"
                f"妳的回覆{salt_injection}："
            )
        else:
            query_text = clean_query if clean_query else "（靜靜看著妳，沒有說話）"
            user_instruct = (
                f"🎯 對方剛剛對妳說：「{query_text}」\n妳的回覆{salt_injection}："
            )

        import time
        start_time = time.time()

        loop = asyncio.get_running_loop()
        reply_future: asyncio.Future[str] = loop.create_future()

        vision_model_setting = str(getattr(settings, "VISION_MODEL", "") or "").strip()
        active_model = vision_model_setting if (images and vision_model_setting) else settings.PRIMARY_MODEL

        async def _on_reply(reply: str) -> None:
            try:
                elapsed_ms = int((time.time() - start_time) * 1000)
                final_reply = reply.strip()
                if ref_link and "http" not in final_reply:
                    final_reply += f"\n\n📂 **參考來源**：\n🔗 <{ref_link}>"
                await simulate_typing_and_send(
                    message,
                    final_reply,
                    intent_name=self.intent_name,
                    prompt=user_query,
                    model=active_model,
                    system_prompt=sys_prompt,
                    user_prompt=user_instruct,
                    latency_ms=elapsed_ms,
                )
                if not reply_future.done():
                    reply_future.set_result(final_reply)
            except Exception as e:
                logger.error("ChatTool: _on_reply 處理失敗: %s", e, exc_info=True)
                if not reply_future.done():
                    reply_future.set_result("")

        task = InferenceTask(
            priority=settings.PRIORITY_TAG_RESPONSE,
            system_prompt=sys_prompt,
            user_prompt=user_instruct,
            callback=_on_reply,
            model=active_model,
            task_id=f"tool-chat-{message.id}",
            images=images,
        )
        await brain.enqueue_task(task)

        try:
            res_text = await asyncio.wait_for(reply_future, timeout=float(settings.OLLAMA_TIMEOUT))
            return ToolResult.success(res_text, is_direct_rendered=True)
        except asyncio.TimeoutError:
            logger.warning("ChatTool: 等待回覆發送逾時 (%ds)", settings.OLLAMA_TIMEOUT)
            return ToolResult.error("等待回覆發送逾時", is_direct_rendered=False)
