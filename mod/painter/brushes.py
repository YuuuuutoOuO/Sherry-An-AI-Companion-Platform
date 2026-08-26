"""
mod/painter/brushes.py
======================
High-fidelity digital brush engines and procedural rendering algorithms.
"""

from __future__ import annotations

import math
import random
from abc import ABC, abstractmethod
from typing import Sequence

from PIL import Image, ImageDraw, ImageFilter

from mod.painter.models import BrushConfig, BrushType, RGBAColor


def _interpolate_points(
    points: Sequence[tuple[float, float]], step_size: float = 2.0
) -> list[tuple[float, float]]:
    """Interpolate dense sequence of points along a polyline.
    
    Args:
        points: Ordered control points.
        step_size: Distance interval between sampled points.
        
    Returns:
        Interpolated dense point coordinates.
    """
    if not points:
        return []
    if len(points) == 1:
        return [(float(points[0][0]), float(points[0][1]))]

    dense: list[tuple[float, float]] = []
    for i in range(len(points) - 1):
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        dist = math.hypot(x2 - x1, y2 - y1)
        steps = max(1, int(dist / max(0.5, step_size)))
        for s in range(steps):
            t = s / steps
            dense.append((x1 + (x2 - x1) * t, y1 + (y2 - y1) * t))
    dense.append((float(points[-1][0]), float(points[-1][1])))
    return dense


class BaseBrush(ABC):
    """Abstract contract for procedural brush rendering."""

    @abstractmethod
    def render_stroke(
        self, layer: Image.Image, points: Sequence[tuple[float, float]], config: BrushConfig
    ) -> None:
        """Render a brush stroke along the given points onto the image layer.
        
        Args:
            layer: Target RGBA Pillow Image.
            points: Ordered sequence of (x, y) coordinates.
            config: Active brush configuration.
        """
        raise NotImplementedError

    @abstractmethod
    def render_stamp(
        self, layer: Image.Image, x: float, y: float, config: BrushConfig
    ) -> None:
        """Render a single brush stamp/dab at (x, y).
        
        Args:
            layer: Target RGBA Pillow Image.
            x: X-coordinate.
            y: Y-coordinate.
            config: Active brush configuration.
        """
        raise NotImplementedError


class PencilBrush(BaseBrush):
    """Pencil & Sketch brush with fine graphite texture and stippling."""

    def render_stroke(
        self, layer: Image.Image, points: Sequence[tuple[float, float]], config: BrushConfig
    ) -> None:
        if not points:
            return
        dense = _interpolate_points(points, step_size=max(1.0, config.size * 0.3))
        draw = ImageDraw.Draw(layer, "RGBA")
        radius = max(0.5, config.size / 2.0)
        base_color = config.color

        for px, py in dense:
            # Add graphite micro-jitter
            jx = px + (random.random() - 0.5) * radius * 0.4
            jy = py + (random.random() - 0.5) * radius * 0.4
            
            # Stippling dots for graphite texture
            alpha_var = max(0.05, min(1.0, base_color.a * (0.6 + random.random() * 0.4)))
            color = (base_color.r, base_color.g, base_color.b, int(alpha_var * 255))
            draw.ellipse([jx - radius, jy - radius, jx + radius, jy + radius], fill=color)

    def render_stamp(
        self, layer: Image.Image, x: float, y: float, config: BrushConfig
    ) -> None:
        draw = ImageDraw.Draw(layer, "RGBA")
        r = max(0.5, config.size / 2.0)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=config.color.tuple_rgba)


