"""handlers/social/brain/parsing.py
================================
模型輸出的寬鬆解析。

刻意不依賴 ``handlers.social.autonomy``：那個套件載入時會連帶載入
``handlers.care``，而 care 又依賴 brain，會形成循環匯入。
"""

from __future__ import annotations

import json
import re
from typing import Any


def parse_json_reply(raw: str) -> dict[str, Any]:
    """寬鬆解析 LLM 的 JSON 輸出；失敗回傳空 dict。"""
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


def extract_reply(
    parsed: dict[str, Any],
    keys: tuple[str, ...] = ("reply", "post_text", "text"),
) -> tuple[str, str]:
    """取出 ``(thought, reply)``；reply 為空代表生成失敗。"""
    thought = str(parsed.get("thought", "") or "").strip()
    reply = ""
    for key in keys:
        candidate = str(parsed.get(key, "") or "").strip()
        if candidate:
            reply = candidate
            break
    return thought, reply
