"""mod/spark/cog.py - Spark 推播系統 Discord Cog (Presentation 層)。

支援全互動 UI（View、Select Menu、Modal 表單、預覽確認卡片）與純文字 CLI 指令。
符合 AGENTS.md §0，此為模組內唯一允許引用 discord 的 Presentation 檔案。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .checkers import is_market_open
from .clients import RssClient, StockClient, WeatherClient
from .config import DEFAULT_CHECK_INTERVAL_MINUTES, DEFAULT_COOLDOWN_SECONDS
from .models import Trigger, TriggerResult
from .nlp import parse_spark_context
from .repository import SparkRepository
from .service import SparkService, build_spark_service

logger = logging.getLogger(__name__)


# ===========================================================================
# 1. 彈出式輸入表單 (Discord Modals)
# ===========================================================================

class PriceTriggerModal(discord.ui.Modal, title="💰 新增/編輯價格監控警報"):
    """價格監控警報表單。"""

    ticker_input: Any = discord.ui.TextInput(
        label="標的代號 (Ticker)",
        placeholder="例如 BTC, ETH, 2330, AAPL, NVDA",
        min_length=1,
        max_length=20,
        required=True,
    )
    target_price_input: Any = discord.ui.TextInput(
        label="目標價格門檻",
        placeholder="例如 65000 或 1000",
        min_length=1,
        max_length=20,
        required=True,
    )
    direction_input: Any = discord.ui.TextInput(
        label="判定方向 (above/below/cross_above/cross_below)",
        placeholder="above (高於) / below (低於) / cross_above (向上突破)",
        default="above",
        min_length=2,
        max_length=20,
        required=True,
    )
    message_input: Any = discord.ui.TextInput(
        label="自訂推播提醒訊息 (選填)",
        placeholder="例如：名偵探提醒您幣價已達標！",
        required=False,
        max_length=200,
    )

    def __init__(
        self,
        service: SparkService,
        initial_data: dict[str, Any] | None = None,
        trigger_id_to_replace: str | None = None,
    ) -> None:
        """初始化價格監控表單，支援預先填入初始值。"""
        super().__init__()
        self.service = service
        self.trigger_id_to_replace = trigger_id_to_replace
        if initial_data:
            if "ticker" in initial_data:
                self.ticker_input.default = str(initial_data["ticker"])
            if "target_price" in initial_data:
                self.target_price_input.default = str(initial_data["target_price"])
            if "direction" in initial_data:
                self.direction_input.default = str(initial_data["direction"])
            if "message" in initial_data:
                self.message_input.default = str(initial_data["message"])

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """表單提交處理。"""
        ticker = self.ticker_input.value.strip().upper()
        try:
            target_price = float(self.target_price_input.value.strip())
        except ValueError:
            await interaction.response.send_message("❌ 目標價格必須為有效數字！", ephemeral=True)
            return

        direction = self.direction_input.value.strip().lower()
        if direction not in ("above", "below", "cross_above", "cross_below", ">=", "<=", ">", "<"):
            direction = "above"

        condition = {
            "ticker": ticker,
            "target_price": target_price,
            "direction": direction,
            "one_shot": True,
            "only_in_market_hours": True,
        }

        if self.trigger_id_to_replace:
            self.service.delete_trigger(self.trigger_id_to_replace)

        trigger = self.service.create_trigger(
            user_id=interaction.user.id,
            channel_id=interaction.channel_id or 0,
            trigger_type="price",
            condition=condition,
            message=self.message_input.value.strip(),
            cooldown_seconds=DEFAULT_COOLDOWN_SECONDS,
        )

        act_text = "更新" if self.trigger_id_to_replace else "建立"
        await interaction.response.send_message(
            f"✅ **價格監控警報已{act_text}！**\n"
            f"- 標的：`{ticker}`\n"
            f"- 條件：`{direction} ${target_price:,.2f}`\n"
            f"- 推播頻道：<#{trigger.channel_id}>",
            ephemeral=True,
        )


class ScheduleTriggerModal(discord.ui.Modal, title="⏰ 新增/編輯定時推播任務"):
    """定時排程推播表單。"""

    mode_input: Any = discord.ui.TextInput(
        label="排程模式 (interval / daily)",
        placeholder="interval (每隔N分鐘) 或 daily (每日固定時間)",
        default="interval",
        min_length=4,
        max_length=15,
        required=True,
    )
    time_val_input: Any = discord.ui.TextInput(
        label="間隔分鐘數 或 每日時間 (如 60 或 08:30)",
        placeholder="例如 60 或 08:30",
        min_length=1,
        max_length=20,
        required=True,
    )
    message_input: Any = discord.ui.TextInput(
        label="推播提醒文字內容",
        placeholder="例如：早安！雪莉提醒您該吃早餐與準備開會囉！",
        required=True,
        max_length=200,
    )

    def __init__(
        self,
        service: SparkService,
        initial_data: dict[str, Any] | None = None,
        trigger_id_to_replace: str | None = None,
    ) -> None:
        """初始化定時排程表單，支援預先填入初始值。"""
        super().__init__()
        self.service = service
        self.trigger_id_to_replace = trigger_id_to_replace
        if initial_data:
            if "mode" in initial_data:
                self.mode_input.default = str(initial_data["mode"])
            if "time" in initial_data:
                self.time_val_input.default = str(initial_data["time"])
            elif "interval_minutes" in initial_data:
                self.time_val_input.default = str(initial_data["interval_minutes"])
            if "message" in initial_data:
                self.message_input.default = str(initial_data["message"])

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """表單提交處理。"""
        mode = "interval" if "inter" in self.mode_input.value.lower() else "daily_time"
        val = self.time_val_input.value.strip()

        condition: dict[str, Any] = {"mode": mode}
        if mode == "interval":
            try:
                condition["interval_minutes"] = int(val)
            except ValueError:
                await interaction.response.send_message("❌ 間隔分鐘數必須為整數數字！", ephemeral=True)
                return
        else:
            condition["time"] = val

        if self.trigger_id_to_replace:
            self.service.delete_trigger(self.trigger_id_to_replace)

        trigger = self.service.create_trigger(
            user_id=interaction.user.id,
            channel_id=interaction.channel_id or 0,
            trigger_type="schedule",
            condition=condition,
            message=self.message_input.value.strip(),
            cooldown_seconds=60,
        )

        act_text = "更新" if self.trigger_id_to_replace else "建立"
        await interaction.response.send_message(
            f"✅ **定時排程已{act_text}！**\n"
            f"- 模式：`{mode}` (`{val}`)\n"
            f"- 訊息：`{trigger.message}`\n"
            f"- 推播頻道：<#{trigger.channel_id}>",
            ephemeral=True,
        )


class RssTriggerModal(discord.ui.Modal, title="📰 新增/編輯 RSS / YouTube 訂閱更新"):
    """RSS / YouTube 訂閱更新表單。"""

    feed_url_input: Any = discord.ui.TextInput(
        label="RSS / Atom 或 YouTube 訂閱 Feed 網址",
        placeholder="https://...",
        min_length=5,
        max_length=300,
        required=True,
    )
    keyword_input: Any = discord.ui.TextInput(
        label="標題關鍵字過濾 (選填)",
        placeholder="例如 AI, release, update",
        required=False,
        max_length=50,
    )
    message_input: Any = discord.ui.TextInput(
        label="自訂附加訊息 (選填)",
        placeholder="發現新內容時一併發送的說明文字",
        required=False,
        max_length=200,
    )

    def __init__(
        self,
        service: SparkService,
        initial_data: dict[str, Any] | None = None,
        trigger_id_to_replace: str | None = None,
    ) -> None:
        """初始化 RSS 表單，支援預先填入初始值。"""
        super().__init__()
        self.service = service
        self.trigger_id_to_replace = trigger_id_to_replace
        if initial_data:
            if "feed_url" in initial_data:
                self.feed_url_input.default = str(initial_data["feed_url"])
            if "keyword" in initial_data:
                self.keyword_input.default = str(initial_data["keyword"])
            if "message" in initial_data:
                self.message_input.default = str(initial_data["message"])

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """表單提交處理。"""
        feed_url = self.feed_url_input.value.strip()
        keyword = self.keyword_input.value.strip()

        condition = {
            "feed_url": feed_url,
            "keyword": keyword,
        }

        if self.trigger_id_to_replace:
            self.service.delete_trigger(self.trigger_id_to_replace)

        trigger = self.service.create_trigger(
            user_id=interaction.user.id,
            channel_id=interaction.channel_id or 0,
            trigger_type="rss",
            condition=condition,
            message=self.message_input.value.strip(),
            cooldown_seconds=300,
        )

        act_text = "更新" if self.trigger_id_to_replace else "建立"
        await interaction.response.send_message(
            f"✅ **RSS / 內容訂閱已{act_text}！**\n"
            f"- 網址：`{feed_url}`\n"
            f"- 關鍵字：`{keyword or '無'}`\n"
            f"- 推播頻道：<#{trigger.channel_id}>",
            ephemeral=True,
        )


class WeatherTriggerModal(discord.ui.Modal, title="🌤️ 新增/編輯即時天氣預警"):
    """天氣預警與播報表單。"""

    city_input: Any = discord.ui.TextInput(
        label="城市名稱 (中英文皆可)",
        placeholder="例如 台北, 新北, 台中, 高雄, Tokyo, London",
        default="台北",
        min_length=1,
        max_length=30,
        required=True,
    )
    alert_type_input: Any = discord.ui.TextInput(
        label="預警類型 (rain / temp_above / temp_below / summary)",
        placeholder="rain (降雨) / temp_above (高溫) / temp_below (低溫) / summary (概況)",
        default="rain",
        min_length=3,
        max_length=20,
        required=True,
    )
    threshold_input: Any = discord.ui.TextInput(
        label="門檻數值 (選填，如溫度35或機率50)",
        placeholder="高溫/低溫門檻或降雨機率",
        required=False,
        max_length=10,
    )

    def __init__(
        self,
        service: SparkService,
        initial_data: dict[str, Any] | None = None,
        trigger_id_to_replace: str | None = None,
    ) -> None:
        """初始化天氣預警表單，支援預先填入初始值。"""
        super().__init__()
        self.service = service
        self.trigger_id_to_replace = trigger_id_to_replace
        if initial_data:
            if "city" in initial_data:
                self.city_input.default = str(initial_data["city"])
            if "alert_type" in initial_data:
                self.alert_type_input.default = str(initial_data["alert_type"])
            if "threshold" in initial_data:
                self.threshold_input.default = str(initial_data["threshold"])

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """表單提交處理。"""
        city = self.city_input.value.strip()
        alert_type = self.alert_type_input.value.strip().lower()
        thresh_val = self.threshold_input.value.strip()

        condition: dict[str, Any] = {
            "city": city,
            "alert_type": alert_type,
        }
        if thresh_val:
            try:
                condition["threshold"] = float(thresh_val)
            except ValueError:
                pass

        if self.trigger_id_to_replace:
            self.service.delete_trigger(self.trigger_id_to_replace)

        trigger = self.service.create_trigger(
            user_id=interaction.user.id,
            channel_id=interaction.channel_id or 0,
            trigger_type="weather",
            condition=condition,
            cooldown_seconds=DEFAULT_COOLDOWN_SECONDS,
        )

        act_text = "更新" if self.trigger_id_to_replace else "建立"
        await interaction.response.send_message(
            f"✅ **即時天氣預警已{act_text}！**\n"
            f"- 城市：`{city}`\n"
            f"- 類型：`{alert_type}`\n"
            f"- 推播頻道：<#{trigger.channel_id}>",
            ephemeral=True,
        )


# ===========================================================================
# 2. 互動式防呆確認視圖 (SparkConfirmView) 與通用視圖
# ===========================================================================

class SparkConfirmView(discord.ui.View):
    """互動式推播預覽確認視圖：支援確認建立、修改參數與取消。"""

    def __init__(
        self,
        service: SparkService,
        candidate: dict[str, Any],
        author_id: int,
    ) -> None:
        """初始化確認視圖。

        Args:
            service: Spark 服務實例。
            candidate: 自然語言解析之候選推播參數字典。
            author_id: 指令發起者 Discord ID。
        """
        super().__init__(timeout=180)
        self.service = service
        self.candidate = candidate
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """權限防呆檢查：僅允許指令發起者操作按鈕。"""
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 只有指令發起者可以操作此卡片！", ephemeral=True)
            return False
        return True

    def build_preview_embed(self) -> discord.Embed:
        """建立預覽確認 Embed 卡片。"""
        ttype = self.candidate.get("trigger_type", "price")
        cond = self.candidate.get("condition", {})
        channel_id = self.candidate.get("channel_id", 0)

        cond_desc_lines = []
        if ttype == "price":
            ticker = cond.get("ticker", "未知")
            target = cond.get("target_price", 0.0)
            direction = cond.get("direction", "above")
            dir_text = "漲破/高於" if direction in ("above", "cross_above", ">=", ">") else "跌破/低於"
            cond_desc_lines.append(f"• **類型**: 💰 股票/加密貨幣價格警報")
            cond_desc_lines.append(f"• **標的**: `{ticker}`")
            cond_desc_lines.append(f"• **觸發條件**: `{dir_text} ${target:,.2f}`")
            cond_desc_lines.append("• **交易時段過濾**: 盤中監控 (休市自動暫停)")
            cond_desc_lines.append("• **觸發模式**: 單次達標後自動完成 (One-shot)")
        elif ttype == "schedule":
            mode = cond.get("mode", "interval")
            cond_desc_lines.append(f"• **類型**: ⏰ 定時排程推播任務")
            if mode == "interval":
                cond_desc_lines.append(f"• **排程模式**: `循環間隔`（每 `{cond.get('interval_minutes')}` 分鐘）")
            else:
                cond_desc_lines.append(f"• **排程模式**: `每日定時`（每日 `{cond.get('time')}`）")
        elif ttype == "weather":
            cond_desc_lines.append(f"• **類型**: 🌤️ 即時天氣預警")
            cond_desc_lines.append(f"• **城市**: `{cond.get('city')}`")
            cond_desc_lines.append(f"• **預警類型**: `{cond.get('alert_type')}`")
            if "threshold" in cond:
                cond_desc_lines.append(f"• **門檻數值**: `{cond.get('threshold')}`")
        elif ttype == "rss":
            cond_desc_lines.append(f"• **類型**: 📰 RSS / 內容訂閱")
            cond_desc_lines.append(f"• **Feed 網址**: `{cond.get('feed_url')}`")
            if cond.get("keyword"):
                cond_desc_lines.append(f"• **關鍵字過濾**: `{cond.get('keyword')}`")

        cond_desc_lines.append(f"• **推播頻道**: <#{channel_id}>")
        if self.candidate.get("message"):
            cond_desc_lines.append(f"• **附加訊息**: {self.candidate.get('message')}")

        embed = discord.Embed(
            title="✨ Spark 智慧推播預覽確認",
            description="請確認由名偵探解析的推播設定是否正確：\n\n" + "\n".join(cond_desc_lines),
            color=discord.Color.teal(),
        )
        embed.set_footer(text="點擊 [確認建立] 立即啟用監控，或點擊 [修改參數] 調整數值")
        return embed

    @discord.ui.button(label="✅ 確認建立", style=discord.ButtonStyle.success)
    async def btn_confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """確認建立推播任務。"""
        trigger = self.service.create_trigger(
            user_id=self.candidate["user_id"],
            channel_id=self.candidate["channel_id"],
            trigger_type=self.candidate["trigger_type"],
            condition=self.candidate["condition"],
            message=self.candidate.get("message", ""),
            cooldown_seconds=self.candidate.get("cooldown_seconds", 3600),
        )
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]

        success_embed = discord.Embed(
            title="✅ Spark 條件推播建立成功！",
            description=(
                f"🎉 **任務已寫入資料庫並啟用背景監控！**\n\n"
                f"• **任務 ID**: `{trigger.trigger_id[:8]}`\n"
                f"• **類型**: `{trigger.trigger_type.upper()}`\n"
                f"• **推播頻道**: <#{trigger.channel_id}>\n"
                f"• **冷卻時間**: `{trigger.cooldown_seconds}` 秒"
            ),
            color=discord.Color.green(),
        )
        await interaction.response.edit_message(embed=success_embed, view=self)

    @discord.ui.button(label="✏️ 修改參數", style=discord.ButtonStyle.primary)
    async def btn_edit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """彈出對應之 Modal，並將當前解析之參數預先填入表單。"""
        ttype = self.candidate.get("trigger_type", "price")
        initial_data = dict(self.candidate.get("condition") or {})
        initial_data["message"] = self.candidate.get("message", "")

        if ttype == "price":
            await interaction.response.send_modal(PriceTriggerModal(self.service, initial_data=initial_data))
        elif ttype == "schedule":
            await interaction.response.send_modal(ScheduleTriggerModal(self.service, initial_data=initial_data))
        elif ttype == "rss":
            await interaction.response.send_modal(RssTriggerModal(self.service, initial_data=initial_data))
        elif ttype == "weather":
            await interaction.response.send_modal(WeatherTriggerModal(self.service, initial_data=initial_data))
        else:
            await interaction.response.send_message("❌ 未知的推播類型，無法開啟表單。", ephemeral=True)

    @discord.ui.button(label="❌ 取消", style=discord.ButtonStyle.danger)
    async def btn_cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """取消本次推播建立。"""
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        cancel_embed = discord.Embed(
            title="❌ 推播建立已取消",
            description="已取消本次 Spark 推播建立流程。",
            color=discord.Color.light_grey(),
        )
        await interaction.response.edit_message(embed=cancel_embed, view=self)


class SparkTypeSelect(discord.ui.Select):
    """新增推播類型選擇下拉選單。"""

    def __init__(self, service: SparkService) -> None:
        self.service = service
        options = [
            discord.SelectOption(label="💰 價格監控警報 (股票/加密貨幣)", value="price", description="監控 BTC、台美股現價與突破跌破"),
            discord.SelectOption(label="⏰ 定時排程推播 (間隔/每日特定時間)", value="schedule", description="每隔 N 分鐘或每天指定時刻提醒"),
            discord.SelectOption(label="📰 RSS / YouTube 訂閱更新", value="rss", description="監聽科技文章、論文或頻道新影片"),
            discord.SelectOption(label="🌤️ 即時天氣與環境預警", value="weather", description="降雨警報、高溫低溫特報或每日氣象"),
        ]
        super().__init__(placeholder="請選擇欲建立的推播類型...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        """選取後彈出對應的 Modal 表單。"""
        selected = self.values[0]
        if selected == "price":
            await interaction.response.send_modal(PriceTriggerModal(self.service))
        elif selected == "schedule":
            await interaction.response.send_modal(ScheduleTriggerModal(self.service))
        elif selected == "rss":
            await interaction.response.send_modal(RssTriggerModal(self.service))
        elif selected == "weather":
            await interaction.response.send_modal(WeatherTriggerModal(self.service))


class SparkTypeSelectView(discord.ui.View):
    """新增推播選擇面板 View。"""

    def __init__(self, service: SparkService) -> None:
        super().__init__(timeout=180)
        self.add_item(SparkTypeSelect(service))


class SparkActionSelect(discord.ui.Select):
    """專用動作選擇下拉選單 (delete / edit / toggle)。"""

    def __init__(self, service: SparkService, action_mode: str, triggers: list[Trigger]) -> None:
        self.service = service
        self.action_mode = action_mode
        options = []
        for t in triggers[:25]:
            status_tag = "🟢" if t.is_active else "⏸️"
            label = f"{status_tag} [{t.trigger_type.upper()}] {t.trigger_id[:8]}"
            desc = ", ".join(f"{k}:{v}" for k, v in list(t.condition.items())[:2])[:50]
            options.append(discord.SelectOption(label=label, value=t.trigger_id, description=desc))

        placeholders = {
            "delete": "🗑️ 請選擇欲刪除的推播任務...",
            "edit": "✏️ 請選擇欲修改的推播任務...",
            "toggle": "⏯️ 請選擇欲切換狀態的推播任務...",
        }
        super().__init__(
            placeholder=placeholders.get(action_mode, "請選擇任務..."),
            min_values=1,
            max_values=1,
            options=options if options else [discord.SelectOption(label="無可用任務", value="none")],
            disabled=len(triggers) == 0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """選中特定任務後執行對應動作。"""
        selected_id = self.values[0]
        if selected_id == "none":
            await interaction.response.defer()
            return

        trigger = self.service.get_trigger(selected_id)
        if not trigger:
            await interaction.response.send_message("❌ 找不到該任務。", ephemeral=True)
            return

        if self.action_mode == "delete":
            self.service.delete_trigger(selected_id)
            await interaction.response.send_message(
                f"🗑️ 已成功刪除推播任務 `[{trigger.trigger_type.upper()}]` `{trigger.trigger_id[:8]}`！",
                ephemeral=True,
            )
        elif self.action_mode == "toggle":
            updated = self.service.toggle_trigger(selected_id)
            state_str = "🟢 啟用" if updated and updated.is_active else "⏸️ 暫停"
            await interaction.response.send_message(
                f"已將任務 `{trigger.trigger_id[:8]}` 切換為 **{state_str}** 狀態！",
                ephemeral=True,
            )
        elif self.action_mode == "edit":
            ttype = trigger.trigger_type
            initial_data = dict(trigger.condition)
            initial_data["message"] = trigger.message
            if ttype == "price":
                await interaction.response.send_modal(PriceTriggerModal(self.service, initial_data=initial_data, trigger_id_to_replace=trigger.trigger_id))
            elif ttype == "schedule":
                await interaction.response.send_modal(ScheduleTriggerModal(self.service, initial_data=initial_data, trigger_id_to_replace=trigger.trigger_id))
            elif ttype == "rss":
                await interaction.response.send_modal(RssTriggerModal(self.service, initial_data=initial_data, trigger_id_to_replace=trigger.trigger_id))
            elif ttype == "weather":
                await interaction.response.send_modal(WeatherTriggerModal(self.service, initial_data=initial_data, trigger_id_to_replace=trigger.trigger_id))


class SparkActionSelectView(discord.ui.View):
    """專用動作選擇面板 View。"""

    def __init__(self, service: SparkService, action_mode: str) -> None:
        super().__init__(timeout=180)
        triggers = service.list_triggers()
        self.add_item(SparkActionSelect(service, action_mode, triggers))


class SparkTriggerSelect(discord.ui.Select):
    """現有推播任務選擇下拉選單（免手打 UUID）。"""

    def __init__(self, triggers: list[Trigger], parent_view: SparkMainView) -> None:
        self.parent_view = parent_view
        options = []
        for t in triggers[:25]:
            status_tag = "🟢" if t.is_active else "⏸️"
            label = f"{status_tag} [{t.trigger_type.upper()}] {t.trigger_id[:8]}"
            desc = ", ".join(f"{k}:{v}" for k, v in list(t.condition.items())[:2])[:50]
            options.append(discord.SelectOption(label=label, value=t.trigger_id, description=desc))

        super().__init__(
            placeholder="點此選擇欲操作的推播任務...",
            min_values=1,
            max_values=1,
            options=options if options else [discord.SelectOption(label="無可用任務", value="none")],
            disabled=len(triggers) == 0,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """選擇特定 Trigger 後更新狀態。"""
        selected_id = self.values[0]
        if selected_id == "none":
            await interaction.response.defer()
            return

        self.parent_view.selected_trigger_id = selected_id
        trigger = self.parent_view.service.get_trigger(selected_id)
        if trigger:
            status_text = "🟢 運行中" if trigger.is_active else "⏸️ 已暫停"
            cond_str = "\n".join(f"• **{k}**: `{v}`" for k, v in trigger.condition.items())
            last_run = trigger.last_triggered_at[:19].replace("T", " ") if trigger.last_triggered_at else "尚未觸發"

            embed = discord.Embed(
                title=f"📡 Spark 任務詳情：[{trigger.trigger_type.upper()}] `{trigger.trigger_id[:8]}`",
                description=(
                    f"**狀態**: {status_text}\n"
                    f"**目標頻道**: <#{trigger.channel_id}>\n"
                    f"**冷卻時間**: `{trigger.cooldown_seconds}` 秒\n"
                    f"**上次觸發**: `{last_run}`\n\n"
                    f"**📋 觸發條件**:\n{cond_str}\n"
                    + (f"\n**💬 附加訊息**: {trigger.message}" if trigger.message else "")
                ),
                color=discord.Color.purple(),
            )
            embed.set_footer(text="可點擊下方按鈕進行【編輯】、【暫停/啟用】、【立即測試】或【刪除】")
            await interaction.response.edit_message(embed=embed, view=self.parent_view)
        else:
            await interaction.response.send_message("❌ 找不到該觸發器。", ephemeral=True)


class SparkMainView(discord.ui.View):
    """Spark 全合一主控介面 View。"""

    def __init__(self, service: SparkService, channel_id: int | None = None) -> None:
        super().__init__(timeout=300)
        self.service = service
        self.channel_id = channel_id
        self.selected_trigger_id: Optional[str] = None
        self._refresh_components()

    def _refresh_components(self) -> None:
        """重新整理下拉選單，並保留所有按鈕元件。"""
        for item in list(self.children):
            if isinstance(item, discord.ui.Select):
                self.remove_item(item)
        triggers = self.service.list_triggers()
        self.add_item(SparkTriggerSelect(triggers, self))

    def build_dashboard_embed(self) -> discord.Embed:
        """建立主儀表板 Embed。"""
        triggers = self.service.list_triggers()
        active_count = sum(1 for t in triggers if t.is_active)
        paused_count = len(triggers) - active_count

        embed = discord.Embed(
            title="📡 Spark 泛用條件推播主控台",
            description=(
                f"📊 目前共有 **{len(triggers)}** 個推播任務（**{active_count}** 啟用中 / **{paused_count}** 暫停中）\n"
                f"💡 請在下方**下拉選單點選任務**，或使用按鈕進行操作："
            ),
            color=discord.Color.blurple(),
        )

        if not triggers:
            embed.add_field(
                name="ℹ️ 尚無推播任務",
                value="點擊下方 **[ ➕ 新增推播 ]** 立即建立第一個條件推播！",
                inline=False,
            )
        else:
            list_text = []
            for t in triggers[:8]:
                st = "🟢" if t.is_active else "⏸️"
                cond_preview = ", ".join(f"{k}:{v}" for k, v in list(t.condition.items())[:2])
                list_text.append(f"{st} `[{t.trigger_type.upper()}]` `{t.trigger_id[:8]}` (<#{t.channel_id}>) — `{cond_preview}`")
            embed.add_field(name="📋 最近任務列表", value="\n".join(list_text), inline=False)
            if len(triggers) > 8:
                embed.set_footer(text=f"更多任務請透過下拉選單瀏覽（共 {len(triggers)} 筆）")

        return embed

    # ── 按鈕組 ──────────────────────────────────────────────

    @discord.ui.button(label="➕ 新增推播", style=discord.ButtonStyle.success, row=1)
    async def btn_add(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """點擊新增推播：跳出類型選擇選單。"""
        await interaction.response.send_message(
            "請選擇您欲建立的推播類型：",
            view=SparkTypeSelectView(self.service),
            ephemeral=True,
        )

    @discord.ui.button(label="✏️ 編輯任務", style=discord.ButtonStyle.primary, row=1)
    async def btn_edit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """編輯選中的推播任務（自動帶入既有數值）。"""
        if not self.selected_trigger_id:
            await interaction.response.send_message("⚠️ 請先在上方下拉選單中選擇一個推播任務！", ephemeral=True)
            return

        trigger = self.service.get_trigger(self.selected_trigger_id)
        if not trigger:
            await interaction.response.send_message("❌ 找不到該任務。", ephemeral=True)
            return

        ttype = trigger.trigger_type
        initial_data = dict(trigger.condition)
        initial_data["message"] = trigger.message

        if ttype == "price":
            await interaction.response.send_modal(PriceTriggerModal(self.service, initial_data=initial_data, trigger_id_to_replace=trigger.trigger_id))
        elif ttype == "schedule":
            await interaction.response.send_modal(ScheduleTriggerModal(self.service, initial_data=initial_data, trigger_id_to_replace=trigger.trigger_id))
        elif ttype == "rss":
            await interaction.response.send_modal(RssTriggerModal(self.service, initial_data=initial_data, trigger_id_to_replace=trigger.trigger_id))
        elif ttype == "weather":
            await interaction.response.send_modal(WeatherTriggerModal(self.service, initial_data=initial_data, trigger_id_to_replace=trigger.trigger_id))
        else:
            await interaction.response.send_message("❌ 未知的推播類型，無法開啟表單。", ephemeral=True)

    @discord.ui.button(label="⏯️ 暫停/啟用", style=discord.ButtonStyle.primary, row=1)
    async def btn_toggle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """切換選中任務之狀態。"""
        if not self.selected_trigger_id:
            await interaction.response.send_message("⚠️ 請先在上方下拉選單中選擇一個推播任務！", ephemeral=True)
            return

        updated = self.service.toggle_trigger(self.selected_trigger_id)
        if updated:
            state_str = "🟢 啟用" if updated.is_active else "⏸️ 暫停"
            self._refresh_components()
            await interaction.response.edit_message(embed=self.build_dashboard_embed(), view=self)
            await interaction.followup.send(f"已將任務 `{self.selected_trigger_id[:8]}` 切換為 **{state_str}** 狀態！", ephemeral=True)
        else:
            await interaction.response.send_message("❌ 找不到該任務。", ephemeral=True)

    @discord.ui.button(label="🔍 立即測試", style=discord.ButtonStyle.secondary, row=2)
    async def btn_test(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """立即測試選中任務條件。"""
        if not self.selected_trigger_id:
            await interaction.response.send_message("⚠️ 請先在上方下拉選單中選擇一個推播任務！", ephemeral=True)
            return

        trigger = self.service.get_trigger(self.selected_trigger_id)
        if not trigger:
            await interaction.response.send_message("❌ 找不到該任務。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        result, _ = await self.service.check_trigger(trigger)
        icon = "✅" if result.should_fire else "❌"
        await interaction.followup.send(
            f"🔍 **觸發器測試成果** (`{trigger.trigger_id[:8]}`):\n"
            f"- 是否達觸發條件: **{icon} {'滿足條件 (應觸發)' if result.should_fire else '未達條件'}**\n"
            f"- 摘要說明: {result.current_value}",
            ephemeral=True,
        )

    @discord.ui.button(label="🗑️ 刪除任務", style=discord.ButtonStyle.danger, row=2)
    async def btn_delete(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """刪除選中的任務。"""
        if not self.selected_trigger_id:
            await interaction.response.send_message("⚠️ 請先在上方下拉選單中選擇一個推播任務！", ephemeral=True)
            return

        target_id = self.selected_trigger_id
        success = self.service.delete_trigger(target_id)
        if success:
            self.selected_trigger_id = None
            self._refresh_components()
            await interaction.response.edit_message(embed=self.build_dashboard_embed(), view=self)
            await interaction.followup.send(f"🗑️ 已成功刪除推播任務 `{target_id[:8]}`！", ephemeral=True)
        else:
            await interaction.response.send_message("❌ 刪除失敗或任務已不存在。", ephemeral=True)

    @discord.ui.button(label="🔄 重新整理", style=discord.ButtonStyle.secondary, row=2)
    async def btn_refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """重新整理主控面板。"""
        self._refresh_components()
        await interaction.response.edit_message(embed=self.build_dashboard_embed(), view=self)


# ===========================================================================
# 3. SparkCog 本體：斜線指令 (/spark) 與前綴 CLI 指令 (!spark)
# ===========================================================================

class SparkCog(commands.Cog, name="SparkCog"):
    """Spark 泛用條件推播系統 Cog。"""

    def __init__(self, bot: commands.Bot, service: Optional[SparkService] = None) -> None:
        """初始化 SparkCog。"""
        self.bot = bot
        if service is not None:
            self.service = service
        else:
            repo = SparkRepository()
            clients = {
                "stock": StockClient(),
                "rss": RssClient(),
                "weather": WeatherClient(),
            }
            self.service = build_spark_service(repo, clients)

    async def cog_load(self) -> None:
        """Cog 載入時啟動背景巡檢排程迴圈。"""
        if not self._spark_check_loop.is_running():
            self._spark_check_loop.start()
        logger.info("📡 Spark 推播系統 Cog 與巡檢排程已成功啟動！")

    async def cog_unload(self) -> None:
        """Cog 卸載時停止巡檢迴圈。"""
        if self._spark_check_loop.is_running():
            self._spark_check_loop.cancel()
        logger.info("📡 Spark 推播系統 Cog 已卸載。")

    @tasks.loop(minutes=DEFAULT_CHECK_INTERVAL_MINUTES)
    async def _spark_check_loop(self) -> None:
        """每分鐘巡檢所有處於啟用狀態的 Trigger。"""
        active_triggers = self.service.list_active_triggers()
        if not active_triggers:
            return

        for trigger in active_triggers:
            try:
                if self.service.is_in_cooldown(trigger):
                    continue

                result, updated = await self.service.check_trigger(trigger)
                if result.should_fire:
                    await self._send_notification(trigger, result.current_value)

            except Exception as exc:
                logger.error(
                    "[Spark Loop] 檢查 Trigger (%s) 發生異常: %s",
                    trigger.trigger_id,
                    exc,
                    exc_info=True,
                )

    @_spark_check_loop.before_loop
    async def _before_spark_check_loop(self) -> None:
        """等待 Discord Bot 就緒。"""
        await self.bot.wait_until_ready()

    async def _send_notification(self, trigger: Trigger, content: str) -> None:
        """向指定頻道發送推播通知。"""
        channel = self.bot.get_channel(trigger.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(trigger.channel_id)
            except Exception as exc:
                logger.warning("[Spark] 無法獲取推播頻道 %s: %s", trigger.channel_id, exc)
                return

        if not hasattr(channel, "send"):
            return

        embed = discord.Embed(
            title=f"📡 Spark 條件推播通知【{trigger.trigger_type.upper()}】",
            description=content,
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc),
        )
        if trigger.trigger_type == "price":
            ticker = str(trigger.condition.get("ticker", "")).strip()
            if ticker:
                _, market_desc = is_market_open(ticker)
                embed.add_field(name="🏛️ 市場狀態", value=market_desc, inline=True)
            if trigger.condition.get("one_shot", True):
                embed.add_field(
                    name="🎉 任務完成",
                    value="目標價已達成！任務已自動完成並暫停（若需持續追蹤可於 /spark 重新啟用）",
                    inline=False,
                )
        if trigger.message:
            embed.add_field(name="💬 自訂訊息", value=trigger.message, inline=False)
        embed.set_footer(text=f"Trigger ID: {trigger.trigger_id[:8]} | 建立者: <@{trigger.user_id}>")

        try:
            await channel.send(embed=embed)  # type: ignore[union-attr]
            logger.info("✅ 成功發送 Spark 推播通知 (%s) 至頻道 %s", trigger.trigger_id, trigger.channel_id)
        except Exception as exc:
            logger.error("❌ 發送 Spark 推播通知失敗 (%s): %s", trigger.trigger_id, exc)

    def _find_trigger_by_id_prefix(self, prefix: str) -> Trigger | None:
        """根據前綴或完整 ID 尋找 Trigger。"""
        clean_prefix = prefix.strip().lower()
        triggers = self.service.list_triggers()
        for t in triggers:
            if t.trigger_id.lower().startswith(clean_prefix):
                return t
        return None

    # ----------------------------------------------------------------------
    # 1. 統一的單一斜線指令 (/spark)
    # ----------------------------------------------------------------------
    @app_commands.command(name="spark", description="📡 Spark 泛用條件推播主控面板與智能推播管理")
    @app_commands.describe(
        action="操作動作 (add: 新增 / panel: 儀表板 / list: 清單 / delete: 刪除 / edit: 編輯 / toggle: 暫停啟用)",
        context="推播需求描述或任務 ID (例如：NVDA 100元叫我 或 4e7b1379)",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="➕ add (新增推播)", value="add"),
            app_commands.Choice(name="📊 panel (主控儀表板)", value="panel"),
            app_commands.Choice(name="📋 list (任務清單)", value="list"),
            app_commands.Choice(name="🗑️ delete (刪除任務)", value="delete"),
            app_commands.Choice(name="✏️ edit (編輯任務)", value="edit"),
            app_commands.Choice(name="⏯️ toggle (暫停/啟用)", value="toggle"),
        ]
    )
    async def spark_command(
        self,
        interaction: discord.Interaction,
        action: Optional[app_commands.Choice[str]] = None,
        context: Optional[str] = None,
    ) -> None:
        """Spark 統一斜線指令入口。"""
        action_val = "panel"
        if action is not None:
            if hasattr(action, "value"):
                action_val = str(action.value).lower().strip()
            else:
                action_val = str(action).lower().strip()

        # 情境 1: action = add (新增推播)
        if action_val == "add":
            if context and context.strip():
                # 智能一句話建立 (NLP 自然語言解析)
                candidate = await parse_spark_context(
                    context=context.strip(),
                    user_id=interaction.user.id,
                    channel_id=interaction.channel_id or 0,
                    bot=self.bot,
                )
                if candidate is not None:
                    confirm_view = SparkConfirmView(
                        service=self.service,
                        candidate=candidate,
                        author_id=interaction.user.id,
                    )
                    preview_embed = confirm_view.build_preview_embed()
                    await interaction.response.send_message(
                        embed=preview_embed,
                        view=confirm_view,
                        ephemeral=True,
                    )
                    return
                else:
                    # 無法直接解析自然語言，給予提示並引導至類型選單
                    view = SparkTypeSelectView(self.service)
                    await interaction.response.send_message(
                        f"🤔 名偵探暫時無法直接理解「`{context}`」的推播條件。\n"
                        f"請透過下方選單選擇類型手動填寫表單：",
                        view=view,
                        ephemeral=True,
                    )
                    return
            else:
                # 傳統彈出表單建立 (無 context)
                view = SparkTypeSelectView(self.service)
                await interaction.response.send_message("請選擇欲建立的推播類型：", view=view, ephemeral=True)
                return

        # 情境 2: action = delete (刪除任務)
        elif action_val == "delete":
            if context and context.strip():
                target = self._find_trigger_by_id_prefix(context)
                if target:
                    self.service.delete_trigger(target.trigger_id)
                    await interaction.response.send_message(
                        f"🗑️ 已成功刪除推播任務 `[{target.trigger_type.upper()}]` `{target.trigger_id[:8]}`！",
                        ephemeral=True,
                    )
                    return
                else:
                    await interaction.response.send_message(
                        f"❌ 找不到開頭為 `{context}` 的推播任務。請使用 `/spark action:list` 查看所有任務。",
                        ephemeral=True,
                    )
                    return
            else:
                # 無指定 ID 時彈出專屬刪除下拉選單
                act_view = SparkActionSelectView(self.service, "delete")
                await interaction.response.send_message(
                    "🗑️ 請在下方下拉選單點選欲刪除的任務：",
                    view=act_view,
                    ephemeral=True,
                )
                return

        # 情境 3: action = edit (編輯任務)
        elif action_val == "edit":
            if context and context.strip():
                target = self._find_trigger_by_id_prefix(context)
                if target:
                    ttype = target.trigger_type
                    initial_data = dict(target.condition)
                    initial_data["message"] = target.message
                    if ttype == "price":
                        await interaction.response.send_modal(PriceTriggerModal(self.service, initial_data=initial_data, trigger_id_to_replace=target.trigger_id))
                    elif ttype == "schedule":
                        await interaction.response.send_modal(ScheduleTriggerModal(self.service, initial_data=initial_data, trigger_id_to_replace=target.trigger_id))
                    elif ttype == "rss":
                        await interaction.response.send_modal(RssTriggerModal(self.service, initial_data=initial_data, trigger_id_to_replace=target.trigger_id))
                    elif ttype == "weather":
                        await interaction.response.send_modal(WeatherTriggerModal(self.service, initial_data=initial_data, trigger_id_to_replace=target.trigger_id))
                    return
                else:
                    await interaction.response.send_message(
                        f"❌ 找不到開頭為 `{context}` 的推播任務。",
                        ephemeral=True,
                    )
                    return
            else:
                act_view = SparkActionSelectView(self.service, "edit")
                await interaction.response.send_message(
                    "✏️ 請在下方下拉選單點選欲編輯的任務：",
                    view=act_view,
                    ephemeral=True,
                )
                return

        # 情境 4: action = toggle (切換暫停/啟用)
        elif action_val == "toggle":
            if context and context.strip():
                target = self._find_trigger_by_id_prefix(context)
                if target:
                    updated = self.service.toggle_trigger(target.trigger_id)
                    state_str = "🟢 啟用" if updated and updated.is_active else "⏸️ 暫停"
                    await interaction.response.send_message(
                        f"已將任務 `{target.trigger_id[:8]}` 切換為 **{state_str}** 狀態！",
                        ephemeral=True,
                    )
                    return
                else:
                    await interaction.response.send_message(
                        f"❌ 找不到開頭為 `{context}` 的推播任務。",
                        ephemeral=True,
                    )
                    return
            else:
                act_view = SparkActionSelectView(self.service, "toggle")
                await interaction.response.send_message(
                    "⏯️ 請在下方下拉選單點選欲切換狀態的任務：",
                    view=act_view,
                    ephemeral=True,
                )
                return

        # 情境 5: action = list (任務清單)
        elif action_val == "list":
            triggers = self.service.list_triggers()
            embed = discord.Embed(
                title="📋 Spark 推播任務清單",
                description=f"目前共有 **{len(triggers)}** 個推播任務：",
                color=discord.Color.blue(),
            )
            if not triggers:
                embed.description = (embed.description or "") + "\n\n*尚未建立任何推播任務。*"
            else:
                for t in triggers[:20]:
                    st = "🟢 啟用" if t.is_active else "⏸️ 暫停"
                    cond_str = ", ".join(f"{k}:{v}" for k, v in list(t.condition.items())[:2])
                    embed.add_field(
                        name=f"[{t.trigger_type.upper()}] `{t.trigger_id[:8]}` ({st})",
                        value=f"• 頻道: <#{t.channel_id}>\n• 條件: `{cond_str}`" + (f"\n• 訊息: {t.message}" if t.message else ""),
                        inline=False,
                    )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # 情境 6: action = panel 或預設 (全合一主控儀表板)
        else:
            main_view = SparkMainView(self.service, channel_id=interaction.channel_id)
            panel_embed = main_view.build_dashboard_embed()
            await interaction.response.send_message(embed=panel_embed, view=main_view, ephemeral=True)

    # ----------------------------------------------------------------------
    # 2. 傳統前綴文字指令 (!spark) — 支援純 CLI 操作
    # ----------------------------------------------------------------------
    @commands.command(name="spark", aliases=["sp", "推播"])
    async def spark_prefix(self, ctx: commands.Context[Any], action: str = "panel", *, args: str = "") -> None:
        """Spark 傳統文字 CLI 指令入口。
        
        用法範例：
          !spark                                  (開啟主控台)
          !spark list                             (列出所有任務)
          !spark delete 4e7b1379                  (刪除指定任務)
          !spark toggle 4e7b1379                  (暫停/啟用任務)
          !spark test 4e7b1379                    (立即測試任務)
          !spark add NVDA 100元時叫我              (一句話新增)
          !spark NVDA 100元時叫我                 (直接一句話新增)
        """
        act = action.lower().strip()
        channel_id = ctx.channel.id if ctx.channel else 0
        user_id = ctx.author.id if ctx.author else 0

        # A. !spark list
        if act == "list":
            triggers = self.service.list_triggers()
            embed = discord.Embed(
                title="📋 Spark 推播任務清單",
                description=f"目前共有 **{len(triggers)}** 個推播任務：",
                color=discord.Color.blue(),
            )
            if not triggers:
                embed.description = (embed.description or "") + "\n\n*尚未建立任何推播任務。*"
            else:
                for t in triggers[:20]:
                    st = "🟢 啟用" if t.is_active else "⏸️ 暫停"
                    cond_str = ", ".join(f"{k}:{v}" for k, v in list(t.condition.items())[:2])
                    embed.add_field(
                        name=f"[{t.trigger_type.upper()}] `{t.trigger_id[:8]}` ({st})",
                        value=f"• 頻道: <#{t.channel_id}>\n• 條件: `{cond_str}`" + (f"\n• 訊息: {t.message}" if t.message else ""),
                        inline=False,
                    )
            await ctx.reply(embed=embed, mention_author=False)
            return

        # B. !spark delete <id> / !spark del <id>
        elif act in ("delete", "del", "remove", "rm"):
            target_id = args.strip()
            if not target_id:
                await ctx.reply("❌ 請提供欲刪除的任務 ID（例如 `!spark delete 4e7b1379`）", mention_author=True)
                return
            target = self._find_trigger_by_id_prefix(target_id)
            if target:
                self.service.delete_trigger(target.trigger_id)
                await ctx.reply(f"🗑️ 已成功刪除推播任務 `[{target.trigger_type.upper()}]` `{target.trigger_id[:8]}`！", mention_author=False)
            else:
                await ctx.reply(f"❌ 找不到開頭為 `{target_id}` 的推播任務。", mention_author=True)
            return

        # C. !spark toggle <id> / pause / resume
        elif act in ("toggle", "pause", "resume"):
            target_id = args.strip()
            if not target_id:
                await ctx.reply("❌ 請提供欲切換狀態的任務 ID（例如 `!spark toggle 4e7b1379`）", mention_author=True)
                return
            target = self._find_trigger_by_id_prefix(target_id)
            if target:
                updated = self.service.toggle_trigger(target.trigger_id)
                state_str = "🟢 啟用" if updated and updated.is_active else "⏸️ 暫停"
                await ctx.reply(f"已將任務 `{target.trigger_id[:8]}` 切換為 **{state_str}** 狀態！", mention_author=False)
            else:
                await ctx.reply(f"❌ 找不到開頭為 `{target_id}` 的推播任務。", mention_author=True)
            return

        # D. !spark test <id>
        elif act == "test":
            target_id = args.strip()
            if not target_id:
                await ctx.reply("❌ 請提供欲測試的任務 ID（例如 `!spark test 4e7b1379`）", mention_author=True)
                return
            target = self._find_trigger_by_id_prefix(target_id)
            if not target:
                await ctx.reply(f"❌ 找不到開頭為 `{target_id}` 的推播任務。", mention_author=True)
                return
            result, _ = await self.service.check_trigger(target)
            icon = "✅" if result.should_fire else "❌"
            await ctx.reply(
                f"🔍 **觸發器測試成果** (`{target.trigger_id[:8]}`):\n"
                f"- 是否達觸發條件: **{icon} {'滿足條件 (應觸發)' if result.should_fire else '未達條件'}**\n"
                f"- 摘要說明: {result.current_value}",
                mention_author=False,
            )
            return

        # E. !spark add <context> 或 直接輸入一句話自然語言
        elif act == "add" or (act not in ("panel", "help") and not args):
            full_context = args.strip() if act == "add" else f"{action} {args}".strip()
            if not full_context:
                view = SparkTypeSelectView(self.service)
                await ctx.reply("請選擇欲建立的推播類型：", view=view, mention_author=False)
                return

            candidate = await parse_spark_context(
                context=full_context,
                user_id=user_id,
                channel_id=channel_id,
                bot=self.bot,
            )
            if candidate is not None:
                confirm_view = SparkConfirmView(
                    service=self.service,
                    candidate=candidate,
                    author_id=user_id,
                )
                preview_embed = confirm_view.build_preview_embed()
                await ctx.reply(embed=preview_embed, view=confirm_view, mention_author=False)
            else:
                view = SparkTypeSelectView(self.service)
                await ctx.reply(
                    f"🤔 名偵探暫時無法直接理解「`{full_context}`」的推播條件。\n"
                    f"請透過下方選單選擇類型填寫表單：",
                    view=view,
                    mention_author=False,
                )
            return

        # F. !spark panel 或預設
        else:
            main_view = SparkMainView(self.service, channel_id=channel_id)
            panel_embed = main_view.build_dashboard_embed()
            await ctx.reply(embed=panel_embed, view=main_view, mention_author=False)


async def setup(bot: commands.Bot) -> None:
    """Discord 擴充模組標準掛載入口。"""
    await bot.add_cog(SparkCog(bot))
