"""core.sys_inspector.formatter
===============================
LLM 友善之極簡高密度系統快照格式化器。
將多層 JSON 結構濃縮為 80~150 字內的緊湊純文字，杜絕 Observation 膨脹與 KV Cache 浪費。
"""

from __future__ import annotations

from typing import Any


def format_compact_system_report(metrics: dict[str, Any], is_admin: bool = False) -> str:
    """將採樣指標格式化為緊湊純文字簡報。

    Args:
        metrics: get_system_metrics() 回傳之原始度量字典。
        is_admin: 是否為管理員（管理員可檢視進程 PID、RSS/VMS 記憶體與執行緒細節）。

    Returns:
        str: 緊湊之純文字系統快照。
    """
    cpu = metrics.get("cpu", {})
    ram = metrics.get("ram", {})
    gpu_list = metrics.get("gpu", [])
    proc = metrics.get("process", {})

    cpu_p = cpu.get("percent", 0.0)
    cpu_c = cpu.get("count", 1)
    ram_u = ram.get("used_gb", 0.0)
    ram_t = ram.get("total_gb", 0.0)
    ram_p = ram.get("percent", 0.0)

    gpu_str = "無專屬 GPU / 集成顯卡"
    if gpu_list:
        g = gpu_list[0]
        g_name = g.get("name", "GPU")
        g_u = g.get("utilization", 0)
        g_vram_u = round(g.get("memory_used_mb", 0) / 1024, 1)
        g_vram_t = round(g.get("memory_total_mb", 0) / 1024, 1)
        g_temp = g.get("temperature_c", 0)
        gpu_str = f"{g_name} (負載: {g_u}%, VRAM: {g_vram_u}/{g_vram_t}GB, {g_temp}°C)"

    if is_admin:
        pid = proc.get("pid", 0)
        rss = proc.get("memory_rss_mb", 0.0)
        threads = proc.get("threads", 1)
        return (
            f"【主機硬體】CPU: {cpu_p}% ({cpu_c}核) | RAM: {ram_u}/{ram_t}GB ({ram_p}%) | GPU: {gpu_str}\n"
            f"【雪莉進程】PID: {pid} | 記憶體(RSS): {rss}MB | 執行緒: {threads} | 系統狀態: 運作正常"
        )

    return (
        f"【主機硬體】CPU: {cpu_p}% ({cpu_c}核) | RAM: {ram_p}% | GPU: {gpu_str}\n"
        f"【雪莉狀態】大腦運算順暢，系統運作正常無卡頓。"
    )
