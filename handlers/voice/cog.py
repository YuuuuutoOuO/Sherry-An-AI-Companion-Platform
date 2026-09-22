from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from handlers.social.router import route_and_execute_tag
from .recorder import VoiceRecorder
from .stt import SpeechToText
from .tts import TextToSpeech
from .vad import VoiceActivityDetector

logger = logging.getLogger(__name__)

_WAKE_WORD_PATTERN = re.compile(r"(雪莉|雪麗|雪力)[,，、。\s]*(.*)", re.IGNORECASE)


class VoiceProxyMessage:
    """模擬 Discord Message 以供文字路由器 (route_and_execute_tag) 處理語音指令。"""

    def __init__(
        self,
        author: discord.Member | discord.User,
        channel: discord.abc.Messageable,
        content: str,
        bot: commands.Bot,
        guild_id: int,
    ) -> None:
        """初始化語音代理訊息。

        Args:
            author: 發話之 Discord 使用者。
            channel: 目標訊息頻道。
            content: 語音識別後之文字內容。
            bot: Discord Bot 實例。
            guild_id: Discord 伺服器 ID。
        """
        self.author = author
        self.channel = channel
        self.content = content
        self.clean_content = content
        self.mentions = [bot.user] if bot.user else []
        self.attachments: list[Any] = []
        self.reference = None
        self.id = 0
        self._bot = bot
        self._guild_id = guild_id

    async def reply(self, content: str | None = None, **kwargs: Any) -> discord.Message | None:
        """回覆訊息並自動發動語音合成 (TTS) 與播放。

        Args:
            content: 回覆文字內容。
            **kwargs: 其他額外參數。

        Returns:
            discord.Message | None: 發送出的 Discord 訊息物件。
        """
        if not content:
            return None

        # 1. 輸出文字到頻道
        sent_msg = await self.channel.send(f"{self.author.mention} {content}")

        # 2. 觸發語音 TTS
        tts_cog: Any = self._bot.get_cog("VoiceCog")
        if tts_cog and getattr(tts_cog, "_tts", None):
            clean_reply = re.sub(r"(?is)<thought>.*?</thought>", "", content).strip()
            # 移除可能影響語音的 Markdown 符號
            clean_reply = re.sub(r"[*_`~#]+", "", clean_reply)
            if clean_reply:
                audio_data = await tts_cog._tts.synthesize(clean_reply)
                if audio_data:
                    await tts_cog._play_audio(self._guild_id, audio_data)
        return sent_msg

    async def add_reaction(self, emoji: str) -> None:
        """忽略反應。"""
        pass


