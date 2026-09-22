"""
version.py
==========
Single source of truth for Sherry's application version.

Import APP_VERSION from this module anywhere a version string is needed
(Web UI, heartbeat, logs, docs tooling). Bump it once, here, on each release.
"""

from __future__ import annotations

APP_NAME: str = "橘雪莉 (Sherry)"
APP_VERSION: str = "v1.9.0-dev"

__version__: str = APP_VERSION
__all__ = ["APP_NAME", "APP_VERSION", "__version__"]