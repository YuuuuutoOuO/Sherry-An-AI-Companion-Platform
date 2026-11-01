"""
cog.py (mod/deep_search)
========================
Deep Search 深度研究模組 Discord 介面控制器 (Presentation Layer)。
支援：
  - Discord 斜線指令：`/deep_search context:[主題] model:[可選模型] rounds:[可選輪數]`
  - Discord 前綴指令：`!deep_search <輪數可選> <主題>`
  - 背景非同步推演與 Discord 即時進度更新
  - 整合 Web UI 深度研究主控台狀態
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import io
import logging
import re
import time
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from mod.deep_search import config
from mod.deep_search.domain.models import ResearchProgress, ResearchSession
from mod.deep_search.engine.deep_search_engine import DeepSearchEngine
from mod.deep_search.repositories.llm_client import OllamaResearchClient
from mod.deep_search.repositories.scraper_client import TrafilaturaScraperClient
from mod.deep_search.repositories.search_client import DuckDuckGoSearchClient
from mod.deep_search.repositories.session_repository import FileSessionRepository

logger = logging.getLogger(__name__)


class DeepSearchCog(commands.Cog, name="DeepSearch"):
    """多輪自主深度研究與報告推演模組。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.session_repo = FileSessionRepository()
        self.search_client = DuckDuckGoSearchClient()
        self.scraper_client = TrafilaturaScraperClient()
        self.llm_client = OllamaResearchClient()
        self.engine = DeepSearchEngine(
            search_client=self.search_client,
            scraper_client=self.scraper_client,
            llm_client=self.llm_client,
            session_repository=self.session_repo,
        )
        self._active_tasks: dict[str, asyncio.Task[Any]] = {}

        # 註冊至 bot 實例，方便 Web UI 存取與跨模組調用
        self.bot._deep_search_cog = self  # type: ignore[attr-defined]
        logger.info("[deep_search] DeepSearchCog 初始化完成")

    # =========================================================================
    # 斜線指令 (Slash Command)
    # =========================================================================
    @app_commands.command(
        name="deep_search",
        description="啟動地端多輪自主深度研究，進行全方位連網檢索與論文級客觀報告撰寫",
    )
    @app_commands.describe(
        context="欲進行深度探討與研究的主題或問題",
        model="指定的 LLM 模型 (留空則使用系統預設主力模型)",
        rounds="探討子專題輪數 (預設 4 輪，每輪包含檢索、精讀與反思)",
    )
    async def deep_search_slash(
        self,
        interaction: discord.Interaction,
        context: str,
        model: str | None = None,
        rounds: app_commands.Range[int, 2, 8] | None = None,
    ) -> None:
        """Discord 斜線指令入口。"""
        await interaction.response.defer(thinking=True)

        target_topic = context.strip()
        if not target_topic:
            await interaction.followup.send("❌ 請提供有效的研究主題內容！", ephemeral=True)
            return

        target_model = model or config.get_default_model()
        actual_rounds = int(rounds or config.DEFAULT_RESEARCH_ROUNDS)
        session_id = f"rs-{int(time.time())}"

        embed = self._build_progress_embed(
            topic=target_topic,
            model=target_model,
            status="RUNNING",
            progress_pct=5,
            step_desc="正在規劃研究綱領與核心探討維度...",
            rounds=actual_rounds,
            session_id=session_id,
        )
        initial_msg = await interaction.followup.send(embed=embed, wait=True)
        msg_id: int | None = initial_msg.id if initial_msg else None

        # 啟動背景研究 Task
        task = asyncio.create_task(
            self._run_research_flow(
                topic=target_topic,
                model=target_model,
                rounds=actual_rounds,
                session_id=session_id,
                channel_id=interaction.channel_id,
                message_id=msg_id,
                interaction=interaction,
            ),
            name=f"deep-search-{session_id}",
        )
        self._active_tasks[session_id] = task

    # =========================================================================
    # 前綴指令 (Prefix Command Fallback)
    # =========================================================================
    @commands.command(name="deep_search", aliases=["deepsearch", "深度研究"])
    async def deep_search_prefix(self, ctx: commands.Context, *, query: str) -> None:
        """Discord 前綴指令入口。支援 `!deep_search 2 BTC走勢` 或 `!deep_search BTC走勢 -r 2`。"""
        raw_query = query.strip()
        if not raw_query:
            await ctx.reply("❌ 請輸入欲探討的研究主題！例如：`!deep_search 常溫超導體最新進展` 或 `!deep_search 2 BTC走勢`")
            return

        target_topic = raw_query
        actual_rounds = config.DEFAULT_RESEARCH_ROUNDS

        # 支援前綴輪數 !deep_search 2 BTC走勢 或後綴 !deep_search BTC走勢 -r 2
        m_r_prefix = re.match(r"^(\d+)\s+(.+)$", raw_query)
        m_r_suffix = re.search(r"\s+-r\s*(\d+)$", raw_query)
        if m_r_prefix:
            r_val = int(m_r_prefix.group(1))
            if 2 <= r_val <= 8:
                actual_rounds = r_val
                target_topic = m_r_prefix.group(2).strip()
        elif m_r_suffix:
            r_val = int(m_r_suffix.group(1))
            if 2 <= r_val <= 8:
                actual_rounds = r_val
                target_topic = target_topic[:m_r_suffix.start()].strip()

        target_model = config.get_default_model()
        session_id = f"rs-{int(time.time())}"

        embed = self._build_progress_embed(
            topic=target_topic,
            model=target_model,
            status="RUNNING",
            progress_pct=5,
            step_desc="正在規劃研究綱領與核心探討維度...",
            rounds=actual_rounds,
            session_id=session_id,
        )
        initial_msg = await ctx.reply(embed=embed)

        task = asyncio.create_task(
            self._run_research_flow(
                topic=target_topic,
                model=target_model,
                rounds=actual_rounds,
                session_id=session_id,
                channel_id=ctx.channel.id,
                message_id=initial_msg.id,
                discord_message=initial_msg,
            ),
            name=f"deep-search-{session_id}",
        )
        self._active_tasks[session_id] = task

    # =========================================================================
    # 核心背景研究排程工作
    # =========================================================================
    async def _run_research_flow(
        self,
        topic: str,
        model: str,
        rounds: int,
        session_id: str,
        channel_id: int | None = None,
        message_id: int | None = None,
        interaction: discord.Interaction | None = None,
        discord_message: discord.Message | None = None,
    ) -> ResearchSession:
        """背景非同步執行深度研究推演，並定時更新 Discord 進度。"""
        last_edit_time = 0.0

        async def _on_progress(event: ResearchProgress) -> None:
            nonlocal last_edit_time
            now = time.time()
            # 節流更新 Discord 訊息 (至少間隔 6 秒或當完成時立即更新)
            if (now - last_edit_time > 6.0) or event.status in ("COMPLETED", "FAILED"):
                last_edit_time = now
                embed = self._build_progress_embed(
                    topic=topic,
                    model=model,
                    status=event.status,
                    progress_pct=event.progress_pct,
                    step_desc=event.current_step,
                    rounds=rounds,
                    session_id=session_id,
                )
                try:
                    if interaction:
                        await interaction.edit_original_response(embed=embed)
                    elif discord_message:
                        await discord_message.edit(embed=embed)
                except Exception as exc:
                    logger.debug("[deep_search] 更新 Discord 進度 Embed 失敗: %s", exc)

        try:
            session = await self.engine.run_research(
                topic=topic,
                model=model,
                max_rounds=rounds,
                session_id=session_id,
                progress_callback=_on_progress,
            )

            # 任務完成後發送報告成果
            await self._send_final_report(
                session=session,
                channel_id=channel_id,
                interaction=interaction,
                discord_message=discord_message,
            )
            return session
        finally:
            # 複審 2：成功、例外、取消都要移除追蹤；避免 Task 強引用永久滯留。
            self._active_tasks.pop(session_id, None)

    async def _send_final_report(
        self,
        session: ResearchSession,
        channel_id: int | None,
        interaction: discord.Interaction | None = None,
        discord_message: discord.Message | None = None,
    ) -> None:
        """在 Discord 發送完成通知與結構化報告。"""
        if session.status != "COMPLETED" or not session.final_report:
            err_embed = discord.Embed(
                title="⚠️ 深度研究推演失敗",
                description=f"研究主題：**{session.topic}**\n錯誤原因：{session.error_message or '未知原因'}",
                colour=discord.Colour.red(),
            )
            try:
                if interaction:
                    await interaction.followup.send(embed=err_embed)
                elif discord_message:
                    await discord_message.reply(embed=err_embed)
            except Exception:
                pass
            return

        report = session.final_report
        summary_text = (report.executive_summary or "").strip()
        if not summary_text:
            summary_text = report.full_content[:600].strip() or "已完成多輪自主文獻鑽研與綜合報告合成。"

        # 安全長度限制 (避免超過 Discord 4096 限制，預留標題與格式空間)
        if len(summary_text) > 1800:
            summary_text = summary_text[:1800] + "...\n\n*(全文過長，請檢閱下方附加之完整 .md 報告檔案)*"

        # 成果 Embed
        report_embed = discord.Embed(
            title=f"🔬 深度研究專案報告：{session.topic}",
            description=f"### 📌 執行摘要 (Executive Summary)\n{summary_text}",
            colour=discord.Colour.blue(),
            timestamp=datetime.now(timezone.utc),
        )
        report_embed.add_field(
            name="🤖 採用推演模型",
            value=f"`{report.model_used}`",
            inline=True,
        )
        report_embed.add_field(
            name="⏱️ 總研究耗時",
            value=f"{report.total_time_seconds:.1f} 秒",
            inline=True,
        )
        report_embed.add_field(
            name="📚 引用文獻來源",
            value=f"{len(report.sources)} 篇文獻",
            inline=True,
        )

        if report.sources:
            sources_preview = "\n".join(
                f"• [{s.title[:45]}]({s.url})" for s in report.sources[:5]
            )
            if len(report.sources) > 5:
                sources_preview += f"\n*(其餘 {len(report.sources)-5} 篇請至 Web UI 查看)*"
            report_embed.add_field(
                name="🌐 精選文獻來源",
                value=sources_preview,
                inline=False,
            )

        report_embed.set_footer(
            text="💡 完整萬字專案報告與思考軌跡，可登入 Sherry Web UI【深度研究】分頁完整檢閱！"
        )

        # 將完整 Markdown 報告作為 .md 附件發送 (確保永遠不為 0 bytes)
        md_content = report.full_content.strip()
        if not md_content:
            md_content = f"# {session.topic} 深度專案研究報告\n\n## 1. 執行摘要\n{summary_text}\n"
        md_bytes = md_content.encode("utf-8")
        file = discord.File(
            fp=io.BytesIO(md_bytes),
            filename=f"deep_research_{session.session_id}.md",
            description=f"深度研究報告：{session.topic}",
        )

        try:
            if interaction:
                await interaction.followup.send(embed=report_embed, file=file)
            elif discord_message:
                await discord_message.reply(embed=report_embed, file=file)
        except Exception as exc:
            logger.error("[deep_search] 發送最終報告失敗: %s", exc)

    # =========================================================================
    # 輔助函式：建立即時進度 Embed
    # =========================================================================
    @staticmethod
    def _build_progress_embed(
        topic: str,
        model: str,
        status: str,
        progress_pct: int,
        step_desc: str,
        rounds: int,
        session_id: str,
    ) -> discord.Embed:
        """建構 Discord 即時進度展示卡片。"""
        status_icon = "⏳" if status == "RUNNING" else ("✅" if status == "COMPLETED" else "❌")
        bar_len = 12
        filled = int(bar_len * (progress_pct / 100))
        progress_bar = "▓" * filled + "░" * (bar_len - filled)

        embed = discord.Embed(
            title=f"🔬 地端自主深度研究中... {status_icon}",
            description=(
                f"**研究主題**：`{topic}`\n"
                f"**專案識別碼**：`{session_id}`\n\n"
                f"**目前進度**：`[{progress_bar}] {progress_pct}%`\n"
                f"**當前階段**：{step_desc}"
            ),
            colour=discord.Colour.purple() if status == "RUNNING" else discord.Colour.green(),
        )
        embed.add_field(name="🧠 推演模型", value=f"`{model}`", inline=True)
        embed.add_field(name="🔄 探討維度", value=f"{rounds} 輪子專題鑽研", inline=True)
        embed.add_field(
            name="📊 Web UI 即時監控",
            value="[點此開啟 Web 控制台查看即時思考軌跡](http://localhost:8763)",
            inline=False,
        )
        embed.set_footer(text="嚴格零人格提示詞 | 客觀學術智庫推演模式")
        return embed


# =========================================================================
# Discord.py 動態 Extension 掛載入口
# =========================================================================
async def setup(bot: commands.Bot) -> None:
    """由 main.py 自動載入。"""
    cog = DeepSearchCog(bot)
    await bot.add_cog(cog)
    logger.info("[deep_search] ✅ DeepSearchCog 已成功掛載到雪莉機器人")