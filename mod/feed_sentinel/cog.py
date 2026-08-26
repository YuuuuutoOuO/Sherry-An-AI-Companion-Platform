"""
cog.py  (mod/feed_sentinel)
===========================
AI 社群情報哨兵 Cog。

每 15 分鐘背景巡邏，符合 08:00~23:00 時段且距離上次推播 >= 1 小時 (3600s) 時，
由 BotBrain 提煉 3 點繁中重點與雪莉銳評，推播至指定 Discord 頻道。
"""

from __future__ import annotations

import asyncio
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
from .models import FeedItem, SharedPost
from .repository import SentinelRepository

logger = logging.getLogger(__name__)

TAIWAN_TZ = timezone(timedelta(hours=8))


def _load_seen_ids() -> set[str]:
    """[相容性保留] 載入已推播過的情報 ID 集合。"""
    repo = SentinelRepository()
    return set(repo._seen_keys)


def _save_seen_ids(seen_ids: set[str]) -> None:
    """[相容性保留] 持久化已推播情報 ID 集合。"""
    repo = SentinelRepository()
    repo._seen_keys.update(seen_ids)
    repo._save_seen_keys()


class FeedSentinelCog(commands.Cog):
    """AI 社群情報哨兵 Cog。"""

    def __init__(self, bot: commands.Bot, repository: SentinelRepository | None = None) -> None:
        self.bot = bot
        self.repository = repository or SentinelRepository()
        self._last_post_time: float = 0.0
        self._is_processing: bool = False

    @property
    def _seen_ids(self) -> set[str]:
        """相容性屬性：取得去重指紋集合。"""
        return self.repository._seen_keys

    @_seen_ids.setter
    def _seen_ids(self, value: set[str]) -> None:
        """相容性設定器。"""
        self.repository._seen_keys = set(value)

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
        """抓取情報並交由 AI 處理後推播至 Discord，並封存至持久化倉儲。"""
        if self._is_processing:
            return False

        self._is_processing = True
        try:
            items = await fetch_all_feeds()
            if not items:
                logger.debug("[FeedSentinel] 未抓取到任何情報項目。")
                return False

            # 找出尚未推播過的第一個項目 (多維指紋比對)
            candidate: FeedItem | None = None
            for item in items:
                if not self.repository.is_duplicate(item) or force:
                    candidate = item
                    break

            if not candidate:
                logger.debug("[FeedSentinel] 所有抓取到的情報均已推播過 (多維特徵完全匹配)。")
                return False

            # 獲取目標頻道 (優先快取，次之 fetch_channel API)
            target_channel = self.bot.get_channel(config.TARGET_CHANNEL_ID)
            if target_channel is None:
                try:
                    target_channel = await self.bot.fetch_channel(config.TARGET_CHANNEL_ID)
                except Exception as fetch_err:
                    logger.warning(
                        "[FeedSentinel] 無法取得目標推播頻道 ID: %d (%s)。請確認 .env 中 SENTINEL_TARGET_CHANNEL_ID 是否正確設定。",
                        config.TARGET_CHANNEL_ID,
                        fetch_err,
                    )
                    return False

            if not hasattr(target_channel, "send"):
                logger.warning(
                    "[FeedSentinel] 目標頻道 ID: %d 不支援發送訊息", config.TARGET_CHANNEL_ID
                )
                return False

            # 透過 BotBrain 進行深度閱讀、重點萃取與銳評
            ai_analysis = await self._generate_sherry_briefing(candidate)

            # 組裝 Discord Embed
            embed = self._build_discord_embed(candidate, ai_analysis)

            if hasattr(target_channel, "send"):
                await target_channel.send(embed=embed)
            self._last_post_time = time.time()

            # 封存文章紀錄至 ./data/feed_sentinel/posts.json
            now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            shared_post = SharedPost(
                post_id=candidate.item_id,
                source=candidate.source,
                title=candidate.title,
                url=candidate.url,
                original_summary=candidate.summary,
                sherry_analysis=ai_analysis,
                author_or_repo=candidate.author_or_repo,
                tags=candidate.tags,
                published_at=candidate.published_at,
                posted_at=now_iso,
            )
            self.repository.save_post(shared_post)

            logger.info(
                "[FeedSentinel] 成功推播情報 [%s]: %s 至頻道 %d 並已持久化紀錄",
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
        brain: Any = self.bot.get_cog("BotBrain")
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
            return str(result)
        except asyncio.TimeoutError:
            logger.warning("[FeedSentinel] AI 推理超時，使用原始摘要作為 fallback。")
            return f"💡 **【情報摘要】**\n- {item.summary}\n\n🕵️‍♀️ **【雪莉銳評】**\n這篇情報看起來很有意思，大家快點進去看看吧！"

    def _build_discord_embed(self, item: FeedItem, analysis_text: str) -> discord.Embed:
        """建構精美 Discord Embed 卡片。"""
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

    def get_status_info(self) -> dict[str, Any]:
        """取得哨兵當前運行狀態 (供 Web UI 查詢)。"""
        now_tw = datetime.now(TAIWAN_TZ)
        in_window = config.START_HOUR <= now_tw.hour < config.END_HOUR
        now_ts = time.time()
        elapsed = now_ts - self._last_post_time if self._last_post_time > 0 else None
        cooldown_rem = (
            max(0.0, config.MIN_GAP_SECONDS - elapsed) if elapsed is not None else 0.0
        )

        return {
            "enabled": config.ENABLED,
            "is_processing": self._is_processing,
            "in_schedule_window": in_window,
            "current_hour": now_tw.hour,
            "start_hour": config.START_HOUR,
            "end_hour": config.END_HOUR,
            "target_channel_id": config.TARGET_CHANNEL_ID,
            "min_gap_seconds": config.MIN_GAP_SECONDS,
            "last_post_time": self._last_post_time,
            "cooldown_remaining_seconds": round(cooldown_rem, 1),
        }

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