class InkWashBrush(BaseBrush):
    """Traditional Chinese ink wash brush with pressure variation & bleed."""

    def render_stroke(
        self, layer: Image.Image, points: Sequence[tuple[float, float]], config: BrushConfig
    ) -> None:
        if not points:
            return
        dense = _interpolate_points(points, step_size=max(1.0, config.size * 0.2))
        total_pts = len(dense)
        if total_pts == 0:
            return

        # Temporary overlay layer for smooth ink bleed
        overlay = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay, "RGBA")
        base_r = max(1.0, config.size / 2.0)
        base_color = config.color

        for i, (px, py) in enumerate(dense):
            # Dynamic calligraphy taper curve (sharp entry & exit, full body)
            t = i / total_pts
            pressure = math.sin(t * math.pi) * 0.8 + 0.3
            curr_r = base_r * pressure

            # Subtle ink dry brush (飛白) at fast strokes or low alpha
            if config.jitter > 0 and random.random() < config.jitter * 0.3:
                continue

            alpha_int = int(base_color.a * 255 * (0.85 + random.random() * 0.15))
            fill_c = (base_color.r, base_color.g, base_color.b, alpha_int)
            draw.ellipse([px - curr_r, py - curr_r, px + curr_r, py + curr_r], fill=fill_c)

        # Apply subtle Gaussian blur for ink water bleed (宣紙墨暈)
        if config.size > 6.0:
            overlay = overlay.filter(ImageFilter.GaussianBlur(radius=max(0.5, config.size * 0.08)))

        # Alpha composite into target layer
        layer.alpha_composite(overlay)

    def render_stamp(
        self, layer: Image.Image, x: float, y: float, config: BrushConfig
    ) -> None:
        overlay = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay, "RGBA")
        r = max(1.0, config.size / 2.0)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=config.color.tuple_rgba)
        if config.size > 8.0:
            overlay = overlay.filter(ImageFilter.GaussianBlur(radius=1.0))
        layer.alpha_composite(overlay)


class WatercolorBrush(BaseBrush):
    """Translucent watercolor brush with soft bleeding edges and glaze layers."""

    def render_stroke(
        self, layer: Image.Image, points: Sequence[tuple[float, float]], config: BrushConfig
    ) -> None:
        if not points:
            return
        dense = _interpolate_points(points, step_size=max(1.5, config.size * 0.25))
        if not dense:
            return

        overlay = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay, "RGBA")
        base_r = max(1.5, config.size / 2.0)
        base_color = config.color
        
        # Lower alpha per dab to simulate translucent watercolor layers
        dab_alpha = max(1, int(base_color.a * 255 * 0.25))

        for px, py in dense:
            # Organic wet water boundary jitter
            jx = px + (random.random() - 0.5) * base_r * 0.3
            jy = py + (random.random() - 0.5) * base_r * 0.3
            r = base_r * (0.85 + random.random() * 0.3)
            
            # Outer wet halo
            draw.ellipse(
                [jx - r, jy - r, jx + r, jy + r],
                fill=(base_color.r, base_color.g, base_color.b, dab_alpha)
            )
            # Inner core
            draw.ellipse(
                [jx - r * 0.6, jy - r * 0.6, jx + r * 0.6, jy + r * 0.6],
                fill=(base_color.r, base_color.g, base_color.b, min(255, dab_alpha * 2))
            )

        # Soft watercolor diffusion
        overlay = overlay.filter(ImageFilter.GaussianBlur(radius=max(1.0, config.size * 0.12)))
        layer.alpha_composite(overlay)

    def render_stamp(
        self, layer: Image.Image, x: float, y: float, config: BrushConfig
    ) -> None:
        overlay = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay, "RGBA")
        r = max(1.5, config.size / 2.0)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=config.color.tuple_rgba)
        overlay = overlay.filter(ImageFilter.GaussianBlur(radius=max(1.0, config.size * 0.1)))
        layer.alpha_composite(overlay)


