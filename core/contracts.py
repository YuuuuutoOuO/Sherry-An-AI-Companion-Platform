"""
core/contracts.py
=================
雪莉微核心 (SherryCore) 之標準資料合約與抽象協定。

定義系統流轉之標準請求 (SherryRequest)、標準回應 (SherryResponse)、
全域能力描述子 (CapabilityDescriptor) 與即時心智快照 (MindSnapshot)。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, Protocol, runtime_checkable


@dataclass(frozen=True)
class ThinkingStep:
    """單一思考推演或工具執行步驟。"""

    step_type: Literal["plan", "action", "observation", "persona_thought", "synthesis"]
    title: str
    content: str
    latency_ms: int = 0
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class SherryRequest:
    """進入雪莉核心的統一標準請求物件。"""

    trace_id: str
    source_type: Literal[
        "chat_message",
        "slash_command",
        "context_menu",
        "component_interaction",
        "modal_submit",
        "system_heartbeat",
        "scheduler",
    ]
    user_id: int
    user_name: str
    guild_id: int
    channel_id: int
    raw_content: str
    options: dict[str, Any] = field(default_factory=dict)
    attachments: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    message_ref: Any | None = None


import discord


@dataclass
class SherryResponse:
    """雪莉核心產出的統一標準回應物件。"""

    trace_id: str
    reply_text: str
    embeds: list[discord.Embed] = field(default_factory=list)
    view: discord.ui.View | None = None
    files: list[discord.File] = field(default_factory=list)
    capabilities_used: list[str] = field(default_factory=list)
    thinking_steps: list[ThinkingStep] = field(default_factory=list)
    thought_log: str = ""
    latency_ms: int = 0
    success: bool = True
    error_message: str = ""


@dataclass
class CapabilityDescriptor:
    """全域能力描述子 (支援日常精選與外掛雙層隔離)。"""

    name: str
    category: Literal["core_builtin", "mod_plugin"]
    description: str
    aliases: list[str] = field(default_factory=list)
    is_ai_callable: bool = True
    requires_gpu: bool = False
    execute_handler: Callable[..., Awaitable[Any]] | None = None


@dataclass(frozen=True)
class MindSnapshot:
    """雪莉當前的即時心智快照 (記憶體駐留，用於自我感知與狀態呈現)。"""

    status: Literal["idle", "thinking", "generating_art", "busy"]
    current_task_count: int
    recent_actions: list[str]
    current_mood: str
    system_health_score: float
    uptime_seconds: int


@runtime_checkable
class CloudInferenceProtocol(Protocol):
    """雲端推論客戶端抽象協定 (Protocol)。

    定義微核心 (SherryCore) 與系統稽核所依賴之雲端模型多模態分析介面，
    實現乾淨架構 (Clean Architecture)，解除 core 對外部 mod.clients 之反向依賴。
    """

    async def summarize_text(
        self,
        text: str = "",
        image_bytes: bytes = b"",
        mime_type: str = "image/png",
        image_url: str = "",
    ) -> str:
        """全能多模態摘要：自動識別純文字、網址/YouTube 與圖片，產出結構化繁中摘要。"""
        ...

    async def translate_text(self, text: str, target_lang: str = "繁體中文") -> str:
        """將目標文字訊息翻譯為指定語言（預設繁體中文）。"""
        ...

    async def analyze_image(
        self,
        image_bytes: bytes = b"",
        mime_type: str = "image/png",
        user_prompt: str = "",
        image_url: str = "",
    ) -> str:
        """對圖片進行多模態視覺辨識、程式報錯截圖診斷與圖表分析。"""
        ...

    async def summarize_url(self, raw_content: str) -> str:
        """從文字中萃取 URL，並依類型進行內容深度摘要。"""
        ...

    async def extend_ask(
        self,
        target_text: str,
        question: str = "",
        context_history: str = "",
        user_name: str = "",
    ) -> str:
        """針對指定訊息進行延伸追問。"""
        ...


