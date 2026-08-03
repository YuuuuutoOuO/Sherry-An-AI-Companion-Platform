import asyncio
import logging
import aiohttp
from discord.ext import commands, tasks

import settings

logger = logging.getLogger(__name__)

class DiscordChannelHandler(logging.Handler):
    def __init__(self, bot: commands.Bot, level=logging.WARNING):
        super().__init__(level)
        self.bot = bot
        self.setFormatter(logging.Formatter('**[%(levelname)s]** %(name)s:\n```text\n%(message)s\n```'))

    def emit(self, record):
        try:
            if settings.HEALTH_ALERT_CHANNEL_ID == 0:
                return
            msg = self.format(record)
            channel = self.bot.get_channel(settings.HEALTH_ALERT_CHANNEL_ID)
            if channel:
                # 確保在事件迴圈內執行發送
                loop = self.bot.loop
                if loop and loop.is_running():
                    loop.create_task(self._send_log(channel, msg))
        except Exception:
            self.handleError(record)

    async def _send_log(self, channel, msg: str):
        try:
            # Discord message limit is 2000
            await channel.send(msg[:2000])
        except Exception:
            pass

class HealthMonitor(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._discord_handler = None

    async def cog_load(self) -> None:
        level_map = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL
        }
        level = level_map.get(getattr(settings, "DISCORD_LOG_LEVEL", "WARNING").upper(), logging.WARNING)
        
        self._discord_handler = DiscordChannelHandler(self.bot, level=level)
        logging.getLogger().addHandler(self._discord_handler)
        
        self._health_loop.start()
        logger.info("HealthMonitor: 啟動 Ollama 狀態監控迴圈與 Discord 頻道日誌收集")

    async def cog_unload(self) -> None:
        if self._discord_handler:
            logging.getLogger().removeHandler(self._discord_handler)
            self._discord_handler = None
            
        self._health_loop.cancel()
        logger.info("HealthMonitor: 關閉監控迴圈與日誌收集")

    async def ping_ollama(self) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(settings.OLLAMA_BASE_URL, timeout=aiohttp.ClientTimeout(total=settings.HEALTH_CHECK_TIMEOUT)) as resp:
                    return resp.status == 200
        except Exception:
            return False

    async def notify_admin(self, message: str) -> None:
        if settings.HEALTH_ALERT_CHANNEL_ID == 0:
            return
        channel = self.bot.get_channel(settings.HEALTH_ALERT_CHANNEL_ID)
        if channel:
            await channel.send(f"⚠️ **[系統警報]** {message}")
        else:
            logger.warning(f"HealthMonitor: 無法取得警告頻道 {settings.HEALTH_ALERT_CHANNEL_ID}")

    @tasks.loop(seconds=settings.HEALTH_CHECK_INTERVAL_SECONDS)
    async def _health_loop(self) -> None:
        is_ok = await self.ping_ollama()
        if not is_ok:
            logger.error("HealthMonitor: Ollama is unreachable!")
            await self.notify_admin("Ollama 伺服器無回應，推理引擎可能已下線！")

    @_health_loop.before_loop
    async def before_loop(self):
        await self.bot.wait_until_ready()
