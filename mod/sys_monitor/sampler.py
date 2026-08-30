"""
sampler.py  (mod/sys_monitor)
=============================
本機系統資源採樣器。

使用 psutil (已安裝) 採樣整機與主程式自身之 CPU / DRAM / Storage，
並以子行程呼叫 nvidia-smi 取得 GPU 利用率 / VRAM / 溫度。
所有讀取皆為唯讀且低干擾，不漏進主事件迴圈造成阻塞。

采樣結果以純 dict 回傳，方便由上層序列化為單行 JSON。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from typing import Any

import psutil

from . import config

logger = logging.getLogger(__name__)

# 快取主程式自身的 psutil.Process (僅建立一次，避免反覆查詢)
_proc: psutil.Process | None = None
_proc_pid: int = int(os.getpid())


def _get_process() -> psutil.Process | None:
    """取得主程式自身的 psutil.Process 實例，並在 PID 變更時刷新。"""
    global _proc, _proc_pid
    pid = int(os.getpid())
    if _proc is None or _proc_pid != pid:
        try:
            _proc = psutil.Process(pid)
            _proc_pid = pid
        except (psutil.Error, Exception):  # noqa: BLE001
            _proc = None
    return _proc


def _round(value: float | None, ndigits: int = 2) -> float | None:
    """安全四捨五入，避免回傳非數值。"""
    if value is None:
        return None
    try:
        return round(float(value), ndigits)
    except (TypeError, ValueError):
        return None


def sample_system() -> dict[str, Any]:
    """採樣整台機器之 CPU / DRAM / Storage 使用量。

    Returns:
        包含整體與主程序自身指標之非同步採樣字典 (不含 GPU)。
    """
    data: dict[str, Any] = {
        "type": "system",
        "cpu": {},
        "memory": {},
        "storage": {},
        "process": {},
    }

    # ── CPU ──
    try:
        data["cpu"]["percent"] = _round(psutil.cpu_percent(interval=None))
        data["cpu"]["count"] = psutil.cpu_count(logical=True)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[SysMonitor] CPU 採樣失敗: %s", exc)

    # ── DRAM ──
    try:
        mem = psutil.virtual_memory()
        data["memory"]["total"] = mem.total
        data["memory"]["used"] = mem.used
        data["memory"]["available"] = mem.available
        data["memory"]["percent"] = _round(mem.percent)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[SysMonitor] DRAM 採樣失敗: %s", exc)

    # ── Storage (所有掛載磁碟分割區) ──
    try:
        parts = []
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
                parts.append(
                    {
                        "device": part.device,
                        "mountpoint": part.mountpoint,
                        "fstype": part.fstype,
                        "total": usage.total,
                        "used": usage.used,
                        "free": usage.free,
                        "percent": _round(usage.percent),
                    }
                )
            except (psutil.Error, OSError):
                continue
        data["storage"]["partitions"] = parts
        data["storage"]["total"] = sum(p["total"] for p in parts)
        data["storage"]["used"] = sum(p["used"] for p in parts)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[SysMonitor] Storage 採樣失敗: %s", exc)

    # ── 主程式自身 (CPU / RAM / 磁碟讀寫 / 開檔數) ──
    proc = _get_process()
    if proc is not None:
        try:
            data["process"]["pid"] = proc.pid
            data["process"]["name"] = proc.name()
            data["process"]["cpu_percent"] = _round(proc.cpu_percent(interval=None))
            pmem = proc.memory_info()
            data["process"]["memory_rss"] = pmem.rss if hasattr(pmem, "rss") else None
            data["process"]["memory_vms"] = pmem.vms if hasattr(pmem, "vms") else None
        except (psutil.Error, Exception):  # noqa: BLE001
            pass
        try:
            io = proc.io_counters()
            data["process"]["read_bytes"] = io.read_bytes if io else None
            data["process"]["write_bytes"] = io.write_bytes if io else None
        except (psutil.Error, AttributeError):
            pass
        try:
            data["process"]["threads"] = proc.num_threads()
            data["process"]["open_files"] = len(proc.open_files())
        except (psutil.Error, Exception):  # noqa: BLE001
            pass

    return data


async def sample_gpu() -> dict[str, Any] | None:
    """透過 nvidia-smi 採樣 GPU 利用率 / VRAM / 溫度。

    Returns:
        GPU 資訊字典；若未啟用、偵測不到或呼叫超時/失敗則回傳 None。
    """
    if not config.GPU_ENABLED:
        return None

    smi = None
    try:
        smi = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=config.GPU_SMI_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError:
        logger.debug("[SysMonitor] 找不到 nvidia-smi，GPU 監測已停用。")
        return None
    except subprocess.TimeoutExpired:
        logger.debug("[SysMonitor] nvidia-smi 呼叫逾時，略過本次 GPU 採樣。")
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("[SysMonitor] nvidia-smi 呼叫異常: %s", exc)
        return None

    if smi.returncode != 0 or not smi.stdout.strip():
        return None

    gpus: list[dict[str, Any]] = []
    for raw_line in smi.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 6:
            continue
        try:
            gpus.append(
                {
                    "index": parts[0],
                    "name": parts[1],
                    "utilization_percent": _round(parts[2]),
                    "memory_used_mb": _round(parts[3]),
                    "memory_total_mb": _round(parts[4]),
                    "temperature_c": _round(parts[5]),
                }
            )
        except (TypeError, ValueError):
            continue

    if not gpus:
        return None

    # 計算 GPU 平均值 (供 Web UI 摘要卡片使用)
    util = [g["utilization_percent"] for g in gpus if g["utilization_percent"] is not None]
    mem_used = [g["memory_used_mb"] for g in gpus if g["memory_used_mb"] is not None]
    mem_total = [g["memory_total_mb"] for g in gpus if g["memory_total_mb"] is not None]
    temp = [g["temperature_c"] for g in gpus if g["temperature_c"] is not None]

    return {
        "type": "gpu",
        "gpus": gpus,
        "summary": {
            "gpu_count": len(gpus),
            "avg_utilization_percent": _round(sum(util) / len(util)) if util else None,
            "memory_used_mb": sum(mem_used) if mem_used else None,
            "memory_total_mb": sum(mem_total) if mem_total else None,
            "memory_percent": (
                _round(sum(mem_used) / sum(mem_total) * 100)
                if mem_used and mem_total
                else None
            ),
            "avg_temperature_c": _round(sum(temp) / len(temp)) if temp else None,
        },
    }


def serialize(sample: dict[str, Any], timestamp: str) -> str:
    """將採樣字典序列化為單行 JSON 字串，供 JSONL 寫入。"""
    return json.dumps({"t": timestamp, **sample}, ensure_ascii=False)
