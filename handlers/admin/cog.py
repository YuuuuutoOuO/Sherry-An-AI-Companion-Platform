import logging
import discord
from discord import app_commands
from discord.ext import commands
import json

import settings

logger = logging.getLogger(__name__)

class ConfigModal(discord.ui.Modal, title='修改設定'):
    new_value = discord.ui.TextInput(
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
        original_val = getattr(settings, self.key, None)
        try:
            if isinstance(original_val, int):
                val = int(val)
            elif isinstance(original_val, float):
                val = float(val)
            elif isinstance(original_val, bool):
                val = val.lower() in ('true', '1', 't', 'y', 'yes')
            
            setattr(settings, self.key, val)
            
            def _save_override():
                overrides = {}
                if settings.CONFIG_OVERRIDE_PATH.exists():
                    with open(settings.CONFIG_OVERRIDE_PATH, "r", encoding="utf-8") as f:
                        overrides = json.load(f)
                overrides[self.key] = val
                with open(settings.CONFIG_OVERRIDE_PATH, "w", encoding="utf-8") as f:
                    json.dump(overrides, f, indent=4, ensure_ascii=False)

            import asyncio
            await asyncio.to_thread(_save_override)
                
            await interaction.response.send_message(f"✅ 設定 `{self.key}` 已更新為 `{val}`，並儲存至 override 檔。", ephemeral=True)
        except ValueError:
            await interaction.response.send_message(f"❌ 數值格式錯誤！無法將 `{val}` 轉為原本的型別。", ephemeral=True)

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
            
        mem_mgr = interaction.client.get_cog("MemoryManager")
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

    @app_commands.command(name="vibe_show", description="顯示目前的氛圍記憶 (僅限自己可見)")
    async def vibe_show(self, interaction: discord.Interaction, channel: discord.TextChannel = None) -> None:
        target = channel or interaction.channel
        vibe_mgr = self.bot.get_cog("VibeManager")
        if not vibe_mgr:
            await interaction.response.send_message("氛圍管理器未加載！", ephemeral=True)
            return
            
        snippet = vibe_mgr.get_top_topics_snippet(target.id)
        if not snippet:
            await interaction.response.send_message(f"{target.mention} 目前沒有氛圍記憶。", ephemeral=True)
        else:
            await interaction.response.send_message(f"**{target.mention} 氛圍記憶:**\n```\n{snippet}\n```", ephemeral=True)

    @app_commands.command(name="vibe_clear", description="清除頻道氛圍記憶")
    async def vibe_clear(self, interaction: discord.Interaction, channel: discord.TextChannel = None) -> None:
        if not self._is_super_admin(interaction):
            await interaction.response.send_message("你沒有權限執行此最高級別指令！只有超級管理員才能刪除文件。", ephemeral=True)
            return
            
        target = channel or interaction.channel
        vibe_mgr = self.bot.get_cog("VibeManager")
        if not vibe_mgr:
            await interaction.response.send_message("氛圍管理器未加載！", ephemeral=True)
            return
            
        channel_id_str = str(target.id)
        if channel_id_str in vibe_mgr._store:
            del vibe_mgr._store[channel_id_str]
            from handlers.vibe_manager.storage import save_vibe_store
            save_vibe_store(vibe_mgr._store, settings.CHANNEL_VIBE_PATH)
            await interaction.response.send_message(f"已成功清除 {target.mention} 的氛圍記憶並寫入檔案。")
        else:
            await interaction.response.send_message(f"{target.mention} 目前沒有任何氛圍記憶可以清除。")

    @app_commands.command(name="memory_show", description="顯示使用者的長期記憶 (僅限自己可見)")
    async def memory_show(self, interaction: discord.Interaction, user: discord.Member) -> None:
        mem_mgr = self.bot.get_cog("MemoryManager")
        if not mem_mgr:
            await interaction.response.send_message("記憶管理器未加載！", ephemeral=True)
            return
            
        snippet = mem_mgr.get_user_memory_snippet(user.id)
        if not snippet:
            await interaction.response.send_message(f"{user.mention} 尚無任何長期記憶。", ephemeral=True)
        else:
            view = MemoryControlView(target_user_id=user.id)
            await interaction.response.send_message(f"**{user.display_name} 長期記憶:**\n```\n{snippet}\n```", view=view, ephemeral=True)

    @app_commands.command(name="memory_clear", description="清除使用者的長期記憶")
    async def memory_clear(self, interaction: discord.Interaction, user: discord.Member) -> None:
        if not self._is_super_admin(interaction):
            await interaction.response.send_message("權限不足！只有最高管理員能刪除使用者記憶。", ephemeral=True)
            return
            
        mem_mgr = self.bot.get_cog("MemoryManager")
        if not mem_mgr:
            await interaction.response.send_message("記憶管理器未加載！", ephemeral=True)
            return
            
        user_id_str = str(user.id)
        if user_id_str in mem_mgr._store:
            del mem_mgr._store[user_id_str]
            from handlers.memory.storage import save_user_memory
            save_user_memory(mem_mgr._store, settings.USER_MEMORY_PATH)
            await interaction.response.send_message(f"已成功清除 {user.mention} 的長期記憶資料並寫入檔案。")
        else:
            await interaction.response.send_message(f"{user.mention} 尚無任何長期記憶可以清除。")

    @app_commands.command(name="reload", description="重新加載 Cog")
    async def reload_cog(self, interaction: discord.Interaction, cog_name: str) -> None:
        if not self._is_admin(interaction):
            await interaction.response.send_message("權限不足！", ephemeral=True)
            return
        try:
            await self.bot.reload_extension(cog_name)
            await interaction.response.send_message(f"已重新加載 {cog_name}！")
        except Exception as e:
            await interaction.response.send_message(f"重新加載失敗：{e}")

    @app_commands.command(name="restart", description="重新加載指定模組或全部模組 (熱重啟)")
    async def restart(self, interaction: discord.Interaction, module_name: str = "all") -> None:
        if not self._is_admin(interaction):
            await interaction.response.send_message("權限不足！", ephemeral=True)
            return
            
        import importlib
        
        try:
            # 延遲載入 main，避免迴圈引入問題
            import main 
            
            # 重新載入 settings，讓設定值可以更新
            if module_name.lower() == "all":
                try:
                    importlib.reload(settings)
                except Exception as e:
                    logger.error("Failed to reload settings: %s", e)
                    
            extensions_to_reload = main.EXTENSIONS if module_name.lower() == "all" else [module_name]
        except Exception as e:
            await interaction.response.send_message(f"載入擴展列表失敗: {e}")
            return
            
        success_list = []
        fail_list = []
        
        for ext in extensions_to_reload:
            try:
                # 簡單處理前綴
                target_ext = ext if module_name.lower() == "all" or ext.startswith("handlers.") else f"handlers.{ext}"
                await self.bot.reload_extension(target_ext)
                success_list.append(target_ext)
            except Exception as e:
                fail_list.append(f"{target_ext} ({e})")
        
        res_text = ""
        if success_list:
            res_text += f"✅ 成功重啟: {', '.join(success_list)}\n"
        if fail_list:
            res_text += f"❌ 重啟失敗: {', '.join(fail_list)}\n"
            
        if not res_text:
            res_text = "找不到指定的模組進行重啟。"
            
        await interaction.response.send_message(res_text)

    @app_commands.command(name="config", description="伺服器端設定面板 (僅限管理員)")
    async def config_panel(self, interaction: discord.Interaction) -> None:
        if not self._is_super_admin(interaction):
            await interaction.response.send_message("你沒有權限執行此指令！", ephemeral=True)
            return
        await interaction.response.send_message("⚙️ **系統動態設定面板**\n請選擇欲修改的參數（修改後將即刻生效並保存）", view=ConfigView(), ephemeral=True)

    @app_commands.command(name="stats", description="顯示系統詳細統計儀表板 (公開)")
    async def stats_command(self, interaction: discord.Interaction) -> None:
        brain = self.bot.get_cog("BotBrain")
        mem_mgr = self.bot.get_cog("MemoryManager")
        vibe_mgr = self.bot.get_cog("VibeManager")
        
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
        embed.add_field(name="⚡ 系統效能", value=f"開機時間: `{uptime_str}`\n主要模型: `{settings.PRIMARY_MODEL}`\n日誌層級: `{getattr(settings, 'DISCORD_LOG_LEVEL', 'WARNING')}`", inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=False)
