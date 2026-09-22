from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

import discord
from discord.ext import commands

import settings
from handlers.social.interject import InterjectState, maybe_interject
from handlers.social.router import route_and_execute_tag
from handlers.social.utils import (
    BOT_MESSAGE_INTENTS,
    append_debug_log,
    fetch_context_script,
    is_admin_user,
)

logger = logging.getLogger(__name__)


class SocialChat(commands.Cog):
    """社群日常對話處理模組，負責訊息計數、熱門話題討論串建立、主動插話與表情符號互動。"""

    def __init__(self, bot: commands.Bot) -> None:
        """初始化 SocialChat 模組。

        Args:
            bot: Discord Bot 實例。
        """
        self.bot = bot
        self._message_counters: dict[int, int] = {}
        self._interject_states: dict[int, InterjectState] = {}
        self._last_vibe_time: dict[int, float] = {}

    def _get_interject_state(self, channel_id: int) -> InterjectState:
        """取得或建立指定頻道的插話評估狀態。"""
        if channel_id not in self._interject_states:
            self._interject_states[channel_id] = InterjectState()
        return self._interject_states[channel_id]

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """監聽文字頻道與討論串訊息，進行計數、氛圍更新與 Tag 意圖路由。"""
        if message.author.bot or not isinstance(message.channel, (discord.TextChannel, discord.Thread)):
            return

        channel_id: int = message.channel.id

        # --- 訊息計數與氛圍更新 ---
        self._message_counters[channel_id] = self._message_counters.get(channel_id, 0) + 1
        count = self._message_counters[channel_id]

        if count % settings.VIBE_UPDATE_MESSAGE_THRESHOLD == 0:
            now = time.time()
            last_time = self._last_vibe_time.get(channel_id, 0)
            self._last_vibe_time[channel_id] = now

            # 若 50 筆訊息在 180 秒內完成，代表極度熱烈，建立 Thread
            is_hot = (last_time > 0 and (now - last_time) < 180)

            vibe_mgr: Any = self.bot.get_cog("VibeManager")
            if vibe_mgr:
                await vibe_mgr.request_vibe_update(message.channel)

            if is_hot and not isinstance(message.channel, discord.Thread):
                try:
                    thread = await message.channel.create_thread(
                        name="🔥 熱烈討論中",
                        message=message,
                        auto_archive_duration=60,
                        reason="High chat activity detected",
                    )
                    await thread.send("大家聊得好熱烈！已經幫你們開了一個專屬討論串，盡情聊吧！")
                except Exception as e:
                    logger.error("建立熱烈討論串失敗: %s", e)

        # --- 主動插話評估 ---
        state = self._get_interject_state(channel_id)
        state.eval_counter += 1
        if state.eval_counter >= settings.INTERJECT_EVAL_THRESHOLD:
            state.eval_counter = 0
            if not state.is_evaluating:
                state.is_evaluating = True
                asyncio.create_task(
                    maybe_interject(self.bot, message.channel, state),
                    name=f"interject-eval-{channel_id}",
                )

        # --- Tag 意圖路由 (Intent Router via SherryCore) ---
        if self.bot.user in message.mentions:
            logger.info("social_chat: bot mentioned by %s in #%s.", message.author.display_name, message.channel.name)
            core: Any = getattr(self.bot, "core", None)
            if core:
                from core.contracts import SherryRequest
                from handlers.social.observability import new_trace_id

                req = SherryRequest(
                    trace_id=new_trace_id("chat"),
                    source_type="chat_message",
                    user_id=message.author.id,
                    user_name=message.author.display_name,
                    guild_id=message.guild.id if message.guild else 0,
                    channel_id=message.channel.id,
                    raw_content=message.clean_content,
                    message_ref=message,
                )
                await core.handle_request(req)
            else:
                await route_and_execute_tag(self.bot, message)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        """處理使用者的 Reaction 快捷操作（🗑️ 刪除、📌 釘選、🔄 重新生成、❓ 除錯回報）。"""
        if not self.bot.user or payload.user_id == self.bot.user.id:
            return

        channel = self.bot.get_channel(payload.channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            return
        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.NotFound:
            return

        if message.author.id != self.bot.user.id:
            return

        emoji_str = str(payload.emoji)
        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        user = guild.get_member(payload.user_id) if guild else self.bot.get_user(payload.user_id)
        if not user and guild:
            try:
                user = await guild.fetch_member(payload.user_id)
            except Exception:
                user = None
        if not user:
            try:
                user = await self.bot.fetch_user(payload.user_id)
            except Exception:
                user = None

        if not user or user.bot:
            return

        is_admin = is_admin_user(user)
        can_manage_msgs = False
        if isinstance(user, discord.Member):
            perms = user.guild_permissions
            can_manage_msgs = perms.administrator or perms.manage_messages

        # 🗑️ 刪除功能
        if emoji_str in ("🗑️", "🗑") or getattr(payload.emoji, "name", "") in ("🗑️", "🗑", "wastebasket"):
            is_reply_to_user = False
            if message.reference:
                ref_msg = message.reference.resolved
                if isinstance(ref_msg, discord.Message):
                    is_reply_to_user = ref_msg.author.id == user.id
                elif message.reference.message_id is not None:
                    try:
                        ref_msg = await channel.fetch_message(message.reference.message_id)
                        is_reply_to_user = ref_msg.author.id == user.id
                    except Exception:
                        pass

            is_mentioning_user = user in message.mentions

            # 檢查 BOT_MESSAGE_INTENTS 中的 user_id 記錄（支援 roast 吐槽與其他主動插話）
            msg_meta = BOT_MESSAGE_INTENTS.get(message.id, {})
            is_target_user = False
            if isinstance(msg_meta, dict):
                recorded_user_id = msg_meta.get("user_id")
                if recorded_user_id and recorded_user_id == user.id:
                    is_target_user = True

            # 雙重兜底：若無引用回覆（如吐槽功能），比對雪莉該訊息前緊鄰的發言者是否為當前點擊的使用者
            if not (is_admin or can_manage_msgs or is_reply_to_user or is_mentioning_user or is_target_user):
                try:
                    async for prev_msg in channel.history(limit=5, before=message):
                        if not prev_msg.author.bot:
                            if prev_msg.author.id == user.id:
                                is_target_user = True
                            break
                except Exception:
                    pass

            if is_admin or can_manage_msgs or is_reply_to_user or is_mentioning_user or is_target_user:
                try:
                    await message.delete()
                except Exception as e:
                    logger.warning("透過 Reaction 刪除訊息失敗: %s", e)

        # 📌 釘選功能
        elif emoji_str == "📌":
            try:
                await message.pin(reason=f"Pinned by {user.display_name}")
            except Exception as e:
                logger.warning("透過 Reaction 釘選訊息失敗: %s", e)

        # 🔄 重新生成功能
        elif emoji_str == "🔄":
            # 如果這是一則回覆，直接拿原本的訊息重跑
            if message.reference and isinstance(message.reference.resolved, discord.Message):
                original_msg = message.reference.resolved
                if original_msg.author.id == user.id or is_admin:
                    try:
                        await message.delete()
                        await route_and_execute_tag(self.bot, original_msg)
                    except Exception as e:
                        logger.error("重新生成回覆失敗: %s", e)

        # ❓ 原有的除錯日誌功能
        elif emoji_str == "❓":
            age_seconds = (datetime.now(timezone.utc) - message.created_at).total_seconds()
            if age_seconds > 300:
                return

            for rec in message.reactions:
                if rec.me and str(rec.emoji) == "✅":
                    return

            msg_meta = BOT_MESSAGE_INTENTS.get(message.id, {})
            intent_name = msg_meta.get("intent", "UNKNOWN") if isinstance(msg_meta, dict) else str(msg_meta)
            thought_text = msg_meta.get("thought", "") if isinstance(msg_meta, dict) else ""
            context_snippet = await fetch_context_script(message.channel, message)

            thought_block = f"【雪莉內部思考 (CoT)】:\n{thought_text}\n" if thought_text else "【雪莉內部思考 (CoT)】: (無)\n"

            log_entry = (
                f"\n{'='*50}\n"
                f"【用戶反映紀錄】 時間: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} (反映者: {user.display_name})\n"
                f"【觸發模塊】: [{intent_name}]\n"
                f"【上下文紀錄】:\n{context_snippet}\n"
                f"{thought_block}"
                f"【雪莉回覆】:\n{message.clean_content}\n"
                f"{'='*50}\n"
            )
            append_debug_log(log_entry)
            try:
                await message.add_reaction("✅")
            except Exception as e:
                logger.debug("添加 ✅ reaction 失敗: %s", e)


async def setup(bot: commands.Bot) -> None:
    """載入 SocialChat 套件架構。"""
    await bot.add_cog(SocialChat(bot))