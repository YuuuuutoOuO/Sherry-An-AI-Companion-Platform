"""
handlers.roast
==============
Core Roast / Tsukkomi subsystem for Sherry.

Provides keyword-triggered intelligent roasting directly in Discord channels
and Web UI configuration capabilities.
"""

from __future__ import annotations

from handlers.roast.cog import RoastCog, setup
from handlers.roast.manager import RoastManager, roast_manager
from handlers.roast.models import RoastConfig, RoastRule

__all__ = [
    "RoastCog",
    "RoastConfig",
    "RoastManager",
    "RoastRule",
    "roast_manager",
    "setup",
]
