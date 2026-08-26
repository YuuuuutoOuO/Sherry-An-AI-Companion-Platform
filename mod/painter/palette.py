"""
mod/painter/palette.py
======================
Color palette manager, color conversions, and style-specific harmonic palettes.
"""

from __future__ import annotations

import re
from typing import Mapping

from mod.painter.models import ArtStyle, RGBColor, RGBAColor


class PaletteManager:
    """Provides color parsing, blending, and predefined style palettes."""

    # 國風古典水墨色譜
    TRADITIONAL_INK: list[RGBColor] = [
        RGBColor(15, 15, 15),       # 玄黑 (濃墨)
        RGBColor(45, 45, 48),       # 焦墨
        RGBColor(90, 92, 95),       # 重墨
        RGBColor(145, 148, 152),    # 淡墨
        RGBColor(200, 202, 205),    # 清墨 (水氣)
        RGBColor(188, 45, 40),      # 硃砂 / 丹紅 (印章/紅梅)
        RGBColor(38, 70, 83),       # 黛藍
        RGBColor(42, 110, 85),      # 石綠 / 松柏綠
        RGBColor(218, 165, 32),     # 藤黃
        RGBColor(139, 69, 19),      # 赭石
        RGBColor(248, 245, 236),    # 宣紙米白 (傳統底色)
    ]

    # 清新水彩色譜
    WATERCOLOR_PASTEL: list[RGBColor] = [
        RGBColor(255, 183, 197),    # 櫻粉
        RGBColor(255, 154, 162),    # 珊瑚淡紅
        RGBColor(254, 219, 166),    # 暖杏黃
        RGBColor(218, 247, 166),    # 薄荷嫩綠
        RGBColor(162, 213, 242),    # 澄澈天藍
        RGBColor(189, 178, 255),    # 薰衣草紫
        RGBColor(255, 255, 252),    # 水彩畫紙白
        RGBColor(92, 118, 141),     # 陰影灰藍
    ]

    # 賽博龐克霓虹色譜
    CYBER_NEON: list[RGBColor] = [
        RGBColor(0, 245, 255),      # 電光青 (Cyan)
        RGBColor(255, 0, 127),      # 螢光桃紅 (Magenta)
        RGBColor(191, 0, 255),      # 霓虹電紫 (Electric Purple)
        RGBColor(57, 255, 20),      # 激光螢綠 (Neon Green)
        RGBColor(255, 255, 0),      # 亮霓黃 (Bright Yellow)
        RGBColor(10, 10, 20),       # 賽博極夜黑 (Dark Canvas)
        RGBColor(20, 25, 45),       # 網格暗藍
        RGBColor(255, 255, 255),    # 高光白 (Pure White)
    ]

    # 復古點陣像素 16 色 (PICO-8 inspired)
    PIXEL_RETRO: list[RGBColor] = [
        RGBColor(0, 0, 0),          # 黑色
        RGBColor(29, 43, 83),       # 暗藍
        RGBColor(126, 37, 83),      # 暗紫
        RGBColor(0, 135, 81),       # 暗綠
        RGBColor(171, 82, 54),      # 棕褐
        RGBColor(95, 87, 79),       # 深灰
        RGBColor(194, 195, 199),    # 淺灰
        RGBColor(255, 241, 232),    # 象牙白
        RGBColor(255, 0, 77),       # 亮紅
        RGBColor(255, 163, 0),      # 鮮橙
        RGBColor(255, 236, 39),     # 亮黃
        RGBColor(0, 228, 54),       # 鮮綠
        RGBColor(41, 173, 255),     # 天藍
        RGBColor(131, 118, 156),    # 靛藍
        RGBColor(255, 119, 168),    # 桃粉
        RGBColor(255, 204, 170),    # 膚色
    ]

    # 溫馨手繪蠟筆色譜
    WARM_CRAYON: list[RGBColor] = [
        RGBColor(230, 57, 70),      # 太陽紅
        RGBColor(244, 162, 97),     # 暖橙
        RGBColor(233, 196, 106),    # 奶油黃
        RGBColor(42, 157, 143),     # 森林綠
        RGBColor(38, 70, 83),       # 海洋藍
        RGBColor(155, 93, 229),     # 夢幻紫
        RGBColor(108, 88, 76),      # 樹幹棕
        RGBColor(250, 243, 224),    # 畫冊暖白
    ]

    # 厚塗油畫色譜
    OIL_IMPASTO: list[RGBColor] = [
        RGBColor(20, 30, 60),       # 普魯士藍
        RGBColor(180, 40, 30),      # 鎘紅
        RGBColor(230, 180, 30),     # 鉻黃
        RGBColor(40, 80, 50),       # 銘綠
        RGBColor(120, 70, 40),      # 熟赭
        RGBColor(240, 235, 220),    # 鈦白
        RGBColor(20, 20, 20),       # 象牙黑
        RGBColor(160, 140, 120),    # 亞麻灰調
    ]

    # 名稱對應表
    NAMED_COLORS: Mapping[str, RGBColor] = {
        "black": RGBColor(0, 0, 0),
        "white": RGBColor(255, 255, 255),
        "red": RGBColor(255, 0, 0),
        "green": RGBColor(0, 255, 0),
        "blue": RGBColor(0, 0, 255),
        "yellow": RGBColor(255, 255, 0),
        "cyan": RGBColor(0, 255, 255),
        "magenta": RGBColor(255, 0, 255),
        "gray": RGBColor(128, 128, 128),
        "orange": RGBColor(255, 165, 0),
        "pink": RGBColor(255, 192, 203),
        "purple": RGBColor(128, 0, 128),
        "brown": RGBColor(165, 42, 42),
        "gold": RGBColor(255, 215, 0),
        "navy": RGBColor(0, 0, 128),
        "rice_paper": RGBColor(248, 245, 236),
        "seal_red": RGBColor(188, 45, 40),
    }

    @classmethod
    def parse_color(cls, value: str | tuple[int, ...] | list[int] | RGBColor | RGBAColor, alpha: float = 1.0) -> RGBAColor:
        """Parse various color formats into a unified RGBAColor.
        
        Args:
            value: Hex string ("#FF5500"), color name ("red"), tuple/list (r, g, b), or Color object.
            alpha: Fallback alpha transparency (0.0 to 1.0).
            
        Returns:
            Standardized RGBAColor object.
        """
        if isinstance(value, RGBAColor):
            return value
        if isinstance(value, RGBColor):
            return value.with_alpha(alpha)
            
        if isinstance(value, (tuple, list)):
            if len(value) >= 4:
                a_val = float(value[3])
                if a_val > 1.0:
                    a_val /= 255.0
                return RGBAColor(int(value[0]), int(value[1]), int(value[2]), a_val)
            if len(value) == 3:
                return RGBAColor(int(value[0]), int(value[1]), int(value[2]), alpha)

        if isinstance(value, str):
            val_str = value.strip().lower()
            # 1. Check named colors
            if val_str in cls.NAMED_COLORS:
                return cls.NAMED_COLORS[val_str].with_alpha(alpha)
            
            # 2. Check Hex format: #RGB, #RRGGBB, #RRGGBBAA
            if val_str.startswith("#"):
                val_str = val_str[1:]
            
            hex_match = re.match(r"^[0-9a-f]{3,8}$", val_str)
            if hex_match:
                if len(val_str) == 3:
                    r = int(val_str[0] * 2, 16)
                    g = int(val_str[1] * 2, 16)
                    b = int(val_str[2] * 2, 16)
                    return RGBAColor(r, g, b, alpha)
                if len(val_str) == 6:
                    r = int(val_str[0:2], 16)
                    g = int(val_str[2:4], 16)
                    b = int(val_str[4:6], 16)
                    return RGBAColor(r, g, b, alpha)
                if len(val_str) == 8:
                    r = int(val_str[0:2], 16)
                    g = int(val_str[2:4], 16)
                    b = int(val_str[4:6], 16)
                    a = int(val_str[6:8], 16) / 255.0
                    return RGBAColor(r, g, b, a)

        # Fallback to pure black
        return RGBAColor(0, 0, 0, alpha)

    @classmethod
    def get_palette_for_style(cls, style: ArtStyle) -> list[RGBColor]:
        """Get recommended harmonic color palette for a given art style.
        
        Args:
            style: The art style enum.
            
        Returns:
            List of RGBColor objects.
        """
        if style == ArtStyle.INK_WASH:
            return cls.TRADITIONAL_INK
        if style == ArtStyle.WATERCOLOR:
            return cls.WATERCOLOR_PASTEL
        if style == ArtStyle.NEON:
            return cls.CYBER_NEON
        if style == ArtStyle.PIXEL_ART:
            return cls.PIXEL_RETRO
        if style == ArtStyle.CRAYON or style == ArtStyle.DOODLE:
            return cls.WARM_CRAYON
        if style == ArtStyle.OIL:
            return cls.OIL_IMPASTO
        return cls.WATERCOLOR_PASTEL + cls.TRADITIONAL_INK[:4]

    @classmethod
    def blend(cls, c1: RGBAColor, c2: RGBAColor, ratio: float) -> RGBAColor:
        """Linear blend between two colors (ratio 0.0 -> c1, 1.0 -> c2).
        
        Args:
            c1: Base color.
            c2: Target color.
            ratio: Blend weight between 0.0 and 1.0.
            
        Returns:
            Blended RGBAColor.
        """
        t = max(0.0, min(1.0, float(ratio)))
        r = int(c1.r * (1 - t) + c2.r * t)
        g = int(c1.g * (1 - t) + c2.g * t)
        b = int(c1.b * (1 - t) + c2.b * t)
        a = c1.a * (1 - t) + c2.a * t
        return RGBAColor(r, g, b, a)
