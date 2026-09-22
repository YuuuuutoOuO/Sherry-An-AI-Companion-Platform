"""handlers/music/ui.py
====================
音樂播放系統 Discord 互動 UI 介面（按鈕面板、分頁清單與點歌彈窗）。
"""

from __future__ import annotations

import logging
from typing import Any

import discord
from discord import ui

logger = logging.getLogger(__name__)


# ===========================================================================
# 1. 點歌 / 貼網址 Modal
# ===========================================================================

class SongInputModal(ui.Modal, title="🎵 點播音樂 / 貼上 YouTube 網址"):
    """彈出式點歌與 YouTube 網址輸入表單。"""

    query_input: Any = ui.TextInput(
        label="YouTube 網址 或 歌曲名稱關鍵字",
        placeholder="例如：https://youtu.be/... 或 Yoasobi Idol",
        min_length=1,
        max_length=300,
        required=True,
    )
    play_mode_input: Any = ui.TextInput(
        label="播放模式 (add: 加入佇列 / insert: 優先插播)",
        placeholder="add (預設加入隊尾) 或 insert (優先插播到最前)",
        default="add",
        min_length=3,
        max_length=10,
        required=False,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """處理點歌提交事件。"""
        query = str(self.query_input.value).strip()
        mode_val = str(self.play_mode_input.value).strip().lower()
        play_type = "insert" if "ins" in mode_val else ("add" if query.startswith(("http://", "https://", "www.")) else "search")

        cog: Any = getattr(interaction.client, "get_cog", lambda _: None)("MusicHandler")
        if cog and hasattr(cog, "play_command"):
            await cog.play_command.callback(cog, interaction, play_type=play_type, context=query)
        else:
            await interaction.response.send_message("❌ 音樂模組尚未載入！", ephemeral=True)


# ===========================================================================
# 2. 留聲機主控面板 View
# ===========================================================================

class MusicControlView(discord.ui.View):
    """音樂播放時的控制按鈕面板。

    包含點歌、暫停/播放、跳過歌曲、隨機打亂、循環切換、停止清空以及查看播放清單分頁面板。
    """

    def __init__(self) -> None:
        """初始化音樂播放控制介面。"""
        super().__init__(timeout=None)

    @discord.ui.button(label="➕ 點歌/貼網址", style=discord.ButtonStyle.success, custom_id="add_song_btn", row=0)
    async def add_song_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """彈出點歌對話框。"""
        await interaction.response.send_modal(SongInputModal())

    @discord.ui.button(label="⏯️ 暫停/播放", style=discord.ButtonStyle.secondary, custom_id="pause_resume_btn", row=0)
    async def pause_resume_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """切換音樂播放或暫停狀態。"""
        vc: Any = interaction.guild.voice_client if interaction.guild else None
        cog: Any = getattr(interaction.client, "get_cog", lambda _: None)("MusicHandler")
        if vc and vc.is_connected() and cog and interaction.guild_id:
            if vc.is_playing():
                vc.pause()
                cog.pause_playback(interaction.guild_id)
                embed = cog.build_playing_embed(interaction.guild_id)
                if interaction.message:
                    try:
                        await interaction.message.edit(embed=embed)
                    except Exception:
                        pass
                await interaction.response.send_message(
                    f"⏸️ {interaction.user.mention} 暫停了音樂！", ephemeral=True
                )
            elif vc.is_paused():
                vc.resume()
                cog.resume_playback(interaction.guild_id)
                embed = cog.build_playing_embed(interaction.guild_id)
                if interaction.message:
                    try:
                        await interaction.message.edit(embed=embed)
                    except Exception:
                        pass
                await interaction.response.send_message(
                    f"▶️ {interaction.user.mention} 繼續播放音樂！", ephemeral=True
                )
            else:
                await interaction.response.send_message("❌ 目前沒有音樂正在播放。", ephemeral=True)
        else:
            await interaction.response.send_message("❌ 機器人目前不在語音頻道內。", ephemeral=True)

    @discord.ui.button(label="⏭️ 跳過此曲", style=discord.ButtonStyle.blurple, custom_id="skip_song_btn", row=0)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """跳過當前播放歌曲。"""
        cog: Any = getattr(interaction.client, "get_cog", lambda _: None)("MusicHandler")
        if cog and interaction.guild_id:
            if cog.skip_song(interaction.guild_id):
                await interaction.response.send_message(
                    f"⏭️ {interaction.user.mention} 跳過了這首歌！", delete_after=5
                )
            else:
                await interaction.response.send_message("❌ 目前沒有音樂正在播放或機器人不在語音頻道內。", ephemeral=True)
        else:
            await interaction.response.send_message("❌ 音樂模組未載入。", ephemeral=True)

    @discord.ui.button(label="⏹️ 停止/清空", style=discord.ButtonStyle.danger, custom_id="stop_song_btn", row=0)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """停止播放並清空佇列。"""
        cog: Any = getattr(interaction.client, "get_cog", lambda _: None)("MusicHandler")
        if cog and interaction.guild_id:
            await cog.stop_player(interaction.guild_id, disconnect=True)
            await interaction.response.send_message(f"⏹️ {interaction.user.mention} 已停止播放並清空清單！", delete_after=5)
        else:
            await interaction.response.send_message("❌ 音樂模組未載入。", ephemeral=True)

    @discord.ui.button(label="🔀 隨機", style=discord.ButtonStyle.secondary, custom_id="shuffle_btn", row=1)
    async def shuffle_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """隨機打亂目前排隊中的播放清單。"""
        cog: Any = getattr(interaction.client, "get_cog", lambda _: None)("MusicHandler")
        if cog and interaction.guild_id:
            import random
            queue = cog.queues.get(interaction.guild_id, [])
            if len(queue) > 1:
                random.shuffle(queue)
                await interaction.response.send_message(
                    f"🔀 {interaction.user.mention} 已將播放清單隨機打亂！", delete_after=5
                )
            else:
                await interaction.response.send_message("❌ 播放清單中沒有足夠的歌曲可以打亂。", ephemeral=True)
        else:
            await interaction.response.send_message("❌ 音樂模組未載入。", ephemeral=True)

    @discord.ui.button(label="🔁 循環", style=discord.ButtonStyle.secondary, custom_id="loop_btn", row=1)
    async def loop_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """切換循環模式：關閉、清單循環或單曲循環。"""
        cog: Any = getattr(interaction.client, "get_cog", lambda _: None)("MusicHandler")
        if cog and interaction.guild_id:
            if not hasattr(cog, "loop_modes"):
                cog.loop_modes = {}
            current = cog.loop_modes.get(interaction.guild_id, "none")
            next_mode = {"none": "all", "all": "single", "single": "none"}[current]
            cog.loop_modes[interaction.guild_id] = next_mode
            mode_text = {"none": "關閉", "all": "清單循環", "single": "單曲循環"}[next_mode]
            embed = cog.build_playing_embed(interaction.guild_id)
            if interaction.message:
                try:
                    await interaction.message.edit(embed=embed)
                except Exception:
                    pass
            await interaction.response.send_message(
                f"🔁 {interaction.user.mention} 已將循環模式切換為：**{mode_text}**", delete_after=5
            )
        else:
            await interaction.response.send_message("❌ 音樂模組未載入。", ephemeral=True)

    @discord.ui.button(label="📋 播放清單", style=discord.ButtonStyle.secondary, custom_id="view_queue_btn", row=1)
    async def view_queue_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """顯示目前的音樂播放清單與分頁介面。"""
        cog: Any = getattr(interaction.client, "get_cog", lambda _: None)("MusicHandler")
        if cog and interaction.guild_id:
            queue = cog.queues.get(interaction.guild_id, [])
            current = cog.current_song.get(interaction.guild_id)
            if not queue and not current:
                await interaction.response.send_message("📭 目前沒有正在播放或排隊的音樂。", ephemeral=True)
                return
            total_pages = max(1, (len(queue) + 10 - 1) // 10)
            view = MusicQueueView(interaction.guild_id, page=0, per_page=10)
            view.update_button_states(total_pages)
            embed = cog.build_queue_embed(interaction.guild_id, page=0, per_page=10)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.send_message("❌ 音樂模組未載入。", ephemeral=True)


# ===========================================================================
# 3. 播放清單分頁檢視 View
# ===========================================================================

class MusicQueueView(discord.ui.View):
    """音樂播放清單的分頁與管理介面。"""

    def __init__(self, guild_id: int, page: int = 0, per_page: int = 10) -> None:
        """初始化播放清單分頁檢視介面。"""
        import settings
        super().__init__(timeout=getattr(settings, "MUSIC_QUEUE_VIEW_TIMEOUT", 180))
        self.guild_id = guild_id
        self.page = page
        self.per_page = per_page

    def update_button_states(self, total_pages: int) -> None:
        """依據頁碼與總頁數更新翻頁按鈕的啟用狀態。"""
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.custom_id == "queue_prev_btn":
                    child.disabled = (self.page <= 0)
                elif child.custom_id == "queue_next_btn":
                    child.disabled = (self.page >= total_pages - 1)

    @discord.ui.button(label="⬅️ 上一頁", style=discord.ButtonStyle.secondary, custom_id="queue_prev_btn")
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """切換至上一頁播放清單。"""
        cog: Any = getattr(interaction.client, "get_cog", lambda _: None)("MusicHandler")
        if not cog:
            await interaction.response.send_message("❌ 音樂模組未載入。", ephemeral=True)
            return
        queue = cog.queues.get(self.guild_id, [])
        total_pages = max(1, (len(queue) + self.per_page - 1) // self.per_page)
        self.page = max(0, self.page - 1)
        self.update_button_states(total_pages)
        embed = cog.build_queue_embed(self.guild_id, page=self.page, per_page=self.per_page)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="➡️ 下一頁", style=discord.ButtonStyle.secondary, custom_id="queue_next_btn")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """切換至下一頁播放清單。"""
        cog: Any = getattr(interaction.client, "get_cog", lambda _: None)("MusicHandler")
        if not cog:
            await interaction.response.send_message("❌ 音樂模組未載入。", ephemeral=True)
            return
        queue = cog.queues.get(self.guild_id, [])
        total_pages = max(1, (len(queue) + self.per_page - 1) // self.per_page)
        self.page = min(total_pages - 1, self.page + 1)
        self.update_button_states(total_pages)
        embed = cog.build_queue_embed(self.guild_id, page=self.page, per_page=self.per_page)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="🔄 重新整理", style=discord.ButtonStyle.primary, custom_id="queue_refresh_btn")
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """重新整理播放清單與當前歌曲時間軸。"""
        cog: Any = getattr(interaction.client, "get_cog", lambda _: None)("MusicHandler")
        if not cog:
            await interaction.response.send_message("❌ 音樂模組未載入。", ephemeral=True)
            return
        queue = cog.queues.get(self.guild_id, [])
        total_pages = max(1, (len(queue) + self.per_page - 1) // self.per_page)
        self.page = min(max(0, self.page), total_pages - 1)
        self.update_button_states(total_pages)
        embed = cog.build_queue_embed(self.guild_id, page=self.page, per_page=self.per_page)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        """當互動逾時過期，將所有按鈕設為停用。"""
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        try:
            if hasattr(self, "message") and self.message:
                await self.message.edit(view=self)
        except Exception:
            pass