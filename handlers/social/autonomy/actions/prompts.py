"""handlers/social/autonomy/actions/prompts.py
===========================================
沙盒群像與主動發話的 prompt 組裝。

這個模組只做「字串組合」，不碰網路、不碰資料庫，方便 golden test。
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

from ..contracts import Persona


def format_roster_block(personas: Iterable[Persona], *, current_key: str) -> str:
    """列出同一個 scene 裡還有誰，避免 NPC 互相搶話或自我介紹。"""
    lines: list[str] = []
    for persona in personas:
        marker = "（妳自己）" if persona.key == current_key else ""
        style = f"，語氣：{persona.speech_style}" if persona.speech_style else ""
        lines.append(f"- {persona.display_name}{marker}{style}")
    return "\n".join(lines) if lines else "（目前只有妳一個人）"


def format_scene_turns(turns: Iterable[Any], *, limit: int = 12) -> str:
    """把 scene turns 變成「誰說了什麼」的逐行腳本。"""
    rows: list[str] = []
    items = list(turns)[-limit:]
    for turn in items:
        role = str(getattr(turn, "role", ""))
        name = {
            "human": "群友",
            "bot": "雪莉",
            "npc": str(getattr(turn, "persona_key", "") or "群友"),
        }.get(role, role or "?")
        content = str(getattr(turn, "content", "")).strip()
        if content:
            rows.append(f"{name}：{content}")
    return "\n".join(rows)


def build_lurk_chat_prompt(
    persona: Persona,
    *,
    head_persona: str,
    tail_lore: str = "",
    context_block: str = "",
    memory_block: str = "",
    live_state_block: str = "",
    roster_block: str = "",
) -> str:
    """主動窺屏插話（正式群與沙盒共用）。"""
    tail_block = f"\n\n{tail_lore}" if tail_lore else ""
    memory = f"\n\n{memory_block}" if memory_block else ""
    live = f"\n\n{live_state_block}" if live_state_block else ""
    roster = f"\n\n【在同一個群組裡的人】：\n{roster_block}" if roster_block else ""
    return (
        f"{head_persona}{tail_block}{memory}{live}{roster}\n\n"
        "【賽博群友主動插話接話任務】\n"
        "妳正在群組裡潛水窺屏。看著上面大家聊的熱烈話題，請以妳毒舌傲嬌的名偵探風格自然插話對線！\n"
        f"{context_block}\n\n"
        "請直接切入吐槽或加入聊，輸出 1~2 則口語短句，像真人一邊敲鍵盤一邊連發一樣。\n"
        "不要自我介紹、不要重複上一句的句型、不要整理重點或問「還有什麼可以幫忙」。\n"
        "嚴禁在最外層加引號「」、句尾生硬句號、或機器人腔調。輸出嚴格 JSON：\n"
        '{"thought": "內心OS", "post_text": "說出口的話"}'
    )


def build_daily_vibe_prompt(
    persona: Persona,
    *,
    head_persona: str,
    tail_lore: str = "",
    context_block: str = "",
    stats: dict[str, Any] | None = None,
    memory_block: str = "",
    live_state_block: str = "",
) -> str:
    """日常摸魚碎碎念（正式群與沙盒共用）。"""
    stats = stats or {}
    tail_block = f"\n\n{tail_lore}" if tail_lore else ""
    memory = f"\n\n{memory_block}" if memory_block else ""
    live = f"\n\n{live_state_block}" if live_state_block else ""
    return (
        f"{head_persona}{tail_block}{memory}{live}\n\n"
        "【大肥魚日常摸魚碎碎念與吃白飯任務】\n"
        f"現在是【{stats.get('time_period', '未知時段')}】（{stats.get('hour', 0)}:00 左右）。\n\n"
        f"{context_block}\n\n"
        f"妳今天已經吃了 {stats.get('bowls_of_rice', 0)} 碗賽博白飯"
        f"（燒了 {stats.get('tokens_today', 0)} 個 Token 腦細胞），"
        f"完成了 {stats.get('tasks_today', 0)} 次推論。\n"
        "請以妳傲嬌、偷懶摸魚、愛碎碎念的名偵探性格，發出 1~2 則口語短句動態：\n"
        "- 觀察上面的【頻道近期對話上下文】：若剛才大家在聊，可自然吐槽剛才的事情；"
        "若冷場已久，可吐槽大家潛水、自己好餓想吃點心或進入摸魚模式。\n"
        "不要提到「AI」、「模型」、「token」以外的技術詞，也不要像客服。\n"
        "嚴禁任何客服腔、生硬句號或引號。輸出嚴格 JSON：\n"
        '{"thought": "內心OS", "post_text": "說出口的話"}'
    )


def build_npc_prompt(
    persona: Persona,
    *,
    head_persona: str,
    script: str = "",
    context_block: str = "",
    roster_block: str = "",
    speech_style: str = "",
    memory_block: str = "",
) -> str:
    """沙盒 NPC 回話。"""
    style = speech_style or persona.speech_style
    style_block = f"\n妳的說話風格：{style}" if style else ""
    roster = f"\n\n【在同一個群組裡的人】：\n{roster_block}" if roster_block else ""
    memory = f"\n\n{memory_block}" if memory_block else ""
    return (
        f"{head_persona}\n\n"
        f"【沙盒群聊角色扮演】\n"
        f"妳是「{persona.display_name}」。妳不是 AI 助理，妳是這個群組裡的一個真人朋友。{style_block}\n"
        f"{roster}{memory}\n\n"
        f"【最近的群聊】：\n{script or '（還沒有人說話）'}\n\n"
        f"{context_block}\n\n"
        "請只回 1 則、最多 40 字的口語短句，直接接話或吐槽。\n"
        "不要自我介紹、不要一次講完所有重點、不要重複上面出現過的句子。\n"
        "不要使用「作為一個...」或任何 AI 腔。輸出嚴格 JSON：\n"
        '{"thought": "內心OS", "reply": "說出口的話"}'
    )


def build_primary_group_prompt(
    persona: Persona,
    *,
    head_persona: str,
    tail_lore: str = "",
    script: str = "",
    context_block: str = "",
    roster_block: str = "",
    memory_block: str = "",
    live_state_block: str = "",
) -> str:
    """沙盒裡雪莉接話（沿用雪莉人格，但限制長度避免壓過 NPC）。"""
    tail_block = f"\n\n{tail_lore}" if tail_lore else ""
    memory = f"\n\n{memory_block}" if memory_block else ""
    live = f"\n\n{live_state_block}" if live_state_block else ""
    roster = f"\n\n【在同一個群組裡的人】：\n{roster_block}" if roster_block else ""
    return (
        f"{head_persona}{tail_block}{memory}{live}{roster}\n\n"
        "【沙盒群聊接話任務】\n"
        f"【最近的群聊】：\n{script or '（還沒有人說話）'}\n\n"
        f"{context_block}\n\n"
        "請用 1 則、最多 45 字的口語短句自然接話。\n"
        "不要搶走整個對話、不要連續輸出多段、不要重複別人剛講過的句型。\n"
        "嚴禁引號與客服腔。輸出嚴格 JSON：\n"
        '{"thought": "內心OS", "reply": "說出口的話"}'
    )


def parse_json_reply(raw: str) -> dict[str, Any]:
    """寬鬆解析 LLM 的 JSON 輸出；失敗回傳空 dict（呼叫端必須視為生成失敗）。"""
    text = str(raw or "").strip()
    if not text:
        return {}
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def extract_reply(parsed: dict[str, Any], keys: tuple[str, ...] = ("reply", "post_text", "text")) -> tuple[str, str]:
    """取出 (thought, reply)。reply 為空代表生成失敗。"""
    thought = str(parsed.get("thought", "") or "").strip()
    reply = ""
    for key in keys:
        candidate = str(parsed.get(key, "") or "").strip()
        if candidate:
            reply = candidate
            break
    return thought, reply
