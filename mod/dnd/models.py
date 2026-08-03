"""mod/dnd/models.py
===================
Data models for the D&D plugin system.

All data structures are defined as dataclasses or TypedDicts to ensure
strict type safety and clear schema definitions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TypedDict


@dataclass(frozen=True)
class GameTime:
    """Represents in-game time in the D&D world.

    Attributes:
        year: Current year.
        month: Current month (1-12).
        day: Current day (1-30).
        hour: Current hour (0-23).
        minute: Current minute (0-59).
    """
    year: int = 1247
    month: int = 3
    day: int = 17
    hour: int = 14
    minute: int = 0

    def advance_hours(self, hours: int = 1) -> GameTime:
        """Advance game time by a given number of hours.

        Args:
            hours: Number of hours to add.

        Returns:
            A new GameTime instance with advanced time.
        """
        total_minutes = self.minute + self.hour * 60 + hours * 60
        new_minute = total_minutes % 60
        new_hour = (total_minutes // 60) % 24
        day_overflow = total_minutes // 60 // 24

        total_days = (self.day - 1) + day_overflow
        new_day = (total_days % 30) + 1
        month_overflow = total_days // 30

        total_months = (self.month - 1) + month_overflow
        new_month = (total_months % 12) + 1
        year_overflow = total_months // 12

        new_year = self.year + year_overflow

        return GameTime(
            year=new_year,
            month=new_month,
            day=new_day,
            hour=new_hour,
            minute=new_minute,
        )

    def to_string(self) -> str:
        """Format in-game time into human-readable string.

        Returns:
            Formatted time string.
        """
        return f"裂隙紀元 {self.year} 年 {self.month} 月 {self.day} 日 {self.hour:02d}:{self.minute:02d}"


@dataclass
class CharacterStats:
    """D&D Core Ability Scores.

    Attributes:
        strength: Physical power.
        dexterity: Agility and reflexes.
        constitution: Endurance and stamina.
        intelligence: Reasoning and knowledge.
        wisdom: Perception and intuition.
        charisma: Force of personality.
    """
    strength: int = 10
    dexterity: int = 10
    constitution: int = 10
    intelligence: int = 10
    wisdom: int = 10
    charisma: int = 10

    def modifier(self, ability: str) -> int:
        """Calculate D&D ability modifier score.

        Args:
            ability: Ability name ('str', 'dex', 'con', 'int', 'wis', 'cha').

        Returns:
            Modifier integer (e.g., score 14 -> +2).
        """
        score = getattr(self, ability.lower(), 10)
        return (score - 10) // 2


class ItemRarity(str, Enum):
    """Rarity classification for items."""
    COMMON = "普通"
    UNCOMMON = "優秀"
    RARE = "稀有"
    EPIC = "史詩"
    LEGENDARY = "傳說"


@dataclass
class Item:
    """Represents an inventory item.

    Attributes:
        name: Item name.
        item_type: Weapon, Armor, Potion, Misc, etc.
        description: Description of the item.
        rarity: Item rarity.
        value_cp: Value in copper pieces.
        equipped: Whether item is currently equipped.
        effect_bonus: Bonus value provided by item.
    """
    name: str
    item_type: str
    description: str
    rarity: ItemRarity = ItemRarity.COMMON
    value_cp: int = 10
    equipped: bool = False
    effect_bonus: int = 0


@dataclass
class Inventory:
    """Character inventory and currency.

    Attributes:
        items: List of items carried.
        gold: Gold coins.
        silver: Silver coins.
        copper: Copper coins.
    """
    items: list[Item] = field(default_factory=list)
    gold: int = 10
    silver: int = 0
    copper: int = 0

    def total_copper(self) -> int:
        """Calculate total value in copper.

        Returns:
            Total copper value.
        """
        return self.gold * 100 + self.silver * 10 + self.copper


@dataclass
class Character:
    """Player character state.

    Attributes:
        user_id: Discord user ID.
        name: Character name.
        race: Race.
        character_class: Class.
        level: Current level.
        experience: XP points.
        hp_max: Maximum Hit Points.
        hp_current: Current Hit Points.
        ac: Armor Class.
        stats: Ability scores.
        inventory: Character inventory.
        location_id: Current location ID.
        created_at: Creation timestamp string.
    """
    user_id: int
    name: str
    race: str
    character_class: str
    level: int = 1
    experience: int = 0
    hp_max: int = 10
    hp_current: int = 10
    ac: int = 10
    stats: CharacterStats = field(default_factory=CharacterStats)
    inventory: Inventory = field(default_factory=Inventory)
    location_id: str = "start_village"
    created_at: str = ""


class QuestStatus(str, Enum):
    """Quest status state."""
    ACTIVE = "進行中"
    COMPLETED = "已完成"
    FAILED = "已失敗"


@dataclass
class Quest:
    """Quest / Task structure.

    Attributes:
        quest_id: Unique quest identifier.
        title: Quest title.
        description: Detailed objective description.
        reward: Description of reward.
        status: Current status.
        deadline_tick: Expiration tick count (0 if no deadline).
        progress: Progress summary.
    """
    quest_id: str
    title: str
    description: str
    reward: str
    status: QuestStatus = QuestStatus.ACTIVE
    deadline_tick: int = 0
    progress: str = "剛接取"


@dataclass
class Npc:
    """Non-Player Character entity.

    Attributes:
        npc_id: Unique NPC ID.
        name: Name.
        location_id: Current location.
        appearance: Visual description.
        personality: Traits and disposition.
        background: Origin story.
        schedule: Daily routine map (e.g. {"08:00": "Work", "18:00": "Tavern"}).
        affinity: Player affinity map {user_id_str: score}.
    """
    npc_id: str
    name: str
    location_id: str
    appearance: str
    personality: str
    background: str
    schedule: dict[str, str] = field(default_factory=dict)
    affinity: dict[str, int] = field(default_factory=dict)


@dataclass
class Location:
    """World location.

    Attributes:
        location_id: Location identifier.
        name: Display name.
        location_type: Village, Dungeon, Wilderness, etc.
        description: Description of the area.
        npcs_present: List of NPC IDs currently here.
        connected_locations: Adjacent location IDs.
    """
    location_id: str
    name: str
    location_type: str
    description: str
    npcs_present: list[str] = field(default_factory=list)
    connected_locations: list[str] = field(default_factory=list)


@dataclass
class WorldState:
    """Global D&D World State.

    Attributes:
        game_time: Current in-game clock.
        current_tick: Total world ticks elapsed.
        locations: Dictionary of locations {location_id: Location}.
        active_channels: Channel IDs with active D&D sessions.
    """
    game_time: GameTime = field(default_factory=GameTime)
    current_tick: int = 0
    locations: dict[str, Location] = field(default_factory=dict)
    active_channels: list[int] = field(default_factory=list)


@dataclass
class Combatant:
    """Combat participant.

    Attributes:
        name: Combatant name.
        is_player: True if PC, False if monster/enemy.
        user_id: Discord ID if player, else 0.
        hp_max: Max HP.
        hp_current: Current HP.
        ac: Armor Class.
        initiative: Initiative roll result.
        status_effects: Buffs or debuffs.
    """
    name: str
    is_player: bool
    user_id: int = 0
    hp_max: int = 10
    hp_current: int = 10
    ac: int = 10
    initiative: int = 0
    status_effects: list[str] = field(default_factory=list)


@dataclass
class CombatSession:
    """Active combat scene.

    Attributes:
        channel_id: Discord channel ID.
        combatants: List of combatants sorted by initiative.
        current_turn: Index of active combatant.
        round_count: Current combat round number.
        is_active: Whether combat is ongoing.
    """
    channel_id: int
    combatants: list[Combatant] = field(default_factory=list)
    current_turn: int = 0
    round_count: int = 1
    is_active: bool = True
