"""
mod/painter/models.py
=====================
Data models, types, and enumerations for the Virtual Canvas & AI Painter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class BrushType(str, Enum):
    """Supported digital brush types."""
    PENCIL = "pencil"          # 細緻顆粒感鉛筆 / 素描排線
    INK_WASH = "ink_wash"      # 水墨毛筆：壓感濃淡、飛白、墨水渲染
    WATERCOLOR = "watercolor"  # 水彩筆：濕畫擴散、半透明疊色與水痕邊緣
    OIL = "oil"                # 厚塗油畫：厚重不透明、筆觸覆蓋
    CRAYON = "crayon"          # 蠟筆 / 粉筆：粗糙紙質顆粒
    AIRBRUSH = "airbrush"      # 噴槍：柔和高斯霧化光暈
    MARKER = "marker"          # 麥克筆：平塗鮮豔、半透明標記
    PIXEL = "pixel"            # 像素筆：網格對齊無反鋸齒
    ERASER = "eraser"          # 橡皮擦：清除塗層


class ArtStyle(str, Enum):
    """Artistic styles supported by the AI Painter."""
    INK_WASH = "ink_wash"      # 傳統水墨國風
    WATERCOLOR = "watercolor"  # 清新通透水彩
    DOODLE = "doodle"          # 可愛簡筆塗鴉
    PIXEL_ART = "pixel_art"    # 復古點陣像素
    NEON = "neon"              # 賽博龐克霓虹
    CRAYON = "crayon"          # 童趣蠟筆粉筆
    OIL = "oil"                # 經典厚塗油畫
    FREESTYLE = "freestyle"    # 隨機自由創作

    @classmethod
    def from_str(cls, value: str) -> ArtStyle:
        """Parse style string into ArtStyle enum with fuzzy alias matching.
        
        Args:
            value: The input style string in English or Traditional Chinese.
            
        Returns:
            Matched ArtStyle enum or FREESTYLE as fallback.
        """
        clean = (value or "").strip().lower()
        alias_map = {
            "ink": cls.INK_WASH,
            "ink_wash": cls.INK_WASH,
            "水墨": cls.INK_WASH,
            "水墨畫": cls.INK_WASH,
            "國畫": cls.INK_WASH,
            "watercolor": cls.WATERCOLOR,
            "水彩": cls.WATERCOLOR,
            "水彩畫": cls.WATERCOLOR,
            "doodle": cls.DOODLE,
            "塗鴉": cls.DOODLE,
            "簡筆": cls.DOODLE,
            "簡筆畫": cls.DOODLE,
            "pixel": cls.PIXEL_ART,
            "pixel_art": cls.PIXEL_ART,
            "像素": cls.PIXEL_ART,
            "像素藝術": cls.PIXEL_ART,
            "點陣": cls.PIXEL_ART,
            "neon": cls.NEON,
            "cyber": cls.NEON,
            "cyberpunk": cls.NEON,
            "霓虹": cls.NEON,
            "賽博霓虹": cls.NEON,
            "crayon": cls.CRAYON,
            "chalk": cls.CRAYON,
            "蠟筆": cls.CRAYON,
            "粉筆": cls.CRAYON,
            "oil": cls.OIL,
            "oil_painting": cls.OIL,
            "油畫": cls.OIL,
            "厚塗": cls.OIL,
            "freestyle": cls.FREESTYLE,
            "隨機": cls.FREESTYLE,
            "自由": cls.FREESTYLE,
            "隨便": cls.FREESTYLE,
            "auto": cls.FREESTYLE,
        }
        return alias_map.get(clean, cls.FREESTYLE)


class ShapeType(str, Enum):
    """Geometric and organic shapes for vector canvas operations."""
    RECT = "rect"
    CIRCLE = "circle"
    ELLIPSE = "ellipse"
    POLYGON = "polygon"
    LINE = "line"
    CURVE = "curve"
    CLOUD = "cloud"
    MOUNTAIN = "mountain"
    STAR = "star"
    MOON = "moon"


@dataclass(frozen=True)
class RGBColor:
    """Represents a standard 24-bit RGB color.
    
    Attributes:
        r: Red component (0-255).
        g: Green component (0-255).
        b: Blue component (0-255).
    """
    r: int
    g: int
    b: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "r", max(0, min(255, int(self.r))))
        object.__setattr__(self, "g", max(0, min(255, int(self.g))))
        object.__setattr__(self, "b", max(0, min(255, int(self.b))))

    @property
    def hex_code(self) -> str:
        """Return #RRGGBB hex string."""
        return f"#{self.r:02x}{self.g:02x}{self.b:02x}"

    @property
    def tuple_rgb(self) -> tuple[int, int, int]:
        """Return (R, G, B) tuple."""
        return (self.r, self.g, self.b)

    def with_alpha(self, alpha: float = 1.0) -> RGBAColor:
        """Create RGBAColor with specified alpha (0.0 - 1.0)."""
        return RGBAColor(r=self.r, g=self.g, b=self.b, a=alpha)


@dataclass(frozen=True)
class RGBAColor:
    """Represents an RGBA color with alpha transparency.
    
    Attributes:
        r: Red component (0-255).
        g: Green component (0-255).
        b: Blue component (0-255).
        a: Alpha transparency (0.0 to 1.0).
    """
    r: int
    g: int
    b: int
    a: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "r", max(0, min(255, int(self.r))))
        object.__setattr__(self, "g", max(0, min(255, int(self.g))))
        object.__setattr__(self, "b", max(0, min(255, int(self.b))))
        object.__setattr__(self, "a", max(0.0, min(1.0, float(self.a))))

    @property
    def alpha_int(self) -> int:
        """Return alpha as integer in range 0-255."""
        return int(self.a * 255)

    @property
    def tuple_rgba(self) -> tuple[int, int, int, int]:
        """Return (R, G, B, A) tuple."""
        return (self.r, self.g, self.b, self.alpha_int)

    @property
    def tuple_rgb(self) -> tuple[int, int, int]:
        """Return (R, G, B) tuple."""
        return (self.r, self.g, self.b)


@dataclass(frozen=True)
class BrushConfig:
    """Configuration for an active brush."""
    type: BrushType = BrushType.PENCIL
    size: float = 5.0
    color: RGBAColor = field(default_factory=lambda: RGBAColor(0, 0, 0, 1.0))
    hardness: float = 0.8
    jitter: float = 0.0


@dataclass(frozen=True)
class DrawingCommand:
    """Single declarative drawing command emitted by LLM or user."""
    action: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PaintingMetadata:
    """Metadata record for a saved painting."""
    painting_id: str
    prompt: str
    style: str
    author_id: str
    author_name: str
    created_at: str
    command_count: int
    filename: str
    duration_sec: float = 0.0
    aspect_ratio: str = "1:1"
    width: int = 800
    height: int = 800
    tags: list[str] = field(default_factory=list)
