"""
handlers/social/recovery_provider.py
====================================
AI 推演中斷未結案任務開機自癒與對帳提供者 (AITaskRecoveryProvider)。
負責短暫重啟自動接續運算、超時自癒清理與殭屍 Loading 訊息消除。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import discord
from core.recovery.base import Recoverable, SnapshotMetadata
from handlers.social.task_registry import InFlightTask, get_in_flight_registry
import settings

logger = logging.getLogger(__name__)


class AITaskRecoveryProvider(Recoverable):
    """AI 任務中斷快照與開機自癒對帳提供者。"""

    def __init__(self, bot: discord.Client | None = None) -> None:
        self.bot = bot
        self.registry = get_in_flight_registry()

    @property
    def recovery_identifier(self) -> str:
        return "in_flight_ai_tasks"

    @property
    def default_ttl_seconds(self) -> float:
        return 300.0  # 快照存活時效 5 分鐘

    async def capture_recovery_snapshot(self) -> dict[str, Any] | None:
        """收集所有進行中的 AI 推演中斷任務。"""
        tasks = self.registry.list_all()
        if not tasks:
            return None
        return {"tasks": [t.to_dict() for t in tasks]}

    async def _restore_single_task(
        self,
        task: InFlightTask,
        now: float,
        bot: discord.Client | None,
        sem: asyncio.Semaphore,
    ) -> None:
        """復原單一未結案 AI 任務，具備並發限制、字數分段、沙盒隔離與錯誤保護。"""
        async with sem:
            channel_id = task.channel_id
            message_id = task.message_id
            elapsed = now - task.start_time

            channel = None
            if bot:
                channel = bot.get_channel(channel_id)
                if not channel and hasattr(bot, "fetch_channel"):
                    try:
                        channel = await bot.fetch_channel(channel_id)
                    except Exception:
                        channel = None

            if not channel:
                logger.warning("[AIRecovery] 找不到頻道 %s，略過任務對帳 (msg: %s)", channel_id, message_id)
                self.registry.unregister(task.task_id)
                return

            try:
                msg = await channel.fetch_message(message_id)
            except discord.NotFound:
                logger.debug("[AIRecovery] 找不到原訊息 %s (可能已被刪除)，安全忽略", message_id)
                self.registry.unregister(task.task_id)
                return
            except Exception as exc:
                logger.warning("[AIRecovery] 取得訊息 %s 失敗: %s", message_id, exc)
                return

            # 若有來源訊息，嘗試移除可能殘留的漏斗等待反應
            if task.origin_message_id and hasattr(channel, "fetch_message"):
                try:
                    origin_msg = await channel.fetch_message(task.origin_message_id)
                    from handlers.social.utils import remove_hourglass_reaction

                    await remove_hourglass_reaction(origin_msg, getattr(bot, "user", None))
                except Exception:
                    pass

            # 依據重啟耗時與任務性質進行對帳判定
            if elapsed <= 45.0 and bot:
                # 短暫重啟 (熱重載 < 45s)：嘗試重新推演或接續運算
                brain: Any = bot.get_cog("BotBrain") if hasattr(bot, "get_cog") else None
                if brain and hasattr(brain, "generate_reply"):
                    try:
                        logger.info("[AIRecovery] 熱重載接續運算 (Task: %s, Msg: %s)", task.task_id, message_id)
                        await msg.edit(
                            content="> 💡 **雪莉思維推演與執行步驟 (Thinking Process)**\n> ⏳ *🔄 雪莉已完成重啟，正在接續為您推演解答中...*",
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                        result = await brain.generate_reply(task.prompt, user_id=task.user_id, channel_id=channel_id)
                        from handlers.social.persona_guard import sanitize_reply
                        final_text = sanitize_reply(result or "（盯——思維接續完成！）", user_input=task.prompt)

                        from handlers.social.utils import _chunk_message, BOT_MESSAGE_INTENTS

                        chunks = _chunk_message(final_text, max_length=1900)
                        no_mention = discord.AllowedMentions.none()

                        await msg.edit(content=chunks[0], allowed_mentions=no_mention)

                        for sub_chunk in chunks[1:]:
                            try:
                                await channel.send(sub_chunk, allowed_mentions=no_mention)
                            except Exception as chunk_exc:
                                logger.error("[AIRecovery] 發送分段訊息失敗: %s", chunk_exc)

                        # 補充標準互動 reaction 與意圖紀錄
                        for emoji in ("🔄", "📌", "🗑️"):
                            try:
                                await msg.add_reaction(emoji)
                            except Exception:
                                pass

                        BOT_MESSAGE_INTENTS[msg.id] = {
                            "intent": "RECOVERED_CHAT",
                            "thought": "系統重啟後自動接續運算恢復",
                            "prompt": task.prompt,
                        }

                        logger.info("[AIRecovery] 成功覆寫熱重載任務解答 (Msg: %s)", message_id)
                        self.registry.unregister(task.task_id)
                        return
                    except Exception as gen_err:
                        logger.warning("[AIRecovery] 接續運算生成失敗，降級顯示重啟說明: %s", gen_err)

            # 超時重啟 (> 45s) 或降級處置：優雅消除殭屍 Loading 轉圈訊息
            notice = "⚠️ *雪莉剛剛因系統更新短暫重啟，先前的推演被打斷了。若您仍需要解答，請再跟我說一次喔！*"
            try:
                await msg.edit(content=notice, allowed_mentions=discord.AllowedMentions.none())
                logger.info("[AIRecovery] 已將未結案 AI 任務 (%s) 替換為重啟說明，消除殭屍訊息", message_id)
            except Exception as edit_err:
                logger.warning("[AIRecovery] 編輯自癒訊息失敗: %s", edit_err)

            self.registry.unregister(task.task_id)

    async def restore_from_snapshot(
        self, snapshot_data: dict[str, Any], metadata: SnapshotMetadata
    ) -> None:
        """開機時對未結案任務進行掃描與自癒對帳。多任務並行處理與 Semaphore 節流。"""
        task_list = snapshot_data.get("tasks", [])
        if not task_list:
            return

        bot = self.bot
        now = time.time()
        sem = asyncio.Semaphore(3)  # 最多同時並發恢復 3 個任務，防範 Discord 與 LLM 節流

        coros = []
        for t_dict in task_list:
            task = InFlightTask.from_dict(t_dict)
            coros.append(self._restore_single_task(task, now, bot, sem))

        await asyncio.gather(*coros, return_exceptions=True)
