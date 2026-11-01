from __future__ import annotations

import re
import time
from typing import Any, Iterable

from .contracts import ChatEvent

# ----------------------------------------------------------------------
# 去 AI 味：移植自 dafeiyu face_convert，並改為繁中口語
# ----------------------------------------------------------------------
_HEDGE_RE = re.compile(
    r"[^，。！？!?；;、：:~\s“”\"‘’（）()【】\[\]]{0,6}"
    r"(?:隱約(?:聽見|聽到|看到|飄來|飄進)|隐约(?:听见|听到|看到|飘来|飘进))"
    r"[^，。！？!?；;、：:~\s“”\"‘’（）()【】\[\]]{0,20}[，、：:]?"
)

_META_BASE = (
    r"PROTOCOL_ENFORCEMENT|必須生成|必須輸出|系統等待|系統自檢|System\s*:"
    r"|作為一個\s*AI|身為一個?\s*AI|我是(?:一個)?(?:語言模型|AI)|"
    r"以下是(?:我的)?(?:回覆|回答)|希望這(?:個|些)回答|如果你(?:還)?有(?:其他)?問題"
)
_META_RE = re.compile(_META_BASE, re.IGNORECASE)

_SHELL_BLOCK = re.compile(
    r"\[(?:\s*\{[\"']type[\"']\s*:\s*[\"']text[\"']\s*,\s*[\"']text[\"']\s*:"
    r"\s*[\"'](?:[^\"'\\]|\\.)*[\"']\s*\}\s*,?)*\s*\]"
)
_SHELL_TEXT = re.compile(r"[\"']text[\"']\s*:\s*[\"']((?:[^\"'\\]|\\.)*)[\"']")

_PROTECT_INLINE = re.compile(
    r"`[^`\n]+`|https?://\S+|“[^”\n]{1,300}”|「[^」\n]{1,300}」|【[^】\n]{1,160}】"
)

_THOUGHT_RE = re.compile(r"<thought>.*?</thought>", re.DOTALL | re.IGNORECASE)
_THOUGHT_OPEN_RE = re.compile(r"<thought>.*$", re.DOTALL | re.IGNORECASE)


def _protect_inline(text: str) -> tuple[str, list[str]]:
    saved: list[str] = []

    def repl(match: re.Match[str]) -> str:
        saved.append(match.group(0))
        return f"\x00{len(saved) - 1}\x00"

    return _PROTECT_INLINE.sub(repl, text), saved


def _restore_inline(text: str, saved: list[str]) -> str:
    def repl(match: re.Match[str]) -> str:
        idx = int(match.group(1))
        return saved[idx] if 0 <= idx < len(saved) else ""

    return re.sub(r"\x00(\d+)\x00", repl, text)


def _drop_meta_sentences(text: str) -> str:
    parts = re.split(r"(?<=[。！？!?…])", text)
    kept = [part for part in parts if part.strip() and not _META_RE.search(part)]
    if not kept:
        return ""
    return "".join(kept).strip()


_QUOTE_PAIRS = (("「", "」"), ("『", "』"), ("“", "”"), ("‘", "’"))


def _trim_dangling_quotes(text: str) -> str:
    """模型被 max_tokens 切斷時，最後一段常是沒有收尾的「引號。

    這種半截內容直接送出會變成「或者是「1%bakery」被切掉的訊息，
    不如退回到引號之前，至少語意完整。
    """
    trimmed = text.rstrip()
    for opening, closing in _QUOTE_PAIRS:
        while trimmed.count(opening) > trimmed.count(closing):
            index = trimmed.rfind(opening)
            if index <= 0:
                break
            trimmed = trimmed[:index].rstrip(" ，,、；;：:")
    return trimmed


