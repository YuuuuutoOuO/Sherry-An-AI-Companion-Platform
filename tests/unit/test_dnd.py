"""tests/unit/test_dnd.py
======================
Unit tests for D&D Plugin modules (AAA pattern).
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from mod.dnd.character import add_experience, create_character
from mod.dnd.combat import CombatManager
from mod.dnd.dice import (
    ability_modifier,
    advantage_roll,
    check_critical,
    disadvantage_roll,
    roll,
    roll_ability_scores,
    roll_dice,
)
from mod.dnd.inventory import add_gold, equip_item
from mod.dnd.models import GameTime, Inventory, Item, ItemRarity
from mod.dnd.storage import (
    load_characters,
    load_world_setting,
    load_world_state,
    save_characters,
    save_world_state,
)


def test_dice_engine_basic_rolls() -> None:
    """Test basic single and multiple dice rolls."""
    # Arrange & Act
    val = roll(20)
    multiple = roll_dice(4, 6)

    # Assert
    assert 1 <= val <= 20
    assert len(multiple) == 4
    for r in multiple:
        assert 1 <= r <= 6


def test_dice_engine_advantage_disadvantage() -> None:
    """Test advantage and disadvantage rolling mechanics."""
    # Arrange & Act
    d1, d2, chosen_adv = advantage_roll(20)
    d3, d4, chosen_dis = disadvantage_roll(20)

    # Assert
    assert chosen_adv == max(d1, d2)
    assert chosen_dis == min(d3, d4)


def test_dice_critical_detection() -> None:
    """Test critical success and failure detection."""
    # Assert
    assert check_critical(20, 20) == "CRITICAL_SUCCESS"
    assert check_critical(1, 20) == "CRITICAL_FAILURE"
    assert check_critical(15, 20) == "NORMAL"


def test_ability_modifier_math() -> None:
    """Test standard D&D ability modifier formula."""
    # Assert
    assert ability_modifier(10) == 0
    assert ability_modifier(14) == 2
    assert ability_modifier(18) == 4
    assert ability_modifier(8) == -1


def test_game_time_advancement() -> None:
    """Test game time hour advancing logic."""
    # Arrange
    gt = GameTime(year=1247, month=3, day=17, hour=22, minute=0)

    # Act
    gt_next = gt.advance_hours(5)

    # Assert
    assert gt_next.hour == 3
    assert gt_next.day == 18
    assert gt_next.to_string() == "裂隙紀元 1247 年 3 月 18 日 03:00"


def test_character_creation_and_level_up(tmp_path: Path) -> None:
    """Test character creation and XP level up mechanics."""
    # Arrange
    user_id = 99999
    with patch("mod.dnd.storage.CHARACTERS_FILE", tmp_path / "dnd_characters.json"):
        # Act
        char = create_character(user_id, "阿爾薩斯", "人類", "戰士")

        # Assert
        assert char.name == "阿爾薩斯"
        assert char.level == 1
        assert char.hp_max > 0

        # Act 2: Add XP to level up
        updated_char, leveled = add_experience(user_id, 500)

        # Assert 2
        assert leveled is True
        assert updated_char.level == 2
        assert updated_char.experience == 500


def test_inventory_equip_and_gold(tmp_path: Path) -> None:
    """Test equipping weapons/armor and adding gold."""
    # Arrange
    user_id = 88888
    with patch("mod.dnd.storage.CHARACTERS_FILE", tmp_path / "dnd_characters.json"):
        create_character(user_id, "羅賓", "精靈", "遊俠")

        # Act: Item "長弓" is already equipped upon creation
        updated_char, msg = equip_item(user_id, "長弓")

        # Assert
        assert "已經在裝備中" in msg

        # Act 2: Add gold
        updated_char = add_gold(user_id, 50)
        assert updated_char.inventory.gold == 65  # 15 starting + 50



def test_combat_manager() -> None:
    """Test turn-based combat initiative and turn management."""
    # Arrange
    cm = CombatManager()
    players = [(101, "戰士", 20, 15)]
    enemies = [("哥布林", 10, 12)]

    # Act
    session = cm.start_combat(channel_id=12345, players=players, enemies=enemies)

    # Assert
    assert session.is_active is True
    assert len(session.combatants) == 2

    # Act 2: Advance turn
    next_c, ended = cm.next_turn(12345)
    assert ended is False
    assert next_c is not None


def test_extract_checks_from_dm_text() -> None:
    """Test regex extraction of ability check prompts from DM narrative text."""
    from unittest.mock import MagicMock
    import sys

    mock_discord = MagicMock()
    with patch.dict("sys.modules", {"discord": mock_discord, "discord.ui": mock_discord.ui, "discord.ext": mock_discord.ext}):
        from mod.dnd.views import extract_checks

        # Arrange
        dm_text = (
            "眼前有螢光草與野狼。\n"
            "【感知檢定 (DC 10)】：發現亮光\n"
            "【力量檢定 (DC 14)】：搬開巨石"
        )

        # Act
        checks = extract_checks(dm_text)

        # Assert
        assert len(checks) == 2
        assert checks[0] == ("感知", 10)
        assert checks[1] == ("力量", 14)


