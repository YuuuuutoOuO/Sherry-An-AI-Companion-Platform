"""
tests/unit/test_painter_agent.py
================================
Unit tests for PainterAgent prompt builder, LLM parsing, fallback, and persistence.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from mod.painter import config
from mod.painter.agent import PainterAgent, get_live_painting_status
from mod.painter.models import ArtStyle


def test_art_style_from_str() -> None:
    """Test fuzzy style string parsing."""
    assert ArtStyle.from_str("水墨畫") == ArtStyle.INK_WASH
    assert ArtStyle.from_str("ink") == ArtStyle.INK_WASH
    assert ArtStyle.from_str("watercolor") == ArtStyle.WATERCOLOR
    assert ArtStyle.from_str("水彩") == ArtStyle.WATERCOLOR
    assert ArtStyle.from_str("塗鴉") == ArtStyle.DOODLE
    assert ArtStyle.from_str("pixel") == ArtStyle.PIXEL_ART
    assert ArtStyle.from_str("霓虹") == ArtStyle.NEON
    assert ArtStyle.from_str("蠟筆") == ArtStyle.CRAYON
    assert ArtStyle.from_str("油畫") == ArtStyle.OIL
    assert ArtStyle.from_str("隨便") == ArtStyle.FREESTYLE


def test_build_user_prompt_multimodal() -> None:
    """Test standard and multimodal prompt synthesis."""
    standard_prompt = PainterAgent.build_user_prompt("貓咪", ArtStyle.INK_WASH, has_image=False)
    assert "貓咪" in standard_prompt
    assert "ink_wash" in standard_prompt

    multimodal_prompt = PainterAgent.build_user_prompt("貓咪", ArtStyle.WATERCOLOR, has_image=True)
    assert "多模態視覺臨摹" in multimodal_prompt
    assert "參考圖片" in multimodal_prompt


def test_build_reflection_user_prompt() -> None:
    """Test reflection and self-critique prompt synthesis."""
    refl_prompt = PainterAgent.build_reflection_user_prompt("櫻花樹", ArtStyle.WATERCOLOR, "第一階段初步完成了樹幹佈局")
    assert "第二階段：畫家自我視覺審視與精修" in refl_prompt
    assert "櫻花樹" in refl_prompt
    assert "第一階段初步完成了樹幹佈局" in refl_prompt


def test_parse_llm_response_markdown() -> None:
    """Test extracting JSON commands from markdown code block."""
    raw = """
這是我為你特別創作的水墨月夜小貓～希望你喜歡！
```json
[
  {"action": "set_background", "params": {"color": "#F8F5EC", "texture": "rice_paper"}},
  {"action": "stroke", "params": {"brush": "ink_wash", "size": 18, "points": [[200, 700], [540, 700]]}}
]
```
這是雪莉最得意的筆觸喔！
"""
    commentary, commands = PainterAgent.parse_llm_response(raw)
    assert "水墨月夜小貓" in commentary
    assert len(commands) == 2
    assert commands[0]["action"] == "set_background"


def test_parse_llm_response_relaxed() -> None:
    """Test recovering malformed JSON with trailing commas."""
    raw = """
[
  {"action": "set_background", "params": {"color": "#FFFFFF",}},
  {"action": "stamp", "params": {"text": "雪莉",}},
]
"""
    commentary, commands = PainterAgent.parse_llm_response(raw)
    assert len(commands) == 2


def test_generate_procedural_fallback() -> None:
    """Test procedural command generation for each art style."""
    cmds = PainterAgent.generate_procedural_fallback("測試主題", ArtStyle.INK_WASH)
    assert len(cmds) >= 5
    actions = [c["action"] for c in cmds]
    assert "set_background" in actions
    assert any("landscape" in a or "character" in a or "architecture" in a or "stroke" in a for a in actions)


def test_procedural_fallback_diversity() -> None:
    """Ensure two consecutive procedural fallback generations are not identical."""
    cmds1 = PainterAgent.generate_procedural_fallback("星空下的黑貓", ArtStyle.INK_WASH)
    cmds2 = PainterAgent.generate_procedural_fallback("星空下的黑貓", ArtStyle.INK_WASH)
    # Check that random coords, splatter or stars vary
    assert cmds1 != cmds2


def test_paint_and_save(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test painting and saving image to ./data/painting/*.jpg with metadata."""
    import asyncio

    async def _run() -> None:
        test_painting_dir = tmp_path / "painting"
        monkeypatch.setattr(config, "PAINTING_DIR", test_painting_dir)

        cmds = PainterAgent.generate_procedural_fallback("月夜黑貓", ArtStyle.INK_WASH)
        canvas, meta, jpg_path = await PainterAgent.paint_and_save(
            prompt="月夜黑貓",
            style=ArtStyle.INK_WASH,
            commands=cmds,
            author_id="123456",
            author_name="TestUser",
        )

        assert jpg_path.exists()
        assert jpg_path.name.endswith(".jpg")
        assert meta.command_count == len(cmds)
        assert meta.prompt == "月夜黑貓"
        assert meta.style == "ink_wash"

        # Verify JSON metadata file
        json_path = jpg_path.with_suffix(".json")
        assert json_path.exists()
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["prompt"] == "月夜黑貓"
        assert len(data["commands"]) == len(cmds)

        # Check live status tracker
        status = get_live_painting_status()
        assert not status["is_drawing"]

    asyncio.run(_run())
