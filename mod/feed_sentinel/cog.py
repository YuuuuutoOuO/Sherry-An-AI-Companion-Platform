"""
cog.py  (mod/feed_sentinel)
===========================
AI 社群情報哨兵 Cog。

每 15 分鐘背景巡邏，符合 08:00~23:00 時段且距離上次推播 >= 1 小時 (3600s) 時，
由 BotBrain 提煉 3 點繁中重點與雪莉銳評，推播至指定 Discord 頻道。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

import settings
from handlers.bot_brain import BotBrain, InferenceTask
from handlers.social.persona import build_dynamic_persona

from . import config
from .fetcher import fetch_all_feeds
from .models import FeedItem

logger = logging.getLogger(__name__)

TAIWAN_TZ = timezone(timedelta(hours=8))


def _load_seen_ids() -> set[str]:
    """載入已推播過的情報 ID 集合。"""
    if config.SEEN_PATH.exists():
        try:
            raw = config.SEEN_PATH.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, list):
                return set(data)
        except Exception as exc:
            logger.warning("[FeedSentinel] 讀取 sentinel_seen.json 異常: %s", exc)
    return set()


def _save_seen_ids(seen_ids: set[str]) -> None:
    """持久化已推播情報 ID 集合（保留最新 500 筆防止無限制膨脹）。"""
    try:
        trimmed = list(seen_ids)[-500:]
        config.SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.SEEN_PATH.write_text(
            json.dumps(trimmed, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("[FeedSentinel] 寫入 sentinel_seen.json 異常: %s", exc)


class FeedSentinelCog(commands.Cog):
    """AI 社群情報哨兵 Cog。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._seen_ids: set[str] = _load_seen_ids()
        self._last_post_time: float = 0.0
        self._is_processing: bool = False

    async def cog_load(self) -> None:
        self._sentinel_loop.start()
        logger.info(
            "[FeedSentinel] AI 社群情報哨兵已啟動！(時段: %02d:00~%02d:00, 間隔: %ds, 目標頻道: %d)",
            config.START_HOUR,
            config.END_HOUR,
            config.MIN_GAP_SECONDS,
            config.TARGET_CHANNEL_ID,
        )

    async def cog_unload(self) -> None:
        self._sentinel_loop.cancel()
        logger.info("[FeedSentinel] AI 社群情報哨兵已停止。")

    # -----------------------------------------------------------------------
    # 背景巡邏迴圈
    # -----------------------------------------------------------------------
    @tasks.loop(minutes=config.CHECK_INTERVAL_MINUTES)
    async def _sentinel_loop(self) -> None:
        await self.bot.wait_until_ready()

        if not config.ENABLED:
            logger.debug("[FeedSentinel] 模組在 config.py 中設定為停用，略過本次輪詢。")
            return

        now_tw = datetime.now(TAIWAN_TZ)

        # 1. 檢查時段限制 (預設 08:00 ~ 23:00)
        if now_tw.hour < config.START_HOUR or now_tw.hour >= config.END_HOUR:
            logger.debug("[FeedSentinel] 目前非推播時段 (%02d:00)，略過。", now_tw.hour)
            return

        # 2. 檢查推播間隔 (至少 1 小時)
        now_ts = time.time()
        elapsed = now_ts - self._last_post_time
        if self._last_post_time > 0 and elapsed < config.MIN_GAP_SECONDS:
            logger.debug(
                "[FeedSentinel] 距離上次推播僅經過 %.1f 分鐘 (需滿 %d 分鐘)，略過。",
                elapsed / 60,
                config.MIN_GAP_SECONDS // 60,
            )
            return

        # 3. 觸發抓取與推播
        await self._process_feed_and_post()

    async def _process_feed_and_post(self, force: bool = False) -> bool:
        """抓取情報並交由 AI 處理後推播至 Discord。"""
        if self._is_processing:
            return False

        self._is_processing = True
        try:
            items = await fetch_all_feeds()
            if not items:
                logger.debug("[FeedSentinel] 未抓取到任何情報項目。")
                return False

            # 找出尚未推播過的第一個項目
            candidate: FeedItem | None = None
            for item in items:
                if item.item_id not in self._seen_ids or force:
                    candidate = item
                    break

            if not candidate:
                logger.debug("[FeedSentinel] 所有抓取到的情報均已推播過。")
                return False

            # 獲取目標頻道
            target_channel = self.bot.get_channel(config.TARGET_CHANNEL_ID)
            if not target_channel or not isinstance(target_channel, discord.TextChannel):
                logger.warning(
                    "[FeedSentinel] 找不到目標推播頻道 ID: %d", config.TARGET_CHANNEL_ID
                )
                return False

            # 透過 BotBrain 進行深度閱讀、重點萃取與銳評
            ai_analysis = await self._generate_sherry_briefing(candidate)

            # 組裝 Discord Embed
            embed = self._build_discord_embed(candidate, ai_analysis)

            await target_channel.send(embed=embed)
            self._last_post_time = time.time()
            self._seen_ids.add(candidate.item_id)
            _save_seen_ids(self._seen_ids)

            logger.info(
                "[FeedSentinel] 成功推播情報 [%s]: %s 至頻道 %d",
                candidate.source,
                candidate.title,
                config.TARGET_CHANNEL_ID,
            )
            return True

        except Exception as exc:
            logger.error("[FeedSentinel] 處理推播發生異常: %s", exc, exc_info=True)
            return False
        finally:
            self._is_processing = False

    async def _generate_sherry_briefing(self, item: FeedItem) -> str:
        """呼叫 BotBrain 生成名偵探橘雪莉的 3 點精華與銳評。"""
        brain: BotBrain | None = self.bot.get_cog("BotBrain")
        if not brain:
            # Fallback 若無 Brain 則返回原始摘要
            return f"💡 **【情報重點】**\n- {item.summary}\n\n🕵️‍♀️ **【雪莉銳評】**\n名偵探正在觀察這項新技術的發展！"

        head_persona, _tail = build_dynamic_persona("", "CHAT", channel_id=config.TARGET_CHANNEL_ID)

        system_prompt = (
            f"{head_persona}\n\n"
            "【任務：AI 社群情報解讀與精華萃取】\n"
            "社群情報哨兵捕捉到了以下最新的 AI / 開源技術情報。\n"
            "請詳細閱讀情報標題、作者與摘要，輸出結構化的繁體中文解讀。\n\n"
            "【輸出格式嚴格規範】\n"
            "💡 **【名偵探精選 3 大核心重點】**\n"
            "1. <第一點核心突破或特色，精簡扼要>\n"
            "2. <第二點核心突破或特色，精簡扼要>\n"
            "3. <第三點核心突破或特色，精簡扼要>\n\n"
            "🕵️‍♀️ **【雪莉的私房銳評】**\n"
            "<以橘雪莉 15 歲毒舌但專業敏銳的名偵探第一人稱口吻，用 50~100 字給予犀利、有趣且有見地的評價。不要帶書名號或多餘前綴>"
        )

        user_prompt = (
            f"【情報來源】：{item.source}\n"
            f"【標題】：{item.title}\n"
            f"【作者/機構/Repo】：{item.author_or_repo}\n"
            f"【原始內容摘要】：\n{item.summary}\n"
            f"【標籤】：{', '.join(item.tags)}"
        )

        loop = asyncio.get_running_loop()
        future = loop.create_future()

        async def _on_reply(reply: str) -> None:
            if not future.done():
                future.set_result(reply.strip())

        task = InferenceTask(
            priority=getattr(settings, "PRIORITY_HEARTBEAT", 20),
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            callback=_on_reply,
            model=settings.PRIMARY_MODEL,
            task_id=f"sentinel-{item.item_id}-{int(time.time())}",
        )
        await brain.enqueue_task(task)

        try:
            result = await asyncio.wait_for(future, timeout=90.0)
            return result
        except asyncio.TimeoutError:
            logger.warning("[FeedSentinel] AI 推理超時，使用原始摘要作為 fallback。")
            return f"💡 **【情報摘要】**\n- {item.summary}\n\n🕵️‍♀️ **【雪莉銳評】**\n這篇情報看起來很有意思，大家快點進去看看吧！"

    def _build_discord_embed(self, item: FeedItem, analysis_text: str) -> discord.Embed:
        """建構精美 Discord Embed 卡片。"""
        # 根據來源設定不同代表色
        color_map = {
            "HuggingFace": 0xFFD21E,  # HF 黃
            "arXiv": 0xB31B1B,        # arXiv 紅
            "GitHub": 0x238636,       # GitHub 綠
        }
        color = 0x7289DA
        for k, c in color_map.items():
            if k.lower() in item.source.lower():
                color = c
                break

        embed = discord.Embed(
            title=f"📡 [{item.source}] {item.title}",
            url=item.url,
            description=analysis_text,
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        if item.author_or_repo:
            embed.set_author(name=f"發布/作者: {item.author_or_repo}")

        if item.tags:
            embed.add_field(
                name="🏷️ 分類標籤",
                value=" · ".join([f"`{t}`" for t in item.tags[:4]]),
                inline=False,
            )

        embed.set_footer(
            text="橘雪莉 AI 社群情報哨兵 · 每日 08:00~23:00 每小時巡邏"
        )
        return embed

    # -----------------------------------------------------------------------
    # 管理員 Slash 指令
    # -----------------------------------------------------------------------
    @app_commands.command(name="sentinel_fetch", description="手動觸發 AI 社群情報哨兵抓取最新情報")
    async def sentinel_fetch_cmd(self, interaction: discord.Interaction) -> None:
        """手動觸發情報哨兵抓取。"""
        await interaction.response.defer(ephemeral=True)
        success = await self._process_feed_and_post(force=True)
        if success:
            await interaction.followup.send(
                f"✅ 情報哨兵抓取完成！已將最新情報推播至 <#{config.TARGET_CHANNEL_ID}>。",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "⚠️ 抓取已執行，但目前沒有新情報或目標頻道發送失敗，請查看日誌。",
                ephemeral=True,
            )


async def setup(bot: commands.Bot) -> None:
    """動態載入 FeedSentinelCog 插件。"""
    await bot.add_cog(FeedSentinelCog(bot))
