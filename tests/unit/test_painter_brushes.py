"""
tests/unit/test_painter_brushes.py
==================================
Unit tests for brush engines and rendering algorithms.
"""

from __future__ import annotations

import pytest
from PIL import Image

from mod.painter.brushes import (
    Airbrush,
    BrushEngine,
    CrayonBrush,
    EraserBrush,
    InkWashBrush,
    MarkerBrush,
    OilBrush,
    PencilBrush,
    PixelBrush,
    WatercolorBrush,
    _interpolate_points,
)
from mod.painter.models import BrushConfig, BrushType, RGBAColor


def test_interpolate_points() -> None:
    """Test point interpolation helper."""
    assert _interpolate_points([]) == []
    assert _interpolate_points([(10.0, 20.0)]) == [(10.0, 20.0)]
    pts = _interpolate_points([(0.0, 0.0), (10.0, 0.0)], step_size=2.0)
    assert len(pts) >= 5
    assert pts[0] == (0.0, 0.0)
    assert pts[-1] == (10.0, 0.0)


@pytest.mark.parametrize(
    "brush_type,brush_cls",
    [
        (BrushType.PENCIL, PencilBrush),
        (BrushType.INK_WASH, InkWashBrush),
        (BrushType.WATERCOLOR, WatercolorBrush),
        (BrushType.CRAYON, CrayonBrush),
        (BrushType.OIL, OilBrush),
        (BrushType.AIRBRUSH, Airbrush),
        (BrushType.MARKER, MarkerBrush),
        (BrushType.PIXEL, PixelBrush),
        (BrushType.ERASER, EraserBrush),
    ],
)
def test_all_brushes_render_stroke_and_stamp(brush_type: BrushType, brush_cls: type) -> None:
    """Ensure all brush types execute render_stroke and render_stamp without error."""
    layer = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    brush = BrushEngine.get_brush(brush_type)
    assert isinstance(brush, brush_cls)

    config = BrushConfig(
        type=brush_type,
        size=8.0,
        color=RGBAColor(200, 50, 50, 0.9),
    )

    # Stroke with multiple points
    brush.render_stroke(layer, [(10, 10), (50, 30), (90, 80)], config)
    
    # Stamp at single point
    brush.render_stamp(layer, 50, 50, config)

    # Empty points check (should gracefully no-op)
    brush.render_stroke(layer, [], config)

    # Verify image has non-zero alpha pixels drawn
    alpha_channel = layer.split()[3]
    assert alpha_channel.getextrema()[1] > 0 or brush_type == BrushType.ERASER
