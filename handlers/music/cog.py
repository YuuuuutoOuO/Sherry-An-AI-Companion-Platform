from __future__ import annotations

import discord
import discord.opus
from discord.ext import commands
from discord import app_commands
import asyncio
import re
import time
from typing import Any, Optional

import settings
from core.concurrency import heavy_io_pool
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
        self._is_stopping: dict[int, bool] = {}
        self._is_skipping: dict[int, bool] = {}
        self._is_seeking: dict[int, bool] = {}
        self._is_shutting_down: bool = False
        self.last_voice_channel_id: dict[int, int] = {}
        self.last_text_channel_id: dict[int, int] = {}
        self.last_checkpoints: dict[int, dict[str, Any]] = {}
        self.current_sources: dict[int, Any] = {}
        self.consecutive_failures: dict[int, int] = {}
        self._recovery_provider: Any = None

    async def cog_load(self) -> None:
        """載入 Cog 時註冊狀態恢復提供者。"""
        try:
            from handlers.music.recovery_provider import MusicRecoveryProvider
            from core.recovery.manager import get_recovery_manager

            self._recovery_provider = MusicRecoveryProvider(self)
            get_recovery_manager().register_provider(self._recovery_provider)
        except Exception as exc:
            logger.warning("註冊 MusicRecoveryProvider 失敗: %s", exc)

    async def cog_unload(self) -> None:
        """卸載 Cog 時註銷狀態恢復提供者。"""
        try:
            from core.recovery.manager import get_recovery_manager

            get_recovery_manager().unregister_provider("music_player")
        except Exception:
            pass

    def trigger_recovery_snapshot(self) -> None:
        """非同步排程觸發音樂系統快照保存。"""
        if getattr(settings, "STATE_RECOVERY_ENABLED", True):
            try:
                from core.recovery.manager import get_recovery_manager

                manager = get_recovery_manager()
                if self.bot and not self.bot.is_closed() and self.bot.loop and self.bot.loop.is_running():
                    self.bot.loop.create_task(manager.capture_provider("music_player"))
            except Exception:
                pass

    # ──────────────────────────────────────────────────────────
    # 資源清理與自癒重連方法
    # ──────────────────────────────────────────────────────────

    def _cleanup_guild_source(self, guild_id: int) -> None:
        """安全釋放並強制終止指定伺服器現有的音訊來源與 FFmpeg 子行程。

        Args:
            guild_id: Discord 伺服器 ID。
        """
        source = self.current_sources.pop(guild_id, None)
        if source is None:
            return

        try:
            # 1. 若為 PCMVolumeTransformer 或包裝類別，取出底層 source
            raw_source = getattr(source, "original", source)

            # 2. 檢查底層 FFmpeg 子行程並強制終止
            proc = getattr(raw_source, "_process", None)
            if proc is not None:
                try:
                    if proc.poll() is None:
                        proc.kill()
                except Exception as e:
                    logger.debug("終止 FFmpeg 進程 (PID: %s) 失敗: %s", getattr(proc, "pid", None), e)

                # 確保關閉 stdout/stderr/stdin 管道，避免 Windows 管道阻塞
                for pipe in (getattr(proc, "stdout", None), getattr(proc, "stderr", None), getattr(proc, "stdin", None)):
                    if pipe is not None:
                        try:
                            pipe.close()
                        except Exception:
                            pass

            # 3. 呼叫原始 cleanup
            if hasattr(source, "cleanup"):
                source.cleanup()
            elif hasattr(raw_source, "cleanup"):
                raw_source.cleanup()
        except Exception as e:
            logger.debug("清理音訊來源失敗 (guild_id: %s): %s", guild_id, e)

    async def _recover_voice_and_play_next(
        self,
        guild: discord.Guild,
        text_channel: discord.TextChannel,
    ) -> None:
        """當底層 Native 音訊/語音通道崩潰時，執行自動重置並從中斷點原地續播。

        Args:
            guild: Discord 伺服器物件。
            text_channel: 文字回覆頻道。
        """
        guild_id = guild.id
        logger.warning("啟動語音客戶端自癒重連程序 (Guild: %s)", guild_id)

        # 1. 保存當前正在播放的歌曲與中斷時間點
        interrupted_song = self.current_song.get(guild_id)
        interrupted_offset = self.get_elapsed_time(guild_id)
        if interrupted_song:
            interrupted_song["start_offset"] = interrupted_offset
            self.save_checkpoint(guild_id, interrupted_song, interrupted_offset)
            if guild_id not in self.queues:
                self.queues[guild_id] = []
            self.queues[guild_id].insert(0, interrupted_song)
            logger.info(
                "已保留伺服器 %s 中斷歌曲《%s》與斷點 %s，排回佇列首位接續",
                guild_id,
                interrupted_song.get("title", "未知"),
                self.format_duration(interrupted_offset),
            )

        # 2. 清理舊音訊來源與時間軸
        self._cleanup_guild_source(guild_id)
        self._stop_progress_updater(guild_id)
        self.current_song[guild_id] = None
        self.is_paused[guild_id] = False

        # 3. 取得原先所在的語音頻道
        vc: Any = guild.voice_client
        target_channel = getattr(vc, "channel", None)

        # 4. 強制中斷舊語音客戶端
        if vc is not None:
            try:
                if hasattr(vc, "stop"):
                    vc.stop()
                if hasattr(vc, "disconnect"):
                    await vc.disconnect(force=True)
            except Exception as e:
                logger.debug("自癒程序中斷舊語音連線異常: %s", e)

        # 5. 短暫冷卻避免與 Discord Gateway 衝突
        await asyncio.sleep(1.0)

        # 6. 若找不到語音頻道，嘗試從伺服器語音狀態或快取找回
        if target_channel is None:
            cached_vc_id = self.last_voice_channel_id.get(guild_id)
            if cached_vc_id:
                target_channel = guild.get_channel(cached_vc_id)
        if target_channel is None:
            for channel in guild.voice_channels:
                if len([m for m in channel.members if not m.bot]) > 0:
                    target_channel = channel
                    break

        if target_channel is None:
            logger.warning("自癒程序找不到可重連的語音頻道 (Guild: %s)，結束自癒", guild_id)
            return

        # 7. 重新連線語音頻道並繼續播放
        try:
            await target_channel.connect(reconnect=True, timeout=20.0, self_deaf=True)
            logger.info("自癒重連語音頻道成功 (Channel: %s)，繼續播放佇列", target_channel.name)
            self._play_next(guild, text_channel)
        except Exception as e:
            logger.error("自癒重連語音頻道失敗: %s", e)
            await text_channel.send(f"❌ 語音通道自癒重連失敗：`{str(e)}`")

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
            self.trigger_recovery_snapshot()

    def resume_playback(self, guild_id: int) -> None:
        """記錄繼續播放狀態並重置段落計時。

        Args:
            guild_id: Discord 伺服器 ID。
        """
        if self.is_paused.get(guild_id, True):
            self.playback_start_time[guild_id] = time.time()
            self.is_paused[guild_id] = False
            self.trigger_recovery_snapshot()

    def save_checkpoint(
        self,
        guild_id: int,
        song: dict[str, Any] | None,
        offset_seconds: float,
        voice_channel_id: int | None = None,
        text_channel_id: int | None = None,
    ) -> None:
        """保存伺服器最後播放歌曲的斷點紀錄。

        Args:
            guild_id: Discord 伺服器 ID。
            song: 歌曲資料字典。
            offset_seconds: 斷點秒數。
            voice_channel_id: 選填語音頻道 ID。
            text_channel_id: 選填文字頻道 ID。
        """
        if not song:
            return
        vc_id = voice_channel_id or self.last_voice_channel_id.get(guild_id)
        tc_id = text_channel_id or self.last_text_channel_id.get(guild_id)
        if vc_id:
            self.last_voice_channel_id[guild_id] = vc_id
        if tc_id:
            self.last_text_channel_id[guild_id] = tc_id
        self.last_checkpoints[guild_id] = {
            "song": dict(song),
            "offset_seconds": float(max(0.0, offset_seconds)),
            "voice_channel_id": vc_id,
            "text_channel_id": tc_id,
            "saved_at": time.time(),
        }
        logger.debug(
            "[MusicCheckpoint] 已儲存斷點: Guild %s | 《%s》 at %.1fs",
            guild_id,
            song.get("title", "未知"),
            offset_seconds,
        )

    @staticmethod
    def parse_time_to_seconds(time_str: str, current_elapsed: int = 0) -> int | None:
        """將時間字串解析為目標秒數。

        支援:
        - 絕對時間: "20:00" -> 1200, "1:20:30" -> 4830, "1200" / "1200s" -> 1200, "20m" -> 1200
        - 中文時間: "20分" / "20分鐘" -> 1200, "1小時20分30秒" -> 4830, "90秒" -> 90
        - 相對跳轉: "+30", "+30s", "+1:00", "-15", "-30s", "-1m" (以 current_elapsed 為基準)

        Args:
            time_str: 欲解析的時間字串。
            current_elapsed: 當前已播放秒數（用於相對計算）。

        Returns:
            int | None: 解析出的總秒數，若格式無效則為 None。
        """
        raw = str(time_str).strip()
        if not raw:
            return None

        # 1. 相對跳轉語法 (+30, -15, +1:00, -30s)
        if raw.startswith(("+", "-")):
            sign = 1 if raw.startswith("+") else -1
            sub_raw = raw[1:].strip()
            if ":" in sub_raw:
                parts = sub_raw.split(":")
                try:
                    if len(parts) == 2:
                        delta = int(parts[0]) * 60 + int(parts[1])
                    elif len(parts) == 3:
                        delta = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                    else:
                        return None
                    return max(0, current_elapsed + sign * delta)
                except ValueError:
                    return None
            sub_sec = MusicHandler.parse_time_to_seconds(sub_raw, 0)
            if sub_sec is not None:
                return max(0, current_elapsed + sign * sub_sec)
            return None

        # 2. mm:ss 或 hh:mm:ss 格式 (如 20:00, 01:20:30)
        if ":" in raw:
            parts = raw.split(":")
            try:
                if len(parts) == 2:
                    return int(parts[0]) * 60 + int(parts[1])
                elif len(parts) == 3:
                    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                return None
            except ValueError:
                return None

        # 3. 純數字或秒 (如 1200, 1200s, 90秒)
        m_pure = re.match(r"^(\d+)\s*(?:s|sec|秒)?$", raw, re.IGNORECASE)
        if m_pure:
            return int(m_pure.group(1))

        # 4. 複合中文或英文字母單位 (如 1小時20分30秒, 1h20m30s, 20分, 20m)
        has_unit = False
        total = 0
        h_m = re.search(r"(\d+)\s*(?:小時|點|hr|h)", raw, re.IGNORECASE)
        if h_m:
            total += int(h_m.group(1)) * 3600
            has_unit = True
        m_m = re.search(r"(\d+)\s*(?:分鐘|min|分|m)", raw, re.IGNORECASE)
        if m_m:
            total += int(m_m.group(1)) * 60
            has_unit = True
        s_m = re.search(r"(\d+)\s*(?:sec|秒|s)", raw, re.IGNORECASE)
        if s_m:
            total += int(s_m.group(1))
            has_unit = True

        if has_unit:
            # 檢驗字串是否全由合法時間單位構成，防止 "20m_foo" 等髒資料誤判
            leftover = re.sub(
                r"\s*(?:\d+)\s*(?:小時|點|hr|h|分鐘|min|分|m|sec|秒|s)\s*",
                "",
                raw,
                flags=re.IGNORECASE,
            )
            if not leftover.strip():
                return total

        return None

    @staticmethod
    def extract_url_timestamp(url: str) -> int:
        """從 YouTube 等網址提取 t= 或 start= 時間戳（秒數）。

        Args:
            url: 影片或音訊網址。

        Returns:
            int: 提取到的秒數，若無則為 0。
        """
        if not url:
            return 0
        m = re.search(r"[?&](?:t|start)=([0-9hmsHMS]+)", url)
        if not m:
            return 0
        raw_val = m.group(1).lower()
        if raw_val.isdigit():
            return int(raw_val)
        secs = 0
        h_m = re.search(r"(\d+)h", raw_val)
        m_m = re.search(r"(\d+)m", raw_val)
        s_m = re.search(r"(\d+)s", raw_val)
        if h_m:
            secs += int(h_m.group(1)) * 3600
        if m_m:
            secs += int(m_m.group(1)) * 60
        if s_m:
            secs += int(s_m.group(1))
        return secs

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
            q_lines: list[str] = []
            for i, song in enumerate(page_songs, start=start_idx + 1):
                duration_str = self.format_duration(song.get("duration", 0) or 0)
                title = song["title"]
                if len(title) > 40:
                    title = title[:37] + "..."
                s_url = self.shorten_url(song.get("webpage_url", ""))
                line = f"**{i}.** [{title}]({s_url}) `[{duration_str}]`"
                total_len = sum((len(x) for x in desc_parts), 0) + sum((len(x) for x in q_lines), 0) + len(line)
                if total_len > 3800:
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
                vc: Any = guild.voice_client if guild else None
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
                    self.trigger_recovery_snapshot()
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
            self._is_skipping[guild_id] = True
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
        self.trigger_recovery_snapshot()
        return count

    def skip_song(self, guild_id: int) -> bool:
        """跳過指定伺服器當前正在播放的歌曲。

        Args:
            guild_id: Discord 伺服器 ID。

        Returns:
            bool: 是否成功觸發跳過。
        """
        guild = self.bot.get_guild(guild_id)
        vc: Any = guild.voice_client if guild else None
        if vc and hasattr(vc, "is_connected") and vc.is_connected():
            if hasattr(vc, "is_playing") and (vc.is_playing() or vc.is_paused()):
                self._is_skipping[guild_id] = True
                self._cleanup_guild_source(guild_id)
                vc.stop()
                self.trigger_recovery_snapshot()
                return True
        return False

    async def stop_player(self, guild_id: int, disconnect: bool = True) -> None:
        """停止指定伺服器的音樂播放、清空佇列、重置循環模式並釋放播放資源。

        Args:
            guild_id: Discord 伺服器 ID。
            disconnect: 是否中斷語音頻道連線（預設 True）。
        """
        # 1. 保存斷點：若當前歌曲播放超過 10 秒且未播完，自動儲存至斷點記憶庫
        cur = self.current_song.get(guild_id)
        if cur:
            elapsed = self.get_elapsed_time(guild_id)
            duration = float(cur.get("duration", 0) or 0)
            if elapsed >= 10 and (duration == 0 or elapsed < duration - 10):
                self.save_checkpoint(guild_id, cur, elapsed)

        # 2. 設置停止標記，阻斷 after_playback 的非預期循環回塞與自動切歌
        self._is_stopping[guild_id] = True

        # 3. 清空佇列與狀態
        self.queues[guild_id] = []
        self.current_song[guild_id] = None
        self.loop_modes[guild_id] = "none"
        self.is_paused[guild_id] = False
        self.playback_start_time.pop(guild_id, None)
        self.playback_accumulated_time.pop(guild_id, None)
        self.consecutive_failures[guild_id] = 0

        # 4. 強制釋放現有音訊來源與 FFmpeg 進程
        self._cleanup_guild_source(guild_id)

        # 5. 停止時間軸任務
        self._stop_progress_updater(guild_id)

        # 6. 刪除當前播放 Embed 訊息
        await self._delete_song_message(guild_id)

        # 7. 停止語音播放與中斷連線
        guild = self.bot.get_guild(guild_id)
        vc: Any = guild.voice_client if guild else None
        if vc and hasattr(vc, "is_connected") and vc.is_connected():
            if hasattr(vc, "is_playing") and (vc.is_playing() or vc.is_paused()):
                vc.stop()
            if disconnect and hasattr(vc, "disconnect"):
                await vc.disconnect()

        if not self._is_shutting_down:
            self.trigger_recovery_snapshot()

    async def seek_to(
        self,
        guild: discord.Guild,
        text_channel: discord.abc.Messageable | None,
        target_seconds: int,
    ) -> bool:
        """跳轉當前播放歌曲至指定時間軸秒數 (FFmpeg -ss)。

        Args:
            guild: Discord 伺服器物件。
            text_channel: 發送訊息的文字頻道。
            target_seconds: 目標秒數。

        Returns:
            bool: 是否成功執行跳轉。
        """
        guild_id = guild.id
        song = self.current_song.get(guild_id)
        vc: Any = guild.voice_client

        if not song or not vc or not getattr(vc, "is_connected", lambda: False)():
            return False

        duration = float(song.get("duration", 0) or 0)
        if duration > 0:
            target_seconds = max(0, min(int(target_seconds), max(0, int(duration) - 2)))
        else:
            target_seconds = max(0, int(target_seconds))

        target_url = song.get("webpage_url") or song.get("url")
        if not target_url:
            return False

        self._is_seeking[guild_id] = True
        loop = asyncio.get_running_loop()

        try:
            true_audio_stream = song.get("stream_url")
            if not true_audio_stream:
                fresh_song: Any = await loop.run_in_executor(
                    heavy_io_pool, lambda: ytdl_client.extract_info(target_url, download=False)
                )
                if not fresh_song or not isinstance(fresh_song, dict):
                    self._is_seeking.pop(guild_id, None)
                    return False
                song_data = fresh_song
                if "entries" in song_data and song_data["entries"]:
                    first_entry = song_data["entries"][0]
                    if isinstance(first_entry, dict):
                        song_data = first_entry
                true_audio_stream = song_data.get("url")
                formats = song_data.get("formats")
                if not true_audio_stream and isinstance(formats, list):
                    audio_formats = [
                        f for f in formats
                        if isinstance(f, dict) and f.get("acodec") != "none" and f.get("url")
                    ]
                    if audio_formats:
                        true_audio_stream = audio_formats[-1]["url"]
                if true_audio_stream:
                    song["stream_url"] = true_audio_stream

            if not true_audio_stream:
                self._is_seeking.pop(guild_id, None)
                return False

            base_before = str(FFMPEG_OPTIONS.get("before_options", "")).strip()
            seek_before = f"{base_before} -ss {target_seconds}".strip() if target_seconds > 0 else base_before
            raw_source = discord.FFmpegPCMAudio(
                true_audio_stream,
                executable=FFMPEG_EXE_PATH,
                before_options=seek_before,
                options=str(FFMPEG_OPTIONS.get("options", "")),
            )
            vol = getattr(settings, "MUSIC_DEFAULT_VOLUME", 1.2)
            source = discord.PCMVolumeTransformer(raw_source, volume=vol)

            was_paused = bool(
                self.is_paused.get(guild_id, False)
                or (vc.is_paused() if hasattr(vc, "is_paused") else False)
            )

            self._cleanup_guild_source(guild_id)
            if hasattr(vc, "is_playing") and (vc.is_playing() or vc.is_paused()):
                vc.stop()
                for _ in range(6):
                    if not (vc.is_playing() or vc.is_paused()):
                        break
                    await asyncio.sleep(0.05)

            self.playback_start_time[guild_id] = time.time()
            self.playback_accumulated_time[guild_id] = float(target_seconds)
            self.current_sources[guild_id] = source

            target_tc = text_channel or self.bot.get_channel(self.last_text_channel_id.get(guild_id, 0))
            after_fn = self._create_after_playback(guild, target_tc, song)
            vc.play(source, after=after_fn)

            if was_paused and hasattr(vc, "pause"):
                vc.pause()
                self.is_paused[guild_id] = True
            else:
                self.is_paused[guild_id] = False

            msg = self.current_message.get(guild_id)
            if msg:
                try:
                    embed = self.build_playing_embed(guild_id)
                    await msg.edit(embed=embed)
                except Exception:
                    pass

            self.trigger_recovery_snapshot()
            return True
        except Exception as exc:
            self._is_seeking.pop(guild_id, None)
            if "raw_source" in locals() and hasattr(raw_source, "cleanup"):
                try:
                    raw_source.cleanup()
                except Exception:
                    pass
            self._cleanup_guild_source(guild_id)
            logger.error("seek_to 執行失敗 (Guild: %s): %s", guild_id, exc)
            return False

    async def resume_from_checkpoint(
        self,
        guild: discord.Guild,
        text_channel: discord.abc.Messageable | None = None,
        voice_channel: discord.VoiceChannel | None = None,
    ) -> dict[str, Any] | None:
        """從斷點記憶庫恢復播放上次中斷的歌曲與時間點。

        Args:
            guild: Discord 伺服器物件。
            text_channel: 發送訊息的文字頻道。
            voice_channel: 欲連線之語音頻道（若無則自動找回）。

        Returns:
            dict[str, Any] | None: 恢復之斷點資料字典，若無可恢復斷點則回傳 None。
        """
        guild_id = guild.id
        cp = self.last_checkpoints.get(guild_id)
        if not cp or not cp.get("song"):
            return None

        song = dict(cp["song"])
        offset = float(cp.get("offset_seconds", 0.0))
        song["start_offset"] = int(offset)

        vc: Any = guild.voice_client
        target_vc = voice_channel
        if not target_vc and cp.get("voice_channel_id"):
            target_vc = guild.get_channel(cp["voice_channel_id"])
        if not target_vc:
            for ch in guild.voice_channels:
                if len([m for m in ch.members if not m.bot]) > 0:
                    target_vc = ch
                    break

        if not target_vc:
            logger.warning("resume_from_checkpoint 找不到可連線之語音頻道 (Guild: %s)", guild_id)
            return None

        if vc is None:
            vc = await target_vc.connect(reconnect=True, timeout=20.0, self_deaf=True)
        elif getattr(vc, "channel", None) != target_vc:
            await vc.move_to(target_vc)

        self.last_voice_channel_id[guild_id] = target_vc.id
        target_tc = text_channel or guild.get_channel(cp.get("text_channel_id", 0))
        if target_tc and hasattr(target_tc, "id"):
            self.last_text_channel_id[guild_id] = target_tc.id

        if guild_id not in self.queues:
            self.queues[guild_id] = []
        self.queues[guild_id].insert(0, song)

        is_playing = bool(vc and hasattr(vc, "is_playing") and (vc.is_playing() or vc.is_paused()))
        if not is_playing:
            self._play_next(guild, target_tc)  # type: ignore[arg-type]
        else:
            self.skip_song(guild_id)

        return cp

    def _create_after_playback(
        self,
        guild: discord.Guild,
        text_channel: discord.abc.Messageable,
        song: dict[str, Any],
    ) -> Any:
        """建立統一且具備半途斷流自癒與斷點保存的 after_playback 回呼函式。"""
        guild_id = guild.id

        def after_playback(error: Exception | None = None) -> None:
            # 1. 若為 seek 跳轉觸發的中斷，略過清理新來源與切歌流程
            if self._is_seeking.pop(guild_id, False):
                logger.debug("播放中斷來自 seek 跳轉 (guild_id: %s)，略過 after_playback 切歌與清理", guild_id)
                return

            self._cleanup_guild_source(guild_id)

            # 2. 若為 stop 操作觸發的中斷，略過切歌流程
            if self._is_stopping.get(guild_id, False):
                logger.debug("播放中斷來自 stop 操作 (guild_id: %s)，略過 after_playback", guild_id)
                return

            guild_obj = self.bot.get_guild(guild_id)
            curr_vc: Any = guild_obj.voice_client if guild_obj else None
            if curr_vc is None or not getattr(curr_vc, "is_connected", lambda: False)():
                logger.debug("語音客戶端已斷線 (guild_id: %s)，略過 after_playback 重入與循環", guild_id)
                self._stop_progress_updater(guild_id)
                if not self._is_stopping.get(guild_id, False) and song:
                    elapsed = self.get_elapsed_time(guild_id)
                    song_copy = dict(song)
                    song_copy["start_offset"] = elapsed
                    self.save_checkpoint(guild_id, song_copy, elapsed)
                    if guild_id not in self.queues:
                        self.queues[guild_id] = []
                    if not (self.queues[guild_id] and self.queues[guild_id][0].get("webpage_url") == song_copy.get("webpage_url")):
                        self.queues[guild_id].insert(0, song_copy)
                self.current_song[guild_id] = None
                self.is_paused[guild_id] = False
                return

            # 3. 處理播放錯誤
            if error:
                logger.error("播放中斷錯誤: %s", error)
                err_str = str(error).lower()
                is_native_crash = (
                    "access violation" in err_str
                    or "not connected to voice" in err_str
                    or isinstance(error, (OSError, discord.ClientException, discord.opus.OpusError))
                )

                self.consecutive_failures[guild_id] = self.consecutive_failures.get(guild_id, 0) + 1

                if self.consecutive_failures[guild_id] >= 3:
                    logger.warning("伺服器 %s 連續 3 次播放失敗，觸發熔斷保護", guild_id)
                    self._stop_progress_updater(guild_id)
                    self.current_song[guild_id] = None
                    self.is_paused[guild_id] = False
                    self.queues[guild_id] = []
                    asyncio.run_coroutine_threadsafe(
                        text_channel.send("🛑 連續 3 次播放異常，為保護系統穩定已自動暫停播放並清空佇列。"),
                        self.bot.loop,
                    )
                    return

                if is_native_crash:
                    logger.warning("偵測到底層語音或音訊解碼崩潰 (%s)，啟動語音自癒重連...", error)
                    asyncio.run_coroutine_threadsafe(
                        text_channel.send("⚠️ 偵測到底層語音串流異常，橘雪莉正在重新校準語音頻道..."),
                        self.bot.loop,
                    )
                    asyncio.run_coroutine_threadsafe(
                        self._recover_voice_and_play_next(guild, text_channel),  # type: ignore[arg-type]
                        self.bot.loop,
                    )
                    return
            else:
                self.consecutive_failures[guild_id] = 0

            # 4. 偵測半途斷流 (Premature EOF) 自動原地續播
            is_skipping = self._is_skipping.pop(guild_id, False)
            elapsed = self.get_elapsed_time(guild_id)
            duration = float(song.get("duration", 0) or 0)
            eof_retries = int(song.get("eof_retries", 0))
            is_premature_eof = (
                not is_skipping
                and not self._is_stopping.get(guild_id, False)
                and duration > 60
                and elapsed < duration - 15
                and elapsed >= 5
                and eof_retries < 3
            )

            if is_premature_eof:
                logger.warning(
                    "歌曲《%s》於 %s 處異常中斷 (總長 %s，重試 %d/3)，自動從斷點嘗試重連串流...",
                    song.get("title", "未知"),
                    self.format_duration(elapsed),
                    self.format_duration(duration),
                    eof_retries + 1,
                )
                self._stop_progress_updater(guild_id)
                song_copy = dict(song)
                song_copy["start_offset"] = elapsed
                song_copy["eof_retries"] = eof_retries + 1
                self.save_checkpoint(guild_id, song_copy, elapsed)
                if guild_id not in self.queues:
                    self.queues[guild_id] = []
                self.queues[guild_id].insert(0, song_copy)
                self.current_song[guild_id] = None
                self.is_paused[guild_id] = False
                self.bot.loop.call_soon_threadsafe(self._play_next, guild, text_channel)
                return

            self._stop_progress_updater(guild_id)
            asyncio.run_coroutine_threadsafe(self._delete_song_message(guild_id), self.bot.loop)

            loop_mode = self.loop_modes.get(guild_id, "none")
            if loop_mode == "single" and song:
                if not is_skipping:
                    song_to_requeue = dict(song)
                    song_to_requeue["start_offset"] = 0
                    self.queues[guild_id].insert(0, song_to_requeue)
            elif loop_mode == "all" and song:
                song_to_requeue = dict(song)
                song_to_requeue["start_offset"] = 0
                self.queues[guild_id].append(song_to_requeue)

            self.current_song[guild_id] = None
            self.is_paused[guild_id] = False
            self.bot.loop.call_soon_threadsafe(self._play_next, guild, text_channel)

        return after_playback

    # ──────────────────────────────────────────────────────────
    # 核心播放引擎
    # ──────────────────────────────────────────────────────────

    async def headless_play(
        self,
        text_channel: discord.TextChannel,
        voice_channel: discord.VoiceChannel,
        query: str,
        insert_at_top: bool = False,
        start_offset: int = 0,
    ) -> None:
        """無頭模式載入音樂至佇列，並於需要時觸發播放。

        Args:
            text_channel: 文字回覆頻道。
            voice_channel: 機器人應加入的語音頻道。
            query: YouTube 網址或關鍵字。
            insert_at_top: 是否優先插播在佇列首位。
            start_offset: 指定起始播放秒數。
        """
        guild = text_channel.guild
        guild_id = guild.id
        self._is_stopping[guild_id] = False
        self.last_voice_channel_id[guild_id] = voice_channel.id
        self.last_text_channel_id[guild_id] = text_channel.id

        vc: Any = guild.voice_client
        try:
            if vc is None:
                vc = await voice_channel.connect(reconnect=True, timeout=20.0, self_deaf=True)
            elif vc.channel != voice_channel:
                await vc.move_to(voice_channel)
        except Exception as e:
            await text_channel.send(f"❌ 橘雪莉的麥克風壞掉了 (連線失敗)：\n`{str(e)}`")
            return

        if guild_id not in self.queues:
            self.queues[guild_id] = []

        cleaned_query = query.strip().rstrip("&?").strip()
        youtube_regex = r'^(https?\:\/\/)?(www\.youtube\.com|youtu\.?be)\/.+$'
        search_query = cleaned_query if re.match(youtube_regex, cleaned_query) else f"ytsearch:{cleaned_query}"

        try:
            loop = asyncio.get_running_loop()
            data: dict[str, Any] | None = await loop.run_in_executor(
                heavy_io_pool, lambda: ytdl_client.extract_info(search_query, download=False)
            )
            if not data:
                await text_channel.send("❌ 橘雪莉找不到這首歌，請換個關鍵字或連結試試看！")
                return

            query_offset = self.extract_url_timestamp(cleaned_query)
            if 'entries' in data and data['entries']:
                entries = [e for e in data['entries'] if e]
                added_items: list[dict[str, Any]] = []
                for entry in entries:
                    url = entry.get('webpage_url') or entry.get('url')
                    if url and not str(url).startswith('http'):
                        url = f"https://www.youtube.com/watch?v={url}"
                    elif not url and entry.get('id'):
                        url = f"https://www.youtube.com/watch?v={entry.get('id')}"

                    thumb = entry.get('thumbnail')
                    if not thumb and entry.get('thumbnails'):
                        thumb = entry['thumbnails'][0].get('url')

                    url_offset = self.extract_url_timestamp(url or "")
                    extracted_offset = query_offset if query_offset > 0 else url_offset
                    item_offset = start_offset if start_offset > 0 else extracted_offset

                    added_items.append({
                        'title': entry.get('title', '未知曲目'),
                        'webpage_url': url or '',
                        'duration': entry.get('duration', 0) or 0,
                        'thumbnail': thumb,
                        'extractor': entry.get('extractor', 'youtube'),
                        'start_offset': item_offset,
                    })
                if not added_items:
                    await text_channel.send("❌ 橘雪莉找不到這首歌，請換個關鍵字或連結試試看！")
                    return
                if insert_at_top:
                    self.queues[guild_id] = added_items + self.queues[guild_id]
                else:
                    self.queues[guild_id].extend(added_items)
            else:
                url = data.get('webpage_url') or data.get('url')
                if url and not str(url).startswith('http'):
                    url = f"https://www.youtube.com/watch?v={url}"
                elif not url and data.get('id'):
                    url = f"https://www.youtube.com/watch?v={data.get('id')}"

                thumb = data.get('thumbnail')
                if not thumb and data.get('thumbnails'):
                    thumb = data['thumbnails'][0].get('url')

                url_offset = self.extract_url_timestamp(url or "")
                extracted_offset = query_offset if query_offset > 0 else url_offset
                item_offset = start_offset if start_offset > 0 else extracted_offset

                song_item: dict[str, Any] = {
                    'title': data.get('title', '未知曲目'),
                    'webpage_url': url or '',
                    'duration': data.get('duration', 0) or 0,
                    'thumbnail': thumb,
                    'extractor': data.get('extractor', 'youtube'),
                    'start_offset': item_offset,
                }
                if insert_at_top:
                    self.queues[guild_id].insert(0, song_item)
                else:
                    self.queues[guild_id].append(song_item)

            is_currently_playing = bool(vc and hasattr(vc, "is_playing") and (vc.is_playing() or vc.is_paused()))
            total_queue_len = len(self.queues[guild_id])
            if not is_currently_playing:
                first_song = self.queues[guild_id][0]
                dur_str = self.format_duration(first_song.get("duration", 0) or 0)
                offset_notice = ""
                if first_song.get("start_offset", 0) > 0:
                    offset_notice = f" (從 {self.format_duration(first_song['start_offset'])} 處起播)"
                await text_channel.send(f"🎵 偵探橘雪莉已就定位！開始播放：**{first_song['title']}** `[{dur_str}]`{offset_notice}")
                self._play_next(guild, text_channel)
            else:
                target_song = self.queues[guild_id][0] if insert_at_top else self.queues[guild_id][-1]
                dur_str = self.format_duration(target_song.get("duration", 0) or 0)
                await text_channel.send(f"📥 已加入播放佇列（第 **{total_queue_len}** 首）：**{target_song['title']}** `[{dur_str}]`")

            self.trigger_recovery_snapshot()

            # 行為記憶：記錄實際解析出的歌曲（涵蓋 slash command 與工具呼叫兩條路徑）
            try:
                from types import SimpleNamespace
                from handlers.social.action_recorder import record_tool_action

                played_title = first_song["title"] if not is_currently_playing else target_song["title"]
                record_tool_action(
                    SimpleNamespace(guild=guild, channel=text_channel, author=None),
                    "MUSIC",
                    cleaned_query,
                    f"播放了《{played_title}》",
                )
            except Exception as mem_err:
                logger.debug("headless_play: 行為記憶寫入略過: %s", mem_err)

        except Exception as e:
            logger.error("headless_play 提取歌曲失敗: %s", e, exc_info=True)
            await text_channel.send(f"⚠️ 找不到這首歌耶，是不是被消音了？ (`{str(e)}`)")
            return

    def _play_next(self, guild: discord.Guild, text_channel: discord.TextChannel) -> None:
        """背景播放下一首歌曲引擎。

        Args:
            guild: Discord 伺服器物件。
            text_channel: 文字頻道。
        """
        guild_id = guild.id
        vc: Any = guild.voice_client

        if vc is None or not vc.is_connected():
            self._stop_progress_updater(guild_id)
            self._cleanup_guild_source(guild_id)
            self.current_song[guild_id] = None
            return

        if guild_id in self.queues and len(self.queues[guild_id]) > 0:
            song = self.queues[guild_id].pop(0)
            self.current_song[guild_id] = song
            self._is_seeking[guild_id] = False

            async def background_fetch_and_play() -> None:
                try:
                    loop = asyncio.get_running_loop()
                    target_url = song.get('webpage_url') or song.get('url')
                    if not target_url:
                        raise ValueError("歌曲項目缺少有效的網址")

                    fresh_song: Any = await loop.run_in_executor(
                        heavy_io_pool, lambda: ytdl_client.extract_info(target_url, download=False)
                    )

                    if not fresh_song or not isinstance(fresh_song, dict):
                        raise ValueError("yt-dlp 無法解析影片，可能缺少 JS 執行環境或影片受限")

                    song_data: dict[str, Any] = fresh_song
                    if 'entries' in song_data and song_data['entries']:
                        first_entry = song_data['entries'][0]
                        if isinstance(first_entry, dict):
                            song_data = first_entry

                    true_audio_stream = song_data.get('url')
                    formats = song_data.get('formats')
                    if not true_audio_stream and isinstance(formats, list):
                        audio_formats = [
                            f for f in formats
                            if isinstance(f, dict) and f.get('acodec') != 'none' and f.get('url')
                        ]
                        if audio_formats:
                            true_audio_stream = audio_formats[-1]['url']

                    if not true_audio_stream:
                        raise ValueError("成功解析但找不到音頻串流 URL")

                    song['duration'] = song_data.get('duration', 0) or song.get('duration', 0) or 0
                    song['thumbnail'] = song_data.get('thumbnail') or song.get('thumbnail')
                    song['stream_url'] = true_audio_stream

                    start_offset = int(song.get("start_offset", 0) or 0)
                    base_before = str(FFMPEG_OPTIONS.get("before_options", "")).strip()
                    seek_before = f"{base_before} -ss {start_offset}".strip() if start_offset > 0 else base_before

                    raw_source = discord.FFmpegPCMAudio(
                        true_audio_stream,
                        executable=FFMPEG_EXE_PATH,
                        before_options=seek_before,
                        options=str(FFMPEG_OPTIONS.get("options", "")),
                    )
                    vol = getattr(settings, "MUSIC_DEFAULT_VOLUME", 1.2)
                    source = discord.PCMVolumeTransformer(raw_source, volume=vol)
                    after_playback = self._create_after_playback(guild, text_channel, song)

                    # 如果當前已在播放中，先安全停止避免 ClientException
                    if vc and hasattr(vc, "is_playing") and (vc.is_playing() or vc.is_paused()):
                        self._cleanup_guild_source(guild_id)
                        vc.stop()
                        await asyncio.sleep(0.05)

                    self.playback_start_time[guild_id] = time.time()
                    self.playback_accumulated_time[guild_id] = float(start_offset)
                    self.is_paused[guild_id] = False
                    self.current_sources[guild_id] = source

                    if vc and hasattr(vc, "play"):
                        vc.play(source, after=after_playback)

                    await self._delete_song_message(guild_id)
                    view = MusicControlView()
                    embed = self.build_playing_embed(guild_id)
                    msg = await text_channel.send(embed=embed, view=view)
                    self.current_message[guild_id] = msg
                    self._start_progress_updater(guild_id)
                    self.trigger_recovery_snapshot()

                except Exception as e:
                    self._cleanup_guild_source(guild_id)
                    self.consecutive_failures[guild_id] = self.consecutive_failures.get(guild_id, 0) + 1
                    logger.error("背景提取歌曲失敗，切換下一首: %s", e)

                    if self.consecutive_failures[guild_id] >= 3:
                        logger.warning("伺服器 %s 連續 3 次提取/播放失敗，觸發熔斷保護", guild_id)
                        await text_channel.send(
                            f"⚠️ 歌曲提取失敗: `{str(e)[:50]}`\n"
                            f"🛑 連續 3 次播放異常，為保護系統穩定已自動暫停播放並清空佇列。"
                        )
                        self._stop_progress_updater(guild_id)
                        self.current_song[guild_id] = None
                        self.queues[guild_id] = []
                        self.is_paused[guild_id] = False
                        return

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
            self._cleanup_guild_source(guild_id)
            self.current_song[guild_id] = None
            self.is_paused[guild_id] = False

    # ──────────────────────────────────────────────────────────
    # 單一 /play 指令（合併所有操作為下拉選單）
    # ──────────────────────────────────────────────────────────

    @app_commands.command(name="play", description="🎵 音樂播放系統 — 無參數開啟留聲機主控面板，或直接選擇操作")
    @app_commands.describe(
        play_type="操作類型（可選，留空則開啟留聲機互動主控台）",
        context="補充內容（YouTube 網址 / 歌名 / 編號），部分操作不需填寫",
    )
    @app_commands.choices(play_type=[
        app_commands.Choice(name="▶️  add    — 新增 YouTube 網址到佇列", value="add"),
        app_commands.Choice(name="🔍  search — 搜尋歌名並播放",           value="search"),
        app_commands.Choice(name="📌  insert — 優先插播（搜尋或網址排到最前）", value="insert"),
        app_commands.Choice(name="📋  list   — 查看目前播放清單",          value="list"),
        app_commands.Choice(name="⏭️  skip   — 跳過當前歌曲",             value="skip"),
        app_commands.Choice(name="🎯  jump   — 優先播放指定編號的歌",      value="jump"),
        app_commands.Choice(name="⏱️  seek   — 跳轉當前歌曲至指定時間 (如 20:00 或 +30s)", value="seek"),
        app_commands.Choice(name="🔄  resume — 從上次中斷的歌曲斷點恢復播放",    value="resume"),
        app_commands.Choice(name="🗑️  remove — 刪除指定編號的歌",         value="remove"),
        app_commands.Choice(name="🧹  clear  — 清空整個播放清單",            value="clear"),
        app_commands.Choice(name="⏹️  stop   — 停止播放並離開語音頻道",       value="stop"),
    ])
    async def play_command(
        self,
        interaction: discord.Interaction,
        play_type: Optional[str] = None,
        context: Optional[str] = "",
    ) -> None:
        """處理 /play 斜線指令的分支操作與主控台發送。

        Args:
            interaction: Discord 互動事件。
            play_type: 操作類型 (add, search, list, skip, jump, remove 等，留空開啟面板)。
            context: 相關參數 (網址、關鍵字或歌曲編號)。
        """
        guild_id = interaction.guild_id
        if guild_id is None:
            return

        if not play_type:
            embed = self.build_playing_embed(guild_id)
            await interaction.response.send_message(embed=embed, view=MusicControlView(), ephemeral=False)
            return

        context_str: str = context or ""
        context = context_str
        queue    = self.queues.get(guild_id, [])
        current  = self.current_song.get(guild_id)
        vc: Any  = interaction.guild.voice_client if interaction.guild else None
        user_vc  = (
            interaction.user.voice.channel
            if interaction.user and hasattr(interaction.user, "voice") and interaction.user.voice
            else None
        )

        # ── add ────────────────────────────────────────────────
        if play_type == "add":
            await interaction.response.defer()
            youtube_regex = r'^(https?\:\/\/)?(www\.youtube\.com|youtu\.?be)\/.+$'
            if not context_str.strip():
                await interaction.followup.send("❌ 請在 `context` 填入 YouTube 網址！", ephemeral=True)
                return
            if not re.match(youtube_regex, context_str.strip()):
                await interaction.followup.send("❌ 格式錯誤！請提供有效的 YouTube 影片或播放清單網址。", ephemeral=True)
                return
            if not user_vc:
                await interaction.followup.send("❌ 你必須先加入一個語音頻道！", ephemeral=True)
                return
            await self.headless_play(interaction.channel, user_vc, context_str.strip())  # type: ignore[arg-type]
            await interaction.followup.send(f"✅ 已收到！正在把 `{context_str.strip()}` 載入佇列...")

        # ── search ─────────────────────────────────────────────
        elif play_type == "search":
            await interaction.response.defer()
            if not context_str.strip():
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
            if self.skip_song(guild_id):
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
            if vc and hasattr(vc, "stop") and (vc.is_playing() or vc.is_paused()):
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
                if s > e:
                    s, e = e, s
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

        # ── seek ───────────────────────────────────────────────
        elif play_type == "seek":
            if not current:
                await interaction.response.send_message("❌ 目前沒有音樂正在播放，無法跳轉時間軸！", ephemeral=True)
                return
            if not context_str.strip():
                await interaction.response.send_message("❌ 請在 `context` 填入要跳轉的時間（如 `20:00`、`1:20:30` 或 `+30s`）！", ephemeral=True)
                return
            cur_elapsed = self.get_elapsed_time(guild_id)
            target_sec = self.parse_time_to_seconds(context_str.strip(), cur_elapsed)
            if target_sec is None:
                await interaction.response.send_message(f"❌ 無法解析時間格式 `{context_str}`，請輸入 `20:00`、`1:05:30` 或 `+30s`！", ephemeral=True)
                return
            await interaction.response.defer()
            success = await self.seek_to(interaction.guild, interaction.channel, target_sec)
            if success:
                target_str = self.format_duration(target_sec)
                await interaction.followup.send(f"⏱️ 已跳轉播放時間軸至 **{target_str}**！🎵")
            else:
                await interaction.followup.send("❌ 跳轉時間軸失敗，可能是音訊串流已過期或無法重新建立。", ephemeral=True)

        # ── resume ─────────────────────────────────────────────
        elif play_type == "resume":
            await interaction.response.defer()
            checkpoint = await self.resume_from_checkpoint(interaction.guild, interaction.channel, user_vc)
            if checkpoint:
                song = checkpoint.get("song", {})
                sec = checkpoint.get("offset_seconds", 0)
                dur_str = self.format_duration(sec)
                await interaction.followup.send(f"🔄 成功接續播放上次中斷的音樂：**{song.get('title', '未知')}** (從 **{dur_str}** 處開始)！🎵")
            else:
                await interaction.followup.send("❌ 查無此伺服器可恢復的音樂斷點紀錄（或語音頻道無人在線）。", ephemeral=True)

        # ── stop ───────────────────────────────────────────────
        elif play_type == "stop":
            await self.stop_player(guild_id, disconnect=True)
            await interaction.response.send_message("⏹️ 已停止播放並清空清單！", delete_after=5)

    # ──────────────────────────────────────────────────────────
    # 語音連線狀態監聽與非預期斷線智慧自癒
    # ──────────────────────────────────────────────────────────

    async def _handle_unexpected_voice_disconnect(
        self,
        guild: discord.Guild,
        before_channel: discord.VoiceChannel | discord.StageChannel,
        text_channel: discord.abc.Messageable | None,
    ) -> None:
        """處理非預期的語音頻道斷線（如 Discord Gateway 重設、語音伺服器遷移或網路抖動），啟動自動重連與接續播放自癒。"""
        guild_id = guild.id
        if self._is_stopping.get(guild_id, False) or self._is_shutting_down:
            return

        # 1. 立即暫存中斷歌曲與已播秒數
        interrupted_song = self.current_song.get(guild_id)
        interrupted_offset = self.get_elapsed_time(guild_id)
        if interrupted_song:
            song_copy = dict(interrupted_song)
            song_copy["start_offset"] = interrupted_offset
            self.save_checkpoint(guild_id, song_copy, interrupted_offset)
            if guild_id not in self.queues:
                self.queues[guild_id] = []
            # 若佇列首位不是該中斷歌曲，插回首位以備接續
            if not (self.queues[guild_id] and self.queues[guild_id][0].get("webpage_url") == song_copy.get("webpage_url")):
                self.queues[guild_id].insert(0, song_copy)

        self._cleanup_guild_source(guild_id)
        self._stop_progress_updater(guild_id)
        self.current_song[guild_id] = None
        self.is_paused[guild_id] = False

        # 2. 自動重連自癒重試迴圈（最多重試 3 次，指數退避）
        max_retries = 3
        reconnect_delays = [2.0, 4.0, 6.0]

        for attempt in range(max_retries):
            delay = reconnect_delays[attempt]
            logger.info(
                "語音非預期斷線自癒程序：等待 %.1f 秒後進行第 %d/%d 次重連嘗試 (Guild: %s, Channel: %s)...",
                delay,
                attempt + 1,
                max_retries,
                guild_id,
                getattr(before_channel, "name", str(before_channel.id)),
            )
            await asyncio.sleep(delay)

            if self._is_stopping.get(guild_id, False) or self._is_shutting_down:
                logger.info("重連期間收到停止或關機指示，中止自癒程序 (Guild: %s)", guild_id)
                return

            # 確保目標頻道依然存在於伺服器中
            fresh_guild = self.bot.get_guild(guild_id) or guild
            target_channel = fresh_guild.get_channel(before_channel.id) if fresh_guild else None
            if not target_channel:
                cached_id = self.last_voice_channel_id.get(guild_id)
                if cached_id and fresh_guild:
                    target_channel = fresh_guild.get_channel(cached_id)

            if not target_channel or not isinstance(target_channel, (discord.VoiceChannel, discord.StageChannel)):
                logger.warning("自癒程序目標語音頻道已不存在 (Guild: %s)，放棄重連", guild_id)
                break

            # 舊 VoiceClient 若殘留則先徹底清理
            curr_vc: Any = fresh_guild.voice_client if fresh_guild else None
            if curr_vc is not None:
                try:
                    if hasattr(curr_vc, "stop"):
                        curr_vc.stop()
                    if hasattr(curr_vc, "disconnect"):
                        await curr_vc.disconnect(force=True)
                except Exception as exc:
                    logger.debug("自癒清理舊 VoiceClient 異常: %s", exc)
                await asyncio.sleep(0.5)

            try:
                # 重新連線語音頻道（開啟 self_deaf=True 以降低流量並防 UDP 阻斷）
                new_vc = await target_channel.connect(reconnect=True, timeout=20.0, self_deaf=True)
                logger.info("語音非預期斷線自癒重連成功！(Guild: %s, Channel: %s)", guild_id, target_channel.name)

                # 發送提示通知
                if text_channel and hasattr(text_channel, "send"):
                    dur_text = self.format_duration(interrupted_offset) if interrupted_offset > 0 else "開頭"
                    song_name = interrupted_song.get("title", "當前曲目") if interrupted_song else "佇列曲目"
                    try:
                        await text_channel.send(
                            f"🔄 偵測到 Discord 語音連線異常抖動，名偵探橘雪莉已成功重連自癒！\n"
                            f"🎵 正在從 **{dur_text}** 處為您接續播放《**{song_name}**》...",
                            delete_after=10,
                        )
                    except Exception:
                        pass

                # 接續播放
                if fresh_guild and text_channel and hasattr(text_channel, "id"):
                    self._play_next(fresh_guild, text_channel)  # type: ignore[arg-type]
                return

            except Exception as e:
                logger.warning(
                    "自癒重連語音頻道嘗試 %d/%d 失敗: %s",
                    attempt + 1,
                    max_retries,
                    e,
                )

        # 多次嘗試皆宣告失敗，方才執行最終清理
        logger.error("語音非預期斷線自癒重試多次均告失敗，執行停止清理 (Guild: %s)", guild_id)
        if text_channel and hasattr(text_channel, "send"):
            try:
                await text_channel.send("❌ 語音通道多次嘗試自動重連失敗，已暫停播放。您可以使用 `/play resume` 隨時接續進度！")
            except Exception:
                pass
        await self.stop_player(guild_id, disconnect=False)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """處理當機器人離開語音頻道時的智慧自癒或資源清理。

        Args:
            member: 變更語音狀態的成員。
            before: 變更前的語音狀態。
            after: 變更後的語音狀態。
        """
        if self.bot.user and member.id == self.bot.user.id and before.channel and not after.channel:
            guild_id = member.guild.id
            if self._is_shutting_down:
                logger.info("機器人關機/自重啟中，保留音樂快照與狀態，略過 stop_player (Guild: %s)", guild_id)
                return

            # 若為使用者明確發起的 stop 指令或點擊停止按鈕，正常清理
            if self._is_stopping.get(guild_id, False):
                logger.info("偵測到雪莉已依指令離開語音頻道 (Guild: %s)，清理音樂狀態與佇列", guild_id)
                await self.stop_player(guild_id, disconnect=False)
                return

            # 若非使用者主動停止，且當前有活躍播放項目、佇列或處於循環模式，啟動非預期斷線自癒程序
            has_active_playback = bool(
                self.current_song.get(guild_id)
                or (guild_id in self.queues and len(self.queues[guild_id]) > 0)
                or self.loop_modes.get(guild_id, "none") != "none"
            )

            if has_active_playback:
                logger.warning(
                    "偵測到雪莉非預期離開語音頻道 (Guild: %s, Channel: %s)，啟動語音斷線自動重連自癒程序...",
                    guild_id,
                    getattr(before.channel, "name", "未知頻道"),
                )
                text_channel_id = self.last_text_channel_id.get(guild_id)
                text_channel = member.guild.get_channel(text_channel_id) if text_channel_id else None
                if not text_channel or not hasattr(text_channel, "send"):
                    text_channel = member.guild.system_channel or next(
                        (ch for ch in member.guild.text_channels if ch.permissions_for(member.guild.me).send_messages),
                        None,
                    )

                asyncio.create_task(
                    self._handle_unexpected_voice_disconnect(member.guild, before.channel, text_channel)
                )
                return

            logger.info("偵測到雪莉已離開語音頻道 (Guild: %s)，自動清理音樂狀態與佇列", guild_id)
            await self.stop_player(guild_id, disconnect=False)