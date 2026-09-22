"""
handlers/social/dispatch/base.py
================================
調度引擎策略抽象基底類別（BaseDispatchStrategy）。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, cast

import discord
from discord.ext import commands

from handlers.social.tool_base import BaseTool

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class BaseDispatchStrategy(ABC):
    """調度策略基底抽象類別。

    所有調度模式（INTEGRATED、PARALLEL、REACT）均繼承此類別，透過 self._agent 存取共用基礎設施。
    """

    def __init__(self, agent: Any) -> None:
        """初始化策略並繫結 DispatchAgentTool (Coordinator)。

        Args:
            agent: DispatchAgentTool 實例。
        """
        self._agent = agent

    @property
    def _tools(self) -> dict[str, BaseTool]:
        """可用工具字典。"""
        return cast(dict[str, BaseTool], self._agent._tools)

    @property
    @abstractmethod
    def mode_name(self) -> str:
        """回傳當前策略模式名稱。"""
        raise NotImplementedError

    def _build_tool_descriptions(self, *, include_chat: bool = False) -> str:
        """委託 Coordinator 組裝工具描述清單。"""
        return cast(str, self._agent._build_tool_descriptions(include_chat=include_chat))

    @abstractmethod
    async def execute(
        self,
        brain: Any,
        bot: commands.Bot,
        message: discord.Message,
        user_query: str,
        vibe_snippet: str,
        full_context: str,
        images: list[str] | None,
        start_time: float,
        visual_context: str = "",
    ) -> None:
        """執行該調度策略的主流程。"""
        raise NotImplementedError
