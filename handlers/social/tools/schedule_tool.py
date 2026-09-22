"""
handlers/social/tools/schedule_tool.py
======================================
排程提醒工具 — 負責意圖解析、自然語言時間抽取與排程資料寫入。

支援兩種解析路徑：
  1. 正規表達式直接解析（如 /schedule 3min 吃泡麵），節省 LLM 算力。
  2. LLM JSON 抽取（如「提醒我3分鐘後吃泡麵」自然語言）。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import discord
from discord.ext import commands

from handlers.social.contracts import ToolResult
from handlers.social.tool_base import BaseTool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 子命令偵測：list / delete
# ---------------------------------------------------------------------------
_LIST_INTENT_REGEX = re.compile(
    r"(?:/schedule\s+list)|(?:排程列表)|(?:目前的排程)|(?:列出排程)|(?:查看排程)|(?:看.*排程)|(?:排程.*看)|(?:我的排程)",
    re.IGNORECASE,
)

_DELETE_INTENT_REGEX = re.compile(
    r"(?:/schedule\s+delete)|(?:/schedule\s+del)|(?:刪除排程)|(?:取消排程)|(?:移除排程)|(?:砍.*排程)|(?:刪.*排程)",
    re.IGNORECASE,
)

_DELETE_TASK_ID_REGEX = re.compile(
    r"[0-9a-fA-F]{8,32}",
)


def _detect_list_intent(text: str) -> bool:
    """偵測是否為列出排程的意圖。"""
    return bool(_LIST_INTENT_REGEX.search(text))


def _detect_delete_intent(text: str) -> str | None:
    """偵測是否為刪除排程的意圖，若是則提取 task_id。"""
    if not _DELETE_INTENT_REGEX.search(text):
        return None
    m = _DELETE_TASK_ID_REGEX.search(text)
    if m:
        return m.group(0).lower()
    return None


# ---------------------------------------------------------------------------
# ScheduleTool 本體
# ---------------------------------------------------------------------------
class ScheduleTool(BaseTool):

    @property
    def intent_name(self) -> str:
        return "SCHEDULE"

    @property
    def llm_description(self) -> str:
        return (
            "泛用排程、定時提醒與主動條件任務建立工具 (SPARK)。"
            "當使用者希望在未來特定時間（如幾分鐘後、明天/後天某時、特定日期生日開會等單次提醒）、"
            "固定週期（如每N分鐘/小時）、或每日定時執行某任務（包含發送通知、調度核心工具如傳圖/股市/天氣、"
            "或委託深度探究研究主題）時調用。"
        )

    def can_handle_by_regex(self, text: str) -> bool:
        """嚴格禁止關鍵字硬比對，全交由 LLM Planner 語意規劃。"""
        return False

    async def execute(
        self,
        bot: commands.Bot,
        message: discord.Message,
        user_query: str,
        vibe_snippet: str,
        full_context: str,
        **kwargs: object,
    ) -> ToolResult:
        spark_cog: Any = bot.get_cog("SparkCog")
        if not spark_cog or not hasattr(spark_cog, "service"):
            msg = "名偵探的大腦中樞未啟動 SparkCog，排程推播功能暫時無法使用！"
            await message.reply(msg, mention_author=True)
            return ToolResult.error(msg, is_direct_rendered=True)

        user_id = message.author.id
        channel_id = message.channel.id

        # ── 子命令：列出排程 ──
        if _detect_list_intent(user_query):
            triggers = [t for t in spark_cog.service.list_triggers() if t.user_id == user_id]
            if not triggers:
                await message.reply("你目前沒有任何啟用中的 Spark 推播或排程任務喔！", mention_author=True)
                return ToolResult.success("目前無任何排程任務。", is_direct_rendered=True)

            embed = discord.Embed(
                title=f"📋 {message.author.display_name} 的 Spark 推播與排程清單",
                description=f"目前共有 **{len(triggers)}** 個任務正在監控中：",
                color=discord.Color.teal(),
                timestamp=datetime.now(timezone.utc),
            )
            from handlers.spark.cog import format_condition_preview

            for t in triggers[:10]:
                status_icon = "🟢" if t.is_active else "⏸️"
                cond_desc = format_condition_preview(t.condition)
                embed.add_field(
                    name=f"{status_icon} [{t.trigger_type.upper()}] ID: `{t.trigger_id[:8]}`",
                    value=f"• 條件: `{cond_desc}`\n• 說明: {t.message or '無'}\n• 頻道: <#{t.channel_id}>",
                    inline=False,
                )
            await message.reply(embed=embed, mention_author=True)
            return ToolResult.success(f"已列出 {len(triggers)} 個任務清單。", is_direct_rendered=True)

        # ── 子命令：刪除排程 ──
        task_id_prefix = _detect_delete_intent(user_query)
        if task_id_prefix:
            target_trig = None
            for t in spark_cog.service.list_triggers():
                if t.user_id == user_id and t.trigger_id.lower().startswith(task_id_prefix.lower()):
                    target_trig = t
                    break

            if target_trig:
                spark_cog.service.delete_trigger(target_trig.trigger_id)
                msg = f"碰！💥 名偵探已經把任務 `{target_trig.trigger_id[:8]}` 給刪除消滅了！"
                await message.reply(msg, mention_author=True)
                return ToolResult.success(msg, is_direct_rendered=True)
            else:
                msg = f"嗯…找不到開頭為 `{task_id_prefix}` 的任務，可以用 `/spark` 或 `/schedule list` 查詢目前編號喔！"
                await message.reply(msg, mention_author=True)
                return ToolResult.error(msg, is_direct_rendered=True)

        # ── 主流程：透過 SparkNLP 進行語意抽取 ──
        from handlers.spark.nlp import parse_spark_context
        from handlers.spark.cog import SparkConfirmView

        candidate = await parse_spark_context(
            context=user_query,
            user_id=user_id,
            channel_id=channel_id,
            bot=bot,
        )

        if not candidate:
            msg = (
                "唔…名偵探沒辦法精確理解這個排程或條件設定，可以再具體一點告訴我時間或條件嗎？\n"
                "例如：\n"
                "• `@雪莉 後天8.我要開會 請記得提醒我`\n"
                "• `@雪莉 請每天早上10.進行本日0050股票走勢的研究`\n"
                "• `@雪莉 請幫我監測NVDA，價格低於200時提醒我`\n"
                "• `@雪莉 請每60分鐘傳一張色圖`"
            )
            await message.reply(msg, mention_author=True)
            return ToolResult.no_data("未能成功解析排程或條件。", is_direct_rendered=True)

        # 渲染防呆確認卡片
        confirm_view = SparkConfirmView(
            service=spark_cog.service,
            candidate=candidate,
            author_id=user_id,
        )
        preview_embed = confirm_view.build_preview_embed()
        await message.reply(embed=preview_embed, view=confirm_view, mention_author=True)
        return ToolResult.success("已為使用者展示 Spark 推播預覽確認卡片。", is_direct_rendered=True)