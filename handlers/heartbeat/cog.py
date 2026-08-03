"""
handlers/heartbeat/cog.py
==========================
Heartbeat 心跳主動排程腦 Cog。

每 30 分鐘讀取 heartbeat.md 掃描到期與冷場任務，主動進行話語生成並發送至指定頻道。
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands, tasks

import settings
from handlers.bot_brain import BotBrain, InferenceTask
from handlers.heartbeat.parser import HeartbeatTask, parse_heartbeat_markdown
from handlers.social.persona import build_dynamic_persona
from handlers.social.utils import simulate_typing_and_send

logger = logging.getLogger(__name__)

# 台灣時間時區 UTC+8
TAIWAN_TZ = timezone(timedelta(hours=8))
CONDITION_START_HOUR = getattr(settings, "HEARTBEAT_CONDITION_START_HOUR", 9)
CONDITION_END_HOUR = getattr(settings, "HEARTBEAT_CONDITION_END_HOUR", 22)
CONDITION_MIN_GAP_SECONDS = getattr(settings, "HEARTBEAT_CONDITION_MIN_GAP_SECONDS", 7200)


class HeartbeatCog(commands.Cog):
    """橘雪莉主動心跳腦：主動喚醒、晨報廣播與冷場救星。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._last_executed: dict[str, str] = {}  # task_title -> last executed YYYY-MM-DD
        self._daily_quiet_triggers: dict[str, int] = {}  # YYYY-MM-DD -> trigger count
        self._last_condition_trigger: dict[int, float] = {}  # channel_id -> last trigger timestamp
        self._heartbeat_path: Path = getattr(
            settings, "HEARTBEAT_PATH", settings.BASE_DIR / "data" / "heartbeat.md"
        )

    async def cog_load(self) -> None:
        self._heartbeat_loop.start()
        logger.info("[HEARTBEAT] 橘雪莉主動心跳腦 Task 已啟動！")

    async def cog_unload(self) -> None:
        self._heartbeat_loop.cancel()
        logger.info("[HEARTBEAT] 橘雪莉主動心跳腦 Task 已停用。")

    @tasks.loop(minutes=settings.HEARTBEAT_CHECK_INTERVAL_MINUTES)
    async def _heartbeat_loop(self) -> None:
        await self.bot.wait_until_ready()
        tasks_list = parse_heartbeat_markdown(self._heartbeat_path)
        if not tasks_list:
            return

        now_tw = datetime.now(TAIWAN_TZ)
        today_str = now_tw.strftime("%Y-%m-%d")

        for task in tasks_list:
            try:
                if task.task_type == "time":
                    await self._evaluate_time_task(task, now_tw, today_str)
                elif task.task_type == "condition":
                    await self._evaluate_condition_task(task, now_tw, today_str)
            except Exception as exc:
                logger.error("[HEARTBEAT] 處理任務 '%s' 異常: %s", task.title, exc)

    async def _evaluate_time_task(
        self, task: HeartbeatTask, now_tw: datetime, today_str: str
    ) -> None:
        """比對時間型任務（如 08:00 或 週五 17:30）。"""
        last_run = self._last_executed.get(task.title, "")
        if last_run == today_str:
            return

        scheduled_text = task.schedule_time.strip()
        is_matched = False

        # 比對週別（若包含「週一」~「週日」）
        weekday_names = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]
        current_weekday_name = weekday_names[now_tw.weekday()]

        if any(w in scheduled_text for w in weekday_names):
            if current_weekday_name not in scheduled_text:
                return

        # 比對時間 (HH:MM) 允許 ±25 分鐘誤差以配合 30 分鐘掃描週期
        time_match = re.search(r"(\d{1,2}):(\d{2})", scheduled_text)
        if time_match:
            target_h = int(time_match.group(1))
            target_m = int(time_match.group(2))
            target_minutes = target_h * 60 + target_m
            current_minutes = now_tw.hour * 60 + now_tw.minute

            if abs(current_minutes - target_minutes) <= 25:
                is_matched = True

        if is_matched:
            self._last_executed[task.title] = today_str
            await self._dispatch_heartbeat_task(task)

    async def _evaluate_condition_task(
        self, task: HeartbeatTask, now_tw: datetime, today_str: str
    ) -> None:
        """比對條件型任務（如冷場 45 分鐘）。僅在每日 09:00-22:00 觸發，間隔至少 2 小時。"""
        # 時間窗口限制：只在 09:00-22:00 之間觸發
        if now_tw.hour < CONDITION_START_HOUR or now_tw.hour >= CONDITION_END_HOUR:
            return

        # 間隔限制：同一頻道兩次觸發至少間隔 2 小時
        last_ts = self._last_condition_trigger.get(task.channel_id, 0)
        if time.time() - last_ts < CONDITION_MIN_GAP_SECONDS:
            return

        # 每日上限最多觸發 3 次冷場救星
        triggered_count = self._daily_quiet_triggers.get(today_str, 0)
        if triggered_count >= 3:
            return

        channel = self.bot.get_channel(task.channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            return

        # 取得頻道最後一則訊息的時間戳
        last_msg_time: datetime | None = None
        async for msg in channel.history(limit=1):
            last_msg_time = msg.created_at
            break

        if not last_msg_time:
            return

        quiet_minutes = (datetime.now(timezone.utc) - last_msg_time).total_seconds() / 60.0
        if quiet_minutes >= task.condition_quiet_minutes:
            self._daily_quiet_triggers[today_str] = triggered_count + 1
            self._last_condition_trigger[task.channel_id] = time.time()
            await self._dispatch_heartbeat_task(task, is_cold_channel=True)

    async def _dispatch_heartbeat_task(
        self, task: HeartbeatTask, is_cold_channel: bool = False
    ) -> None:
        """推送心跳任務至 BotBrain。"""
        brain: BotBrain | None = self.bot.get_cog("BotBrain")
        if not brain:
            return

        now_tw = datetime.now(TAIWAN_TZ)

        target_channel = self.bot.get_channel(task.channel_id)
        if not target_channel or not isinstance(target_channel, discord.TextChannel):
            logger.warning("[HEARTBEAT] 找不到頻道 ID: %d", task.channel_id)
            return

        head_persona, _tail = build_dynamic_persona("", "CHAT", channel_id=task.channel_id)

        vibe_context = ""
        if is_cold_channel:
            vibe_mgr = self.bot.get_cog("VibeManager")
            if vibe_mgr:
                vibe_snippet = vibe_mgr.get_top_topics_snippet(task.channel_id)
                if vibe_snippet:
                    vibe_context = f"\n近期群組熱門話題供參考：\n{vibe_snippet}\n"

        system_prompt = (
            f"{head_persona}\n\n"
            f"【💓 雪莉心跳主動任務啟動：{task.title}】\n"
            f"任務需求：{task.prompt}\n"
            f"{vibe_context}"
            "【執行規則】：\n"
            "1. 必須以橘雪莉第一人稱口吻直接開口說話，文字生動有活力。\n"
            "2. 嚴禁引號括號包裹整句，也不要輸出任何小說旁白敘述。\n"
            "3. 保持 15 歲名偵探的自信、自然與調皮人設。"
        )

        async def _on_heartbeat_reply(reply: str) -> None:
            clean_reply = reply.strip()
            if clean_reply:
                try:
                    await simulate_typing_and_send(target_channel, clean_reply)
                except Exception as exc:
                    logger.error("[HEARTBEAT] 發送心跳訊息至頻道 %d 失敗: %s", task.channel_id, exc)

        inference_task = InferenceTask(
            priority=getattr(settings, "PRIORITY_HEARTBEAT", 20),
            system_prompt=system_prompt,
            user_prompt=f"主動發言任務：{task.title}",
            callback=_on_heartbeat_reply,
            model=settings.PRIMARY_MODEL,
            task_id=f"heartbeat-{task.channel_id}-{int(now_tw.timestamp())}",
        )
        await brain.enqueue_task(inference_task)
        logger.info("[HEARTBEAT] 已將心跳任務 '%s' 推送至 BotBrain 佇列 (Channel: %d)", task.title, task.channel_id)


import re


async def setup(bot: commands.Bot) -> None:
    """載入 HeartbeatCog 模組。"""
    await bot.add_cog(HeartbeatCog(bot))
