import logging
import sys
import threading
import time
import aiohttp
import discord
from discord.ext import commands, tasks

import settings

logger = logging.getLogger(__name__)

class DiscordChannelHandler(logging.Handler):
    def __init__(self, bot: commands.Bot, level: int = logging.WARNING) -> None:
        super().__init__(level)
        self.bot = bot
        self.setFormatter(logging.Formatter('**[%(levelname)s]** %(name)s:\n```text\n%(message)s\n```'))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            channel_id = getattr(settings, "HEALTH_ALERT_CHANNEL_ID", 0)
            user_ids = settings.get_alert_recipient_user_ids() if hasattr(settings, "get_alert_recipient_user_ids") else []
            if channel_id == 0 and not user_ids:
                return
            msg = self.format(record)
            # 確保在事件迴圈內執行發送
            loop = self.bot.loop
            if loop and loop.is_running():
                loop.create_task(self._send_log(channel_id, user_ids, msg))
        except Exception:
            self.handleError(record)

    async def _send_log(self, channel_id: int, user_ids: list[int], msg: str) -> None:
        """非同步發送日誌訊息至頻道與管理員私訊。

        Args:
            channel_id: 目標 Discord 頻道 ID（若為 0 則不發送）。
            user_ids: 接收私訊的 Discord 使用者 ID 清單。
            msg: 欲發送的日誌字串內容。
        """
        truncated = msg[:2000]
        if channel_id != 0:
            try:
                channel = self.bot.get_channel(channel_id)
                if channel and hasattr(channel, "send"):
                    await channel.send(truncated)
            except Exception:
                pass

        for uid in user_ids:
            try:
                user = self.bot.get_user(uid)
                if user is None:
                    user = await self.bot.fetch_user(uid)
                if user and hasattr(user, "send"):
                    await user.send(truncated)
            except discord.Forbidden:
                sys.stderr.write(f"[DiscordAlert] ⚠️ 無法私訊 User ID {uid}：對方關閉了伺服器成員私訊權限 (403 Forbidden)\n")
            except Exception as exc:
                sys.stderr.write(f"[DiscordAlert] ⚠️ 發送私訊給 User ID {uid} 失敗: {exc}\n")


