"""handlers/spark/cog.py - Spark 推播系統 Discord Cog (Presentation 層)。

支援全互動 UI（View、Select Menu、Modal 表單、預覽確認卡片）與純文字 CLI 指令。
符合 AGENTS.md §0，此為模組內唯一允許引用 discord 的 Presentation 檔案。
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import settings
from core.autonomy_guard import AutonomyGuard, global_autonomy_guard
from core.leader_lease import LeaderLease
from handlers.social.observability import (
    current_trace_id,
    log_event,
    new_trace_id,
)

from .checkers import is_market_open
from .clients import RssClient, StockClient, WeatherClient, WebMonitorClient
from .config import DEFAULT_CHECK_INTERVAL_MINUTES, DEFAULT_COOLDOWN_SECONDS
from .models import Trigger
from .nlp import parse_spark_context
from .repository import SparkRepository
from .service import SparkQuotaExceeded, SparkService, build_spark_service

logger = logging.getLogger(__name__)

# 台灣/香港標準時區 (UTC+8)
TAIPEI_TZ = timezone(timedelta(hours=8))


def format_spark_time_display(time_val: str) -> str:
    """將 ISO 或各類時間字串格式化為包含 UTC+8 的親切易讀格式。"""
    if not time_val:
        return ""
    s = str(time_val).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        dt_tw = dt.astimezone(TAIPEI_TZ)
        return dt_tw.strftime("%Y-%m-%d %H:%M (UTC+8)")
    except Exception:
        return f"{s} (UTC+8)" if "UTC" not in s else s


def format_condition_preview(cond: dict[str, Any]) -> str:
    """格式化推播條件字典為易讀字串（含 UTC+8 標示）。"""
    mode = str(cond.get("mode") or "").lower().strip()
    suffix = ""
    if "max_runs" in cond:
        suffix = f" (最多 {cond['max_runs']} 次)"

    if mode in ("once", "one_shot", "timestamp"):
        t = cond.get("target_time") or cond.get("trigger_time") or cond.get("time") or ""
        return f"單次提醒: {format_spark_time_display(str(t))}{suffix}"
    elif mode in ("daily_time", "daily", "fixed_time"):
        t = cond.get("time", "08:00")
        dow_desc = "每日"
        dow = cond.get("days_of_week")
        if dow == [0, 1, 2, 3, 4]:
            dow_desc = "工作日"
        elif dow == [5, 6]:
            dow_desc = "週末"
        elif isinstance(dow, list) and dow:
            day_names = ["一", "二", "三", "四", "五", "六", "日"]
            dow_desc = f"每週{'、'.join(day_names[d] for d in dow if 0 <= d <= 6)}"
        return f"{dow_desc}定時: {t} (UTC+8){suffix}"
    elif mode in ("interval", "periodic"):
        return f"循環間隔: 每 {cond.get('interval_minutes')} 分鐘{suffix}"
    elif "ticker" in cond:
        target_p = cond.get("target_price", 0)
        direction = "高於" if cond.get("direction") in ("above", "cross_above", ">=", ">") else "低於"
        return f"{cond.get('ticker')} {direction} ${target_p:,.2f}{suffix}"
    elif "feed_url" in cond:
        return f"RSS 訂閱: {cond.get('feed_url')[:30]}..."
    elif "query" in cond and ("ai_condition" in cond or "interval_minutes" in cond):
        ai_c = f"（條件：{cond['ai_condition']}）" if cond.get("ai_condition") else ""
        return f"智慧巡檢: 每 {cond.get('interval_minutes', 30)} 分鐘監測「{cond.get('query')}」{ai_c}{suffix}"
    else:
        return ", ".join(f"{k}:{v}" for k, v in list(cond.items())[:2]) + suffix


# ===========================================================================
# 1. 彈出式輸入表單 (Discord Modals)
# ===========================================================================

async def _send_quota_error(
    interaction: discord.Interaction,
    exc: SparkQuotaExceeded,
) -> None:
    """以 ephemeral 訊息回報 Spark 配額超限（避免 Interaction 無回應）。"""
    await interaction.response.send_message(f"⚠️ **Spark 配額已滿**：{exc}", ephemeral=True)


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

        try:
            trigger = self.service.create_trigger(
                user_id=interaction.user.id,
                channel_id=interaction.channel_id or 0,
                trigger_type="price",
                condition=condition,
                message=self.message_input.value.strip(),
                cooldown_seconds=DEFAULT_COOLDOWN_SECONDS,
            )
        except SparkQuotaExceeded as exc:
            await _send_quota_error(interaction, exc)
            return

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

        try:
            trigger = self.service.create_trigger(
                user_id=interaction.user.id,
                channel_id=interaction.channel_id or 0,
                trigger_type="schedule",
                condition=condition,
                message=self.message_input.value.strip(),
                cooldown_seconds=60,
            )
        except SparkQuotaExceeded as exc:
            await _send_quota_error(interaction, exc)
            return

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

        try:
            trigger = self.service.create_trigger(
                user_id=interaction.user.id,
                channel_id=interaction.channel_id or 0,
                trigger_type="rss",
                condition=condition,
                message=self.message_input.value.strip(),
                cooldown_seconds=300,
            )
        except SparkQuotaExceeded as exc:
            await _send_quota_error(interaction, exc)
            return

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
                logger.debug("[Spark] 天氣閾值無法解析為數字: %s", thresh_val, exc_info=True)

        if self.trigger_id_to_replace:
            self.service.delete_trigger(self.trigger_id_to_replace)

        try:
            trigger = self.service.create_trigger(
                user_id=interaction.user.id,
                channel_id=interaction.channel_id or 0,
                trigger_type="weather",
                condition=condition,
                cooldown_seconds=DEFAULT_COOLDOWN_SECONDS,
            )
        except SparkQuotaExceeded as exc:
            await _send_quota_error(interaction, exc)
            return

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
            cond_desc_lines.append("• **類型**: 💰 股票/加密貨幣價格警報")
            cond_desc_lines.append(f"• **標的**: `{ticker}`")
            cond_desc_lines.append(f"• **觸發條件**: `{dir_text} ${target:,.2f}`")
            cond_desc_lines.append("• **交易時段過濾**: 盤中監控 (休市自動暫停)")
            cond_desc_lines.append("• **觸發模式**: 單次達標後自動完成 (One-shot)")
        elif ttype == "schedule":
            mode = cond.get("mode", "interval")
            max_r = cond.get("max_runs")
            max_r_str = f"（最多執行 {max_r} 次）" if max_r else ""
            cond_desc_lines.append(f"• **類型**: ⏰ 定時排程推播任務 {max_r_str}".strip())

            dow = cond.get("days_of_week")
            dow_desc = ""
            if dow == [0, 1, 2, 3, 4]:
                dow_desc = "【工作日】"
            elif dow == [5, 6]:
                dow_desc = "【週末】"
            elif isinstance(dow, list) and dow:
                day_names = ["一", "二", "三", "四", "五", "六", "日"]
                dow_desc = f"【每週{'、'.join(day_names[d] for d in dow if 0 <= d <= 6)}】"

            if mode == "interval":
                cond_desc_lines.append(f"• **排程模式**: `循環間隔`（{dow_desc}每 `{cond.get('interval_minutes')}` 分鐘）")
            elif mode in ("once", "one_shot", "timestamp"):
                t_raw = cond.get("target_time") or cond.get("time") or ""
                formatted_time = format_spark_time_display(str(t_raw))
                cond_desc_lines.append(f"• **排程模式**: `單次提醒`（預定時間: `{formatted_time}`）")
                cond_desc_lines.append("• **生命週期**: 到期觸發後自動封存結案 (One-shot)")
            else:
                daily_t = cond.get("time", "08:00")
                cond_desc_lines.append(f"• **排程模式**: `每日定時`（{dow_desc}每日 `{daily_t}` UTC+8）")
        elif ttype == "web_monitor":
            cond_desc_lines.append("• **類型**: 🔎 智慧網頁巡檢守門員")
            cond_desc_lines.append(f"• **監控目標**: `{cond.get('query', '最新消息')}`")
            cond_desc_lines.append(f"• **巡檢頻率**: 每 `{cond.get('interval_minutes', 30)}` 分鐘主動檢索")
            if cond.get("ai_condition"):
                cond_desc_lines.append(f"• **AI 語意條件**: `{cond.get('ai_condition')}`")
            cond_desc_lines.append("• **去重防擾**: SHA-256 內容指紋比對（零重複洗版）")
        elif ttype == "weather":
            cond_desc_lines.append("• **類型**: 🌤️ 即時天氣預警")
            cond_desc_lines.append(f"• **城市**: `{cond.get('city')}`")
            cond_desc_lines.append(f"• **預警類型**: `{cond.get('alert_type')}`")
            if "threshold" in cond:
                cond_desc_lines.append(f"• **門檻數值**: `{cond.get('threshold')}`")
        elif ttype == "rss":
            cond_desc_lines.append("• **類型**: 📰 RSS / 內容訂閱")
            cond_desc_lines.append(f"• **Feed 網址**: `{cond.get('feed_url')}`")
            if cond.get("keyword"):
                cond_desc_lines.append(f"• **關鍵字過濾**: `{cond.get('keyword')}`")

        # 動作分流預覽
        action = self.candidate.get("action") or {}
        action_type = str(action.get("action_type") or "notify").lower()
        if action_type == "mod_deep_search":
            topic = action.get("payload", {}).get("topic") or self.candidate.get("message") or "未指定"
            cond_desc_lines.append("• **觸發動作**: 🔬 【特權模組】多輪自主深度研究")
            cond_desc_lines.append(f"• **研究主題**: `{topic}`")
        elif action_type == "agent_prompt":
            prompt = action.get("payload", {}).get("prompt") or self.candidate.get("message") or "未指定"
            cond_desc_lines.append("• **觸發動作**: 🤖 【自主代理】`AGENT_PROMPT` (多工具自主調度)")
            cond_desc_lines.append(f"• **任務指令**: `{prompt}`")
        elif action_type == "core_tool":
            from handlers.social.tool_specs import resolve_tool_name
            target = resolve_tool_name(str(action.get("target") or "TOOL").upper().strip())
            query = action.get("payload", {}).get("query") or self.candidate.get("message") or ""
            if target == "CHAT":
                cond_desc_lines.append("• **觸發動作**: 💬 【名偵探對話】`CHAT` (動態人設發話)")
            elif target == "SEARCH":
                cond_desc_lines.append("• **觸發動作**: 🛠️ 【核心工具】`SEARCH` (強制即時聯網檢索)")
            else:
                cond_desc_lines.append(f"• **觸發動作**: 🛠️ 【核心工具】`{target}`")
            if query:
                cond_desc_lines.append(f"• **工具參數**: `{query}`")
        else:
            cond_desc_lines.append("• **觸發動作**: 📢 訊息/卡片推播通知")

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
        try:
            trigger = self.service.create_trigger(
                user_id=self.candidate["user_id"],
                channel_id=self.candidate["channel_id"],
                trigger_type=self.candidate["trigger_type"],
                condition=self.candidate["condition"],
                message=self.candidate.get("message", ""),
                cooldown_seconds=self.candidate.get("cooldown_seconds", 3600),
                action=self.candidate.get("action", {}),
            )
        except SparkQuotaExceeded as exc:
            await _send_quota_error(interaction, exc)
            return
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
                cond_preview = format_condition_preview(t.condition)
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
        result, _ = await self.service.check_trigger(trigger, dry_run=True)
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
# 2.5 動作執行分發器 (Presentation Action Runner)
# ===========================================================================

class _DummyAuthor:
    """供模擬上下文使用之虛擬作者。"""

    def __init__(self, user_id: int = 0, name: str = "SparkTrigger", bot: Any = None) -> None:
        self.id = user_id
        display_name = name
        if bot and user_id:
            try:
                user = bot.get_user(user_id)
                if user:
                    display_name = user.display_name
            except Exception:
                logger.debug(
                    "[Spark] 取得使用者顯示名稱失敗（user_id=%s）", user_id, exc_info=True
                )
        self.display_name = display_name
        self.name = display_name
        self.mention = f"<@{user_id}>" if user_id else "@Spark"
        self.bot = False


class SparkChannelContext:
    """提供給 Core Tool 執行的通道上下文，模擬 discord.Message 以便工具直接發送。"""

    def __init__(
        self,
        channel: Any,
        user_id: int = 0,
        bot: Any = None,
        follow_up_view: discord.ui.View | None = None,
    ) -> None:
        self.channel = channel
        self.author = _DummyAuthor(user_id=user_id, bot=bot)
        self.id = int(time.time() * 1000)
        self.clean_content = ""
        self.content = ""
        self.guild = getattr(channel, "guild", None)
        self.reference = None
        self.follow_up_view = follow_up_view
        self._has_mentioned = False

    async def reply(self, *args: Any, **kwargs: Any) -> Any:
        """轉導 reply 至 channel.send，並主動 @Tag 委託人與掛載互動按鈕。"""
        kwargs.pop("mention_author", None)
        new_args = list(args)
        content = kwargs.get("content")

        if new_args:
            first_arg = new_args[0]
            if isinstance(first_arg, str) and self.author.id and not self._has_mentioned:
                mention = f"<@{self.author.id}>"
                if mention not in first_arg:
                    first_arg = f"{mention} {first_arg}"
                self._has_mentioned = True
                new_args[0] = first_arg
        elif content is not None:
            if self.author.id and not self._has_mentioned:
                mention = f"<@{self.author.id}>"
                if mention not in str(content):
                    content = f"{mention} {content}"
                self._has_mentioned = True
                kwargs["content"] = content
        elif self.author.id and not self._has_mentioned:
            kwargs["content"] = f"<@{self.author.id}>"
            self._has_mentioned = True

        kwargs.setdefault("allowed_mentions", discord.AllowedMentions(users=True))
        if self.follow_up_view and "view" not in kwargs:
            kwargs["view"] = self.follow_up_view
            self.follow_up_view = None

        if hasattr(self.channel, "send"):
            return await self.channel.send(*new_args, **kwargs)
        return None

    send = reply

    async def add_reaction(self, emoji: str) -> None:
        """忽略反應添加。"""
        pass


class SparkFollowUpView(discord.ui.View):
    """推播觸發後的互動延伸按鈕 View（提供貪睡10分鐘、轉深度研究與停用排程）。"""

    def __init__(
        self,
        bot: commands.Bot,
        trigger: Trigger,
        service: Optional[SparkService] = None,
        timeout: float = 300.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.bot = bot
        self.trigger = trigger
        self.service = service

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.trigger.user_id and interaction.user.id != self.trigger.user_id:
            await interaction.response.send_message("⚠️ 只有設定此推播排程的委託人才能操作此按鈕喔！", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="💤 延後 10 分鐘", style=discord.ButtonStyle.secondary)
    async def btn_snooze(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """一鍵以原動作與訊息建立一個 10 分鐘後的單次 (once) 排程。"""
        if not self.service:
            await interaction.response.send_message("❌ 服務未初始化，無法延後。", ephemeral=True)
            return

        now_utc = datetime.now(timezone.utc)
        target_time = (now_utc + timedelta(minutes=10)).astimezone(TAIPEI_TZ).isoformat()
        new_cond = {"mode": "once", "target_time": target_time, "tz_offset": 8}

        try:
            new_trig = self.service.create_trigger(
                user_id=self.trigger.user_id,
                channel_id=self.trigger.channel_id,
                trigger_type="schedule",
                condition=new_cond,
                message=self.trigger.message,
                cooldown_seconds=60,
                action=self.trigger.action,
            )
        except SparkQuotaExceeded as exc:
            await _send_quota_error(interaction, exc)
            return
        button.disabled = True
        try:
            if interaction.message:
                await interaction.message.edit(view=self)
        except Exception:
            logger.debug("[Spark] 更新延後提醒按鈕狀態失敗", exc_info=True)
        await interaction.response.send_message(
            f"💤 已成功為您延後 10 分鐘！新的單次提醒任務 ID 為 `{new_trig.trigger_id[:8]}`～",
            ephemeral=True,
        )

    @discord.ui.button(label="🔬 轉深度研究", style=discord.ButtonStyle.primary)
    async def btn_deep_search(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """針對本次主題一鍵啟動 DeepSearch 多輪自主研究。"""
        payload = self.trigger.action.get("payload", {}) if isinstance(self.trigger.action, dict) else {}
        topic = (
            payload.get("query")
            or payload.get("topic")
            or payload.get("prompt")
            or self.trigger.message
            or "最新情報研究"
        )
        deep_search_cog: Any = getattr(self.bot, "_deep_search_cog", None) or self.bot.get_cog("DeepSearch")
        if not deep_search_cog:
            await interaction.response.send_message("⚠️ DeepSearch 模組未載入，無法啟動深度研究。", ephemeral=True)
            return

        button.disabled = True
        try:
            if interaction.message:
                await interaction.message.edit(view=self)
        except Exception:
            logger.debug("[Spark] 更新深度研究按鈕狀態失敗", exc_info=True)
        await interaction.response.defer(ephemeral=True)
        runner = SparkActionRunner(self.bot, service=self.service)
        deep_action = {
            "action_type": "mod_deep_search",
            "payload": {"topic": topic, "rounds": 3},
        }
        await runner._run_deep_search(self.trigger, interaction.channel, deep_action)
        try:
            await interaction.followup.send(f"🔬 已針對主題「`{topic}`」啟動多輪自主深度研究！", ephemeral=True)
        except Exception:
            logger.debug("[Spark] 發送深度研究啟動提示失敗", exc_info=True)

    @discord.ui.button(label="⏹️ 停用此排程", style=discord.ButtonStyle.danger)
    async def btn_stop(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """針對週期性任務一鍵切換為暫停/停用。"""
        if not self.service:
            await interaction.response.send_message("❌ 服務未初始化，無法停用。", ephemeral=True)
            return

        trig = self.service.get_trigger(self.trigger.trigger_id)
        if not trig:
            await interaction.response.send_message("❌ 找不到該排程任務。", ephemeral=True)
            return

        if trig.is_active:
            self.service.toggle_trigger(trig.trigger_id)

        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]

        try:
            if interaction.message:
                await interaction.message.edit(view=self)
        except Exception:
            logger.debug("[Spark] 更新停用排程按鈕狀態失敗", exc_info=True)

        await interaction.response.send_message(
            f"⏹️ 已成功為您停用此排程任務 `[{trig.trigger_type.upper()}]` `{trig.trigger_id[:8]}`！",
            ephemeral=True,
        )


class SparkActionRunner:
    """Spark 觸發動作執行分發器。"""

    def __init__(self, bot: commands.Bot, service: Optional[SparkService] = None) -> None:
        self.bot = bot
        self.service = service

    async def run_action(self, trigger: Trigger, content: str) -> bool:
        """根據 Trigger 設定執行對應動作。

        Args:
            trigger: 觸發的 Trigger 實例。
            content: 觸發資訊摘要文字。

        Returns:
            bool: 動作是否成功派發執行。
        """
        channel = self.bot.get_channel(trigger.channel_id)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(trigger.channel_id)
            except Exception as exc:
                logger.error("[SparkActionRunner] 無法取得目標頻道 (%s): %s", trigger.channel_id, exc)
                return False

        action_dict = dict(trigger.action) if isinstance(trigger.action, dict) else {}
        raw_type = action_dict.get("action_type") or getattr(trigger, "action_type", None)

        # 向後相容例外防護：若歷史資料完全缺少 action 定義，且 trigger.message 包含明確核心工具關鍵詞
        if not raw_type and trigger.message:
            from handlers.spark.nlp import detect_action_from_message_fallback
            inferred = detect_action_from_message_fallback(trigger.message)
            if inferred.get("action_type") == "core_tool" and inferred.get("target") != "CHAT":
                logger.info(
                    "[SparkActionRunner] 歷史任務缺漏 Action 自動相容推導: %s -> %s (%s)",
                    trigger.trigger_id[:8],
                    inferred.get("action_type"),
                    inferred.get("target"),
                )
                action_dict = inferred
                raw_type = inferred["action_type"]
            elif inferred.get("action_type") in ("mod_deep_search", "agent_prompt"):
                action_dict = inferred
                raw_type = inferred["action_type"]

        act_type = str(raw_type or "notify").lower().strip()

        logger.info(
            "[SparkActionRunner] 執行動作: trigger_id=%s, type=%s, target=%s",
            trigger.trigger_id[:8],
            act_type,
            action_dict.get("target"),
        )

        if act_type in ("mod_deep_search", "deep_search"):
            return await self._run_deep_search(trigger, channel, action_dict)
        elif act_type in ("agent_prompt", "agent"):
            return await self._run_agent_prompt(trigger, channel, action_dict, content)
        elif act_type in ("core_tool", "tool"):
            return await self._run_core_tool(trigger, channel, action_dict, content)
        else:
            return await self._run_notify(trigger, channel, content)

    async def _run_notify(self, trigger: Trigger, channel: Any, content: str) -> bool:
        """執行純文字或 Embed 推播通知。"""
        if not hasattr(channel, "send"):
            return False

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
        else:
            mode = str(trigger.condition.get("mode", "")).lower()
            if mode in ("once", "one_shot"):
                embed.add_field(
                    name="🎉 任務完成",
                    value="本排程為單次提醒，已成功觸發並自動結案存檔～",
                    inline=False,
                )

        if trigger.message:
            embed.add_field(name="💬 備忘/訊息", value=trigger.message, inline=False)

        embed.set_footer(text=f"Trigger ID: {trigger.trigger_id[:8]}")

        view = SparkFollowUpView(self.bot, trigger, self.service) if getattr(self, "service", None) else None

        try:
            await channel.send(
                content=f"<@{trigger.user_id}>",
                embed=embed,
                allowed_mentions=discord.AllowedMentions(users=True),
                view=view,
            )
            return True
        except Exception as exc:
            logger.error("[SparkActionRunner] 發送推播 Embed 失敗 (%s): %s", trigger.trigger_id, exc)
            return False

    async def _run_core_tool(
        self,
        trigger: Trigger,
        channel: Any,
        action_dict: dict[str, Any],
        trigger_summary: str,
    ) -> bool:
        """調用核心日常工具 (如 PIXIV, STOCK, SEARCH, DRAW, CHAT, MUSIC)。"""
        raw_target_name = str(action_dict.get("target") or "").upper().strip()
        from handlers.social.tool_specs import resolve_tool_name
        target_tool_name = resolve_tool_name(raw_target_name)
        payload = action_dict.get("payload") or {}
        query = str(payload.get("query") or trigger.message or "").strip()

        # 若非 CHAT/SEARCH 對話，發送定時調度提示橫幅；若為 CHAT/SEARCH 則免去橫幅，讓名偵探自然發話！
        if target_tool_name not in ("CHAT", "SEARCH"):
            try:
                intro_embed = discord.Embed(
                    title=f"📡 Spark 定時調度：發動核心工具【{target_tool_name}】",
                    description=f"觸發依據：{trigger_summary}\n查詢參數：`{query}`",
                    color=discord.Color.teal(),
                    timestamp=datetime.now(timezone.utc),
                )
                intro_embed.set_footer(text=f"Trigger ID: {trigger.trigger_id[:8]}")
                await channel.send(
                    content=f"<@{trigger.user_id}>",
                    embed=intro_embed,
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
            except Exception:
                logger.debug(
                    "[SparkActionRunner] 發送核心工具啟動訊息失敗（trigger=%s）",
                    trigger.trigger_id[:8],
                    exc_info=True,
                )

        from handlers.social.registry import TOOL_LIST

        target_tool = None
        for t in TOOL_LIST:
            if t.intent_name.upper() == target_tool_name:
                target_tool = t
                break

        if not target_tool:
            logger.error("[SparkActionRunner] 找不到指定核心工具: %s", target_tool_name)
            await channel.send(f"<@{trigger.user_id}> ⚠️ Spark 執行失敗：找不到核心工具 `{target_tool_name}`")
            return False

        view = SparkFollowUpView(self.bot, trigger, self.service) if getattr(self, "service", None) else None
        ctx_msg = SparkChannelContext(channel=channel, user_id=trigger.user_id, bot=self.bot, follow_up_view=view)
        try:
            kwargs: dict[str, Any] = {
                "bot": self.bot,
                "message": ctx_msg,
                "user_query": query,
                "vibe_snippet": "",
                "full_context": f"Spark 定時自動觸發工具 {target_tool_name}，查詢為：{query}",
            }
            if payload.get("force_search") or target_tool_name == "SEARCH":
                kwargs["force_search"] = True
            prev_mem = trigger.extra_data.get("last_output_summary", "") if isinstance(trigger.extra_data, dict) else ""
            if prev_mem:
                kwargs["spark_previous_memory"] = prev_mem

            res = await target_tool.execute(**kwargs)
            logger.info("[SparkActionRunner] 核心工具 %s 執行完成: %s", target_tool_name, res)

            # 更新跨輪記憶
            summary = ""
            if isinstance(res, str):
                summary = res
            elif hasattr(res, "content") and res.content:
                summary = str(res.content)
            if summary and getattr(self, "service", None):
                self.service.update_trigger_memory(trigger.trigger_id, summary)

            return True
        except Exception as exc:
            logger.error("[SparkActionRunner] 執行核心工具 %s 異常: %s", target_tool_name, exc, exc_info=True)
            await channel.send(f"<@{trigger.user_id}> ❌ Spark 執行核心工具 `{target_tool_name}` 發生異常：{exc}")
            return False

    async def _run_agent_prompt(
        self,
        trigger: Trigger,
        channel: Any,
        action_dict: dict[str, Any],
        trigger_summary: str,
    ) -> bool:
        """調用 Agentic Spark 多工具自主調度 (IntegratedStrategy)。"""
        payload = action_dict.get("payload") or {}
        prompt = str(payload.get("prompt") or trigger.message or "").strip()
        if not prompt:
            prompt = trigger_summary

        view = SparkFollowUpView(self.bot, trigger, self.service) if getattr(self, "service", None) else None
        ctx_msg = SparkChannelContext(channel=channel, user_id=trigger.user_id, bot=self.bot, follow_up_view=view)
        ctx_msg.clean_content = prompt

        from handlers.social.registry import TOOL_LIST
        from handlers.social.dispatch_agent import DispatchAgentTool

        dispatch_agent = DispatchAgentTool(TOOL_LIST, mode="INTEGRATED")
        try:
            kwargs: dict[str, Any] = {
                "bot": self.bot,
                "message": ctx_msg,
                "user_query": prompt,
                "vibe_snippet": "",
                "full_context": f"Spark 定時自主代理任務：{prompt}",
            }
            prev_mem = trigger.extra_data.get("last_output_summary", "") if isinstance(trigger.extra_data, dict) else ""
            if prev_mem:
                kwargs["spark_previous_memory"] = prev_mem

            res = await dispatch_agent.execute(**kwargs)
            summary = ""
            if isinstance(res, str):
                summary = res
            elif hasattr(res, "content") and res.content:
                summary = str(res.content)
            if summary and getattr(self, "service", None):
                self.service.update_trigger_memory(trigger.trigger_id, summary)

            return True
        except Exception as exc:
            logger.error("[SparkActionRunner] 執行 Agent Prompt 異常: %s", exc, exc_info=True)
            await channel.send(f"<@{trigger.user_id}> ❌ Spark 執行自主代理任務發生異常：{exc}")
            return False

    async def _run_deep_search(
        self,
        trigger: Trigger,
        channel: Any,
        action_dict: dict[str, Any],
    ) -> bool:
        """特權調度多輪深度研究 (Deep Search)。"""
        payload = action_dict.get("payload") or {}
        topic = str(payload.get("topic") or trigger.message or "").strip()
        rounds = int(payload.get("rounds") or 3)
        rounds = max(2, min(6, rounds))

        if not topic:
            logger.warning("[SparkActionRunner] 深度研究缺少主題 topic")
            await channel.send(f"<@{trigger.user_id}> ⚠️ Spark 深度研究觸發失敗：未指定研究主題！")
            return False

        deep_search_cog: Any = getattr(self.bot, "_deep_search_cog", None) or self.bot.get_cog("DeepSearch")
        if not deep_search_cog:
            logger.error("[SparkActionRunner] DeepSearchCog 未掛載，無法發動深度研究")
            await channel.send(f"<@{trigger.user_id}> ⚠️ Spark 深度研究觸發失敗：系統未載入 DeepSearch 模組！")
            return False

        session_id = f"spark-{trigger.trigger_id[:8]}-{int(time.time())}"
        start_embed = discord.Embed(
            title="📡 Spark 條件觸發：已啟動【地端多輪自主深度研究】",
            description=(
                f"**研究主題**：{topic}\n"
                f"**探討輪數**：{rounds} 輪自主交叉研讀\n"
                f"**任務編號**：`{session_id}`\n\n"
                f"雪莉名偵探正在連網檢索、精讀文獻並自我反思驗證中，請稍候……"
            ),
            color=discord.Color.purple(),
            timestamp=datetime.now(timezone.utc),
        )
        start_embed.set_footer(text=f"Trigger ID: {trigger.trigger_id[:8]}")

        view = SparkFollowUpView(self.bot, trigger, self.service) if getattr(self, "service", None) else None
        initial_msg = None
        try:
            initial_msg = await channel.send(
                content=f"<@{trigger.user_id}>",
                embed=start_embed,
                allowed_mentions=discord.AllowedMentions(users=True),
                view=view,
            )
        except Exception as exc:
            logger.warning("[SparkActionRunner] 發送深度研究起始訊息失敗: %s", exc)

        import settings
        default_model = getattr(settings, "PRIMARY_MODEL", "gemini-2.5-flash")

        research_task = asyncio.create_task(
            deep_search_cog._run_research_flow(
                topic=topic,
                model=default_model,
                rounds=rounds,
                session_id=session_id,
                channel_id=channel.id,
                message_id=initial_msg.id if initial_msg else None,
                discord_message=initial_msg,
            ),
            name=f"spark-deep-search-{session_id}",
        )
        # 複審 2：深度研究同屬可取消的長任務，納入 DeepSearchCog 追蹤表，
        # 讓 Web UI 取消入口與 Cog 卸載能真正傳播 CancelledError。
        active_tasks = getattr(deep_search_cog, "_active_tasks", None)
        if isinstance(active_tasks, dict):
            active_tasks[session_id] = research_task

            def _discard_done(task: asyncio.Task[Any], sid: str = session_id) -> None:
                if active_tasks.get(sid) is task:
                    active_tasks.pop(sid, None)

            research_task.add_done_callback(_discard_done)
        return True


# ===========================================================================
# 3. SparkCog 本體：斜線指令 (/spark) 與前綴 CLI 指令 (!spark)
# ===========================================================================

class SparkCog(commands.Cog, name="SparkCog"):
    """Spark 泛用條件推播系統 Cog。"""

    def __init__(
        self,
        bot: commands.Bot,
        service: Optional[SparkService] = None,
        autonomy_guard: AutonomyGuard | None = None,
    ) -> None:
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
                "web_monitor": WebMonitorClient(),
            }
            self.service = build_spark_service(repo, clients)
        self.action_runner = SparkActionRunner(bot, service=self.service)
        # 治理閘門：派發前檢查風險／健康／熔斷／預算（預設共用全域單例）
        self._autonomy_guard = autonomy_guard or global_autonomy_guard
        # 動作任務的長生命週期並發上限與強引用集合（避免無界累積與 GC 靜默消失）
        try:
            max_actions = int(getattr(settings, "SPARK_MAX_CONCURRENT_ACTIONS", 4))
        except (TypeError, ValueError):
            max_actions = 4
        self._action_sem = asyncio.Semaphore(max(1, max_actions))
        self._action_tasks: set[asyncio.Task[Any]] = set()
        # R17：跨實例 leader 租約（防雙實例重複巡檢／重複推播）
        self._lease_started = False
        self._leader_task: asyncio.Task[Any] | None = None
        self._leader_lease = LeaderLease(
            "spark_scheduler",
            owner=f"{socket.gethostname()}:{os.getpid()}",
        )

    def _leader_ttl(self) -> float:
        try:
            return max(5.0, float(getattr(settings, "SPARK_LEADER_LEASE_TTL_SECONDS", 90.0)))
        except (TypeError, ValueError):
            return 90.0

    def _is_leader(self) -> bool:
        # 未經 cog_load（單元測試／CLI）時維持單行程語意。
        return (not self._lease_started) or self._leader_lease.is_leader()

    async def _leader_heartbeat_loop(self) -> None:
        try:
            interval = max(2.0, float(getattr(settings, "SPARK_LEADER_HEARTBEAT_SECONDS", 30.0)))
        except (TypeError, ValueError):
            interval = 30.0
        while True:
            try:
                if self._leader_lease.renew(self._leader_ttl()):
                    if not getattr(self, "_leader_logged", False):
                        logger.info("Spark leader 租約持有中（owner=%s）", self._leader_lease.owner)
                        self._leader_logged = True
                elif self._leader_lease.acquire(self._leader_ttl()):
                    logger.info("Spark 取得 leader 租約（owner=%s）", self._leader_lease.owner)
                    self._leader_logged = True
                else:
                    if getattr(self, "_leader_logged", False):
                        logger.warning("Spark 失去 leader 租約，巡檢暫停")
                    self._leader_logged = False
            except Exception as exc:
                logger.warning("Spark leader 心跳失敗：%s", exc)
            await asyncio.sleep(interval)

    async def cog_load(self) -> None:
        """Cog 載入時啟動背景巡檢排程迴圈。"""
        self._lease_started = True
        lease_enabled = bool(getattr(settings, "SPARK_LEADER_LEASE_ENABLED", True))
        acquired = self._leader_lease.acquire(self._leader_ttl()) if lease_enabled else False
        if not lease_enabled:
            self._lease_started = False
            logger.info("Spark leader 租約已停用（單實例模式）")
        logger.info(
            "Spark leader 租約%s（owner=%s）",
            "已取得" if acquired else ("停用" if not lease_enabled else "由其他實例持有，待接手"),
            self._leader_lease.owner,
        )
        if not self._spark_check_loop.is_running():
            self._spark_check_loop.start()
        if lease_enabled and (self._leader_task is None or self._leader_task.done()):
            self._leader_task = asyncio.create_task(self._leader_heartbeat_loop())
        logger.info("📡 Spark 推播系統 Cog 與巡檢排程已成功啟動！")

    async def cog_unload(self) -> None:
        """Cog 卸載時停止巡檢迴圈，並取消所有未完成的動作任務。"""
        if self._spark_check_loop.is_running():
            self._spark_check_loop.cancel()
        if self._leader_task is not None:
            self._leader_task.cancel()
            self._leader_task = None
        try:
            self._leader_lease.release()
        except Exception as exc:
            logger.debug("Spark leader 租約釋放失敗：%s", exc)
        pending = list(self._action_tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._action_tasks.clear()
        logger.info("📡 Spark 推播系統 Cog 已卸載（取消 %d 個未完成動作）。", len(pending))

    @tasks.loop(minutes=DEFAULT_CHECK_INTERVAL_MINUTES)
    async def _spark_check_loop(self) -> None:
        """每分鐘巡檢所有處於啟用狀態的 Trigger（並行無阻塞評估）。"""
        await self._run_check_cycle()

    async def _run_check_cycle(self) -> None:
        """執行單輪巡檢：並行評估所有啟用中的 Trigger，並派發達標動作。

        每個 checker 由 ``SparkService.check_trigger`` 套用個別 timeout；本輪
        另以 ``asyncio.timeout`` 設定整體 deadline，任何卡死都不會讓背景迴圈
        永久停擺。每輪結束後產生 trace 與執行摘要，寫入觀測與
        ``SparkService``（供 /api/spark/stats 查詢）。

        此處的 semaphore 僅覆蓋 checker 評估（網路 I/O）；真正耗時的動作
        交由 `self._action_sem` 的長生命週期 semaphore 控管，避免每輪重建。
        """
        active_triggers = self.service.list_active_triggers()
        if not active_triggers:
            return
        if not self._is_leader():
            logger.debug("Spark 非 leader，跳過本輪巡檢（owner=%s）", self._leader_lease.owner)
            return

        try:
            cycle_timeout = float(
                getattr(settings, "SPARK_CHECK_CYCLE_TIMEOUT_SECONDS", 60.0)
            )
        except (TypeError, ValueError):
            cycle_timeout = 60.0
        if cycle_timeout <= 0:
            cycle_timeout = 60.0

        try:
            max_checkers = max(
                1, int(getattr(settings, "SPARK_MAX_CONCURRENT_CHECKS", 8))
            )
        except (TypeError, ValueError):
            max_checkers = 8

        trace_id = new_trace_id("spark")
        summary: dict[str, Any] = {
            "triggers": len(active_triggers),
            "cooldown_skipped": 0,
            "fired": 0,
            "not_fired": 0,
            "checker_timeouts": 0,
            "failed": 0,
            "guard_rejected": 0,
            "quota_rejected": 0,
            "actions_dispatched": 0,
            "cycle_timed_out": False,
            "duration_ms": 0,
        }
        started = time.monotonic()
        sem = asyncio.Semaphore(max_checkers)  # 控制檢查並行度，防止網路突發阻塞

        async def _eval_trigger(trig: Trigger) -> None:
            async with sem:
                try:
                    if self.service.is_in_cooldown(trig):
                        summary["cooldown_skipped"] += 1
                        return

                    result, updated = await self.service.check_trigger(trig)
                    if not result.should_fire:
                        if bool((getattr(result, "detail_data", None) or {}).get("timeout")):
                            summary["checker_timeouts"] += 1
                        else:
                            summary["not_fired"] += 1
                        return

                    summary["fired"] += 1
                    target_trig = updated or trig
                    # 治理閘門與配額通過後才建立受控動作任務（非阻塞）
                    status = self._dispatch_action(
                        target_trig, result.current_value, trace_id=trace_id
                    )
                    if status == "dispatched":
                        summary["actions_dispatched"] += 1
                    elif status == "guard_rejected":
                        summary["guard_rejected"] += 1
                    elif status == "quota_rejected":
                        summary["quota_rejected"] += 1
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    summary["failed"] += 1
                    logger.error(
                        "[Spark Loop] 檢查 Trigger (%s) 發生異常: %s",
                        trig.trigger_id,
                        exc,
                        exc_info=True,
                    )

        try:
            from core.compat import async_timeout
            async with async_timeout(cycle_timeout):
                await asyncio.gather(*[_eval_trigger(t) for t in active_triggers])
        except (asyncio.TimeoutError, TimeoutError):
            summary["cycle_timed_out"] = True
            logger.error(
                "[Spark Loop] 本輪巡檢超過 %.1f 秒，已取消剩餘檢查（trace=%s）",
                cycle_timeout,
                trace_id,
            )

        summary["duration_ms"] = int((time.monotonic() - started) * 1000)
        self._publish_cycle_summary(summary, trace_id)

    def _publish_cycle_summary(self, summary: dict[str, Any], trace_id: str) -> None:
        """將本輪巡檢摘要寫入 SparkService 與結構化觀測。"""
        try:
            self.service.record_cycle_summary(dict(summary))
        except Exception:
            logger.debug("[Spark] 記錄巡檢摘要失敗", exc_info=True)

        try:
            log_event(
                event="spark_cycle",
                trace_id=trace_id,
                latency_ms=int(summary.get("duration_ms", 0)),
                **summary,
            )
            logger.info(
                "[Spark] 巡檢摘要 trace=%s 觸發=%d 派發=%d 未達=%d 跳過=%d "
                "逾時=%d 失敗=%d 治理拒絕=%d 配額拒絕=%d 本輪逾時=%s 耗時=%dms",
                trace_id,
                summary["fired"],
                summary["actions_dispatched"],
                summary["not_fired"],
                summary["cooldown_skipped"],
                summary["checker_timeouts"],
                summary["failed"],
                summary["guard_rejected"],
                summary["quota_rejected"],
                summary["cycle_timed_out"],
                summary["duration_ms"],
            )
        except Exception:
            logger.debug("[Spark] 寫入巡檢摘要觀測事件失敗", exc_info=True)

    def _dispatch_action(
        self, trigger: Trigger, content: str, trace_id: str = ""
    ) -> str:
        """在治理閘門與每日配額通過後，派發受控的動作任務。

        派發前依序檢查：
        1. `AutonomyGuard`：風險等級／系統健康／熔斷／行動預算。
        2. 每位使用者的每日推送配額。
        通過後才建立背景任務，並以 `_action_tasks` 強引用與 `_action_sem`
        長生命週期 semaphore 管理，避免 fire-and-forget 無界累積。

        Args:
            trigger: 已達觸發條件（或更新後）的 Trigger。
            content: 觸發摘要文字，供動作執行器使用。
            trace_id: 本輪巡檢的 trace ID（供動作日誌關聯）。

        Returns:
            str: ``dispatched``／``guard_rejected``／``quota_rejected``。
        """
        decision = self._autonomy_guard.check(
            "spark_push",
            params={
                "trigger_id": trigger.trigger_id,
                "trigger_type": trigger.trigger_type,
                "user_id": trigger.user_id,
            },
            description=f"SPARK 推播 {trigger.trigger_id[:8]}",
        )
        if not decision.allowed:
            logger.warning(
                "[Spark] 行動被治理閘門拒絕（trigger=%s，trace=%s）：%s",
                trigger.trigger_id[:8],
                trace_id or "-",
                decision.reason,
            )
            return "guard_rejected"

        allowed, reason = self.service.consume_push_quota(trigger.user_id)
        if not allowed:
            logger.warning(
                "[Spark] 使用者 %s 推送配額不足（trigger=%s，trace=%s）：%s",
                trigger.user_id,
                trigger.trigger_id[:8],
                trace_id or "-",
                reason,
            )
            return "quota_rejected"

        task = asyncio.create_task(
            self._run_action_guarded(trigger, content, trace_id=trace_id),
            name=f"spark-action-{trigger.trigger_id[:8]}",
        )
        self._action_tasks.add(task)
        task.add_done_callback(self._action_tasks.discard)
        return "dispatched"

    async def _run_action_guarded(
        self, trigger: Trigger, content: str, trace_id: str = ""
    ) -> None:
        """在 semaphore 與 timeout 保護下執行單一 Spark 動作。

        動作失敗或逾時只記錄日誌，不影響巡檢迴圈；`asyncio.wait_for` 會
        取消底層 `run_action` 任務，避免殘留的 LLM 呼叫或網路 I/O。
        """
        if trace_id:
            current_trace_id.set(trace_id)
        async with self._action_sem:
            try:
                timeout = float(getattr(settings, "SPARK_ACTION_TIMEOUT_SECONDS", 300.0))
            except (TypeError, ValueError):
                timeout = 300.0
            try:
                await asyncio.wait_for(
                    self.action_runner.run_action(trigger, content),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                logger.error(
                    "[Spark] 動作超過 %.1f 秒已取消（trigger=%s，trace=%s）",
                    timeout,
                    trigger.trigger_id[:8],
                    trace_id or "-",
                )
            except asyncio.CancelledError:
                logger.info(
                    "[Spark] 動作已取消（trigger=%s，trace=%s）",
                    trigger.trigger_id[:8],
                    trace_id or "-",
                )
                raise
            except Exception as exc:
                logger.error(
                    "[Spark] 動作執行失敗（trigger=%s，trace=%s）：%s",
                    trigger.trigger_id[:8],
                    trace_id or "-",
                    exc,
                    exc_info=True,
                )

    @_spark_check_loop.before_loop
    async def _before_spark_check_loop(self) -> None:
        """等待 Discord Bot 就緒。"""
        await self.bot.wait_until_ready()

    async def _send_notification(self, trigger: Trigger, content: str) -> None:
        """向指定頻道發送推播通知（委託 SparkActionRunner 分流執行）。"""
        await self.action_runner.run_action(trigger, content)

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
                # 智能一句話建立 (NLP 自然語言解析需呼叫 LLM，耗時常超過 Discord 3 秒 timeout，故先 defer)
                await interaction.response.defer(ephemeral=True)
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
                    await interaction.followup.send(
                        embed=preview_embed,
                        view=confirm_view,
                        ephemeral=True,
                    )
                    return
                else:
                    # 無法直接解析自然語言，給予提示並引導至類型選單
                    view = SparkTypeSelectView(self.service)
                    await interaction.followup.send(
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
                    cond_str = format_condition_preview(t.condition)
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
                    cond_str = format_condition_preview(t.condition)
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
            result, _ = await self.service.check_trigger(target, dry_run=True)
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
