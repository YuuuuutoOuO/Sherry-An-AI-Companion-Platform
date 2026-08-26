"""
mod.painter.cog
===============
Discord Slash Command `/draw` and interactive UI controls for Sherry AI Canvas Painter.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

import settings
from handlers.bot_brain import BotBrain, InferenceTask
from mod.painter.models import ArtStyle
from mod.painter.canvas import VirtualCanvas
from mod.painter.interpreter import CommandInterpreter
from mod.painter.agent import PainterAgent, _SYSTEM_PAINTER_PROMPT

logger = logging.getLogger(__name__)

# Style choices for Discord Slash Command
_STYLE_CHOICES = [
    app_commands.Choice(name="🖌️ 水墨畫 (古典國風墨韻)", value="ink_wash"),
    app_commands.Choice(name="🎨 水彩畫 (清新通透疊色)", value="watercolor"),
    app_commands.Choice(name="✏️ 簡筆塗鴉 (可愛手繪線條)", value="doodle"),
    app_commands.Choice(name="👾 像素藝術 (8-bit 復古點陣)", value="pixel_art"),
    app_commands.Choice(name="🌃 賽博霓虹 (夜間電光發光)", value="neon"),
    app_commands.Choice(name="🖍️ 粉筆蠟筆 (童趣顆粒手繪)", value="crayon"),
    app_commands.Choice(name="🖼️ 厚塗油畫 (經典飽和質感)", value="oil"),
    app_commands.Choice(name="✨ 隨機自由 (由雪莉靈感決定)", value="freestyle"),
]


class DrawCanvasControlView(discord.ui.View):
    """Interactive Discord buttons for redrawing or changing style."""

    def __init__(
        self,
        bot: commands.Bot,
        user_query: str,
        current_style: ArtStyle,
        original_author: discord.Member | discord.User,
    ) -> None:
        super().__init__(timeout=getattr(settings, "DISCORD_UI_VIEW_TIMEOUT", 180))
        self.bot = bot
        self.user_query = user_query
        self.current_style = current_style
        self.original_author = original_author

    @discord.ui.button(label="🔄 重新繪製", style=discord.ButtonStyle.primary, emoji="🎨")
    async def redraw_button(
        self, interaction: discord.Interaction, button: discord.ui.Button[Any]
    ) -> None:
        """Trigger redraw with the same prompt and style."""
        if interaction.user != self.original_author:
            await interaction.response.send_message("❌ 這是別人委託雪莉畫的畫作喔！", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        art_cog: PainterCog | None = self.bot.get_cog("PainterCog")  # type: ignore[assignment]
        if art_cog:
            await art_cog.execute_draw(interaction, self.user_query, self.current_style.value)

    @discord.ui.button(label="🎲 隨機風格重繪", style=discord.ButtonStyle.secondary, emoji="✨")
    async def random_style_button(
        self, interaction: discord.Interaction, button: discord.ui.Button[Any]
    ) -> None:
        """Trigger redraw with freestyle/random style."""
        if interaction.user != self.original_author:
            await interaction.response.send_message("❌ 這是別人委託雪莉畫的畫作喔！", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        art_cog: PainterCog | None = self.bot.get_cog("PainterCog")  # type: ignore[assignment]
        if art_cog:
            await art_cog.execute_draw(interaction, self.user_query, "freestyle")


class PainterCog(commands.Cog):
    """Cog providing the `/draw` slash command and canvas artist capabilities."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="draw",
        description="🎨 讓雪莉化身專屬畫家，在數位畫布上調用多種筆刷為你繪畫！",
    )
    @app_commands.describe(
        content="想要雪莉畫什麼內容（如：月夜竹林小貓、櫻花飄落的咖啡廳、星空下的城堡）",
        type="畫作風格（預設隨便，可選水墨畫、水彩、簡筆塗鴉、像素、賽博霓虹等）",
        image="選填：上傳參考圖片，讓多模態大腦 (Gemma) 觀察並臨摹/轉化風格！",
        refine="選填：啟用 Gemma 4 雙階段自我審視精修（看著草圖二次修飾）",
    )
    @app_commands.choices(type=_STYLE_CHOICES)
    async def draw_command(
        self,
        interaction: discord.Interaction,
        content: str,
        type: str = "freestyle",
        image: discord.Attachment | None = None,
        refine: bool = False,
    ) -> None:
        """Slash command handler for /draw [content] [type] [image] [refine]."""
        await interaction.response.defer(thinking=True)
        await self.execute_draw(interaction, content, type, attachment=image, refine=refine)

    async def execute_draw(
        self,
        interaction: discord.Interaction,
        content: str,
        style_str: str,
        attachment: discord.Attachment | None = None,
        refine: bool = False,
    ) -> None:
        """Core execution pipeline for drawing requests with two-pass vision reflection and 120s timeout.
        
        Args:
            interaction: Discord interaction.
            content: User prompt content.
            style_str: Style string (e.g. 'ink_wash', 'watercolor', 'freestyle').
            attachment: Optional reference image attachment.
            refine: Whether to trigger Stage 2 self-critique & refinement pass.
        """
        import base64
        import time
        from mod.painter.agent import _SYSTEM_REFLECTION_PROMPT, MAX_PAINTING_TIMEOUT_SEC

        start_time = time.time()
        style = ArtStyle.from_str(style_str)
        brain: BotBrain | None = self.bot.get_cog("BotBrain")  # type: ignore[assignment]

        author_name = interaction.user.display_name
        author_id = str(interaction.user.id)

        commentary = ""
        commands_list: list[dict[str, Any]] = []
        images_to_pass: list[str] | None = None

        if attachment and attachment.content_type and attachment.content_type.startswith("image/"):
            try:
                img_bytes = await attachment.read()
                images_to_pass = [base64.b64encode(img_bytes).decode("utf-8")]
                logger.info("PainterCog: Successfully extracted reference image (%d bytes) for multimodal draw.", len(img_bytes))
            except Exception as exc:
                logger.warning("PainterCog: Failed to read attachment image: %s", exc)

        # ── Stage 1: Draft Pass (大佈局與基底草稿) ───────────────────
        if brain:
            user_prompt = PainterAgent.build_user_prompt(content, style, has_image=bool(images_to_pass))
            loop = asyncio.get_running_loop()
            future1: asyncio.Future[str] = loop.create_future()

            async def _on_reply1(reply: str) -> None:
                if not future1.done():
                    future1.set_result(reply)

            task1 = InferenceTask(
                priority=getattr(settings, "PRIORITY_TAG_RESPONSE", 10),
                system_prompt=_SYSTEM_PAINTER_PROMPT,
                user_prompt=user_prompt,
                callback=_on_reply1,
                task_id=f"draw-{author_id}-s1-{int(asyncio.get_event_loop().time())}",
                images=images_to_pass,
            )
            await brain.enqueue_task(task1)

            try:
                raw_reply1 = await asyncio.wait_for(future1, timeout=50.0)
                commentary, commands_list = PainterAgent.parse_llm_response(raw_reply1)
            except asyncio.TimeoutError:
                logger.warning("PainterCog: Stage 1 LLM inference timed out, falling back to procedural painting.")
                commentary = "✨ 靈感如泉湧！雪莉直接為你繪製這幅作品～"
            except Exception as exc:
                logger.error("PainterCog: Error during Stage 1 LLM inference: %s", exc)
                commentary = "✨ 雪莉為你畫好囉！"

        if not commands_list:
            commands_list = PainterAgent.generate_procedural_fallback(content, style)
            if not commentary:
                commentary = f"✨ 雪莉以【{style.value}】風格為你繪製了這幅「{content}」！"

        # Initialize canvas & execute Stage 1
        canvas = VirtualCanvas(width=800, height=800, style=style)
        interpreter = CommandInterpreter(canvas)
        interpreter.execute_batch(commands_list)

        # ── Stage 2: Self-Critique & Refine Pass (自我視覺審視與精修) ─────────
        elapsed = time.time() - start_time
        remaining_budget = MAX_PAINTING_TIMEOUT_SEC - elapsed

        if refine and brain and remaining_budget > 15.0:
            try:
                draft_bytes = canvas.export_bytes(format_name="JPEG", quality=85)
                draft_b64 = base64.b64encode(draft_bytes).decode("utf-8")
                refine_prompt = PainterAgent.build_reflection_user_prompt(content, style, draft_commentary=commentary)

                loop = asyncio.get_running_loop()
                future2: asyncio.Future[str] = loop.create_future()

                async def _on_reply2(reply: str) -> None:
                    if not future2.done():
                        future2.set_result(reply)

                stage2_timeout = min(45.0, remaining_budget - 5.0)
                task2 = InferenceTask(
                    priority=getattr(settings, "PRIORITY_TAG_RESPONSE", 10),
                    system_prompt=_SYSTEM_REFLECTION_PROMPT,
                    user_prompt=refine_prompt,
                    callback=_on_reply2,
                    task_id=f"draw-{author_id}-s2-{int(asyncio.get_event_loop().time())}",
                    images=[draft_b64],
                )
                await brain.enqueue_task(task2)

                raw_reply2 = await asyncio.wait_for(future2, timeout=stage2_timeout)
                commentary2, commands_list2 = PainterAgent.parse_llm_response(raw_reply2)
                if commands_list2:
                    interpreter.execute_batch(commands_list2)
                    if commentary2:
                        commentary = f"{commentary}\n\n🔍 **雪莉自我審視精修講評**：\n{commentary2}"
            except asyncio.TimeoutError:
                logger.warning("PainterCog: Stage 2 self-critique timed out (120s guard). Finalizing with draft.")
            except Exception as exc:
                logger.warning("PainterCog: Stage 2 error: %s. Finalizing with draft.", exc)

        # ── Final Save & Embed Construction ────────────────────────
        total_duration = round(time.time() - start_time, 2)
        total_command_count = len(interpreter.executed_commands)

        try:
            canvas, meta, jpg_path = await PainterAgent.paint_and_save(
                prompt=content,
                style=style,
                commands=[],
                author_id=author_id,
                author_name=author_name,
                existing_canvas=canvas,
                stage="refined" if refine else "single_pass",
                custom_duration=total_duration,
                custom_command_count=total_command_count,
            )
        except Exception as exc:
            logger.exception("PainterCog: Rendering failure: %s", exc)
            await interaction.followup.send(f"❌ 繪圖渲染時發生異常：{exc}", ephemeral=True)
            return

        file = discord.File(str(jpg_path), filename=meta.filename)
        embed = discord.Embed(
            title=f"🎨 雪莉的藝術畫布 — {content}",
            description=commentary,
            color=discord.Color.from_rgb(255, 182, 193),
        )
        embed.set_image(url=f"attachment://{meta.filename}")
        embed.add_field(name="🖌️ 風格", value=f"`{style.value}`", inline=True)
        embed.add_field(name="📐 筆刷指令數", value=f"`{meta.command_count} 步`", inline=True)
        embed.add_field(name="⏱️ 總耗時", value=f"`{meta.duration_sec}s`", inline=True)
        if refine:
            embed.add_field(name="✨ 模式", value="`Gemma 雙階段自我審視精修`", inline=False)
        embed.set_footer(text=f"已保存至 ./data/painting/{meta.filename} · 創作者 {author_name}")

        view = DrawCanvasControlView(
            bot=self.bot,
            user_query=content,
            current_style=style,
            original_author=interaction.user,
        )

        await interaction.followup.send(embed=embed, file=file, view=view)


async def setup(bot: commands.Bot) -> None:
    """Extension setup entry point."""
    await bot.add_cog(PainterCog(bot))
