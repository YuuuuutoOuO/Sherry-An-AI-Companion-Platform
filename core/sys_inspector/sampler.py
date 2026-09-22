"""core.sys_inspector.sampler
=============================
主機與進程系統資源採樣器。
封裝 psutil、nvidia-smi 與進程資源採樣，支援 TTL 防抖快取與完全非同步執行，杜絕主事件迴圈阻塞。
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import time
from typing import Any

logger = logging.getLogger(__name__)

_last_net_bytes: tuple[int, int, float] | None = None
_CACHED_SYSTEM_METRICS: dict[str, Any] = {}
_LAST_METRICS_TIME: float = 0.0
_DEFAULT_METRICS_CACHE_TTL: float = 2.0


def _sample_gpu() -> list[dict[str, Any]]:
    """以子進程呼叫 nvidia-smi 採樣 GPU 狀態。"""
    gpu_info: list[dict[str, Any]] = []
    try:
        cmd = [
            "nvidia-smi",
            "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
            "--format=csv,noheader,nounits",
        ]
        out = subprocess.check_output(cmd, encoding="utf-8", timeout=1.5).strip()
        if out:
            for line in out.splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 5:
                    gpu_name = parts[0]
                    gpu_util = int(parts[1]) if parts[1].isdigit() else 0
                    mem_used = int(parts[2]) if parts[2].isdigit() else 0
                    mem_total = int(parts[3]) if parts[3].isdigit() else 1
                    temp = int(parts[4]) if parts[4].isdigit() else 0
                    gpu_info.append(
                        {
                            "name": gpu_name,
                            "utilization": gpu_util,
                            "memory_used_mb": mem_used,
                            "memory_total_mb": mem_total,
                            "memory_percent": round((mem_used / mem_total) * 100, 1),
                            "temperature_c": temp,
                        }
                    )
    except Exception:
        pass
    return gpu_info


def get_system_metrics() -> dict[str, Any]:
    """同步採樣 CPU、RAM、網路、GPU 與主進程資源指標。"""
    global _last_net_bytes

    now = time.time()
    cpu_percent = 0.0
    cpu_count = 1
    cpu_freq_mhz = 0.0
    ram_total_gb = 0.0
    ram_used_gb = 0.0
    ram_percent = 0.0
    sent_rate_kbps = 0.0
    recv_rate_kbps = 0.0
    total_sent_mb = 0.0
    total_recv_mb = 0.0

    proc_info: dict[str, Any] = {
        "pid": os.getpid(),
        "memory_rss_mb": 0.0,
        "memory_vms_mb": 0.0,
        "cpu_percent": 0.0,
        "threads": 1,
    }

    try:
        import psutil

        cpu_percent = psutil.cpu_percent(interval=None)
        cpu_count = psutil.cpu_count(logical=True) or 1
        cpu_freq = psutil.cpu_freq()
        cpu_freq_mhz = round(cpu_freq.current, 1) if cpu_freq else 0.0

        mem = psutil.virtual_memory()
        ram_total_gb = round(mem.total / (1024**3), 2)
        ram_used_gb = round(mem.used / (1024**3), 2)
        ram_percent = mem.percent

        net_io = psutil.net_io_counters()
        total_sent_mb = round(net_io.bytes_sent / (1024**2), 1)
        total_recv_mb = round(net_io.bytes_recv / (1024**2), 1)

        if _last_net_bytes is not None:
            prev_sent, prev_recv, prev_time = _last_net_bytes
            dt = max(0.1, now - prev_time)
            sent_rate_kbps = round((net_io.bytes_sent - prev_sent) / 1024 / dt, 1)
            recv_rate_kbps = round((net_io.bytes_recv - prev_recv) / 1024 / dt, 1)

        _last_net_bytes = (net_io.bytes_sent, net_io.bytes_recv, now)

        try:
            curr_proc = psutil.Process(os.getpid())
            pmem = curr_proc.memory_info()
            proc_info["memory_rss_mb"] = round(pmem.rss / (1024**2), 1)
            proc_info["memory_vms_mb"] = round(pmem.vms / (1024**2), 1)
            proc_info["cpu_percent"] = round(curr_proc.cpu_percent(interval=None), 1)
            proc_info["threads"] = curr_proc.num_threads()
        except Exception:
            pass

    except ImportError:
        pass

    gpu_info = _sample_gpu()

    return {
        "cpu": {
            "percent": cpu_percent,
            "count": cpu_count,
            "freq_mhz": cpu_freq_mhz,
        },
        "ram": {
            "used_gb": ram_used_gb,
            "total_gb": ram_total_gb,
            "percent": ram_percent,
        },
        "network": {
            "sent_rate_kbps": sent_rate_kbps,
            "recv_rate_kbps": recv_rate_kbps,
            "total_sent_mb": total_sent_mb,
            "total_recv_mb": total_recv_mb,
        },
        "gpu": gpu_info,
        "process": proc_info,
        "timestamp": now,
    }


async def get_system_metrics_async(ttl: float = _DEFAULT_METRICS_CACHE_TTL) -> dict[str, Any]:
    """非同步取得系統度量，內建 TTL 防抖快取與 to_thread 執行保護。"""
    global _CACHED_SYSTEM_METRICS, _LAST_METRICS_TIME
    now = time.time()
    if (now - _LAST_METRICS_TIME < ttl) and _CACHED_SYSTEM_METRICS:
        return _CACHED_SYSTEM_METRICS

    metrics = await asyncio.to_thread(get_system_metrics)
    _CACHED_SYSTEM_METRICS = metrics
    _LAST_METRICS_TIME = now
    return metrics
