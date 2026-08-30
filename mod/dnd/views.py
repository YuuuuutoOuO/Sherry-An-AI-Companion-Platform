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
from mod.dnd import config
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
        brain: Any = getattr(bot, "get_cog", lambda _: None)("BotBrain")

        if not brain:
            await interaction.followup.send(action_desc)
            return

        world = load_world_state()
        loc_name = "晨風村"
        if char and char.location_id in world.locations:
            loc_name = world.locations[char.location_id].name

        # Access cog method to build DM prompt if available
        dnd_cog: Any = getattr(bot, "get_cog", lambda _: None)("DndCog")
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
            priority=config.DEFAULT_PRIORITY,
            system_prompt=sys_prompt,
            user_prompt=user_prompt,
            callback=_on_dm_reply,
            model=config.DEFAULT_MODEL or getattr(settings, "PRIMARY_MODEL", "lms:gemma-4"),
            task_id=f"dnd-roll-btn-{uid}-{int(interaction.created_at.timestamp())}",
        )
        await brain.enqueue_task(task)

        # Disable this button once clicked to prevent double-roll
        self.disabled = True
        if self.view is not None and interaction.message is not None:
            try:
                await interaction.message.edit(view=self.view)
            except Exception:
                pass


class CustomActionModal(ui.Modal, title="✍️ 自訂冒險行動"):
    """Modal for typing custom action without slash command."""

    action_text: Any = ui.TextInput(
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

        brain: Any = getattr(self.bot, "get_cog", lambda _: None)("BotBrain")
        if not brain:
            await interaction.followup.send("⚠️ BotBrain 大腦未上線。")
            return

        dnd_cog: Any = getattr(self.bot, "get_cog", lambda _: None)("DndCog")
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
            priority=config.DEFAULT_PRIORITY,
            system_prompt=sys_prompt,
            user_prompt=user_prompt,
            callback=_on_reply,
            model=config.DEFAULT_MODEL or getattr(settings, "PRIMARY_MODEL", "lms:gemma-4"),
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
            await interaction.response.send_message("❌ 你還沒有冒險者角色！請使用 `/dnd` 面板創角。", ephemeral=True)
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


class DndCreateModal(ui.Modal, title="⚔️ 創建 D&D 冒險者角色卡"):
    """D&D 創角彈出表單。"""

    name_input: Any = ui.TextInput(
        label="冒險者名字",
        placeholder="例如 亞瑟、莉莉絲、格羅姆",
        min_length=1,
        max_length=20,
        required=True,
    )
    race_input: Any = ui.TextInput(
        label="種族 (人類/精靈/矮人/半身人/半獸人)",
        placeholder="人類 (+全屬性) / 精靈 (+敏捷) / 矮人 (+體質) / 半獸人 (+力量)",
        default="人類",
        min_length=2,
        max_length=20,
        required=True,
    )
    class_input: Any = ui.TextInput(
        label="職業 (戰士/法師/遊俠/盜賊/牧師)",
        placeholder="戰士 (近戰) / 法師 (法術) / 遊俠 (遠程) / 盜賊 (敏捷) / 牧師 (治療)",
        default="戰士",
        min_length=2,
        max_length=20,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """創角表單送出。"""
        name = str(self.name_input.value).strip()
        race = str(self.race_input.value).strip()
        char_class = str(self.class_input.value).strip()

        char = create_character(
            user_id=interaction.user.id,
            name=name,
            race=race,
            character_class=char_class,
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
        embed.set_footer(text="提示：可於 /dnd 面板中點擊 [✍️ 冒險行動] 向地下城主提出行動！")

        await interaction.response.send_message(embed=embed, ephemeral=False)


class DndRollModal(ui.Modal, title="🎲 進行 D&D 擲骰判定"):
    """D&D 擲骰彈出表單。"""

    dice_input: Any = ui.TextInput(
        label="骰子規格 (例如 d20, 2d6, 1d8)",
        default="d20",
        min_length=2,
        max_length=15,
        required=True,
    )
    modifier_input: Any = ui.TextInput(
        label="修正數值 (+/-數字)",
        default="0",
        min_length=1,
        max_length=6,
        required=False,
    )
    mode_input: Any = ui.TextInput(
        label="判定模式 (normal / advantage / disadvantage)",
        placeholder="normal (一般) / advantage (優勢) / disadvantage (劣勢)",
        default="normal",
        min_length=4,
        max_length=20,
        required=False,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """擲骰計算。"""
        dice = self.dice_input.value.strip().lower()
        mode_val = self.mode_input.value.strip().lower()
        try:
            mod = int(self.modifier_input.value.strip())
        except ValueError:
            mod = 0

        match = re.match(r"^(\d*)d(\d+)$", dice)
        if not match:
            await interaction.response.send_message("❌ 擲骰格式錯誤！請使用如 `d20` 或 `2d6` 格式。", ephemeral=True)
            return

        count = int(match.group(1)) if match.group(1) else 1
        sides = int(match.group(2))

        from mod.dnd.dice import advantage_roll, disadvantage_roll, roll_dice
        if "adv" in mode_val and "dis" not in mode_val:
            d1, d2, chosen = advantage_roll(sides)
            total = chosen + mod
            desc = f"🎲 優勢擲骰 [{dice}]: 第一次 `{d1}`，第二次 `{d2}` → 取高 `{chosen}` + 修正 `{mod:+d}` = **{total}**"
        elif "dis" in mode_val:
            d1, d2, chosen = disadvantage_roll(sides)
            total = chosen + mod
            desc = f"🎲 劣勢擲骰 [{dice}]: 第一次 `{d1}`，第二次 `{d2}` → 取低 `{chosen}` + 修正 `{mod:+d}` = **{total}**"
        else:
            rolls = roll_dice(count, sides)
            raw_sum = sum(rolls)
            total = raw_sum + mod
            rolls_str = ", ".join(str(r) for r in rolls)
            desc = f"🎲 擲骰 [{count}d{sides}]: ({rolls_str}) = `{raw_sum}` + 修正 `{mod:+d}` = **{total}**"

        embed = discord.Embed(
            title="🎲 擲骰結果",
            description=desc,
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=False)


class DndMainDashboardView(ui.View):
    """D&D 跑團冒險主控面板 View。"""

    def __init__(self, bot: discord.Client) -> None:
        super().__init__(timeout=300)
        self.bot = bot

    def build_dashboard_embed(self, user_id: int) -> discord.Embed:
        """建立 D&D 主控面板 Embed。"""
        from mod.dnd.storage import load_world_setting, save_world_state
        setting = load_world_setting()
        world = load_world_state()
        characters = load_characters()
        char = characters.get(user_id)

        embed = discord.Embed(
            title=f"🐉 D&D 冒險中樞：{setting.get('world_name', '艾瑟蘭大陸')}",
            description=f"紀元：**{setting.get('era', '裂隙紀元')}** | 當前時間：`{world.game_time.to_string()}`",
            color=discord.Color.dark_red(),
        )

        if char:
            loc = world.locations.get(char.location_id)
            loc_name = loc.name if loc else char.location_id
            embed.add_field(
                name=f"👤 冒險者：{char.name} (Lv.{char.level} {char.character_class})",
                value=(
                    f"• **生命值**: ❤️ {char.hp_current}/{char.hp_max} | **防禦**: 🛡️ {char.ac}\n"
                    f"• **當前位置**: 📍 {loc_name} | **金幣**: 🪙 {char.inventory.gold}"
                ),
                inline=False,
            )
        else:
            embed.add_field(
                name="👤 尚未創建角色",
                value="點擊下方 **[ 👤 創建角色 ]** 按鈕建立你的專屬冒險者！",
                inline=False,
            )

        embed.set_footer(text="點擊按鈕可直接行動、擲骰、檢視背包或切換跑團對話模式！")
        return embed

    @ui.button(label="✍️ 冒險行動", style=discord.ButtonStyle.primary, row=0)
    async def btn_action(self, interaction: discord.Interaction, button: ui.Button) -> None:
        """開啟行動輸入彈窗。"""
        await interaction.response.send_modal(CustomActionModal(self.bot))

    @ui.button(label="🎲 擲骰判定", style=discord.ButtonStyle.primary, row=0)
    async def btn_roll(self, interaction: discord.Interaction, button: ui.Button) -> None:
        """開啟擲骰彈窗。"""
        await interaction.response.send_modal(DndRollModal())

    @ui.button(label="👤 創建角色", style=discord.ButtonStyle.success, row=0)
    async def btn_create(self, interaction: discord.Interaction, button: ui.Button) -> None:
        """開啟創角彈窗。"""
        await interaction.response.send_modal(DndCreateModal())

    @ui.button(label="📊 角色卡", style=discord.ButtonStyle.secondary, row=1)
    async def btn_status(self, interaction: discord.Interaction, button: ui.Button) -> None:
        """顯示角色卡。"""
        characters = load_characters()
        char = characters.get(interaction.user.id)
        if not char:
            await interaction.response.send_message("❌ 你還沒有創建角色！請先點擊 [ 👤 創建角色 ] 創角。", ephemeral=True)
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
        embed.add_field(name="📊 核心屬性", value=stats_str, inline=False)
        embed.add_field(name="💰 錢包", value=f"🪙 {char.inventory.gold} 金幣 | 🥈 {char.inventory.silver} 銀幣", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="🎒 背包清單", style=discord.ButtonStyle.secondary, row=1)
    async def btn_inventory(self, interaction: discord.Interaction, button: ui.Button) -> None:
        """查看背包。"""
        characters = load_characters()
        char = characters.get(interaction.user.id)
        if not char:
            await interaction.response.send_message("❌ 你還沒有創建角色！", ephemeral=True)
            return

        embed = discord.Embed(title=f"🎒 {char.name} 的冒險背包", color=discord.Color.orange())
        if not char.inventory.items:
            embed.description = "背包空空如也。"
        else:
            item_lines = [f"• **{it.name}** ({it.item_type})" + (" [⚔️ 裝備中]" if it.equipped else "") for it in char.inventory.items]
            embed.description = "\n".join(item_lines)
        embed.set_footer(text=f"金幣: {char.inventory.gold} | 銀幣: {char.inventory.silver}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="🏰 切換 Session", style=discord.ButtonStyle.secondary, row=1)
    async def btn_toggle_session(self, interaction: discord.Interaction, button: ui.Button) -> None:
        """切換頻道跑團模式。"""
        from mod.dnd.storage import save_world_state
        world = load_world_state()
        cid = interaction.channel_id
        if not cid:
            await interaction.response.send_message("❌ 無法取得頻道 ID。", ephemeral=True)
            return

        if cid in world.active_channels:
            world.active_channels.remove(cid)
            save_world_state(world)
            await interaction.response.send_message("🕊️ 已關閉本頻道的 D&D 跑團模式（恢復一般聊天）。", ephemeral=True)
        else:
            world.active_channels.append(cid)
            save_world_state(world)
            await interaction.response.send_message("🏰 已開啟本頻道的 D&D 跑團模式！現在本頻道的發言皆會由 DM 進行判定。", ephemeral=True)

    @ui.button(label="🌍 世界情報", style=discord.ButtonStyle.secondary, row=1)
    async def btn_world(self, interaction: discord.Interaction, button: ui.Button) -> None:
        """查看世界狀態。"""
        from mod.dnd.storage import load_world_setting
        setting = load_world_setting()
        world = load_world_state()
        embed = discord.Embed(
            title=f"🌍 世界狀態：{setting.get('world_name', '艾瑟蘭大陸')}",
            description=f"紀元：**{setting.get('era', '裂隙紀元')}**",
            color=discord.Color.teal(),
        )
        embed.add_field(name="⏰ 遊戲時間", value=world.game_time.to_string(), inline=False)
        embed.add_field(name="⏳ 累計 Tick", value=f"{world.current_tick} ticks", inline=True)
        embed.add_field(name="🏰 啟用頻道數", value=f"{len(world.active_channels)} 個", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
