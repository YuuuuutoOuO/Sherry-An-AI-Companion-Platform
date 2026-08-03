"""mod/dnd/combat.py
===================
Turn-based combat manager for D&D plugin.
"""

from __future__ import annotations

import logging

from mod.dnd.dice import roll
from mod.dnd.models import Combatant, CombatSession

logger = logging.getLogger(__name__)


class CombatManager:
    """Manages active turn-based combat sessions across Discord channels."""

    def __init__(self) -> None:
        self._sessions: dict[int, CombatSession] = {}

    def get_session(self, channel_id: int) -> CombatSession | None:
        """Get active combat session for a channel.

        Args:
            channel_id: Channel ID.

        Returns:
            CombatSession or None.
        """
        return self._sessions.get(channel_id)

    def start_combat(
        self,
        channel_id: int,
        players: list[tuple[int, str, int, int]],  # (user_id, name, hp, ac)
        enemies: list[tuple[str, int, int]],       # (name, hp, ac)
    ) -> CombatSession:
        """Start a new turn-based combat session.

        Args:
            channel_id: Discord Channel ID.
            players: List of (user_id, name, hp, ac).
            enemies: List of (name, hp, ac).

        Returns:
            Newly created CombatSession.
        """
        combatants: list[Combatant] = []

        for uid, name, hp, ac in players:
            init = roll(20)
            combatants.append(
                Combatant(
                    name=name,
                    is_player=True,
                    user_id=uid,
                    hp_max=hp,
                    hp_current=hp,
                    ac=ac,
                    initiative=init,
                )
            )

        for name, hp, ac in enemies:
            init = roll(20)
            combatants.append(
                Combatant(
                    name=name,
                    is_player=False,
                    user_id=0,
                    hp_max=hp,
                    hp_current=hp,
                    ac=ac,
                    initiative=init,
                )
            )

        # Sort by initiative descending
        combatants.sort(key=lambda c: c.initiative, reverse=True)

        session = CombatSession(
            channel_id=channel_id,
            combatants=combatants,
            current_turn=0,
            round_count=1,
            is_active=True,
        )
        self._sessions[channel_id] = session
        return session

    def next_turn(self, channel_id: int) -> tuple[Combatant | None, bool]:
        """Advance combat turn.

        Args:
            channel_id: Channel ID.

        Returns:
            Tuple of (Next active Combatant, whether combat ended).
        """
        session = self.get_session(channel_id)
        if not session or not session.is_active:
            return None, True

        # Check combat end conditions
        players_alive = any(c.is_player and c.hp_current > 0 for c in session.combatants)
        enemies_alive = any(not c.is_player and c.hp_current > 0 for c in session.combatants)

        if not players_alive or not enemies_alive:
            session.is_active = False
            return None, True

        # Move turn index to next living combatant
        num_combatants = len(session.combatants)
        for _ in range(num_combatants):
            session.current_turn = (session.current_turn + 1) % num_combatants
            if session.current_turn == 0:
                session.round_count += 1

            next_c = session.combatants[session.current_turn]
            if next_c.hp_current > 0:
                return next_c, False

        session.is_active = False
        return None, True

    def end_combat(self, channel_id: int) -> None:
        """Force end a combat session.

        Args:
            channel_id: Channel ID.
        """
        if channel_id in self._sessions:
            del self._sessions[channel_id]
