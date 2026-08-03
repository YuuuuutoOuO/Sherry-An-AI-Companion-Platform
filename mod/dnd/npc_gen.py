"""mod/dnd/npc_gen.py
===================
NPC generation & schedule handling for D&D plugin.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from mod.dnd.models import Npc

logger = logging.getLogger(__name__)


def build_npc_generation_prompt(location_name: str, era: str) -> str:
    """Build prompt for LLM to generate a new NPC entity.

    Args:
        location_name: Current location name.
        era: World era string.

    Returns:
        System prompt formatted for JSON output.
    """
    return (
        f"你正在為【{era}】的【{location_name}】生成一位新的 D&D NPC。\n"
        "請以嚴格的 JSON 格式回傳，格式如下：\n"
        "{\n"
        '  "npc_id": "英文小寫識別碼",\n'
        '  "name": "NPC 名字與稱號",\n'
        '  "appearance": "外觀特徵與穿著描述",\n'
        '  "personality": "性格特質與說話習慣",\n'
        '  "background": "簡短的背景故事",\n'
        '  "schedule": {\n'
        '    "08:00": "工作地點與行為",\n'
        '    "12:00": "午餐與休息",\n'
        '    "18:00": "酒館或社交",\n'
        '    "22:00": "回家休息"\n'
        "  }\n"
        "}\n"
        "不要輸出 JSON 以外的任何開頭或結尾文字。"
    )


def parse_npc_response(json_str: str, location_id: str) -> Npc | None:
    """Parse JSON output from LLM into Npc object.

    Args:
        json_str: Raw JSON string from LLM.
        location_id: Current location.

    Returns:
        Parsed Npc object or None if failed.
    """
    try:
        # Strip potential markdown backticks
        clean = json_str.strip()
        if clean.startswith("```"):
            lines = clean.splitlines()
            clean = "\n".join(lines[1:-1])

        data = json.loads(clean)
        return Npc(
            npc_id=data.get("npc_id", "unknown_npc"),
            name=data.get("name", "神秘旅人"),
            location_id=location_id,
            appearance=data.get("appearance", "穿著普通的衣袍。"),
            personality=data.get("personality", "沉穩寡言。"),
            background=data.get("background", "無人知曉其過去。"),
            schedule=data.get("schedule", {}),
            affinity={},
        )
    except Exception as exc:
        logger.error("Failed to parse NPC response: %s", exc)
        return None
