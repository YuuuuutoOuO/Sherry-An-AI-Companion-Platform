"""mod/dnd/inventory.py
=====================
Inventory management logic for D&D plugin.

Handles equipping items, gold/currency transactions, and item usage.
"""

from __future__ import annotations

from mod.dnd.models import Character, Item
from mod.dnd.storage import load_characters, save_characters


def equip_item(user_id: int, item_name: str) -> tuple[Character, str]:
    """Equip an item from inventory.

    Args:
        user_id: User ID.
        item_name: Name of item to equip.

    Returns:
        Tuple of (Updated Character, message result).
    """
    all_chars = load_characters()
    if user_id not in all_chars:
        return None, "找不到您的冒險者角色。"  # type: ignore[return-value]

    char = all_chars[user_id]
    target_item: Item | None = None

    for item in char.inventory.items:
        if item.name.lower() == item_name.lower():
            target_item = item
            break

    if not target_item:
        return char, f"背包中找不到「{item_name}」。"

    if target_item.equipped:
        return char, f"「{target_item.name}」已經在裝備中。"

    # Unequip existing items of same type
    for item in char.inventory.items:
        if item.item_type == target_item.item_type and item.equipped:
            item.equipped = False
            if item.item_type == "防具":
                char.ac -= item.effect_bonus

    target_item.equipped = True
    if target_item.item_type == "防具":
        char.ac += target_item.effect_bonus

    all_chars[user_id] = char
    save_characters(all_chars)

    return char, f"已成功裝備「{target_item.name}」！"


def add_gold(user_id: int, gold_amount: int) -> Character:
    """Add gold to character.

    Args:
        user_id: User ID.
        gold_amount: Amount of gold to add.

    Returns:
        Updated Character.
    """
    all_chars = load_characters()
    if user_id in all_chars:
        char = all_chars[user_id]
        char.inventory.gold = max(0, char.inventory.gold + gold_amount)
        all_chars[user_id] = char
        save_characters(all_chars)
        return char
    raise ValueError(f"Character for user {user_id} not found.")