def sanitize_reply(
    text: str,
    *,
    allow_urls: bool = False,
    max_chars: int = 220,
    max_sparkles: int = 1,
) -> str:
    """把模型輸出變成「像群友打的字」，並移除洩漏的系統/工具痕跡。"""
    if not text:
        return ""

    cleaned = str(text).replace("\r\n", "\n").replace("\r", "\n").strip()

    # 1. 內部思考與未閉合標籤
    cleaned = _THOUGHT_RE.sub("", cleaned)
    cleaned = _THOUGHT_OPEN_RE.sub("", cleaned)
    cleaned = cleaned.replace("</thought>", "").strip()
    # 工具協議殘留不可外洩；容忍 `] ]` 與未閉合格式。
    cleaned = re.sub(r"\[\[\s*TOOL:.*?\]\s*\]", "", cleaned, flags=re.DOTALL).strip()
    cleaned = re.sub(r"\[\[\s*TOOL:.*$", "", cleaned, flags=re.DOTALL).strip()
    # 自主協議不可外洩；正常情況會在送出前就被 parse_autonomy_reply 攔下。
    cleaned = re.sub(r"\[\[\s*THINK\s*:.*?\]\]", "", cleaned, flags=re.IGNORECASE | re.DOTALL).strip()
    cleaned = re.sub(r"\[\[\s*(?:SILENT|WAIT\s*:\s*\d+)\s*\]\]", "", cleaned, flags=re.IGNORECASE).strip()

    # 1.5 生成被截斷時，移除沒有收尾的引號片段，避免送出半句話。
    cleaned = _trim_dangling_quotes(cleaned).strip()

    # 2. 格式殼拆包：[{'type':'text','text':'...'}]
    if _SHELL_BLOCK.search(cleaned):
        texts = _SHELL_TEXT.findall(cleaned)
        if texts:
            cleaned = " ".join(texts)
        else:
            cleaned = _SHELL_BLOCK.sub("", cleaned)

    # 3. code fence
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned).strip()

    # 4. URL 政策
    if not allow_urls:
        cleaned = re.sub(r"https?://\S+", "", cleaned)

    # 5. 保護行內代码、連結、引用，避免 meta 清洗誤刪
    cleaned, saved = _protect_inline(cleaned)

    # 6. 移除 hedge 與 meta 句
    cleaned = _HEDGE_RE.sub("", cleaned)
    cleaned = _drop_meta_sentences(cleaned)
    if not cleaned:
        return ""

    # 7. 外圍引號與句尾句號
    while True:
        prev = cleaned
        cleaned = re.sub(r"^[「\"'“‘]+|[」\"'”’]+$", "", cleaned).strip()
        if cleaned.endswith("。"):
            cleaned = cleaned[:-1].strip()
        if cleaned == prev:
            break

    # 8. 空白與重複裝飾
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    sparkle_count = 0

    def _clamp_sparkle(match: re.Match[str]) -> str:
        nonlocal sparkle_count
        sparkle_count += 1
        return match.group(0) if sparkle_count <= max_sparkles else ""

    cleaned = re.sub(r"✨", _clamp_sparkle, cleaned)

    # 9. 自稱收斂
    detective_count = 0

    def _replace_detective(match: re.Match[str]) -> str:
        nonlocal detective_count
        detective_count += 1
        return match.group(0) if detective_count <= 1 else "我"

    cleaned = re.sub(r"(?<!劇)本名偵探|(?<!劇)本偵探", _replace_detective, cleaned)

    # 10. 還原受保護內容
    cleaned = _restore_inline(cleaned, saved).strip()

    # 11. 長度裁剪：優先切在句末
    if max_chars > 0 and len(cleaned) > max_chars:
        head = cleaned[:max_chars]
        cut = max(head.rfind("。"), head.rfind("！"), head.rfind("？"), head.rfind("…"))
        cleaned = (head[:cut + 1] if cut >= max_chars * 0.6 else head).strip()

    return cleaned


_SENTENCE_END_RE = re.compile(r"(?<=[。！？!?…～~])")
_CLAUSE_END_RE = re.compile(r"(?<=[，,、；;：:])")
_TOOL_PROMISE_RE = re.compile(
    r"(?:我(?:先|來|去|幫你|馬上)?|讓我|等我|稍等)[^。！？!?\n]{0,12}(?:查|搜|找|掃|確認|看一下)"
)


def looks_like_tool_promise(text: str) -> bool:
    """判斷模型是否只說「我去查一下」卻沒有真的輸出工具呼叫。

    這種半截回覆如果直接送出，使用者會看到「我幫你搜尋一下」然後就沒有
    下文；工具迴圈會在這種情況下多要一輪真正的工具呼叫或完整答案。
    """
    body = str(text or "").strip()
    if not body or len(body) > 80:
        return False
    if "http://" in body or "https://" in body:
        return False
    # 已經講出結果（數字、元、%）就當成完整回答，不要再多問一輪。
    if any(marker in body for marker in ("結果", "查到", "找到", "答案是", "元", "%")):
        return False
    return bool(_TOOL_PROMISE_RE.search(body))


_AUTONOMY_SILENT_RE = re.compile(r"^\s*\[\[\s*SILENT\s*\]\]\s*$", re.IGNORECASE)
_AUTONOMY_WAIT_RE = re.compile(r"^\s*\[\[\s*WAIT\s*:\s*(\d{1,3})\s*\]\]\s*$", re.IGNORECASE)


