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
    start_time_input: Any = ui.TextInput(
        label="起始播放時間 (選填，例如 20:00 或 1:05:30)",
        placeholder="留空則從頭播放，支援 20:00、1:20:30 等",
        min_length=0,
        max_length=20,
        required=False,
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
        start_time_val = str(self.start_time_input.value).strip() if getattr(self, "start_time_input", None) else ""
        play_type = "insert" if "ins" in mode_val else ("add" if query.startswith(("http://", "https://", "www.")) else "search")

        cog: Any = getattr(interaction.client, "get_cog", lambda _: None)("MusicHandler")
        if not cog:
            await interaction.response.send_message("❌ 音樂模組尚未載入！", ephemeral=True)
            return

        if start_time_val and hasattr(cog, "parse_time_to_seconds"):
            parsed_sec = cog.parse_time_to_seconds(start_time_val)
            if parsed_sec is not None and parsed_sec >= 0:
                user_vc = (
                    interaction.user.voice.channel
                    if interaction.user and hasattr(interaction.user, "voice") and interaction.user.voice
                    else None
                )
                if not user_vc:
                    await interaction.response.send_message("❌ 你必須先加入一個語音頻道！", ephemeral=True)
                    return
                await interaction.response.defer()
                is_insert = (play_type == "insert")
                await cog.headless_play(interaction.channel, user_vc, query, insert_at_top=is_insert, start_offset=parsed_sec)
                start_dur = cog.format_duration(parsed_sec)
                await interaction.followup.send(f"🎵 已收到！正在從 **{start_dur}** 處起播：`{query}`！")
                return

        if hasattr(cog, "play_command"):
            await cog.play_command.callback(cog, interaction, play_type=play_type, context=query)
        else:
            await interaction.response.send_message("❌ 音樂模組尚未載入！", ephemeral=True)


# ===========================================================================
# 2. 時間軸指定跳轉 Modal
# ===========================================================================

class SeekTimeModal(ui.Modal, title="⏱️ 跳轉音樂時間軸"):
    """彈出式跳轉時間軸輸入表單。"""

    target_time_input: Any = ui.TextInput(
        label="目標時間 (支援 20:00, 1:20:00, +30s, -1m)",
        placeholder="例如：20:00、01:15:30、+30s 或 -15s",
        min_length=1,
        max_length=20,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """處理時間軸跳轉提交事件。"""
        val = str(self.target_time_input.value).strip()
        cog: Any = getattr(interaction.client, "get_cog", lambda _: None)("MusicHandler")
        if not cog or not interaction.guild_id:
            await interaction.response.send_message("❌ 音樂模組尚未載入！", ephemeral=True)
            return

        current = cog.current_song.get(interaction.guild_id)
        if not current:
            await interaction.response.send_message("❌ 目前沒有正在播放的音樂，無法跳轉！", ephemeral=True)
            return

        cur_elapsed = cog.get_elapsed_time(interaction.guild_id)
        target_sec = cog.parse_time_to_seconds(val, cur_elapsed)
        if target_sec is None:
            await interaction.response.send_message(
                f"❌ 無法識別時間格式 `{val}`，請輸入如 `20:00`、`1:05:30` 或 `+30s`！", ephemeral=True
            )
            return

        await interaction.response.defer()
        success = await cog.seek_to(interaction.guild, interaction.channel, target_sec)
        if success:
            dur_str = cog.format_duration(target_sec)
            await interaction.followup.send(f"⏱️ {interaction.user.mention} 已將時間軸跳轉至 **{dur_str}**！🎵")
        else:
            await interaction.followup.send("❌ 跳轉時間軸失敗，可能是音訊串流已過期或無法重新建立。", ephemeral=True)


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

    @discord.ui.button(label="⏪ -30s", style=discord.ButtonStyle.secondary, custom_id="seek_rewind_btn", row=2)
    async def seek_rewind_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """倒退 30 秒。"""
        cog: Any = getattr(interaction.client, "get_cog", lambda _: None)("MusicHandler")
        if not cog or not interaction.guild_id:
            await interaction.response.send_message("❌ 音樂模組未載入。", ephemeral=True)
            return
        current = cog.current_song.get(interaction.guild_id)
        if not current:
            await interaction.response.send_message("❌ 目前沒有音樂正在播放。", ephemeral=True)
            return
        cur_elapsed = cog.get_elapsed_time(interaction.guild_id)
        target_sec = max(0, cur_elapsed - 30)
        await interaction.response.defer()
        if await cog.seek_to(interaction.guild, interaction.channel, target_sec):
            await interaction.followup.send(
                f"⏪ {interaction.user.mention} 倒退了 30 秒 (至 {cog.format_duration(target_sec)})！",
                ephemeral=True,
            )
        else:
            await interaction.followup.send("❌ 跳轉失敗。", ephemeral=True)

    @discord.ui.button(label="⏱️ 指定時間", style=discord.ButtonStyle.primary, custom_id="seek_modal_btn", row=2)
    async def seek_modal_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """開啟指定時間跳轉對話框。"""
        await interaction.response.send_modal(SeekTimeModal())

    @discord.ui.button(label="⏩ +30s", style=discord.ButtonStyle.secondary, custom_id="seek_forward_btn", row=2)
    async def seek_forward_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """快轉 30 秒。"""
        cog: Any = getattr(interaction.client, "get_cog", lambda _: None)("MusicHandler")
        if not cog or not interaction.guild_id:
            await interaction.response.send_message("❌ 音樂模組未載入。", ephemeral=True)
            return
        current = cog.current_song.get(interaction.guild_id)
        if not current:
            await interaction.response.send_message("❌ 目前沒有音樂正在播放。", ephemeral=True)
            return
        cur_elapsed = cog.get_elapsed_time(interaction.guild_id)
        target_sec = cur_elapsed + 30
        await interaction.response.defer()
        if await cog.seek_to(interaction.guild, interaction.channel, target_sec):
            await interaction.followup.send(
                f"⏩ {interaction.user.mention} 快轉了 30 秒 (至 {cog.format_duration(target_sec)})！",
                ephemeral=True,
            )
        else:
            await interaction.followup.send("❌ 跳轉失敗。", ephemeral=True)

    @discord.ui.button(label="🔄 斷點續播", style=discord.ButtonStyle.success, custom_id="resume_checkpoint_btn", row=2)
    async def resume_checkpoint_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """從斷點恢復播放。"""
        cog: Any = getattr(interaction.client, "get_cog", lambda _: None)("MusicHandler")
        if not cog or not interaction.guild_id:
            await interaction.response.send_message("❌ 音樂模組未載入。", ephemeral=True)
            return
        user_vc = (
            interaction.user.voice.channel
            if interaction.user and hasattr(interaction.user, "voice") and interaction.user.voice
            else None
        )
        await interaction.response.defer()
        cp = await cog.resume_from_checkpoint(interaction.guild, interaction.channel, user_vc)
        if cp:
            song = cp.get("song", {})
            sec = cp.get("offset_seconds", 0)
            await interaction.followup.send(
                f"🔄 {interaction.user.mention} 接續播放了上次中斷的歌曲：**{song.get('title', '未知')}** (從 **{cog.format_duration(sec)}** 處開始)！🎵"
            )
        else:
            await interaction.followup.send("❌ 查無此伺服器可接續的音樂斷點紀錄（或語音頻道無人在線）。", ephemeral=True)


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