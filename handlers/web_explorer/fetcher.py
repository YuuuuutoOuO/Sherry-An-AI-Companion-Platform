import logging
import xml.etree.ElementTree as ET
import aiohttp
import settings
from handlers.web_explorer.models import VideoMeta

logger = logging.getLogger(__name__)

_YT_SEARCH_URL = settings.YOUTUBE_SEARCH_API_URL
_YT_RSS_URL = settings.YOUTUBE_RSS_FEED_URL
_YT_RSS_NS = settings.YOUTUBE_RSS_NS

async def fetch_via_api(session: aiohttp.ClientSession) -> list[VideoMeta]:
    """官方API拿貨(需要Youtube Token，可以抓地區當下總合熱門排行榜)"""
    params: dict[str, str | int] = {
        "part": "snippet",
        "chart": "mostPopular",
        "regionCode": "TW",
        "hl": "zh-TW",
        "maxResults": settings.YOUTUBE_MAX_RESULTS,
        "key": settings.YOUTUBE_API_KEY,
    }
    async with session.get(_YT_SEARCH_URL, params=params, timeout=aiohttp.ClientTimeout(total=settings.YOUTUBE_EXPLORER_TIMEOUT)) as resp:
        resp.raise_for_status()
        data = await resp.json()

    results: list[VideoMeta] = []
    for item in data.get("items", []):
        vid_id: str = item.get("id", {}).get("videoId", "")
        snippet = item.get("snippet", {})
        if not vid_id: continue
        results.append(
            VideoMeta(
                video_id=vid_id,
                title=snippet.get("title", "（無標題）"),
                channel_name=snippet.get("channelTitle", ""),
                url=f"https://www.youtube.com/watch?v={vid_id}",
            )
        )
    return results


async def fetch_via_rss(session: aiohttp.ClientSession) -> list[VideoMeta]:
    """免洗解析流抓貨法 (免代幣 / 純以指定單個頻道擷取最新資訊法)"""
    async with session.get(_YT_RSS_URL, timeout=aiohttp.ClientTimeout(total=settings.YOUTUBE_EXPLORER_TIMEOUT)) as resp:
        resp.raise_for_status()
        raw_xml = await resp.text()

    root = ET.fromstring(raw_xml)
    results: list[VideoMeta] = []

    for entry in root.findall(f"{{{_YT_RSS_NS}}}entry"):
        link_el = entry.find(f"{{{_YT_RSS_NS}}}link")
        title_el = entry.find(f"{{{_YT_RSS_NS}}}title")
        author_el = entry.find(f"{{{_YT_RSS_NS}}}author/{{{_YT_RSS_NS}}}name")

        url = link_el.attrib.get("href", "") if link_el is not None else ""
        title = title_el.text or "（無標題）" if title_el is not None else "（無標題）"
        channel_name = author_el.text or "" if author_el is not None else ""
        video_id = url.split("v=")[-1] if "v=" in url else ""

        if not url or not video_id: continue

        results.append(VideoMeta(video_id=video_id, title=title, channel_name=channel_name, url=url))
        if len(results) >= settings.YOUTUBE_MAX_RESULTS: break

    return results