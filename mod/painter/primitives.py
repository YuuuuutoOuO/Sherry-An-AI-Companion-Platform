"""
mod/painter/primitives.py
=========================
High-Level Semantic Vector DSL Renderers for Sherry AI Painter.
Provides mathematical geometry and artistic compositing for Characters,
Architecture, Landscapes, Weather FX, and Objects.
"""

from __future__ import annotations

import math
import random
from typing import Any
from PIL import Image, ImageDraw, ImageFilter

from mod.painter.canvas import VirtualCanvas


class CharacterRenderer:
    """Renders proportional anime figures, chibi characters, and animal silhouettes."""

    @staticmethod
    def draw(canvas: VirtualCanvas, params: dict[str, Any], layer_name: str = "main") -> None:
        """Render character on canvas with designated outfit, pose, and color scheme.
        
        Args:
            canvas: VirtualCanvas target.
            params: Dictionary containing character configuration.
            layer_name: Target canvas layer (default 'main').
        """
        char_type = str(params.get("type", "anime_girl")).lower().strip()
        outfit = str(params.get("outfit", "school_swimsuit")).lower().strip()
        pose = str(params.get("pose", "sitting")).lower().strip()
        x = float(params.get("x", canvas.width / 2.0))
        y = float(params.get("y", canvas.height * 0.65))
        scale = float(params.get("scale", 1.0))

        # Color schemes
        skin_color = str(params.get("skin_color", "#FFE0BD"))
        hair_color = str(params.get("hair_color", "#2B2D42"))
        eye_color = str(params.get("eye_color", "#3A86FF"))
        outfit_main = str(params.get("outfit_color", params.get("color", "#1D3557")))
        outfit_trim = str(params.get("trim_color", "#F1FAEE"))

        layer = canvas.get_layer(layer_name)
        draw = ImageDraw.Draw(layer)

        if "cat" in char_type:
            CharacterRenderer._draw_cat(draw, x, y, scale, hair_color, outfit_main)
            return

        # ── Anime Girl / Chibi Renderer ─────────────────────────────
        head_r = 32.0 * scale
        head_cx = x
        head_cy = y - (85.0 * scale if pose in ("sitting", "kneeling") else 120.0 * scale)

        # 1. Back Hair (Behind head and body)
        draw.ellipse(
            [head_cx - head_r * 1.25, head_cy - head_r * 1.15, head_cx + head_r * 1.25, head_cy + head_r * 1.4],
            fill=hair_color,
        )
        # Twin tails or long flowing locks
        draw.polygon(
            [
                (head_cx - head_r * 1.1, head_cy),
                (head_cx - head_r * 1.8, head_cy + head_r * 2.5),
                (head_cx - head_r * 0.9, head_cy + head_r * 2.8),
                (head_cx - head_r * 0.5, head_cy + head_r * 1.2),
            ],
            fill=hair_color,
        )
        draw.polygon(
            [
                (head_cx + head_r * 1.1, head_cy),
                (head_cx + head_r * 1.8, head_cy + head_r * 2.5),
                (head_cx + head_r * 0.9, head_cy + head_r * 2.8),
                (head_cx + head_r * 0.5, head_cy + head_r * 1.2),
            ],
            fill=hair_color,
        )

        # 2. Torso & Outfit
        torso_w = 28.0 * scale
        torso_top = head_cy + head_r * 0.8
        torso_bottom = torso_top + (55.0 * scale if pose in ("sitting", "kneeling") else 75.0 * scale)

        if "swimsuit" in outfit or "sukumizu" in outfit:
            # Classic Japanese School Swimsuit (死庫水)
            # Neck & Chest
            draw.polygon(
                [
                    (head_cx - torso_w * 0.7, torso_top),
                    (head_cx + torso_w * 0.7, torso_top),
                    (head_cx + torso_w * 0.9, torso_bottom),
                    (head_cx - torso_w * 0.9, torso_bottom),
                ],
                fill=outfit_main,
            )
            # White Name Tag on Chest
            draw.rectangle(
                [head_cx - 10 * scale, torso_top + 16 * scale, head_cx + 10 * scale, torso_top + 28 * scale],
                fill=outfit_trim,
                outline="#999999",
                width=1,
            )
            # Swimsuit shoulder straps
            draw.line([(head_cx - torso_w * 0.6, torso_top), (head_cx - torso_w * 0.4, torso_top + 15 * scale)], fill=outfit_main, width=int(4 * scale))
            draw.line([(head_cx + torso_w * 0.6, torso_top), (head_cx + torso_w * 0.4, torso_top + 15 * scale)], fill=outfit_main, width=int(4 * scale))

        elif "kimono" in outfit or "yukata" in outfit:
            # Elegant Kimono
            draw.polygon(
                [
                    (head_cx - torso_w * 0.8, torso_top),
                    (head_cx + torso_w * 0.8, torso_top),
                    (head_cx + torso_w * 1.6, torso_bottom + 40 * scale),
                    (head_cx - torso_w * 1.6, torso_bottom + 40 * scale),
                ],
                fill=outfit_main,
            )
            # Obi Belt
            draw.rectangle(
                [head_cx - torso_w * 0.9, torso_top + 25 * scale, head_cx + torso_w * 0.9, torso_top + 45 * scale],
                fill=outfit_trim,
            )
        else:
            # Casual / Sailor Uniform
            draw.rectangle([head_cx - torso_w * 0.8, torso_top, head_cx + torso_w * 0.8, torso_bottom], fill=outfit_main)
            draw.polygon([
                (head_cx - torso_w * 1.1, torso_bottom),
                (head_cx + torso_w * 1.1, torso_bottom),
                (head_cx + torso_w * 1.5, torso_bottom + 30 * scale),
                (head_cx - torso_w * 1.5, torso_bottom + 30 * scale)
            ], fill=outfit_trim)

        # 3. Neck (connects head to torso)
        neck_w = 8.0 * scale
        neck_top = head_cy + head_r * 0.85
        neck_bottom = torso_top
        # Ensure y0 <= y1 regardless of pose geometry
        ny0 = min(neck_top, neck_bottom)
        ny1 = max(neck_top, neck_bottom)
        if ny1 > ny0:
            draw.rectangle(
                [head_cx - neck_w, ny0, head_cx + neck_w, ny1],
                fill=skin_color,
            )

        # 4. Limbs & Pose
        leg_w = 11.0 * scale
        if pose in ("sitting", "kneeling", "beach"):
            # Sitting with legs forward/curled
            draw.polygon([
                (head_cx - torso_w * 0.7, torso_bottom - 5 * scale),
                (head_cx - torso_w * 1.4, torso_bottom + 25 * scale),
                (head_cx - torso_w * 0.6, torso_bottom + 35 * scale),
                (head_cx - torso_w * 0.1, torso_bottom + 10 * scale),
            ], fill=skin_color)
            draw.polygon([
                (head_cx + torso_w * 0.7, torso_bottom - 5 * scale),
                (head_cx + torso_w * 1.4, torso_bottom + 25 * scale),
                (head_cx + torso_w * 0.6, torso_bottom + 35 * scale),
                (head_cx + torso_w * 0.1, torso_bottom + 10 * scale),
            ], fill=skin_color)
            # Arms resting on ground / knees
            draw.line([(head_cx - torso_w * 0.7, torso_top + 10 * scale), (head_cx - torso_w * 1.1, torso_bottom + 15 * scale)], fill=skin_color, width=int(7 * scale))
            draw.line([(head_cx + torso_w * 0.7, torso_top + 10 * scale), (head_cx + torso_w * 1.1, torso_bottom + 15 * scale)], fill=skin_color, width=int(7 * scale))
        else:
            # Standing legs: two-segment with knee joint
            thigh_len = 38.0 * scale
            shin_len = 30.0 * scale
            # Left leg: thigh
            lx = head_cx - torso_w * 0.55
            draw.rectangle([lx - leg_w * 0.5, torso_bottom, lx + leg_w * 0.5, torso_bottom + thigh_len], fill=skin_color)
            # Left knee bulge
            draw.ellipse([lx - leg_w * 0.65, torso_bottom + thigh_len - 4 * scale, lx + leg_w * 0.65, torso_bottom + thigh_len + 6 * scale], fill=skin_color)
            # Left shin
            draw.rectangle([lx - leg_w * 0.45, torso_bottom + thigh_len + 3 * scale, lx + leg_w * 0.45, torso_bottom + thigh_len + shin_len], fill=skin_color)
            # Left foot
            draw.ellipse([lx - leg_w, torso_bottom + thigh_len + shin_len - 2 * scale, lx + leg_w * 0.5, torso_bottom + thigh_len + shin_len + 8 * scale], fill=skin_color)

            # Right leg: thigh
            rx = head_cx + torso_w * 0.55
            draw.rectangle([rx - leg_w * 0.5, torso_bottom, rx + leg_w * 0.5, torso_bottom + thigh_len], fill=skin_color)
            # Right knee bulge
            draw.ellipse([rx - leg_w * 0.65, torso_bottom + thigh_len - 4 * scale, rx + leg_w * 0.65, torso_bottom + thigh_len + 6 * scale], fill=skin_color)
            # Right shin
            draw.rectangle([rx - leg_w * 0.45, torso_bottom + thigh_len + 3 * scale, rx + leg_w * 0.45, torso_bottom + thigh_len + shin_len], fill=skin_color)
            # Right foot
            draw.ellipse([rx - leg_w * 0.5, torso_bottom + thigh_len + shin_len - 2 * scale, rx + leg_w, torso_bottom + thigh_len + shin_len + 8 * scale], fill=skin_color)

            # Arms
            draw.line([(head_cx - torso_w * 0.7, torso_top + 10 * scale), (head_cx - torso_w * 1.0, torso_bottom + 10 * scale)], fill=skin_color, width=int(6 * scale))
            draw.line([(head_cx + torso_w * 0.7, torso_top + 10 * scale), (head_cx + torso_w * 1.0, torso_bottom + 10 * scale)], fill=skin_color, width=int(6 * scale))

        # 4. Face & Head (Smooth oval skin)
        draw.ellipse(
            [head_cx - head_r, head_cy - head_r, head_cx + head_r, head_cy + head_r],
            fill=skin_color,
        )

        # 5. Eyes, Lashes & Cheerful Blush
        eye_y = head_cy + head_r * 0.12
        eye_offset_x = head_r * 0.38
        ew, eh = 7.0 * scale, 8.0 * scale  # eye width / height

        # Eye shadow / outline ring
        draw.ellipse([head_cx - eye_offset_x - ew - 1, eye_y - eh - 1, head_cx - eye_offset_x + ew + 1, eye_y + eh + 1], fill="#1A1A2E")
        draw.ellipse([head_cx + eye_offset_x - ew - 1, eye_y - eh - 1, head_cx + eye_offset_x + ew + 1, eye_y + eh + 1], fill="#1A1A2E")
        # Iris
        draw.ellipse([head_cx - eye_offset_x - ew, eye_y - eh, head_cx - eye_offset_x + ew, eye_y + eh], fill=eye_color)
        draw.ellipse([head_cx + eye_offset_x - ew, eye_y - eh, head_cx + eye_offset_x + ew, eye_y + eh], fill=eye_color)
        # Pupil
        draw.ellipse([head_cx - eye_offset_x - ew * 0.45, eye_y - eh * 0.45, head_cx - eye_offset_x + ew * 0.45, eye_y + eh * 0.45], fill="#0A0A18")
        draw.ellipse([head_cx + eye_offset_x - ew * 0.45, eye_y - eh * 0.45, head_cx + eye_offset_x + ew * 0.45, eye_y + eh * 0.45], fill="#0A0A18")
        # Eye Highlights (catchlight)
        draw.ellipse([head_cx - eye_offset_x - ew * 0.3, eye_y - eh * 0.55, head_cx - eye_offset_x + ew * 0.3, eye_y - eh * 0.05], fill="#FFFFFF")
        draw.ellipse([head_cx + eye_offset_x - ew * 0.3, eye_y - eh * 0.55, head_cx + eye_offset_x + ew * 0.3, eye_y - eh * 0.05], fill="#FFFFFF")
        # Small secondary highlight
        draw.ellipse([head_cx - eye_offset_x + ew * 0.1, eye_y + eh * 0.1, head_cx - eye_offset_x + ew * 0.45, eye_y + eh * 0.45], fill=(255, 255, 255, 140))
        draw.ellipse([head_cx + eye_offset_x + ew * 0.1, eye_y + eh * 0.1, head_cx + eye_offset_x + ew * 0.45, eye_y + eh * 0.45], fill=(255, 255, 255, 140))

        # Eyebrows (arched, slightly above eyes)
        brow_y = eye_y - eh * 1.6
        draw.arc([head_cx - eye_offset_x - ew * 1.1, brow_y - 4 * scale, head_cx - eye_offset_x + ew * 0.8, brow_y + 5 * scale], start=200, end=340, fill=hair_color, width=int(max(1, 2 * scale)))
        draw.arc([head_cx + eye_offset_x - ew * 0.8, brow_y - 4 * scale, head_cx + eye_offset_x + ew * 1.1, brow_y + 5 * scale], start=200, end=340, fill=hair_color, width=int(max(1, 2 * scale)))

        # Cheerful Soft Blush (semi-transparent)
        draw.ellipse([head_cx - eye_offset_x - 9 * scale, eye_y + eh + 2 * scale, head_cx - eye_offset_x + 5 * scale, eye_y + eh + 12 * scale], fill=(255, 145, 164, 90))
        draw.ellipse([head_cx + eye_offset_x - 5 * scale, eye_y + eh + 2 * scale, head_cx + eye_offset_x + 9 * scale, eye_y + eh + 12 * scale], fill=(255, 145, 164, 90))
        # Cute Mouth (wider smile)
        draw.arc([head_cx - 6 * scale, eye_y + eh + 10 * scale, head_cx + 6 * scale, eye_y + eh + 20 * scale], start=0, end=180, fill="#E63946", width=int(max(1, 2.5 * scale)))

        # 6. Front Bangs & Hair Fringe
        draw.polygon(
            [
                (head_cx - head_r * 1.05, head_cy - head_r * 0.3),
                (head_cx - head_r * 0.5, head_cy + head_r * 0.1),
                (head_cx - head_r * 0.1, head_cy - head_r * 0.2),
                (head_cx + head_r * 0.3, head_cy + head_r * 0.05),
                (head_cx + head_r * 0.7, head_cy - head_r * 0.1),
                (head_cx + head_r * 1.05, head_cy - head_r * 0.3),
                (head_cx + head_r * 0.8, head_cy - head_r * 1.1),
                (head_cx - head_r * 0.8, head_cy - head_r * 1.1),
            ],
            fill=hair_color,
        )

    @staticmethod
    def _draw_cat(draw: ImageDraw.ImageDraw, x: float, y: float, scale: float, color: str, trim: str) -> None:
        """Render a cute cat silhouette with ears, body, and tail."""
        r = 28.0 * scale
        # Body
        draw.ellipse([x - r * 1.3, y - r * 1.4, x + r * 1.3, y + r * 1.4], fill=color)
        # Head
        draw.ellipse([x - r * 0.9, y - r * 2.2, x + r * 0.9, y - r * 0.6], fill=color)
        # Left Ear & Right Ear
        draw.polygon([(x - r * 0.8, y - r * 1.8), (x - r * 1.1, y - r * 2.8), (x - r * 0.3, y - r * 2.2)], fill=color)
        draw.polygon([(x + r * 0.8, y - r * 1.8), (x + r * 1.1, y - r * 2.8), (x + r * 0.3, y - r * 2.2)], fill=color)
        # Tail (curling upwards)
        draw.arc([x + r * 0.8, y - r * 0.5, x + r * 2.2, y + r * 1.2], start=180, end=360, fill=color, width=int(8 * scale))


