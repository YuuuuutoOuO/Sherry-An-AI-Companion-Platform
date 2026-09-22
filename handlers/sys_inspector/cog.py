"""handlers.sys_inspector.cog
===========================
全景系統監控中樞 Discord Cog (Presentation Layer)。

整合：
1. 事件迴圈 Lag 與大腦推論佇列卡頓之背景健康守衛。
2. 5 分鐘日誌快照與雲端搜尋模型 (Cloud Search Model) 技術稽核診斷卡片發送。
3. `/self-monitoring [action:check]` 等 Slash 管理指令。
"""

from __future__ import annotations

import logging
import time
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.sys_inspector.audit_service import (
    AuditService,
    audit_service,
)
from core.sys_inspector.models import (
    DiagnosticResult,
    IncidentReport,
    IncidentSeverity,
    LogSnapshot,
)
from core.sys_inspector.notifications import register_cog_instance
import settings

logger = logging.getLogger(__name__)


def build_diagnostic_embed(
    incident: IncidentReport, diagnostic: DiagnosticResult, snapshot: LogSnapshot
) -> discord.Embed:
    """建構美觀的 Discord 卡片式回覆 (Embed)。"""
    if incident.severity == IncidentSeverity.CRITICAL:
        color = discord.Color.red()
        icon = "🚨"
        severity_label = "【嚴重異常】"
    elif incident.severity == IncidentSeverity.WARNING:
        color = discord.Color.gold()
        icon = "⚠️"
        severity_label = "【慢速警告】"
    else:
        color = discord.Color.teal()
        icon = "🔍"
        severity_label = "【體檢報告】"

    title = f"{icon} {severity_label} 系統自我監控與延遲稽核報告"
    sec_str = f"{incident.latency_ms / 1000.0:.1f}s" if incident.latency_ms > 0 else "N/A"
    desc = f"**觸發原因**：{incident.trigger_reason}\n**單次耗時**：`{sec_str}` (`{incident.latency_ms} ms`)"

    embed = discord.Embed(title=title, description=desc, color=color)

    # 1. 雲端搜尋模型根因診斷
    model_name = diagnostic.model_name or "Cloud Search Model"
    diag_body = (
        f"**瓶頸分類**：`{diagnostic.bottleneck_category}`\n"
        f"**根因剖析**：{diagnostic.root_cause}"
    )
    if len(diag_body) > 1000:
        diag_body = diag_body[:980] + "..."
    embed.add_field(name=f"🧠 雲端模型稽核診斷 ({model_name})", value=diag_body, inline=False)

    # 2. 具體修復與優化建議
    recs = "\n".join(f"• {r}" for r in diagnostic.recommendations) if diagnostic.recommendations else "• 暫無特定建議"
    if len(recs) > 1000:
        recs = recs[:980] + "..."
    embed.add_field(name="💡 稽核改善與處置建議", value=recs, inline=False)

    # 3. 關鍵日誌跡證（過去 5 分鐘快照）
    evi_lines = []
    if diagnostic.key_evidence:
        evi_lines.extend(diagnostic.key_evidence)
    elif snapshot.bot_log_snippet:
        last_lines = [line for line in snapshot.bot_log_snippet.splitlines() if line.strip()][-3:]
        evi_lines.extend(last_lines)

    evi_text = "\n".join(f"```text\n{line[:150]}\n```" for line in evi_lines[:3]) if evi_lines else "`無特殊報錯日誌`"
    if len(evi_text) > 1000:
        evi_text = evi_text[:980] + "..."
    embed.add_field(name=f"📝 關鍵日誌跡證 (近 {snapshot.window_minutes} 分鐘快照)", value=evi_text, inline=False)

    # 4. 鏈路追蹤與指標快照
    tid = incident.trace_id or "-"
    channel_tag = f"<#{incident.channel_id}>" if incident.channel_id else "系統背景"
    diag_lat = f"{diagnostic.diagnosis_latency_ms}ms" if diagnostic.diagnosis_latency_ms else "N/A"
    metrics_field = (
        f"• **Trace ID**: `{tid}`\n"
        f"• **關聯頻道**: {channel_tag}\n"
        f"• **5分內日誌**: 總計 {snapshot.total_lines} 筆 (錯誤 {snapshot.error_count} / 警告 {snapshot.warning_count})\n"
        f"• **診斷耗時**: `{diag_lat}`"
    )
    embed.add_field(name="📊 鏈路與效能指標", value=metrics_field, inline=False)

    embed.set_footer(text="系統自我監控中樞 (Sys-Inspector) • 專業效能稽核報告")
    return embed


