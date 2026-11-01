"""
handlers/music/recovery_provider.py
===================================
音樂播放系統自癒與狀態快照提供者 (MusicRecoveryProvider)。
支援多伺服器狀態快照、FFmpeg -ss 斷點接播與舊控制卡片清理。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, TYPE_CHECKING

import discord

from core.concurrency import heavy_io_pool
from core.recovery.base import Recoverable, SnapshotMetadata
from handlers.music.config import FFMPEG_EXE_PATH, FFMPEG_OPTIONS, ytdl_client
from handlers.music.ui import MusicControlView
import settings

if TYPE_CHECKING:
    from handlers.music.cog import MusicHandler

logger = logging.getLogger(__name__)


class MusicRecoveryProvider(Recoverable):
    """音樂播放系統狀態快照提供者與自癒接續器。"""

    def __init__(self, music_handler: MusicHandler) -> None:
        self.music_handler = music_handler
        self._semaphore = asyncio.Semaphore(3)

    @property
    def recovery_identifier(self) -> str:
        return "music_player"

    @property
    def default_ttl_seconds(self) -> float:
        return float(getattr(settings, "MUSIC_RECOVERY_TTL_SECONDS", 1800.0))  # 預設 30 分鐘有效

    async def capture_recovery_snapshot(self) -> dict[str, Any] | None:
        """收集所有伺服器之音樂播放狀態與佇列。"""
        guilds: dict[str, Any] = {}
        all_guild_ids = set(self.music_handler.queues.keys()) | set(self.music_handler.current_song.keys())
        bot = self.music_handler.bot

        for gid in all_guild_ids:
            cur_song = self.music_handler.current_song.get(gid)
            q = self.music_handler.queues.get(gid, [])
            if not cur_song and not q:
                continue

            guild = bot.get_guild(gid)
            vc: Any = guild.voice_client if guild else None
            voice_channel_id = getattr(getattr(vc, "channel", None), "id", None)
            if not voice_channel_id:
                # 若語音連線因關機而先一步中斷，從快取找回
                voice_channel_id = self.music_handler.last_voice_channel_id.get(gid)
            if not voice_channel_id:
                # 若完全查無語音頻道 ID，略過此群
                continue

            msg = self.music_handler.current_message.get(gid)
            text_channel_id = getattr(getattr(msg, "channel", None), "id", None)
            if not text_channel_id:
                text_channel_id = self.music_handler.last_text_channel_id.get(gid)
            current_message_id = getattr(msg, "id", None)

            is_paused = bool(self.music_handler.is_paused.get(gid, False))
            offset = float(self.music_handler.get_elapsed_time(gid))
            loop_mode = self.music_handler.loop_modes.get(gid, "none")
            vol = getattr(settings, "MUSIC_DEFAULT_VOLUME", 1.2)

            guilds[str(gid)] = {
                "guild_id": gid,
                "voice_channel_id": voice_channel_id,
                "text_channel_id": text_channel_id,
                "current_message_id": current_message_id,
                "is_paused": is_paused,
                "current_song": cur_song,
                "playback_offset_seconds": offset,
                "queue": [dict(s) for s in q],
                "loop_mode": loop_mode,
                "volume": vol,
            }

        if not guilds:
            return None
        return {"guilds": guilds}

    async def restore_from_snapshot(
        self, snapshot_data: dict[str, Any], metadata: SnapshotMetadata
    ) -> None:
        """開機自癒時接收快照並執行多群並行恢復。"""
        now = time.time()
        age = now - metadata.snapshot_time
        if age > metadata.ttl_seconds:
            logger.info(
                "[MusicRecovery] 音樂快照已逾期 (中斷 %.1fs > TTL %.1fs)，略過語音自動重連。",
                age,
                metadata.ttl_seconds,
            )
            return

        guilds = snapshot_data.get("guilds", {})
        if not guilds:
            return

        tasks = [
            self._restore_single_guild(int(gid_str), gdata)
            for gid_str, gdata in guilds.items()
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _restore_single_guild(self, guild_id: int, gdata: dict[str, Any]) -> None:
        """單一伺服器的語音重連、面板清理與斷點續播。"""
        async with self._semaphore:
            bot = self.music_handler.bot
            guild = bot.get_guild(guild_id)
            if not guild:
                logger.warning("[MusicRecovery] 找不到伺服器 %s", guild_id)
                return

            voice_channel_id = gdata.get("voice_channel_id")
            text_channel_id = gdata.get("text_channel_id")
            current_message_id = gdata.get("current_message_id")
            current_song = gdata.get("current_song")
            offset_seconds = float(gdata.get("playback_offset_seconds", 0.0))
            queue = gdata.get("queue", [])
            loop_mode = gdata.get("loop_mode", "none")
            is_paused = bool(gdata.get("is_paused", False))

            # 1. 舊卡片清理
            if text_channel_id and current_message_id:
                text_ch = guild.get_channel(text_channel_id) or bot.get_channel(text_channel_id)
                if text_ch and hasattr(text_ch, "fetch_message"):
                    try:
                        old_msg = await text_ch.fetch_message(current_message_id)
                        await old_msg.delete()
                        logger.debug("[MusicRecovery] 成功刪除舊音樂卡片 (Message: %s)", current_message_id)
                    except discord.NotFound:
                        pass
                    except Exception as exc:
                        logger.debug("[MusicRecovery] 刪除舊音樂卡片失敗: %s", exc)

            # 2. 檢查語音頻道與在線成員
            if not voice_channel_id:
                return
            voice_ch = guild.get_channel(voice_channel_id) or bot.get_channel(voice_channel_id)
            if not voice_ch:
                logger.warning("[MusicRecovery] 找不到語音頻道 %s (Guild: %s)", voice_channel_id, guild_id)
                return

            members = getattr(voice_ch, "members", [])
            non_bots = [m for m in members if not getattr(m, "bot", False)]
            if not non_bots:
                logger.info(
                    "[MusicRecovery] 語音頻道 %s 全體成員已離線，不執行自癒連線 (Guild: %s)",
                    voice_ch.name,
                    guild_id,
                )
                if current_song and hasattr(self.music_handler, "save_checkpoint"):
                    self.music_handler.save_checkpoint(
                        guild_id,
                        current_song,
                        offset_seconds,
                        voice_channel_id=voice_channel_id,
                        text_channel_id=text_channel_id,
                    )
                return

            # 3. 重新連線語音
            vc: Any = guild.voice_client
            try:
                if vc is None:
                    vc = await voice_ch.connect(reconnect=True, timeout=20.0, self_deaf=True)
                elif getattr(vc, "channel", None) != voice_ch:
                    await vc.move_to(voice_ch)
            except Exception as exc:
                logger.error("[MusicRecovery] 重連語音頻道 %s 失敗: %s", voice_ch.name, exc)
                return

            # 4. 恢復佇列與設定
            self.music_handler.queues[guild_id] = list(queue)
            self.music_handler.loop_modes[guild_id] = loop_mode

            text_ch = None
            if text_channel_id:
                text_ch = guild.get_channel(text_channel_id) or bot.get_channel(text_channel_id)

            if not text_ch:
                logger.warning("[MusicRecovery] 找不到文字頻道 %s，略過面板發送", text_channel_id)
                return

            # 5. FFmpeg 斷點續播 (-ss) 或播放下一首
            if current_song:
                await self.resume_playback(
                    guild=guild,
                    text_channel=text_ch,
                    song=current_song,
                    offset_seconds=offset_seconds,
                    is_paused=is_paused,
                )
            elif queue:
                self.music_handler._play_next(guild, text_ch)

    async def resume_playback(
        self,
        guild: discord.Guild,
        text_channel: discord.TextChannel,
        song: dict[str, Any],
        offset_seconds: float,
        is_paused: bool = False,
    ) -> None:
        """以 FFmpeg -ss 斷點續播當前歌曲。"""
        guild_id = guild.id
        self.music_handler.current_song[guild_id] = song
        self.music_handler._is_stopping[guild_id] = False
        loop = asyncio.get_running_loop()

        target_url = song.get("webpage_url") or song.get("url")
        if not target_url:
            self.music_handler._play_next(guild, text_channel)
            return

        try:
            fresh_song: Any = await loop.run_in_executor(
                heavy_io_pool, lambda: ytdl_client.extract_info(target_url, download=False)
            )
            if not fresh_song or not isinstance(fresh_song, dict):
                raise ValueError("yt-dlp 解析失敗")

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

            if not true_audio_stream:
                raise ValueError("找不到音訊串流 URL")

            song["duration"] = song_data.get("duration", 0) or song.get("duration", 0) or 0
            song["thumbnail"] = song_data.get("thumbnail") or song.get("thumbnail")

            # 動態注入 FFmpeg -ss 參數
            base_before = str(FFMPEG_OPTIONS.get("before_options", "")).strip()
            seek_seconds = int(max(0.0, offset_seconds))
            seek_before = f"{base_before} -ss {seek_seconds}".strip() if seek_seconds > 0 else base_before

            raw_source = discord.FFmpegPCMAudio(
                true_audio_stream,
                executable=FFMPEG_EXE_PATH,
                before_options=seek_before,
                options=str(FFMPEG_OPTIONS.get("options", "")),
            )
            vol = getattr(settings, "MUSIC_DEFAULT_VOLUME", 1.2)
            source = discord.PCMVolumeTransformer(raw_source, volume=vol)

            after_playback = self.music_handler._create_after_playback(guild, text_channel, song)

            vc: Any = guild.voice_client
            if vc and hasattr(vc, "is_playing") and (vc.is_playing() or vc.is_paused()):
                self.music_handler._cleanup_guild_source(guild_id)
                vc.stop()
                await asyncio.sleep(0.05)

            self.music_handler.playback_start_time[guild_id] = time.time()
            self.music_handler.playback_accumulated_time[guild_id] = float(seek_seconds)
            self.music_handler.is_paused[guild_id] = is_paused
            self.music_handler.current_sources[guild_id] = source

            if vc and hasattr(vc, "play"):
                vc.play(source, after=after_playback)
                if is_paused and hasattr(vc, "pause"):
                    vc.pause()

            await self.music_handler._delete_song_message(guild_id)
            view = MusicControlView()
            embed = self.music_handler.build_playing_embed(guild_id)
            offset_str = self.music_handler.format_duration(seek_seconds)
            notice = f"🎵 **音樂接續播放**：雪莉已完成重啟，正在為大家接續播放《{song['title']}》({offset_str} 處)！"
            msg = await text_channel.send(content=notice, embed=embed, view=view)
            self.music_handler.current_message[guild_id] = msg
            self.music_handler._start_progress_updater(guild_id)

        except Exception as exc:
            logger.error("[MusicRecovery] 恢復播放歌曲失敗: %s", exc, exc_info=True)
            self.music_handler._play_next(guild, text_channel)
