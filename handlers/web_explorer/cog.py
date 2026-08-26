import logging
from typing import Any
import aiohttp
import discord
from discord.ext import commands, tasks

import settings
# 我們完美收納分類的新夥伴們與神經系統
from handlers.web_explorer.models import VideoMeta
from handlers.web_explorer.fetcher import fetch_via_api, fetch_via_rss
from handlers.bot_brain import BotBrain, InferenceTask

logger = logging.getLogger(__name__)

class WebExplorer(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._seen_video_ids: set[str] = set()

    async def cog_load(self) -> None:
        self._explore_loop.change_interval(seconds=settings.EXPLORER_INTERVAL_SECONDS)
        self._explore_loop.start()
        logger.info("WebExplorer: 已註冊無聊時網頁刷榜探勘爬蟲機制.")

    async def cog_unload(self) -> None:
        self._explore_loop.cancel()

    def _get_brain(self) -> Any:
        brain: Any = self.bot.get_cog("BotBrain")
        return brain

    def _pick_target_channels(self) -> list[discord.TextChannel]:
        if settings.EXPLORER_TARGET_CHANNEL_IDS:
            channels: list[discord.TextChannel] = []
            for ch_id in settings.EXPLORER_TARGET_CHANNEL_IDS:
                ch = self.bot.get_channel(ch_id)
                if isinstance(ch, discord.TextChannel) and ch.permissions_for(ch.guild.me).send_messages:
                    channels.append(ch)
                else:
                    logger.warning("WebExplorer: 給定推送 channel ID %d 失效.", ch_id)
            if channels:
                return channels
            logger.warning("WebExplorer: 給定的 ID 不合規定; 切至底層掃頻防呆模式發射。")

        # 自動防呆底層尋訪全工會允許之首選推送公海大廳頻道
        for guild in self.bot.guilds:
            for channel in guild.text_channels:
                if channel.permissions_for(guild.me).send_messages:
                    return [channel]
        return []

    async def _share_video(self, target_channel: discord.TextChannel, video: VideoMeta, commentary: str) -> None:
        embed = discord.Embed(title=video["title"], url=video["url"], colour=discord.Colour.red(), description=commentary)
        embed.set_author(name=video["channel_name"])
        embed.set_thumbnail(url=f"https://img.youtube.com/vi/{video['video_id']}/mqdefault.jpg")
        embed.set_footer(text="來自 YouTube 熱門 🎬")

        try:
            await target_channel.send(embed=embed)
        except discord.HTTPException as exc:
            logger.error("WebExplorer: 卡牌裝入失敗 failed embed: %s", exc)

    @tasks.loop(seconds=1800)
    async def _explore_loop(self) -> None:
        brain = self._get_brain()
        if brain is None: return

        target_channels = self._pick_target_channels()
        if not target_channels: return

        # 乾淨的分派拉回
        timeout = aiohttp.ClientTimeout(total=getattr(settings, "DEFAULT_TIMEOUT_SEC", 8))
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                if settings.YOUTUBE_API_KEY:
                    videos = await fetch_via_api(session)
                else:
                    videos = await fetch_via_rss(session)
            except aiohttp.ClientError as exc:
                logger.error("WebExplorer: 向網頁探底大停機 %s", exc)
                return

        for video in videos:
            if video["video_id"] in self._seen_video_ids: continue
            self._seen_video_ids.add(video["video_id"])

            _video = video
            _channels = target_channels 

            system_prompt = (
                f"你是 {settings.BOT_NAME}，一個活潑的 Discord 群組成員。\n"
                "判斷以下 YouTube 影片標題是否值得分享給一群喜歡遊戲、科技、搞笑內容的台灣年輕人。\n"
                "如果值得分享，回覆格式如下（嚴格遵守）：\n"
                "YES|<一句有趣的推薦語，20字以內，用你的活潑個性>\n"
                "如果不值得，只回覆：NO\n"
                "禁止輸出任何其他內容。"
            )
            user_prompt = f"影片標題：{_video['title']}\n頻道：{_video['channel_name']}"

            # 修復作用區域捕捉技巧。我們原封不動的承繼你的修正邏輯段！！
            async def _on_classify(result: str, v: VideoMeta = _video, chs: list[discord.TextChannel] = _channels) -> None:
                result = result.strip()
                if result.upper().startswith("YES"):
                    parts = result.split("|", 1)
                    commentary = parts[1].strip() if len(parts) > 1 else "欸這個看起來不錯！"
                    for ch in chs:
                        await self._share_video(ch, v, commentary)

            task = InferenceTask(
                priority=settings.PRIORITY_WEB_EXPLORER,
                system_prompt=system_prompt, user_prompt=user_prompt,
                callback=_on_classify, model=settings.CLASSIFIER_MODEL,
                task_id=f"explore-{video['video_id']}",
            )
            await brain.enqueue_task(task)

    @_explore_loop.before_loop
    async def _before_explore_loop(self) -> None:
        await self.bot.wait_until_ready()