class HealthMonitor(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._discord_handler: DiscordChannelHandler | None = None
        self._perf_lock = threading.Lock()
        self._perf_sample = 0
        self._perf_sum_tok_s = 0.0
        self._perf_window_start = time.monotonic()
        self._perf_consecutive_slow = 0
        self._perf_last_alert = 0.0

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
        
        self._session: aiohttp.ClientSession | None = aiohttp.ClientSession()
        self._health_loop.start()
        logger.info("HealthMonitor: 啟動 Ollama 狀態監控迴圈與 Discord 頻道日誌收集")

    async def cog_unload(self) -> None:
        if self._discord_handler:
            logging.getLogger().removeHandler(self._discord_handler)
            self._discord_handler = None
            
        if self._session and not self._session.closed:
            await self._session.close()
            
        self._health_loop.cancel()
        logger.info("HealthMonitor: 關閉監控迴圈與日誌收集")

    async def ping_ollama(self) -> bool:
        try:
            if not self._session or self._session.closed:
                self._session = aiohttp.ClientSession()
            async with self._session.get(settings.OLLAMA_BASE_URL, timeout=aiohttp.ClientTimeout(total=settings.HEALTH_CHECK_TIMEOUT)) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def notify_admin(self, message: str) -> None:
        """發送系統警報至警報頻道與管理員/指定使用者私訊。

        Args:
            message: 警報內容文字。
        """
        channel_id = getattr(settings, "HEALTH_ALERT_CHANNEL_ID", 0)
        user_ids = settings.get_alert_recipient_user_ids() if hasattr(settings, "get_alert_recipient_user_ids") else []
        if channel_id == 0 and not user_ids:
            return

        alert_text = f"⚠️ **[系統警報]** {message}"[:2000]

        if channel_id != 0:
            channel = self.bot.get_channel(channel_id)
            if channel and hasattr(channel, "send"):
                try:
                    await channel.send(alert_text)
                except Exception:
                    pass
            else:
                logger.warning(f"HealthMonitor: 無法取得警告頻道 {channel_id}")

        for uid in user_ids:
            try:
                user = self.bot.get_user(uid)
                if user is None:
                    user = await self.bot.fetch_user(uid)
                if user and hasattr(user, "send"):
                    await user.send(alert_text)
            except discord.Forbidden:
                sys.stderr.write(f"[DiscordAlert] ⚠️ 無法私訊 User ID {uid}：對方關閉了伺服器成員私訊權限 (403 Forbidden)\n")
            except Exception as exc:
                sys.stderr.write(f"[DiscordAlert] ⚠️ 發送私訊給 User ID {uid} 失敗: {exc}\n")

    def record_tok_speed(self, tok_s: float) -> None:
        """由 BotBrain 回報每則回應的真實生成速度，供效能看門狗評估。

        使用滑動式觀察窗口：每次呼叫時若已跨越窗口時長，先結算上一窗口再開新窗。
        累積 tok/s 樣本，足夠樣本且連續多窗平均都低於門檻時，發送重啟提醒。
        本方法為同步計數，只更新記憶體狀態，實際發送由後續 _health_loop 週期處理，
        避免在串流 hot-path 上做網路 I/O。
        """
        if not getattr(settings, "PERF_WATCH_ENABLED", True) or tok_s <= 0:
            return
        window_len = float(getattr(settings, "PERF_WATCH_WINDOW_SECONDS", 900))
        min_samples = int(getattr(settings, "PERF_WATCH_MIN_SAMPLES", 5))
        consecutive_needed = int(getattr(settings, "PERF_WATCH_CONSECUTIVE_WINDOWS", 2))
        threshold = float(getattr(settings, "PERF_WATCH_TOK_S_THRESHOLD", 6.0))
        cooldown = float(getattr(settings, "PERF_WATCH_COOLDOWN_SECONDS", 21600))

        with self._perf_lock:
            now = time.monotonic()
            # 結算已進行的窗口：可能一次跨越多個窗口，循環處理
            while now - self._perf_window_start >= window_len:
                self._close_perf_window(
                    min_samples=min_samples,
                    consecutive_needed=consecutive_needed,
                    threshold=threshold,
                    cooldown=cooldown,
                    now=now,
                )
                self._perf_window_start += window_len
                self._perf_sample = 0
                self._perf_sum_tok_s = 0.0

            self._perf_sample += 1
            self._perf_sum_tok_s += tok_s

    def _close_perf_window(self, *, min_samples: int, consecutive_needed: int, threshold: float, cooldown: float, now: float) -> None:
        """結算一個觀察窗口：樣本足夠且平均過慢則累計失敗次數。"""
        if self._perf_sample < min_samples:
            self._perf_consecutive_slow = 0
            return
        avg_tok_s = self._perf_sum_tok_s / self._perf_sample if self._perf_sample else 0.0
        if avg_tok_s < threshold:
            self._perf_consecutive_slow += 1
            logger.warning(
                "HealthMonitor: 效能窗口結算，平均 %.1f tok/s < %.1f (樣本 %d)，連續慢 %d/%d 窗",
                avg_tok_s, threshold, self._perf_sample,
                self._perf_consecutive_slow, consecutive_needed,
            )
            has_channel = getattr(settings, "HEALTH_ALERT_CHANNEL_ID", 0) != 0
            has_users = bool(settings.get_alert_recipient_user_ids() if hasattr(settings, "get_alert_recipient_user_ids") else [])
            if (
                self._perf_consecutive_slow >= consecutive_needed
                and (now - self._perf_last_alert) >= cooldown
                and (has_channel or has_users)
            ):
                self._perf_last_alert = now
                self._perf_consecutive_slow = 0
                alert_msg = (
                    "推理引擎疑似衰退（最近幾則平均 "
                    f"**{avg_tok_s:.1f} tok/s**，低於門檻 {threshold}）。\n"
                    "KV 前綴快取累積過久可能導致降速。**建議重啟 LLM 引擎**以回復效能。"
                )
                self.bot.loop.create_task(self.notify_admin(alert_msg))
        else:
            self._perf_consecutive_slow = 0

    @tasks.loop(seconds=settings.HEALTH_CHECK_INTERVAL_SECONDS)
    async def _health_loop(self) -> None:
        is_ok = await self.ping_ollama()
        if not is_ok:
            logger.error("HealthMonitor: Ollama is unreachable!")
            await self.notify_admin("Ollama 伺服器無回應，推理引擎可能已下線！")

    @_health_loop.before_loop
    async def before_loop(self) -> None:
        await self.bot.wait_until_ready()
