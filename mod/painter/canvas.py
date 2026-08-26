"""
mod/painter/canvas.py
=====================
Multi-layer Virtual Canvas system with paper textures and procedural shapes.
"""

from __future__ import annotations

import io
import math
import random
from pathlib import Path
from typing import Any, Sequence

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from mod.painter.brushes import BrushEngine
from mod.painter.models import ArtStyle, BrushConfig, BrushType, RGBAColor, RGBColor, ShapeType
from mod.painter.palette import PaletteManager


class VirtualCanvas:
    """Multi-layer procedural canvas for digital painting rendering."""

    def __init__(
        self,
        width: int = 800,
        height: int = 800,
        style: ArtStyle = ArtStyle.FREESTYLE,
    ) -> None:
        self.width = max(100, width)
        self.height = max(100, height)
        self.style = style
        self._layers: dict[str, Image.Image] = {
            "bg": Image.new("RGBA", (self.width, self.height), (255, 255, 255, 255)),
            "main": Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0)),
            "fg": Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0)),
            "fx": Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0)),
        }
        self._init_default_background()

    def _init_default_background(self) -> None:
        """Initialize appropriate background paper texture based on style."""
        if self.style == ArtStyle.INK_WASH:
            self.set_background(PaletteManager.TRADITIONAL_INK[-1], texture="rice_paper")
        elif self.style == ArtStyle.NEON:
            self.set_background(RGBAColor(12, 14, 25, 1.0), texture="cyber_grid")
        elif self.style == ArtStyle.WATERCOLOR:
            self.set_background(RGBAColor(252, 250, 246, 1.0), texture="watercolor_grain")
        elif self.style == ArtStyle.CRAYON:
            self.set_background(RGBAColor(248, 244, 235, 1.0), texture="rough_paper")
        elif self.style == ArtStyle.OIL:
            self.set_background(RGBAColor(235, 228, 215, 1.0), texture="canvas_linen")
        else:
            self.set_background(RGBAColor(255, 255, 255, 1.0), texture="plain")

    def _get_layer(self, name: str) -> Image.Image:
        """Get or create target layer."""
        if name not in self._layers:
            self._layers[name] = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        return self._layers[name]

    def get_layer(self, name: str) -> Image.Image:
        """Public accessor to get or create target canvas layer."""
        return self._get_layer(name)

    def set_background(
        self, color: RGBAColor | RGBColor | str | tuple[int, ...], texture: str = "plain"
    ) -> None:
        """Set solid background color and apply procedural texture.
        
        Args:
            color: Solid color representation.
            texture: Texture style ('plain', 'rice_paper', 'watercolor_grain', 'cyber_grid', 'canvas_linen', 'rough_paper').
        """
        c = PaletteManager.parse_color(color)
        bg = Image.new("RGBA", (self.width, self.height), (c.r, c.g, c.b, 255))
        draw = ImageDraw.Draw(bg, "RGBA")

        if texture == "rice_paper":
            # Subtle fiber specks and warm organic grain (宣紙纖維)
            for _ in range(int(self.width * self.height * 0.0008)):
                rx = random.randint(0, self.width - 1)
                ry = random.randint(0, self.height - 1)
                alpha = random.randint(10, 30)
                draw.ellipse([rx, ry, rx + random.randint(1, 3), ry + random.randint(1, 3)], fill=(120, 110, 95, alpha))
            for _ in range(int(self.width * 0.05)):
                fx = random.randint(0, self.width - 20)
                fy = random.randint(0, self.height - 20)
                draw.line([(fx, fy), (fx + random.randint(5, 18), fy + random.randint(-4, 4))], fill=(160, 145, 130, 25), width=1)

        elif texture == "cyber_grid":
            # Dark grid with subtle neon coordinates
            grid_gap = 40
            grid_color = (25, 35, 65, 80)
            for x in range(0, self.width, grid_gap):
                draw.line([(x, 0), (x, self.height)], fill=grid_color, width=1)
            for y in range(0, self.height, grid_gap):
                draw.line([(0, y), (self.width, y)], fill=grid_color, width=1)

        elif texture == "watercolor_grain":
            # Gentle porous paper grain
            for _ in range(int(self.width * self.height * 0.001)):
                gx = random.randint(0, self.width - 1)
                gy = random.randint(0, self.height - 1)
                shade = random.randint(210, 240)
                draw.point((gx, gy), fill=(shade, shade, shade, 20))

        elif texture == "rough_paper":
            # Rough wax crayon paper: cross-hatch short fibers + coarse grain
            for _ in range(int(self.width * self.height * 0.0018)):
                gx = random.randint(0, self.width - 1)
                gy = random.randint(0, self.height - 1)
                shade = random.randint(160, 220)
                alpha = random.randint(15, 45)
                length = random.randint(3, 12)
                angle_deg = random.choice([0, 30, 60, 90, 120, 150])
                rad = math.radians(angle_deg)
                ex = int(gx + length * math.cos(rad))
                ey = int(gy + length * math.sin(rad))
                draw.line([(gx, gy), (ex, ey)], fill=(shade, shade, shade - 10, alpha), width=1)
            # Coarse grain dots
            for _ in range(int(self.width * self.height * 0.0006)):
                gx = random.randint(0, self.width - 1)
                gy = random.randint(0, self.height - 1)
                shade = random.randint(140, 200)
                draw.point((gx, gy), fill=(shade, shade - 5, shade - 15, random.randint(20, 50)))

        elif texture == "canvas_linen":
            # Woven canvas texture
            for y in range(0, self.height, 4):
                draw.line([(0, y), (self.width, y)], fill=(180, 170, 155, 15), width=1)
            for x in range(0, self.width, 4):
                draw.line([(x, 0), (x, self.height)], fill=(180, 170, 155, 15), width=1)

        self._layers["bg"] = bg

    def draw_stroke(
        self,
        points: Sequence[tuple[float, float]],
        brush_type: BrushType | str = BrushType.PENCIL,
        size: float = 6.0,
        color: RGBAColor | str | tuple[int, ...] = "#000000",
        opacity: float = 1.0,
        layer_name: str = "main",
    ) -> None:
        """Render a stroke using specified brush algorithm onto a layer.
        
        Args:
            points: Ordered sequence of (x, y) coordinates.
            brush_type: BrushType enum or string name.
            size: Brush width / thickness in pixels.
            color: Color value.
            opacity: Opacity between 0.0 and 1.0.
            layer_name: Target layer name ('main', 'fg', 'fx', etc.).
        """
        if not points:
            return

        b_type = BrushType(brush_type) if isinstance(brush_type, str) else brush_type
        c = PaletteManager.parse_color(color, alpha=opacity)
        config = BrushConfig(type=b_type, size=max(1.0, float(size)), color=c)
        layer = self._get_layer(layer_name)

        brush = BrushEngine.get_brush(b_type)
        brush.render_stroke(layer, points, config)

    def draw_shape(
        self,
        shape_type: ShapeType | str,
        coords: Any,
        fill_color: RGBAColor | str | tuple[int, ...] | None = None,
        outline_color: RGBAColor | str | tuple[int, ...] | None = None,
        outline_width: int = 2,
        layer_name: str = "main",
    ) -> None:
        """Render structured geometric or organic shape.
        
        Args:
            shape_type: ShapeType enum or name ('circle', 'rect', 'cloud', 'mountain', etc.).
            coords: Coordinates format depending on shape (e.g. [x, y, r] for circle, [x, y, w, h] for rect, list of points).
            fill_color: Optional fill color.
            outline_color: Optional outline stroke color.
            outline_width: Outline stroke thickness.
            layer_name: Target layer name.
        """
        s_type = ShapeType(shape_type) if isinstance(shape_type, str) else shape_type
        layer = self._get_layer(layer_name)
        draw = ImageDraw.Draw(layer, "RGBA")

        fc = PaletteManager.parse_color(fill_color).tuple_rgba if fill_color else None
        oc = PaletteManager.parse_color(outline_color).tuple_rgba if outline_color else None

        if s_type == ShapeType.CIRCLE:
            # coords: [cx, cy, radius] or [x1, y1, x2, y2]
            if len(coords) == 3:
                cx, cy, r = coords[0], coords[1], coords[2]
                box = [cx - r, cy - r, cx + r, cy + r]
            else:
                box = coords
            draw.ellipse(box, fill=fc, outline=oc, width=outline_width)

        elif s_type == ShapeType.RECT:
            # coords: [x, y, w, h] or [x1, y1, x2, y2]
            if len(coords) == 4:
                x, y, w, h = coords
                box = [x, y, x + w, y + h] if w > 0 and h > 0 and (x + w <= self.width * 2) else coords
            else:
                box = coords
            draw.rectangle(box, fill=fc, outline=oc, width=outline_width)

        elif s_type == ShapeType.ELLIPSE:
            draw.ellipse(coords, fill=fc, outline=oc, width=outline_width)

        elif s_type == ShapeType.POLYGON:
            # coords: list of (x, y) tuples
            pts = [(float(p[0]), float(p[1])) for p in coords]
            draw.polygon(pts, fill=fc, outline=oc)

        elif s_type == ShapeType.LINE:
            pts = [(float(p[0]), float(p[1])) for p in coords]
            draw.line(pts, fill=oc or fc, width=outline_width)

        elif s_type == ShapeType.MOUNTAIN:
            # coords: list of apex points or bottom bounds [base_y, peak1_x, peak1_y, peak2_x, peak2_y...]
            pts = [(float(p[0]), float(p[1])) for p in coords]
            if len(pts) >= 3:
                draw.polygon(pts, fill=fc, outline=oc)

        elif s_type == ShapeType.CLOUD:
            # coords: [cx, cy, scale] - multiple overlapping soft circles
            cx, cy, scale = coords[0], coords[1], coords[2] if len(coords) >= 3 else 30
            for ox, oy, rad in [(-scale * 0.6, 0, scale * 0.8), (0, -scale * 0.4, scale), (scale * 0.6, 0, scale * 0.8), (-scale * 0.2, scale * 0.2, scale * 0.7), (scale * 0.2, scale * 0.2, scale * 0.7)]:
                draw.ellipse([cx + ox - rad, cy + oy - rad, cx + ox + rad, cy + oy + rad], fill=fc, outline=oc)

        elif s_type == ShapeType.MOON:
            # Crescent or Full moon: coords [cx, cy, radius, phase(0.0-1.0)]
            cx, cy, r = coords[0], coords[1], coords[2]
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fc or (255, 245, 200, 240), outline=oc)

        elif s_type == ShapeType.STAR:
            # coords: [cx, cy, outer_r, inner_r, points_count]
            cx, cy = coords[0], coords[1]
            outer_r = coords[2] if len(coords) > 2 else 12
            inner_r = coords[3] if len(coords) > 3 else outer_r * 0.4
            num_pts = 5
            star_pts = []
            for i in range(num_pts * 2):
                rad = outer_r if i % 2 == 0 else inner_r
                ang = i * math.pi / num_pts - math.pi / 2
                star_pts.append((cx + math.cos(ang) * rad, cy + math.sin(ang) * rad))
            draw.polygon(star_pts, fill=fc, outline=oc)

    def draw_splatter(
        self,
        x: float,
        y: float,
        radius: float = 30.0,
        count: int = 15,
        color: RGBAColor | str | tuple[int, ...] = "#000000",
        opacity: float = 0.8,
        layer_name: str = "main",
    ) -> None:
        """Procedural ink splatter / watercolor paint drops.
        
        Args:
            x: Center X.
            y: Center Y.
            radius: Splash spread radius.
            count: Number of droplets.
            color: Droplet color.
            opacity: Droplet opacity.
            layer_name: Target layer.
        """
        layer = self._get_layer(layer_name)
        draw = ImageDraw.Draw(layer, "RGBA")
        c = PaletteManager.parse_color(color, alpha=opacity)

        for _ in range(count):
            angle = random.random() * 2 * math.pi
            dist = (random.random() ** 1.5) * radius
            sx = x + math.cos(angle) * dist
            sy = y + math.sin(angle) * dist
            drop_r = max(0.8, (1.0 - dist / max(1.0, radius)) * 4.0 * random.random() + 0.5)
            alpha_int = int(c.a * 255 * (0.5 + random.random() * 0.5))
            draw.ellipse([sx - drop_r, sy - drop_r, sx + drop_r, sy + drop_r], fill=(c.r, c.g, c.b, alpha_int))

    def draw_wash(
        self,
        color: RGBAColor | str | tuple[int, ...],
        opacity: float = 0.25,
        direction: str = "vertical",
        layer_name: str = "main",
    ) -> None:
        """Full-canvas atmospheric color wash / gradient.
        
        Args:
            color: Tint color.
            opacity: Max wash intensity.
            direction: 'vertical', 'horizontal', 'radial', 'uniform'.
            layer_name: Target layer.
        """
        layer = self._get_layer(layer_name)
        c = PaletteManager.parse_color(color, alpha=opacity)
        overlay = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay, "RGBA")

        if direction == "vertical":
            # Top-to-bottom: color fades from bottom (strongest) to top (transparent)
            # This gives richer sky horizon / ground tone
            steps = 60
            for i in range(steps):
                y1 = int(i * self.height / steps)
                y2 = int((i + 1) * self.height / steps)
                alpha_factor = (i / steps) * c.a  # increases toward bottom
                draw.rectangle([0, y1, self.width, y2], fill=(c.r, c.g, c.b, int(alpha_factor * 255)))

        elif direction == "horizontal":
            steps = 60
            for i in range(steps):
                x1 = int(i * self.width / steps)
                x2 = int((i + 1) * self.width / steps)
                alpha_factor = (i / steps) * c.a
                draw.rectangle([x1, 0, x2, self.height], fill=(c.r, c.g, c.b, int(alpha_factor * 255)))

        elif direction == "radial":
            cx, cy = self.width / 2.0, self.height / 2.0
            max_r = math.hypot(cx, cy)
            steps = 40
            for i in range(steps, 0, -1):
                r = max_r * (i / steps)
                alpha_factor = (1.0 - i / steps) * c.a
                draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(c.r, c.g, c.b, int(alpha_factor * 255)))

        else:
            draw.rectangle([0, 0, self.width, self.height], fill=c.tuple_rgba)

        overlay = overlay.filter(ImageFilter.GaussianBlur(radius=8.0))
        layer.alpha_composite(overlay)

    def draw_seal_stamp(
        self,
        x: float,
        y: float,
        text: str = "雪莉",
        size: int = 42,
        layer_name: str = "fg",
    ) -> None:
        """Render a traditional Chinese red seal stamp (朱文/白文雅印).
        
        Args:
            x: Top-left X.
            y: Top-left Y.
            text: 2-4 characters for the seal (e.g. "雪莉", "雪莉御賞", "雪莉之印").
            size: Width and height of the square stamp.
            layer_name: Target layer (default 'fg').
        """
        layer = self._get_layer(layer_name)
        draw = ImageDraw.Draw(layer, "RGBA")
        seal_red = (195, 45, 38, 235)  # 硃砂印泥紅

        # 1. Outer weathered square frame
        draw.rectangle([x, y, x + size, y + size], fill=(255, 255, 255, 0), outline=seal_red, width=3)
        # Inner fill with slight translucency
        draw.rectangle([x + 3, y + 3, x + size - 3, y + size - 3], fill=(195, 45, 38, 210))

        # 2. Weathered corner erosion simulation
        for _ in range(6):
            ex = x + random.choice([0, 1, size - 1, size])
            ey = y + random.randint(0, size)
            draw.point((ex, ey), fill=(255, 255, 255, 180))

        # 3. Draw Seal Text (White characters or stylized calligraphy)
        chars = list(text[:4])
        font_size = max(10, int(size / (2.1 if len(chars) > 2 else 1.5)))
        font: Any = None
        try:
            # Attempt to use standard Asian font or fallback to default
            font = ImageFont.truetype("msyh.ttc", font_size)
        except Exception:
            try:
                font = ImageFont.truetype("simhei.ttf", font_size)
            except Exception:
                font = ImageFont.load_default()

        # Layout 2x2 or 1x2 grid for seal
        if len(chars) <= 2:
            for idx, ch in enumerate(chars):
                cy = y + 4 + idx * (font_size + 2)
                cx = x + (size - font_size) / 2.0
                draw.text((cx, cy), ch, fill=(255, 250, 240, 240), font=font)
        else:
            # Traditional column order (top-right, bottom-right, top-left, bottom-left)
            pos_map = [
                (x + size / 2.0, y + 4),
                (x + size / 2.0, y + size / 2.0),
                (x + 4, y + 4),
                (x + 4, y + size / 2.0),
            ]
            for idx, ch in enumerate(chars[:4]):
                cx, cy = pos_map[idx]
                draw.text((cx, cy), ch, fill=(255, 250, 240, 240), font=font)

    def flatten(self) -> Image.Image:
        """Composite all layers into a single RGB Image.
        
        Returns:
            Flattened RGB Pillow Image.
        """
        combined = Image.new("RGBA", (self.width, self.height), (255, 255, 255, 255))
        for layer_key in ["bg", "main", "fg", "fx"]:
            if layer_key in self._layers:
                combined.alpha_composite(self._layers[layer_key])
        return combined.convert("RGB")

    def composite(self) -> Image.Image:
        """Alias for flatten()."""
        return self.flatten()

    def export_bytes(self, format_name: str = "JPEG", quality: int = 92) -> bytes:
        """Export the rendered canvas to image bytes.
        
        Args:
            format_name: "JPEG" or "PNG".
            quality: Compression quality (1-100).
            
        Returns:
            Image bytes in specified format.
        """
        img = self.flatten()
        buffer = io.BytesIO()
        img.save(buffer, format=format_name, quality=quality)
        return buffer.getvalue()

    def save_to_disk(
        self, filepath: Path | str, format_name: str = "JPEG", quality: int = 92
    ) -> Path:
        """Export and save the canvas image directly to disk.
        
        Args:
            filepath: Destination file path.
            format_name: "JPEG" or "PNG".
            quality: Image quality.
            
        Returns:
            Saved Path object.
        """
        target = Path(filepath)
        target.parent.mkdir(parents=True, exist_ok=True)
        img = self.flatten()
        img.save(target, format=format_name, quality=quality)
        return target
