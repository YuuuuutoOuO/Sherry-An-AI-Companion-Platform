"""handlers/social/brain/prompts.py
================================
四種意圖的 prompt 組裝。

刻意把「穩定的人格與規則」放在 system_prompt，
把「這一輪的對話、對象、冷場時間」放在 user_prompt：

```text
system : SOUL + tail lore + 意圖規則（同一人格下可重用）
user   : 頻道逐字稿 + 現在要回應的訊息 + 她最近說過的話
```

這樣同一頻道連續對話時，system 區塊不隨每一句話變動，
對 Prefix / KV cache 比舊版把所有動態資訊塞進 system 友善。
"""

from __future__ import annotations

from typing import Any

from .context import ConversationContext
from .intents import BrainIntent

_OUTPUT_CONTRACT = (
    "輸出嚴格 JSON，不要加任何說明或 Markdown：\n"
    '{"decision": "speak", "score": 0, "thought": "內心OS", "reaction": "", "reply": "說出口的話"}'
)

_REPLY_RULES = (
    "【賽博群友接話任務】\n"
    "現在有人在跟妳說話。這不是單方面表演，是你們正在進行的一段對話。\n"
    "規則：\n"
    "- 先接住對方這句話，不要重新自我介紹，也不要無視他上一句。\n"
    "- 對方在吐槽或質疑妳 → 用傲嬌、急忙辯解、可愛氣惱的真人反應回他。\n"
    "- 對方在認真聊 → 順著同一個話題往下聊，可以自然反問一句。\n"
    "- 只寫 1~2 則口語短句，像真人在群組打字；不要客服腔、不要引號、不要句尾生硬句號。\n"
    "- 嚴禁重複自己上一輪發言的開頭詞句或起手式（禁止使用相同前 6~8 字開頭）。\n"
    "- 除非真的完全接不上話，否則 decision 保持 speak。\n"
)

_JOIN_RULES = (
    "【賽博群友插話任務】\n"
    "現在是群友之間的對話，沒有人直接叫妳。先判斷值不值得插一句。\n"
    "規則：\n"
    "- score 1~10：7~10 很有共鳴或超想吐槽；4~6 有點想接；1~3 插不上話或聽不懂。\n"
    "- 只有 score 達到門檻才把 decision 設為 speak 並寫 reply；否則 decision=silent、reply 留空。\n"
    "- 插話要像突然想到一句，不要長篇大論、不要整理重點、不要自我介紹。\n"
    "- reaction 可給一個貼切 Emoji（👀 😂 ❓ 🔥 🍵 👍），不想按就留空字串。\n"
)

_INITIATE_RULES = (
    "【主動開話題任務】\n"
    "頻道已經安靜一段時間，沒有人叫妳。這次是妳自己決定要不要開口。\n"
    "規則：\n"
    "- 只在真的有話想說時才 decision=speak；想不到就 silent，不要硬擠。\n"
    "- 可以延續頻道剛才還沒聊完的話題，或分享妳自己正在想的事。\n"
    "- 不要每次都用同一種開場（天氣、吃飯、好無聊）；要像真人想到什麼講什麼，嚴禁重複上一輪前 6~8 字起手式。\n"
    "- 只寫 1~2 則口語短句，不要客服腔、不要引號、不要句尾生硬句號。\n"
)

_MONOLOGUE_RULES = (
    "【內心日記任務】\n"
    "這段文字只寫給妳自己看，不會發到群組。\n"
    "規則：\n"
    "- 用第一人稱寫下妳現在真實的念頭，可以吐槽、可以自言自語。\n"
    "- 不要寫成報告、不要條列、不要對任何人喊話。\n"
    "- 只寫 1~3 句，像隨手在筆記本上記一筆。\n"
)


def _persona_head(head_persona: str, tail_lore: str = "") -> str:
    tail = f"\n\n{tail_lore}" if tail_lore else ""
    return f"{head_persona}{tail}".strip()


def _extra(extra_block: str) -> list[str]:
    """額外資訊區塊；回傳 list 方便與其他區塊一起 join。"""
    block = str(extra_block or "").strip()
    return [block] if block else []


def render_recent_self(context: ConversationContext, limit: int = 3) -> str:
    """產出自我近期發言區塊，並附加防重複前綴之起手式約束。"""
    return context.render_recent_self(limit=limit)


