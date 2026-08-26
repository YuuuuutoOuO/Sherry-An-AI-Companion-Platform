import logging
from typing import Any
import discord
from discord import app_commands
from discord.ext import commands
import json

import settings

logger = logging.getLogger(__name__)

class ConfigModal(discord.ui.Modal, title='修改設定'):
    new_value: Any = discord.ui.TextInput(
        label='新數值',
        style=discord.TextStyle.short,
        placeholder='請輸入新的設定值...',
        required=True
    )

    def __init__(self, key: str, current_value: str):
        super().__init__()
        self.key = key
        self.new_value.default = current_value

    async def on_submit(self, interaction: discord.Interaction):
        val = self.new_value.value
        try:
            success = settings.update_setting(self.key, val)
            if success:
                new_val = getattr(settings, self.key, val)
                await interaction.response.send_message(f"✅ 設定 `{self.key}` 已更新為 `{new_val}`，並儲存至 override 檔。", ephemeral=True)
            else:
                await interaction.response.send_message(f"❌ 更新失敗！請確認 `{self.key}` 的數值格式是否正確。", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ 設定更新異常：{e}", ephemeral=True)

class ConfigSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="PRIMARY_MODEL", description="主要對話模型"),
            discord.SelectOption(label="VISION_MODEL", description="視覺模型"),
            discord.SelectOption(label="DISCORD_LOG_LEVEL", description="Discord 日誌層級"),
            discord.SelectOption(label="VIBE_DECAY_INTERVAL_SECONDS", description="氛圍衰減秒數"),
            discord.SelectOption(label="OLLAMA_TIMEOUT", description="Ollama API 逾時"),
            discord.SelectOption(label="INTERJECT_EVAL_THRESHOLD", description="插話評估頻率"),
        ]
        super().__init__(placeholder="請選擇要修改的設定...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        key = self.values[0]
        current_value = str(getattr(settings, key, ""))
        await interaction.response.send_modal(ConfigModal(key, current_value))

class ConfigView(discord.ui.View):
    def __init__(self):
        super().__init__()
        self.add_item(ConfigSelect())

class MemoryControlView(discord.ui.View):
    def __init__(self, target_user_id: int):
        super().__init__(timeout=settings.DISCORD_UI_VIEW_TIMEOUT)
        self.target_user_id = target_user_id

    @discord.ui.button(label="✅ 沒錯，很準", style=discord.ButtonStyle.success)
    async def correct_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_user_id:
            await interaction.response.send_message("❌ 你只能確認自己的記憶喔！", ephemeral=True)
            return
        await interaction.response.send_message("✅ 收到確認，雪莉記對了！", ephemeral=True)

    @discord.ui.button(label="❌ 記錯了，清除記憶", style=discord.ButtonStyle.danger)
    async def incorrect_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_user_id:
            await interaction.response.send_message("❌ 你只能清除自己的記憶喔！", ephemeral=True)
            return
            
        mem_mgr: Any = getattr(interaction.client, "get_cog", lambda _: None)("MemoryManager")
        if mem_mgr:
            user_id_str = str(self.target_user_id)
            if user_id_str in mem_mgr._store:
                del mem_mgr._store[user_id_str]
                from handlers.memory.storage import save_user_memory
                save_user_memory(mem_mgr._store, settings.USER_MEMORY_PATH)
                await interaction.response.send_message("🗑️ 已經幫你把錯亂的記憶刪除囉，雪莉會重新認識你！", ephemeral=True)
            else:
                await interaction.response.send_message("目前沒有記憶可以清除。", ephemeral=True)

class AdminCog(commands.Cog):
    """提供伺服器管理、記憶維護、氛圍操作與除錯指令。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def _is_admin(self, interaction: discord.Interaction) -> bool:
        if not settings.ADMIN_ROLE_IDS:
            return True
        if isinstance(interaction.user, discord.Member):
            return any(r.id in settings.ADMIN_ROLE_IDS for r in interaction.user.roles)
        return False

    def _is_super_admin(self, interaction: discord.Interaction) -> bool:
        if not settings.ADMIN_USER_IDS:
            return False
        return interaction.user.id in settings.ADMIN_USER_IDS

    # ── /vibe ──────────────────────────────────────────────────
    @app_commands.command(name="vibe", description="💬 檢視或清除頻道即時話題氛圍")
    @app_commands.describe(
        action="選擇執行的動作",
        channel="目標文字頻道 (預設為當前頻道)",
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="👀 檢視氛圍 (show) — 查看當前頻道話題氛圍 (公開)", value="show"),
        app_commands.Choice(name="🧹 清除氛圍 (clear) — 清除指定頻道氛圍資料 (管理員)", value="clear"),
    ])
    async def vibe_command(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        channel: discord.TextChannel | None = None,
    ) -> None:
        target = channel or interaction.channel
        vibe_mgr: Any = self.bot.get_cog("VibeManager")
        if not vibe_mgr:
            await interaction.response.send_message("氛圍管理器未加載！", ephemeral=True)
            return

        if not target or not hasattr(target, "id"):
            await interaction.response.send_message("❌ 無效的文字頻道！", ephemeral=True)
            return

        target_mention = getattr(target, "mention", str(target))
        if action.value == "show":
            snippet = vibe_mgr.get_top_topics_snippet(target.id)
            if not snippet:
                await interaction.response.send_message(f"{target_mention} 目前尚無足夠的話題氛圍記錄。", ephemeral=False)
            else:
                await interaction.response.send_message(f"**{target_mention} 即時話題氛圍摘要:**\n```\n{snippet}\n```", ephemeral=False)
        elif action.value == "clear":
            if not self._is_super_admin(interaction):
                await interaction.response.send_message("你沒有權限執行此最高級別指令！只有超級管理員才能刪除文件。", ephemeral=True)
                return
            channel_id_str = str(target.id)
            if channel_id_str in vibe_mgr._store:
                del vibe_mgr._store[channel_id_str]
                from handlers.vibe_manager.storage import save_vibe_store
                save_vibe_store(vibe_mgr._store, settings.CHANNEL_VIBE_PATH)
                await interaction.response.send_message(f"已成功清除 {target_mention} 的氛圍記憶並寫入檔案。")
            else:
                await interaction.response.send_message(f"{target_mention} 目前沒有任何氛圍記憶可以清除。")

    # ── /memory ────────────────────────────────────────────────
    @app_commands.command(name="memory", description="🧠 檢視或清除成員的長期記憶")
    @app_commands.describe(
        action="選擇執行的動作",
        user="選擇目標成員",
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="👀 檢視記憶 (show) — 檢視成員長期記憶 (僅自己可見)", value="show"),
        app_commands.Choice(name="🧹 清除記憶 (clear) — 清除成員長期記憶 (管理員)", value="clear"),
    ])
    async def memory_command(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        user: discord.Member,
    ) -> None:
        mem_mgr: Any = self.bot.get_cog("MemoryManager")
        if not mem_mgr:
            await interaction.response.send_message("記憶管理器未加載！", ephemeral=True)
            return

        if action.value == "show":
            snippet = mem_mgr.get_user_memory_snippet(user.id)
            if not snippet:
                await interaction.response.send_message(f"{user.mention} 尚無任何長期記憶。", ephemeral=True)
            else:
                view = MemoryControlView(target_user_id=user.id)
                await interaction.response.send_message(f"**{user.display_name} 長期記憶:**\n```\n{snippet}\n```", view=view, ephemeral=True)
        elif action.value == "clear":
            if not self._is_super_admin(interaction):
                await interaction.response.send_message("權限不足！只有最高管理員能刪除使用者記憶。", ephemeral=True)
                return
            user_id_str = str(user.id)
            if user_id_str in mem_mgr._store:
                del mem_mgr._store[user_id_str]
                from handlers.memory.storage import save_user_memory
                save_user_memory(mem_mgr._store, settings.USER_MEMORY_PATH)
                await interaction.response.send_message(f"已成功清除 {user.mention} 的長期記憶資料並寫入檔案。")
            else:
                await interaction.response.send_message(f"{user.mention} 尚無任何長期記憶可以清除。")

    # ── /admin ─────────────────────────────────────────────────
    @app_commands.command(name="admin", description="⚙️ 系統維護、指令同步與熱重載管理")
    @app_commands.describe(
        action="選擇執行的管理動作",
        target="目標 Cog 名稱或模組名 (reload 或 restart 時選填，預設為 all)",
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="🔄 同步指令 (sync) — 即時同步所有 Slash 指令至 Discord API", value="sync"),
        app_commands.Choice(name="⚡ 模組熱重啟 (restart) — 重新加載指定模組或全部模組與 settings", value="restart"),
        app_commands.Choice(name="📦 重新加載 Cog (reload) — 重新加載特定 Cog 擴展模組", value="reload"),
        app_commands.Choice(name="🛠️ 設定面板 (config) — 開啟伺服器端動態參數設定面板", value="config"),
    ])
    async def admin_command(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        target: str | None = None,
    ) -> None:
        act = action.value
        if act == "config":
            if not self._is_super_admin(interaction):
                await interaction.response.send_message("你沒有權限執行此指令！", ephemeral=True)
                return
            await interaction.response.send_message("⚙️ **系統動態設定面板**\n請選擇欲修改的參數（修改後將即刻生效並保存）", view=ConfigView(), ephemeral=True)
            return

        if not self._is_admin(interaction):
            await interaction.response.send_message("權限不足！", ephemeral=True)
            return

        if act == "sync":
            if not self._is_super_admin(interaction):
                await interaction.response.send_message("權限不足！只有最高管理員能強制同步指令樹。", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            try:
                synced = await self.bot.tree.sync()
                await interaction.followup.send(
                    f"✅ 成功同步 `{len(synced)}` 個 Slash 指令至 Discord！\n"
                    f"💡 *提示：Discord 客戶端有本機快取，若未立即看見新指令，請在 Discord 按 `Ctrl+R` 重整。*",
                    ephemeral=True,
                )
            except Exception as e:
                await interaction.followup.send(f"❌ 指令同步失敗：{e}", ephemeral=True)

        elif act == "reload":
            if not target:
                await interaction.response.send_message("❌ 請在 `target` 輸入欲重新加載的 Cog 名稱 (如 `handlers.music`)！", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            try:
                await self.bot.reload_extension(target)
                synced = await self.bot.tree.sync()
                await interaction.followup.send(f"✅ 已成功重新加載 `{target}`，並完成 `{len(synced)}` 個指令樹同步！")
            except Exception as e:
                await interaction.followup.send(f"❌ 重新加載失敗：{e}")

        elif act == "restart":
            module_name = target or "all"
            await interaction.response.defer(ephemeral=True)
            import importlib
            try:
                import main
                if module_name.lower() == "all":
                    try:
                        importlib.reload(settings)
                    except Exception as e:
                        logger.error("Failed to reload settings: %s", e)
                extensions_to_reload = main.EXTENSIONS if module_name.lower() == "all" else [module_name]
            except Exception as e:
                await interaction.followup.send(f"載入擴展列表失敗: {e}")
                return

            success_list = []
            fail_list = []
            for ext in extensions_to_reload:
                try:
                    target_ext = ext if module_name.lower() == "all" or ext.startswith("handlers.") else f"handlers.{ext}"
                    await self.bot.reload_extension(target_ext)
                    success_list.append(target_ext)
                except Exception as e:
                    fail_list.append(f"{target_ext} ({e})")

            sync_info = ""
            try:
                synced = await self.bot.tree.sync()
                sync_info = f"\n🔄 指令樹已同步：共 `{len(synced)}` 個斜線指令即刻生效。"
            except Exception as sync_e:
                sync_info = f"\n⚠️ 指令樹同步警告：{sync_e}"

            res_text = ""
            if success_list:
                res_text += f"✅ 成功重啟: {', '.join(success_list)}\n"
            if fail_list:
                res_text += f"❌ 重啟失敗: {', '.join(fail_list)}\n"
            if not res_text:
                res_text = "找不到指定的模組進行重啟。"
            res_text += sync_info
            await interaction.followup.send(res_text)

    # ── /debug ─────────────────────────────────────────────────
    @app_commands.command(name="debug", description="🐞 系統除錯模式設定")
    @app_commands.describe(
        action="選擇欲調整的除錯功能",
        enable="True = 開啟卡片式回覆；False = 恢復一般文字對話",
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="📋 詳細卡片模式 (detail) — 顯示 CoT 思考過程、執行耗時、模型標籤", value="detail"),
    ])
    async def debug_command(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        enable: bool,
    ) -> None:
        if action.value == "detail":
            settings.DEBUG_DETAIL = enable
            settings.update_setting("DEBUG_DETAIL", enable)
            state_text = "已開啟 ✅ (回覆將展示 CoT 思考過程、執行耗時、模型與意圖標籤)" if enable else "已關閉 ❌ (恢復標準對話模式)"
            await interaction.response.send_message(
                f"🐞 **[Debug 模式設定]** 詳細卡片回覆模式{state_text}",
                ephemeral=True,
            )

    # ── /stats ─────────────────────────────────────────────────
    @app_commands.command(name="stats", description="顯示系統詳細統計儀表板 (公開)")
    async def stats_command(self, interaction: discord.Interaction) -> None:
        brain: Any = self.bot.get_cog("BotBrain")
        mem_mgr: Any = self.bot.get_cog("MemoryManager")
        vibe_mgr: Any = self.bot.get_cog("VibeManager")

        qsize = brain._queue.qsize() if brain else 0
        sd_status = "運行中" if getattr(brain, "_sd_generating", False) else "閒置"
        voice_status = "啟動" if getattr(brain, "_voice_mode_active", False) else "關閉"

        mem_count = len(mem_mgr._store) if mem_mgr else 0
        vibe_count = len(vibe_mgr._store) if vibe_mgr else 0

        import time
        from datetime import timedelta
        uptime_seconds = time.time() - getattr(self.bot, "_start_time", time.time())
        uptime_str = str(timedelta(seconds=int(uptime_seconds)))

        embed = discord.Embed(title="📊 雪莉系統狀態儀表板", color=0x2b2d31)
        embed.add_field(name="🧠 推理大腦", value=f"佇列任務: `{qsize}`\n語音模式: `{voice_status}`\n畫圖狀態: `{sd_status}`", inline=False)
        embed.add_field(name="💾 記憶中樞", value=f"紀錄人數: `{mem_count}`\n頻道氛圍數: `{vibe_count}`", inline=False)
        embed.add_field(name="⚡ 系統效能", value=f"開機時間: `{uptime_str}`\n主要模型: `{settings.PRIMARY_MODEL}`\nDebug卡片: `{getattr(settings, 'DEBUG_DETAIL', False)}`\n日誌層級: `{getattr(settings, 'DISCORD_LOG_LEVEL', 'WARNING')}`", inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=False)

