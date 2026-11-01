"""``/care`` 使用者指令與管理報表（R20）。

使用者可自行 opt-in／opt-out／調整頻率；報表僅限伺服器管理員或
``settings.ADMIN_USER_IDS`` 檢視。所有資料庫操作都丟到 worker thread，
避免阻塞事件迴圈（R12/R13 規範）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime as dt, timezone, timedelta
from typing import Any

import discord
import settings
from discord import app_commands
from discord.ext import commands, tasks

from .engine import CareEngine
from .repository import is_within_active_hours
from .surfer import CareSurfer, is_channel_unanswered

logger = logging.getLogger(__name__)

_TZ_TW = timezone(timedelta(hours=8))

_FREQUENCY_LABELS = {
    "daily": "每日",
    "weekly": "每週",
    "off": "關閉",
}


class CareCog(commands.Cog, name="CareCog"):
    """主動關懷的使用者設定與管理報表。"""

    care = app_commands.Group(name="care", description="💗 主動關懷設定與回饋報表")

    def __init__(self, bot: commands.Bot, engine: CareEngine) -> None:
        self.bot = bot
        self.engine = engine
        from .surfer import get_care_surfer
        self.surfer = getattr(self.engine, "surfer", None) or get_care_surfer()
        self.surfer.repository = self.engine.repository
        from .followup_scanner import FollowupScanner
        self.followup_scanner = FollowupScanner(self.surfer)
        self._last_bot_speak_time: dict[str, float] = {}
        self._lurk_counter: dict[str, int] = {}
        self._is_handling_chat: dict[str, bool] = {}
        self._social_director_instance: Any = None
        self._sandbox_runner_instance: Any = None

        from .cyber import CyberEngine, set_cyber_engine

        self.cyber = CyberEngine(bot, engine)
        set_cyber_engine(self.cyber)
        # v2 主動開話題時，沿用既有的 CareSurfer（RSS／興趣檢索）去網路上找素材。
        self.cyber.surfer = self.surfer

    # ------------------------------------------------------------------
    # Social Core（決策帳本／沙盒群像）
    # ------------------------------------------------------------------
    @property
    def social_director(self) -> Any:
        """延遲建立 SocialDirector，避免模組載入期循環匯入。"""
        if self._social_director_instance is None:
            from handlers.social.autonomy import SocialDirector

            self._social_director_instance = SocialDirector(self.engine.repository)
        return self._social_director_instance

    @property
    def sandbox_runner(self) -> Any:
        """延遲建立沙盒群像 runner（正式群永遠不會用到）。"""
        if self._sandbox_runner_instance is None:
            from handlers.social.autonomy import SandboxGroupRunner

            self._sandbox_runner_instance = SandboxGroupRunner(
                self.bot,
                self.engine.repository,
                director=self.social_director,
                broadcaster=getattr(self.surfer, "broadcaster", None),
            )
        return self._sandbox_runner_instance

    @staticmethod
    def _gateway_enabled() -> bool:
        """WebUI v2 的 TurnGateway 開關；.env 只作為舊版回退。"""
        try:
            from .cyber.ui_config import get_cyber_ui_config

            return bool(get_cyber_ui_config().gateway_enabled)
        except Exception:
            return bool(getattr(settings, "INTERACTION_GATEWAY_ENABLED", False))

    @staticmethod
    def _guild_id(interaction: discord.Interaction) -> int:
        return int(interaction.guild_id or 0)

    @staticmethod
    def _can_view_report(interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            admin_ids = {int(uid) for uid in getattr(settings, "ADMIN_USER_IDS", [])}
            return int(interaction.user.id) in admin_ids
        permissions = getattr(interaction.user, "guild_permissions", None)
        return bool(permissions and permissions.manage_guild)

    async def _status_text(self, user_id: int, guild_id: int) -> str:
        status = await asyncio.to_thread(
            self.engine.preference_status, user_id, guild_id
        )
        lines = [
            f"狀態：{'✅ 已開啟' if status['opt_in'] else '⛔ 已關閉'}",
            f"頻率：{_FREQUENCY_LABELS.get(status['frequency'], status['frequency'])}",
            f"本週可用額度：{status['effective_weekly_limit']} 條",
        ]
        if not status["guild_enabled"]:
            lines.append(
                f"⚠️ 此伺服器尚未開啟主動關懷灰度（{status['rollout_percent']}%）。"
            )
        if status["limit_reason"]:
            lines.append(f"⚠️ {status['limit_reason']}")
        return "\n".join(lines)

    @care.command(name="opt-in", description="開啟雪莉的主動關懷（可隨時 opt-out）")
    async def care_opt_in(self, interaction: discord.Interaction) -> None:
        guild_id = self._guild_id(interaction)
        user_id = int(interaction.user.id)
        preference = await asyncio.to_thread(
            self.engine.repository.get_preference, user_id, guild_id
        )
        frequency = (
            preference.frequency
            if preference.frequency in ("daily", "weekly")
            else "weekly"
        )
        await asyncio.to_thread(
            self.engine.repository.set_opt_in,
            user_id,
            guild_id,
            opt_in=True,
            frequency=frequency,
        )
        text = await self._status_text(user_id, guild_id)
        await interaction.response.send_message(
            f"💗 **主動關懷已開啟**\n{text}", ephemeral=True
        )

    @care.command(name="opt-out", description="立即關閉雪莉的主動關懷")
    async def care_opt_out(self, interaction: discord.Interaction) -> None:
        guild_id = self._guild_id(interaction)
        user_id = int(interaction.user.id)
        await asyncio.to_thread(
            self.engine.repository.set_opt_in,
            user_id,
            guild_id,
            opt_in=False,
        )
        await interaction.response.send_message(
            "⛔ **主動關懷已關閉**。雪莉不會再對你發送主動關懷訊息。",
            ephemeral=True,
        )

    @care.command(name="frequency", description="設定主動關懷頻率（非關閉即同時 opt-in）")
    @app_commands.choices(
        frequency=[
            app_commands.Choice(name="每日", value="daily"),
            app_commands.Choice(name="每週（預設）", value="weekly"),
            app_commands.Choice(name="關閉", value="off"),
        ]
    )
    async def care_frequency(
        self,
        interaction: discord.Interaction,
        frequency: app_commands.Choice[str],
    ) -> None:
        guild_id = self._guild_id(interaction)
        user_id = int(interaction.user.id)
        selected = frequency.value
        if selected == "off":
            await asyncio.to_thread(
                self.engine.repository.set_opt_in,
                user_id,
                guild_id,
                opt_in=False,
                frequency="off",
            )
            await interaction.response.send_message(
                "⛔ 主動關懷已關閉。", ephemeral=True
            )
            return

        await asyncio.to_thread(
            self.engine.repository.set_opt_in,
            user_id,
            guild_id,
            opt_in=True,
            frequency=selected,
        )
        text = await self._status_text(user_id, guild_id)
        await interaction.response.send_message(
            f"💗 **主動關懷頻率已設為「{_FREQUENCY_LABELS[selected]}」**\n{text}",
            ephemeral=True,
        )

    @care.command(name="status", description="檢視你的主動關懷設定與本週額度")
    async def care_status(self, interaction: discord.Interaction) -> None:
        text = await self._status_text(
            int(interaction.user.id), self._guild_id(interaction)
        )
        await interaction.response.send_message(
            f"💗 **你的主動關懷設定**\n{text}", ephemeral=True
        )

    @care.command(name="report", description="檢視主動關懷回覆率／沉默率報表（管理員）")
    @app_commands.describe(days="統計窗口天數（預設 30 天）")
    async def care_report(
        self, interaction: discord.Interaction, days: app_commands.Range[int, 1, 365] = 30
    ) -> None:
        if not self._can_view_report(interaction):
            await interaction.response.send_message(
                "❌ 此指令需要「管理伺服器」權限。", ephemeral=True
            )
            return

        guild_id = self._guild_id(interaction)
        report = await asyncio.to_thread(
            self.engine.feedback_report,
            days=float(days),
            guild_id=(guild_id if guild_id else None),
        )
        overall = report["overall"]
        resolved = overall["resolved"]
        embed = discord.Embed(
            title="💗 主動關懷回饋報表",
            description=f"統計窗口：最近 {days} 天",
            color=0xF472B6,
        )
        embed.add_field(
            name="核准 / 拒絕",
            value=f"{overall['approved']} / {overall['rejected']}",
            inline=True,
        )
        embed.add_field(
            name="回覆率",
            value=f"{overall['response_rate']:.1%} ({overall['responded']}/{resolved})",
            inline=True,
        )
        embed.add_field(
            name="沉默率",
            value=f"{overall['silence_rate']:.1%} ({overall['silent']}/{resolved})",
            inline=True,
        )
        embed.add_field(
            name="待回饋 / 待回訪話題",
            value=f"{overall['pending']} / {report['pending_topics']}",
            inline=True,
        )
        embed.add_field(
            name="已 opt-in 使用者",
            value=str(report["opt_in_users"]),
            inline=True,
        )
        limits = report["limits"]
        embed.add_field(
            name="額度（每週/每日）",
            value=(
                f"{limits['weekly_per_user']} / {limits['daily_global']}"
                f"（自適應：{'開' if limits['adaptive_enabled'] else '關'}）"
            ),
            inline=True,
        )
        if resolved == 0:
            embed.set_footer(text="尚無已結案樣本；沉默率需先由 settle 或回應記錄產生。")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # 網活仔定時衝浪背景任務與 Port 8765 實況服務啟動
    # ------------------------------------------------------------------
    async def cog_load(self) -> None:
        # 心智狀態持久化：把上次執行時的無聊／好奇接回來，重啟不再歸零。
        try:
            from .surfer import enable_mind_persistence, load_mind_state

            enable_mind_persistence()
            if load_mind_state():
                logger.info("[CareCog] 已載回上次的心智狀態（重啟不歸零）。")
        except Exception as exc:
            logger.debug("[CareCog] 載回心智狀態失敗: %s", exc)

        try:
            from .surfer_server import launch_surfer_server_task, set_bot_instance, set_surfer
            from .web import set_bot_instance as set_web_bot_instance
            set_bot_instance(self.bot)
            set_web_bot_instance(self.bot)
            set_surfer(self.surfer)
            launch_surfer_server_task()
            logger.info("[CareCog] Port 8765 雪莉上網衝浪實況伺服器已就緒。")
        except Exception as exc:
            logger.warning("[CareCog] 啟動 Port 8765 衝浪伺服器失敗: %s", exc)

        if not self._surfer_loop.is_running():
            self._surfer_loop.start()
            logger.info("[CareCog] 網活仔定時衝浪巡航任務已上線（每分鐘檢查一次）。")

        try:
            await self.cyber.start()
        except Exception as exc:
            logger.warning("[CareCog] CyberEngine 啟動失敗: %s", exc)

    async def cog_unload(self) -> None:
        try:
            await self.cyber.stop()
        except Exception as exc:
            logger.debug("[CareCog] CyberEngine 關閉例外: %s", exc)
        try:
            from .cyber import set_cyber_engine

            set_cyber_engine(None)
        except Exception:
            pass
        if self._surfer_loop.is_running():
            self._surfer_loop.cancel()
        if hasattr(self, "followup_scanner"):
            self.followup_scanner.cancel_all()
        try:
            from .surfer_server import stop_surfer_server_task
            stop_surfer_server_task()
        except Exception as exc:
            logger.debug("[CareCog] 關閉 Port 8765 伺服器例外: %s", exc)

    @tasks.loop(minutes=1)
    async def _surfer_loop(self) -> None:
        """定時巡航背景迴圈：定時檢查間隔並觸發衝浪發文與每分鐘心智脈動。"""
        if self.cyber.mode == "live":
            # 外發與心智累積由 CyberEngine._proactive_loop 接管；上網找話題則由
            # 它呼叫 CareSurfer.fetch_candidates() 後帶進同一個 Cyber 回合，
            # 避免第二條發送路徑造成重複發言。
            return
        try:
            # 1. 推進每分鐘心智脈動 (自然遞減與需求自主觸發)
            await self.surfer.tick_mind_pulse(self.bot)
        except Exception as exc:
            logger.debug("[CareCog] 心智脈動異常: %s", exc)

        try:
            config = await asyncio.to_thread(self.engine.repository.get_surfer_config)
            if not config.enabled:
                return
            channel_id = int(config.sandbox_channel_id) if config.sandbox_channel_id.isdigit() else 0
            if channel_id <= 0:
                return

            now = time.time()
            interval_sec = max(5, config.interval_minutes) * 60
            if now - config.last_run_at >= interval_sec:
                now_dt = dt.now(_TZ_TW)
                if not is_within_active_hours(now_dt.hour, config.active_hour_start, config.active_hour_end):
                    return
                # 定時巡航也必須先經過 SocialDirector 的共同閘門（冷卻／模式／帳本）。
                from handlers.social.autonomy import SocialEvent, TriggerType

                event = SocialEvent(
                    kind="surf",
                    channel_id=str(channel_id),
                    trigger_type=TriggerType.SURF,
                    is_human=False,
                    created_at=now,
                )
                decision = self.social_director.decide(
                    event,
                    config,
                    room=self.engine.repository.get_room(channel_id),
                    persona_key="sherry",
                )
                if not decision.allowed:
                    # 閘門擋下（額度／冷卻／時段）時，把下次巡航往後推一個間隔，
                    # 否則 last_run_at 不動，這一分鐘迴圈會每 60 秒重試並洗一筆 SILENT。
                    await asyncio.to_thread(self.engine.repository.set_surfer_last_run)
                    logger.info(
                        "[CareCog] 定時巡航未通過發話閘門（%s），延後 %d 分鐘再試。",
                        ", ".join(decision.gate.reasons) or "blocked",
                        max(5, config.interval_minutes),
                    )
                    return
                logger.info("[CareCog] 觸發定時網活仔衝浪巡航...")
                if await is_channel_unanswered(self.bot, channel_id, heartbeat_seconds=interval_sec):
                    logger.info("[CareCog] 頻道 %s 尚無人回應雪莉，暫緩定時巡航發文。", channel_id)
                    await asyncio.to_thread(self.engine.repository.set_surfer_last_run)
                    return
                surf_result = await self.surfer.surf_once(self.bot, dry_run=False)
                if isinstance(surf_result, dict) and surf_result.get("posted_message_id"):
                    self.social_director.apply_cooldown(channel_id, config, proactive=True)
                    if hasattr(self, "followup_scanner"):
                        await self.followup_scanner.on_bot_spoke(
                            channel_id,
                            self.bot,
                            speak_timestamp=now - 60,
                        )
        except Exception as exc:
            logger.warning("[CareCog] 網活仔衝浪巡航異常: %s", exc)

    @_surfer_loop.before_loop
    async def _before_surfer_loop(self) -> None:
        if hasattr(self.bot, "wait_until_ready"):
            await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """監聽測試頻道群友發言，接管沙盒內的熱聊陪聊 (Active Chat) 與窺屏接話 (Lurk & Chat)。"""
        if not (isinstance(message.channel, (discord.TextChannel, discord.Thread)) or hasattr(message.channel, "history") or hasattr(message.channel, "send")):
            return

        from .surfer import (
            add_token_consumption,
            clean_cyber_bubble,
            record_proactive_action_memory,
            send_cyber_bubbles,
            split_into_bubbles,
            update_bot_speak_timestamp,
            update_chatter_timestamp,
        )

        cid_str = str(message.channel.id)
        bot_user = self.bot.user

        # 若是雪莉自己的發言，刷新最後發言時間，維持熱聊窗口
        if bot_user and message.author.id == bot_user.id:
            update_bot_speak_timestamp(cid_str, keep_participants=True)
            if getattr(self, "cyber", None) is not None:
                try:
                    await self.cyber.note_external_bot_speak(
                        cid_str,
                        sent_at=message.created_at.timestamp(),
                        message_id=str(message.id),
                    )
                except Exception:
                    pass
            return

        if getattr(message.author, "bot", False):
            return

        try:
            update_chatter_timestamp(message.channel.id)

            config = await asyncio.to_thread(self.engine.repository.get_surfer_config)
            guild_id = str(getattr(message.guild, "id", "") or "")

            # 0. 沙盒群像：只有 SANDBOX_GROUP 頻道才會走到這裡；正式群永遠降級 SOLO。
            channel_mode = self.social_director.mode_for(cid_str, guild_id=guild_id, config=config)
            if channel_mode.is_group:
                if not config.chat_reply_enabled:
                    return
                # 群像模式由 SandboxGroupRunner 的 turn 排程接手，取消 solo 巡檢避免雙軌重複回覆。
                if hasattr(self, "followup_scanner"):
                    self.followup_scanner.cancel(cid_str)
                await self.sandbox_runner.handle_human_message(message, config)
                return

            sandbox_cid = config.sandbox_channel_id
            if not sandbox_cid or cid_str != str(sandbox_cid):
                return

            # Shadow 觀察模式：即使 gateway 關閉，也讓 Cyber 收到同一批事件。
            if self.cyber.mode == "shadow" and not self._gateway_enabled():
                try:
                    from .cyber import event_from_message

                    await self.cyber.submit_human(
                        event_from_message(
                            message,
                            self.bot.user,
                            is_mentioned=bool(self.bot.user and self.bot.user in message.mentions),
                        )
                    )
                except Exception:
                    pass

            if self.cyber.mode == "live" and self._gateway_enabled():
                # 單一入口已由 SocialChat → TurnGateway 接管；CareCog 只負責主動 tick。
                return

            # 1. 自動標記關懷回覆
            uid = str(message.author.id)
            actions = await asyncio.to_thread(
                self.engine.repository.list_actions,
                uid,
                limit=5,
                statuses=("approved", "pending"),
            )
            for act in actions:
                if act.get("source") == "surfer":
                    await asyncio.to_thread(
                        self.engine.record_response,
                        act["action_id"],
                        responded=True,
                    )

            if not config.chat_reply_enabled:
                return

            # 2. 優先權讓路檢測：
            # 若訊息明確 Tag 雪莉（@雪莉、@sherry 或 <@bot_id>），交由標準路由 SocialChat 處理
            bot_id = str(bot_user.id) if bot_user else ""
            content_lower = (message.content or "").lower()

            is_explicitly_targeting_bot = False
            if bot_user and bot_user in message.mentions:
                is_explicitly_targeting_bot = True
            elif "@雪莉" in (message.content or "") or "@sherry" in content_lower:
                is_explicitly_targeting_bot = True
            elif bot_id and (f"<@{bot_id}>" in (message.content or "") or f"<@!{bot_id}>" in (message.content or "")):
                is_explicitly_targeting_bot = True

            if is_explicitly_targeting_bot:
                # 使用者主動 @雪莉，主動賽博引擎讓路給 normal router，但刷新熱聊窗口時間！
                # 同時記進對話鏈：router 回覆後，對方接著說的話才算延續，而不是整群人。
                update_bot_speak_timestamp(cid_str, target_author_id=message.author.id)
                return

            # 檢查吐槽關鍵字
            roast_cog = self.bot.get_cog("RoastCog")
            if roast_cog:
                from handlers.roast.manager import roast_manager
                if roast_manager.match_rule(message.content or ""):
                    return

            # 3. 允許主動講話時段檢查（防半夜擾民）：
            # 若不在設定的活躍時段內，雪莉在夜間安靜休眠，絕不主動插話或熱聊接話（但前面已讓路給明確 @雪莉 正常回答）
            now_dt = dt.now(_TZ_TW)
            if not is_within_active_hours(now_dt.hour, config.active_hour_start, config.active_hour_end):
                return

            # 4. 對話鏈判定（取代舊的「她講過話 → 窗口內誰講話都回」計時器）
            # 判斷是否為引用回覆雪莉（未強制要求 @雪莉）
            is_reply_to_bot = False
            if message.reference:
                res = message.reference.resolved
                if isinstance(res, discord.Message) and res.author and bot_user and res.author.id == bot_user.id:
                    is_reply_to_bot = True
                elif getattr(message.reference, "cached_message", None) and bot_user and message.reference.cached_message.author.id == bot_user.id:
                    is_reply_to_bot = True

            from handlers.social.autonomy import SocialEvent, TriggerType, get_dialogue_tracker

            dialogue = get_dialogue_tracker()
            dialogue.note_human_message(cid_str, str(message.author.id))
            phase = dialogue.classify(
                cid_str,
                author_id=message.author.id,
                content=message.content or "",
                is_reply_to_bot=is_reply_to_bot,
                window_seconds=config.active_chat_window_seconds,
            )

            if phase.is_for_sherry:
                # 🔥 明確叫她、或她正在跟這個人一來一往 → 免 Tag 自然接話陪聊
                # 若只是別人在聊（AMBIENT），不自動接話，改走下面的低頻插話評分。
                if self._is_handling_chat.get(cid_str, False):
                    return

                from handlers.social.autonomy import ChannelMode

                event = SocialEvent(
                    kind="hot_chat",
                    channel_id=cid_str,
                    guild_id=guild_id,
                    trigger_type=TriggerType.HUMAN_MESSAGE,
                    message_id=str(getattr(message, "id", "") or ""),
                    content=message.content or "",
                    author_id=str(message.author.id),
                    author_name=getattr(message.author, "display_name", "") or "群友",
                    is_human=True,
                    created_at=time.time(),
                )
                decision = self.social_director.decide(
                    event,
                    config,
                    room=self.engine.repository.get_room(cid_str),
                    persona_key="sherry",
                    ignore_cooldown=True,
                    bypass_budget=True,
                )
                if not decision.allowed:
                    return

                self._is_handling_chat[cid_str] = True
                if hasattr(self, "followup_scanner"):
                    self.followup_scanner.cancel(cid_str)
                try:
                    from handlers.social.brain import BrainIntent, build_conversation_context, think
                    from handlers.social.persona import build_dynamic_persona

                    # 結構化上下文：逐字稿 + 回覆關係 + 她最近說過的話。
                    context = await build_conversation_context(
                        message.channel,
                        focus_message=message,
                        bot=self.bot,
                        limit=10,
                    )
                    script = context.render_transcript()
                    guild_id_int = getattr(message.guild, "id", None)
                    head_persona, tail_lore = build_dynamic_persona(
                        script,
                        "CHAT",
                        channel_id=message.channel.id,
                        user_query=message.content,
                        bot=self.bot,
                        guild_id=guild_id_int,
                    )

                    result = await think(
                        self.bot,
                        intent=BrainIntent.REPLY,
                        context=context,
                        head_persona=head_persona,
                        tail_lore=tail_lore,
                        to_sherry=True,
                        continuation=(phase.value == "continuation"),
                        addressed_by_name=(phase.value == "addressed" and not is_reply_to_bot),
                        model=getattr(
                            settings,
                            "PRIMARY_MODEL",
                            "freetoken:mistralai/Mixtral-8x7B-Instruct-v0.1",
                        ),
                        max_tokens=900,
                        priority=getattr(settings, "PRIORITY_INTERJECT", 2),
                        task_id=f"active-chat-{cid_str}-{int(time.time())}",
                    )
                    thought = result.thought
                    reply = result.reply

                    if not result.should_speak:
                        # 生成失敗一律沉默，不補任何罐頭台詞。
                        self.social_director.record_silent(
                            event,
                            mode=ChannelMode.SOLO,
                            persona_key="sherry",
                            reasons=["generation_failed" if not result.reply else f"brain_{result.decision}"],
                            gate=decision.gate,
                        )
                        return

                    try:
                        from .cyber.ui_config import configured_max_bubbles
                        bubble_limit = configured_max_bubbles(2)
                    except Exception:
                        bubble_limit = 2
                    bubbles = split_into_bubbles(reply, max_bubbles=bubble_limit) if config.multi_message_enabled else [clean_cyber_bubble(reply)]
                    bubbles = [b for b in bubbles if b.strip()]
                    if not bubbles:
                        self.social_director.record_silent(
                            event,
                            mode=ChannelMode.SOLO,
                            persona_key="sherry",
                            reasons=["empty_bubbles"],
                            gate=decision.gate,
                        )
                        return
                    sent = await send_cyber_bubbles(message.channel, bubbles, reply_to=message)
                    update_bot_speak_timestamp(cid_str, target_author_id=message.author.id)
                    if hasattr(self, "followup_scanner"):
                        await self.followup_scanner.on_bot_spoke(cid_str, self.bot)
                    add_token_consumption(180)

                    self.social_director.record_speak(
                        event,
                        mode=ChannelMode.SOLO,
                        persona_key="sherry",
                        text="\n".join(bubbles),
                        gate=decision.gate,
                        tokens=180,
                        model=str(getattr(settings, "PRIMARY_MODEL", "")),
                    )
                    self.social_director.observe_ai(event, role="bot", persona_key="sherry")
                    self.social_director.apply_cooldown(cid_str, config)

                    record_proactive_action_memory(
                        summary=f"熱聊對線陪聊：{bubbles[0][:60]}",
                        channel_id=cid_str,
                        user_id=message.author.id,
                        user_name=message.author.display_name,
                        detail={"bubbles": bubbles, "thought": thought},
                    )
                finally:
                    self._is_handling_chat[cid_str] = False
                return

            # 4. 平時潛水窺屏計數 (Lurk & Evaluate)
            self._lurk_counter[cid_str] = self._lurk_counter.get(cid_str, 0) + 1
            if self._lurk_counter[cid_str] >= config.interject_threshold:
                self._lurk_counter[cid_str] = 0
                if self._is_handling_chat.get(cid_str, False):
                    return
                self._is_handling_chat[cid_str] = True
                if hasattr(self, "followup_scanner"):
                    self.followup_scanner.cancel(cid_str)
                try:
                    from handlers.social.utils import fetch_recent_script
                    script = await fetch_recent_script(message.channel, 8)
                    eval_res = await self.surfer.evaluate_channel_lurk(
                        self.bot, message.channel, script, target_message=message
                    )
                    if eval_res.get("acted"):
                        update_bot_speak_timestamp(cid_str, target_author_id=message.author.id)
                        if hasattr(self, "followup_scanner"):
                            await self.followup_scanner.on_bot_spoke(cid_str, self.bot)
                finally:
                    self._is_handling_chat[cid_str] = False

        except Exception as exc:
            logger.debug("[CareCog] 測試頻道賽博群友事件處理略過: %s", exc)
