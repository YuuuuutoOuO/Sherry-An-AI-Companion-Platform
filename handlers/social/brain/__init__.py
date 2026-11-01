"""handlers/social/brain/__init__.py
==================================
賽博群友大腦（內容生成層）。

這一層只回答一個問題：**現在該用什麼身分、什麼意圖、看著什麼上下文來說話？**

對外主要入口：
- ``BrainIntent``：REPLY / JOIN / INITIATE / MONOLOGUE
- ``ConversationContext``：結構化對話上下文（含回覆關係與「在跟誰說話」）
- ``build_conversation_context``：從 Discord 頻道歷史建上下文
- ``think``：統一生成入口，回傳 ``BrainResult``

設計原則：
1. 正式群與沙盒共用；沙盒既有的 daily_vibe / care_poke / mind_monologue 不動。
2. 生成失敗一律回傳 silent，不補任何罐頭台詞。
3. 動態上下文放在 user_prompt，system_prompt 只放人格與意圖規則，保護 KV cache。
"""

from __future__ import annotations

from .context import (
    ContextMessage,
    ConversationContext,
    build_conversation_context,
)
from .engine import BrainResult, think
from .intents import BrainIntent, decide_intent
from .prompts import build_brain_prompt

__all__ = [
    "BrainIntent",
    "BrainResult",
    "ContextMessage",
    "ConversationContext",
    "build_brain_prompt",
    "build_conversation_context",
    "decide_intent",
    "think",
]
