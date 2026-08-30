"""
cog.py  (mod/sys_monitor)
=========================
系統資源監控 Cog。

背景迴圈每 INTERVAL_SECONDS (預設 5 秒) 採樣一次系統與主程式資源，
累積於記憶體緩衝，每 FLUSH_SECONDS (預設 30 秒) 批次寫入單行 JSONL，
以達到「零頻繁開關檔」的低干擾落盤目標。

另提供：
- Discord `/sys_status` Slash 指令，即時顯示目前資源佔用。
- Web UI 共用之 Cog 狀態查詢 (get_status_info / get_latest / get_history)。
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

from . import config
from .sampler import sample_gpu, sample_system

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")


def _fmt_bytes(value: float | int | None) -> str:
    """將位元組數格式化為人類可讀字串。"""
    if value is None:
        return "-"
    n = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0 or unit == "TB":
            return f"{n:.2f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0
    return f"{n:.2f} TB"


def _fmt_gb(value: float | int | None) -> str:
    """將 MB 數值格式化為 GB 字串。"""
    if value is None:
        return "-"
    return f"{float(value) / 1024:.1f} GB"


class SysMonitorCog(commands.Cog):
    """本機系統與主程式資源監控 Cog。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # 記憶體緩衝：儲存序列化前的採樣 dict，於 flush 時一次寫入
        self._sample_buffer: list[dict[str, Any]] = []
        self._last_flush_time: float = time.time()
        self._sample_count: int = 0
        self._flush_count: int = 0
        self._last_sample: dict[str, Any] | None = None
        # GPU 快取與可用旗標
        self._last_gpu: dict[str, Any] | None = None
        self._gpu_available: bool = False
        self._enabled: bool = config.ENABLED

    # -----------------------------------------------------------------------
    # 生命週期
    # -----------------------------------------------------------------------
    async def cog_load(self) -> None:
        if self._enabled:
            self._monitor_loop.start()
            self._gpu_refresh_loop.start()
            logger.info(
                "[SysMonitor] 系統資源監控已啟動! (間隔: %gs, 批次 flush: %gs)",
                config.INTERVAL_SECONDS,
                config.FLUSH_SECONDS,
            )
        else:
            logger.debug("[SysMonitor] 模組在 config.py 中設定為停用。")

    async def cog_unload(self) -> None:
        if self._monitor_loop.is_running():
            self._monitor_loop.cancel()
        if self._gpu_refresh_loop.is_running():
            self._gpu_refresh_loop.cancel()
        # 關閉前最後一次將殘留緩衝寫入
        await self._flush_buffer(force=True)
        logger.info("[SysMonitor] 系統資源監控已停止。")

    # -----------------------------------------------------------------------
    # 背景採樣與 GPU 更新迴圈
    # -----------------------------------------------------------------------
    @tasks.loop(seconds=max(0.5, config.INTERVAL_SECONDS))
    async def _monitor_loop(self) -> None:
        """主採樣迴圈：採樣並累積至緩衝，到點則批次落盤。"""
        try:
            sample = sample_system()
            merged = {
                "t": _now_iso(),
                "type": "system",
                "cpu": sample.get("cpu"),
                "memory": sample.get("memory"),
                "storage": sample.get("storage"),
                "process": sample.get("process"),
                "gpu": self._last_gpu,
            }
            self._sample_buffer.append(merged)
            self._sample_count += 1
            self._last_sample = merged

            now = time.time()
            if now - self._last_flush_time >= config.FLUSH_SECONDS:
                await self._flush_buffer()
        except Exception:
            logger.exception("[SysMonitor] 採樣迴圈發生異常，略過本次採樣。")

    @tasks.loop(seconds=max(1.0, config.FLUSH_SECONDS))
    async def _gpu_refresh_loop(self) -> None:
        """獨立、較長間隔的 GPU 採樣迴圈，避免拖慢主採樣。"""
        try:
            gpu = await sample_gpu()
            if gpu is not None:
                self._last_gpu = gpu
                self._gpu_available = True
        except Exception:
            logger.exception("[SysMonitor] GPU 採樣迴圈發生異常。")

    async def _flush_buffer(self, force: bool = False) -> None:
        """將記憶體緩衝批次寫入 JSONL 檔案，並視情況修剪歷史行數。"""
        if not self._sample_buffer:
            return

        rows = self._sample_buffer
        self._sample_buffer = []
        self._last_flush_time = time.time()

        try:
            config.SERIES_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(config.SERIES_PATH, "a", encoding=config.ENCODING) as fh:
                for row in rows:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            self._flush_count += 1
            logger.debug(
                "[SysMonitor] 批次寫入 %d 筆採樣至 %s",
                len(rows),
                config.SERIES_PATH,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[SysMonitor] 批次寫入 JSONL 失敗: %s", exc)
            return

        # 修剪過多的歷史行數
        if force or config.MAX_SERIES_ROWS > 0:
            try:
                self._trim_series()
            except Exception:
                pass

    def _trim_series(self) -> None:
        """若 JSONL 行數超過上限，保留最新 MAX_SERIES_ROWS 行並原子重寫。"""
        if not config.SERIES_PATH.exists():
            return
        with open(config.SERIES_PATH, encoding=config.ENCODING) as fh:
            all_rows = fh.readlines()
        if len(all_rows) <= config.MAX_SERIES_ROWS:
            return
        keep = all_rows[-config.MAX_SERIES_ROWS:]
        tmp = config.SERIES_PATH.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding=config.ENCODING) as fh:
            fh.writelines(keep)
        os.replace(str(tmp), str(config.SERIES_PATH))

    # -----------------------------------------------------------------------
    # Web UI / 共用狀態查詢
    # -----------------------------------------------------------------------
    def get_status_info(self) -> dict[str, Any]:
        """取得監控器當前運行狀態 (供 Discord 與 Web UI 查詢)。"""
        return {
            "enabled": self._enabled,
            "interval_seconds": config.INTERVAL_SECONDS,
            "flush_seconds": config.FLUSH_SECONDS,
            "sample_count": self._sample_count,
            "flush_count": self._flush_count,
            "buffer_size": len(self._sample_buffer),
            "gpu_available": self._gpu_available,
            "history_path": str(config.SERIES_PATH),
        }

    def get_latest(self) -> dict[str, Any] | None:
        """取得最近一次採樣結果。"""
        return self._last_sample

    def get_status_summary(self) -> dict[str, Any]:
        """合併最近採樣與 GPU 快取的即時摘要 (供 Web UI / Discord 使用)。"""
        latest = self._last_sample or {}
        gpu = latest.get("gpu") or self._last_gpu or {}
        return {
            "cpu_percent": (latest.get("cpu") or {}).get("percent"),
            "memory_percent": (latest.get("memory") or {}).get("percent"),
            "storage_used": (latest.get("storage") or {}).get("used"),
            "storage_total": (latest.get("storage") or {}).get("total"),
            "process_cpu_percent": (latest.get("process") or {}).get("cpu_percent"),
            "process_memory_rss": (latest.get("process") or {}).get("memory_rss"),
            "process_write_bytes": (latest.get("process") or {}).get("write_bytes"),
            "gpu": gpu.get("summary") or {},
        }

    def get_history(self, limit: int = 60) -> list[dict[str, Any]]:
        """從 JSONL 讀回最近採樣記錄，供 Web UI 繪製趨勢。"""
        limit = max(1, min(int(limit or 60), 2000))
        rows: list[dict[str, Any]] = []
        if not config.SERIES_PATH.exists():
            return rows
        try:
            with open(config.SERIES_PATH, encoding=config.ENCODING) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        continue
        except Exception as exc:
            logger.warning("[SysMonitor] 讀取歷史失敗: %s", exc)
        return rows[-limit:]

    # -----------------------------------------------------------------------
    # Discord Slash 指令
    # -----------------------------------------------------------------------
    @app_commands.command(name="sys_status", description="顯示橘雪莉本機與主程式的即時系統資源佔用")
    async def sys_status_cmd(self, interaction: discord.Interaction) -> None:
        """即時顯示系統資源使用狀況。"""
        await interaction.response.defer()

        latest = self.get_latest()
        if not latest:
            await interaction.followup.send(
                "⚠️ 尚未取得任何採樣資料，請稍後再試。",
                ephemeral=True,
            )
            return

        cpu = latest.get("cpu") or {}
        memory = latest.get("memory") or {}
        process = latest.get("process") or {}
        storage = latest.get("storage") or {}
        gpu = latest.get("gpu") or {}

        embed = discord.Embed(
            title="🖥️ 系統資源即時狀態",
            color=0x38BDF8,
            timestamp=datetime.now(timezone.utc),
        )

        embed.add_field(
            name="💻 CPU",
            value=f"使用率: **{cpu.get('percent', '-')}%** / {cpu.get('count', '-')} 核心",
            inline=True,
        )
        embed.add_field(
            name="🧠 DRAM",
            value=(
                f"使用率: **{memory.get('percent', '-')}%**\n"
                f"{_fmt_bytes(memory.get('used'))} / {_fmt_bytes(memory.get('total'))}"
            ),
            inline=True,
        )
        embed.add_field(
            name="🗄️ Storage",
            value=(
                f"總用量: **{_fmt_bytes(storage.get('used'))}** / "
                f"{_fmt_bytes(storage.get('total'))}"
            ),
            inline=True,
        )

        gpu_sum = gpu.get("summary") or {}
        if gpu_sum:
            embed.add_field(
                name="🎮 GPU (NVIDIA)",
                value=(
                    f"利用率: **{gpu_sum.get('avg_utilization_percent', '-')}%**\n"
                    f"VRAM: {_fmt_gb(gpu_sum.get('memory_used_mb'))} / "
                    f"{_fmt_gb(gpu_sum.get('memory_total_mb'))}\n"
                    f"溫度: **{gpu_sum.get('avg_temperature_c', '-')}°C**"
                ),
                inline=True,
            )
        else:
            embed.add_field(
                name="🎮 GPU",
                value="未偵測到 (無 nvidia-smi)",
                inline=True,
            )

        embed.add_field(
            name="🐍 雪莉主程式",
            value=(
                f"CPU: **{process.get('cpu_percent', '-')}%**\n"
                f"RAM: **{_fmt_bytes(process.get('memory_rss'))}**\n"
                f"磁碟寫入: {_fmt_bytes(process.get('write_bytes'))}"
            ),
            inline=True,
        )

        embed.set_footer(
            text=f"採樣間隔 {config.INTERVAL_SECONDS}s · 每 {config.FLUSH_SECONDS}s 批次落盤"
        )
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    """動態載入 SysMonitorCog 插件。"""
    await bot.add_cog(SysMonitorCog(bot))