class CrayonBrush(BaseBrush):
    """Rough wax & chalk texture brush."""

    def render_stroke(
        self, layer: Image.Image, points: Sequence[tuple[float, float]], config: BrushConfig
    ) -> None:
        if not points:
            return
        dense = _interpolate_points(points, step_size=max(1.0, config.size * 0.2))
        draw = ImageDraw.Draw(layer, "RGBA")
        radius = max(1.0, config.size / 2.0)
        base_color = config.color

        for px, py in dense:
            # Scatter porous wax particles around stroke center
            particle_count = max(4, int(radius * 1.5))
            for _ in range(particle_count):
                angle = random.random() * 2 * math.pi
                dist = (random.random() ** 0.5) * radius
                gx = px + math.cos(angle) * dist
                gy = py + math.sin(angle) * dist
                p_rad = max(0.5, radius * 0.25 * random.random())
                p_alpha = int(base_color.a * 255 * (0.4 + random.random() * 0.6))
                draw.ellipse(
                    [gx - p_rad, gy - p_rad, gx + p_rad, gy + p_rad],
                    fill=(base_color.r, base_color.g, base_color.b, p_alpha)
                )

    def render_stamp(
        self, layer: Image.Image, x: float, y: float, config: BrushConfig
    ) -> None:
        draw = ImageDraw.Draw(layer, "RGBA")
        r = max(1.0, config.size / 2.0)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=config.color.tuple_rgba)


class OilBrush(BaseBrush):
    """Opaque impasto oil painting brush with rich bristle textures."""

    def render_stroke(
        self, layer: Image.Image, points: Sequence[tuple[float, float]], config: BrushConfig
    ) -> None:
        if not points:
            return
        dense = _interpolate_points(points, step_size=max(1.0, config.size * 0.2))
        draw = ImageDraw.Draw(layer, "RGBA")
        radius = max(1.0, config.size / 2.0)
        base_color = config.color

        for px, py in dense:
            # Render multi-bristle parallel strokelets
            bristles = max(3, int(radius * 0.8))
            for b in range(bristles):
                offset_x = (b / bristles - 0.5) * radius * 1.5
                offset_y = (random.random() - 0.5) * radius * 0.4
                bx = px + offset_x
                by = py + offset_y
                br_size = max(1.0, radius * 0.4)
                
                # Slight pigment value variation per bristle
                val_mod = (random.random() - 0.5) * 20
                cr = max(0, min(255, int(base_color.r + val_mod)))
                cg = max(0, min(255, int(base_color.g + val_mod)))
                cb = max(0, min(255, int(base_color.b + val_mod)))
                draw.ellipse(
                    [bx - br_size, by - br_size, bx + br_size, by + br_size],
                    fill=(cr, cg, cb, int(base_color.a * 255))
                )

    def render_stamp(
        self, layer: Image.Image, x: float, y: float, config: BrushConfig
    ) -> None:
        draw = ImageDraw.Draw(layer, "RGBA")
        r = max(1.0, config.size / 2.0)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=config.color.tuple_rgba)


class Airbrush(BaseBrush):
    """Soft radial spray / glow airbrush."""

    def render_stroke(
        self, layer: Image.Image, points: Sequence[tuple[float, float]], config: BrushConfig
    ) -> None:
        if not points:
            return
        dense = _interpolate_points(points, step_size=max(2.0, config.size * 0.3))
        overlay = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay, "RGBA")
        radius = max(2.0, config.size / 2.0)
        base_color = config.color
        air_alpha = max(1, int(base_color.a * 255 * 0.15))

        for px, py in dense:
            draw.ellipse(
                [px - radius, py - radius, px + radius, py + radius],
                fill=(base_color.r, base_color.g, base_color.b, air_alpha)
            )

        overlay = overlay.filter(ImageFilter.GaussianBlur(radius=max(1.0, radius * 0.4)))
        layer.alpha_composite(overlay)

    def render_stamp(
        self, layer: Image.Image, x: float, y: float, config: BrushConfig
    ) -> None:
        overlay = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay, "RGBA")
        r = max(2.0, config.size / 2.0)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=config.color.tuple_rgba)
        overlay = overlay.filter(ImageFilter.GaussianBlur(radius=max(1.0, r * 0.4)))
        layer.alpha_composite(overlay)


