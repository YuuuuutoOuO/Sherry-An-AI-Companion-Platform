"""mod/dnd/cog.py
================
Main Discord Cog Extension for D&D Game Plugin.

Provides slash commands (`/dnd_*`) and channel session listener (`on_message`)
to interact with the AI DM (地下城主) and the D&D engine.
"""

from __future__ import annotations

import logging
import re
from typing import Literal, Any

import discord
from discord import app_commands
from discord.ext import commands

import settings
from handlers.bot_brain import BotBrain, InferenceTask
from handlers.social.utils import simulate_typing_and_send
from mod.dnd import config
from mod.dnd.character import create_character
from mod.dnd.combat import CombatManager
from mod.dnd.dice import advantage_roll, disadvantage_roll, roll, roll_dice
from mod.dnd.inventory import equip_item
from mod.dnd.models import Character
from mod.dnd.storage import (
    load_characters,
    load_world_setting,
    load_world_state,
    save_world_state,
)
from mod.dnd.world_sim import WorldSimEngine

from mod.dnd.views import DndStoryView

logger = logging.getLogger(__name__)

# Maximum number of history entries (user + assistant) kept per channel.
# 6 = 3 rounds of [player action + DM reply], enough context without wasting tokens.
MAX_HISTORY_ROUNDS: int = 6


