"""handlers/context_menu/cog.py
==============================
Discord 右鍵訊息應用程式指令 (Message Context Menu Commands) 適配模組。

將 Discord 訊息快捷點擊動作轉化為標準 SherryRequest，
並全數交由 SherryCore 中央微核心統籌調度與限流守衛。
包含三大高頻快捷功能：
1. 雪莉摘要 (自動支援純文字、網址/YouTube、圖片/報錯截圖多模態分析)
2. 雪莉延伸發問 (彈出 Modal UI 視窗進行深度追問)
3. 翻譯這則訊息 (即時多國語言翻譯為繁體中文)
"""

from __future__ import annotations

import logging
from typing import Any
import discord
from discord import app_commands
from discord.ext import commands

from core.contracts import SherryRequest
from handlers.social.observability import new_trace_id
import settings

logger = logging.getLogger(__name__)


class ExtendAskModal(discord.ui.Modal, title="雪莉延伸發問"):
    """右鍵選單延伸追問彈出式表單視窗。"""

    question_input: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
        label="請輸入您的延伸問題",
        style=discord.TextStyle.paragraph,
        placeholder="例如：那後天的天氣呢？／這段程式碼有什麼潛在問題？",
        required=True,
        max_length=1000,
    )

    def __init__(self, cog: ContextMenuCog, target_message: discord.Message) -> None:
        """初始化延伸發問 Modal。

        Args:
            cog: ContextMenuCog 實例。
            target_message: 使用者選取的目標訊息。
        """
        super().__init__()
        self.cog = cog
        self.target_message = target_message

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """使用者送出表單時的回呼處理。

        Args:
            interaction: Discord Interaction 實例。
        """
        user_question = str(self.question_input.value).strip()
        if not user_question:
            await interaction.response.defer(ephemeral=self.cog.is_ephemeral)
            await interaction.followup.send("⚠️ 延伸問題不能為空喔！", ephemeral=self.cog.is_ephemeral)
            return

        # 嘗試擷取該頻道上下文脈絡（若可用）
        context_history = ""
        try:
            from handlers.social.utils import fetch_context_script
            if hasattr(self.target_message, "channel"):
                context_history = await fetch_context_script(
                    self.target_message.channel, self.target_message, bot=self.cog.bot
                )
        except Exception as exc:
            logger.debug("ContextMenuCog: 抓取頻道上下文時發生非致命例外 (略過): %s", exc)

        await self.cog._dispatch_to_core(
            interaction=interaction,
            message=self.target_message,
            action="extend_ask",
            title_prefix="💬 **【雪莉延伸回答】**",
            extra_options={
                "question": user_question,
                "context_history": context_history,
                "user_name": interaction.user.display_name,
            },
        )


