import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import discord
from discord.ext import commands

import settings
from handlers.bot_brain.models import InferenceTask
from handlers.social.utils import fetch_recent_script, append_thinking_log, simulate_typing_and_send_to_channel, SAFETY_FOOTER

# ✨ 掛載雪莉的全自動換裝魔法庫與動態人格套件引擎
from handlers.social.persona import build_dynamic_persona

logger = logging.getLogger(__name__)

class InterjectState:
    __slots__ = ("cooldown_until", "consecutive_count", "eval_counter", "is_evaluating")
    def __init__(self) -> None:
        self.cooldown_until: float = 0.0
        self.consecutive_count: int = 0
        self.eval_counter: int = 0
        self.is_evaluating: bool = False
    def is_cooling_down(self) -> bool:
        return time.monotonic() < self.cooldown_until
    def reset_cooldown(self) -> None:
        self.cooldown_until = time.monotonic() + settings.INTERJECT_COOLDOWN_SECONDS
        self.consecutive_count = 0
    def can_interject(self) -> bool:
        if self.is_cooling_down():
            return self.consecutive_count < settings.INTERJECT_MAX_CONSECUTIVE
        self.consecutive_count = 0
        return True

async def maybe_interject(bot: commands.Bot, channel: discord.TextChannel | discord.Thread | Any, state: InterjectState) -> None:
    try:
        import asyncio
        if not state.can_interject():
            return

        brain_cog = bot.get_cog("BotBrain")
        if not brain_cog:
            return
        brain: Any = brain_cog

        conversation_snapshot = await fetch_recent_script(channel, settings.INTERJECT_EVAL_THRESHOLD)
        if not conversation_snapshot:
            return

        vibe_mgr: Any = bot.get_cog("VibeManager")
        vibe_snippet = vibe_mgr.get_top_topics_snippet(channel.id) if vibe_mgr else ""

        # ✨✨ 【偷聽竊取神威啟動！】透過擷取前十五句話自動動態組合並偷偷幫她解開人設限制防禦！！
        # 插話評分只需要 head_persona（世界觀背景即可），不需要 tail_lore。
        head_persona, _tail = build_dynamic_persona(conversation_snapshot, "CHAT", channel_id=channel.id)
        dynamic_persona = head_persona

        score_system_prompt = (
            f"{dynamic_persona}\n\n"
            "【群內旁觀與活躍參與任務】\n"
            "用你這個「缺乏常識的名偵探」這特異且八卦的視角，評估這堆未指定跟妳談話的人對話內容是否吸引妳！？"
            "判斷妳現在有沒有超想無聊開口打岔反駁他們或是吐槽附和這一切。\n"
            "請直接返回如下的 JSON 格式即可：\n"
            '{"score": <1到10的整數>, "reasoning": "<給出一句話讓你自己知道妳自己為何對這些廢話評這點數！>"}\n'
            "超好笑或妳超感興趣為10分，妳聽不懂不好玩不想加入為1分\n"
            "注意禁止吐出任何 JSON 外包裹的其他反思文章語調內容！"
        )

        score_future = bot.loop.create_future()
        async def _on_score_result(raw_result: str) -> None:
            if not score_future.done():
                score_future.set_result(raw_result)

        score_task = InferenceTask(
            priority=settings.PRIORITY_INTERJECT,
            system_prompt=score_system_prompt,
            user_prompt=conversation_snapshot,
            callback=_on_score_result,
            model=settings.CLASSIFIER_MODEL,
            task_id=f"interject-score-{channel.id}-{int(time.monotonic())}",
        )
        await brain.enqueue_task(score_task)

        try:
            raw_result = await asyncio.wait_for(score_future, timeout=settings.INTERJECT_SCORE_TIMEOUT)
        except asyncio.TimeoutError:
            setattr(score_task, "is_cancelled", True)
            return

        score: int = 0
        reasoning: str = raw_result

        try:
            cleaned = raw_result.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            json_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if json_match:
                cleaned = json_match.group(0)
            parsed = json.loads(cleaned)
            score = int(parsed.get("score", 0))
            reasoning = str(parsed.get("reasoning", raw_result))
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning("social_chat: 無法解析旁觀決策分出 JSON: %s | raw: %s", e, raw_result[:100])
            num_match = re.search(r"\b([1-9]|10)\b", raw_result)
            if num_match:
                score = int(num_match.group(1))

        will_act = score >= settings.INTERJECT_SCORE_THRESHOLD and state.can_interject()

        append_thinking_log({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "channel_id": channel.id,
            "channel_name": channel.name,
            "trigger": "interject_eval",
            "score": score,
            "reasoning": reasoning,
            "acted": will_act,
            "reply": None,
        })

        if not will_act:
            return

        parts = [dynamic_persona]
        if vibe_snippet:
            parts.append(f"\n🔥 【目前暗藏觀察推衍氣氛脈絡（勿直接跟其他人唸出來！）】:\n{vibe_snippet}")
        import uuid
        import random
        salt_hex = uuid.uuid4().hex[:8]
        salt_prompts = [
            "這次請用『閃閃發光的期待語氣』切入，好像發現了什麼超棒的線索！",
            "這回請用有點『天然呆』的疑問句，可愛地誤解他們對話的意思！",
            "這次請用『得意的自誇』開頭，像個急著討摸摸的小女孩一樣！",
            "這回請用『撒嬌』的口吻，抱怨大家講的話題太深奧了！",
            "這次請帶著『滿滿的活力』，用元氣滿滿的方式大聲贊同或打岔！",
            "這回請用『吃醋或鬧彆扭』的可愛語氣，抱怨大家都沒有注意到本名偵探！",
            "這次請用『故作玄虛的可愛偵探口吻』，把一件普通的小事說成天大的秘密！",
            "這回請用『假裝很懂但其實完全不懂』的語氣，強行加入他們的話題！",
            "這次請帶著『被冷落的小可憐』的語氣，弱弱地舉手發問！",
            "這回請用『發現新大陸』般興奮的口吻，抓著對話裡的一個小細節追問！"
        ]
        chosen_salt = random.choice(salt_prompts)
        
        cot_instruction = ""
        if getattr(settings, "MICRO_COT_ENABLED", True):
            cot_instruction = "\n0. 🧠 【深度思考機制】：在給出最終插話前，妳「必須」先用 <thought>...</thought> 標籤包覆妳的內心想法與插話策略。思考完後，再輸出對外說的話。\n"

        parts.append(
            "\n🚨 【妳準備大嗓門破門強制中斷這對話場景了！！】：\n"
            "觀察並接收上方這一長串群內的交談紀錄，對這個有趣的情況！不按牌理或極盡傲嬌且狂熱的強接這個段落去介入發話參與進去。"
            "只要你直接吐槽嘴砲或搭應上一兩行，不要演旁觀內心小說情境也不需介紹自己身分直接如真人講屁話進入群體的社交頻率中！\n"
            f"{cot_instruction}"
            f"🎲 [System Random Directive: {salt_hex}] {chosen_salt}\n"
            "⚠️ 【防止跳針極度警告】：絕對、絕對不要重複稍早用過的詞彙、句型或開場白！每次發話都要像是完全不同心情的妳！\n"
            "⚠️ 【格式嚴格警告】：妳輸出的文字必須「直接是妳說出口的話」，絕對禁止在整句話的最外圍加上「」或 \"\" 這些引號！這會讓妳看起來像個機器人！"
        )
        interject_system = "\n".join(parts) + SAFETY_FOOTER

        start_time = time.time()
        reply_future = bot.loop.create_future()
        async def _on_interject_reply(reply: str) -> None:
            if not reply_future.done():
                reply_future.set_result(reply)

        interject_task = InferenceTask(
            priority=settings.PRIORITY_INTERJECT,
            system_prompt=interject_system,
            user_prompt=conversation_snapshot,
            callback=_on_interject_reply,
            model=settings.PRIMARY_MODEL,
            task_id=f"interject-{channel.id}-{int(time.monotonic())}",
        )
        await brain.enqueue_task(interject_task)

        try:
            reply = await asyncio.wait_for(reply_future, timeout=settings.INTERJECT_REPLY_TIMEOUT)
        except asyncio.TimeoutError:
            return

        if not reply or not reply.strip():
            return

        elapsed_ms = int((time.time() - start_time) * 1000)

        append_thinking_log({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "channel_id": channel.id,
            "channel_name": channel.name,
            "trigger": "interject_reply",
            "score": score,
            "reasoning": reasoning,
            "acted": True,
            "reply": reply,
        })

        if not state.is_cooling_down():
            state.reset_cooldown()
        state.consecutive_count += 1
        await simulate_typing_and_send_to_channel(
            channel,
            reply,
            intent_name="INTERJECT",
            prompt=conversation_snapshot[-300:] if len(conversation_snapshot) > 300 else conversation_snapshot,
            model=settings.PRIMARY_MODEL,
            system_prompt=interject_system,
            user_prompt=conversation_snapshot,
            latency_ms=elapsed_ms,
        )

    finally:
        state.is_evaluating = False