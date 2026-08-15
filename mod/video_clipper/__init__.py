"""
mod/video_clipper package initialization.
Exports core domain models, service, and abstract contract interface.
"""

from mod.video_clipper.contracts import AbstractVideoGifClient
from mod.video_clipper.domain import TimeRange, VideoClipRequest, VideoClipResult
from mod.video_clipper.service import VideoClipperService, parse_timestamp

__all__ = [
    "AbstractVideoGifClient",
    "TimeRange",
    "VideoClipRequest",
    "VideoClipResult",
    "VideoClipperService",
    "parse_timestamp",
]
