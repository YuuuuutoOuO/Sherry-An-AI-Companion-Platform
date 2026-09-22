"""
main.py
=======
Application entry point.

Bootstraps the Discord client, registers all Cog extensions, and starts
the asyncio event loop. All business logic lives in the /handlers package.
"""

from __future__ import annotations

import asyncio
import logging
from logging.handlers import RotatingFileHandler
import sys
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands

import settings

# ---------------------------------------------------------------------------
# Ensure data directory exists before FileHandler is created
# ---------------------------------------------------------------------------
settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
settings.FILE_DIR.mkdir(parents=True, exist_ok=True)
settings.SOUL_DIR.mkdir(parents=True, exist_ok=True)
settings.CHAT_LOG_DIR.mkdir(parents=True, exist_ok=True)
settings.VIDEO_DIR.mkdir(parents=True, exist_ok=True)

import os

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
if sys.platform == "win32":
    os.system("title 🤖 雪莉主核心 (Sherry Core)")

# 記錄主程序 PID 供終端監控分頁同步關閉
try:
    (settings.DATA_DIR / "sherry.pid").write_text(str(os.getpid()), encoding="utf-8")
except Exception:
    pass


from handlers.social.observability import (
    TraceIdFilter,
    get_trace_id,
    log_command,
    log_error,
    make_timer,
    new_trace_id,
    set_trace_id,
)

# ---------------------------------------------------------------------------
# Logging setup (must happen before any other import that logs)
# ---------------------------------------------------------------------------
_stream_handler = logging.StreamHandler(sys.stdout)
_file_handler = RotatingFileHandler(
    settings.BOT_LOG_PATH,
    maxBytes=10 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
_trace_filter = TraceIdFilter()
_stream_handler.addFilter(_trace_filter)
_file_handler.addFilter(_trace_filter)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(trace_id)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[_stream_handler, _file_handler],
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core Cog extension paths (relative dotted module paths)
# ---------------------------------------------------------------------------
EXTENSIONS: list[str] = [
    "handlers.bot_brain",
    "handlers.social",
    "handlers.vibe_manager",
    "handlers.music",
    "handlers.memory",
    "handlers.admin",
    "handlers.voice",
    "handlers.spark",  # 核心 SPARK 條件排程與推播系統（全面接管 scheduler）
    "handlers.executor",
    "handlers.help_cog",
    "handlers.welcome_cog",
    "handlers.heartbeat",
    "handlers.sys_inspector",
    "handlers.roast",
    "handlers.context_menu",
]

import json

# ---------------------------------------------------------------------------
# Optional Mod Directory (auto-discovered plugins)
# 凡是 ./mod/<name>/cog.py 存在的子目錄，且未在 data/mod_config.json 中被停用者，
# 都會被自動嘗試載入。失敗時只輸出 WARNING，絕不影響雪莉主程式。
# ---------------------------------------------------------------------------
BASE_DIR: Path = Path(__file__).resolve().parent
MOD_DIR: Path = BASE_DIR / "mod"
MOD_CONFIG_PATH: Path = settings.DATA_DIR / "mod_config.json"


def _load_mod_config() -> dict[str, bool]:
    """載入 data/mod_config.json 模組開關設定。

    Returns:
        dict[str, bool]: 模組鍵名對應是否啟用之字典。
    """
    if MOD_CONFIG_PATH.exists():
        try:
            raw = MOD_CONFIG_PATH.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict):
                return {str(k): bool(v) for k, v in data.items()}
        except Exception as exc:
            logger.warning("讀取 mod_config.json 失敗: %s", exc)
    return {}


def _is_mod_enabled(mod_key: str, config: dict[str, bool]) -> bool:
    """檢查模組是否啟用（未明確設定者預設為 True）。

    Args:
        mod_key: 模組識別碼。
        config: 模組設定字典。

    Returns:
        bool: 若啟用回傳 True，否則回傳 False。
    """
    return config.get(mod_key, True)


