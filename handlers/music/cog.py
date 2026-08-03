import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import re
import time
from typing import Any

# 匯入分離出來的模塊
from handlers.music.config import FFMPEG_EXE_PATH, FFMPEG_OPTIONS, ytdl_client
from handlers.music.ui import MusicControlView, MusicQueueView

import logging
logger = logging.getLogger(__name__)


class MusicHandler(commands.Cog):
    """音樂處理器，負責語音頻道連接、佇列管理、播放與時間軸控制。"""

    def __init__(self, bot: commands.Bot) -> None:
        """初始化音樂處理器模組。

        Args:
            bot: Discord Bot 實例。
        """
        self.bot = bot
        self.queues: dict[int, list[dict[str, Any]]] = {}
        self.current_song: dict[int, dict[str, Any] | None] = {}
        self.loop_modes: dict[int, str] = {}  # "none" | "all" | "single"
        self.current_message: dict[int, discord.Message] = {}
        self.playback_start_time: dict[int, float] = {}
        self.playback_accumulated_time: dict[int, float] = {}
        self.is_paused: dict[int, bool] = {}
        self.progress_tasks: dict[int, asyncio.Task[Any]] = {}

    # ──────────────────────────────────────────────────────────
    # 留言移除與時間軸工具方法
    # ──────────────────────────────────────────────────────────

    async def _delete_song_message(self, guild_id: int) -> None:
        """刪除指定伺服器當前正在播放歌曲的留聲訊息。

        Args:
            guild_id: Discord 伺服器 ID。
        """
        msg = self.current_message.pop(guild_id, None)
        if msg:
            try:
                await msg.delete()
            except Exception as e:
                logger.debug(f"刪除過期歌曲留言失敗: {e}")

    def format_duration(self, seconds: Any) -> str:
        """將秒數格式化為 mm:ss 或 hh:mm:ss 字串。

        Args:
            seconds: 總秒數（可為 int、float 或數字字串）。

        Returns:
            str: 格式化後的時間字串。
        """
        try:
            total_secs = int(float(seconds))
        except (TypeError, ValueError):
            return "00:00"
        if total_secs < 0:
            return "00:00"
        hours = total_secs // 3600
        minutes = (total_secs % 3600) // 60
        secs = total_secs % 60
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    def format_time_axis(self, elapsed_seconds: Any, total_seconds: Any, bar_length: int = 15) -> str:
        """產生聲音進度時間軸字串（含進度條圖示與時間）。

        Args:
            elapsed_seconds: 已播放秒數。
            total_seconds: 歌曲總秒數。
            bar_length: 進度條長度。

        Returns:
            str: 格式化後的時間軸。
        """
        try:
            e_sec = float(elapsed_seconds)
        except (TypeError, ValueError):
            e_sec = 0.0
        try:
            t_sec = float(total_seconds)
        except (TypeError, ValueError):
            t_sec = 0.0

        if t_sec > 0:
            ratio = min(max(e_sec / t_sec, 0.0), 1.0)
            pos = int(ratio * (bar_length - 1))
            bar = "─" * pos + "🔘" + "─" * (bar_length - 1 - pos)
            return f"`{bar}` `[{self.format_duration(e_sec)} / {self.format_duration(t_sec)}]`"
        return f"`🔘───────────────` `[{self.format_duration(e_sec)} / LIVE]`"

    def get_elapsed_time(self, guild_id: int) -> int:
        """取得當前歌曲的已播放時間（秒）。

        Args:
            guild_id: Discord 伺服器 ID。

        Returns:
            int: 已播放秒數。
        """
        if guild_id not in self.playback_start_time:
            return 0
        total = self.playback_accumulated_time.get(guild_id, 0.0)
        if not self.is_paused.get(guild_id, False):
            total += max(0.0, time.time() - self.playback_start_time.get(guild_id, time.time()))
        current = self.current_song.get(guild_id)
        try:
            duration = float(current.get("duration", 0) or 0) if current else 0.0
        except (TypeError, ValueError):
            duration = 0.0
        if duration > 0:
            return int(min(total, duration))
        return int(total)

    def pause_playback(self, guild_id: int) -> None:
        """記錄暫停播放狀態與已播放累積時間。

        Args:
            guild_id: Discord 伺服器 ID。
        """
        if not self.is_paused.get(guild_id, False):
            elapsed_segment = time.time() - self.playback_start_time.get(guild_id, time.time())
            self.playback_accumulated_time[guild_id] = (
                self.playback_accumulated_time.get(guild_id, 0.0) + max(0.0, elapsed_segment)
            )
            self.is_paused[guild_id] = True

    def resume_playback(self, guild_id: int) -> None:
        """記錄繼續播放狀態並重置段落計時。

        Args:
            guild_id: Discord 伺服器 ID。
        """
        if self.is_paused.get(guild_id, True):
            self.playback_start_time[guild_id] = time.time()
            self.is_paused[guild_id] = False

    def shorten_url(self, url: str) -> str:
        """縮短 YouTube 等播放網址以避免 Embed 字元超過上限。

        Args:
            url: 原始影片網址。

        Returns:
            str: 縮短後的網址。
        """
        if not url:
            return ""
        m = re.search(r'(?:v=|\/)([0-9A-Za-z_-]{11})(?:[&?]|$)', url)
        if m and ("youtube.com" in url or "youtu.be" in url):
            return f"https://youtu.be/{m.group(1)}"
        if len(url) > 60:
            return url[:60]
        return url

    def build_playing_embed(self, guild_id: int) -> discord.Embed:
        """構建正在播放歌曲的 Embed 介面與時間軸。

        Args:
            guild_id: Discord 伺服器 ID。

        Returns:
            discord.Embed: 正在播放的音樂 Embed。
        """
        song = self.current_song.get(guild_id)
        if not song:
            return discord.Embed(title="🎶 音樂播放", description="目前無播放項目", color=discord.Color.brand_green())

        elapsed = self.get_elapsed_time(guild_id)
        duration = song.get("duration", 0) or 0
        time_axis = self.format_time_axis(elapsed, duration)
        cur_url = self.shorten_url(song.get("webpage_url", ""))

        embed = discord.Embed(
            title="▶️ 正在播放音樂",
            description=f"**[{song['title']}]({cur_url})**\n\n{time_axis}",
            color=discord.Color.brand_green(),
        )
        if song.get("thumbnail"):
            embed.set_thumbnail(url=song["thumbnail"])

        queue = self.queues.get(guild_id, [])
        loop_mode = self.loop_modes.get(guild_id, "none")
        mode_text = {"none": "🚫 關閉", "all": "🔁 清單循環", "single": "🔂 單曲循環"}[loop_mode]
        embed.set_footer(text=f"佇列歌曲數：{len(queue)} 首 | 循環模式：{mode_text}")
        return embed

    def build_queue_embed(self, guild_id: int, page: int = 0, per_page: int = 10) -> discord.Embed:
        """構建音樂播放清單的分頁 Embed，並附帶當前播放歌曲與時間軸。

        Args:
            guild_id: Discord 伺服器 ID。
            page: 欲顯示的頁碼 (0-indexed)。
            per_page: 每頁顯示幾筆歌曲。

        Returns:
            discord.Embed: 音樂清單分頁 Embed。
        """
        queue = self.queues.get(guild_id, [])
        current = self.current_song.get(guild_id)
        total_pages = max(1, (len(queue) + per_page - 1) // per_page)
        page = min(max(0, page), total_pages - 1)

        embed = discord.Embed(title="🎶 當前播放清單", color=discord.Color.brand_green())

        desc_parts = []
        if current:
            elapsed = self.get_elapsed_time(guild_id)
            duration = current.get("duration", 0) or 0
            time_axis = self.format_time_axis(elapsed, duration)
            cur_url = self.shorten_url(current.get("webpage_url", ""))
            desc_parts.append(f"### ▶️ 正在播放\n**[{current['title']}]({cur_url})**\n{time_axis}")

        desc_parts.append(f"### ⏳ 排隊中 (共 {len(queue)} 首 — 第 {page + 1}/{total_pages} 頁)")
        if queue:
            start_idx = page * per_page
            end_idx = min(len(queue), start_idx + per_page)
            page_songs = queue[start_idx:end_idx]
            q_lines = []
            for i, song in enumerate(page_songs, start=start_idx + 1):
                duration_str = self.format_duration(song.get("duration", 0) or 0)
                title = song["title"]
                if len(title) > 40:
                    title = title[:37] + "..."
                s_url = self.shorten_url(song.get("webpage_url", ""))
                line = f"**{i}.** [{title}]({s_url}) `[{duration_str}]`"
                if sum(len(x) for x in desc_parts) + sum(len(x) for x in q_lines) + len(line) > 3800:
                    q_lines.append("*(...字數已達上限，部分歌曲省略顯示)*")
                    break
                q_lines.append(line)
            desc_parts.append("\n".join(q_lines))
        else:
            desc_parts.append("目前佇列中沒有歌曲")

        embed.description = "\n\n".join(desc_parts)

        loop_mode = self.loop_modes.get(guild_id, "none")
        mode_text = {"none": "🚫 關閉", "all": "🔁 清單循環", "single": "🔂 單曲循環"}[loop_mode]
        embed.set_footer(text=f"循環模式：{mode_text} | 頁碼 {page + 1}/{total_pages}")
        return embed

    async def send_queue_embed(self, channel: discord.abc.Messageable, guild_id: int) -> None:
        """發送包含音樂清單與分頁按鈕的 Embed 訊息。

        Args:
            channel: 發送訊息的 Discord 頻道。
            guild_id: Discord 伺服器 ID。
        """
        queue = self.queues.get(guild_id, [])
        current = self.current_song.get(guild_id)
        if not queue and not current:
            await channel.send("📭 目前沒有正在播放或排隊的音樂。")
            return
        total_pages = max(1, (len(queue) + 10 - 1) // 10)
        view = MusicQueueView(guild_id, page=0, per_page=10)
        view.update_button_states(total_pages)
        embed = self.build_queue_embed(guild_id, page=0, per_page=10)
        await channel.send(embed=embed, view=view)

    def _start_progress_updater(self, guild_id: int) -> None:
        """啟動定時更新音樂時間軸的後台工作。

        Args:
            guild_id: Discord 伺服器 ID。
        """
        self._stop_progress_updater(guild_id)
        task = self.bot.loop.create_task(self._progress_updater_loop(guild_id))
        self.progress_tasks[guild_id] = task

    def _stop_progress_updater(self, guild_id: int) -> None:
        """停止並取消指定伺服器的時間軸更新 Task。

        Args:
            guild_id: Discord 伺服器 ID。
        """
        task = self.progress_tasks.pop(guild_id, None)
        if task and not task.done():
            task.cancel()

    async def _progress_updater_loop(self, guild_id: int) -> None:
        """定時更新當前播放歌曲的 Embed 時間軸。

        Args:
            guild_id: Discord 伺服器 ID。
        """
        try:
            while True:
                await asyncio.sleep(10)
                msg = self.current_message.get(guild_id)
                song = self.current_song.get(guild_id)
                guild = self.bot.get_guild(guild_id)
                vc = guild.voice_client if guild else None
                if not msg or not song or not vc or not vc.is_connected():
                    break
                if vc.is_playing() or vc.is_paused():
                    try:
                        embed = self.build_playing_embed(guild_id)
                        await msg.edit(embed=embed)
                    except discord.NotFound:
                        self.current_message.pop(guild_id, None)
                        break
                    except Exception as e:
                        logger.debug(f"更新時間軸 Embed 失敗: {e}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"時間軸更新工作異常中斷: {e}")

    # ──────────────────────────────────────────────────────────
    # 公開工具方法（供 music_tool.py 呼叫）
    # ──────────────────────────────────────────────────────────

    def jump_to(self, guild_id: int, index: int) -> dict[str, Any] | None:
        """把第 index 首（1-based）移到佇列最前面，並回傳該歌曲資訊。

        由呼叫方負責 vc.stop() 觸發 after_playback → _play_next。

        Args:
            guild_id: Discord 伺服器 ID。
            index: 歌曲位置（從 1 開始）。

        Returns:
            dict[str, Any] | None: 指定之歌曲字典，若無則為 None。
        """
        queue = self.queues.get(guild_id, [])
        if 1 <= index <= len(queue):
            song = queue.pop(index - 1)
            queue.insert(0, song)
            return song
        return None

    def clear_queue(self, guild_id: int) -> int:
        """清空伺服器的播放清單，並回傳清除的歌曲數量。

        Args:
            guild_id: Discord 伺服器 ID。

        Returns:
            int: 已被清除的歌曲數。
        """
        queue = self.queues.get(guild_id, [])
        count = len(queue)
        self.queues[guild_id] = []
        return count

    # ──────────────────────────────────────────────────────────
    # 核心播放引擎
    # ──────────────────────────────────────────────────────────

    async def headless_play(
        self,
        text_channel: discord.TextChannel,
        voice_channel: discord.VoiceChannel,
        query: str,
        insert_at_top: bool = False,
    ) -> None:
        """無頭模式載入音樂至佇列，並於需要時觸發播放。

        Args:
            text_channel: 文字回覆頻道。
            voice_channel: 機器人應加入的語音頻道。
            query: YouTube 網址或關鍵字。
            insert_at_top: 是否優先插播在佇列首位。
        """
        guild = text_channel.guild
        guild_id = guild.id

        vc = guild.voice_client
        try:
            if vc is None:
                vc = await voice_channel.connect()
            elif vc.channel != voice_channel:
                await vc.move_to(voice_channel)
        except Exception as e:
            await text_channel.send(f"❌ 橘雪莉的麥克風壞掉了 (連線失敗)：\n`{str(e)}`")
            return

        if guild_id not in self.queues:
            self.queues[guild_id] = []

        youtube_regex = r'^(https?\:\/\/)?(www\.youtube\.com|youtu\.?be)\/.+$'
        search_query = query if re.match(youtube_regex, query.strip()) else f"ytsearch:{query}"

        try:
            loop = asyncio.get_running_loop()
            data = await loop.run_in_executor(None, lambda: ytdl_client.extract_info(search_query, download=False))

            if 'entries' in data:
                entries = [e for e in data['entries'] if e]
                added_items = []
                for entry in entries:
                    url = entry.get('webpage_url', entry.get('url'))
                    if url and not url.startswith('http'):
                        url = f"https://www.youtube.com/watch?v={url}"
                    added_items.append({
                        'title': entry.get('title', '未知曲目'),
                        'webpage_url': url,
                        'duration': entry.get('duration', 0) or 0,
                        'thumbnail': entry.get('thumbnail'),
                    })
                if insert_at_top:
                    self.queues[guild_id] = added_items + self.queues[guild_id]
                else:
                    self.queues[guild_id].extend(added_items)
            else:
                song_item = {
                    'title': data.get('title', '未知曲目'),
                    'webpage_url': data.get('webpage_url', data.get('url')),
                    'duration': data.get('duration', 0) or 0,
                    'thumbnail': data.get('thumbnail'),
                }
                if insert_at_top:
                    self.queues[guild_id].insert(0, song_item)
                else:
                    self.queues[guild_id].append(song_item)

        except Exception as e:
            await text_channel.send(f"⚠️ 找不到這首歌耶，是不是被消音了？ (`{str(e)}`)")
            return

        if not vc.is_playing() and not vc.is_paused():
            self._play_next(guild, text_channel)

    def _play_next(self, guild: discord.Guild, text_channel: discord.TextChannel) -> None:
        """背景播放下一首歌曲引擎。

        Args:
            guild: Discord 伺服器物件。
            text_channel: 文字頻道。
        """
        guild_id = guild.id
        vc = guild.voice_client

        if vc is None or not vc.is_connected():
            self._stop_progress_updater(guild_id)
            self.current_song[guild_id] = None
            return

        if guild_id in self.queues and len(self.queues[guild_id]) > 0:
            song = self.queues[guild_id].pop(0)
            self.current_song[guild_id] = song

            async def background_fetch_and_play() -> None:
                try:
                    loop = asyncio.get_running_loop()
                    fresh_song = await loop.run_in_executor(
                        None, lambda: ytdl_client.extract_info(song['webpage_url'], download=False)
                    )

                    if not fresh_song:
                        raise ValueError("yt-dlp 無法解析影片，可能缺少 JS 執行環境或影片受限")

                    true_audio_stream = fresh_song.get('url')
                    if not true_audio_stream:
                        raise ValueError("成功解析但找不到音頻串流 URL")

                    song['duration'] = fresh_song.get('duration', 0) or song.get('duration', 0) or 0
                    song['thumbnail'] = fresh_song.get('thumbnail') or song.get('thumbnail')

                    source = discord.FFmpegPCMAudio(
                        true_audio_stream,
                        executable=FFMPEG_EXE_PATH,
                        **FFMPEG_OPTIONS
                    )

                    def after_playback(error: Exception | None = None) -> None:
                        if error:
                            logger.error(f"播放中斷錯誤: {error}")

                        self._stop_progress_updater(guild_id)
                        asyncio.run_coroutine_threadsafe(self._delete_song_message(guild_id), self.bot.loop)

                        loop_mode = self.loop_modes.get(guild_id, "none")
                        if loop_mode == "single" and song:
                            self.queues[guild_id].insert(0, song)
                        elif loop_mode == "all" and song:
                            self.queues[guild_id].append(song)

                        self.current_song[guild_id] = None
                        self.bot.loop.call_soon_threadsafe(self._play_next, guild, text_channel)

                    self.playback_start_time[guild_id] = time.time()
                    self.playback_accumulated_time[guild_id] = 0.0
                    self.is_paused[guild_id] = False

                    vc.play(source, after=after_playback)

                    await self._delete_song_message(guild_id)
                    view = MusicControlView()
                    embed = self.build_playing_embed(guild_id)
                    msg = await text_channel.send(embed=embed, view=view)
                    self.current_message[guild_id] = msg
                    self._start_progress_updater(guild_id)

                except Exception as e:
                    logger.error(f"背景提取歌曲失敗，切換下一首: {e}")
                    await text_channel.send(
                        f"⚠️ 名偵探發聲警告！目標似乎上了反魔陣，無法發聲放出來！自動跳接下一號...\n"
                        f"*(技術錯失回執報:{str(e)[:50]})*"
                    )
                    self._stop_progress_updater(guild_id)
                    self.current_song[guild_id] = None
                    self.bot.loop.call_soon_threadsafe(self._play_next, guild, text_channel)

            asyncio.run_coroutine_threadsafe(background_fetch_and_play(), self.bot.loop)
        else:
            self._stop_progress_updater(guild_id)
            self.current_song[guild_id] = None

    # ──────────────────────────────────────────────────────────
    # 單一 /play 指令（合併所有操作為下拉選單）
    # ──────────────────────────────────────────────────────────

    @app_commands.command(name="play", description="🎵 音樂播放系統 — 選擇操作類型後填入對應內容")
    @app_commands.describe(
        play_type="選擇操作類型",
        context="補充內容（YouTube 網址 / 歌名 / 編號），部分操作不需填寫"
    )
    @app_commands.choices(play_type=[
        app_commands.Choice(name="▶️  add    — 新增 YouTube 網址到佇列", value="add"),
        app_commands.Choice(name="🔍  search — 搜尋歌名並播放",           value="search"),
        app_commands.Choice(name="📌  insert — 優先插播（搜尋或網址排到最前）", value="insert"),
        app_commands.Choice(name="📋  list   — 查看目前播放清單",          value="list"),
        app_commands.Choice(name="⏭️  skip   — 跳過當前歌曲",             value="skip"),
        app_commands.Choice(name="🎯  jump   — 優先播放指定編號的歌",      value="jump"),
        app_commands.Choice(name="🗑️  remove — 刪除指定編號的歌",         value="remove"),
        app_commands.Choice(name="🧹  clear  — 清空整個播放清單",            value="clear"),
    ])
    async def play_command(
        self,
        interaction: discord.Interaction,
        play_type: str,
        context: str = ""
    ) -> None:
        """處理 /play 斜線指令的分支操作。

        Args:
            interaction: Discord 互動事件。
            play_type: 操作類型 (add, search, list, skip, jump, remove)。
            context: 相關參數 (網址、關鍵字或歌曲編號)。
        """
        guild_id = interaction.guild_id
        if guild_id is None:
            return
        queue    = self.queues.get(guild_id, [])
        current  = self.current_song.get(guild_id)
        vc       = interaction.guild.voice_client if interaction.guild else None
        user_vc  = (
            interaction.user.voice.channel
            if interaction.user and hasattr(interaction.user, "voice") and interaction.user.voice
            else None
        )

        # ── add ────────────────────────────────────────────────
        if play_type == "add":
            await interaction.response.defer()
            youtube_regex = r'^(https?\:\/\/)?(www\.youtube\.com|youtu\.?be)\/.+$'
            if not context.strip():
                await interaction.followup.send("❌ 請在 `context` 填入 YouTube 網址！", ephemeral=True)
                return
            if not re.match(youtube_regex, context.strip()):
                await interaction.followup.send("❌ 格式錯誤！請提供有效的 YouTube 影片或播放清單網址。", ephemeral=True)
                return
            if not user_vc:
                await interaction.followup.send("❌ 你必須先加入一個語音頻道！", ephemeral=True)
                return
            await self.headless_play(interaction.channel, user_vc, context.strip())  # type: ignore[arg-type]
            await interaction.followup.send(f"✅ 已收到！正在把 `{context.strip()}` 載入佇列...")

        # ── search ─────────────────────────────────────────────
        elif play_type == "search":
            await interaction.response.defer()
            if not context.strip():
                await interaction.followup.send("❌ 請在 `context` 填入想搜尋的歌名或關鍵字！", ephemeral=True)
                return
            if not user_vc:
                await interaction.followup.send("❌ 你必須先加入一個語音頻道！", ephemeral=True)
                return
            await self.headless_play(interaction.channel, user_vc, context.strip())  # type: ignore[arg-type]
            await interaction.followup.send(f"🔍 正在搜尋並加入佇列：**{context.strip()}**...")

        # ── insert ─────────────────────────────────────────────
        elif play_type == "insert":
            await interaction.response.defer()
            if not context.strip():
                await interaction.followup.send("❌ 請在 `context` 填入想優先插播的歌名、關鍵字或網址！", ephemeral=True)
                return
            if not user_vc:
                await interaction.followup.send("❌ 你必須先加入一個語音頻道！", ephemeral=True)
                return
            await self.headless_play(interaction.channel, user_vc, context.strip(), insert_at_top=True)  # type: ignore[arg-type]
            await interaction.followup.send(f"📌 已優先插播至清單最前：**{context.strip()}**！")

        # ── list ────────────────────────────────────────────────
        elif play_type == "list":
            if not queue and not current:
                await interaction.response.send_message("📭 目前沒有正在播放或排隊的音樂。", ephemeral=True)
                return
            total_pages = max(1, (len(queue) + 10 - 1) // 10)
            view = MusicQueueView(guild_id, page=0, per_page=10)
            view.update_button_states(total_pages)
            embed = self.build_queue_embed(guild_id, page=0, per_page=10)
            await interaction.response.send_message(embed=embed, view=view)

        # ── skip ────────────────────────────────────────────────
        elif play_type == "skip":
            if vc and (vc.is_playing() or vc.is_paused()):
                vc.stop()
                await interaction.response.send_message("⏭️ 已跳過當前歌曲！", delete_after=5)
            else:
                await interaction.response.send_message("❌ 目前沒有歌曲正在播放。", ephemeral=True)

        # ── jump ────────────────────────────────────────────────
        elif play_type == "jump":
            m = re.search(r'\d+', context)
            if not m:
                await interaction.response.send_message("❌ 請在 `context` 填入要跳至的歌曲編號（例如：`6`）！", ephemeral=True)
                return
            idx = int(m.group())
            if not queue:
                await interaction.response.send_message("❌ 佇列是空的，沒有歌曲可以跳轉！", ephemeral=True)
                return
            song = self.jump_to(guild_id, idx)
            if not song:
                await interaction.response.send_message(
                    f"❌ 找不到第 `{idx}` 首！佇列目前共 {len(queue)} 首，請確認編號。", ephemeral=True
                )
                return
            if vc and (vc.is_playing() or vc.is_paused()):
                vc.stop()  # 觸發 after_playback → _play_next → 立刻播 jump 過去的歌
            await interaction.response.send_message(f"🎯 跳至第 **{idx}** 首：**{song['title']}**！", delete_after=5)

        # ── remove ──────────────────────────────────────────────
        elif play_type == "remove":
            if not queue:
                await interaction.response.send_message("❌ 佇列是空的，沒有歌曲可以刪除！", ephemeral=True)
                return
            if not context.strip():
                await interaction.response.send_message(
                    "❌ 請在 `context` 填入要刪除的編號，支援：`3`、`1,3,5`、`1~10` 或 `all`", ephemeral=True
                )
                return

            if context.strip().lower() in ("all", "全部", "0"):
                count = len(queue)
                self.queues[guild_id] = []
                embed = discord.Embed(
                    title="🧹 播放清單已清空",
                    description=f"已刪除佇列中的 {count} 首歌曲。",
                    color=discord.Color.red()
                )
                await interaction.response.send_message(embed=embed)
                return

            # 解析範圍語法
            combined = context
            result_set: set[int] = set()
            for rm in re.finditer(r'(\d+)\s*[~\-到至]\s*(\d+)', combined):
                s, e = int(rm.group(1)), int(rm.group(2))
                if s > e: s, e = e, s
                result_set.update(range(s, min(e, 9999) + 1))
            for n in re.findall(r'\d+', combined):
                result_set.add(int(n))

            indexes_to_purge = sorted(result_set, reverse=True)
            removed = []
            for i in indexes_to_purge:
                if 1 <= i <= len(queue):
                    target = queue.pop(i - 1)
                    removed.append(target['title'])

            if not removed:
                await interaction.response.send_message(
                    f"❌ 沒有找到對應的編號（佇列共 {len(queue)} 首），請確認後再試。", ephemeral=True
                )
                return

            removed.reverse()
            removed_text = "\n".join(f"❌ {t}" for t in removed[:15])
            if len(removed) > 15:
                removed_text += f"\n...以及另外 {len(removed) - 15} 首"
            embed = discord.Embed(
                title=f"🗑️ 已刪除 {len(removed)} 首歌曲",
                description=removed_text,
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)

        # ── clear ──────────────────────────────────────────────
        elif play_type == "clear":
            if not queue:
                await interaction.response.send_message("❌ 佇列是空的，沒有歌曲可以清空！", ephemeral=True)
                return
            count = len(queue)
            self.queues[guild_id] = []
            embed = discord.Embed(
                title="🧹 播放清單已清空",
                description=f"已移除佇列中的 {count} 首歌曲。",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)