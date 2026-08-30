"""
core/registry.py
================
雪莉全域能力註冊表 (CapabilityRegistry)。

實踐雙層能力隔離（精選日常 Handler 工具 vs 專題 Mod 外掛）：
- AI 日常對話規劃器僅能看到標記為 is_ai_callable=True 的精選工具。
- 專題 Mod 插件維持獨立運行，並在合約層保留隨時一鍵開放 AI 調用之相容性。
"""

from __future__ import annotations

import logging
from typing import Any

from core.contracts import CapabilityDescriptor

logger = logging.getLogger(__name__)


class CapabilityRegistry:
    """全域能力註冊中樞。"""

    def __init__(self) -> None:
        self._capabilities: dict[str, CapabilityDescriptor] = {}
        self._alias_map: dict[str, str] = {}

    def register(self, descriptor: CapabilityDescriptor) -> None:
        """註冊一項新能力（Handler 工具或 Mod 插件）。

        Args:
            descriptor: CapabilityDescriptor 能力描述子。
        """
        canonical_name = descriptor.name.upper()
        self._capabilities[canonical_name] = descriptor

        # 建立別名映射
        for alias in descriptor.aliases:
            self._alias_map[alias.upper()] = canonical_name

        logger.debug(
            "CapabilityRegistry: 註冊能力 [%s] (Category: %s, AI-Callable: %s)",
            canonical_name,
            descriptor.category,
            descriptor.is_ai_callable,
        )

    def unregister(self, name: str) -> None:
        """移除已註冊之能力。

        Args:
            name: 能力名稱或別名。
        """
        canonical = self._alias_map.get(name.upper(), name.upper())
        if canonical in self._capabilities:
            desc = self._capabilities.pop(canonical)
            for alias in desc.aliases:
                self._alias_map.pop(alias.upper(), None)

    def get_capability(self, name: str) -> CapabilityDescriptor | None:
        """根據名稱或別名查詢能力描述子。

        Args:
            name: 能力名稱（如 'STOCK', 'SEARCH', 'dnd' 等）。

        Returns:
            CapabilityDescriptor | None: 找到之描述子或 None。
        """
        canonical = self._alias_map.get(name.upper(), name.upper())
        return self._capabilities.get(canonical)

    def get_ai_callable_tools(self) -> list[CapabilityDescriptor]:
        """取得供 AI 日常對話 (Parallel / ReAct Planner) 調用之精選工具清單。

        嚴格限制於 is_ai_callable=True 的工具，防止工具超載與幻覺。

        Returns:
            list[CapabilityDescriptor]: 精選 AI 工具清單。
        """
        return [
            cap for cap in self._capabilities.values()
            if cap.is_ai_callable
        ]

    def get_dedicated_mods(self) -> list[CapabilityDescriptor]:
        """取得獨立專題 Mod 外掛清單。

        Returns:
            list[CapabilityDescriptor]: 專題外掛清單。
        """
        return [
            cap for cap in self._capabilities.values()
            if cap.category == "mod_plugin"
        ]

    def list_all(self) -> list[CapabilityDescriptor]:
        """列出所有已註冊之能力。

        Returns:
            list[CapabilityDescriptor]: 全體能力列表。
        """
        return list(self._capabilities.values())
