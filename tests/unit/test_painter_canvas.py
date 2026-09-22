"""
tests/unit/test_painter_canvas.py
=================================
Unit tests for VirtualCanvas rendering, textures, and shape operations.
"""

from __future__ import annotations

from pathlib import Path

from mod.painter.canvas import VirtualCanvas
from mod.painter.models import ArtStyle, BrushType, ShapeType


def test_canvas_initialization_and_textures(tmp_path: Path) -> None:
    """Test canvas initialization across multiple art styles."""
    for style in [
        ArtStyle.INK_WASH,
        ArtStyle.WATERCOLOR,
        ArtStyle.NEON,
        ArtStyle.CRAYON,
        ArtStyle.OIL,
        ArtStyle.FREESTYLE,
    ]:
        canvas = VirtualCanvas(width=200, height=200, style=style)
        assert canvas.width == 200
        assert canvas.height == 200
        img = canvas.flatten()
        assert img.size == (200, 200)

        # Test export bytes
        jpg_bytes = canvas.export_bytes(format_name="JPEG")
        assert len(jpg_bytes) > 100
        assert jpg_bytes.startswith(b"\xff\xd8")  # JPEG header


def test_canvas_shapes_and_strokes(tmp_path: Path) -> None:
    """Test drawing various shapes and stroke primitives."""
    canvas = VirtualCanvas(width=300, height=300)

    # 1. Circle & Rect
    canvas.draw_shape(ShapeType.CIRCLE, [100, 100, 30], fill_color="#FF5500", outline_color="#000000")
    canvas.draw_shape(ShapeType.RECT, [20, 20, 50, 50], fill_color="#00AAFF")

    # 2. Polygon, Mountain, Cloud, Moon, Star
    canvas.draw_shape(ShapeType.POLYGON, [(0, 0), (50, 0), (25, 50)], fill_color="#00FF00")
    canvas.draw_shape(ShapeType.MOUNTAIN, [(0, 200), (100, 100), (200, 200), (0, 200)], fill_color="#444444")
    canvas.draw_shape(ShapeType.CLOUD, [150, 80, 25], fill_color="#EEEEEE")
    canvas.draw_shape(ShapeType.MOON, [250, 50, 20], fill_color="#FFF8DC")
    canvas.draw_shape(ShapeType.STAR, [50, 250, 15, 6], fill_color="#FFD700")

    # 3. Strokes & Wash & Splatter & Stamp
    canvas.draw_stroke([(10, 10), (100, 100)], brush_type=BrushType.INK_WASH, size=10, color="#111111")
    canvas.draw_wash("#336699", opacity=0.2, direction="vertical")
    canvas.draw_wash("#FF0055", opacity=0.1, direction="radial")
    canvas.draw_splatter(150, 150, radius=20, count=10, color="#000000")
    canvas.draw_seal_stamp(220, 220, text="雪莉", size=36)

    # 4. Save to disk test
    out_file = tmp_path / "test_art.jpg"
    canvas.save_to_disk(out_file)
    assert out_file.exists()
    assert out_file.stat().st_size > 500
