"""handlers/social/autonomy/actions/__init__.py
============================================
Social Core 的 action 集合。

Phase 2 先把最常用的兩個 action（lurk_chat / daily_vibe）從 ``surfer.py``
抽出來；其餘模式暫時保留在 surfer，逐步遷移。
"""

from __future__ import annotations

from . import prompts
from .daily_vibe import ActionPrompt, DailyVibeAction
from .lurk_chat import LurkChatAction

__all__ = [
    "ActionPrompt",
    "DailyVibeAction",
    "LurkChatAction",
    "prompts",
]
