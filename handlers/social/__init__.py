import asyncio
import logging
import discord
from discord.ext import commands
from typing import Any

import settings
from handlers.social.interject import InterjectState, maybe_interject
from handlers.social.router import route_and_execute_tag

logger = logging.getLogger(__name__)

class SocialChat(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._message_counters: dict[int, int] = {}
        self._interject_states: dict[int, InterjectState] = {}
        self._last_vibe_time: dict[int, float] = {}

    def _get_interject_state(self, channel_id: int) -> InterjectState:
        if channel_id not in self._interject_states:
            self._interject_states[channel_id] = InterjectState()
        return self._interject_states[channel_id]

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # ✅ [修復] 同時支援 TextChannel 與 Thread，讓雪莉在討論串也能正常响應
        if message.author.bot or not isinstance(message.channel, (discord.TextChannel, discord.Thread)):
            return

        channel_id: int = message.channel.id

        # --- 訊息計數與氛圍更新 ---
        self._message_counters[channel_id] = self._message_counters.get(channel_id, 0) + 1
        count = self._message_counters[channel_id]

        if count % settings.VIBE_UPDATE_MESSAGE_THRESHOLD == 0:
            import time
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
                        reason="High chat activity detected"
                    )
                    await thread.send("大家聊得好熱烈！名偵探幫你們開了一個專屬討論串，盡情聊吧！")
                except Exception as e:
                    logger.error(f"Failed to create thread: {e}")

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

        # --- Tag 意圖路由 (Intent Router) ---
        if self.bot.user in message.mentions:
            logger.info("social_chat: bot mentioned by %s in #%s.", message.author.display_name, message.channel.name)
            await route_and_execute_tag(self.bot, message)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if not self.bot.user or payload.user_id == self.bot.user.id:
            return

        channel = self.bot.get_channel(payload.channel_id)
        if not channel or not hasattr(channel, "fetch_message"): return
        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.NotFound:
            return

        if message.author.id != self.bot.user.id:
            return

        emoji_str = str(payload.emoji)
        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        user = guild.get_member(payload.user_id) if guild else self.bot.get_user(payload.user_id)
        if not user or user.bot: return

        # 🗑️ 刪除功能
        if emoji_str == "🗑️":
            is_admin = False
            if hasattr(user, "roles") and settings.ADMIN_ROLE_IDS:
                is_admin = any(r.id in settings.ADMIN_ROLE_IDS for r in getattr(user, "roles", []))
            if user.id in settings.ADMIN_USER_IDS:
                is_admin = True
            
            is_reply_to_user = False
            if message.reference and isinstance(message.reference.resolved, discord.Message):
                is_reply_to_user = message.reference.resolved.author.id == user.id
            
            is_mentioning_user = user in message.mentions
            
            if is_admin or is_reply_to_user or is_mentioning_user:
                try:
                    await message.delete()
                except Exception as e:
                    logger.warning(f"Failed to delete message via reaction: {e}")
                    
        # 📌 釘選功能
        elif emoji_str == "📌":
            try:
                await message.pin(reason=f"Pinned by {user.display_name}")
            except Exception as e:
                logger.warning(f"Failed to pin message via reaction: {e}")
                
        # 🔄 重新生成功能
        elif emoji_str == "🔄":
            from handlers.social.utils import BOT_MESSAGE_INTENTS
            msg_meta = BOT_MESSAGE_INTENTS.get(message.id, {})
            original_prompt = msg_meta.get("prompt")
            
            is_admin = False
            if hasattr(user, "roles") and settings.ADMIN_ROLE_IDS:
                is_admin = any(r.id in settings.ADMIN_ROLE_IDS for r in getattr(user, "roles", []))
            if user.id in settings.ADMIN_USER_IDS:
                is_admin = True
                
            # 如果這是一則回覆，直接拿原本的訊息重跑
            if message.reference and isinstance(message.reference.resolved, discord.Message):
                original_msg = message.reference.resolved
                if original_msg.author.id == user.id or is_admin:
                    try:
                        await message.delete()
                        await route_and_execute_tag(self.bot, original_msg)
                    except Exception as e:
                        logger.error(f"Regenerate failed: {e}")
            elif original_prompt:
                # 如果找不到原訊息但有 cache，可做一些備用處理，但這裡簡單略過
                pass

        # ❓ 原有的除錯日誌功能
        elif emoji_str == "❓":
            from datetime import datetime, timezone
            age_seconds = (datetime.now(timezone.utc) - message.created_at).total_seconds()
            if age_seconds > 300: return
            
            for rec in message.reactions:
                if rec.me and str(rec.emoji) == "✅": return
                
            from handlers.social.utils import BOT_MESSAGE_INTENTS, append_debug_log, fetch_context_script
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
            except Exception: pass

async def setup(bot: commands.Bot) -> None:
    """載入全新的 SocialChat 套件架構"""
    await bot.add_cog(SocialChat(bot))