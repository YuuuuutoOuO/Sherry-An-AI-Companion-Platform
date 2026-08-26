"""
mod/painter/interpreter.py
==========================
Safely parses and executes drawing commands onto a VirtualCanvas instance.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Sequence

from mod.painter.canvas import VirtualCanvas
from mod.painter.models import ArtStyle, BrushType, DrawingCommand, ShapeType

logger = logging.getLogger(__name__)


class CommandInterpreter:
    """Interprets structured drawing commands and applies them to a VirtualCanvas."""

    def __init__(self, canvas: VirtualCanvas) -> None:
        self.canvas = canvas
        self.executed_commands: list[DrawingCommand] = []

    def execute_single(self, cmd: DrawingCommand | dict[str, Any]) -> bool:
        """Execute a single declarative command.
        
        Args:
            cmd: DrawingCommand object or command dict.
            
        Returns:
            True if executed successfully, False otherwise.
        """
        action = cmd.action if isinstance(cmd, DrawingCommand) else str(cmd.get("action", "")).lower().strip()
        params = cmd.params if isinstance(cmd, DrawingCommand) else (cmd.get("params") or cmd)

        try:
            if action in ("set_background", "background", "bg"):
                color = params.get("color", "#FFFFFF")
                texture = params.get("texture", "plain")
                self.canvas.set_background(color, texture=texture)

            elif action in ("stroke", "draw_stroke", "brush_stroke", "line_stroke"):
                points_raw = params.get("points") or params.get("path") or []
                points: list[tuple[float, float]] = []
                for pt in points_raw:
                    if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                        points.append((float(pt[0]), float(pt[1])))
                if not points:
                    return False

                brush = params.get("brush") or params.get("brush_type") or BrushType.PENCIL
                size = float(params.get("size") or params.get("thickness") or 6.0)
                color = params.get("color") or "#000000"
                opacity = float(params.get("opacity") or params.get("alpha") or 1.0)
                layer = str(params.get("layer") or "main")

                self.canvas.draw_stroke(
                    points=points,
                    brush_type=brush,
                    size=size,
                    color=color,
                    opacity=opacity,
                    layer_name=layer,
                )

            elif action in ("shape", "draw_shape", "fill_shape"):
                shape_type = params.get("shape_type") or params.get("type") or ShapeType.CIRCLE
                coords = params.get("coords") or params.get("points") or [0, 0, 10]
                fill_color = params.get("fill_color") or params.get("fill")
                outline_color = params.get("outline_color") or params.get("outline") or params.get("stroke")
                outline_width = int(params.get("outline_width") or params.get("width") or 2)
                layer = str(params.get("layer") or "main")

                self.canvas.draw_shape(
                    shape_type=shape_type,
                    coords=coords,
                    fill_color=fill_color,
                    outline_color=outline_color,
                    outline_width=outline_width,
                    layer_name=layer,
                )

            elif action in ("wash", "draw_wash", "atmosphere"):
                color = params.get("color") or "#888888"
                opacity = float(params.get("opacity") or 0.25)
                direction = str(params.get("direction") or "vertical")
                layer = str(params.get("layer") or "main")
                self.canvas.draw_wash(color, opacity=opacity, direction=direction, layer_name=layer)

            elif action in ("splatter", "draw_splatter", "ink_splash", "drops"):
                x = float(params.get("x", self.canvas.width / 2.0))
                y = float(params.get("y", self.canvas.height / 2.0))
                radius = float(params.get("radius", 30.0))
                count = int(params.get("count", 15))
                color = params.get("color", "#000000")
                opacity = float(params.get("opacity", 0.8))
                layer = str(params.get("layer", "main"))

                self.canvas.draw_splatter(
                    x=x, y=y, radius=radius, count=count, color=color, opacity=opacity, layer_name=layer
                )

            elif action in ("stamp", "draw_stamp", "seal", "signature"):
                x = float(params.get("x", self.canvas.width - 80))
                y = float(params.get("y", self.canvas.height - 80))
                text = str(params.get("text", "雪莉"))
                size = int(params.get("size", 42))
                layer = str(params.get("layer", "fg"))

                self.canvas.draw_seal_stamp(x=x, y=y, text=text, size=size, layer_name=layer)

            # ── High-Level Semantic DSL Actions ───────────────────────────
            elif action in ("draw_character", "character", "draw_figure", "figure", "char"):
                from mod.painter.primitives import CharacterRenderer
                layer = str(params.get("layer", "main"))
                CharacterRenderer.draw(self.canvas, params, layer_name=layer)

            elif action in ("draw_architecture", "architecture", "building", "structure", "draw_building"):
                from mod.painter.primitives import ArchitectureRenderer
                layer = str(params.get("layer", "main"))
                ArchitectureRenderer.draw(self.canvas, params, layer_name=layer)

            elif action in ("draw_landscape", "landscape", "nature", "draw_nature", "scenery"):
                from mod.painter.primitives import LandscapeRenderer
                layer = str(params.get("layer", "bg"))
                LandscapeRenderer.draw(self.canvas, params, layer_name=layer)

            elif action in ("draw_weather_fx", "weather_fx", "fx", "particles", "weather"):
                from mod.painter.primitives import WeatherFxRenderer
                layer = str(params.get("layer", "fx"))
                WeatherFxRenderer.draw(self.canvas, params, layer_name=layer)

            elif action in ("draw_object", "object", "prop", "draw_prop"):
                from mod.painter.primitives import ObjectRenderer
                layer = str(params.get("layer", "main"))
                ObjectRenderer.draw(self.canvas, params, layer_name=layer)

            else:
                logger.warning("CommandInterpreter: Unknown action '%s' ignored.", action)
                return False

            if isinstance(cmd, DrawingCommand):
                self.executed_commands.append(cmd)
            else:
                self.executed_commands.append(DrawingCommand(action=action, params=params))
            return True

        except Exception as exc:
            logger.error("CommandInterpreter error executing '%s': %s", action, exc)
            return False

    def execute_batch(
        self,
        commands: Sequence[DrawingCommand | dict[str, Any]],
        on_step: Callable[[int, int], None] | None = None,
    ) -> int:
        """Execute a sequence of commands in order.
        
        Args:
            commands: Sequence of drawing commands.
            on_step: Optional callback(current_idx, total_count).
            
        Returns:
            Number of successfully executed commands.
        """
        success_count = 0
        total = len(commands)
        for idx, cmd in enumerate(commands):
            if self.execute_single(cmd):
                success_count += 1
            if on_step:
                on_step(idx + 1, total)
        return success_count
