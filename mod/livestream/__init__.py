"""
mod/livestream
==============
Sherry Livestream Display UI & Video Player Module.
"""

from mod.livestream.clients.broadcaster import LivestreamBroadcaster
from mod.livestream.cog import LivestreamCog
from mod.livestream.contracts import ILivestreamBroadcaster, ILivestreamService
from mod.livestream.domain import (
    MediaItem,
    PlayAction,
    PlaybackStatus,
    StreamEvent,
    StreamPlaybackState,
)
from mod.livestream.service import LivestreamService

MOD_INFO = {
    "id": "livestream",
    "name": "實況劇院與影音播放",
    "icon": "📺",
    "description": "支援 Discord 點播 YouTube 與本地影片至實況大螢幕，具備選集連播、影音狀態即時控制與播放器伺服器。",
    "category": "影音 / Livestream",
    "version": "1.0.0",
    "has_web_ui": False,  # 實況劇院運行於獨立伺服器 (PORT 8764)，非主要 Web UI 側邊欄外掛
    "public_routes": ["/api/livestream/"],
}

__all__ = [
    "MOD_INFO",
    "PlayAction",
    "PlaybackStatus",
    "MediaItem",
    "StreamPlaybackState",
    "StreamEvent",
    "ILivestreamBroadcaster",
    "ILivestreamService",
    "LivestreamService",
    "LivestreamBroadcaster",
    "LivestreamCog",
]
