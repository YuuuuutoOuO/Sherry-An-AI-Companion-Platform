"""handlers/social/brain/engine.py
================================
統一的生成入口與輸出契約。

所有「雪莉自己想說話」的路徑都走 ``think``：

```python
result = await think(bot, intent=BrainIntent.REPLY, context=ctx, head_persona=head)
if result.should_speak:
    await send(result.reply)
```

好處是：
- 四種意圖共用同一份解析與沉默規則。
- 生成失敗、空回覆、分數不足都只會得到 ``decision="silent"``。
- 呼叫端不需要再各自處理 JSON、引號、罐頭台詞。
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from .context import ConversationContext
from .intents import BrainIntent
from .parsing import extract_reply, parse_json_reply
from .prompts import build_brain_prompt

logger = logging.getLogger(__name__)

_SPEAK_DECISIONS = frozenset({"speak", "say", "reply", "join", "yes", "true"})


def clean_reply(text: str) -> str:
    """移除模型偷加的外層引號、Markdown fence 與生硬句尾。"""
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned).strip()
    cleaned = re.sub(r"<thought>.*?</thought>", "", cleaned, flags=re.DOTALL).strip()
    while True:
        previous = cleaned
        cleaned = re.sub(r'^[「"\'“‘]+|[」"\'”’]+$', "", cleaned).strip()
        if cleaned.endswith("。"):
            cleaned = cleaned[:-1].strip()
        if cleaned == previous:
            break
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


@dataclass(frozen=True)
class BrainResult:
    """一次生成的結果；失敗時一定長成 silent。"""

    intent: BrainIntent
    decision: str = "silent"
    reply: str = ""
    thought: str = ""
    reaction: str = ""
    score: float = 0.0
    raw: str = ""
    model: str = ""
    latency_ms: int = 0

    @property
    def is_silent(self) -> bool:
        return not self.should_speak

    @property
    def should_speak(self) -> bool:
        """是否應該把 ``reply`` 發到頻道（日記永遠不外發）。"""
        if not self.intent.is_public:
            return False
        if self.decision == "silent":
            return False
        return bool(self.reply.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.value,
            "decision": self.decision,
            "reply": self.reply,
            "thought": self.thought,
            "reaction": self.reaction,
            "score": self.score,
            "latency_ms": self.latency_ms,
            "model": self.model,
        }


def _normalize_decision(
    intent: BrainIntent,
    *,
    raw_decision: str,
    reply: str,
    score: float,
    threshold: float,
) -> str:
    """把模型的 decision 收斂成 speak / silent。"""
    decision = str(raw_decision or "").strip().lower()

    if not reply.strip():
        return "silent"

    if intent is BrainIntent.REPLY:
        # 有人在跟她說話：只要生成出內容就回，避免模型太客氣而冷場。
        return "speak"

    if intent is BrainIntent.JOIN:
        # 插話必須同時「有意願」且「分數過門檻」。
        willing = decision in _SPEAK_DECISIONS or not decision
        if not willing:
            return "silent"
        return "speak" if score >= float(threshold) else "silent"

    if intent is BrainIntent.MONOLOGUE:
        return "speak"

    # INITIATE：尊重模型自己判斷，只有在明確說 silent 時才閉嘴。
    return "silent" if decision == "silent" else "speak"


async def think(
    bot: Any,
    *,
    intent: BrainIntent,
    context: ConversationContext | None = None,
    head_persona: str,
    tail_lore: str = "",
    extra_block: str = "",
    threshold: float = 0.0,
    stats: dict[str, Any] | None = None,
    to_sherry: bool = False,
    continuation: bool = False,
    addressed_by_name: bool = False,
    max_lines: int = 14,
    model: str | None = None,
    max_tokens: int = 300,
    timeout: float = 25.0,
    priority: int | None = None,
    task_id: str = "",
) -> BrainResult:
    """依意圖組 prompt、呼叫模型、解析成 ``BrainResult``。

    生成失敗時回傳 ``BrainResult(decision="silent")``，絕不補罐頭台詞。
    """
    started = time.time()
    ctx = context or ConversationContext()
    # 延遲載入：autonomy 套件會連帶載入 care，而 care 又依賴 brain。
    from handlers.social.autonomy import generation as _generation

    system_prompt, user_prompt = build_brain_prompt(
        intent,
        head_persona=head_persona,
        tail_lore=tail_lore,
        context=ctx,
        extra_block=extra_block,
        threshold=threshold,
        stats=stats,
        to_sherry=to_sherry,
        continuation=continuation,
        addressed_by_name=addressed_by_name,
        max_lines=max_lines,
    )

    raw = await _generation.generate_text(
        bot,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=model,
        max_tokens=max_tokens,
        timeout=timeout,
        priority=priority,
        task_id=task_id or f"brain-{intent.value}-{int(time.time() * 1000)}",
    )
    latency_ms = int((time.time() - started) * 1000)

    if not raw:
        return BrainResult(
            intent=intent,
            decision="silent",
            model=str(model or ""),
            latency_ms=latency_ms,
        )

    parsed = parse_json_reply(raw)
    thought, extracted = extract_reply(parsed)
    reply = clean_reply(extracted)
    thought = thought or str(parsed.get("thought", "") or "").strip()
    reaction = str(parsed.get("reaction", "") or "").strip()

    if not reply and not parsed and intent is BrainIntent.REPLY:
        # 模型偶爾直接回一段純文字；接話情境可以寬鬆接受。
        reply = clean_reply(raw)

    try:
        score = float(parsed.get("score", 0) or 0)
    except (TypeError, ValueError):
        score = 0.0

    decision = _normalize_decision(
        intent,
        raw_decision=str(parsed.get("decision", "") or ""),
        reply=reply,
        score=score,
        threshold=threshold,
    )

    return BrainResult(
        intent=intent,
        decision=decision,
        reply=reply if decision != "silent" else "",
        thought=thought,
        reaction=reaction,
        score=score,
        raw=raw,
        model=str(model or ""),
        latency_ms=latency_ms,
    )
