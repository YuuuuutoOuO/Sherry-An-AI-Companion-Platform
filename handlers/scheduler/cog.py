"""
handlers/scheduler/cog.py
=========================
背景排程執行緒 Cog — 每 10 秒掃描 schedule.json，觸發到期任務。

功能：
  - 每 10 秒檢查所有 active 任務。
  - "once" 任務：當前時間 >= trigger_time → 觸發並刪除。
  - "cron" 任務：符合當前分鐘級時間 → 觸發（保留任務供下次週期）。
  - 觸發時：將任務推入 BotBrain.task_queue 產生動態提醒文案。
  - 文案生成後：透過 Discord API 發送至 channel_id。
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Any

import discord
from discord.ext import commands, tasks

import settings
from handlers.bot_brain import BotBrain, InferenceTask
from handlers import schedule_storage
from handlers.social.utils import strip_markdown

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 動態提醒文案 Prompt（Task 4）
# ---------------------------------------------------------------------------
_NOTIFICATION_SYSTEM_PROMPT = (
    "妊是橘雪莉，15歲的可愛名偵探。現在有一個排程任務到期了，請用妳的語氣生成一句短短的「提醒呼嗚語」（不超過 20 字）。"
    "《需要遵守的規則》："
    "1. 不要包含任何人名、不要重複提醒內容（下方會已自動顏示）。"
    "2. 只輸出一句呼嗚語，不要其他文字。"
    "例子：「彈彈！時間到囉！」「哼！名偵探特地來提醒你喔～」"
)


# ---------------------------------------------------------------------------
# 簡易 Cron 解析器（支援分 時 日 月 週）
# ---------------------------------------------------------------------------
def _cron_matches(cron_expr: str, dt: datetime) -> bool:
    """
    簡易 cron 比對，支援 5 欄位格式：「分 時 日 月 週」。
    - 分: 0-59
    - 時: 0-23
    - 日: 1-31
    - 月: 1-12
    - 週: 0-6 (0=週日, 1=週一, ..., 6=週六)
    每個欄位支援：* （任意）、單一數字、逗號分隔數字。
    """
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        logger.warning("scheduler: invalid cron expr=%s", cron_expr)
        return False

    def _field_matches(field: str, value: int) -> bool:
        if field == "*":
            return True
        for item in field.split(","):
            item = item.strip()
            try:
                if int(item) == value:
                    return True
            except ValueError:
                pass
        return False

    try:
        minute_field, hour_field, day_field, month_field, weekday_field = parts
    except ValueError:
        return False

    # Python datetime: weekday: 0=Monday ... 6=Sunday
    # Cron: 0=Sunday, 1=Monday ... 6=Saturday
    cron_weekday = (dt.weekday() + 1) % 7  # 轉換為 cron 的週表示

    return (
        _field_matches(minute_field, dt.minute)
        and _field_matches(hour_field, dt.hour)
        and _field_matches(day_field, dt.day)
        and _field_matches(month_field, dt.month)
        and _field_matches(weekday_field, cron_weekday)
    )


# ---------------------------------------------------------------------------
# ScheduleWorker Cog
# ---------------------------------------------------------------------------
class ScheduleWorker(commands.Cog):
    """背景排程工人：盯時間、觸發提醒、動態生成文案並發送。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._last_cron_check_minute: int = -1  # 避免同一分鐘內重複觸發 cron

    async def cog_load(self) -> None:
        self._schedule_loop.start()
        logger.info("ScheduleWorker: 排程背景工人已上線（每 10 秒掃描一次）")

    async def cog_unload(self) -> None:
        self._schedule_loop.cancel()
        logger.info("ScheduleWorker: 排程背景工人已下線")

    # -----------------------------------------------------------------------
    # 背景迴圈
    # -----------------------------------------------------------------------
    @tasks.loop(seconds=10)
    async def _schedule_loop(self) -> None:
        await self._tick()

    @_schedule_loop.before_loop
    async def _before_schedule_loop(self) -> None:
        await self.bot.wait_until_ready()
        logger.info("ScheduleWorker: 排程 loop 已就緒，開始監控任務...")

    async def _tick(self) -> None:
        """每次 tick 的核心邏輯。"""
        now_ts = int(time.time())
        now_dt = datetime.now(timezone(timedelta(hours=8)))  # 台灣時間

        # 1. 處理單次任務（once）
        triggered_once = await schedule_storage.purge_once_tasks_before(now_ts)
        for task in triggered_once:
            await self._fire_notification(task)

        # 2. 處理週期任務（cron）— 每分鐘最多觸發一次，避免每秒重複
        current_minute = now_dt.minute + now_dt.hour * 60
        if current_minute != self._last_cron_check_minute:
            self._last_cron_check_minute = current_minute
            all_tasks = await schedule_storage.load_all_tasks()
            for task in all_tasks:
                if not task.get("is_active", True):
                    continue
                if task.get("schedule_type") != "cron":
                    continue
                cron_expr = task.get("cron_expr", "")
                if cron_expr and _cron_matches(cron_expr, now_dt):
                    await self._fire_notification(task)

    # -----------------------------------------------------------------------
    # 動態提醒文案生成 + 發送（Task 4）
    # -----------------------------------------------------------------------
    async def _fire_notification(self, task: dict[str, Any]) -> None:
        """
        對一個已到期的任務生成動態提醒文案並發送。
        流程：
          1. 建立 InferenceTask 推入 BotBrain.task_queue（使用 QUICK_MODEL）。
          2. callback 中用 Discord API 發送至 channel_id。
          3. 透過 asyncio.Future 等待結果。
        """
        brain: BotBrain | None = self.bot.get_cog("BotBrain")
        if not brain:
            logger.error("ScheduleWorker: BotBrain 不可用，無法觸發提醒 task=%s", task.get("task_id"))
            return

        user_id = task.get("user_id", "未知")
        channel_id_str = task.get("channel_id", "")
        content = task.get("content", "提醒事項")

        # 🔑 優先使用建立任務時存下的名稱（最可靠），
        # 其次才查 Discord cache，避免 LLM 腦補
        display_name: str = task.get("display_name", "").strip()
        if not display_name:
            try:
                user_obj = self.bot.get_user(int(user_id))
            except (ValueError, TypeError):
                user_obj = None
            display_name = user_obj.display_name if user_obj else str(user_id)

        user_mention = f"<@{user_id}>"

        # LLM 只需生成短短的呼嗚語，名字與內容都會由我們确保套入
        user_prompt = f"使用者名稱：{display_name}，提醒事項：{content}"

        try:
            channel_id = int(channel_id_str)
        except (ValueError, TypeError):
            logger.error("ScheduleWorker: 無效的 channel_id=%s", channel_id_str)
            return

        channel = self.bot.get_channel(channel_id)
        if channel is None:
            logger.warning("ScheduleWorker: 無法取得頻道 channel_id=%s，嘗試 fetch_channel", channel_id)
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception:
                logger.error("ScheduleWorker: 無法 fetch_channel channel_id=%s", channel_id)
                return

        if not isinstance(channel, discord.TextChannel):
            logger.error("ScheduleWorker: channel_id=%s 不是文字頻道", channel_id)
            return

        # 建立 Future 等待 LLM 回覆
        loop = asyncio.get_event_loop()
        result_future: asyncio.Future = loop.create_future()

        async def _on_reply(reply: str) -> None:
            try:
                flavor = strip_markdown(reply.strip())
                if not flavor:
                    flavor = "哼！時間到囉！"
                # 結構： @mention 呼嗚語 + 內容必定顯示在下方，100% 不會漏掉
                final_msg = f"{user_mention} {flavor}\n📌 提醒事項：**{content}**"
                await channel.send(final_msg)
                logger.info("ScheduleWorker: 已發送提醒 task=%s → channel=%s", task.get("task_id"), channel_id)
                result_future.set_result(True)
            except discord.HTTPException as exc:
                logger.error("ScheduleWorker: 發送提醒失敗: %s", exc)
                result_future.set_result(False)
            except Exception as exc:
                logger.exception("ScheduleWorker: 發送提醒未知錯誤: %s", exc)
                result_future.set_result(False)

        notify_task = InferenceTask(
            priority=settings.PRIORITY_INTERJECT,  # 使用插話優先級（比 VIBE/DRAW 高）
            system_prompt=_NOTIFICATION_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            callback=_on_reply,
            model=settings.QUICK_MODEL,
            task_id=f"schedule-notify-{task.get('task_id', 'unknown')}",
        )

        await brain.enqueue_task(notify_task)
        logger.debug("ScheduleWorker: enqueued notification task=%s", task.get("task_id"))

        # 不阻塞 tick loop，但記錄結果
        try:
            await asyncio.wait_for(result_future, timeout=30.0)
        except asyncio.TimeoutError:
            logger.error("ScheduleWorker: 提醒生成超時 task=%s", task.get("task_id"))