class ArchitectureRenderer:
    """Renders authentic architectural structures (Torii gates, pagodas, castles, cottages)."""

    @staticmethod
    def draw(canvas: VirtualCanvas, params: dict[str, Any], layer_name: str = "main") -> None:
        """Render architectural structure on designated canvas layer.
        
        Args:
            canvas: VirtualCanvas target.
            params: Dictionary containing architecture parameters.
            layer_name: Target canvas layer (default 'main').
        """
        arch_type = str(params.get("type", "torii_gate")).lower().strip()
        cx = float(params.get("x", canvas.width / 2.0))
        bottom_y = float(params.get("y", canvas.height * 0.82))
        w = float(params.get("width", 360.0))
        h = float(params.get("height", 340.0))
        color_main = str(params.get("color", params.get("color_main", "#D90429")))
        color_roof = str(params.get("color_roof", "#1A1A24"))

        layer = canvas.get_layer(layer_name)
        draw = ImageDraw.Draw(layer)

        if "torii" in arch_type or "gate" in arch_type:
            ArchitectureRenderer._draw_torii_gate(draw, cx, bottom_y, w, h, color_main, color_roof)
        elif "pagoda" in arch_type or "temple" in arch_type:
            ArchitectureRenderer._draw_pagoda(draw, cx, bottom_y, w, h, color_main, color_roof)
        elif "castle" in arch_type:
            ArchitectureRenderer._draw_castle(draw, cx, bottom_y, w, h, color_main, color_roof)
        elif "city" in arch_type or "skyline" in arch_type:
            ArchitectureRenderer._draw_city_skyline(draw, cx, bottom_y, w, h, color_main)
        else:
            # Cozy Cottage default
            ArchitectureRenderer._draw_cottage(draw, cx, bottom_y, w, h, color_main, color_roof)

    @staticmethod
    def _draw_torii_gate(
        draw: ImageDraw.ImageDraw,
        cx: float,
        bottom_y: float,
        w: float,
        h: float,
        color_vermilion: str,
        color_black: str,
    ) -> None:
        """Render an authentic Japanese Shrine Torii Gate (鳥居) with correct architectural parts."""
        top_y = bottom_y - h
        pillar_dist = w * 0.62
        pillar_w = w * 0.085
        left_px = cx - pillar_dist / 2.0
        right_px = cx + pillar_dist / 2.0

        # 1. Twin Upright Pillars (柱 Hashira)
        draw.rectangle([left_px - pillar_w / 2.0, top_y + h * 0.12, left_px + pillar_w / 2.0, bottom_y], fill=color_vermilion)
        draw.rectangle([right_px - pillar_w / 2.0, top_y + h * 0.12, right_px + pillar_w / 2.0, bottom_y], fill=color_vermilion)

        # 2. Black Foundation Stones (龜腹 Kamebara / Base Nemaki)
        nemaki_h = h * 0.12
        draw.rectangle([left_px - pillar_w * 0.7, bottom_y - nemaki_h, left_px + pillar_w * 0.7, bottom_y], fill=color_black)
        draw.rectangle([right_px - pillar_w * 0.7, bottom_y - nemaki_h, right_px + pillar_w * 0.7, bottom_y], fill=color_black)

        # 3. Intermediate Crossbeam (貫 Nuki)
        nuki_y = top_y + h * 0.34
        nuki_h = h * 0.07
        nuki_ext = w * 0.08
        draw.rectangle([left_px - nuki_ext, nuki_y, right_px + nuki_ext, nuki_y + nuki_h], fill=color_vermilion)

        # 4. Central Strut (額束 Gakuzuka)
        gaku_w = w * 0.06
        shimaki_y = top_y + h * 0.12
        draw.rectangle([cx - gaku_w / 2.0, shimaki_y, cx + gaku_w / 2.0, nuki_y], fill=color_vermilion)

        # 5. Lower Top Beam (島木 Shimaki)
        shimaki_w = w * 0.92
        shimaki_h = h * 0.08
        draw.rectangle([cx - shimaki_w / 2.0, shimaki_y, cx + shimaki_w / 2.0, shimaki_y + shimaki_h], fill=color_vermilion)

        # 6. Upper Curved Lintual (笠木 Kasagi - upward curved ends)
        kasagi_w = w * 1.05
        kasagi_h = h * 0.1
        draw.polygon(
            [
                (cx - kasagi_w / 2.0 - w * 0.03, top_y - h * 0.04),
                (cx + kasagi_w / 2.0 + w * 0.03, top_y - h * 0.04),
                (cx + kasagi_w / 2.0, top_y + kasagi_h),
                (cx - kasagi_w / 2.0, top_y + kasagi_h),
            ],
            fill=color_black,
        )

    @staticmethod
    def _draw_pagoda(
        draw: ImageDraw.ImageDraw,
        cx: float,
        bottom_y: float,
        w: float,
        h: float,
        color_body: str,
        color_roof: str,
    ) -> None:
        """Render multi-tiered Japanese Pagoda (五重塔)."""
        tiers = 4
        tier_h = h / (tiers + 1)
        cur_y = bottom_y
        for i in range(tiers):
            tier_w = w * (1.0 - i * 0.18)
            roof_w = tier_w * 1.3
            # Wall Body
            draw.rectangle([cx - tier_w / 2.0, cur_y - tier_h * 0.65, cx + tier_w / 2.0, cur_y], fill=color_body)
            # Overhanging Eaves / Roof
            draw.polygon([
                (cx - roof_w / 2.0, cur_y - tier_h * 0.65),
                (cx, cur_y - tier_h * 1.05),
                (cx + roof_w / 2.0, cur_y - tier_h * 0.65),
            ], fill=color_roof)
            cur_y -= tier_h * 0.95

        # Spire on top (相輪 Sōrin)
        draw.line([(cx, cur_y - tier_h * 0.6), (cx, cur_y - tier_h * 1.8)], fill="#D4AF37", width=4)

    @staticmethod
    def _draw_castle(draw: ImageDraw.ImageDraw, cx: float, bottom_y: float, w: float, h: float, color_main: str, color_roof: str) -> None:
        """Render majestic fantasy/medieval castle silhouette."""
        # Main Keep
        draw.rectangle([cx - w * 0.25, bottom_y - h * 0.7, cx + w * 0.25, bottom_y], fill=color_main)
        # Left Tower & Right Tower
        draw.rectangle([cx - w * 0.45, bottom_y - h * 0.85, cx - w * 0.25, bottom_y], fill=color_main)
        draw.rectangle([cx + w * 0.25, bottom_y - h * 0.85, cx + w * 0.45, bottom_y], fill=color_main)
        # Roof Cones
        draw.polygon([(cx - w * 0.47, bottom_y - h * 0.85), (cx - w * 0.35, bottom_y - h * 1.05), (cx - w * 0.23, bottom_y - h * 0.85)], fill=color_roof)
        draw.polygon([(cx + w * 0.23, bottom_y - h * 0.85), (cx + w * 0.35, bottom_y - h * 1.05), (cx + w * 0.47, bottom_y - h * 0.85)], fill=color_roof)
        draw.polygon([(cx - w * 0.28, bottom_y - h * 0.7), (cx, bottom_y - h * 0.95), (cx + w * 0.28, bottom_y - h * 0.7)], fill=color_roof)

    @staticmethod
    def _draw_cottage(draw: ImageDraw.ImageDraw, cx: float, bottom_y: float, w: float, h: float, color_main: str, color_roof: str) -> None:
        """Render cozy storybook cottage."""
        draw.rectangle([cx - w * 0.35, bottom_y - h * 0.55, cx + w * 0.35, bottom_y], fill=color_main)
        draw.polygon([(cx - w * 0.45, bottom_y - h * 0.55), (cx, bottom_y - h * 0.95), (cx + w * 0.45, bottom_y - h * 0.55)], fill=color_roof)
        # Glowing warm window
        draw.rectangle([cx - w * 0.12, bottom_y - h * 0.38, cx + w * 0.12, bottom_y - h * 0.18], fill="#FFD166")

    @staticmethod
    def _draw_city_skyline(draw: ImageDraw.ImageDraw, cx: float, bottom_y: float, w: float, h: float, color_main: str) -> None:
        """Render glowing cyberpunk or metropolis skyline."""
        bld_count = 6
        step_w = w / bld_count
        for i in range(bld_count):
            bh = h * (0.4 + (math.sin(i * 1.5) * 0.3 + 0.3))
            bx1 = (cx - w / 2.0) + i * step_w
            bx2 = bx1 + step_w * 0.88
            draw.rectangle([bx1, bottom_y - bh, bx2, bottom_y], fill=color_main)
            # Glowing windows
            for wy in range(int(bottom_y - bh + 15), int(bottom_y - 10), 18):
                draw.rectangle([bx1 + 6, wy, bx2 - 6, wy + 8], fill="#00F0FF")


