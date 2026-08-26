"""
handlers/social/contracts.py
============================
核心工具標準合約與型別定義。
規範工具執行結果、執行狀態與類別，確保 Agent 調度器與工具之間具備明確強型別合約。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ToolExecutionStatus(str, Enum):
    """工具執行狀態代碼。"""

    SUCCESS = "SUCCESS"
    ERROR = "ERROR"
    NO_DATA = "NO_DATA"


class ToolCategory(str, Enum):
    """工具類別劃分。

    DIRECT_UI:
        自帶 Discord 渲染（Embed / 圖片 / 音樂語音）的互動服務工具。
        若使用者無額外聊天/心得提問，執行完成後可觸發對話抑制以避免重複發言。
    DATA_PROVIDER:
        純資料/API 查詢工具，本身不發送最終回覆，僅提供結構化摘要供 Agent 進行性格轉述。
    """

    DIRECT_UI = "DIRECT_UI"
    DATA_PROVIDER = "DATA_PROVIDER"
    DATA_ONLY = "DATA_ONLY"


@dataclass(frozen=True)
class ToolResult:
    """核心工具標準執行成果合約。

    Attributes:
        status: 執行狀態 (SUCCESS, ERROR, NO_DATA)
        is_direct_rendered: True 代表工具已向 Discord 發送完整的 UI/圖片/訊息，Agent 應避免重複合成回覆
        summary_text: 供 Agent 對話合成或記憶記錄使用的文字摘要
        extra_data: 額外的結構化資料字典
    """

    status: ToolExecutionStatus = ToolExecutionStatus.SUCCESS
    is_direct_rendered: bool = False
    summary_text: str = ""
    extra_data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(
        cls,
        summary_text: str = "",
        *,
        is_direct_rendered: bool = False,
        extra_data: dict[str, Any] | None = None,
    ) -> ToolResult:
        """建立成功結果。"""
        return cls(
            status=ToolExecutionStatus.SUCCESS,
            is_direct_rendered=is_direct_rendered,
            summary_text=summary_text,
            extra_data=extra_data or {},
        )

    @classmethod
    def error(
        cls,
        error_msg: str,
        *,
        is_direct_rendered: bool = False,
        extra_data: dict[str, Any] | None = None,
    ) -> ToolResult:
        """建立失敗結果。"""
        return cls(
            status=ToolExecutionStatus.ERROR,
            is_direct_rendered=is_direct_rendered,
            summary_text=error_msg,
            extra_data=extra_data or {},
        )

    @classmethod
    def no_data(
        cls,
        message: str = "未找到相關數據。",
        *,
        is_direct_rendered: bool = False,
        extra_data: dict[str, Any] | None = None,
    ) -> ToolResult:
        """建立無數據結果。"""
        return cls(
            status=ToolExecutionStatus.NO_DATA,
            is_direct_rendered=is_direct_rendered,
            summary_text=message,
            extra_data=extra_data or {},
        )