def parse_autonomy_reply(
    text: str,
    *,
    min_wait: int = 30,
    max_wait: int = 90,
) -> tuple[str, int]:
    """解析 proactive 的自主協議。

    只認「整則輸出剛好是協議」；混在句子裡的一律視為說話，避免誤吞正常回覆。
    回傳 ``(act, wait_minutes)``，act 為 ``speak`` / ``silent`` / ``wait``。
    """
    raw = str(text or "").strip()
    if _AUTONOMY_SILENT_RE.match(raw):
        return "silent", 0
    match = _AUTONOMY_WAIT_RE.match(raw)
    if match:
        minutes = int(match.group(1))
        return "wait", max(int(min_wait), min(int(max_wait), minutes))
    return "speak", 0


_AUTONOMY_THOUGHT_RE = re.compile(
    r"^\s*\[\[\s*THINK\s*:\s*(.*?)\s*\]\][ \t]*(?:\r?\n)?",
    re.IGNORECASE,
)


def extract_autonomy_thought(text: str, *, max_chars: int = 60) -> tuple[str, str]:
    """取出 ``[[THINK: ...]]`` 的內心戲，回傳 ``(thought, 剩下的回覆)``。

    只認開頭第一行；這一行不會送進 Discord，只給 WebUI 顯示。
    """
    raw = str(text or "")
    match = _AUTONOMY_THOUGHT_RE.match(raw)
    if not match:
        return "", raw
    thought = str(match.group(1) or "").strip()[:max_chars]
    return thought, raw[match.end():]


def _hard_wrap(text: str, max_chars: int) -> list[str]:
    """最後手段：完全沒有標點可切時，均分切塊；不丟棄任何字元。"""
    return [text[i:i + max_chars] for i in range(0, len(text), max_chars)]


def _split_units(text: str, max_chars: int) -> list[str]:
    """先切句子，過長再切子句；確保每個單元都保留原文。"""
    units: list[str] = []
    for sentence in _SENTENCE_END_RE.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) <= max_chars:
            units.append(sentence)
            continue

        buffer = ""
        for clause in _CLAUSE_END_RE.split(sentence):
            clause = clause.strip()
            if not clause:
                continue
            if len(clause) > max_chars:
                if buffer:
                    units.append(buffer)
                    buffer = ""
                units.extend(_hard_wrap(clause, max_chars))
            elif len(buffer) + len(clause) <= max_chars:
                buffer += clause
            else:
                if buffer:
                    units.append(buffer)
                buffer = clause
        if buffer:
            units.append(buffer)
    return [unit for unit in units if unit]


def _pack_units(units: Iterable[str], *, max_bubbles: int, max_chars: int) -> list[str]:
    """把單元裝進氣泡；超過氣泡上限時併入最後一顆，而不是截斷丟字。"""
    bubbles: list[str] = []
    for unit in units:
        unit = str(unit or "").strip()
        if not unit:
            continue
        if not bubbles:
            bubbles.append(unit)
        elif len(bubbles) < max_bubbles and len(bubbles[-1]) + len(unit) <= max_chars:
            bubbles[-1] += unit
        elif len(bubbles) < max_bubbles:
            bubbles.append(unit)
        else:
            bubbles[-1] += unit
    return bubbles


def split_bubbles(text: str, *, max_bubbles: int = 3, max_chars: int = 60) -> list[str]:
    """把一段回覆拆成 1~N 顆口語氣泡；不改變任何外部狀態。

    舊版會用 ``chunk[:max_chars]`` 直接截斷，導致「1%bakery」這種
    講到一半被切掉的訊息。現在只負責「切」不負責「丟」：所有原始字元
    都會保留在某一顆氣泡裡，超過上限的句子併回最後一顆。
    """
    max_bubbles = max(1, int(max_bubbles))
    max_chars = max(24, int(max_chars))
    clean = sanitize_reply(text, max_chars=0)
    if not clean:
        return []

    units: list[str] = []
    for line in clean.splitlines():
        line = line.strip()
        if line:
            units.extend(_split_units(line, max_chars))
    return _pack_units(units, max_bubbles=max_bubbles, max_chars=max_chars)


def _fmt_clock(ts: float) -> str:
    if not ts:
        return "無"
    return time.strftime("%H:%M", time.localtime(ts))