class LandscapeRenderer:
    """Renders natural landscapes (Sakura trees, bamboo groves, ocean waves, mountains, galaxies)."""

    @staticmethod
    def draw(canvas: VirtualCanvas, params: dict[str, Any], layer_name: str = "bg") -> None:
        """Render natural landscape elements."""
        ls_type = str(params.get("type", "cherry_tree")).lower().strip()
        cx = float(params.get("x", canvas.width / 2.0))
        cy = float(params.get("y", canvas.height * 0.7))
        scale = float(params.get("scale", 1.0))
        color = str(params.get("color", "#2B2D42"))
        accent = str(params.get("accent_color", "#FFB7B2"))

        layer = canvas.get_layer(layer_name)
        draw = ImageDraw.Draw(layer)

        if "cherry" in ls_type or "sakura" in ls_type or "tree" in ls_type:
            LandscapeRenderer._draw_cherry_tree(layer, draw, cx, cy, scale, color, accent)
        elif "bamboo" in ls_type:
            LandscapeRenderer._draw_bamboo_grove(draw, cx, cy, scale, color, accent)
        elif "wave" in ls_type or "ocean" in ls_type or "sea" in ls_type:
            LandscapeRenderer._draw_ocean_waves(draw, canvas.width, canvas.height, color, accent)
        elif "mountain" in ls_type:
            LandscapeRenderer._draw_misty_mountains(draw, canvas.width, canvas.height, color, accent)
        elif "galaxy" in ls_type or "starry" in ls_type or "nebula" in ls_type:
            LandscapeRenderer._draw_galaxy_spiral(draw, canvas.width, canvas.height, color, accent)

    @staticmethod
    def _draw_cherry_tree(layer: Image.Image, draw: ImageDraw.ImageDraw, cx: float, cy: float, scale: float, trunk_color: str, petal_color: str) -> None:
        """Render branching sakura tree with tapered trunk and soft blossom clusters."""
        from PIL import ImageFilter as _IF
        from mod.painter.palette import PaletteManager as _PM

        tc = _PM.parse_color(trunk_color)
        trunk_rgba = (tc.r, tc.g, tc.b, 255)

        # --- Tapered trunk: wide at base, narrow at top ---
        # Main trunk as a tapering polygon (trapezoid segments)
        base_x, base_y = cx, cy + 115 * scale
        mid_x, mid_y = cx - 8 * scale, cy + 20 * scale
        top_x, top_y = cx + 6 * scale, cy - 55 * scale
        bw, mw, tw = 14 * scale, 9 * scale, 4 * scale  # half-widths

        draw.polygon([
            (base_x - bw, base_y), (base_x + bw, base_y),
            (mid_x + mw, mid_y), (mid_x - mw, mid_y),
        ], fill=trunk_rgba)
        draw.polygon([
            (mid_x - mw, mid_y), (mid_x + mw, mid_y),
            (top_x + tw, top_y), (top_x - tw, top_y),
        ], fill=trunk_rgba)

        # Major branches (thick)
        draw.line([(cx + 5 * scale, cy - 20 * scale), (cx + 85 * scale, cy - 82 * scale), (cx + 148 * scale, cy - 72 * scale)], fill=trunk_rgba, width=int(8 * scale))
        draw.line([(cx - 10 * scale, cy - 48 * scale), (cx - 95 * scale, cy - 88 * scale), (cx - 138 * scale, cy - 74 * scale)], fill=trunk_rgba, width=int(7 * scale))
        # Sub-branches (thinner)
        draw.line([(cx - 5 * scale, cy - 30 * scale), (cx + 40 * scale, cy - 120 * scale)], fill=trunk_rgba, width=int(4 * scale))
        draw.line([(cx - 15 * scale, cy - 42 * scale), (cx - 55 * scale, cy - 130 * scale)], fill=trunk_rgba, width=int(3 * scale))
        draw.line([(cx + 85 * scale, cy - 82 * scale), (cx + 100 * scale, cy - 140 * scale)], fill=trunk_rgba, width=int(3 * scale))

        # --- Soft blossom clusters via RGBA overlay + blur ---
        pc = _PM.parse_color(petal_color)
        blossom_overlay = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        b_draw = ImageDraw.Draw(blossom_overlay, "RGBA")

        centers = [
            (cx - 118 * scale, cy - 112 * scale, 60 * scale),
            (cx - 38 * scale, cy - 162 * scale, 78 * scale),
            (cx + 62 * scale, cy - 152 * scale, 73 * scale),
            (cx + 142 * scale, cy - 98 * scale, 62 * scale),
            (cx + 2 * scale, cy - 115 * scale, 68 * scale),
            (cx + 45 * scale, cy - 128 * scale, 45 * scale),  # extra cluster
            (cx - 70 * scale, cy - 130 * scale, 48 * scale),  # extra cluster
        ]
        for bx, by, br in centers:
            # Outer soft halo (low alpha)
            b_draw.ellipse([bx - br * 1.2, by - br, bx + br * 1.2, by + br], fill=(pc.r, pc.g, pc.b, 80))
            # Main cluster
            b_draw.ellipse([bx - br, by - br * 0.85, bx + br, by + br * 0.85], fill=(pc.r, pc.g, pc.b, 190))
            # Inner lighter core (soft highlight, NOT pure white)
            lighter_r = min(255, pc.r + 40)
            lighter_g = min(255, pc.g + 25)
            lighter_b = min(255, pc.b + 25)
            b_draw.ellipse([bx - br * 0.55, by - br * 0.45, bx + br * 0.55, by + br * 0.45], fill=(lighter_r, lighter_g, lighter_b, 130))

        # Apply gentle blur for soft fluffy blossom edge
        blossom_overlay = blossom_overlay.filter(_IF.GaussianBlur(radius=max(2.0, 4.5 * scale)))
        layer.alpha_composite(blossom_overlay)

    @staticmethod
    def _draw_bamboo_grove(draw: ImageDraw.ImageDraw, cx: float, cy: float, scale: float, stalk_color: str, leaf_color: str) -> None:
        """Render Japanese bamboo stalks and swaying leaves."""
        stalk_xs = [cx - 120 * scale, cx - 40 * scale, cx + 50 * scale, cx + 130 * scale]
        for sx in stalk_xs:
            w = 12 * scale
            for segment_y in range(int(cy + 150 * scale), int(cy - 250 * scale), int(-50 * scale)):
                draw.rectangle([sx - w / 2.0, segment_y - 45 * scale, sx + w / 2.0, segment_y], fill=stalk_color)
                # Bamboo Node ring
                draw.line([(sx - w * 0.8, segment_y), (sx + w * 0.8, segment_y)], fill="#1B4332", width=3)
                # Bamboo Leaf sprays
                draw.polygon([(sx, segment_y), (sx + 35 * scale, segment_y - 15 * scale), (sx + 45 * scale, segment_y - 5 * scale)], fill=leaf_color)
                draw.polygon([(sx, segment_y), (sx - 35 * scale, segment_y - 20 * scale), (sx - 45 * scale, segment_y - 10 * scale)], fill=leaf_color)

    @staticmethod
    def _draw_ocean_waves(draw: ImageDraw.ImageDraw, w: int, h: int, wave_color: str, foam_color: str) -> None:
        """Render Great Wave / Hokusai style layered sea waves."""
        base_y = h * 0.68
        for layer_idx, alpha_color in enumerate([wave_color, "#1D3557", "#457B9D"]):
            pts = [(0, h)]
            for step_x in range(0, w + 40, 30):
                wave_y = base_y + layer_idx * 35 + math.sin(step_x * 0.02 + layer_idx) * 25
                pts.append((int(step_x), int(wave_y)))
            pts.append((w, h))
            draw.polygon(pts, fill=alpha_color)
            # White crest foam lines
            for i in range(1, len(pts) - 2):
                draw.line([pts[i], (pts[i][0] + 15, pts[i][1] - 8)], fill=foam_color, width=3)

    @staticmethod
    def _draw_misty_mountains(draw: ImageDraw.ImageDraw, w: int, h: int, deep_color: str, mist_color: str) -> None:
        """Render 3-layer misty mountain ridges with atmospheric perspective depth."""
        from PIL import ImageFilter as _IF, Image as _Img
        from mod.painter.palette import PaletteManager as _PM

        # Parse base color and derive lighter/mistier versions for distant ridges
        dc = _PM.parse_color(deep_color)
        mc = _PM.parse_color(mist_color)

        def _brighten(c: tuple[int, int, int], t: float) -> tuple[int, int, int]:
            """Blend color toward mist color at ratio t."""
            r = int(c[0] * (1 - t) + mc.r * t)
            g = int(c[1] * (1 - t) + mc.g * t)
            b = int(c[2] * (1 - t) + mc.b * t)
            return (min(255, r + int(t * 35)), min(255, g + int(t * 35)), min(255, b + int(t * 30)))

        layer_defs = [
            # (base_y_ratio, peaks, distance_ratio, blur_radius, alpha)
            (h * 0.65, 6, 0.0, 0.0, 255),    # foreground — darkest, sharpest
            (h * 0.52, 5, 0.45, 1.2, 230),   # mid ridge
            (h * 0.40, 4, 0.75, 2.5, 195),   # far ridge — lightest, most blurred
        ]

        # Get parent layer size from draw object
        canvas_size = draw._image.size  # type: ignore[attr-defined]

        for base_y, peaks, dist_t, blur_r, alpha in layer_defs:
            col_rgb = _brighten((dc.r, dc.g, dc.b), dist_t)
            col_rgba = (*col_rgb, alpha)

            mtn_overlay = _Img.new("RGBA", canvas_size, (0, 0, 0, 0))
            m_draw = ImageDraw.Draw(mtn_overlay, "RGBA")

            pts = [(0, h)]
            step = w / peaks
            for i in range(peaks + 1):
                px = i * step
                py = base_y + math.sin(i * 2.3 + dist_t * 5) * (55 - dist_t * 15)
                pts.append((int(px), int(py)))
            pts.append((w, h))
            m_draw.polygon(pts, fill=col_rgba)

            if blur_r > 0:
                mtn_overlay = mtn_overlay.filter(_IF.GaussianBlur(radius=blur_r))

            draw._image.alpha_composite(mtn_overlay)  # type: ignore[attr-defined]

    @staticmethod
    def _draw_galaxy_spiral(draw: ImageDraw.ImageDraw, w: int, h: int, core_color: str, arm_color: str) -> None:
        """Render glowing celestial nebula & galaxy dust with fine stars and color falloff."""
        from mod.painter.palette import PaletteManager as _PM
        cx, cy = w * 0.5, h * 0.35

        cc = _PM.parse_color(core_color)
        ac = _PM.parse_color(arm_color)

        # Core glow (soft ellipse)
        draw.ellipse([cx - 70, cy - 42, cx + 70, cy + 42], fill=(cc.r, cc.g, cc.b, 210))
        draw.ellipse([cx - 38, cy - 22, cx + 38, cy + 22], fill=(min(255, cc.r + 40), min(255, cc.g + 40), min(255, cc.b + 40), 230))

        # Spiral arms: fine stars with distance-based color falloff
        max_r = 280.0
        for theta_deg in range(0, 720, 6):
            rad = math.radians(theta_deg)
            r = theta_deg * 0.39
            if r > max_r:
                break
            gx = cx + r * math.cos(rad)
            gy = cy + r * math.sin(rad) * 0.52
            # Fade from warm white near core to arm_color at edge
            t = min(1.0, r / max_r)
            sr = int((255) * (1 - t) + ac.r * t)
            sg = int((248) * (1 - t) + ac.g * t)
            sb = int((220) * (1 - t) + ac.b * t)
            # Smaller fine stars
            star_r = random.uniform(0.4, 2.2) * (1.0 - t * 0.4)
            star_alpha = int(210 * (1.0 - t * 0.35))
            draw.ellipse([gx - star_r, gy - star_r, gx + star_r, gy + star_r], fill=(sr, sg, sb, star_alpha))


