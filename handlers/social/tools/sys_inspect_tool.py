"""handlers.social.tools.sys_inspect_tool
=========================================
系統環境與硬體監控工具 (SYSTEM_INSPECT)。
支援自主調用以獲取主機 CPU/RAM/GPU 負載與雪莉運行狀態。
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from core.sys_inspector.service import SysInspectorService
from handlers.social.contracts import ToolResult
from handlers.social.tool_base import BaseTool
from handlers.social.utils import is_admin_user

logger = logging.getLogger(__name__)


class SystemInspectTool(BaseTool):
    """查詢主機硬體負載與雪莉內部運行狀態工具。"""

    @property
    def intent_name(self) -> str:
        return "SYSTEM_INSPECT"

    @property
    def llm_description(self) -> str:
        return "查詢主機硬體負載 (CPU/RAM/GPU/VRAM)、雪莉運行狀態或系統順暢度"

    async def execute(
        self,
        bot: commands.Bot,
        message: discord.Message,
        user_query: str,
        vibe_snippet: str,
        full_context: str,
        **kwargs: object,
    ) -> ToolResult:
        """執行系統快照採樣並回傳高密度觀察文本。"""
        is_admin = is_admin_user(message.author)
        report = await SysInspectorService.get_compact_report(is_admin=is_admin)
        logger.info("SystemInspectTool: 產生系統狀態快照 (is_admin=%s)", is_admin)
        return ToolResult.success(report)