def build_dynamic_context(
    *,
    now: float,
    unanswered: int,
    last_bot_at: float,
    last_human_at: float,
    phase: str,
    silence_seconds: float,
    trigger: str = "",
    motivation: str = "",
    autonomy_lines: Iterable[str] = (),
) -> str:
    lines = [
        f"- 現在時間：{_fmt_clock(now)}",
        f"- 本回合待回應訊息：{max(0, int(unanswered))} 則",
        f"- 雪莉上次發言：{_fmt_clock(last_bot_at)}",
        f"- 頻道最後人類發言：{_fmt_clock(last_human_at)}",
        f"- 目前狀態：{phase}",
    ]
    if silence_seconds >= 60:
        lines.append(f"- 距離上次人類發言：約 {int(silence_seconds // 60)} 分鐘")
    if trigger == "proactive":
        suffix = f"（{motivation}）" if motivation.strip() else ""
        lines.append(f"- 這一輪：你自己想開話題{suffix}")
        for extra_line in autonomy_lines:
            text = str(extra_line or "").strip()
            if text:
                lines.append(text)
    return "<dynamic_context>\n" + "\n".join(lines) + "\n</dynamic_context>"


def build_identity_block(*, query_text: str = "", max_chars: int = 700) -> str:
    """舊介面保留；新流程應改用 MemoryContextService。"""
    parts: list[str] = []
    try:
        from handlers.memory.self_memory import get_active_interests, get_self_memory_snippet

        snippet = str(get_self_memory_snippet(query_text=query_text, limit=4) or "").strip()
        if snippet:
            parts.append(snippet)
        interests = get_active_interests(limit=5)
        if interests:
            parts.append("近期在意：" + "、".join(str(item) for item in interests if str(item).strip()))
    except Exception:
        return ""

    body = "\n".join(parts).strip()
    if not body:
        return ""
    if len(body) > max_chars:
        body = body[:max_chars].rstrip() + "…"
    return (
        "<identity>\n"
        "以下是雪莉自己的記憶與偏好，只用來讓語氣一致；"
        "絕對不要說出標籤名稱、不要逐條背誦、不要提到系統。\n"
        f"{body}\n"
        "</identity>"
    )


def build_system_prompt(
    *,
    persona: str,
    tools_enabled: bool = False,
    mode: str = "ambient",
    static_context: str = "",
    allowed_tools: Iterable[str] | None = None,
    autonomy_enabled: bool = False,
    autonomy_wait_max_minutes: int = 90,
    autonomy_thought_enabled: bool = False,
) -> str:
    """組出 **完全靜態** 的 system prompt。

    前綴快取（KV Cache / Prefix Caching）只認「從第一個 token 開始完全相同」
    的前綴，所以時間、記憶、待回訊息數這類每回合都會變的東西一律不能放在
    system prompt，否則整段人設每次都要重新 prefill。動態內容請交給
    ``build_user_prompt`` 的 ``memory_block`` / ``dynamic_block`` / ``tail_block``。

    Args:
        persona: 靜態 head persona（SOUL + 通用規則）。
        tools_enabled: 是否在規則中列出工具協議。
        mode: 回合型態，決定固定的語氣規則。
        static_context: 其他靜態補充（例如頻道固定設定）。
    """
    tool_rule = (
        "- 需要即時資料時，只能單獨輸出一行工具呼叫，不要同時輸出要給群友看的話。\n"
        "- 查網路／新聞：[[TOOL:SEARCH {\"query\":\"...\"}]]\n"
        "- 查股票／行情：[[TOOL:STOCK {\"query\":\"0050\"}]]\n"
        "- 總結網址：[[TOOL:SUMMARY {\"url\":\"https://...\"}]]\n"
        "- 一般閒聊、常識、你已經知道的內容直接回答，不要為了保險而呼叫工具。\n"
        if tools_enabled
        else ""
    )
    mode_rule = (
        "- 這一回合是被動回覆：直接回答對方，不要主動轉移話題。\n"
        if mode in {"mention", "reply"}
        else "- 這一回合是自主接話：只在真的有話想說時開口，不要硬接。\n"
    )
    capability_rule = ""
    if tools_enabled and allowed_tools is not None:
        try:
            from handlers.social.tool_specs import format_capability_hint

            capability_rule = format_capability_hint(allowed_tools)
        except Exception:
            capability_rule = ""
    capability_block = f"{capability_rule}\n" if capability_rule else ""
    autonomy_block = ""
    if autonomy_enabled and mode == "proactive":
        wait_max = max(30, int(autonomy_wait_max_minutes))
        thought_line = (
            "- 先寫一句你心裡真正的想法：[[THINK: …]]（不會發出去）\n"
            if autonomy_thought_enabled
            else ""
        )
        autonomy_block = (
            "<autonomy>\n"
            "現在頻道很安靜，這是你自己的時間。\n"
            "你可以：\n"
            f"{thought_line}"
            "- 想說就說：直接講你真正想說的，1～2 句。\n"
            "- 不想說：只輸出 [[SILENT]]\n"
            f"- 想晚點說：只輸出 [[WAIT:45]]（30～{wait_max} 分鐘）\n"
            "沒有想說的就安靜，不用填滿沉默。\n"
            "</autonomy>"
        )
    parts = [persona.strip()]
    if static_context.strip():
        parts.append(static_context.strip())
    parts.append(
        "<rules>\n"
        "- 你現在是在 Discord 群組裡打字，不是在寫文章。\n"
        "- 只輸出要對群友說的話；不要輸出思考過程、標籤、JSON、Markdown 清單或程式碼區塊。\n"
        "- 每次 1 到 3 句，口語、短句、可以有情緒，但不要每句都驚嘆號或表情符號。\n"
        "- 不要自稱 AI、語言模型或助手；不要說「作為一個…」。\n"
        "- 不要重複對方已經說過的話，也不要每句都反問。\n"
        "- 不確定就承認不確定，不要編造事實。\n"
        f"{tool_rule}"
        f"{capability_block}"
        f"{mode_rule}"
        "</rules>"
    )
    if autonomy_block:
        parts.append(autonomy_block)
    return "\n\n".join(part for part in parts if part)


