import discord


class MusicControlView(discord.ui.View):
    """音樂播放時的控制按鈕面板。

    包含暫停/播放、跳過歌曲、隨機打亂、循環切換以及查看播放清單分頁面板。
    """

    def __init__(self) -> None:
        """初始化音樂播放控制介面。"""
        super().__init__(timeout=None)

    @discord.ui.button(label="⏯️ 暫停/播放", style=discord.ButtonStyle.secondary, custom_id="pause_resume_btn")
    async def pause_resume_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """切換音樂播放或暫停狀態。"""
        vc = interaction.guild.voice_client if interaction.guild else None
        cog = interaction.client.get_cog("MusicHandler")
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

    @discord.ui.button(label="⏭️ 跳過此曲", style=discord.ButtonStyle.blurple, custom_id="skip_song_btn")
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """跳過當前播放歌曲。"""
        vc = interaction.guild.voice_client if interaction.guild else None
        if vc and vc.is_connected():
            if vc.is_playing() or vc.is_paused():
                vc.stop()  # 觸發 FFmpeg 的 after 行為切下一首
                await interaction.response.send_message(
                    f"⏭️ {interaction.user.mention} 跳過了這首歌！", delete_after=5
                )
            else:
                await interaction.response.send_message("❌ 目前沒有音樂正在播放。", ephemeral=True)
        else:
            await interaction.response.send_message("❌ 機器人目前不在語音頻道內。", ephemeral=True)

    @discord.ui.button(label="🔀 隨機", style=discord.ButtonStyle.secondary, custom_id="shuffle_btn")
    async def shuffle_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """隨機打亂目前排隊中的播放清單。"""
        cog = interaction.client.get_cog("MusicHandler")
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

    @discord.ui.button(label="🔁 循環", style=discord.ButtonStyle.secondary, custom_id="loop_btn")
    async def loop_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """切換循環模式：關閉、清單循環或單曲循環。"""
        cog = interaction.client.get_cog("MusicHandler")
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

    @discord.ui.button(label="📋 播放清單", style=discord.ButtonStyle.secondary, custom_id="view_queue_btn")
    async def view_queue_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """顯示目前的音樂播放清單與分頁介面。"""
        cog = interaction.client.get_cog("MusicHandler")
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


class MusicQueueView(discord.ui.View):
    """音樂播放清單的分頁與管理介面。

    Attributes:
        guild_id: Discord 伺服器 ID。
        page: 當前顯示的頁碼 (0-indexed)。
        per_page: 每頁顯示的歌曲筆數。
    """

    def __init__(self, guild_id: int, page: int = 0, per_page: int = 10) -> None:
        """初始化播放清單分頁檢視介面。

        Args:
            guild_id: Discord 伺服器 ID。
            page: 初始頁碼 (預設為 0)。
        """
        import settings
        super().__init__(timeout=getattr(settings, "MUSIC_QUEUE_VIEW_TIMEOUT", 180))
        self.guild_id = guild_id
        self.page = page
        self.per_page = per_page

    def update_button_states(self, total_pages: int) -> None:
        """依據頁碼與總頁數更新翻頁按鈕的啟用狀態。

        Args:
            total_pages: 播放清單的總頁數。
        """
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.custom_id == "queue_prev_btn":
                    child.disabled = (self.page <= 0)
                elif child.custom_id == "queue_next_btn":
                    child.disabled = (self.page >= total_pages - 1)

    @discord.ui.button(label="⬅️ 上一頁", style=discord.ButtonStyle.secondary, custom_id="queue_prev_btn")
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """切換至上一頁播放清單。"""
        cog = interaction.client.get_cog("MusicHandler")
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
        cog = interaction.client.get_cog("MusicHandler")
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
        cog = interaction.client.get_cog("MusicHandler")
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