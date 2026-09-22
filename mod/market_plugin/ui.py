"""mod/market_plugin/ui.py
========================
股票市場分析與 PTCG 卡片追蹤 Discord 互動介面（按鈕面板、下拉選單與 Modal 輸入表單）。
"""

from __future__ import annotations

import logging
from typing import Any

import discord
from discord import ui

logger = logging.getLogger(__name__)


# ===========================================================================
# 1. 股票新增 Modal
# ===========================================================================

class StockAddModal(ui.Modal, title="📈 新增自選股票追蹤"):
    """彈出式新增股票代號表單。"""

    list_type_input: Any = ui.TextInput(
        label="清單類別 (ai / raw / etf)",
        placeholder="ai: AI分析組 / raw: 指標晨報組 / etf: 台股ETF組",
        default="raw",
        min_length=2,
        max_length=5,
        required=True,
    )
    ticker_input: Any = ui.TextInput(
        label="股票代號 / 標的 (美股/台股/ETF/加密幣)",
        placeholder="例如：NVDA, AAPL, 2330.TW, 0050.TW, BTC-USD",
        min_length=1,
        max_length=20,
        required=True,
    )

    def __init__(self, cog: Any) -> None:
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """處理股票新增提交。"""
        list_type_val = str(self.list_type_input.value).strip().lower()
        if list_type_val not in ("ai", "raw", "etf"):
            list_type_val = "raw"
        ticker_val = str(self.ticker_input.value).strip().upper().replace(".B", "-B")

        data = self.cog._load_watchlists()
        key = f"{list_type_val}_list"
        if ticker_val in data.get(key, []):
            await interaction.response.send_message(f"⚠️ **{ticker_val}** 已在 `{list_type_val}` 清單中！", ephemeral=True)
            return

        data.setdefault(key, []).append(ticker_val)
        if self.cog._save_watchlists(data):
            await interaction.response.send_message(f"✅ 已成功將 **{ticker_val}** 加入 `{list_type_val}` 追蹤清單！", ephemeral=True)
        else:
            await interaction.response.send_message("❌ 儲存清單時發生錯誤。", ephemeral=True)


# ===========================================================================
# 2. PTCG 卡片新增 Modal
# ===========================================================================

class CardAddModal(ui.Modal, title="🃏 新增 PTCG 卡片追蹤"):
    """彈出式新增 PTCG 卡片追蹤表單。"""

    name_input: Any = ui.TextInput(
        label="卡片自訂名稱",
        placeholder="例如：皮卡丘 25週年 PSA 10",
        min_length=1,
        max_length=50,
        required=True,
    )
    url_input: Any = ui.TextInput(
        label="SNKRDUNK 卡片頁面網址",
        placeholder="https://snkrdunk.com/products/...",
        min_length=10,
        max_length=300,
        required=True,
    )

    def __init__(self, cog: Any) -> None:
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """處理卡片新增提交。"""
        name_val = str(self.name_input.value).strip()
        url_val = str(self.url_input.value).strip()

        data = self.cog._load_cards()
        if name_val in data:
            await interaction.response.send_message(f"⚠️ **{name_val}** 已在卡片追蹤清單中！", ephemeral=True)
            return

        data[name_val] = {"url": url_val, "last_prices": {}}
        if self.cog._save_cards(data):
            await interaction.response.send_message(f"✅ 已成功將 **{name_val}** 加入卡片追蹤清單！", ephemeral=True)
        else:
            await interaction.response.send_message("❌ 儲存卡片清單時發生錯誤。", ephemeral=True)


# ===========================================================================
# 3. 股票市場主控面板 View
# ===========================================================================