class ContextMenuCog(commands.Cog):
    """右鍵訊息快捷指令 Cog。"""

    @property
    def is_ephemeral(self) -> bool:
        """依據 settings.CONTEXT_MENU_EPHEMERAL 決定是否私密回覆（預設 False 為全公開發布）。"""
        return bool(getattr(settings, "CONTEXT_MENU_EPHEMERAL", False))

    def __init__(self, bot: commands.Bot) -> None:
        """初始化 ContextMenuCog 並向 bot 指令樹註冊快捷選單。

        Args:
            bot: Discord Bot 實例。
        """
        self.bot = bot

        # 1. 建立 3 個精選 Message Context Menu 指令
        self.ctx_summarize = app_commands.ContextMenu(
            name="雪莉摘要",
            callback=self.handle_summarize_context,
        )
        self.ctx_extend_ask = app_commands.ContextMenu(
            name="雪莉延伸發問",
            callback=self.handle_extend_ask_context,
        )
        self.ctx_translate = app_commands.ContextMenu(
            name="翻譯這則訊息",
            callback=self.handle_translate_context,
        )

        # 2. 加入 bot 指令樹
        self.bot.tree.add_command(self.ctx_summarize)
        self.bot.tree.add_command(self.ctx_extend_ask)
        self.bot.tree.add_command(self.ctx_translate)
        logger.info("ContextMenuCog: 已註冊 3 個訊息快捷選單指令 (雪莉摘要/延伸發問/翻譯)")

    async def cog_unload(self) -> None:
        """Cog 卸載時從指令樹中移除快捷指令，避免殘留。"""
        self.bot.tree.remove_command(self.ctx_summarize.name, type=self.ctx_summarize.type)
        self.bot.tree.remove_command(self.ctx_extend_ask.name, type=self.ctx_extend_ask.type)
        self.bot.tree.remove_command(self.ctx_translate.name, type=self.ctx_translate.type)
        logger.info("ContextMenuCog: 已卸載訊息快捷選單指令。")

    # ------------------------------------------------------------------
    # 內部共用調度核心
    # ------------------------------------------------------------------

    async def _dispatch_to_core(
        self,
        interaction: discord.Interaction,
        message: discord.Message,
        action: str,
        title_prefix: str,
        extra_options: dict[str, Any] | None = None,
    ) -> None:
        """統一將 Discord 訊息事件封裝為 SherryRequest 並交由 SherryCore 執行。

        Args:
            interaction: Discord Interaction 物件。
            message: 使用者右鍵點選的目標訊息。
            action: 動作識別名稱 (summarize / translate / vision / url_summarize)。
            title_prefix: 回傳面板標題前綴。
            extra_options: 額外參數字典。
        """
        # 1. 第一時間發送 Defer，防範 3 秒逾時（由 settings.CONTEXT_MENU_EPHEMERAL 控制是否私密，預設 False 為公開）
        ephemeral = self.is_ephemeral
        await interaction.response.defer(ephemeral=ephemeral)

        target_text = message.clean_content or message.content or ""
        attachments = [
            att.url
            for att in message.attachments
            if att.url and (att.content_type is None or att.content_type.startswith("image/"))
        ]

        options: dict[str, Any] = {
            "action": action,
            "interaction": interaction,
        }
        if extra_options:
            options.update(extra_options)

        # 2. 封裝為標準微核心請求
        req = SherryRequest(
            trace_id=new_trace_id("ctx"),
            source_type="context_menu",
            user_id=interaction.user.id,
            user_name=interaction.user.display_name,
            guild_id=interaction.guild_id or 0,
            channel_id=interaction.channel_id or 0,
            raw_content=target_text,
            attachments=attachments,
            message_ref=message,
            options=options,
        )

        core: Any = getattr(self.bot, "core", None)
        if not core:
            await interaction.followup.send("❌ 雪莉中央微核心未就緒，請確認 Bot 啟動狀態。", ephemeral=ephemeral)
            return

        try:
            # 3. 委派中央調度
            resp = await core.handle_request(req)

            # 4. 回傳回覆內容
            prefix = f"{title_prefix}\n\n" if resp.success else ""
            reply_content = f"{prefix}{resp.reply_text}"
            await interaction.followup.send(reply_content, ephemeral=ephemeral)

        except Exception as exc:
            logger.error("ContextMenuCog: 執行 %s 失敗: %s", action, exc, exc_info=True)
            await interaction.followup.send(f"❌ 處理訊息時發生未預期錯誤：{exc}", ephemeral=ephemeral)

    # ------------------------------------------------------------------
    # 三大右鍵動作回呼
    # ------------------------------------------------------------------

    async def handle_summarize_context(
        self, interaction: discord.Interaction, message: discord.Message
    ) -> None:
        """對訊息進行全能多模態智能速讀與摘要（自動支援純文字、網址/YouTube、圖片/報錯截圖）。"""
        content = (message.clean_content or message.content or "").strip()
        image_attachments = [
            att for att in message.attachments
            if att.url and (att.content_type is None or att.content_type.startswith("image/"))
        ]

        if not content and not image_attachments and not message.attachments:
            await interaction.response.defer(ephemeral=self.is_ephemeral)
            await interaction.followup.send("⚠️ 此訊息無任何文字、網址或圖片附件可供分析！", ephemeral=self.is_ephemeral)
            return

        first_image_url = ""
        mime_type = "image/png"
        if image_attachments:
            first_image_url = image_attachments[0].url
            mime_type = image_attachments[0].content_type or "image/png"
        elif message.attachments:
            first_image_url = message.attachments[0].url

        extra_options: dict[str, Any] = {}
        if first_image_url:
            extra_options["image_url"] = first_image_url
            extra_options["mime_type"] = mime_type

        await self._dispatch_to_core(
            interaction=interaction,
            message=message,
            action="summarize",
            title_prefix="📄 **【雪莉摘要】**",
            extra_options=extra_options if extra_options else None,
        )

    async def handle_extend_ask_context(
        self, interaction: discord.Interaction, message: discord.Message
    ) -> None:
        """彈出表單視窗，供使用者針對目標訊息進行延伸發問與追問。"""
        modal = ExtendAskModal(cog=self, target_message=message)
        await interaction.response.send_modal(modal)

    async def handle_translate_context(
        self, interaction: discord.Interaction, message: discord.Message
    ) -> None:
        """將訊息即時翻譯為專業繁體中文。"""
        content = message.clean_content or message.content or ""
        if not content.strip():
            await interaction.response.defer(ephemeral=self.is_ephemeral)
            await interaction.followup.send("⚠️ 此訊息無文字內容可供翻譯！", ephemeral=self.is_ephemeral)
            return

        await self._dispatch_to_core(
            interaction=interaction,
            message=message,
            action="translate",
            title_prefix="🌐 **【訊息翻譯 (繁中)】**",
            extra_options={"target_lang": "繁體中文"},
        )


