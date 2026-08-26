"""
engine (mod/deep_search)
========================
深度研究業務邏輯核心。
包含研究提示詞庫與自主多輪推演引擎。
遵循 AGENTS.md：MUST NOT import discord / sqlite3 / requests。
"""

from __future__ import annotations

from mod.deep_search.engine.deep_search_engine import DeepSearchEngine

__all__ = ["DeepSearchEngine"]
