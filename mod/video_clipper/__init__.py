"""
mod/video_clipper package initialization.
Exports core domain models, service, and abstract contract interface.
"""

from mod.video_clipper.contracts import AbstractVideoGifClient
from mod.video_clipper.domain import TimeRange, VideoClipRequest, VideoClipResult
from mod.video_clipper.service import VideoClipperService, parse_timestamp

MOD_INFO = {
    "id": "video_clipper",
    "name": "YouTube 影片轉 GIF 動圖",
    "icon": "🎬",
    "description": "透過 /video 指令將 YouTube 影片指定時間片段（例如 1:30~1:45）快速轉換為精華 GIF 動圖並發送至頻道。",
    "category": "工具 / Tools",
    "version": "1.0.0",
}

__all__ = [
    "MOD_INFO",
    "AbstractVideoGifClient",
    "TimeRange",
    "VideoClipRequest",
    "VideoClipResult",
    "VideoClipperService",
    "parse_timestamp",
]
