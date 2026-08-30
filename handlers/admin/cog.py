import asyncio
from datetime import datetime, timezone
import io
import json
import logging
import time
from typing import Any, Literal

import discord
from discord import app_commands
from discord.ext import commands

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
            if hasattr(mem_mgr, "get_full_user_memory_snippet"):
                snippet = mem_mgr.get_full_user_memory_snippet(user.id)
            else:
                snippet = mem_mgr.get_user_memory_snippet(user.id)

            if not snippet:
                await interaction.response.send_message(f"{user.mention} 尚無任何長期記憶。", ephemeral=True)
            else:
                view = MemoryControlView(target_user_id=user.id)
                msg_content = f"**{user.display_name} 長期記憶:**\n```\n{snippet}\n```"
                if len(msg_content) > 1950:
                    available = 1950 - len(f"**{user.display_name} 長期記憶:**\n```\n\n...(篇幅過長截斷)\n```")
                    msg_content = f"**{user.display_name} 長期記憶:**\n```\n{snippet[:available]}\n...(篇幅過長截斷)\n```"
                await interaction.response.send_message(msg_content, view=view, ephemeral=True)
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
    @app_commands.command(name="debug", description="🐞 系統除錯模式設定、狀態檢視與完整 Prompt 注入檢視")
    @app_commands.describe(
        action="選擇欲調整或執行的除錯功能 (status: 狀態總覽 | detail: 步驟化思考欄 | live: 即時動態編輯思考 | tag: 模擬匯出 Prompt)",
        enable="True = 開啟功能；False = 關閉功能 (僅 action 為 detail 或 live 時有效)",
        context="輸入想模擬 @雪莉 的對話內容 (僅 action 為 tag 時有效)",
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="📊 檢視當前除錯與推演狀態 (status) — 一覽所有除錯開關、模型與推演配置", value="status"),
        app_commands.Choice(name="💡 步驟化思考推演欄 (detail) — 顯示步驟化思考、執行耗時、模型與工具調用", value="detail"),
        app_commands.Choice(name="⚡ 即時動態編輯思考 (live) — 原地動態編輯更新思考進度，0 等待", value="live"),
        app_commands.Choice(name="🏷️ 完整 Prompt 注入檢視 (tag) — 模擬 @雪莉 並產出完整注入的 System/User Prompt", value="tag"),
    ])
    async def debug_command(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str] | None = None,
        enable: bool | None = None,
        context: str | None = None,
    ) -> None:
        action_val = action.value if action else "status"

        if action_val == "status":
            await self._handle_debug_status(interaction)
        elif action_val == "detail":
            if enable is None:
                enable = not getattr(settings, "DEBUG_DETAIL", False)
            settings.DEBUG_DETAIL = enable
            settings.update_setting("DEBUG_DETAIL", enable)
            state_text = "已開啟 ✅ (完成後同時保留步驟化思考推演欄與回覆)" if enable else "已關閉 ❌ (完成後覆寫為純淨回覆，生成期間仍保留動態思考推演)"
            await interaction.response.send_message(
                f"🐞 **[Debug 模式設定]** 步驟化思考推演欄{state_text}",
                ephemeral=True,
            )
        elif action_val == "live":
            if enable is None:
                enable = not getattr(settings, "LIVE_THINKING_EDIT", False)
            settings.LIVE_THINKING_EDIT = enable
            settings.update_setting("LIVE_THINKING_EDIT", enable)
            state_text = "已開啟 🚀 (雪莉將在 Discord 原地動態更新思考推演進度)" if enable else "已關閉 ❌ (恢復完成後一次性發送)"
            await interaction.response.send_message(
                f"⚡ **[即時思考推演設定]** 動態編輯模式{state_text}",
                ephemeral=True,
            )
        elif action_val == "tag":
            await self._handle_debug_tag(interaction, context)

    async def _handle_debug_status(self, interaction: discord.Interaction) -> None:
        """顯示除錯模式、思考推演與模型調度之即時配置狀態總覽。

        Args:
            interaction: Discord 互動事件實例。
        """
        debug_detail = getattr(settings, "DEBUG_DETAIL", False)
        live_thinking = getattr(settings, "LIVE_THINKING_EDIT", True)
        micro_cot = getattr(settings, "MICRO_COT_ENABLED", True)
        early_term = getattr(settings, "ACTION_EARLY_TERMINATION_ENABLED", True)
        dispatch_mode = getattr(settings, "AGENT_DISPATCH_MODE", "INTEGRATED").upper()
        primary_model = getattr(settings, "PRIMARY_MODEL", "未設定")
        planner_model = getattr(settings, "PARALLEL_PLANNER_MODEL", getattr(settings, "CLASSIFIER_MODEL", "未設定"))
        react_model = getattr(settings, "REACT_MODEL", getattr(settings, "CLASSIFIER_MODEL", "未設定"))
        vision_model = getattr(settings, "VISION_MODEL", "") or "（原生端到端多模態）"
        temp = getattr(settings, "LLM_TEMPERATURE", 0.85)
        step_timeout = getattr(
            settings,
            "PARALLEL_STEP_TIMEOUT" if dispatch_mode == "PARALLEL" else "REACT_STEP_TIMEOUT",
            60.0,
        )

        embed = discord.Embed(
            title="🐞 雪莉系統除錯與思考推演狀態總覽",
            color=0x2ECC71 if (debug_detail or live_thinking) else 0x95A5A6,
            timestamp=datetime.now(timezone.utc),
        )

        detail_desc = (
            "已開啟 ✅ (完成後保留步驟推演與回覆)"
            if debug_detail
            else "已關閉 ❌ (完成後覆寫為純淨回覆，生成期間仍有動態推演)"
        )
        live_desc = "已開啟 🚀 (Discord 原地動態更新思考推演進度)" if live_thinking else "已關閉 ❌ (完成後一次性發送)"
        cot_desc = "已開啟 ✅ (輸出 `<thought>` 內心推理)" if micro_cot else "已關閉 ❌"
        early_desc = "已開啟 ⚡ (純動作工具極速短路)" if early_term else "已關閉 ❌"

        embed.add_field(
            name="🛠️ 除錯與思考推演模式",
            value=(
                f"• **💡 步驟化思考推演欄 (detail)**: {detail_desc}\n"
                f"• **⚡ 即時動態編輯推演 (live)**: {live_desc}\n"
                f"• **🧠 微思維鏈心智推理 (Micro-CoT)**: {cot_desc}\n"
                f"• **⏩ 動作工具極速短路**: {early_desc}"
            ),
            inline=False,
        )

        embed.add_field(
            name="🤖 調度架構與模型配置",
            value=(
                f"• **調度模式 (AGENT_DISPATCH_MODE)**: `{dispatch_mode}`\n"
                f"• **主力生成模型 (PRIMARY_MODEL)**: `{primary_model}`\n"
                f"• **規劃/推理模型 ({'PLANNER' if dispatch_mode == 'PARALLEL' else 'REACT'}_MODEL)**: `{planner_model if dispatch_mode == 'PARALLEL' else react_model}`\n"
                f"• **視覺解析模型 (VISION_MODEL)**: `{vision_model}`"
            ),
            inline=False,
        )

        embed.add_field(
            name="⚙️ 推演運行參數",
            value=(
                f"• **單步推演超時**: `{step_timeout}s`\n"
                f"• **採樣溫度 (Temperature)**: `{temp}`\n"
                f"• **最大輸出 Token**: `{getattr(settings, 'LLM_MAX_OUTPUT_TOKENS', 2048)}`\n"
                f"• **日誌等級 (DISCORD_LOG_LEVEL)**: `{getattr(settings, 'DISCORD_LOG_LEVEL', 'WARNING')}`"
            ),
            inline=False,
        )

        embed.set_footer(text="可使用 /debug action:<detail|live> enable:<True|False> 隨時調整開關")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _handle_debug_tag(
        self,
        interaction: discord.Interaction,
        context: str | None,
    ) -> None:
        """模擬 @雪莉 流程，執行並匯出完整 Prompt、中間排程與推演報告。"""
        if not context or not context.strip():
            await interaction.response.send_message(
                "❌ 請在 `context` 參數中輸入要測試的 Prompt 內容！\n例如：`/debug action:tag context:妳覺得這案子該怎麼查？`",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=False, thinking=True)

        user_query = context.strip()
        channel = interaction.channel
        user = interaction.user
        channel_id = getattr(channel, "id", 0)
        display_name = getattr(user, "display_name", "使用者")
        user_id = getattr(user, "id", 0)

        brain_cog = self.bot.get_cog("BotBrain")
        if not brain_cog:
            await interaction.followup.send("❌ BotBrain 未就緒，無法執行推論。")
            return
        brain: Any = brain_cog

        start_time = time.time()

        # 1. 擷取真實頻道上下文
        from handlers.social.utils import fetch_context_script
        from handlers.social.memory_builder import build_user_memory_block
        from handlers.social.persona import build_dynamic_persona

        context_script = await fetch_context_script(channel, None) if channel else ""
        trigger_line = f"🎯 【當下必須回應的目標】：[{display_name}] 正在對妳說：「{user_query}」"
        full_context = f"{context_script}\n\n{trigger_line}" if context_script else trigger_line

        # 2. 擷取頻道話題氛圍與長期記憶
        vibe_mgr: Any = self.bot.get_cog("VibeManager")
        vibe_snippet = vibe_mgr.get_top_topics_snippet(channel_id) if vibe_mgr else ""
        memory_block = build_user_memory_block(self.bot, user_id, display_name)

        # 3. 擷取動態人設與 Lorebook 觸發小抄
        recent_text = (full_context[-1500:] + "\n" + user_query) if full_context else user_query
        head_persona, tail_lore = build_dynamic_persona(recent_text, "CHAT", channel_id=channel_id)

        # 4. 初始化 DispatchAgentTool
        from handlers.social.registry import TOOL_LIST
        from handlers.social.dispatch_agent import (
            DispatchAgentTool,
            _PARALLEL_PLANNER_SYSTEM_TEMPLATE,
            _USER_MESSAGE_TEMPLATE,
            _parse_plan_json,
        )

        dispatch_mode = getattr(settings, "AGENT_DISPATCH_MODE", "INTEGRATED").upper()
        agent_mode: Literal["INTEGRATED", "PARALLEL", "REACT"] = (
            "REACT" if dispatch_mode == "REACT" else ("PARALLEL" if dispatch_mode == "PARALLEL" else "INTEGRATED")
        )
        agent = DispatchAgentTool(TOOL_LIST, mode=agent_mode)

        planner_model = getattr(settings, "PARALLEL_PLANNER_MODEL", settings.CLASSIFIER_MODEL)
        planner_timeout = getattr(settings, "PARALLEL_STEP_TIMEOUT", 60.0)

        try:
            # 5. 執行 Planner
            system_prompt = _PARALLEL_PLANNER_SYSTEM_TEMPLATE.format(
                tool_descriptions=agent._build_tool_descriptions()
            )
            planner_user_msg = _USER_MESSAGE_TEMPLATE.format(author=display_name, content=user_query)

            planner_raw = await agent._call_llm(
                brain, system_prompt, planner_user_msg,
                task_id=f"debug-plan-{interaction.id}",
                model=planner_model,
                temperature=0.1,
                timeout=planner_timeout,
            )
            plan = _parse_plan_json(planner_raw, user_query)

            # 6. 執行規劃工具（若有）
            max_tasks = getattr(settings, "PARALLEL_MAX_TASKS", 5)
            seen_keys: set[tuple[str, str]] = set()
            deduped = []
            for t in plan.tasks[:max_tasks]:
                key = (t.tool.upper(), t.input.strip())
                if key not in seen_keys:
                    seen_keys.add(key)
                    deduped.append(t)

            from handlers.executor.cog import _FakeMessage
            fake_msg = _FakeMessage(
                channel=channel,  # type: ignore[arg-type]
                author=user,      # type: ignore[arg-type]
                clean_content=user_query,
                id=interaction.id,
                _interaction=interaction,
            )

            executed_results = []
            if deduped:
                executed_results = list(await asyncio.gather(*[
                    agent._parallel_execute_single_task(t, self.bot, fake_msg, vibe_snippet, full_context, images=None)  # type: ignore[arg-type]
                    for t in deduped
                ]))

            obs_pairs = [(name, obs) for name, _, obs in executed_results]

            # 7. 生成最終回覆與捕捉 Prompt
            final_speech, persona_thought, sys_prompt, user_prompt = await agent._generate_final_response(
                brain=brain, user_query=user_query, channel_id=channel_id,
                bot=self.bot, user_id=user_id, display_name=display_name,
                observations=obs_pairs, vibe_snippet=vibe_snippet,
                full_context=full_context, images=None,
            )

            elapsed_ms = int((time.time() - start_time) * 1000)

            # 8. 組裝 Markdown 除錯報告
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            planned_tasks_text = "\n".join([f"- **工具**: `{t.tool}` | **參數**: `{t.input}`" for t in plan.tasks]) or "- (無規劃任何工具)"
            tool_obs_text = "\n".join([f"- **[{name}]**:\n```\n{obs}\n```" for name, obs in obs_pairs]) or "- (未調用任何工具回傳)"

            report_md = (
                f"# 🐞 雪莉除錯流程分析報告 (Sherry Full Prompt & Pipeline Dump)\n\n"
                f"- **測試時間**: {now_str} (UTC)\n"
                f"- **觸發使用者**: {display_name} (`{user_id}`)\n"
                f"- **所在頻道**: #{getattr(channel, 'name', '未知頻道')} (`{channel_id}`)\n"
                f"- **原始輸入 (Context)**: {user_query}\n"
                f"- **調度模式**: `{dispatch_mode}`\n"
                f"- **執行耗時**: `{elapsed_ms} ms`\n"
                f"- **使用模型**: `{planner_model} -> {settings.PRIMARY_MODEL}`\n\n"
                f"---\n\n"
                f"## 1. 📜 擷取到的頻道對話上下文 (Chat History Context)\n"
                f"{context_script if context_script else '*(當前頻道無近期對話歷史紀錄，或時間間隔超過 15 分鐘已重置)*'}\n\n"
                f"---\n\n"
                f"## 2. 🎯 當前鎖定之回應目標 (Trigger Target)\n"
                f"{trigger_line}\n\n"
                f"---\n\n"
                f"## 3. 💭 頻道話題氛圍 (Channel Vibe)\n"
                f"{vibe_snippet if vibe_snippet else '*(無近期活躍話題氛圍)*'}\n\n"
                f"---\n\n"
                f"## 4. 🧠 注入之使用者長期記憶 (User Memory)\n"
                f"{memory_block if memory_block else '*(無特定使用者個人記憶紀錄)*'}\n\n"
                f"---\n\n"
                f"## 5. 📖 觸發之 Lorebook 知識小抄 (Triggered Lorebook)\n"
                f"{tail_lore if tail_lore else '*(未觸發任何 Lorebook 關鍵字小抄)*'}\n\n"
                f"---\n\n"
                f"## 6. 🛠️ 任務規劃與排程 (Planner & Observations)\n"
                f"- **排程模型**: `{planner_model}`\n"
                f"- **Planner 思考 (Thought)**:\n```text\n{plan.thought or '(無思考)'}\n```\n"
                f"- **規劃任務清單**:\n{planned_tasks_text}\n"
                f"- **工具執行結果**:\n{tool_obs_text}\n\n"
                f"---\n\n"
                f"## 7. 📥 完整注入之 System Prompt (系統提示詞)\n"
                f"```text\n{sys_prompt}\n```\n\n"
                f"---\n\n"
                f"## 8. 👤 完整注入之 User Prompt (使用者提示詞)\n```text\n{user_prompt}\n```\n\n"
                f"---\n\n"
                f"## 9. 💭 模型生成之思考推演 (CoT Thought)\n```text\n{persona_thought or '(無內部思考)'}\n```\n\n"
                f"---\n\n"
                f"## 10. 🐱 雪莉最終回覆內容 (Final Speech)\n{final_speech}\n"
            )

            # 9. 建立 Discord Embed 與 File 附檔
            embed = discord.Embed(
                title="🐞 [Debug Tag] 完整 Prompt 與推演流程檢視",
                color=0x3498DB,
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(name="👤 測試提問", value=f"> {user_query[:300]}", inline=False)
            embed.add_field(
                name="⚡ 執行資訊",
                value=f"• **模型**: `{planner_model} -> {settings.PRIMARY_MODEL}`\n• **耗時**: `{elapsed_ms} ms`\n• **調度模式**: `{dispatch_mode}`\n• **調用工具數**: `{len(obs_pairs)}`",
                inline=False,
            )
            ctx_display = context_script[:300] + "..." if len(context_script) > 300 else (context_script if context_script else "(無近期對話歷史紀錄)")
            embed.add_field(name="📜 頻道對話上下文", value=f"```{ctx_display}```", inline=False)
            embed.add_field(
                name="🧠 記憶與話題",
                value=f"• **話題**: {vibe_snippet[:80] or '(無)'}\n• **記憶**: {memory_block[:80] or '(無)'}",
                inline=False,
            )
            if persona_thought:
                embed.add_field(
                    name="🧠 思考推演 (CoT 預覽)",
                    value=f"```{persona_thought[:400]}```" if len(persona_thought) <= 400 else f"```{persona_thought[:400]}...```",
                    inline=False,
                )
            embed.add_field(
                name="🐱 雪莉回覆預覽",
                value=final_speech[:500] if len(final_speech) <= 500 else final_speech[:500] + "...",
                inline=False,
            )
            embed.set_footer(text=f"完整 10 大區塊分析與 Prompt 已匯出至下方 .md 附檔 • 耗時 {elapsed_ms} ms")

            file = discord.File(
                fp=io.BytesIO(report_md.encode("utf-8")),
                filename=f"sherry_debug_prompt_{interaction.id}.md",
            )

            await interaction.followup.send(embed=embed, file=file)
        except Exception as e:
            logger.error("debug tag error: %s", e, exc_info=True)
            await interaction.followup.send(f"❌ 執行 `/debug action:tag` 時發生錯誤：{e}")

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

