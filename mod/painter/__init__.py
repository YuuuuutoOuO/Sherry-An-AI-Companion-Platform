"""
mod.painter
===========
Sherry Digital Canvas & AI Painter Module.
"""

from __future__ import annotations

from mod.painter.models import (
    ArtStyle,
    BrushConfig,
    BrushType,
    DrawingCommand,
    PaintingMetadata,
    RGBAColor,
    RGBColor,
    ShapeType,
)
from mod.painter.palette import PaletteManager
from mod.painter.brushes import BaseBrush, BrushEngine
from mod.painter.canvas import VirtualCanvas
from mod.painter.interpreter import CommandInterpreter
from mod.painter.agent import PainterAgent, get_live_painting_status

MOD_INFO = {
    "id": "painter",
    "name": "畫畫工作室",
    "icon": "🎨",
    "description": "雪莉數位畫布繪圖引擎、多筆刷調色盤、Gemma 多模態臨摹與歷史畫廊！",
    "category": "創作 / Creativity",
    "version": "1.0.0",
    "author": "Sherry Team",
    "has_web_ui": True,
    "nav_id": "painter",
    "nav_title": "畫畫工作室",
    "nav_icon": "🎨",
    "view_title": "🎨 雪莉藝術畫布工作室 (Mod)",
    "admin_only": False,
    "order": 45,
    "script_file": "painter.js",
}

__all__ = [
    "MOD_INFO",
    "ArtStyle",
    "BrushConfig",
    "BrushType",
    "DrawingCommand",
    "PaintingMetadata",
    "RGBAColor",
    "RGBColor",
    "ShapeType",
    "PaletteManager",
    "BaseBrush",
    "BrushEngine",
    "VirtualCanvas",
    "CommandInterpreter",
    "PainterAgent",
    "get_live_painting_status",
]
