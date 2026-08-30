"""
mod.sys_monitor
===============
本機系統資源監控外掛 (System Monitor Plugin).

以記憶體緩衝 + 批次寫入方式持續採樣本機與主程式自身的
CPU / DRAM / Storage / GPU 使用量，每 30 秒批次 flush 為單行 JSONL，
並提供 Discord `/sys_status` 指令與 Web UI 即時主控台檢視。
旨在「零頻繁硬碟寫入」地低干擾監測整台機器與雪莉主程式效能。
"""

from __future__ import annotations

MOD_INFO = {
    "id": "sys_monitor",
    "name": "🖥️ 系統資源監控",
    "icon": "🖥️",
    "description": "監測本機與雪莉主程式的 CPU / DRAM / Storage / GPU 使用量，以記憶體緩衝＋批次寫入降低硬碟 I/O，提供即時指標與歷史趨勢。",
    "category": "系統 / System",
    "version": "1.0.0",
    "author": "Sherry Team",
    "has_web_ui": True,
    "nav_id": "sys-monitor",
    "nav_title": "系統資源監控",
    "nav_icon": "🖥️",
    "view_title": "🖥️ 系統資源監控主控台",
    "admin_only": False,
    "order": 10,
    "script_file": "sys_monitor.js",
}

__all__ = ["MOD_INFO"]
