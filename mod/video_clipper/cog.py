"""
mod/video_clipper/cog.py
========================
Discord Cog providing the /video slash command interface.
"""

from __future__ import annotations

import io
import logging
from typing import Optional

import discord  # type: ignore[import-not-found]
from discord import app_commands
from discord.ext import commands  # type: ignore[import-not-found]

from mod.video_clipper.clients.yt_gif_client import YoutubeGifClient
from mod.video_clipper.service import VideoClipperService

logger = logging.getLogger(__name__)


class VideoClipperCog(commands.Cog):  # type: ignore[misc]
    """Discord Cog for YouTube video clip conversion to GIF."""

    def __init__(self, bot: commands.Bot, service: Optional[VideoClipperService] = None) -> None:
        """Initializes the VideoClipperCog with bot instance and service.

        Args:
            bot: Discord bot instance.
            service: Optional custom VideoClipperService (defaults to YoutubeGifClient).
        """
        self.bot: commands.Bot = bot
        if service is None:
            client = YoutubeGifClient()
            self.service: VideoClipperService = VideoClipperService(client)
        else:
            self.service = service

    @app_commands.command(  # type: ignore[misc]
        name="video",
        description="將 YouTube 影片指定時間片段轉換為 GIF 動圖發送到頻道中",
    )
    @app_commands.describe(  # type: ignore[misc]
        url="YouTube 影片網址 (例如 https://www.youtube.com/watch?v=...)",
        start_time="開始時間 (例如 1:30, 10 或 00:01:15)",
        end_time="結束時間 (例如 1:45, 25 或 00:01:30)",
    )
    async def video_command(
        self,
        interaction: discord.Interaction,
        url: str,
        start_time: str,
        end_time: str,
    ) -> None:
        """Slash command handler for /video.

        Args:
            interaction: Discord interaction context.
            url: YouTube video URL.
            start_time: Start timestamp.
            end_time: End timestamp.
        """
        logger.info(
            "Received /video command from user %s (%s) for URL=%s [%s - %s]",
            interaction.user.name,
            interaction.user.id,
            url,
            start_time,
            end_time,
        )

        await interaction.response.defer(thinking=True)

        try:
            result = await self.service.generate_gif(url, start_time, end_time)

            embed = discord.Embed(
                title="🎬 YouTube 影片轉 GIF 完成",
                description=f"**原影片網址**: {url}\n**片段區間**: `{start_time}` ~ `{end_time}` ({result.duration:.1f}s)",
                color=discord.Color.green(),
            )
            embed.set_footer(text=f"檔案大小: {result.file_size_bytes / (1024 * 1024):.2f} MB")

            file_attachment = discord.File(
                fp=io.BytesIO(result.gif_bytes),
                filename=result.file_name,
            )

            await interaction.followup.send(embed=embed, file=file_attachment)
            logger.info("Successfully sent GIF file to Discord channel for user %s", interaction.user.name)

        except ValueError as val_err:
            logger.warning("Validation error in /video command: %s", val_err)
            embed_err = discord.Embed(
                title="⚠️ 請求參數錯誤",
                description=str(val_err),
                color=discord.Color.orange(),
            )
            await interaction.followup.send(embed=embed_err)

        except Exception as exc:
            logger.error("Unexpected error in /video command: %s", exc, exc_info=True)
            embed_err = discord.Embed(
                title="❌ 處理失敗",
                description="轉換 GIF 過程中發生未預期的錯誤，請稍後再試或檢查網址是否正確。",
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed_err)


async def setup(bot: commands.Bot) -> None:
    """Extension setup entrypoint called by bot.load_extension.

    Args:
        bot: Discord bot instance.
    """
    await bot.add_cog(VideoClipperCog(bot))