class DndCog(commands.Cog):
    """D&D AI World Engine Cog."""

    dnd_group = app_commands.Group(name="dnd", description="🐉 D&D 跑團冒險系統")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.combat_mgr = CombatManager()
        self.world_engine = WorldSimEngine(bot)
        # Per-channel rolling conversation history for DM continuity.
        # Format: {channel_id: [{"role": "user"|"assistant", "content": str}, ...]}
        self._channel_histories: dict[int, list[dict[str, str]]] = {}

    # ---------------------------------------------------------------------------
    # Helper: Channel History Management
    # ---------------------------------------------------------------------------

    def _get_history(self, channel_id: int) -> list[dict[str, str]]:
        """Retrieve conversation history for a channel.

        Args:
            channel_id: Discord channel ID.

        Returns:
            List of history dicts with 'role' and 'content' keys.
        """
        return self._channel_histories.get(channel_id, [])

    def _append_history(self, channel_id: int, role: str, content: str) -> None:
        """Append one entry to a channel's history, trimming to MAX_HISTORY_ROUNDS.

        Args:
            channel_id: Discord channel ID.
            role: 'user' or 'assistant'.
            content: Message content.
        """
        if channel_id not in self._channel_histories:
            self._channel_histories[channel_id] = []
        self._channel_histories[channel_id].append({"role": role, "content": content})
        # Keep only the most recent MAX_HISTORY_ROUNDS entries
        if len(self._channel_histories[channel_id]) > MAX_HISTORY_ROUNDS:
            self._channel_histories[channel_id] = self._channel_histories[channel_id][-MAX_HISTORY_ROUNDS:]

    def _clear_history(self, channel_id: int) -> None:
        """Clear conversation history for a channel (e.g. on session stop).

        Args:
            channel_id: Discord channel ID.
        """
        self._channel_histories.pop(channel_id, None)
        logger.info("DnD history cleared for channel %d", channel_id)

    # ---------------------------------------------------------------------------
    # Helper: Build DM System Prompt
    # ---------------------------------------------------------------------------

    def _build_dm_prompt(
        self,
        character: Character | None,
        location_name: str,
        user_action: str,
        history: list[dict[str, str]] | None = None,
    ) -> tuple[str, str]:
        """Assemble DM System Prompt and User Prompt for BotBrain inference.

        Args:
            character: Character performing action (if any).
            location_name: Name of current location.
            user_action: Action text input by player.
            history: Optional rolling conversation history to inject for continuity.

        Returns:
            Tuple of (System Prompt, User Prompt).
        """
        setting = load_world_setting()
        world = load_world_state()

        world_name = setting.get("world_name", "艾瑟蘭大陸")
        era = setting.get("era", "裂隙紀元 1247 年")
        magic_level = setting.get("magic_level", "中魔")
        directive = setting.get("dm_core_directive", "你是地下城主 (DM)。")

        char_summary = "無角色卡（旁觀者）"
        if character:
            char_summary = (
                f"【冒險者卡片】\n"
                f"名字：{character.name}（種族：{character.race} / 職業：{character.character_class}）\n"
                f"等級：Lv.{character.level} | HP：{character.hp_current}/{character.hp_max} | AC：{character.ac}\n"
                f"屬性：力量{character.stats.strength}(+{character.stats.modifier('strength')}) "
                f"敏捷{character.stats.dexterity}(+{character.stats.modifier('dexterity')}) "
                f"體質{character.stats.constitution}(+{character.stats.modifier('constitution')}) "
                f"智力{character.stats.intelligence}(+{character.stats.modifier('intelligence')}) "
                f"感知{character.stats.wisdom}(+{character.stats.modifier('wisdom')}) "
                f"魅力{character.stats.charisma}(+{character.stats.modifier('charisma')})"
            )

        location_key = character.location_id if character else "start_village"
        loc = world.locations.get(location_key)
        loc_desc = loc.description if loc else "未知區域"
        npcs = ", ".join(loc.npcs_present) if loc and loc.npcs_present else "無"

        # Build conversation history block if available
        history_block = ""
        if history:
            lines = []
            for entry in history:
                role_label = "[玩家行動]" if entry["role"] == "user" else "[DM 敘事]"
                # Truncate very long entries to avoid bloating the prompt
                content_preview = entry["content"][:300] + "..." if len(entry["content"]) > 300 else entry["content"]
                lines.append(f"{role_label} {content_preview}")
            history_block = (
                "\n\n【近期對話記錄（必須嚴格依照此記錄繼續當前場景，不得跳脫或重新開始）】\n"
                + "\n".join(lines)
            )

        system_prompt = (
            f"{directive}\n\n"
            f"【世界基本資訊】\n"
            f"世界名稱：{world_name} | 紀元：{era} | 魔法等級：{magic_level}\n"
            f"目前遊戲時間：{world.game_time.to_string()}\n"
            f"當前地點：{location_name}（{loc_desc}）\n"
            f"在場 NPC：{npcs}\n\n"
            f"{char_summary}\n\n"
            "【DM 敘事法則】：\n"
            "1. 必須以精緻、沉浸且具有奇幻感的敘事風格回覆。\n"
            "2. 如果玩家行動需要檢定（如力量、敏捷、說服、觀察等），請在敘事中提示玩家進行相關能力檢定與 DC 難度，格式必須為【能力檢定 (DC 數字)】，例如【力量檢定 (DC 10)】或【感知檢定 (DC 12)】。\n"
            "3. 保持語氣客觀中立，並創造引人入勝的情景與描繪。\n"
            "4. 絕對不可以跳出或忽略【近期對話記錄】中正在進行的場景（如戰鬥、對話、探索），必須在原場景上繼續延伸。"
            f"{history_block}"
        )

        user_prompt = f"玩家行動：{user_action}"
        return system_prompt, user_prompt

    # ---------------------------------------------------------------------------
    # Event Listener: Session Mode Message Interceptor
    # ---------------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Intercept messages in channels where D&D Session Mode is enabled.

        Args:
            message: Discord Message.
        """
        # Ignore bot messages or empty content
        if message.author.bot or not message.content:
            return

        # Ignore slash commands or prefix commands
        if message.content.startswith("/") or message.content.startswith("!"):
            return

        world = load_world_state()
        if message.channel.id not in world.active_channels:
            return

        # Channel has active D&D session -> process message as player action
        characters = load_characters()
        char = characters.get(message.author.id)

        brain: Any = self.bot.get_cog("BotBrain")
        if not brain:
            return

        location_name = "晨風村"
        if char and char.location_id in world.locations:
            location_name = world.locations[char.location_id].name

        # Add loading reaction to acknowledge receipt of D&D action
        try:
            await message.add_reaction("⏳")
        except Exception:
            pass

        # Record player action into history BEFORE inference
        channel_id = message.channel.id
        self._append_history(channel_id, "user", message.content)
        history = self._get_history(channel_id)

        sys_prompt, user_prompt = self._build_dm_prompt(
            char, location_name, message.content, history=history[:-1]  # exclude the entry we just added
        )

        async def _on_dm_reply(reply: str) -> None:
            clean_reply = reply.strip()
            try:
                if clean_reply:
                    # Record DM reply into history for next turn continuity
                    self._append_history(channel_id, "assistant", clean_reply)

                    embed = discord.Embed(
                        title="🎲 艾瑟蘭大陸 — DM 敘事推演",
                        description=clean_reply,
                        color=discord.Color.dark_green(),
                    )
                    if char:
                        embed.set_author(name=f"冒險者：{char.name} (Lv.{char.level} {char.character_class})")
                    else:
                        embed.set_author(name=f"冒險者：{message.author.display_name}")

                    story_view = DndStoryView(bot=self.bot, text=clean_reply)
                    await message.channel.send(embed=embed, view=story_view)
            except Exception as exc:
                logger.error("Failed to send D&D action reply with view: %s", exc)
            finally:
                # Remove loading reaction once processing is complete
                try:
                    if self.bot.user:
                        await message.remove_reaction("⏳", self.bot.user)
                except Exception:
                    pass

        task = InferenceTask(
            priority=config.DEFAULT_PRIORITY,
            system_prompt=sys_prompt,
            user_prompt=user_prompt,
            callback=_on_dm_reply,
            model=config.DEFAULT_MODEL or getattr(settings, "PRIMARY_MODEL", "lms:gemma-4"),
            task_id=f"dnd-session-{message.author.id}-{int(message.created_at.timestamp())}",
        )
        await brain.enqueue_task(task)

    # ---------------------------------------------------------------------------
    # /dnd Subcommand Group
    # ---------------------------------------------------------------------------

    @dnd_group.command(name="join", description="🎲 創建你的 D&D 冒險者角色卡")
    @app_commands.choices(
        race=[
            app_commands.Choice(name="人類 (+全屬性 +1)", value="人類"),
            app_commands.Choice(name="精靈 (+敏捷 +2)", value="精靈"),
            app_commands.Choice(name="矮人 (+體質 +2)", value="矮人"),
            app_commands.Choice(name="半身人", value="半身人"),
            app_commands.Choice(name="半獸人 (+力量 +2)", value="半獸人"),
        ],
        character_class=[
            app_commands.Choice(name="戰士 (高 HP / 近戰好手)", value="戰士"),
            app_commands.Choice(name="法師 (魔法攻擊 / 知識強者)", value="法師"),
            app_commands.Choice(name="遊俠 (遠程射手 / 森林追蹤)", value="遊俠"),
            app_commands.Choice(name="盜賊 (高敏捷 / 技巧與隱蔽)", value="盜賊"),
            app_commands.Choice(name="牧師 (神聖治療 / 輔助戰鬥)", value="牧師"),
        ],
    )
    async def dnd_join(
        self,
        interaction: discord.Interaction,
        race: app_commands.Choice[str],
        character_class: app_commands.Choice[str],
        name: str,
    ) -> None:
        """Create a new D&D character."""
        await interaction.response.defer(ephemeral=False)

        uid = interaction.user.id
        char = create_character(
            user_id=uid,
            name=name,
            race=race.value,
            character_class=character_class.value,
        )

        embed = discord.Embed(
            title=f"⚔️ 冒險者誕生：{char.name}",
            description=f"歡迎來到艾瑟蘭大陸！你成功創建了 **{char.race} {char.character_class}**。",
            color=discord.Color.gold(),
        )
        embed.add_field(name="❤️ 生命值 (HP)", value=f"{char.hp_current} / {char.hp_max}", inline=True)
        embed.add_field(name="🛡️ 防禦等級 (AC)", value=f"{char.ac}", inline=True)
        embed.add_field(name="⭐ 等級", value=f"Lv.{char.level} (XP: {char.experience})", inline=True)

        stats_str = (
            f"💪 力量: {char.stats.strength} (+{char.stats.modifier('strength')})\n"
            f"🏃 敏捷: {char.stats.dexterity} (+{char.stats.modifier('dexterity')})\n"
            f"🛡️ 體質: {char.stats.constitution} (+{char.stats.modifier('constitution')})\n"
            f"🧠 智力: {char.stats.intelligence} (+{char.stats.modifier('intelligence')})\n"
            f"👁️ 感知: {char.stats.wisdom} (+{char.stats.modifier('wisdom')})\n"
            f"✨ 魅力: {char.stats.charisma} (+{char.stats.modifier('charisma')})"
        )
        embed.add_field(name="📊 核心屬性", value=stats_str, inline=False)
        embed.set_footer(text="提示：使用 /dnd session start 可開啟本頻道的自然打字對話模式！")

        await interaction.followup.send(embed=embed)

    @dnd_group.command(name="session", description="🏰 開啟或關閉本頻道的 D&D 自動遊戲模式")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="▶️ 開啟 (本頻道打字自動視為 D&D 行動)", value="start"),
            app_commands.Choice(name="⏹️ 關閉 (恢復一般聊天)", value="stop"),
        ]
    )
    async def dnd_session(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
    ) -> None:
        """Toggle D&D session mode for channel."""
        world = load_world_state()
        cid = interaction.channel_id
        if cid is None:
            await interaction.response.send_message("❌ 無法取得當前頻道 ID！", ephemeral=True)
            return

        if action.value == "start":
            if cid not in world.active_channels:
                world.active_channels.append(cid)
                save_world_state(world)
            await interaction.response.send_message(
                "🏰 **【D&D 遊戲模式已開啟】**\n在本頻道輸入的任何文字都將自動視為冒險行動！由地下城主 (DM) 為您敘事。",
                ephemeral=False,
            )
        else:
            if cid in world.active_channels:
                world.active_channels.remove(cid)
                save_world_state(world)
            # Clear history so next session starts fresh
            self._clear_history(cid)
            await interaction.response.send_message(
                "🕊️ **【D&D 遊戲模式已關閉】**\n頻道已恢復為橘雪莉一般聊天模式。對話歷史已清除。",
                ephemeral=False,
            )

    @dnd_group.command(name="action", description="📜 執行冒險行動（向 DM 提出動作描述）")
    async def dnd_action(self, interaction: discord.Interaction, action_text: str) -> None:
        """Perform action in D&D world."""
        await interaction.response.defer(ephemeral=False)

        uid = interaction.user.id
        characters = load_characters()
        char = characters.get(uid)

        brain: Any = self.bot.get_cog("BotBrain")
        if not brain:
            await interaction.followup.send("⚠️ 系統錯誤：無法連接 BotBrain 大腦伺服器。")
            return

        world = load_world_state()
        loc_name = "晨風村"
        if char and char.location_id in world.locations:
            loc_name = world.locations[char.location_id].name

        channel_id = interaction.channel_id or 0
        self._append_history(channel_id, "user", action_text)
        history = self._get_history(channel_id)

        sys_prompt, user_prompt = self._build_dm_prompt(
            char, loc_name, action_text, history=history[:-1]
        )

        async def _on_action_reply(reply: str) -> None:
            clean_reply = reply.strip()
            if not clean_reply:
                return
            self._append_history(channel_id, "assistant", clean_reply)
            short_title = action_text if len(action_text) <= 20 else action_text[:20] + "..."
            embed = discord.Embed(
                title=f"🎲 冒險行動：{short_title}",
                description=clean_reply,
                color=discord.Color.dark_purple(),
            )
            if char:
                embed.set_author(name=f"{char.name} (Lv.{char.level} {char.character_class})")
            story_view = DndStoryView(bot=self.bot, text=clean_reply)
            await interaction.followup.send(embed=embed, view=story_view)

        task = InferenceTask(
            priority=config.DEFAULT_PRIORITY,
            system_prompt=sys_prompt,
            user_prompt=user_prompt,
            callback=_on_action_reply,
            model=config.DEFAULT_MODEL or getattr(settings, "PRIMARY_MODEL", "lms:gemma-4"),
            task_id=f"dnd-action-{uid}-{int(interaction.created_at.timestamp())}",
        )
        await brain.enqueue_task(task)

    @dnd_group.command(name="roll", description="🎲 進行 D&D 擲骰判定 (支援 d20, d6, 優勢/劣勢)")
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="一般擲骰", value="normal"),
            app_commands.Choice(name="優勢 (取高)", value="advantage"),
            app_commands.Choice(name="劣勢 (取低)", value="disadvantage"),
        ]
    )
    async def dnd_roll(
        self,
        interaction: discord.Interaction,
        dice: str = "d20",
        modifier: int = 0,
        mode: app_commands.Choice[str] | None = None,
    ) -> None:
        """Roll dice."""
        mode_val = mode.value if mode else "normal"
        match = re.match(r"^(\d*)d(\d+)$", dice.lower().strip())

        if not match:
            await interaction.response.send_message("❌ 擲骰格式錯誤！請使用如 `d20` 或 `2d6` 格式。", ephemeral=True)
            return

        count = int(match.group(1)) if match.group(1) else 1
        sides = int(match.group(2))

        if mode_val == "advantage":
            d1, d2, chosen = advantage_roll(sides)
            total = chosen + modifier
            desc = f"🎲 優勢擲骰 [{dice}]: 第一次 `{d1}`，第二次 `{d2}` → 取高 `{chosen}` + 修正 `{modifier:+d}` = **{total}**"
        elif mode_val == "disadvantage":
            d1, d2, chosen = disadvantage_roll(sides)
            total = chosen + modifier
            desc = f"🎲 劣勢擲骰 [{dice}]: 第一次 `{d1}`，第二次 `{d2}` → 取低 `{chosen}` + 修正 `{modifier:+d}` = **{total}**"
        else:
            rolls = roll_dice(count, sides)
            raw_sum = sum(rolls)
            total = raw_sum + modifier
            rolls_str = ", ".join(str(r) for r in rolls)
            desc = f"🎲 擲骰 [{count}d{sides}]: ({rolls_str}) = `{raw_sum}` + 修正 `{modifier:+d}` = **{total}**"

        embed = discord.Embed(
            title="🎲 擲骰結果",
            description=desc,
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed)

    @dnd_group.command(name="status", description="📊 查看你的角色卡狀態")
    async def dnd_status(self, interaction: discord.Interaction) -> None:
        """View character status."""
        uid = interaction.user.id
        characters = load_characters()
        char = characters.get(uid)

        if not char:
            await interaction.response.send_message("❌ 你還沒有創建角色！請先使用 `/dnd join` 創角。", ephemeral=True)
            return

        world = load_world_state()
        loc_name = world.locations.get(char.location_id, None)
        loc_display = loc_name.name if loc_name else char.location_id

        embed = discord.Embed(
            title=f"📜 角色卡：{char.name}",
            description=f"**{char.race} {char.character_class}** | 當前位置：📍 {loc_display}",
            color=discord.Color.green(),
        )
        embed.add_field(name="❤️ 生命值 (HP)", value=f"{char.hp_current} / {char.hp_max}", inline=True)
        embed.add_field(name="🛡️ 防禦等級 (AC)", value=f"{char.ac}", inline=True)
        embed.add_field(name="⭐ 等級 / 經驗", value=f"Lv.{char.level} ({char.experience} XP)", inline=True)

        stats_str = (
            f"💪 力量: {char.stats.strength} (+{char.stats.modifier('strength')})\n"
            f"🏃 敏捷: {char.stats.dexterity} (+{char.stats.modifier('dexterity')})\n"
            f"🛡️ 體質: {char.stats.constitution} (+{char.stats.modifier('constitution')})\n"
            f"🧠 智力: {char.stats.intelligence} (+{char.stats.modifier('intelligence')})\n"
            f"👁️ 感知: {char.stats.wisdom} (+{char.stats.modifier('wisdom')})\n"
            f"✨ 魅力: {char.stats.charisma} (+{char.stats.modifier('charisma')})"
        )
        embed.add_field(name="📊 核心能力值", value=stats_str, inline=False)
        embed.add_field(name="💰 錢包", value=f"🪙 {char.inventory.gold} 金幣 | 🥈 {char.inventory.silver} 銀幣", inline=False)

        await interaction.response.send_message(embed=embed)

    @dnd_group.command(name="inventory", description="🎒 查看背包內的裝備與物品")
    async def dnd_inventory(self, interaction: discord.Interaction) -> None:
        """View character inventory."""
        uid = interaction.user.id
        characters = load_characters()
        char = characters.get(uid)

        if not char:
            await interaction.response.send_message("❌ 你還沒有創建角色！請使用 `/dnd join` 創角。", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"🎒 {char.name} 的冒險背包",
            color=discord.Color.orange(),
        )

        if not char.inventory.items:
            embed.description = "背包空空如也。"
        else:
            item_lines = []
            for it in char.inventory.items:
                eq_tag = " [⚔️ 裝備中]" if it.equipped else ""
                item_lines.append(f"• **{it.name}** ({it.rarity.value} {it.item_type}){eq_tag} - {it.description}")
            embed.description = "\n".join(item_lines)

        embed.set_footer(text=f"金幣: {char.inventory.gold} | 銀幣: {char.inventory.silver} | 銅幣: {char.inventory.copper}")
        await interaction.response.send_message(embed=embed)

    @dnd_group.command(name="world", description="🌍 查看世界時間與勢力狀態")
    async def dnd_world(self, interaction: discord.Interaction) -> None:
        """View global D&D world state."""
        setting = load_world_setting()
        world = load_world_state()

        embed = discord.Embed(
            title=f"🌍 世界狀態：{setting.get('world_name', '艾瑟蘭大陸')}",
            description=f"紀元：**{setting.get('era', '裂隙紀元')}**",
            color=discord.Color.teal(),
        )
        embed.add_field(name="⏰ 遊戲時間", value=world.game_time.to_string(), inline=False)
        embed.add_field(name="⏳ 累計世界 Tick", value=f"{world.current_tick} ticks", inline=True)
        embed.add_field(name="🏰 模式啟動頻道數", value=f"{len(world.active_channels)} 個頻道", inline=True)

        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    """Setup function for cog loading."""
    await bot.add_cog(DndCog(bot))
