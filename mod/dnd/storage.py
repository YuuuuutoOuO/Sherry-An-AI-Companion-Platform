"""mod/dnd/storage.py
====================
JSON Persistence repository for D&D plugin data.

Provides atomic write operations and JSON/JSONL serialization for:
- World state (locations, time, active channels)
- Characters
- Quests
- NPCs
- World history events
"""

from __future__ import annotations

import functools
import json
import logging
import os
from pathlib import Path
from typing import Any

from mod.dnd import config
from mod.dnd.models import (
    Character,
    CharacterStats,
    GameTime,
    Inventory,
    Item,
    ItemRarity,
    Location,
    Npc,
    Quest,
    QuestStatus,
    WorldState,
)

logger = logging.getLogger(__name__)

# File paths (aliased from config for compatibility)
WORLD_FILE: Path = config.WORLD_FILE
CHARACTERS_FILE: Path = config.CHARACTERS_FILE
QUESTS_FILE: Path = config.QUESTS_FILE
NPCS_FILE: Path = config.NPCS_FILE
HISTORY_FILE: Path = config.HISTORY_FILE
WORLD_SETTING_FILE: Path = config.WORLD_SETTING_FILE


def _atomic_write_json(file_path: Path, data: Any) -> None:
    """Write data to a JSON file atomically using a temporary file.

    Args:
        file_path: Target path to write JSON.
        data: Data structure to serialize.
    """
    tmp_path = file_path.with_suffix(".tmp")
    file_path.parent.mkdir(parents=True, exist_ok=True)

    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    os.replace(tmp_path, file_path)


# ---------------------------------------------------------------------------
# World Setting (Static Skeleton)
# ---------------------------------------------------------------------------

DEFAULT_WORLD_SETTING: dict[str, Any] = {
    "world_name": "艾瑟蘭大陸",
    "era": "裂隙紀元 1247 年",
    "magic_level": "中魔",
    "starting_area": {
        "name": "晨風村",
        "type": "邊境村落",
        "description": "位於平原與迷霧森林邊界的平靜村落，是無數冒險者的起點。",
    },
    "available_races": ["人類", "精靈", "矮人", "半身人", "半獸人"],
    "available_classes": ["戰士", "法師", "遊俠", "盜賊", "牧師"],
    "dm_core_directive": "你是艾瑟蘭大陸的地下城主 (DM)。世界是中魔設定、科技約等同中世紀歐洲。嚴禁引入現代科技或其它作品的角色。保持專業、沉浸且具有奇幻感的敘事風格。",
}


@functools.lru_cache(maxsize=1)
def load_world_setting() -> dict[str, Any]:
    """Load static world setting JSON skeleton.

    Returns:
        Dictionary containing static setting.
    """
    if not WORLD_SETTING_FILE.exists():
        try:
            WORLD_SETTING_FILE.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_json(WORLD_SETTING_FILE, DEFAULT_WORLD_SETTING)
            logger.info("Initialized default world setting at %s", WORLD_SETTING_FILE)
            return DEFAULT_WORLD_SETTING.copy()
        except Exception as exc:
            logger.warning("Could not persist default world setting: %s", exc)
            return DEFAULT_WORLD_SETTING.copy()

    try:
        with open(WORLD_SETTING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)  # type: ignore[no-any-return]
    except Exception as exc:
        logger.error("Failed to load world setting file: %s", exc)
        return DEFAULT_WORLD_SETTING.copy()


# ---------------------------------------------------------------------------
# World State Repository
# ---------------------------------------------------------------------------

def load_world_state() -> WorldState:
    """Load WorldState from JSON storage or create default.

    Returns:
        WorldState instance.
    """
    if not WORLD_FILE.exists():
        setting = load_world_setting()
        start_area = setting.get("starting_area", {})
        start_loc = Location(
            location_id="start_village",
            name=start_area.get("name", "晨風村"),
            location_type=start_area.get("type", "邊境村落"),
            description=start_area.get("description", "一處平靜的村落。"),
            npcs_present=["老鐵匠", "酒館老闆娘"],
            connected_locations=["whisper_forest"],
        )
        forest_loc = Location(
            location_id="whisper_forest",
            name="低語森林",
            location_type="野外森林",
            description="傳聞森林深處常有怪異低語與怪物出沒。",
            npcs_present=[],
            connected_locations=["start_village"],
        )

        world = WorldState(
            game_time=GameTime(),
            current_tick=0,
            locations={
                "start_village": start_loc,
                "whisper_forest": forest_loc,
            },
            active_channels=[],
        )
        save_world_state(world)
        return world

    try:
        with open(WORLD_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)

        gt_raw = raw.get("game_time", {})
        game_time = GameTime(
            year=gt_raw.get("year", 1247),
            month=gt_raw.get("month", 3),
            day=gt_raw.get("day", 17),
            hour=gt_raw.get("hour", 14),
            minute=gt_raw.get("minute", 0),
        )

        locations: dict[str, Location] = {}
        for loc_id, loc_data in raw.get("locations", {}).items():
            locations[loc_id] = Location(
                location_id=loc_id,
                name=loc_data.get("name", loc_id),
                location_type=loc_data.get("location_type", "探索區域"),
                description=loc_data.get("description", ""),
                npcs_present=loc_data.get("npcs_present", []),
                connected_locations=loc_data.get("connected_locations", []),
            )

        return WorldState(
            game_time=game_time,
            current_tick=raw.get("current_tick", 0),
            locations=locations,
            active_channels=raw.get("active_channels", []),
        )
    except Exception as exc:
        logger.error("Error reading dnd_world.json: %s", exc)
        return WorldState()


