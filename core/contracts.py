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
from typing import Any, Awaitable, Callable, Literal


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
