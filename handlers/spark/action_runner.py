"""handlers/spark/action_runner.py - 向後相容轉導模組。

為遵循 AGENTS.md §0 分層規範，
實際的 Discord Action 執行器已統整於 Presentation 層 handlers/spark/cog.py。
此模組保留供歷史引用與單元測試轉導使用。
"""

from __future__ import annotations

from .cog import SparkActionRunner, SparkChannelContext

__all__ = ["SparkActionRunner", "SparkChannelContext"]
