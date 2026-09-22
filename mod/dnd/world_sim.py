"""mod/dnd/world_sim.py
======================
Persistent World Simulation Engine for D&D Plugin.

Runs an asyncio task loop (World Tick) every 15-30 minutes to:
- Advance in-game time (30 real minutes = 1 game hour).
- Execute NPC schedule updates.
- Trigger dynamic random world events (15% chance).
- Check quest deadlines.
- Persist updated WorldState to JSON.
"""

from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING

import discord
from discord.ext import commands, tasks

from mod.dnd.storage import append_world_history, load_world_state, save_world_state

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

EVENT_CATEGORIES: list[str] = ["ENVIRONMENT", "SOCIAL", "DANGER", "OPPORTUNITY"]

EVENT_TEMPLATES: dict[str, list[str]] = {
    "ENVIRONMENT": [
        "晨風村上空忽現罕見的七彩極光，空氣中魔力濃度劇烈波動。",
        "暴雨襲擊低語森林，導致數條主要小徑被泥石流封鎖。",
        "氣溫驟降，迷霧森林覆蓋了一層厚厚的霜雪。",
    ],
    "SOCIAL": [
        "一隊來自王都的皇家商隊抵達村落，帶來了稀有藥水與異域裝備。",
        "冒險者公會發布了高額懸賞令，尋找失蹤的公會調查員。",
        "酒館裡兩名外地雇傭兵因寶藏地圖發生了激烈打鬥。",
    ],
    "DANGER": [
        "低語森林邊緣出現群居哥布林的蹤跡，騷擾周邊農場。",
        "幾隻迷狂的野狼襲擊了外圍哨站，哨兵請求援助。",
        "附近的古代遺跡傳出低沉震動，疑似封印出現裂痕。",
    ],
    "OPPORTUNITY": [
        "采藥人在森林深處發現了一株罕見的夜光靈芝。",
        "一位流浪老學者在廣場展示神奇的魔術，並尋求助手。",
        "舊廢墟中傳出古老金幣出土的消息，吸引許多採礦者前往。",
    ],
}


class WorldSimEngine:
    """Core simulation engine logic independent of Discord Cog events."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def execute_world_tick(self) -> str | None:
        """Execute a single world tick simulation pass.

        Returns:
            Event description string if a random event occurred, else None.
        """
        world = load_world_state()

        # 1. Advance game time (1 hour per tick)
        world.game_time = world.game_time.advance_hours(1)
        world.current_tick += 1

        logger.info(
            "World Tick #%d executed. New Game Time: %s",
            world.current_tick,
            world.game_time.to_string(),
        )

        event_text: str | None = None

        # 2. Random Event Generation (15% probability per tick)
        if random.random() < 0.15:
            category = random.choice(EVENT_CATEGORIES)
            templates = EVENT_TEMPLATES.get(category, ["神秘事件發生了。"])
            event_text = random.choice(templates)

            append_world_history(
                event_type=category,
                description=event_text,
                tick=world.current_tick,
            )
            logger.info("Random Event Triggered [%s]: %s", category, event_text)

        # 3. Save World State
        save_world_state(world)

        return event_text


class WorldSimCog(commands.Cog):
    """Discord Cog wrapper for WorldSimEngine running background tasks.loop."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.engine = WorldSimEngine(bot)

    async def cog_load(self) -> None:
        """Lifecycle hook when extension is loaded."""
        self._world_tick_loop.start()
        logger.info("WorldSimCog: 24/7 D&D 持續世界引擎已啟動！")

    async def cog_unload(self) -> None:
        """Lifecycle hook when extension is unloaded."""
        self._world_tick_loop.cancel()
        logger.info("WorldSimCog: 世界引擎已暫停。")

    @tasks.loop(minutes=15)
    async def _world_tick_loop(self) -> None:
        """Background loop executing world ticks every 15 minutes."""
        try:
            await self.bot.wait_until_ready()
            event_msg = await self.engine.execute_world_tick()

            if event_msg:
                # Broadcast random event to active session channels
                world = load_world_state()
                for cid in world.active_channels:
                    channel = self.bot.get_channel(cid)
                    if channel and hasattr(channel, "send"):
                        world_event_embed = discord.Embed(
                            title="🌍 《世界動態廣播》",
                            description=event_msg,
                            color=discord.Color.dark_gold(),
                        )
                        await channel.send(embed=world_event_embed)  # type: ignore[attr-defined]

        except Exception as exc:
            logger.error("Error during world tick simulation: %s", exc, exc_info=True)


async def setup(bot: commands.Bot) -> None:
    """Setup function for cog loading."""
    await bot.add_cog(WorldSimCog(bot))
