"""
mod/livestream/cog.py
=====================
Discord Cog providing the /play_video, /stream_stop, and /stream_status slash commands
with action choices (file / youtube), dynamic series autocomplete, and continuous episode playback.
"""

from __future__ import annotations

import logging
from typing import List, Optional

import discord  # type: ignore[import-not-found]
from discord import app_commands
from discord.ext import commands  # type: ignore[import-not-found]

from mod.livestream import config
from mod.livestream.contracts import ILivestreamService
from mod.livestream.domain import PlayAction, PlaybackStatus
from mod.livestream.web import (
    get_service,
    launch_livestream_server_task,
    livestream_bp,
)

logger = logging.getLogger(__name__)


class LivestreamCog(commands.Cog):  # type: ignore[misc]
    """Discord Cog for controlling Sherry's Livestream Display Screen."""

    def __init__(self, bot: commands.Bot, service: Optional[ILivestreamService] = None) -> None:
        """Initializes the LivestreamCog with bot instance and service.

        Args:
            bot: Discord bot instance.
            service: Optional custom ILivestreamService implementation.
        """
        self.bot: commands.Bot = bot
        self.service: ILivestreamService = service if service is not None else get_service()

    def _get_stream_ui_url(self) -> str:
        """Constructs the URL for accessing the livestream web UI on PORT 8764."""
        host = config.RADMIN_IP or config.HOST
        if host == "0.0.0.0":
            host = "127.0.0.1"
        port = config.PORT
        proto = "https" if config.HTTPS else "http"
        return f"{proto}://{host}:{port}/"

    async def video_name_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> List[app_commands.Choice[str]]:
        """Autocomplete handler that dynamically generates matching series from ./Video."""
        try:
            action_val = getattr(interaction.namespace, "action", "file")
            if action_val == "youtube":
                if not current:
                    return [
                        app_commands.Choice(
                            name="請貼上 YouTube 影片網址 (例如 https://www.youtube.com/watch?v=...)",
                            value="https://www.youtube.com/watch?v=",
                        )
                    ]
                return []

            available_series = self.service.get_available_series()
            query = current.strip().lower()
            matched = [
                s for s in available_series
                if query in s.lower()
            ] if query else available_series

            return [
                app_commands.Choice(name=s, value=s)
                for s in matched[:25]
            ]
        except Exception as exc:
            logger.warning("Error in video_name_autocomplete: %s", exc)
            return []

    @app_commands.command(  # type: ignore[misc]
        name="stream",
        description="🎬 雪莉直播螢幕控制中樞（播放本機影片/YouTube、停止播放、檢視狀態）",
    )
    @app_commands.describe(  # type: ignore[misc]
        action="操作類型 (play: 播放影片/YouTube, stop: 停止播放, status: 查看狀態與清單)",
        video_name="影片名稱 (本機影片自動補全，YouTube 則貼上網址)",
        episode="開始播放集數 (僅本機影片適用，預設第 1 集)",
    )
    @app_commands.choices(  # type: ignore[misc]
        action=[
            app_commands.Choice(name="▶️ 播放影片 (play)", value="play"),
            app_commands.Choice(name="⏹️ 停止播放 (stop)", value="stop"),
            app_commands.Choice(name="📊 檢視狀態與清單 (status)", value="status"),
        ]
    )
    @app_commands.autocomplete(video_name=video_name_autocomplete)  # type: ignore[misc]
    async def stream_command(
        self,
        interaction: discord.Interaction,
        action: Optional[app_commands.Choice[str]] = None,
        video_name: Optional[str] = None,
        episode: Optional[int] = 1,
    ) -> None:
        """Unified slash command handler for /stream."""
        if action is None:
            from mod.livestream.ui import StreamControlView
            view = StreamControlView(self)
            embed = view.build_dashboard_embed()
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            return

        act = action.value
        if act == "stop":
            await self._handle_stop(interaction)
        elif act == "status":
            await self._handle_status(interaction)
        else:
            if not video_name:
                await interaction.response.send_message("❌ 請提供影片名稱或 YouTube 網址！", ephemeral=True)
                return
            await self._handle_play(
                interaction=interaction,
                action=app_commands.Choice(name="play", value="file"),
                video_name=video_name,
                episode=episode,
            )

    async def _handle_play(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        video_name: str,
        episode: Optional[int] = 1,
    ) -> None:
        """Core play logic."""
        logger.info(
            "Received play video request from user %s (%s) [action=%s, video_name=%s, episode=%s]",
            interaction.user.name,
            interaction.user.id,
            action.value,
            video_name,
            episode,
        )

        await interaction.response.defer(thinking=True)

        requester_name = interaction.user.display_name or interaction.user.name
        requester_avatar = ""
        if interaction.user.display_avatar:
            requester_avatar = interaction.user.display_avatar.url

        ep = episode if episode is not None and episode > 0 else 1

        try:
            clean_input = video_name.strip()

            # Smart auto-detection: if input looks like a YouTube URL or ID, prioritize YouTube
            is_yt_link = (
                clean_input.startswith(("http://", "https://", "www.youtube.", "youtu.be", "youtube.com"))
                or "youtube.com" in clean_input.lower()
                or "youtu.be" in clean_input.lower()
            )

            available_series = self.service.get_available_series()
            is_local_series = clean_input in available_series or any(clean_input.lower() == s.lower() for s in available_series)

            if is_yt_link or (action.value == "youtube" and not is_local_series):
                new_state = await self.service.play_video(
                    action=PlayAction.YOUTUBE,
                    context=clean_input,
                    requester_name=requester_name,
                    requester_avatar=requester_avatar,
                )
                source_label = "YouTube 串流"
            else:
                new_state = await self.service.play_local_video(
                    series_name=clean_input,
                    episode=ep,
                    requester_name=requester_name,
                    requester_avatar=requester_avatar,
                )
                source_label = "本機影片庫 (連續自動輪播)"

            stream_url = self._get_stream_ui_url()
            current_media = new_state.current_media

            embed = discord.Embed(
                title="🎬 直播螢幕已切換播放",
                description=(
                    f"**播放來源**: `{source_label}`\n"
                    f"**影片名稱**: **{current_media.title if current_media else clean_input}**\n"
                    f"**點播探員**: {interaction.user.mention}\n\n"
                    f"📺 **直播螢幕畫面**: [點此開啟直播視窗 (Port 8764)]({stream_url})"
                ),
                color=discord.Color.teal(),
            )

            if current_media and current_media.action in (PlayAction.LOCAL, PlayAction.FILE):
                embed.add_field(
                    name="連播資訊",
                    value=f"目前播放第 `{current_media.episode_index}` 集 / 共 `{current_media.total_episodes}` 集（播畢自動順播下一集）",
                    inline=False,
                )
            embed.set_footer(text="提示：請用另一個 DC 帳號開啟直播螢幕視窗並進行螢幕分享。")

            await interaction.followup.send(embed=embed)
            logger.info("Successfully started playback for livestream by %s: %s", requester_name, clean_input)

        except ValueError as val_err:
            logger.warning("Validation error in livestream play: %s", val_err)
            embed_err = discord.Embed(
                title="⚠️ 播放參數錯誤",
                description=str(val_err),
                color=discord.Color.orange(),
            )
            await interaction.followup.send(embed=embed_err)

        except Exception as exc:
            logger.error("Unexpected error in livestream play: %s", exc, exc_info=True)
            embed_err = discord.Embed(
                title="❌ 播放失敗",
                description="播放處理過程中發生未預期的錯誤，請檢查名稱或稍後再試。",
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed_err)

    async def _handle_stop(self, interaction: discord.Interaction) -> None:
        """Core stop logic."""
        logger.info("Received livestream stop request from user %s", interaction.user.name)
        await interaction.response.defer(thinking=True)

        try:
            await self.service.stop()
            embed = discord.Embed(
                title="⏹️ 直播螢幕已停止",
                description="已停止播放並切換回雪莉待機畫面。",
                color=discord.Color.blurple(),
            )
            await interaction.followup.send(embed=embed)
        except Exception as exc:
            logger.error("Error in livestream stop: %s", exc, exc_info=True)
            await interaction.followup.send(content="❌ 停止直播畫面時發生錯誤。")

    async def _handle_status(self, interaction: discord.Interaction) -> None:
        """Core status logic."""
        state = self.service.get_current_state()
        stream_url = self._get_stream_ui_url()
        available_series = self.service.get_available_series()

        status_text = {
            PlaybackStatus.IDLE: "待機中 (Waiting)",
            PlaybackStatus.PLAYING: "播放中 (Playing)",
            PlaybackStatus.PAUSED: "暫停中 (Paused)",
            PlaybackStatus.STOPPED: "已停止 (Stopped)",
        }.get(state.status, state.status.value)

        embed = discord.Embed(
            title="📡 雪莉直播螢幕狀態",
            description=f"**當前狀態**: `{status_text}`\n**直播網址**: [開啟直播畫面 (Port 8764)]({stream_url})",
            color=discord.Color.gold() if state.status == PlaybackStatus.PLAYING else discord.Color.dark_grey(),
        )

        if state.current_media:
            embed.add_field(
                name="正在播放",
                value=(
                    f"**標題**: `{state.current_media.title}`\n"
                    f"**點播者**: `{state.current_media.requester_name}`"
                ),
                inline=False,
            )

        series_str = ", ".join(f"`{s}`" for s in available_series) if available_series else "（./Video 目前無影片）"
        embed.add_field(
            name=f"📁 本機可用影片庫 ({len(available_series)} 個系列)",
            value=series_str[:1000],
            inline=False,
        )

        embed.set_footer(text=f"專屬串流 UI 位址: {stream_url}")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(  # type: ignore[misc]
        name="play_video",
        description="播放本機影片或 YouTube 至直播螢幕 UI（/play_video [action] [video_name] [episode]）",
    )
    @app_commands.describe(  # type: ignore[misc]
        action="播放來源類型 (file: 本機影片庫, youtube: YouTube 串流)",
        video_name="影片名稱 (本機檔案將自動產生選單，YouTube 則輸入網址)",
        episode="開始播放集數 (數字，預設第 1 集，僅本機影片適用)",
    )
    @app_commands.choices(  # type: ignore[misc]
        action=[
            app_commands.Choice(name="本機影片 (file)", value="file"),
            app_commands.Choice(name="YouTube 串流 (youtube)", value="youtube"),
        ]
    )
    @app_commands.autocomplete(video_name=video_name_autocomplete)  # type: ignore[misc]
    async def play_video(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        video_name: str,
        episode: Optional[int] = 1,
    ) -> None:
        """Slash command handler for /play_video."""
        await self._handle_play(interaction, action, video_name, episode)

async def setup(bot: commands.Bot) -> None:
    """Extension setup entrypoint called by bot.load_extension.

    Args:
        bot: Discord bot instance.
    """
    # 1. Register Cog
    await bot.add_cog(LivestreamCog(bot))

    # 2. Launch dedicated Port 8764 Livestream Web Server in bot event loop
    try:
        launch_livestream_server_task()
    except Exception as exc:
        logger.error("Failed to launch livestream server on port 8764: %s", exc)

    # 3. Register Quart Blueprint to main web_ui app if available
    try:
        from web_ui.server import app as quart_app
        if "livestream" not in quart_app.blueprints:
            quart_app.register_blueprint(livestream_bp)
            logger.info("Successfully registered livestream Blueprint to main Web UI server.")
    except Exception as exc:
        logger.warning("Could not automatically register livestream Blueprint into Web UI: %s", exc)
