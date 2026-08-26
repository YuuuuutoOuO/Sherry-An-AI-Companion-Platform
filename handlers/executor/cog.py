"""
handlers/executor/cog.py
=======================
強制指定意圖類型執行 Prompt 的測試指令 /execute。
繞過 LLM 分類器，直接調用對應 Tool 的 execute()。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

import settings
from handlers.bot_brain import BotBrain
from handlers.social.registry import TOOL_LIST, get_fallback_tool
from handlers.social.utils import fetch_context_script

logger = logging.getLogger(__name__)

# 動態從 TOOL_LIST 構建 intent choices，避免硬編碼
_INTENT_CHOICES = [
    app_commands.Choice(name=f"{t.intent_name} ({t.llm_description[:40]}{'…' if len(t.llm_description) > 40 else ''})", value=t.intent_name)
    for t in TOOL_LIST
]


@dataclass
class _FakeMessage:
    """
    模擬 discord.Message 的最小包裝，供 tool.execute() 使用。
    將 reply() 委派給 interaction.followup.send()。
    """

    channel: discord.TextChannel
    author: discord.Member | discord.User
    clean_content: str
    id: int
    _interaction: discord.Interaction

    @property
    def guild(self) -> discord.Guild:
        """委派至 channel.guild，許多 tool 會存取此屬性。"""
        return self.channel.guild

    async def reply(self, content: str | None = None, *, embed: discord.Embed | None = None, mention_author: bool = False) -> Any:
        """將 reply 轉發至 interaction.followup.send()。"""
        try:
            kwargs: dict[str, Any] = {}
            if content is not None:
                kwargs["content"] = content
            if embed is not None:
                kwargs["embed"] = embed
            return await self._interaction.followup.send(**kwargs)
        except discord.HTTPException as e:
            logger.error("FakeMessage.reply failed: %s", e)
            return None

    async def add_reaction(self, emoji: str) -> None:
        """工具可能嘗試添加反應，靜默忽略（interaction 不支援）。"""
        pass


class ExecutorCog(commands.Cog):
    """提供 /execute 強制意圖測試指令。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="execute",
        description="強制指定意圖類型執行 Prompt（繞過分類器，測試用）",
    )
    @app_commands.describe(
        intent="要強制執行的意圖類型",
        content="要傳入的 Prompt 內容",
    )
    @app_commands.choices(intent=_INTENT_CHOICES)
    async def execute_intent(
        self,
        interaction: discord.Interaction,
        intent: str,
        content: str,
    ) -> None:
        """
        根據使用者選擇的 intent，直接調用對應 Tool 執行 content。
        完全繞過 route_and_execute_tag() 的 LLM 分類流程。
        """
        brain: Any = self.bot.get_cog("BotBrain")
        if not brain:
            await interaction.response.send_message(
                "❌ BotBrain 未加載，無法執行任何意圖！",
                ephemeral=True,
            )
            return

        # ── 查找對應 Tool ──
        target_tool = get_fallback_tool()
        for tool in TOOL_LIST:
            if tool.intent_name == intent:
                target_tool = tool
                break

        if target_tool.intent_name != intent and intent != "CHAT":
            # 理論上不應該發生，因為 choices 來自 TOOL_LIST
            await interaction.response.send_message(
                f"❌ 找不到意圖類型 `{intent}`！可用類型：{', '.join(t.intent_name for t in TOOL_LIST)}",
                ephemeral=True,
            )
            return

        # ── 延遲回應，準備執行 ──
        await interaction.response.defer(thinking=True)

        # ── 構建 Context（與 route_and_execute_tag 邏輯一致）──
        if isinstance(interaction.channel, discord.TextChannel):
            channel = interaction.channel
        else:
            await interaction.followup.send("❌ 此指令僅限在文字頻道中使用！")
            return

        context_script = await fetch_context_script(channel, None)
        trigger_line = (
            f"🎯 【強制測試指令 /execute [{intent}]】："
            f"[{interaction.user.display_name}] 正在測試：\u201c{content}\u201d"
        )
        full_context = f"{context_script}\n\n{trigger_line}" if context_script else trigger_line

        vibe_mgr: Any = self.bot.get_cog("VibeManager")
        vibe_snippet = vibe_mgr.get_top_topics_snippet(channel.id) if vibe_mgr else ""

        mem_mgr: Any = self.bot.get_cog("MemoryManager")
        if mem_mgr:
            user_long_memory = mem_mgr.get_user_memory_snippet(interaction.user.id)
            if user_long_memory:
                full_context = (
                    f"🧠【全局跨頻跨世界通透：私心調檔案專屬解析】\n"
                    f"{user_long_memory}\n\n"
                    f"==== 👇 (現實當前情況紀錄區群聚發展之境象段：依循往下照看與接連！！) 👇=====\n\n"
                    f"{full_context}"
                )

        # ── 構建模擬 Message ──
        fake_message = _FakeMessage(
            channel=channel,
            author=interaction.user,
            clean_content=content,
            id=interaction.id,
            _interaction=interaction,
        )

        # ── 直接調用 Tool.execute()，繞過路由器 ──
        logger.info(
            "executor: [/execute] 強制執行 intent=%s, content=%r, user=%s",
            intent, content, interaction.user.display_name,
        )

        try:
            await target_tool.execute(
                bot=self.bot,
                message=fake_message,  # type: ignore[arg-type]
                user_query=content,
                vibe_snippet=vibe_snippet,
                full_context=full_context,
            )
        except Exception as e:
            logger.error("executor: Tool %s 執行失敗: %s", intent, e, exc_info=True)
            await interaction.followup.send(f"❌ 執行 `{intent}` 時發生錯誤：{e}")