class WeatherFxRenderer:
    """Renders atmospheric weather and particle FX (sakura petals, fireflies, sunbeams, shooting stars)."""

    @staticmethod
    def draw(canvas: VirtualCanvas, params: dict[str, Any], layer_name: str = "fx") -> None:
        """Render atmospheric FX onto fx or fg layer."""
        fx_type = str(params.get("type", "sakura_blizzard")).lower().strip()
        count = int(params.get("count", 35))
        color = str(params.get("color", "#FFB7B2"))

        layer = canvas.get_layer(layer_name)
        draw = ImageDraw.Draw(layer)

        w, h = canvas.width, canvas.height

        if "sakura" in fx_type or "petal" in fx_type or "flower" in fx_type:
            for _ in range(count):
                px = random.uniform(20, w - 20)
                py = random.uniform(20, h - 20)
                size = random.uniform(6.0, 14.0)
                angle = random.uniform(0, math.pi)
                # Curved petal shape
                draw.polygon([
                    (px, py),
                    (px + size * math.cos(angle), py + size * math.sin(angle)),
                    (px + size * 1.3 * math.cos(angle + 0.4), py + size * 1.3 * math.sin(angle + 0.4)),
                    (px + size * 0.7 * math.cos(angle + 0.8), py + size * 0.7 * math.sin(angle + 0.8)),
                ], fill=color)

        elif "star" in fx_type or "shooting" in fx_type or "meteor" in fx_type:
            # Diagonal shooting star trails
            for _ in range(max(3, count // 6)):
                sx = random.uniform(100, w - 100)
                sy = random.uniform(40, h * 0.45)
                length = random.uniform(60, 140)
                draw.line([(sx, sy), (sx - length, sy + length * 0.6)], fill="#FFFFFF", width=3)
                draw.ellipse([sx - 4, sy - 4, sx + 4, sy + 4], fill="#FFE66D")

        elif "fireflies" in fx_type or "bokeh" in fx_type or "sparkle" in fx_type:
            # Dual-layer glow orbs with soft outer halo
            from mod.painter.palette import PaletteManager as _PM
            pc = _PM.parse_color(color)
            glow_overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            g_draw = ImageDraw.Draw(glow_overlay, "RGBA")
            for _ in range(count):
                fx_x = random.uniform(30, w - 30)
                fy = random.uniform(h * 0.28, h - 30)
                core_r = random.uniform(2.0, 5.0)
                halo_r = core_r * random.uniform(2.8, 4.5)
                # Outer halo (very soft, low alpha)
                g_draw.ellipse([fx_x - halo_r, fy - halo_r, fx_x + halo_r, fy + halo_r],
                               fill=(pc.r, pc.g, pc.b, random.randint(20, 45)))
                # Mid glow ring
                mid_r = core_r * 1.6
                g_draw.ellipse([fx_x - mid_r, fy - mid_r, fx_x + mid_r, fy + mid_r],
                               fill=(pc.r, pc.g, pc.b, random.randint(55, 90)))
                # Bright core
                g_draw.ellipse([fx_x - core_r, fy - core_r, fx_x + core_r, fy + core_r],
                               fill=(min(255, pc.r + 60), min(255, pc.g + 60), min(255, pc.b + 40), random.randint(180, 230)))
            # Blur the whole overlay for overall softness
            glow_overlay = glow_overlay.filter(ImageFilter.GaussianBlur(radius=3.5))
            layer.alpha_composite(glow_overlay)

        elif "sunbeams" in fx_type or "god_rays" in fx_type or "light" in fx_type:
            # Atmospheric god rays via RGBA overlay + GaussianBlur (no more opaque polygons)
            beam_overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            b_draw = ImageDraw.Draw(beam_overlay, "RGBA")
            source_x = w * random.uniform(0.72, 0.92)
            source_y = 0.0
            beam_count = random.randint(5, 8)
            for i in range(beam_count):
                angle = 1.85 + i * (0.65 / beam_count)
                target_x = source_x + math.cos(angle) * w * 1.35
                target_y = source_y + math.sin(angle) * h * 1.35
                half_w_near = random.uniform(8, 18)
                half_w_far = half_w_near * random.uniform(2.5, 4.5)
                beam_alpha = random.randint(28, 52)
                b_draw.polygon([
                    (source_x - half_w_near, source_y),
                    (source_x + half_w_near, source_y),
                    (target_x + half_w_far, target_y),
                    (target_x - half_w_far, target_y),
                ], fill=(255, 248, 210, beam_alpha))
            beam_overlay = beam_overlay.filter(ImageFilter.GaussianBlur(radius=12.0))
            layer.alpha_composite(beam_overlay)


class ObjectRenderer:
    """Renders artistic scene props (lanterns, paper boats, coffee cups, flower bouquets)."""

    @staticmethod
    def draw(canvas: VirtualCanvas, params: dict[str, Any], layer_name: str = "main") -> None:
        """Render individual prop objects on canvas."""
        obj_type = str(params.get("type", "lantern")).lower().strip()
        x = float(params.get("x", canvas.width * 0.5))
        y = float(params.get("y", canvas.height * 0.5))
        scale = float(params.get("scale", 1.0))
        color = str(params.get("color", "#E63946"))

        layer = canvas.get_layer(layer_name)
        draw = ImageDraw.Draw(layer)

        if "lantern" in obj_type:
            # Japanese Festival Paper Lantern (提燈)
            r = 25.0 * scale
            draw.ellipse([x - r, y - r * 1.3, x + r, y + r * 1.3], fill=color)
            draw.rectangle([x - r * 0.6, y - r * 1.4, x + r * 0.6, y - r * 1.2], fill="#1D1D1D")
            draw.rectangle([x - r * 0.6, y + r * 1.2, x + r * 0.6, y + r * 1.4], fill="#1D1D1D")
            # Glowing core
            draw.ellipse([x - r * 0.4, y - r * 0.6, x + r * 0.4, y + r * 0.6], fill="#FFE66D")
        elif "boat" in obj_type:
            # Paper boat / wooden skiff
            w = 60 * scale
            draw.polygon([(x - w, y), (x + w, y), (x + w * 0.6, y + 20 * scale), (x - w * 0.6, y + 20 * scale)], fill=color)
            draw.polygon([(x, y), (x, y - 45 * scale), (x + 35 * scale, y - 10 * scale)], fill="#FFFFFF")
