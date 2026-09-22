"""
tests/unit/test_painter_interpreter.py
======================================
Unit tests for CommandInterpreter.
"""

from __future__ import annotations


from mod.painter.canvas import VirtualCanvas
from mod.painter.interpreter import CommandInterpreter


def test_interpreter_executes_all_commands() -> None:
    """Test executing standard commands batch."""
    canvas = VirtualCanvas(width=400, height=400)
    interpreter = CommandInterpreter(canvas)

    commands = [
        {"action": "set_background", "params": {"color": "#FAFAFA", "texture": "plain"}},
        {"action": "wash", "params": {"color": "#336699", "opacity": 0.2, "direction": "vertical"}},
        {"action": "shape", "params": {"shape_type": "circle", "coords": [200, 200, 40], "fill_color": "#FF0000"}},
        {"action": "stroke", "params": {"brush": "ink_wash", "size": 12, "color": "#000000", "points": [[50, 50], [150, 150]]}},
        {"action": "splatter", "params": {"x": 200, "y": 200, "radius": 25, "count": 10, "color": "#111111"}},
        {"action": "stamp", "params": {"x": 300, "y": 300, "text": "雪莉", "size": 36}},
        {"action": "unknown_action", "params": {}},  # gracefully ignored
    ]

    steps_called: list[tuple[int, int]] = []

    def on_step(curr: int, total: int) -> None:
        steps_called.append((curr, total))

    success = interpreter.execute_batch(commands, on_step=on_step)
    assert success == 6
    assert len(steps_called) == len(commands)
    assert len(interpreter.executed_commands) == 6
