from __future__ import annotations

import logging
import re
from typing import Any

import discord
from discord.ext import commands

import settings
from handlers.bot_brain import InferenceTask
from handlers.social.persona import build_dynamic_persona

logger = logging.getLogger(__name__)


class WelcomeCog(commands.Cog):
    """新進成員歡迎模組，動態生成符合雪莉人設的歡迎問候語。"""

    def __init__(self, bot: commands.Bot) -> None:
        """初始化 WelcomeCog 模組。

        Args:
            bot: Discord Bot 實例。
        """
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """監聽成員加入伺服器事件並生成歡迎詞。"""
        # 尋找伺服器預設的系統頻道，或名稱為 general, 閒聊, 聊天 的頻道
        channel = member.guild.system_channel
        if not channel:
            for c in member.guild.text_channels:
                if any(x in c.name.lower() for x in ["general", "閒聊", "聊天", "大廳", "welcome", "歡迎"]):
                    channel = c
                    break

        if not channel:
            return

        brain: Any = self.bot.get_cog("BotBrain")
        if not brain:
            await channel.send(f"歡迎 {member.mention} 來到 **{member.guild.name}**！")
            return

        # 動態生成帶有雪莉人設的歡迎詞
        head_persona, _tail = build_dynamic_persona("", "CHAT", channel_id=channel.id)
        sys_prompt = (
            f"{head_persona}\n\n"
            "【任務】\n"
            f"有一個新的成員「{member.display_name}」剛剛加入了「{member.guild.name}」伺服器。\n"
            "請用妳的人設向他打招呼，並簡單介紹妳自己是誰、可以做什麼（例如畫圖、播音樂、聊天）。\n"
            "【規則】\n"
            "1. 必須 @ 對方 (使用 {mention} 佔位符，我會在程式中替換)。\n"
            "2. 長度適中，展現出符合妳當前人設的性格特質與語氣風格。\n"
            "3. 只輸出妳說的話，不要帶引號或多餘前綴。"
        )

        user_instruct = f"新成員 {member.display_name} 加入了伺服器！請說些歡迎詞吧。"

        async def _on_welcome_reply(reply: str) -> None:
            clean_reply = re.sub(r"(?is)<thought>.*?</thought>", "", reply).strip()
            if "{mention}" in clean_reply:
                clean_reply = clean_reply.replace("{mention}", member.mention)
            else:
                clean_reply = f"{member.mention} {clean_reply}"

            await channel.send(clean_reply)

        task = InferenceTask(
            priority=settings.PRIORITY_TAG_RESPONSE,
            system_prompt=sys_prompt,
            user_prompt=user_instruct,
            callback=_on_welcome_reply,
            model=settings.PRIMARY_MODEL,
            task_id=f"welcome-{member.id}",
        )
        await brain.enqueue_task(task)


async def setup(bot: commands.Bot) -> None:
    """載入 WelcomeCog 模組。"""
    await bot.add_cog(WelcomeCog(bot))