class MarketControlView(ui.View):
    """股票市場分析互動控制面板。"""

    def __init__(self, cog: Any) -> None:
        super().__init__(timeout=180)
        self.cog = cog

    def build_dashboard_embed(self) -> discord.Embed:
        """建立市場儀表板 Embed。"""
        data = self.cog._load_watchlists()
        ai_list = ", ".join(f"`{s}`" for s in data.get("ai_list", [])) or "（目前無）"
        raw_list = ", ".join(f"`{s}`" for s in data.get("raw_list", [])) or "（目前無）"
        etf_list = ", ".join(f"`{s}`" for s in data.get("etf_list", [])) or "（目前無）"

        embed = discord.Embed(
            title="📈 雪莉股票市場與量化分析中樞",
            description="點選下方按鈕即可快速管理自選股或即時產生技術指標分析報告：",
            color=discord.Color.blue(),
        )
        embed.add_field(name="🤖 AI 分析組 (21:30 晚間推播)", value=ai_list, inline=False)
        embed.add_field(name="☀️ 指標晨報組 (08:00 晨報推播)", value=raw_list, inline=False)
        embed.add_field(name="📊 台股 ETF 組 (盤中溢折價監控)", value=etf_list, inline=False)
        embed.set_footer(text="提示：使用 /market <action> 亦可直接以指令快速操作。")
        return embed

    @ui.button(label="➕ 新增自選股", style=discord.ButtonStyle.success, row=0)
    async def btn_add_stock(self, interaction: discord.Interaction, button: ui.Button) -> None:
        """彈出新增自選股表單。"""
        await interaction.response.send_modal(StockAddModal(self.cog))

    @ui.button(label="📊 產出即時技術晨報", style=discord.ButtonStyle.primary, row=0)
    async def btn_run_raw(self, interaction: discord.Interaction, button: ui.Button) -> None:
        """立即產生技術指標晨報。"""
        await interaction.response.send_message("📊 正在抓取最新技術指標，請稍候...", ephemeral=False)
        try:
            await self.cog._task_daily_raw(target_channel_id=interaction.channel_id)
        except Exception as e:
            await interaction.followup.send(f"❌ 報告生成失敗: {e}", ephemeral=True)

    @ui.button(label="🔄 重新整理", style=discord.ButtonStyle.secondary, row=0)
    async def btn_refresh(self, interaction: discord.Interaction, button: ui.Button) -> None:
        """重新整理面板內容。"""
        embed = self.build_dashboard_embed()
        await interaction.response.edit_message(embed=embed, view=self)


# ===========================================================================
# 4. PTCG 卡價主控面板 View
# ===========================================================================

class CardControlView(ui.View):
    """PTCG 卡片行情追蹤控制面板。"""

    def __init__(self, cog: Any) -> None:
        super().__init__(timeout=180)
        self.cog = cog

    def build_dashboard_embed(self) -> discord.Embed:
        """建立卡價儀表板 Embed。"""
        data = self.cog._load_cards()
        embed = discord.Embed(
            title="🃏 PTCG 卡片行情監控中樞",
            description=f"目前共追蹤 **{len(data)}** 張熱門卡片。\n點選下方按鈕新增追蹤或立即觸發爬蟲更新最新報價：",
            color=discord.Color.gold(),
        )

        if data:
            cards_summary = []
            for name, info in list(data.items())[:10]:
                lp = info.get("last_prices", {})
                pa = lp.get("A", "N/A")
                pp = lp.get("PSA 10", "N/A")
                cards_summary.append(f"• **{name}** (A: `{pa}` | PSA10: `{pp}`)")
            embed.add_field(name="📋 追蹤卡片一覽 (前 10 筆)", value="\n".join(cards_summary), inline=False)
        else:
            embed.add_field(name="📋 追蹤卡片一覽", value="（目前無追蹤卡片，請點擊下方按鈕新增）", inline=False)

        embed.set_footer(text="提示：使用 /card <action> 亦可直接以指令快速操作。")
        return embed

    @ui.button(label="➕ 追蹤卡片", style=discord.ButtonStyle.success, row=0)
    async def btn_add_card(self, interaction: discord.Interaction, button: ui.Button) -> None:
        """彈出新增卡片表單。"""
        await interaction.response.send_modal(CardAddModal(self.cog))

    @ui.button(label="🔄 爬取最新報價", style=discord.ButtonStyle.primary, row=0)
    async def btn_update_cards(self, interaction: discord.Interaction, button: ui.Button) -> None:
        """手動觸發爬蟲更新卡價。"""
        await interaction.response.send_message("🃏 正在啟動爬蟲潛入 SNKRDUNK 抓取最新卡價，請稍候...", ephemeral=False)
        try:
            await self.cog._task_card_report(target_channel_id=interaction.channel_id)
        except Exception as e:
            await interaction.followup.send(f"❌ 卡片報價獲取失敗: {e}", ephemeral=True)

    @ui.button(label="🔄 重新整理清單", style=discord.ButtonStyle.secondary, row=0)
    async def btn_refresh_cards(self, interaction: discord.Interaction, button: ui.Button) -> None:
        """重新整理卡片面板。"""
        embed = self.build_dashboard_embed()
        await interaction.response.edit_message(embed=embed, view=self)