class VoiceCog(commands.Cog):
    """語音互動處理模組，負責加入語音頻道、VAD 語音活動檢測、STT 識別與 TTS 播報。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._stt: SpeechToText | None = None
        self._tts: TextToSpeech | None = None
        self._vad = VoiceActivityDetector()
        self._recorder: VoiceRecorder | None = None
        self._active_channels: dict[int, discord.VoiceClient] = {}

    @app_commands.command(name="join_voice", description="加入語音頻道開始交談")
    async def join_voice(self, interaction: discord.Interaction) -> None:
        user = interaction.user
        voice_state = getattr(user, "voice", None)
        if not voice_state or not voice_state.channel:
            await interaction.response.send_message("你必須先加入語音頻道！")
            return
            
        channel = voice_state.channel
        guild_id = interaction.guild_id
        if not guild_id:
            await interaction.response.send_message("❌ 請在伺服器中使用此指令！")
            return

        await interaction.response.send_message(f"準備進入 {getattr(channel, 'name', '語音頻道')}，正在加載語音模型...")
        
        # 標記 VRAM 狀態，關閉 SD
        brain: Any = self.bot.get_cog("BotBrain")
        if brain:
            brain._voice_mode_active = True
            
        # 加載龐大的 ML 模型
        self._stt = SpeechToText()
        self._tts = TextToSpeech()
        self._recorder = VoiceRecorder(self._vad)
        
        try:
            vc = await channel.connect()
            self._active_channels[guild_id] = vc
            asyncio.create_task(self._listen_loop(guild_id))
            logger.info(f"已加入語音頻道: {getattr(channel, 'name', guild_id)}")
        except Exception as e:
            logger.error(f"加入語音頻道失敗: {e}")
            if brain:
                brain._voice_mode_active = False

    @app_commands.command(name="leave_voice", description="離開語音頻道並釋放 VRAM")
    async def leave_voice(self, interaction: discord.Interaction) -> None:
        guild_id = interaction.guild_id
        if guild_id and guild_id in self._active_channels:
            vc = self._active_channels.pop(guild_id)
            await vc.disconnect()
            
            # 釋放模型佔用的 VRAM
            if self._stt:
                self._stt.unload()
                self._stt = None
            if self._tts:
                self._tts.unload()
                self._tts = None
                
            brain: Any = self.bot.get_cog("BotBrain")
            if brain:
                brain._voice_mode_active = False
                
            await interaction.response.send_message("已離開語音頻道，並釋放神經網路資源。")
        else:
            await interaction.response.send_message("我目前不在語音頻道裡喔！")

    async def _listen_loop(self, guild_id: int) -> None:
        logger.info("啟動 Voice 監聽迴圈 (Guild: %d)", guild_id)
        while guild_id in self._active_channels:
            try:
                if not self._recorder:
                    await asyncio.sleep(1)
                    continue

                result = await self._recorder.get_next_utterance()
                if not result:
                    await asyncio.sleep(0.5)
                    continue

                user_id, audio_bytes = result

                if self._stt:
                    text = await self._stt.transcribe(audio_bytes)
                    if text:
                        logger.info("STT 辨識結果 [%s]: %s", user_id, text)

                        match = _WAKE_WORD_PATTERN.search(text)
                        if match:
                            command_text = match.group(2).strip()
                            if not command_text:
                                command_text = "（戳）"

                            logger.info("偵測到喚醒詞，將作為指令處理: %s", command_text)

                            vc = self._active_channels.get(guild_id)
                            if vc and vc.channel:
                                channel = vc.channel
                                guild = self.bot.get_guild(guild_id)
                                author = guild.get_member(user_id) if guild else self.bot.get_user(user_id)

                                if author:
                                    proxy_msg = VoiceProxyMessage(author, channel, command_text, self.bot, guild_id)
                                    await route_and_execute_tag(self.bot, proxy_msg)  # type: ignore[arg-type]

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("語音迴圈發生錯誤: %s", e)
                await asyncio.sleep(1)

    async def _play_audio(self, guild_id: int, audio_bytes: bytes) -> None:
        """在指定伺服器的語音頻道播放合成音訊。"""
        vc = self._active_channels.get(guild_id)
        if not vc:
            return

        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as f:
            f.write(audio_bytes)
            tmp_name = f.name

        try:
            source = discord.FFmpegPCMAudio(tmp_name)
            vc.play(source)
            while vc.is_playing():
                await asyncio.sleep(0.1)
        except Exception as e:
            logger.error("語音播放失敗: %s", e)
        finally:
            if os.path.exists(tmp_name):
                try:
                    os.remove(tmp_name)
                except OSError as err:
                    logger.debug("刪除臨時語音檔案失敗: %s", err)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """處理若機器人被踢出語音頻道時釋放神經網路資源。"""
        if self.bot.user and member.id == self.bot.user.id and before.channel and not after.channel:
            guild_id = member.guild.id
            if guild_id in self._active_channels:
                self._active_channels.pop(guild_id)
                if self._stt:
                    self._stt.unload()
                if self._tts:
                    self._tts.unload()
                self._stt = None
                self._tts = None
                brain: Any = self.bot.get_cog("BotBrain")
                if brain:
                    brain._voice_mode_active = False
