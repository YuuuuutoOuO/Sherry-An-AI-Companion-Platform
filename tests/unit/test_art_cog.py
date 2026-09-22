"""
tests/unit/test_art_cog.py
==========================
Unit tests for ArtCog and /draw Discord command.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path
import pytest
import discord

from mod.painter import config
from mod.painter.cog import PainterCog, DrawCanvasControlView, setup as painter_setup
from mod.painter.models import ArtStyle


def test_art_cog_execute_draw(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test execute_draw workflow with mocked BotBrain and Discord interaction."""
    import asyncio

    async def _run() -> None:
        test_painting_dir = tmp_path / "painting"
        monkeypatch.setattr(config, "PAINTING_DIR", test_painting_dir)

        bot = MagicMock()
        brain = MagicMock()
        
        async def fake_enqueue(task: Any) -> None:
            raw_json = """
            雪莉特別畫的水墨小貓喔！
            ```json
            [
                {"action": "set_background", "params": {"color": "#F8F5EC"}},
                {"action": "stamp", "params": {"text": "雪莉"}}
            ]
            ```
            """
            await task.callback(raw_json)

        brain.enqueue_task = AsyncMock(side_effect=fake_enqueue)
        bot.get_cog = MagicMock(side_effect=lambda name: brain if name == "BotBrain" else None)

        cog = PainterCog(bot)

        interaction = MagicMock(spec=discord.Interaction)
        interaction.user = MagicMock()
        interaction.user.id = 12345
        interaction.user.display_name = "Alice"
        interaction.followup = MagicMock()
        interaction.followup.send = AsyncMock()

        # 1. Without attachment
        await cog.execute_draw(interaction, "竹林中的小貓", "水墨畫")

        # Assert Discord message was sent
        assert interaction.followup.send.called
        kwargs = interaction.followup.send.call_args.kwargs
        assert "embed" in kwargs
        assert "file" in kwargs
        assert "view" in kwargs
        assert "竹林中的小貓" in kwargs["embed"].title

        # 2. With image attachment (Multimodal Gemma mode)
        attachment = MagicMock(spec=discord.Attachment)
        attachment.content_type = "image/png"
        attachment.read = AsyncMock(return_value=b"\x89PNG\r\n\x1a\n\x00\x00fake_image")
        await cog.execute_draw(interaction, "臨摹這張照片", "watercolor", attachment=attachment)
        assert interaction.followup.send.call_count == 2

        # 3. With Two-Pass Self-Critique & Refine Pass
        await cog.execute_draw(interaction, "雙階段水彩城堡", "watercolor", refine=True)
        assert interaction.followup.send.call_count == 3

    asyncio.run(_run())


def test_draw_control_view_callbacks() -> None:
    """Test interaction buttons on DrawCanvasControlView."""
    import asyncio

    async def _run() -> None:
        bot = MagicMock()
        art_cog = MagicMock()
        art_cog.execute_draw = AsyncMock()
        bot.get_cog = MagicMock(side_effect=lambda name: art_cog if name == "PainterCog" else None)

        author = MagicMock()
        view = DrawCanvasControlView(bot, "月夜", ArtStyle.INK_WASH, author)

        # 1. Non-author interaction
        other_user = MagicMock()
        inter_other = MagicMock()
        inter_other.user = other_user
        inter_other.response.send_message = AsyncMock()
        await view.redraw_button.callback(inter_other)
        assert inter_other.response.send_message.called

        # 2. Author redraw
        inter_author = MagicMock()
        inter_author.user = author
        inter_author.response.defer = AsyncMock()
        await view.redraw_button.callback(inter_author)
        assert art_cog.execute_draw.called

    asyncio.run(_run())


def test_setup_art_cog() -> None:
    """Test setup function adds cog to bot."""
    import asyncio

    async def _run() -> None:
        bot = MagicMock()
        bot.add_cog = AsyncMock()
        await painter_setup(bot)
        assert bot.add_cog.called

    asyncio.run(_run())