def format_events(events: Iterable[ChatEvent]) -> str:
    lines: list[str] = []
    for event in events:
        suffix = "（回覆雪莉）" if event.is_reply_to_bot else ""
        lines.append(f"[{_fmt_clock(event.created_at)}] {event.author_name}: {event.content}{suffix}")
    return "\n".join(lines)


def build_web_topic_block(topic: dict[str, Any] | None) -> str:
    """把剛從網路上看到的一則情報變成「自己開話題」的素材。

    One Brain, Two Lanes 上線後，舊的 surf_once 排程在 live 模式被停用，
    「自己上網找話題」就跟著消失。這裡只負責把抓回來的情報整理成
    prompt 片段，實際發送仍由 Cyber worker 的單一出口處理。
    """
    if not isinstance(topic, dict):
        return ""
    title = str(topic.get("title", "") or "").strip()
    if not title:
        return ""
    summary = str(topic.get("summary", "") or "").strip()
    source = str(topic.get("source", "") or "").strip()
    lines = ["你剛剛自己滑網路時看到這則：", f"- 標題：{title}"]
    if source:
        lines.append(f"- 來源：{source}")
    if summary:
        lines.append(f"- 摘要：{summary[:160]}")
    lines.append(
        "如果你覺得有趣，就用它開一個具體話題，講你自己的看法或吐槽；"
        "不要說「我在網路上看到」這種報告腔，也不要貼網址。"
    )
    return "\n".join(lines)


def build_user_prompt(
    *,
    transcript: str,
    events: Iterable[ChatEvent],
    extra: str = "",
    memory_block: str = "",
    dynamic_block: str = "",
    tail_block: str = "",
) -> str:
    """組出 user prompt。

    所有每回合會變動的內容都集中在這裡：記憶、即時狀態、時間與作者備註。
    system prompt 保持靜態，這樣同一個頻道／同一條 lane 的整段人設可以
    被 provider 的前綴快取重複利用（Gemini 隱式快取、llama.cpp KV reuse 等）。
    越靠近生成點的資訊影響力越大，所以時間與 tail 備註放在最後。
    """
    event_text = format_events(events) or "（這一回合沒有人類訊息，請主動開一個自然的話題）"
    head_parts: list[str] = []
    if memory_block.strip():
        head_parts.append(memory_block.strip())
    body = (
        "【近期對話】\n"
        f"{transcript.strip() or '（沒有更早的對話）'}\n\n"
        "【這一回合要回應的訊息】\n"
        f"{event_text}\n"
    )
    tail_parts: list[str] = []
    if dynamic_block.strip():
        tail_parts.append(dynamic_block.strip())
    if tail_block.strip():
        tail_parts.append(tail_block.strip())
    tail_parts.append(
        "【任務】\n"
        "用雪莉的語氣自然接話。若對方只是延續上一句，就直接回應；"
        "若對方在問問題，先回答重點，不要長篇解釋。"
    )
    if extra.strip():
        tail_parts.append(extra.strip())
    return "\n\n".join([part for part in head_parts if part] + [body] + [part for part in tail_parts if part])
