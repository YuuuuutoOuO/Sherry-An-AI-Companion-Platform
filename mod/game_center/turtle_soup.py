"""
mod.game_center.turtle_soup
===========================
Situation Puzzle (Turtle Soup) game engine.
"""

from __future__ import annotations

import random
import uuid

from mod.game_center.models import GameResultStatus, TurtleSoupState

SOUP_PRESETS = [
    {
        "title": "海邊的餐館",
        "surface": "一個人在海邊的餐館點了一碗海龜湯，喝了一口之後，他走出餐館跳海自殺了。為什麼？",
        "truth": "這個人過去曾遭遇海難，當時在荒島上同伴煮了一碗湯給他喝，並告訴他是海龜湯，他才得以活下來。多年後他真正喝到了真正的海龜湯，發現味道完全不同，才明白當年同伴煮給他喝的其實是死去的同伴的肉，絕望悲痛之下選擇自殺。",
    },
    {
        "title": "關燈之後",
        "surface": "一個人關上了家裡的電燈，上了床睡覺。第二天早上看新聞後，他痛苦地跳樓了。為什麼？",
        "truth": "這個人是燈塔看守員。他昨晚誤把燈塔的大燈當成家裡的燈給關掉了，導致夜間過往的輪船撞上暗礁沉沒。早上看到慘重船難新聞後，自責不已而跳樓。",
    },
    {
        "title": "照相館的失誤",
        "surface": "一個人去照相館拿洗好的照片，看完照片後他露出恐怖的笑容並報警了。為什麼？",
        "truth": "照片裡拍下了他家裡天花板上的鏡像，赫然發現床底下竟然躲著一個手持兇器的陌生人！",
    },
]


class TurtleSoupEngine:
    """Engine managing Situation Puzzle rounds."""

    def __init__(self) -> None:
        self._active_games: dict[str, TurtleSoupState] = {}

    def start_game(self, preset_index: int | None = None) -> TurtleSoupState:
        """Start a new Turtle Soup game round."""
        game_id = str(uuid.uuid4())[:8]
        if preset_index is not None and 0 <= preset_index < len(SOUP_PRESETS):
            chosen = SOUP_PRESETS[preset_index]
        else:
            chosen = random.choice(SOUP_PRESETS)

        state = TurtleSoupState(
            game_id=game_id,
            title=chosen["title"],
            surface_story=chosen["surface"],
            truth=chosen["truth"],
            status=GameResultStatus.ONGOING,
            sherry_reply=f"哼哼！經典湯面『{chosen['title']}』～向本名偵探提問吧！只能問可以用『是』、『否』或『無關』回答的問題喔 🍊✨",
        )
        self._active_games[game_id] = state
        return state

    def get_game(self, game_id: str) -> TurtleSoupState | None:
        """Retrieve active Turtle Soup game state."""
        return self._active_games.get(game_id)
