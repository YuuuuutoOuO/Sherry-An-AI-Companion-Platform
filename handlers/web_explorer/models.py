from typing import TypedDict

class VideoMeta(TypedDict):
    """提取 Youtube 清單回報用的輕量最小化元資料結構"""
    video_id: str
    title: str
    channel_name: str
    url: str