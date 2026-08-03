"""mod/dnd/quest_gen.py
=====================
Quest generation & tracking logic for D&D plugin.
"""

from __future__ import annotations

import json
import logging

from mod.dnd.models import Quest, QuestStatus

logger = logging.getLogger(__name__)


def build_quest_prompt(location_name: str, player_level: int) -> str:
    """Build prompt for generating dynamic quest via LLM.

    Args:
        location_name: Current location.
        player_level: Current player level.

    Returns:
        Formatted prompt.
    """
    return (
        f"請為一位【等級 {player_level}】位於【{location_name}】的冒險者生成一個符合奇幻世界觀的任務委託。\n"
        "請以嚴格 JSON 格式輸出：\n"
        "{\n"
        '  "quest_id": "quest_英文標籤",\n'
        '  "title": "任務名稱",\n'
        '  "description": "委託任務背景與具體目標",\n'
        '  "reward": "獎勵（如：50 金幣與精良鐵劍）",\n'
        '  "deadline_ticks": 48\n'
        "}\n"
        "不要輸出 JSON 以外的文字。"
    )


def parse_quest_response(json_str: str, current_tick: int) -> Quest | None:
    """Parse quest JSON response into Quest object.

    Args:
        json_str: Raw JSON output from LLM.
        current_tick: Current world tick count.

    Returns:
        Quest object or None.
    """
    try:
        clean = json_str.strip()
        if clean.startswith("```"):
            lines = clean.splitlines()
            clean = "\n".join(lines[1:-1])

        data = json.loads(clean)
        deadline_ticks = data.get("deadline_ticks", 0)
        deadline = (current_tick + deadline_ticks) if deadline_ticks > 0 else 0

        return Quest(
            quest_id=data.get("quest_id", "quest_default"),
            title=data.get("title", "無名委託"),
            description=data.get("description", "解決當地的問題。"),
            reward=data.get("reward", "10 金幣"),
            status=QuestStatus.ACTIVE,
            deadline_tick=deadline,
            progress="剛接取任務",
        )
    except Exception as exc:
        logger.error("Failed to parse Quest response: %s", exc)
        return None
