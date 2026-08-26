import logging
import asyncio
from typing import Any
import discord
from discord import app_commands
from discord.ext import commands

from .stt import SpeechToText
from .tts import TextToSpeech
from .vad import VoiceActivityDetector
from .recorder import VoiceRecorder

from handlers.bot_brain import InferenceTask
from handlers.social.persona import build_dynamic_persona
import settings

logger = logging.getLogger(__name__)

class VoiceCog(commands.Cog):
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
        logger.info(f"啟動 Voice 監聽迴圈 (Guild: {guild_id})")
        while guild_id in self._active_channels:
            try:
                # 這裡會等待 recorder 收音
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
                        logger.info(f"STT 辨識結果 [{user_id}]: {text}")
                        
                        import re
                        # 檢查是否包含喚醒詞
                        match = re.search(r"(雪莉|雪麗|雪力)[,，、。\s]*(.*)", text, re.IGNORECASE)
                        if match:
                            command_text = match.group(2).strip()
                            if not command_text:
                                command_text = "（戳）"
                                
                            logger.info(f"偵測到喚醒詞，將作為指令處理: {command_text}")
                            
                            vc = self._active_channels.get(guild_id)
                            if vc and vc.channel:
                                channel = vc.channel
                                guild = self.bot.get_guild(guild_id)
                                author = guild.get_member(user_id) if guild else self.bot.get_user(user_id)
                                
                                if author:
                                    # 建立一個 MockMessage 模擬文字輸入
                                    class MockMessage:
                                        def __init__(self, author, channel, content, bot):
                                            self.author = author
                                            self.channel = channel
                                            self.content = content
                                            self.clean_content = content
                                            self.mentions = [bot.user]
                                            self.attachments = []
                                            self.reference = None
                                            self.id = 0
                                            
                                        async def reply(self, content=None, **kwargs):
                                            if content:
                                                # 1. 輸出文字到頻道
                                                sent_msg = await self.channel.send(f"{self.author.mention} {content}")
                                                
                                                # 2. 觸發語音 TTS
                                                tts_cog = bot.get_cog("VoiceCog")
                                                if tts_cog and getattr(tts_cog, "_tts", None):
                                                    clean_reply = re.sub(r"<\/?thought>", "", content).strip()
                                                    # 移除可能影響語音的 Markdown
                                                    clean_reply = re.sub(r"[*_`~#]+", "", clean_reply)
                                                    if clean_reply:
                                                        audio_data = await tts_cog._tts.synthesize(clean_reply)
                                                        if audio_data:
                                                            await tts_cog._play_audio(guild_id, audio_data)
                                                return sent_msg
                                                
                                        async def add_reaction(self, emoji):
                                            pass
                                            
                                    mock_msg = MockMessage(author, channel, command_text, self.bot)
                                    from handlers.social.router import route_and_execute_tag
                                    
                                    # 將這個語音指令直接丟進原本的文字處理路由 (支援所有工具如排程、畫圖、音樂等)
                                    await route_and_execute_tag(self.bot, mock_msg)  # type: ignore[arg-type]
                        else:
                            # 沒聽到雪莉，當作背景音略過
                            pass
                            
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"語音迴圈發生錯誤: {e}")
                await asyncio.sleep(1)

    async def _play_audio(self, guild_id: int, audio_bytes: bytes) -> None:
        vc = self._active_channels.get(guild_id)
        if not vc:
            return
            
        import tempfile, os
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as f:
            f.write(audio_bytes)
            tmp_name = f.name
            
        try:
            source = discord.FFmpegPCMAudio(tmp_name)
            vc.play(source)
            while vc.is_playing():
                await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"語音播放失敗: {e}")
        finally:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        # 處理如果機器人被強制踢除，要清空資源
        if member.id == self.bot.user.id and before.channel and not after.channel:
            guild_id = member.guild.id
            if guild_id in self._active_channels:
                self._active_channels.pop(guild_id)
                if self._stt: self._stt.unload()
                if self._tts: self._tts.unload()
                self._stt = None
                self._tts = None
                brain = self.bot.get_cog("BotBrain")
                if brain:
                    brain._voice_mode_active = False
