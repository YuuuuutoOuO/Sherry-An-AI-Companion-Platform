"""
handlers.roast.cog
==================
Discord Cog for the Roast (Tsukkomi) subsystem.

Monitors incoming channel messages for configured keywords, performs
probability rolls, and dispatches dynamic persona roasting messages
directly to the channel without quoting/replying.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import discord
from discord.ext import commands

import settings
from handlers.bot_brain import InferenceTask
from handlers.roast.manager import roast_manager
from handlers.roast.models import RoastRule
from handlers.roast.utils import clean_roast_reply, extract_fallback_roast
from handlers.social.persona import build_dynamic_persona
from handlers.social.utils import SAFETY_FOOTER, append_thinking_log, simulate_typing_and_send_to_channel

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class RoastCog(commands.Cog, name="RoastCog"):
    """Handles automatic keyword-triggered roasting in Discord channels."""

    def __init__(self, bot: commands.Bot) -> None:
        """Initialize the RoastCog.

        Args:
            bot: Active Discord Bot instance.
        """
        self.bot: commands.Bot = bot
        self._channel_cooldowns: dict[int, float] = {}

    def _is_cooling_down(self, channel_id: int) -> bool:
        """Check whether the channel is currently in roast cooldown.

        Args:
            channel_id: Discord channel ID.

        Returns:
            True if cooling down, False otherwise.
        """
        cooldown_until = self._channel_cooldowns.get(channel_id, 0.0)
        return time.monotonic() < cooldown_until

    def _set_cooldown(self, channel_id: int, duration_seconds: int) -> None:
        """Set the cooldown expiration for a channel.

        Args:
            channel_id: Discord channel ID.
            duration_seconds: Cooldown duration in seconds.
        """
        self._channel_cooldowns[channel_id] = time.monotonic() + max(1, duration_seconds)

    def _is_targeting_bot(self, message: discord.Message) -> bool:
        """Check if the message is explicitly mentioning, replying to, or targeting the bot.

        Args:
            message: Incoming Discord message.

        Returns:
            True if message targets the bot and should be handled by normal chat instead of roast.
        """
        if not self.bot.user:
            return False

        # 1. Discord @mention
        if self.bot.user in message.mentions:
            return True

        # 2. 文字形式 @雪莉 / @Sherry / raw mention tag
        content = (message.content or "").lower()
        bot_id = str(self.bot.user.id)
        if (
            "@雪莉" in message.content
            or "@sherry" in content
            or f"<@{bot_id}>" in message.content
            or f"<@!{bot_id}>" in message.content
        ):
            return True

        # 3. 回覆引用雪莉的訊息 (Reply to Bot)
        if message.reference:
            resolved = message.reference.resolved
            if isinstance(resolved, discord.Message) and resolved.author and resolved.author.id == self.bot.user.id:
                return True

        # 4. 身分組 @mention
        if message.guild and hasattr(message.guild, "me") and message.guild.me:
            bot_roles = getattr(message.guild.me, "roles", [])
            if any(role in message.role_mentions for role in bot_roles if not getattr(role, "is_default", lambda: False)()):
                return True

        return False

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Inspect chat messages for roast trigger keywords.

        Args:
            message: Incoming Discord message.
        """
        # 1. 基礎防護：忽略 Bot 自身訊息、非文字頻道/討論串
        if message.author.bot or not isinstance(message.channel, (discord.TextChannel, discord.Thread)):
            return

        # 2. 忽略任何 @雪莉 / 標記 / 回覆雪莉的訊息（由 SocialChat 正常意圖處理，不觸發吐槽）
        if self._is_targeting_bot(message):
            return

        # 3. 檢查吐槽總開關
        if not roast_manager.is_enabled():
            return

        # 4. 檢查頻道冷卻
        channel_id = message.channel.id
        if self._is_cooling_down(channel_id):
            return

        # 4. 關鍵字規則匹配
        content = message.content or ""
        matching_rules = roast_manager.find_matching_rules(content)
        if not matching_rules:
            return

        # 5. 進行獨立機率擲骰，找出第一個命中觸發的規則
        selected_rule: RoastRule | None = None
        for rule in matching_rules:
            if roast_manager.roll_probability(rule):
                selected_rule = rule
                break

        if not selected_rule:
            return

        # 6. 成功觸發：設定冷卻時間並啟動吐槽任務
        config = roast_manager.get_config()
        self._set_cooldown(channel_id, config.global_cooldown_seconds)

        logger.info(
            "Roast triggered in #%s by user '%s' on keyword '%s' (prob=%d%%)",
            message.channel.name,
            message.author.display_name,
            selected_rule.keyword,
            selected_rule.probability,
        )

        asyncio.create_task(
            self._execute_roast(message, selected_rule),
            name=f"roast-{channel_id}-{int(time.monotonic())}",
        )

    async def _execute_roast(self, message: discord.Message, rule: RoastRule) -> None:
        """Generate and send the roast message directly in the channel.

        Args:
            message: The message that triggered the roast.
            rule: The triggered RoastRule.
        """
        channel = message.channel
        brain: Any = self.bot.get_cog("BotBrain")

        # 取得動態雪莉人設
        head_persona, _tail = build_dynamic_persona(
            message.content,
            "CHAT",
            channel_id=channel.id,
        )

        # 隨機語氣修飾，增加吐槽豐富度
        roast_flavor = random.choice([
            "請用銳利又帶點傲嬌的語氣，一針見血地吐槽！",
            "請用像在看小笨蛋一樣的無奈語氣吐槽！",
            "請用名偵探自信滿滿且得意的語氣吐槽！",
            "請用可愛又毒舌的短句直擊對方的弱點！",
        ])

        system_prompt = (
            f"{head_persona}\n\n"
            "🚨 【即時吐槽任務 (Tsukkomi Directive)】\n"
            f"群友「{message.author.display_name}」剛剛發了這句話：「{message.content}」。\n"
            f"此發言觸發了吐槽關鍵字「{rule.keyword}」！\n"
            f"【核心吐槽指引】：「{rule.reply_guide}」\n\n"
            "🎯 【吐槽生成四大核心法則】（極為重要！）：\n"
            "1. 🔗 深度結合對方發言情境：妳必須敏銳抓住對方句子裡的具體人事物、動作或場景（例如對方提到的特定人名、食物、事情或動作），將「核心吐槽指引」的語氣與觀點自然融合進去進行精準反駁！絕對禁止忽視對方說的內容只講生硬的罐頭台詞。\n"
            f"2. 🍊 傲嬌毒舌名偵探人設：{roast_flavor} 一針見血地戳破對方的幻想或錯誤！\n"
            "3. ⚡ 俐落生動短句：長度約 15 ~ 55 字，必須是妳直接說出口的生動對白，絕對禁止在最外層包裹「」或 \"\" 引號，禁止任何機器人客套話！\n"
            "4. 🚫 嚴禁複製指引標籤：【核心吐槽指引】是提供給妳的思維邏輯與參考指引，請將其內化為妳的傲嬌口吻。若指引中包含方括號條件（例如 [若提及雪莉:...]）或提示詞，請依據群友發言情境判定符合的條件並轉化為自然的日常說話對白，**絕對禁止**把方括號標籤或提示詞原樣輸出！\n"
        ) + SAFETY_FOOTER

        reply_text: str = ""

        if brain:
            reply_future = self.bot.loop.create_future()

            async def _on_reply(res: str) -> None:
                if not reply_future.done():
                    reply_future.set_result(res)

            task = InferenceTask(
                priority=settings.PRIORITY_TAG_RESPONSE,
                system_prompt=system_prompt,
                user_prompt=f"【群友「{message.author.display_name}」的發言內容】: {message.content}",
                callback=_on_reply,
                model=settings.PRIMARY_MODEL,
                task_id=f"roast-{channel.id}-{int(time.monotonic())}",
            )
            await brain.enqueue_task(task)

            try:
                raw_reply = await asyncio.wait_for(
                    reply_future,
                    timeout=getattr(settings, "ROAST_REPLY_TIMEOUT", 30.0),
                )
                cleaned = clean_roast_reply(raw_reply)
                if cleaned:
                    reply_text = cleaned
            except (asyncio.TimeoutError, Exception) as exc:
                setattr(task, "is_cancelled", True)
                logger.warning("Roast LLM inference timed out or failed: %s; using safe guide fallback", exc)

        # 若 LLM 未回傳或失敗，採用安全提取的兜底台詞（絕不暴露 Prompt / 方括號指令）
        if not reply_text:
            reply_text = extract_fallback_roast(rule.reply_guide, user_content=message.content)

        # 記錄除錯與思考日誌
        append_thinking_log({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "channel_id": channel.id,
            "channel_name": getattr(channel, "name", "unknown"),
            "trigger": "roast_keyword",
            "keyword": rule.keyword,
            "rule_id": rule.id,
            "probability": rule.probability,
            "user": message.author.display_name,
            "user_content": message.content,
            "roast_reply": reply_text,
        })

        # 🚀 依使用者指定規範：不使用 Discord 訊息引用回覆 (reference=None)，直接留言在頻道下方！
        try:
            await simulate_typing_and_send_to_channel(
                channel,
                reply_text,
                intent_name="ROAST",
                target_user_id=message.author.id,
            )
        except Exception as exc:
            logger.error("Failed to send roast message to channel: %s", exc)


async def setup(bot: commands.Bot) -> None:
    """Register the RoastCog with the Discord bot.

    Args:
        bot: Bot instance.
    """
    await bot.add_cog(RoastCog(bot))
    logger.info("Loaded core extension: handlers.roast")
