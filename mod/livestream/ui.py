"""mod/livestream/ui.py
======================
直播大螢幕模組 Discord 互動介面（主控面板、播片 Modal 表單）。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import discord
from discord import app_commands, ui

from mod.livestream.domain import PlaybackStatus

logger = logging.getLogger(__name__)


# ===========================================================================
# 1. 播片 Modal
# ===========================================================================

class StreamPlayModal(ui.Modal, title="🎬 直播大螢幕 — 點播影片 / 串流"):
    """彈出式直播影片播放表單。"""

    video_input: Any = ui.TextInput(
        label="本機系列名稱 或 YouTube 網址",
        placeholder="例如：海綿寶寶 或 https://youtu.be/...",
        min_length=1,
        max_length=300,
        required=True,
    )
    episode_input: Any = ui.TextInput(
        label="開始播放集數 (僅本機影片適用)",
        placeholder="1",
        default="1",
        min_length=1,
        max_length=5,
        required=False,
    )

    def __init__(self, cog: Any) -> None:
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """處理表單提交並委派播放。"""
        video_name = str(self.video_input.value).strip()
        ep_val = str(self.episode_input.value).strip()
        episode = int(ep_val) if ep_val.isdigit() else 1

        await self.cog._handle_play(
            interaction=interaction,
            action=app_commands.Choice(name="play", value="file"),
            video_name=video_name,
            episode=episode,
        )


# ===========================================================================
# 2. 直播大螢幕主控面板 View
# ===========================================================================

class StreamControlView(ui.View):
    """直播大螢幕控制面板 View。"""

    def __init__(self, cog: Any) -> None:
        super().__init__(timeout=180)
        self.cog = cog

    def build_dashboard_embed(self) -> discord.Embed:
        """建立直播螢幕狀態儀表板 Embed。"""
        state = self.cog.service.get_current_state()
        stream_url = self.cog._get_stream_ui_url()
        available_series = self.cog.service.get_available_series()

        status_text = {
            PlaybackStatus.IDLE: "待機中 (Waiting)",
            PlaybackStatus.PLAYING: "播放中 (Playing)",
            PlaybackStatus.PAUSED: "暫停中 (Paused)",
            PlaybackStatus.STOPPED: "已停止 (Stopped)",
        }.get(state.status, state.status.value)

        embed = discord.Embed(
            title="🎬 雪莉直播大螢幕控制中樞 (Port 8764)",
            description=(
                f"**當前播放狀態**: `{status_text}`\n"
                f"📺 **專屬串流 UI**: [點此開啟直播視窗]({stream_url})"
            ),
            color=discord.Color.teal() if state.status == PlaybackStatus.PLAYING else discord.Color.dark_grey(),
        )

        if state.current_media:
            embed.add_field(
                name="正在播放",
                value=(
                    f"**標題**: `{state.current_media.title}`\n"
                    f"**點播探員**: `{state.current_media.requester_name}`"
                ),
                inline=False,
            )

        series_str = ", ".join(f"`{s}`" for s in available_series) if available_series else "（./Video 目前無影片）"
        embed.add_field(
            name=f"📁 本機可用影片庫 ({len(available_series)} 個系列)",
            value=series_str[:1000],
            inline=False,
        )
        embed.set_footer(text="提示：使用 /stream <action> 亦可直接以指令快速操作。")
        return embed

    @ui.button(label="▶️ 播片/串流", style=discord.ButtonStyle.success, row=0)
    async def btn_play(self, interaction: discord.Interaction, button: ui.Button) -> None:
        """彈出播片表單。"""
        await interaction.response.send_modal(StreamPlayModal(self.cog))

    @ui.button(label="⏹️ 停止播放", style=discord.ButtonStyle.danger, row=0)
    async def btn_stop(self, interaction: discord.Interaction, button: ui.Button) -> None:
        """停止播放並切回待機畫面。"""
        await self.cog._handle_stop(interaction)

    @ui.button(label="🔄 重新整理", style=discord.ButtonStyle.secondary, row=0)
    async def btn_refresh(self, interaction: discord.Interaction, button: ui.Button) -> None:
        """重新整理狀態。"""
        embed = self.build_dashboard_embed()
        await interaction.response.edit_message(embed=embed, view=self)