_CURRENT_SERVICE: AuditService | None = None


def get_self_monitoring_service() -> AuditService | None:
    """取得當前運行中之自我監控服務實例。"""
    return _CURRENT_SERVICE


class SysInspectorCog(commands.Cog, name="SysInspector"):
    """全景系統硬體監控與延遲稽核守衛 Cog。"""

    def __init__(self, bot: commands.Bot, service: AuditService | None = None) -> None:
        self.bot = bot
        self.service = service or audit_service
        self._last_loop_check_time: float = time.monotonic()

    async def cog_load(self) -> None:
        """載入 Cog 時向 notifications 模組註冊全域實例並啟動背景輪詢。"""
        global _CURRENT_SERVICE
        _CURRENT_SERVICE = self.service

        loop = getattr(self.bot, "loop", None)
        register_cog_instance(self, loop)

        try:
            self._health_check_loop.start()
            logger.info("SysInspectorCog: 背景健康檢查守衛輪詢已啟動。")
        except Exception as exc:
            logger.warning("SysInspectorCog: 啟動輪詢失敗: %s", exc)

    async def cog_unload(self) -> None:
        """卸載 Cog 時取消輪詢任務。"""
        global _CURRENT_SERVICE
        _CURRENT_SERVICE = None
        register_cog_instance(None, None)

        if self._health_check_loop.is_running():
            self._health_check_loop.cancel()
        logger.info("SysInspectorCog: 背景健康檢查守衛輪詢已停止。")

    @tasks.loop(seconds=30.0)
    async def _health_check_loop(self) -> None:
        """背景定時輪詢：監測事件迴圈 Lag 與推論佇列。"""
        now = time.monotonic()
        lag_sec = max(0.0, (now - self._last_loop_check_time) - 30.0)
        self._last_loop_check_time = now

        q_size = 0
        try:
            brain = self.bot.get_cog("BotBrain") if self.bot else None
            if brain is not None and hasattr(brain, "get_pending_tasks"):
                q_size = len(brain.get_pending_tasks())
        except Exception:
            pass

        incident = self.service.check_system_health(
            event_loop_lag_ms=lag_sec * 1000,
            queue_size=q_size,
        )
        if incident:
            logger.warning("SysInspectorCog 檢測到系統異常指標: %s", incident.trigger_reason)
            await self._run_diagnosis_and_alert(incident)

    async def handle_slow_response(
        self,
        latency_ms: int,
        trace_id: str = "",
        channel_id: int = 0,
        user_name: str = "",
        raw_prompt: str = "",
    ) -> None:
        """處理單次回覆超過 90 秒事件。"""
        incident = self.service.create_slow_response_incident(
            latency_ms=latency_ms,
            trace_id=trace_id,
            channel_id=channel_id,
            user_name=user_name,
            raw_prompt=raw_prompt,
        )
        if self.service.should_suppress_alert(incident.incident_type):
            logger.debug("handle_slow_response: 警報處於冷卻期內，予以抑制。")
            return
        await self._run_diagnosis_and_alert(incident)

    async def handle_failure(
        self,
        latency_ms: int = 0,
        error_msg: str = "",
        trace_id: str = "",
        channel_id: int = 0,
        user_name: str = "",
        raw_prompt: str = "",
        task_id: str = "",
        is_fatal: bool = False,
    ) -> None:
        """處理單次超過 120 秒失敗或重大中斷事件。"""
        if is_fatal:
            incident = self.service.create_inference_failure_incident(
                latency_ms=latency_ms,
                error_msg=error_msg,
                trace_id=trace_id,
                channel_id=channel_id,
                user_name=user_name,
                raw_prompt=raw_prompt,
                task_id=task_id,
            )
        else:
            incident = self.service.create_failure_incident(
                latency_ms=latency_ms,
                error_msg=error_msg,
                trace_id=trace_id,
                channel_id=channel_id,
                user_name=user_name,
                raw_prompt=raw_prompt,
            )
        if self.service.should_suppress_alert(incident.incident_type):
            logger.debug("handle_failure: 警報處於冷卻期內，予以抑制。")
            return
        await self._run_diagnosis_and_alert(incident)

    async def _run_diagnosis_and_alert(self, incident: IncidentReport) -> None:
        """執行診斷並發送卡片告警至警報頻道與管理員私訊。"""
        try:
            diag, snapshot = await self.service.diagnose_incident(incident)
            embed = build_diagnostic_embed(incident, diag, snapshot)
            target_channel_id = getattr(settings, "HEALTH_ALERT_CHANNEL_ID", 0) or incident.channel_id
            if target_channel_id and self.bot:
                ch = self.bot.get_channel(int(target_channel_id))
                if ch and hasattr(ch, "send"):
                    await ch.send(embed=embed)
            user_ids = settings.get_alert_recipient_user_ids() if hasattr(settings, "get_alert_recipient_user_ids") else []
            for uid in user_ids:
                try:
                    user = self.bot.get_user(uid)
                    if user is None:
                        user = await self.bot.fetch_user(uid)
                    if user and hasattr(user, "send"):
                        await user.send(embed=embed)
                except Exception:
                    pass
        except Exception as exc:
            logger.error("SysInspectorCog: 執行稽核診斷發送告警失敗: %s", exc)

    @app_commands.command(name="self-monitoring", description="系統自我監控中樞技術稽核與健康體檢")
    @app_commands.describe(action="操作類型: check (立即執行技術稽核體檢) / status (查看狀態) / test_alert (測試警報)")
    async def self_monitoring_cmd(
        self,
        interaction: discord.Interaction,
        action: Literal["check", "status", "test_alert"] = "check",
    ) -> None:
        """Slash 指令入口。"""
        await interaction.response.defer(ephemeral=False)

        if action == "status":
            embed = discord.Embed(
                title="🛡️ 雪莉自我監控狀態",
                description="自我監控與技術稽核守衛正常運行中。",
                color=discord.Color.blue(),
            )
            embed.add_field(name="背景守衛", value="`ONLINE (30s)`", inline=True)
            embed.add_field(name="稽核儲存庫", value="`data/sys_inspector/audit.db`", inline=True)
            await interaction.followup.send(embed=embed)
            return

        if action == "test_alert":
            inc = self.service.create_slow_response_incident(
                latency_ms=95500,
                trace_id="tr-test-alert",
                channel_id=interaction.channel_id or 0,
                user_name=str(interaction.user),
                raw_prompt="test alert",
            )
            diag, snapshot = await self.service.diagnose_incident(inc)
            embed = build_diagnostic_embed(inc, diag, snapshot)
            await interaction.followup.send(embed=embed)
            return

        # 預設 check
        inc = self.service.create_manual_check_incident(
            channel_id=interaction.channel_id or 0,
            user_name=str(interaction.user),
        )
        diag, snapshot = await self.service.diagnose_incident(inc)
        embed = build_diagnostic_embed(inc, diag, snapshot)
        await interaction.followup.send(embed=embed)


# 相容別名
SelfMonitoringCog = SysInspectorCog
