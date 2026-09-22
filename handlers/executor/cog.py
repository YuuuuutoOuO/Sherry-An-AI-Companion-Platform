"""handlers/executor/cog.py
=======================
雪莉全能互動測試中樞預覽版 (Command Hub Preview) /execute。
支援互動式下拉選單動態預覽各模組面板，並保留強制指定意圖執行 Prompt 的測試能力。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from handlers.social.registry import TOOL_LIST, get_fallback_tool
from handlers.social.utils import fetch_context_script, is_admin_user

logger = logging.getLogger(__name__)

ADMIN_ONLY_INTENTS: set[str] = {
    "ADMIN",
    "RELOAD",
    "SYNC",
    "SYSTEM_SYNC",
    "MEMORY_CLEAR",
    "VIBE_CLEAR",
    "DEBUG",
}


# 動態從 TOOL_LIST 構建 intent choices
_INTENT_CHOICES = [
    app_commands.Choice(
        name=f"{t.intent_name} ({t.llm_description[:35]}{'…' if len(t.llm_description) > 35 else ''})",
        value=t.intent_name,
    )
    for t in TOOL_LIST
]


@dataclass
class _FakeMessage:
    """模擬 discord.Message 的最小包裝，供 tool.execute() 使用。"""

    channel: discord.TextChannel
    author: discord.Member | discord.User
    clean_content: str
    id: int
    _interaction: discord.Interaction

    @property
    def guild(self) -> discord.Guild:
        """委派至 channel.guild。"""
        return self.channel.guild

    async def reply(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        mention_author: bool = False,
    ) -> Any:
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
        """忽略反應。"""
        pass


# ===========================================================================
# 1. 意圖直通測試 Modal
# ===========================================================================

class PromptTestModal(discord.ui.Modal, title="🧪 意圖直通與 Prompt 測試"):
    """彈出式 Prompt 測試表單。"""

    intent_input: Any = discord.ui.TextInput(
        label="意圖類型 (如 CHAT, STOCK_QUERY, WEATHER)",
        placeholder="例如 CHAT, STOCK_QUERY, WEATHER_FORECAST",
        default="CHAT",
        min_length=2,
        max_length=40,
        required=True,
    )
    prompt_input: Any = discord.ui.TextInput(
        label="測試 Prompt 內容",
        style=discord.TextStyle.paragraph,
        placeholder="輸入欲傳入該意圖 Tool 執行的文字內容...",
        min_length=1,
        max_length=500,
        required=True,
    )

    def __init__(self, bot: commands.Bot) -> None:
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """執行指定的 Prompt。"""
        intent = self.intent_input.value.strip().upper()
        content = self.prompt_input.value.strip()

        # 敏感管理意圖權限檢查
        if intent in ADMIN_ONLY_INTENTS and not is_admin_user(interaction):
            await interaction.response.send_message(
                f"🔒 **權限不足**：意圖 `{intent}` 屬於敏感管理操作，僅限管理員執行！",
                ephemeral=True,
            )
            return

        target_tool = get_fallback_tool()
        for tool in TOOL_LIST:
            if tool.intent_name.upper() == intent:
                target_tool = tool
                break

        await interaction.response.defer(thinking=True)
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.followup.send("❌ 請在文字頻道中執行！")
            return

        context_script = await fetch_context_script(interaction.channel, None, bot=self.bot)
        trigger_line = f"🎯 【互動中樞測試 [{intent}]】：[{interaction.user.display_name}] 正在測試：\u201c{content}\u201d"
        full_context = f"{context_script}\n\n{trigger_line}" if context_script else trigger_line

        fake_message = _FakeMessage(
            channel=interaction.channel,
            author=interaction.user,
            clean_content=content,
            id=interaction.id,
            _interaction=interaction,
        )

        try:
            await target_tool.execute(
                bot=self.bot,
                message=fake_message,  # type: ignore[arg-type]
                user_query=content,
                vibe_snippet="",
                full_context=full_context,
            )
        except Exception as e:
            logger.error("executor modal error: %s", e, exc_info=True)
            await interaction.followup.send(f"❌ 執行 `{intent}` 時發生錯誤：{e}")


# ===========================================================================
# 2. 全能互動測試中樞 View
# ===========================================================================

class CommandHubCategorySelect(discord.ui.Select):
    """功能領域分類切換選單。"""

    def __init__(self, parent_view: CommandHubView) -> None:
        self.parent_view = parent_view
        options = [
            discord.SelectOption(
                label="📡 Spark 條件推播系統",
                value="spark",
                description="價格警報、定時排程、RSS/YT 訂閱與天氣預警",
                emoji="📡",
            ),
            discord.SelectOption(
                label="🐉 D&D 跑團冒險系統",
                value="dnd",
                description="冒險者角色卡、擲骰判定、向 DM 提出行動與世界時間",
                emoji="🐉",
            ),
            discord.SelectOption(
                label="🎵 音樂播放系統",
                value="music",
                description="留聲機主控面板、暫停/播放、切歌、循環模式與點歌",
                emoji="🎵",
            ),
            discord.SelectOption(
                label="🎬 直播大螢幕控制",
                value="stream",
                description="播放本機影片庫、YouTube 串流與播放進度管理 (Port 8764)",
                emoji="🎬",
            ),
            discord.SelectOption(
                label="📈 股市與行情分析",
                value="market",
                description="台美股與加密貨幣即時行情、技術指標報告與卡價",
                emoji="📈",
            ),
            discord.SelectOption(
                label="🎨 專屬畫家與繪圖",
                value="draw",
                description="調用筆刷調色盤、數位畫布與 AI 圖像生成",
                emoji="🎨",
            ),
            discord.SelectOption(
                label="🔍 深度聯網研究",
                value="deep_search",
                description="多輪自主網路資料探勘與論文級報告撰寫",
                emoji="🔍",
            ),
            discord.SelectOption(
                label="⚙️ 系統管理與可觀測性",
                value="admin",
                description="記憶庫管理、話題氛圍檢視、硬體統計與指令熱重載",
                emoji="⚙️",
            ),
            discord.SelectOption(
                label="🧪 意圖直通與 Prompt 測試",
                value="intent",
                description="直接指定 Tool 意圖並輸入 Prompt 進行測試",
                emoji="🧪",
            ),
        ]
        super().__init__(
            placeholder="請選擇欲體驗或測試的功能模組...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """選取分類後切換主面板顯示。"""
        selected = self.values[0]
        self.parent_view.current_category = selected
        embed = self.parent_view.build_preview_embed(selected)
        await interaction.response.edit_message(embed=embed, view=self.parent_view)


class CommandHubView(discord.ui.View):
    """雪莉全能互動測試中樞 View。"""

    def __init__(self, bot: commands.Bot, author_id: int | None = None) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.author_id = author_id
        self.message: discord.Message | None = None
        self.current_category: str = "spark"
        self.add_item(CommandHubCategorySelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """限制僅指令發起者可點擊操作。"""
        if self.author_id and interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 這是其他人的控制面板，你不能點選喔！", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        """超時時自動將元件設為 disabled，防止過期點擊。"""
        for item in self.children:
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

    def build_preview_embed(self, category: str) -> discord.Embed:
        """根據選定分類產生預覽說明 Embed。"""
        if category == "spark":
            embed = discord.Embed(
                title="📡 預覽：Spark 泛用條件推播系統",
                description=(
                    "**核心理念**：透過事件驅動在達到特定條件時主動推播通知至 Discord 頻道。\n\n"
                    "• 💰 **價格監控**：支援高於、低於、向上突破指定股價或加密幣價格。\n"
                    "• ⏰ **定時排程**：支援固定間隔分鐘或每日指定時刻提醒。\n"
                    "• 📰 **RSS / YT 訂閱**：監聽新文章或 YouTube 最新影片並支援關鍵字過濾。\n"
                    "• 🌤️ **天氣預警**：即時降雨警報、高低溫特報與每日氣象概況。"
                ),
                color=discord.Color.gold(),
            )
            embed.set_footer(text="點擊下方 [ 🚀 開啟該模組主控台 ] 即可直接掛載 Spark 互動介面！")

        elif category == "dnd":
            embed = discord.Embed(
                title="🐉 預覽：D&D 跑團冒險中樞",
                description=(
                    "**核心理念**：由橘雪莉化身 AI 地下城主 (DM) 為玩家敘事，支援擲骰與狀態模擬。\n\n"
                    "• 👤 **創建角色**：支援人類、精靈、矮人等種族與戰士、法師、遊俠等職業。\n"
                    "• 🎲 **擲骰判定**：支援 d20, d6 擲骰，並具備優勢、劣勢與大成功/大失敗判定。\n"
                    "• ✍️ **冒險行動**：彈出輸入框輸入自訂行動，DM 自動進行情境敘事。\n"
                    "• 🏰 **Session 模式**：開啟後本頻道發言自動進入跑團對話流。"
                ),
                color=discord.Color.dark_red(),
            )
            embed.set_footer(text="點擊下方 [ 🚀 開啟該模組主控台 ] 即可直接掛載 D&D 跑團主控台！")

        elif category == "music":
            embed = discord.Embed(
                title="🎵 預覽：留聲機音樂主控台",
                description=(
                    "**核心理念**：支援 YouTube 串流播放，提供視覺化留聲機時間軸與快捷控制按鈕。\n\n"
                    "• ⏯️ **播放/暫停/跳過**：一鍵切換播放狀態。\n"
                    "• 🔍 **點歌彈窗**：直接彈出表單搜尋歌曲或貼上 YouTube 網址。\n"
                    "• 📜 **清單分頁**：支援互動翻頁檢視播放佇列。"
                ),
                color=discord.Color.magenta(),
            )

        elif category == "stream":
            embed = discord.Embed(
                title="🎬 預覽：直播大螢幕控制中樞",
                description=(
                    "**核心理念**：播放本機影片庫或 YouTube 串流至專屬直播大螢幕 (Port 8764)。\n\n"
                    "• ▶️ **影片播放**：支援自動輪播本機劇集與 YouTube 網址。\n"
                    "• ⏹️ **停止播放**：一鍵停止並切回雪莉待機畫面。\n"
                    "• 📊 **狀態檢視**：即時查看目前播放媒體與可用影片庫清單。"
                ),
                color=discord.Color.teal(),
            )

        elif category == "admin":
            embed = discord.Embed(
                title="⚙️ 預覽：系統管理與可觀測性儀表板",
                description=(
                    "**核心理念**：提供管理員一站式系統健康診斷與指令熱重載能力。\n\n"
                    "• 🧠 **長期記憶**：檢視與維護跨頻道成員記憶庫。\n"
                    "• 💬 **話題氛圍**：即時掌握頻道熱門話題與情緒指標。\n"
                    "• 🔄 **指令熱同步**：一鍵重新同步應用程式斜線指令。"
                ),
                color=discord.Color.dark_grey(),
            )

        else:
            embed = discord.Embed(
                title=f"🎛️ 預覽：{category.upper()} 互動中樞",
                description="點擊下方按鈕可進行 Prompt 直通測試或呼叫對應功能！",
                color=discord.Color.blurple(),
            )

        return embed

    @discord.ui.button(label="🚀 開啟該模組主控台", style=discord.ButtonStyle.success, row=1)
    async def btn_open_module(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """根據當前分類動態開啟對應模組主控台。"""
        if self.current_category == "spark":
            spark_cog: Any = self.bot.get_cog("SparkCog")
            if spark_cog and hasattr(spark_cog, "service"):
                from handlers.spark.cog import SparkMainView
                s_view = SparkMainView(spark_cog.service, channel_id=interaction.channel_id)
                s_embed = s_view.build_dashboard_embed()
                await interaction.response.send_message(embed=s_embed, view=s_view, ephemeral=True)
            else:
                await interaction.response.send_message("❌ Spark 模組尚未載入！", ephemeral=True)

        elif self.current_category == "dnd":
            from mod.dnd.views import DndMainDashboardView
            d_view = DndMainDashboardView(self.bot)
            d_embed = d_view.build_dashboard_embed(interaction.user.id)
            await interaction.response.send_message(embed=d_embed, view=d_view, ephemeral=True)

        elif self.current_category == "music":
            music_cog: Any = self.bot.get_cog("MusicHandler")
            if music_cog and interaction.guild_id:
                embed = music_cog.build_playing_embed(interaction.guild_id)
                from handlers.music.ui import MusicControlView
                await interaction.response.send_message(embed=embed, view=MusicControlView(), ephemeral=True)
            else:
                await interaction.response.send_message("❌ 音樂模組未載入或目前不在伺服器中！", ephemeral=True)

        elif self.current_category == "stream":
            stream_cog: Any = self.bot.get_cog("LivestreamCog")
            if stream_cog:
                from mod.livestream.ui import StreamControlView
                st_view = StreamControlView(stream_cog)
                st_embed = st_view.build_dashboard_embed()
                await interaction.response.send_message(embed=st_embed, view=st_view, ephemeral=True)
            else:
                await interaction.response.send_message("❌ 直播螢幕模組尚未載入！", ephemeral=True)

        elif self.current_category == "market":
            market_cog: Any = self.bot.get_cog("Market & PTCG")
            if market_cog:
                from mod.market_plugin.ui import MarketControlView
                m_view = MarketControlView(market_cog)
                m_embed = m_view.build_dashboard_embed()
                await interaction.response.send_message(embed=m_embed, view=m_view, ephemeral=True)
            else:
                await interaction.response.send_message("❌ 股票市場模組尚未載入！", ephemeral=True)

        elif self.current_category == "admin":
            if not is_admin_user(interaction):
                await interaction.response.send_message(
                    "🔒 **權限不足**：此「系統管理與可觀測性」功能僅限管理員使用！",
                    ephemeral=True,
                )
                return
            embed = discord.Embed(
                title="⚙️ 雪莉系統管理儀表板",
                description="請使用 `/admin`、`/vibe`、`/memory` 指令進行系統維護與指令同步。",
                color=discord.Color.dark_grey(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        elif self.current_category == "intent":
            await interaction.response.send_modal(PromptTestModal(self.bot))

        else:
            await interaction.response.send_message(
                f"ℹ️ 模組 `{self.current_category}` 正在互動化開發中！",
                ephemeral=True,
            )

    @discord.ui.button(label="🧪 測試 Prompt 執行", style=discord.ButtonStyle.primary, row=1)
    async def btn_prompt_test(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """彈出 Prompt 直通測試表單。"""
        await interaction.response.send_modal(PromptTestModal(self.bot))


# ===========================================================================
# 3. ExecutorCog
# ===========================================================================

class ExecutorCog(commands.Cog):
    """提供 /execute 互動測試中樞與強制意圖測試指令。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="execute",
        description="🎛️ 開啟雪莉全能互動測試中樞 (Command Hub Preview) 或強制執行 Prompt",
    )
    @app_commands.describe(
        intent="可選：要直接強制執行的意圖類型 (若留空則開啟互動測試中樞)",
        content="可選：要傳入執行的 Prompt 內容",
    )
    @app_commands.choices(intent=_INTENT_CHOICES)
    async def execute_intent(
        self,
        interaction: discord.Interaction,
        intent: Optional[str] = None,
        content: Optional[str] = None,
    ) -> None:
        """若無傳入 intent 則開啟互動測試中樞；若有傳入則直接執行 Tool。"""
        # 若未指定 intent，直接開啟全功能互動測試中樞（全體成員皆可開啟）
        if not intent or not content:
            view = CommandHubView(self.bot, author_id=interaction.user.id)
            embed = view.build_preview_embed(view.current_category)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            return

        # 敏感管理意圖檢查
        if intent.upper() in ADMIN_ONLY_INTENTS and not is_admin_user(interaction):
            await interaction.response.send_message(
                f"🔒 **權限不足**：意圖 `{intent}` 屬於敏感系統管理操作，僅限管理員執行！",
                ephemeral=True,
            )
            return

        brain: Any = self.bot.get_cog("BotBrain")
        if not brain:
            await interaction.response.send_message("❌ BotBrain 未加載，無法執行任何意圖！", ephemeral=True)
            return

        target_tool = get_fallback_tool()
        for tool in TOOL_LIST:
            if tool.intent_name == intent:
                target_tool = tool
                break

        await interaction.response.defer(thinking=True)
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.followup.send("❌ 此指令僅限在文字頻道中使用！")
            return

        context_script = await fetch_context_script(interaction.channel, None, bot=self.bot)
        trigger_line = f"🎯 【強制測試指令 /execute [{intent}]】：[{interaction.user.display_name}] 正在測試：\u201c{content}\u201d"
        full_context = f"{context_script}\n\n{trigger_line}" if context_script else trigger_line

        fake_message = _FakeMessage(
            channel=interaction.channel,
            author=interaction.user,
            clean_content=content,
            id=interaction.id,
            _interaction=interaction,
        )

        try:
            await target_tool.execute(
                bot=self.bot,
                message=fake_message,  # type: ignore[arg-type]
                user_query=content,
                vibe_snippet="",
                full_context=full_context,
            )
        except Exception as e:
            logger.error("executor: Tool %s 執行失敗: %s", intent, e, exc_info=True)
            await interaction.followup.send(f"❌ 執行 `{intent}` 時發生錯誤：{e}")


async def setup(bot: commands.Bot) -> None:
    """掛載 ExecutorCog。"""
    await bot.add_cog(ExecutorCog(bot))