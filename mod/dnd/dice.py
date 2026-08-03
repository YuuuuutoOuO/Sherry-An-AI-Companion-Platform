"""mod/dnd/dice.py
=================
Dice rolling engine for D&D checks, attacks, and stats generation.
"""

from __future__ import annotations

import random
from typing import Literal


def roll(sides: int) -> int:
    """Roll a single n-sided die.

    Args:
        sides: Number of sides (e.g. 4, 6, 8, 10, 12, 20, 100).

    Returns:
        Rolled integer value (1 to sides).
    """
    if sides < 1:
        return 1
    return random.randint(1, sides)


def roll_dice(count: int, sides: int) -> list[int]:
    """Roll multiple n-sided dice.

    Args:
        count: Number of dice to roll.
        sides: Number of sides per die.

    Returns:
        List of rolled values.
    """
    return [roll(sides) for _ in range(max(1, count))]


def advantage_roll(sides: int = 20) -> tuple[int, int, int]:
    """Roll twice and pick the higher value (Advantage).

    Args:
        sides: Number of sides.

    Returns:
        Tuple of (first_roll, second_roll, chosen_higher_value).
    """
    d1 = roll(sides)
    d2 = roll(sides)
    return d1, d2, max(d1, d2)


def disadvantage_roll(sides: int = 20) -> tuple[int, int, int]:
    """Roll twice and pick the lower value (Disadvantage).

    Args:
        sides: Number of sides.

    Returns:
        Tuple of (first_roll, second_roll, chosen_lower_value).
    """
    d1 = roll(sides)
    d2 = roll(sides)
    return d1, d2, min(d1, d2)


def check_critical(value: int, sides: int = 20) -> Literal["CRITICAL_SUCCESS", "CRITICAL_FAILURE", "NORMAL"]:
    """Determine critical success (Nat 20) or failure (Nat 1).

    Args:
        value: The natural roll value on the die.
        sides: Number of sides.

    Returns:
        Critical status string.
    """
    if sides == 20:
        if value == 20:
            return "CRITICAL_SUCCESS"
        if value == 1:
            return "CRITICAL_FAILURE"
    return "NORMAL"


def ability_modifier(score: int) -> int:
    """Calculate standard D&D ability modifier.

    Args:
        score: Ability score value (e.g., 14).

    Returns:
        Modifier value (e.g., +2).
    """
    return (score - 10) // 2


def roll_ability_scores() -> dict[str, int]:
    """Generate standard 6 ability scores using 4d6 (drop lowest).

    Returns:
        Dictionary mapping ability names to scores.
    """
    abilities = ["strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"]
    scores: dict[str, int] = {}

    for ability in abilities:
        rolls = sorted(roll_dice(4, 6))
        # Keep highest 3
        score = sum(rolls[1:])
        scores[ability] = score

    return scores