def save_world_state(world: WorldState) -> None:
    """Save WorldState to JSON storage.

    Args:
        world: WorldState object.
    """
    raw_locations = {}
    for loc_id, loc in world.locations.items():
        raw_locations[loc_id] = {
            "name": loc.name,
            "location_type": loc.location_type,
            "description": loc.description,
            "npcs_present": loc.npcs_present,
            "connected_locations": loc.connected_locations,
        }

    raw = {
        "game_time": {
            "year": world.game_time.year,
            "month": world.game_time.month,
            "day": world.game_time.day,
            "hour": world.game_time.hour,
            "minute": world.game_time.minute,
        },
        "current_tick": world.current_tick,
        "locations": raw_locations,
        "active_channels": world.active_channels,
    }

    _atomic_write_json(WORLD_FILE, raw)


# ---------------------------------------------------------------------------
# Characters Repository
# ---------------------------------------------------------------------------

def load_characters() -> dict[int, Character]:
    """Load all player characters from storage.

    Returns:
        Map of user_id to Character.
    """
    if not CHARACTERS_FILE.exists():
        return {}

    try:
        with open(CHARACTERS_FILE, "r", encoding="utf-8") as f:
            raw_map: dict[str, Any] = json.load(f)

        result: dict[int, Character] = {}
        for uid_str, data in raw_map.items():
            uid = int(uid_str)
            stats_raw = data.get("stats", {})
            stats = CharacterStats(
                strength=stats_raw.get("strength", 10),
                dexterity=stats_raw.get("dexterity", 10),
                constitution=stats_raw.get("constitution", 10),
                intelligence=stats_raw.get("intelligence", 10),
                wisdom=stats_raw.get("wisdom", 10),
                charisma=stats_raw.get("charisma", 10),
            )

            inv_raw = data.get("inventory", {})
            items: list[Item] = []
            for item_raw in inv_raw.get("items", []):
                items.append(
                    Item(
                        name=item_raw.get("name", "未命名物品"),
                        item_type=item_raw.get("item_type", "雜物"),
                        description=item_raw.get("description", ""),
                        rarity=ItemRarity(item_raw.get("rarity", ItemRarity.COMMON.value)),
                        value_cp=item_raw.get("value_cp", 10),
                        equipped=item_raw.get("equipped", False),
                        effect_bonus=item_raw.get("effect_bonus", 0),
                    )
                )

            inventory = Inventory(
                items=items,
                gold=inv_raw.get("gold", 10),
                silver=inv_raw.get("silver", 0),
                copper=inv_raw.get("copper", 0),
            )

            char = Character(
                user_id=uid,
                name=data.get("name", "無名冒險者"),
                race=data.get("race", "人類"),
                character_class=data.get("character_class", "戰士"),
                level=data.get("level", 1),
                experience=data.get("experience", 0),
                hp_max=data.get("hp_max", 10),
                hp_current=data.get("hp_current", 10),
                ac=data.get("ac", 10),
                stats=stats,
                inventory=inventory,
                location_id=data.get("location_id", "start_village"),
                created_at=data.get("created_at", ""),
            )
            result[uid] = char

        return result
    except Exception as exc:
        logger.error("Failed to load dnd_characters.json: %s", exc)
        return {}


def save_characters(characters: dict[int, Character]) -> None:
    """Save character dictionary to storage.

    Args:
        characters: Dictionary mapping user_id to Character.
    """
    raw_map: dict[str, Any] = {}
    for uid, char in characters.items():
        items_raw = [
            {
                "name": it.name,
                "item_type": it.item_type,
                "description": it.description,
                "rarity": it.rarity.value,
                "value_cp": it.value_cp,
                "equipped": it.equipped,
                "effect_bonus": it.effect_bonus,
            }
            for it in char.inventory.items
        ]

        raw_map[str(uid)] = {
            "name": char.name,
            "race": char.race,
            "character_class": char.character_class,
            "level": char.level,
            "experience": char.experience,
            "hp_max": char.hp_max,
            "hp_current": char.hp_current,
            "ac": char.ac,
            "stats": {
                "strength": char.stats.strength,
                "dexterity": char.stats.dexterity,
                "constitution": char.stats.constitution,
                "intelligence": char.stats.intelligence,
                "wisdom": char.stats.wisdom,
                "charisma": char.stats.charisma,
            },
            "inventory": {
                "gold": char.inventory.gold,
                "silver": char.inventory.silver,
                "copper": char.inventory.copper,
                "items": items_raw,
            },
            "location_id": char.location_id,
            "created_at": char.created_at,
        }

    _atomic_write_json(CHARACTERS_FILE, raw_map)


# ---------------------------------------------------------------------------
# History Append (JSONL)
# ---------------------------------------------------------------------------

def append_world_history(event_type: str, description: str, tick: int) -> None:
    """Append a world history log entry.

    Args:
        event_type: Category of event (e.g., 'ENVIRONMENT', 'SOCIAL').
        description: Description of event.
        tick: World tick number.
    """
    entry = {
        "tick": tick,
        "type": event_type,
        "description": description,
    }
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
