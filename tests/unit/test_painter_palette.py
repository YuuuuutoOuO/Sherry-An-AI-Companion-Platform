"""
tests/unit/test_painter_palette.py
==================================
Unit tests for PaletteManager, color conversions, and style palettes.
"""

from __future__ import annotations


from mod.painter.models import ArtStyle, RGBAColor, RGBColor
from mod.painter.palette import PaletteManager


def test_rgb_color_bounds() -> None:
    """Test RGBColor clamps components within 0-255."""
    c = RGBColor(-10, 300, 128)
    assert c.r == 0
    assert c.g == 255
    assert c.b == 128
    assert c.hex_code == "#00ff80"
    assert c.tuple_rgb == (0, 255, 128)


def test_rgba_color_bounds() -> None:
    """Test RGBAColor clamps alpha between 0.0 and 1.0."""
    c = RGBAColor(100, 150, 200, 1.5)
    assert c.a == 1.0
    assert c.alpha_int == 255
    assert c.tuple_rgba == (100, 150, 200, 255)

    c2 = RGBAColor(100, 150, 200, -0.5)
    assert c2.a == 0.0
    assert c2.alpha_int == 0


def test_parse_color_hex() -> None:
    """Test parsing various hex color representations."""
    # 3-char hex
    c3 = PaletteManager.parse_color("#F0A")
    assert c3.r == 255
    assert c3.g == 0
    assert c3.b == 170

    # 6-char hex
    c6 = PaletteManager.parse_color("#123456")
    assert c6.r == 0x12
    assert c6.g == 0x34
    assert c6.b == 0x56

    # 8-char hex with alpha
    c8 = PaletteManager.parse_color("#12345680")
    assert c8.r == 0x12
    assert c8.g == 0x34
    assert c8.b == 0x56
    assert abs(c8.a - (128 / 255.0)) < 0.01


def test_parse_color_names_and_tuples() -> None:
    """Test parsing named colors and tuple/list formats."""
    c_red = PaletteManager.parse_color("red", alpha=0.5)
    assert c_red.r == 255
    assert c_red.g == 0
    assert c_red.b == 0
    assert c_red.a == 0.5

    c_seal = PaletteManager.parse_color("seal_red")
    assert c_seal.r == 188
    assert c_seal.g == 45
    assert c_seal.b == 40

    c_tuple3 = PaletteManager.parse_color((10, 20, 30))
    assert c_tuple3.r == 10 and c_tuple3.g == 20 and c_tuple3.b == 30

    c_tuple4 = PaletteManager.parse_color((10, 20, 30, 204))  # 204 / 255 = 0.8
    assert c_tuple4.r == 10 and abs(c_tuple4.a - 0.8) < 0.05


def test_get_palette_for_style() -> None:
    """Test palette retrieval for each art style."""
    ink_pal = PaletteManager.get_palette_for_style(ArtStyle.INK_WASH)
    assert len(ink_pal) >= 5
    assert ink_pal[0] == PaletteManager.TRADITIONAL_INK[0]

    wc_pal = PaletteManager.get_palette_for_style(ArtStyle.WATERCOLOR)
    assert len(wc_pal) >= 5

    neon_pal = PaletteManager.get_palette_for_style(ArtStyle.NEON)
    assert PaletteManager.CYBER_NEON[0] in neon_pal

    pixel_pal = PaletteManager.get_palette_for_style(ArtStyle.PIXEL_ART)
    assert len(pixel_pal) == 16


def test_color_blend() -> None:
    """Test linear blending between two colors."""
    black = RGBAColor(0, 0, 0, 1.0)
    white = RGBAColor(200, 200, 200, 1.0)
    mid = PaletteManager.blend(black, white, 0.5)
    assert mid.r == 100
    assert mid.g == 100
    assert mid.b == 100
