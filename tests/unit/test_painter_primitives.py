"""
tests/unit/test_painter_primitives.py
=====================================
Unit tests for High-Level Semantic Vector DSL renderers in mod.painter.primitives.
"""

from __future__ import annotations

from mod.painter.canvas import VirtualCanvas
from mod.painter.models import ArtStyle
from mod.painter.primitives import (
    ArchitectureRenderer,
    CharacterRenderer,
    LandscapeRenderer,
    ObjectRenderer,
    WeatherFxRenderer,
)
from mod.painter.interpreter import CommandInterpreter


def test_character_renderer_anime_girl_swimsuit() -> None:
    """Test rendering anime girl in school swimsuit pose."""
    canvas = VirtualCanvas(800, 800, style=ArtStyle.WATERCOLOR)
    CharacterRenderer.draw(
        canvas,
        {
            "type": "anime_girl",
            "outfit": "school_swimsuit",
            "pose": "sitting",
            "x": 400,
            "y": 600,
            "scale": 1.0,
            "hair_color": "#2B2D42",
            "outfit_color": "#1D3557",
        },
        layer_name="main",
    )
    img = canvas.composite()
    assert img.size == (800, 800)


def test_character_renderer_kimono_and_cat() -> None:
    """Test rendering anime girl in kimono and cat silhouette."""
    canvas = VirtualCanvas(800, 800, style=ArtStyle.INK_WASH)
    CharacterRenderer.draw(
        canvas,
        {"type": "anime_girl", "outfit": "kimono", "pose": "standing", "x": 300, "y": 600},
        layer_name="main",
    )
    CharacterRenderer.draw(
        canvas,
        {"type": "cat", "x": 550, "y": 650, "scale": 1.2},
        layer_name="main",
    )
    img = canvas.composite()
    assert img.size == (800, 800)


def test_architecture_renderer_torii_and_pagoda() -> None:
    """Test rendering Japanese Torii Gate and Pagoda."""
    canvas = VirtualCanvas(800, 800, style=ArtStyle.FREESTYLE)
    ArchitectureRenderer.draw(
        canvas,
        {"type": "torii_gate", "x": 400, "y": 700, "width": 350, "height": 320, "color_main": "#D90429"},
        layer_name="main",
    )
    ArchitectureRenderer.draw(
        canvas,
        {"type": "japanese_pagoda", "x": 180, "y": 500, "width": 200, "height": 280},
        layer_name="main",
    )
    img = canvas.composite()
    assert img.size == (800, 800)


def test_architecture_renderer_castle_and_city() -> None:
    """Test rendering castle, cottage and city skyline."""
    canvas = VirtualCanvas(800, 800, style=ArtStyle.NEON)
    ArchitectureRenderer.draw(canvas, {"type": "castle", "x": 250, "y": 600}, layer_name="main")
    ArchitectureRenderer.draw(canvas, {"type": "city_skyline", "x": 600, "y": 600}, layer_name="main")
    ArchitectureRenderer.draw(canvas, {"type": "cozy_cottage", "x": 400, "y": 750}, layer_name="main")
    img = canvas.composite()
    assert img.size == (800, 800)


def test_landscape_renderer_all_types() -> None:
    """Test rendering sakura trees, bamboo, ocean waves, misty mountains, and galaxy."""
    canvas = VirtualCanvas(800, 800, style=ArtStyle.WATERCOLOR)
    LandscapeRenderer.draw(canvas, {"type": "cherry_tree", "x": 200, "y": 500, "scale": 1.0}, layer_name="bg")
    LandscapeRenderer.draw(canvas, {"type": "bamboo_grove", "x": 600, "y": 500, "scale": 1.0}, layer_name="bg")
    LandscapeRenderer.draw(canvas, {"type": "ocean_waves"}, layer_name="bg")
    LandscapeRenderer.draw(canvas, {"type": "misty_mountains"}, layer_name="bg")
    LandscapeRenderer.draw(canvas, {"type": "starry_galaxy"}, layer_name="bg")
    img = canvas.composite()
    assert img.size == (800, 800)


def test_weather_fx_renderer_all_types() -> None:
    """Test rendering sakura blizzard, shooting stars, fireflies, and sunbeams."""
    canvas = VirtualCanvas(800, 800, style=ArtStyle.WATERCOLOR)
    WeatherFxRenderer.draw(canvas, {"type": "sakura_blizzard", "count": 25}, layer_name="fx")
    WeatherFxRenderer.draw(canvas, {"type": "shooting_stars", "count": 10}, layer_name="fx")
    WeatherFxRenderer.draw(canvas, {"type": "fireflies", "count": 20}, layer_name="fx")
    WeatherFxRenderer.draw(canvas, {"type": "sunbeams"}, layer_name="fx")
    img = canvas.composite()
    assert img.size == (800, 800)


def test_object_renderer_lantern_and_boat() -> None:
    """Test rendering lantern and boat props."""
    canvas = VirtualCanvas(800, 800, style=ArtStyle.INK_WASH)
    ObjectRenderer.draw(canvas, {"type": "lantern", "x": 300, "y": 300, "scale": 1.0}, layer_name="main")
    ObjectRenderer.draw(canvas, {"type": "boat", "x": 500, "y": 600, "scale": 1.0}, layer_name="main")
    img = canvas.composite()
    assert img.size == (800, 800)


def test_command_interpreter_executes_high_level_dsl() -> None:
    """Test CommandInterpreter correctly executing semantic DSL commands."""
    canvas = VirtualCanvas(800, 800, style=ArtStyle.WATERCOLOR)
    interpreter = CommandInterpreter(canvas)
    commands = [
        {"action": "set_background", "params": {"color": "#FFE5B4"}},
        {"action": "draw_landscape", "params": {"type": "ocean_waves"}},
        {"action": "draw_character", "params": {"type": "anime_girl", "outfit": "school_swimsuit", "pose": "sitting"}},
        {"action": "draw_architecture", "params": {"type": "torii_gate"}},
        {"action": "draw_weather_fx", "params": {"type": "sunbeams"}},
        {"action": "draw_object", "params": {"type": "lantern"}},
        {"action": "stamp", "params": {"text": "雪莉"}},
    ]
    success_count = interpreter.execute_batch(commands)
    assert success_count == len(commands)
    assert len(interpreter.executed_commands) == len(commands)
