"""mod/dnd/views.py
=================
Discord Interactive UI Views & Components for D&D Plugin.

Provides interactive buttons, select menus, and modals to eliminate
manual command typing during gameplay.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

import discord
from discord import ui

import settings
from handlers.bot_brain import InferenceTask
from mod.dnd.character import create_character
from mod.dnd.dice import roll
from mod.dnd.inventory import equip_item
from mod.dnd.storage import load_characters, load_world_state

if TYPE_CHECKING:
    from handlers.bot_brain import BotBrain

logger = logging.getLogger(__name__)

# Regular expression to detect check prompts like 【感知檢定 (DC 10)】 or 【力量檢定】
CHECK_PATTERN = re.compile(r"【(力量|敏捷|體質|智力|感知|魅力)檢定(?:\s*\([Dd][Cc]\s*(\d+)\))?】")

ABILITY_MAP: dict[str, str] = {
    "力量": "strength",
    "敏捷": "dexterity",
    "體質": "constitution",
    "智力": "intelligence",
    "感知": "wisdom",
    "魅力": "charisma",
}


def extract_checks(text: str) -> list[tuple[str, int]]:
    """Extract check requirements from DM text.

    Args:
        text: Raw DM narrative text.

    Returns:
        List of tuples: (ability_name, dc_target). DC is 10 if not specified.
    """
    matches = CHECK_PATTERN.findall(text)
    results: list[tuple[str, int]] = []
    seen: set[str] = set()

    for ability_name, dc_str in matches:
        if ability_name not in seen:
            seen.add(ability_name)
            dc = int(dc_str) if dc_str else 10
            results.append((ability_name, dc))

    return results[:4]  # Limit to max 4 check buttons per view for UI cleanliness


class CheckRollButton(ui.Button["DndStoryView"]):
    """Button for one-click ability check roll."""

    def __init__(self, ability_name: str, dc: int) -> None:
        super().__init__(
            style=discord.ButtonStyle.primary,
            label=f"🎲 進行{ability_name}檢定 (DC {dc})",
            custom_id=f"dnd_check_{ability_name}_{dc}",
        )
        self.ability_name = ability_name
        self.dc = dc

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle button click."""
        await interaction.response.defer(ephemeral=False)

        uid = interaction.user.id
        characters = load_characters()
        char = characters.get(uid)

        # Calculate modifier
        mod = 0
        attr_key = ABILITY_MAP.get(self.ability_name, "")
        if char and attr_key:
            mod = char.stats.modifier(attr_key)

        d20_roll = roll(20)
        total = d20_roll + mod
        is_success = total >= self.dc

        result_tag = "✅ 成功" if is_success else "❌ 失敗"
        if d20_roll == 20:
            result_tag = "🌟 大成功 (Nat 20)!"
        elif d20_roll == 1:
            result_tag = "💀 大失敗 (Nat 1)!"

        char_name = char.name if char else interaction.user.display_name
        action_desc = (
            f"冒險者 {char_name} 進行了【{self.ability_name}檢定】！\n"
            f"🎲 擲骰 d20({d20_roll}) + 修正({mod:+d}) = **{total}** (目標 DC {self.dc}) → **{result_tag}**"
        )

        # Trigger BotBrain DM narrative update for this roll result
        bot = interaction.client
        brain: BotBrain | None = bot.get_cog("BotBrain")

        if not brain:
            await interaction.followup.send(action_desc)
            return

        world = load_world_state()
        loc_name = "晨風村"
        if char and char.location_id in world.locations:
            loc_name = world.locations[char.location_id].name

        # Access cog method to build DM prompt if available
        dnd_cog = bot.get_cog("DndCog")
        channel_id: int = interaction.channel_id or 0

        if dnd_cog and hasattr(dnd_cog, "_build_dm_prompt"):
            # Append roll result to history so DM knows what happened
            dnd_cog._append_history(channel_id, "user", action_desc)
            history = dnd_cog._get_history(channel_id)
            sys_prompt, user_prompt = dnd_cog._build_dm_prompt(
                char, loc_name, action_desc, history=history[:-1]
            )
        else:
            sys_prompt = f"你是 D&D 地下城主。目前玩家進行了判定：{action_desc}。請描繪結果。"
            user_prompt = action_desc
            dnd_cog = None
            channel_id = 0

        async def _on_dm_reply(reply: str) -> None:
            # Save DM reply to history for next turn
            if dnd_cog and channel_id:
                dnd_cog._append_history(channel_id, "assistant", reply.strip())
            new_view = DndStoryView(bot=bot, text=reply)
            embed = discord.Embed(
                title=f"🎲 檢定結果：{self.ability_name} (DC {self.dc})",
                description=f"{action_desc}\n\n---\n{reply}",
                color=discord.Color.green() if is_success else discord.Color.red(),
            )
            await interaction.followup.send(embed=embed, view=new_view)

        task = InferenceTask(
            priority=getattr(settings, "PRIORITY_TAG_RESPONSE", 1),
            system_prompt=sys_prompt,
            user_prompt=user_prompt,
            callback=_on_dm_reply,
            model=settings.PRIMARY_MODEL,
            task_id=f"dnd-roll-btn-{uid}-{int(interaction.created_at.timestamp())}",
        )
        await brain.enqueue_task(task)

        # Disable this button once clicked to prevent double-roll
        self.disabled = True
        if self.view is not None:
            try:
                await interaction.message.edit(view=self.view)
            except Exception:
                pass