def _discover_optional_extensions() -> list[str]:
    """掃描 ./mod/ 目錄，自動找出含有 cog.py 且未被停用的子資料夾。

    Returns:
        list[str]: 待載入之模組擴充套件路徑清單。
    """
    found: list[str] = []
    if not MOD_DIR.exists():
        return found
    mod_config = _load_mod_config()
    for child in sorted(MOD_DIR.iterdir()):
        if child.is_dir() and (child / "cog.py").exists():
            mod_key = child.name
            if _is_mod_enabled(mod_key, mod_config):
                found.append(f"mod.{mod_key}.cog")
            else:
                logger.info("可選模組 [%s] 在 mod_config.json 中已設為停用，略過啟動載入。", mod_key)
    return found


# ---------------------------------------------------------------------------
# Bot factory
# ---------------------------------------------------------------------------

def build_bot() -> commands.Bot:
    """Construct and return the configured Bot instance."""
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True

    bot = commands.Bot(
        command_prefix="!",   # Fallback prefix; slash commands preferred
        intents=intents,
        help_command=None,
    )
    return bot


# ---------------------------------------------------------------------------
# Lifecycle hooks
# ---------------------------------------------------------------------------

async def main() -> None:
    """Async entry point: load extensions then connect to Discord."""
    # Set exception handler on loop to gracefully ignore Windows WinError 10054 on client disconnects
    def _asyncio_loop_exception_handler(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        exc = context.get("exception")
        if isinstance(exc, ConnectionResetError) or (isinstance(exc, OSError) and getattr(exc, "winerror", None) == 10054):
            return
        msg = context.get("message", "")
        if "_call_connection_lost" in msg or ("ProactorBasePipeTransport" in msg and "10054" in str(context)):
            return
        loop.default_exception_handler(context)

    try:
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(_asyncio_loop_exception_handler)
    except RuntimeError:
        pass

    bot = build_bot()
    from app import SherryCore
    bot.core = SherryCore(bot)  # type: ignore[attr-defined]
    from core.sys_inspector.audit_service import set_cloud_inference_client
    set_cloud_inference_client(bot.core.cloud_client)  # type: ignore[attr-defined]

    async with bot:
        for ext in EXTENSIONS:
            try:
                await bot.load_extension(ext)
                logger.info("Loaded extension: %s", ext)
            except Exception as exc:
                logger.error("Failed to load extension %s: %s", ext, exc, exc_info=True)
                sys.exit(1)

        # --- 動態掃描並選用載入 ./mod/ 插件 ---
        optional_exts = _discover_optional_extensions()
        loaded_mods: list[str] = []
        for ext in optional_exts:
            try:
                await bot.load_extension(ext)
                loaded_mods.append(ext)
                logger.info("Loaded optional module: %s", ext)
            except Exception as exc:
                logger.warning(
                    "Optional module [%s] skipped (failed to load): %s", ext, exc
                )

        @bot.event
        async def on_ready() -> None:
            import time
            setattr(bot, "_start_time", time.time())
            try:
                synced = await bot.tree.sync()
                logger.info("Synced %d slash command(s).", len(synced))
            except Exception as exc:
                logger.error("Failed to sync slash commands: %s", exc)

            # --- Startup Summary Banner ---
            bot_id = bot.user.id if bot.user else "Unknown"
            banner_lines = [
                "=" * 60,
                "🚀 SYSTEM STARTUP COMPLETE",
                "=" * 60,
                f"🤖 Bot Name : {bot.user}",
                f"🆔 Bot ID   : {bot_id}",
                f"🏠 Guilds   : {len(bot.guilds)}",
                "-" * 60,
            ]
            banner_lines.extend(f"  ✅ {ext} - OK" for ext in EXTENSIONS)
            if loaded_mods:
                banner_lines.append("-" * 60)
                banner_lines.append("🔌 OPTIONAL MODS LOADED:")
                banner_lines.extend(f"  🟢 {m} - OK" for m in loaded_mods)
            elif optional_exts:
                banner_lines.append("-" * 60)
                banner_lines.append("⚠️  Optional mods found but failed to load (see WARNING logs)")

            banner_lines.extend([
                "-" * 60,
                "✅ All systems go! Ready for inference.",
                "=" * 60,
            ])

            logger.info("\n" + "\n".join(banner_lines))

            logger.info("Startup sequence completed successfully.")

            async def presence_loop() -> None:
                await bot.wait_until_ready()
                import random
                statuses = [
                    "正在監視某人...",
                    "思考著這世界的密室...",
                    "等待新的奇妙事件...",
                    "發呆中...",
                    "偷偷看著群裡的大家...",
                    "名偵探上線辦案中！",
                    "破解各種離奇死法...",
                ]
                while not bot.is_closed():
                    try:
                        status = random.choice(statuses)
                        await bot.change_presence(activity=discord.Game(name=status))
                        await asyncio.sleep(300)
                    except Exception as e:
                        logger.error(f"Presence loop error: {e}")
                        await asyncio.sleep(60)

            bot.loop.create_task(presence_loop())

            # 啟動 Web UI 伺服器
            try:
                from web_ui.server import start_server_async
                logger.info(f"Targeting Web UI at {settings.WEB_UI_HOST}:{settings.WEB_UI_PORT} (HTTPS={settings.WEB_UI_HTTPS})")
                bot.loop.create_task(start_server_async(bot))
                logger.info("Web Management UI initialized.")
            except Exception as e:
                logger.error(f"Failed to start Web UI: {e}")

        @bot.event
        async def on_interaction(interaction: discord.Interaction) -> None:
            """全域攔截 Discord 互動事件（斜線指令、按鈕、選單、表單）並記錄生命週期與 Trace ID。"""
            itype = interaction.type
            user_str = f"{interaction.user.display_name} ({interaction.user.name})"
            user_id = interaction.user.id
            guild_name = interaction.guild.name if interaction.guild else "DM"
            guild_id = interaction.guild.id if interaction.guild else 0
            channel_name = getattr(interaction.channel, "name", "DM") if interaction.channel else "Unknown"
            channel_id = interaction.channel_id or 0

            # 1. 斜線指令 (Application Command)
            if itype == discord.InteractionType.application_command:
                tid = new_trace_id("cmd")
                set_trace_id(tid)
                raw_data: Any = interaction.data
                data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
                cmd_name = str(data.get("name") or "unknown")

                # 萃取參數選項（支援子指令結構）
                options_dict: dict[str, Any] = {}

                def _extract_options(opts: list[Any]) -> None:
                    for opt in opts:
                        if isinstance(opt, dict):
                            if "value" in opt:
                                options_dict[str(opt.get("name", ""))] = opt.get("value")
                            elif "options" in opt:
                                sub_name = str(opt.get("name", ""))
                                options_dict["_subcommand"] = sub_name
                                sub_opts = opt.get("options")
                                if isinstance(sub_opts, list):
                                    _extract_options(sub_opts)

                raw_opts = data.get("options")
                if isinstance(raw_opts, list):
                    _extract_options(raw_opts)

                logger.info(
                    "[SlashCommand] User: %s (%s) | Guild: %s | Channel: #%s | Command: /%s | Options: %s",
                    user_str,
                    user_id,
                    guild_name,
                    channel_name,
                    cmd_name,
                    options_dict,
                )
                log_command(
                    command_type="slash",
                    command_name=cmd_name,
                    user=user_str,
                    user_id=user_id,
                    guild_name=guild_name,
                    guild_id=guild_id,
                    channel_name=channel_name,
                    channel_id=channel_id,
                    options=options_dict,
                    status="invoked",
                    trace_id=tid,
                )

            # 2. 按鈕與選單元件 (Message Component)
            elif itype == discord.InteractionType.component:
                tid = new_trace_id("btn")
                set_trace_id(tid)
                raw_data = interaction.data
                data = raw_data if isinstance(raw_data, dict) else {}
                custom_id = str(data.get("custom_id") or "unknown")
                comp_type = int(data.get("component_type") or 0)
                raw_values = data.get("values")
                values = raw_values if isinstance(raw_values, list) else []

                comp_kind = "select" if values or comp_type in (3, 5, 6, 7, 8) else "button"
                logger.info(
                    "[Component] Kind: %s | Custom ID: %s | User: %s | Channel: #%s | Values: %s",
                    comp_kind,
                    custom_id,
                    user_str,
                    channel_name,
                    values,
                )
                log_command(
                    command_type=comp_kind,
                    command_name=custom_id,
                    user=user_str,
                    user_id=user_id,
                    guild_name=guild_name,
                    guild_id=guild_id,
                    channel_name=channel_name,
                    channel_id=channel_id,
                    options={"values": values} if values else None,
                    status="invoked",
                    trace_id=tid,
                )

            # 3. 表單提交 (Modal Submit)
            elif itype == discord.InteractionType.modal_submit:
                tid = new_trace_id("modal")
                set_trace_id(tid)
                raw_data = interaction.data
                data = raw_data if isinstance(raw_data, dict) else {}
                custom_id = str(data.get("custom_id") or "unknown")
                logger.info(
                    "[Modal] Custom ID: %s | User: %s | Channel: #%s",
                    custom_id,
                    user_str,
                    channel_name,
                )
                log_command(
                    command_type="modal",
                    command_name=custom_id,
                    user=user_str,
                    user_id=user_id,
                    guild_name=guild_name,
                    guild_id=guild_id,
                    channel_name=channel_name,
                    channel_id=channel_id,
                    status="submitted",
                    trace_id=tid,
                )

        # 註冊 CommandTree 全域錯誤捕捉器
        @bot.tree.error
        async def on_app_command_error(
            interaction: discord.Interaction,
            error: discord.app_commands.AppCommandError,
        ) -> None:
            """全域捕捉並記錄斜線指令異常，防止靜默崩潰。"""
            cmd_name = getattr(interaction.command, "name", "unknown")
            tid = get_trace_id() or new_trace_id("err")
            logger.error("Slash command /%s failed with error: %s", cmd_name, error, exc_info=True)
            log_error(
                event="command_error",
                trace_id=tid,
                error_type=type(error).__name__,
                message=str(error),
                extra={
                    "command": cmd_name,
                    "user": str(interaction.user),
                    "guild_id": interaction.guild_id or 0,
                    "channel_id": interaction.channel_id or 0,
                },
            )
            # 友善回應用戶
            try:
                err_text = f"❌ 執行指令 `/{cmd_name}` 時發生錯誤：{error}"
                if interaction.response.is_done():
                    await interaction.followup.send(err_text, ephemeral=True)
                else:
                    await interaction.response.send_message(err_text, ephemeral=True)
            except Exception as e:
                logger.debug("Failed to send error response to interaction: %s", e)

        # 註冊前綴指令監聽
        @bot.event
        async def on_command(ctx: commands.Context[Any]) -> None:
            tid = new_trace_id("prefix")
            set_trace_id(tid)
            setattr(ctx, "_start_timer", make_timer())
            cmd_name = ctx.command.name if ctx.command else "unknown"
            logger.info(
                "[PrefixCommand Invoked] User: %s (%s) | Channel: #%s | Command: !%s",
                ctx.author.display_name,
                ctx.author.id,
                getattr(ctx.channel, "name", "DM"),
                cmd_name,
            )

        @bot.event
        async def on_command_completion(ctx: commands.Context[Any]) -> None:
            timer = getattr(ctx, "_start_timer", None)
            latency_ms = timer() if timer else 0
            cmd_name = ctx.command.name if ctx.command else "unknown"
            logger.info("[PrefixCommand Completed] !%s in %d ms", cmd_name, latency_ms)
            log_command(
                command_type="prefix",
                command_name=cmd_name,
                user=f"{ctx.author.display_name} ({ctx.author.name})",
                user_id=ctx.author.id,
                guild_name=ctx.guild.name if ctx.guild else "DM",
                guild_id=ctx.guild.id if ctx.guild else 0,
                channel_name=getattr(ctx.channel, "name", "DM") if ctx.channel else "Unknown",
                channel_id=ctx.channel.id if ctx.channel else 0,
                latency_ms=latency_ms,
                status="success",
            )

        @bot.event
        async def on_command_error(ctx: commands.Context[Any], error: Exception) -> None:
            # 忽略找不到前綴指令的錯誤（群組成員打 !xxx 不影響 slash commands）
            if isinstance(error, commands.CommandNotFound):
                return
            logger.error("Command error in %s: %s", ctx.command, error, exc_info=True)
            log_error(
                event="prefix_command_error",
                error_type=type(error).__name__,
                message=str(error),
                extra={
                    "command": str(ctx.command),
                    "user": f"{ctx.author.display_name} ({ctx.author.name})",
                    "channel_id": ctx.channel.id if ctx.channel else 0,
                },
            )

        await bot.start(settings.DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())