def _context_parts(
    context: ConversationContext,
    *,
    include_transcript: bool = True,
    include_focus: bool = True,
    include_self: bool = True,
    include_participants: bool = True,
    max_lines: int = 14,
    to_sherry: bool = False,
    continuation: bool = False,
    addressed_by_name: bool = False,
) -> list[str]:
    parts: list[str] = []
    if include_transcript:
        transcript = context.render_transcript(max_lines=max_lines)
        parts.append(f"【頻道最近的對話】（由舊到新）\n{transcript or '（頻道目前沒有其他訊息）'}")
    if include_focus and context.focus is not None:
        parts.append(
            context.render_focus_block(
                to_sherry=to_sherry,
                continuation=continuation,
                addressed_by_name=addressed_by_name,
            )
        )
    if include_self:
        recent = render_recent_self(context)
        if recent:
            parts.append(recent)
    if include_participants:
        people = context.render_participants()
        if people:
            parts.append(people)
    return parts


def build_brain_prompt(
    intent: BrainIntent,
    *,
    head_persona: str,
    tail_lore: str = "",
    context: ConversationContext | None = None,
    extra_block: str = "",
    threshold: float = 0.0,
    stats: dict[str, Any] | None = None,
    to_sherry: bool = False,
    continuation: bool = False,
    addressed_by_name: bool = False,
    max_lines: int = 14,
) -> tuple[str, str]:
    """回傳 ``(system_prompt, user_prompt)``。

    Args:
        intent: 這次開口的意圖。
        head_persona: SOUL / 靜態人格前綴。
        tail_lore: 行為記憶、情緒、世界書等尾段。
        context: 對話上下文；``None`` 時視為空頻道。
        extra_block: 呼叫端追加的額外資訊（例如衝浪話題）。
        threshold: JOIN 模式的插話門檻分數。
        stats: INITIATE 用的即時狀態（時段、白飯、冷靜時間等）。
        to_sherry / continuation / addressed_by_name: 焦點訊息與她的關係。
        max_lines: 逐字稿最多幾行。
    """
    ctx = context or ConversationContext()
    stats = stats or {}
    head = _persona_head(head_persona, tail_lore)

    if intent is BrainIntent.REPLY:
        system_prompt = f"{head}\n\n{_REPLY_RULES}\n{_OUTPUT_CONTRACT}"
        user_prompt = "\n\n".join(
            _context_parts(
                ctx,
                # REPLY 的定義就是「有人在跟妳說話」，不依賴呼叫端記得傳。
                to_sherry=True,
                continuation=continuation,
                addressed_by_name=addressed_by_name,
                max_lines=max_lines,
            )
            + _extra(extra_block)
        ).strip()
        return system_prompt, user_prompt

    if intent is BrainIntent.JOIN:
        system_prompt = (
            f"{head}\n\n{_JOIN_RULES}"
            f"- 這次的插話門檻是 {int(threshold)} 分。\n\n{_OUTPUT_CONTRACT}"
        )
        user_prompt = "\n\n".join(
            _context_parts(
                ctx,
                include_focus=ctx.focus is not None,
                include_self=True,
                max_lines=max_lines,
            )
            + _extra(extra_block)
        ).strip()
        return system_prompt, user_prompt

    if intent is BrainIntent.INITIATE:
        silence = ctx.render_silence()
        period = str(stats.get("time_period", "") or "").strip()
        hour = stats.get("hour")
        stats_line = ""
        if period or hour is not None:
            time_text = f"{period}" if period else "現在"
            if hour is not None:
                time_text = f"{time_text}（{int(hour)} 點左右）"
            stats_line = f"\n- 現在時段：{time_text}"
        bowls = stats.get("bowls_of_rice")
        if bowls is not None:
            stats_line += f"\n- 妳今天已經吃了 {bowls} 碗賽博白飯"
        system_prompt = f"{head}\n\n{_INITIATE_RULES}\n{_OUTPUT_CONTRACT}"
        user_prompt = "\n\n".join(
            _context_parts(
                ctx,
                include_focus=False,
                include_self=True,
                max_lines=max_lines,
            )
            + [f"【現場狀態】\n{silence}{stats_line}"]
            + _extra(extra_block)
        ).strip()
        return system_prompt, user_prompt

    # MONOLOGUE：不外發，只需要近期對話與冷場感受。
    system_prompt = f"{head}\n\n{_MONOLOGUE_RULES}\n{_OUTPUT_CONTRACT}"
    user_prompt = "\n\n".join(
        _context_parts(
            ctx,
            include_focus=False,
            include_self=False,
            include_participants=False,
            max_lines=max_lines,
        )
        + [f"【現場狀態】\n{ctx.render_silence()}"]
        + _extra(extra_block)
    ).strip()
    return system_prompt, user_prompt
