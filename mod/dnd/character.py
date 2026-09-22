"""mod/dnd/character.py
======================
Character management logic for D&D plugin.

Handles creation of new characters, ability score calculation,
HP calculations based on class, and leveling up.
"""

from __future__ import annotations

import time

from mod.dnd.dice import roll_ability_scores
from mod.dnd.models import Character, CharacterStats, Inventory, Item, ItemRarity
from mod.dnd.storage import load_characters, save_characters


CLASS_BASE_HP: dict[str, int] = {
    "戰士": 10,
    "牧師": 8,
    "遊俠": 8,
    "盜賊": 8,
    "法師": 6,
}

CLASS_STARTING_EQUIPMENT: dict[str, list[Item]] = {
    "戰士": [
        Item("長劍", "武器", "一把標準的鐵製長劍", ItemRarity.COMMON, 1500, True, 2),
        Item("鐵甲", "防具", "堅固的半身鐵甲", ItemRarity.COMMON, 3000, True, 4),
    ],
    "法師": [
        Item("法師長杖", "武器", "嵌有次級水晶的木杖", ItemRarity.COMMON, 1000, True, 1),
        Item("布袍", "防具", "舒適輕盈的學者長袍", ItemRarity.COMMON, 500, True, 1),
    ],
    "遊俠": [
        Item("長弓", "武器", "精良的木質長弓", ItemRarity.COMMON, 2500, True, 2),
        Item("皮甲", "防具", "硬化皮革胸甲", ItemRarity.COMMON, 1000, True, 2),
    ],
    "盜賊": [
        Item("雙匕首", "武器", "鋒利的刺客匕首", ItemRarity.COMMON, 1000, True, 2),
        Item("輕皮甲", "防具", "利於隱蔽的暗色皮甲", ItemRarity.COMMON, 800, True, 2),
    ],
    "牧師": [
        Item("戰錘", "武器", "帶有聖徽的沉重戰錘", ItemRarity.COMMON, 1500, True, 2),
        Item("鎖子甲", "防具", "環環相扣的金屬金屬甲", ItemRarity.COMMON, 4000, True, 3),
    ],
}


def create_character(user_id: int, name: str, race: str, character_class: str) -> Character:
    """Create a brand new Level 1 Character for a user.

    Args:
        user_id: Discord user ID.
        name: Character name.
        race: Race (e.g. 人類, 精靈).
        character_class: Class (e.g. 戰士, 法師).

    Returns:
        Newly created Character object.
    """
    scores = roll_ability_scores()

    # Race racial bonuses
    if race == "精靈":
        scores["dexterity"] += 2
    elif race == "矮人":
        scores["constitution"] += 2
    elif race == "人類":
        for k in scores:
            scores[k] += 1
    elif race == "半獸人":
        scores["strength"] += 2

    stats = CharacterStats(**scores)

    con_mod = stats.modifier("constitution")
    base_hp = CLASS_BASE_HP.get(character_class, 8)
    max_hp = max(1, base_hp + con_mod)

    dex_mod = stats.modifier("dexterity")
    base_ac = 10 + dex_mod

    equipment = CLASS_STARTING_EQUIPMENT.get(character_class, [])
    # Calculate initial bonus AC from starting equipped armor
    for eq in equipment:
        if eq.item_type == "防具":
            base_ac += eq.effect_bonus

    inventory = Inventory(
        items=equipment,
        gold=15,
        silver=0,
        copper=0,
    )

    created_time = time.strftime("%Y-%m-%d %H:%M:%S")

    char = Character(
        user_id=user_id,
        name=name,
        race=race,
        character_class=character_class,
        level=1,
        experience=0,
        hp_max=max_hp,
        hp_current=max_hp,
        ac=base_ac,
        stats=stats,
        inventory=inventory,
        location_id="start_village",
        created_at=created_time,
    )

    all_chars = load_characters()
    all_chars[user_id] = char
    save_characters(all_chars)

    return char