class MarkerBrush(BaseBrush):
    """Semi-transparent flat chisel marker."""

    def render_stroke(
        self, layer: Image.Image, points: Sequence[tuple[float, float]], config: BrushConfig
    ) -> None:
        if not points or len(points) < 2:
            return
        draw = ImageDraw.Draw(layer, "RGBA")
        width = int(max(1.0, config.size))
        draw.line(list(points), fill=config.color.tuple_rgba, width=width, joint="curve")

    def render_stamp(
        self, layer: Image.Image, x: float, y: float, config: BrushConfig
    ) -> None:
        draw = ImageDraw.Draw(layer, "RGBA")
        r = max(1.0, config.size / 2.0)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=config.color.tuple_rgba)


class PixelBrush(BaseBrush):
    """Crisp non-antialiased pixel grid brush."""

    def render_stroke(
        self, layer: Image.Image, points: Sequence[tuple[float, float]], config: BrushConfig
    ) -> None:
        if not points:
            return
        dense = _interpolate_points(points, step_size=max(1.0, config.size * 0.5))
        draw = ImageDraw.Draw(layer, "RGBA")
        grid_size = max(1, int(config.size))
        color = config.color.tuple_rgba

        for px, py in dense:
            # Snap to integer pixel grid
            gx = int(px // grid_size) * grid_size
            gy = int(py // grid_size) * grid_size
            draw.rectangle([gx, gy, gx + grid_size, gy + grid_size], fill=color)

    def render_stamp(
        self, layer: Image.Image, x: float, y: float, config: BrushConfig
    ) -> None:
        draw = ImageDraw.Draw(layer, "RGBA")
        grid_size = max(1, int(config.size))
        gx = int(x // grid_size) * grid_size
        gy = int(y // grid_size) * grid_size
        draw.rectangle([gx, gy, gx + grid_size, gy + grid_size], fill=config.color.tuple_rgba)


class EraserBrush(BaseBrush):
    """Eraser brush to clear pixels."""

    def render_stroke(
        self, layer: Image.Image, points: Sequence[tuple[float, float]], config: BrushConfig
    ) -> None:
        if not points:
            return
        dense = _interpolate_points(points, step_size=max(1.0, config.size * 0.3))
        draw = ImageDraw.Draw(layer, "RGBA")
        r = max(1.0, config.size / 2.0)
        clear_color = (0, 0, 0, 0)

        for px, py in dense:
            draw.ellipse([px - r, py - r, px + r, py + r], fill=clear_color)

    def render_stamp(
        self, layer: Image.Image, x: float, y: float, config: BrushConfig
    ) -> None:
        draw = ImageDraw.Draw(layer, "RGBA")
        r = max(1.0, config.size / 2.0)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=(0, 0, 0, 0))


class BrushEngine:
    """Factory and registry for brush renderers."""

    _REGISTRY: dict[BrushType, BaseBrush] = {
        BrushType.PENCIL: PencilBrush(),
        BrushType.INK_WASH: InkWashBrush(),
        BrushType.WATERCOLOR: WatercolorBrush(),
        BrushType.OIL: OilBrush(),
        BrushType.CRAYON: CrayonBrush(),
        BrushType.AIRBRUSH: Airbrush(),
        BrushType.MARKER: MarkerBrush(),
        BrushType.PIXEL: PixelBrush(),
        BrushType.ERASER: EraserBrush(),
    }

    @classmethod
    def get_brush(cls, brush_type: BrushType) -> BaseBrush:
        """Get the renderer instance for a specific brush type.
        
        Args:
            brush_type: Target BrushType enum.
            
        Returns:
            BaseBrush instance.
        """
        return cls._REGISTRY.get(brush_type, cls._REGISTRY[BrushType.PENCIL])
