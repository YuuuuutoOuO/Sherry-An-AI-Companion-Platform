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
import sys
from pathlib import Path

import discord
from discord.ext import commands

import settings

# ---------------------------------------------------------------------------
# Ensure data directory exists before FileHandler is created
# ---------------------------------------------------------------------------
settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
settings.FILE_DIR.mkdir(parents=True, exist_ok=True)
settings.SOUL_DIR.mkdir(parents=True, exist_ok=True)
settings.THINKING_LOG_DIR.mkdir(parents=True, exist_ok=True)
settings.CHAT_LOG_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Logging setup (must happen before any other import that logs)
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(settings.BOT_LOG_PATH, encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Core Cog extension paths (relative dotted module paths)
# ---------------------------------------------------------------------------
EXTENSIONS: list[str] = [
    "handlers.bot_brain",
    "handlers.social",
    "handlers.vibe_manager",
    # "handlers.web_explorer",
    "handlers.music",
    "handlers.memory",
    "handlers.admin",
    "handlers.voice",
    "handlers.scheduler",
    "handlers.executor",
    "handlers.help_cog",
    "handlers.welcome_cog",
    "handlers.heartbeat",
]

# ---------------------------------------------------------------------------
# Optional Mod Directory (auto-discovered plugins)
# 凡是 ./mod/<name>/cog.py 存在的子目錄，都會被自動嘗試載入。
# 失敗時只輸出 WARNING，絕不影響雪莉主程式。
# ---------------------------------------------------------------------------
BASE_DIR: Path = Path(__file__).resolve().parent
MOD_DIR: Path = BASE_DIR / "mod"


def _discover_optional_extensions() -> list[str]:
    """掃描 ./mod/ 目錄，自動找出含有 cog.py 的子資料夾。"""
    found: list[str] = []
    if not MOD_DIR.exists():
        return found
    for child in sorted(MOD_DIR.iterdir()):
        if child.is_dir() and (child / "cog.py").exists():
            found.append(f"mod.{child.name}.cog")
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
    bot = build_bot()

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
            bot._start_time = time.time()
            try:
                synced = await bot.tree.sync()
                logger.info("Synced %d slash command(s).", len(synced))
            except Exception as exc:
                logger.error("Failed to sync slash commands: %s", exc)

            # --- Startup Summary Banner ---
            banner_lines = [
                "=" * 60,
                "🚀 SYSTEM STARTUP COMPLETE",
                "=" * 60,
                f"🤖 Bot Name : {bot.user}",
                f"🆔 Bot ID   : {bot.user.id}",
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

            async def presence_loop():
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
        async def on_command_error(ctx: commands.Context, error: Exception) -> None:
            # 忽略找不到前綴指令的錯誤（群組成員打 !xxx 不影響 slash commands）
            if isinstance(error, commands.CommandNotFound):
                return
            logger.error("Command error in %s: %s", ctx.command, error)

        await bot.start(settings.DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())