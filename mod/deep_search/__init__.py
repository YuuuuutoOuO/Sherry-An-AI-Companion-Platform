"""
mod/deep_search
===============
地端 LLM / SLM 多輪自主深度研究與報告推演模組。
支援 10~30 分鐘級深度推演、連網檢索、文獻精讀、批判性反思與論文級報告產出。
"""

from __future__ import annotations

__version__ = "1.1.0"

MOD_INFO = {
    "id": "deep_search",
    "name": "深度研究主控台",
    "icon": "🔬",
    "description": "多輪自主思考、連網檢索與論文級學術報告生成 (支援 10~30 分鐘深思研究與即時進度追蹤)。",
    "category": "研究 / Deep Research",
    "version": __version__,
    "author": "Sherry Team",
    "has_web_ui": True,
    "nav_id": "deep-search",
    "nav_title": "深度研究主控台",
    "nav_icon": "🔬",
    "view_title": "🔬 地端 LLM / SLM 深度研究控制台",
    "admin_only": False,
    "order": 45,
    "script_file": "deep_search.js",
}

__all__ = ["__version__", "MOD_INFO"]