class CustomActionModal(ui.Modal, title="✍️ 自訂冒險行動"):
    """Modal for typing custom action without slash command."""

    action_text = ui.TextInput(
        label="你想採取什麼行動？",
        style=discord.TextStyle.paragraph,
        placeholder="例如：我嘗試觀察老鐵匠的表情，看看他有沒有隱瞞什麼...",
        required=True,
        max_length=300,
    )

    def __init__(self, bot: discord.Client) -> None:
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Handle modal submission."""
        await interaction.response.defer(ephemeral=False)

        uid = interaction.user.id
        characters = load_characters()
        char = characters.get(uid)
        world = load_world_state()

        loc_name = "晨風村"
        if char and char.location_id in world.locations:
            loc_name = world.locations[char.location_id].name

        brain: BotBrain | None = self.bot.get_cog("BotBrain")
        if not brain:
            await interaction.followup.send("⚠️ BotBrain 大腦未上線。")
            return

        dnd_cog = self.bot.get_cog("DndCog")
        channel_id: int = interaction.channel_id or 0

        if dnd_cog and hasattr(dnd_cog, "_build_dm_prompt"):
            dnd_cog._append_history(channel_id, "user", self.action_text.value)
            history = dnd_cog._get_history(channel_id)
            sys_prompt, user_prompt = dnd_cog._build_dm_prompt(
                char, loc_name, self.action_text.value, history=history[:-1]
            )
        else:
            sys_prompt = "你是 D&D 地下城主。"
            user_prompt = self.action_text.value
            dnd_cog = None
            channel_id = 0

        async def _on_reply(reply: str) -> None:
            if dnd_cog and channel_id:
                dnd_cog._append_history(channel_id, "assistant", reply.strip())
            new_view = DndStoryView(bot=self.bot, text=reply)
            embed = discord.Embed(
                title=f"📜 冒險行動：{self.action_text.value[:20]}...",
                description=reply,
                color=discord.Color.purple(),
            )
            await interaction.followup.send(embed=embed, view=new_view)

        task = InferenceTask(
            priority=getattr(settings, "PRIORITY_TAG_RESPONSE", 1),
            system_prompt=sys_prompt,
            user_prompt=user_prompt,
            callback=_on_reply,
            model=settings.PRIMARY_MODEL,
            task_id=f"dnd-modal-{uid}-{int(interaction.created_at.timestamp())}",
        )
        await brain.enqueue_task(task)


class DndStoryView(ui.View):
    """View attached to DM narrative messages containing action buttons and utility controls."""

    def __init__(self, bot: discord.Client, text: str = "") -> None:
        super().__init__(timeout=86400)  # 24 hour timeout for UI buttons
        self.bot = bot

        # Extract and attach dynamic check buttons
        checks = extract_checks(text)
        for ability_name, dc in checks:
            self.add_item(CheckRollButton(ability_name, dc))

    @ui.button(label="✍️ 自訂行動", style=discord.ButtonStyle.secondary, row=1)
    async def custom_action_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        """Open modal for custom action."""
        await interaction.response.send_modal(CustomActionModal(self.bot))

    @ui.button(label="📊 角色狀態", style=discord.ButtonStyle.secondary, row=1)
    async def status_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        """Show character status card."""
        uid = interaction.user.id
        characters = load_characters()
        char = characters.get(uid)

        if not char:
            await interaction.response.send_message("❌ 你還沒有冒險者角色！請使用 `/dnd_join` 創角。", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"📜 角色卡：{char.name}",
            description=f"**{char.race} {char.character_class}** | 等級：Lv.{char.level}",
            color=discord.Color.green(),
        )
        embed.add_field(name="❤️ 生命值", value=f"{char.hp_current}/{char.hp_max}", inline=True)
        embed.add_field(name="🛡️ 防禦等級", value=f"{char.ac}", inline=True)
        embed.add_field(name="💰 金幣", value=f"{char.inventory.gold} 金幣", inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="🎒 查看背包", style=discord.ButtonStyle.secondary, row=1)
    async def inventory_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        """Show inventory view."""
        uid = interaction.user.id
        characters = load_characters()
        char = characters.get(uid)

        if not char:
            await interaction.response.send_message("❌ 你還沒有冒險者角色！請使用 `/dnd_join` 創角。", ephemeral=True)
            return

        items = char.inventory.items
        if not items:
            await interaction.response.send_message("🎒 背包內沒有物品。", ephemeral=True)
            return

        item_descs = [f"• **{it.name}** ({it.item_type})" + (" [已裝備]" if it.equipped else "") for it in items]
        embed = discord.Embed(
            title=f"🎒 {char.name} 的冒險背包",
            description="\n".join(item_descs),
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
