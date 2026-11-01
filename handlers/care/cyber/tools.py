from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class CyberToolRegistry:
    """受控工具註冊表。

    只暴露唯讀、低風險的工具；所有結果都會被包成不可信資料回填 prompt。
    工具本身仍使用專案既有的 SSRF 防護與 semaphore。
    """

    def __init__(self, *, timeout: float = 20.0) -> None:
        self.timeout = max(3.0, float(timeout))

    async def run(self, name: str, args: dict[str, Any]) -> str:
        handler = {
            "web_search": self._web_search,
            "fetch_url": self._fetch_url,
        }.get(str(name or "").strip().lower())
        if handler is None:
            return f"error: unknown tool '{name}'"
        try:
            return await asyncio.wait_for(handler(args or {}), timeout=self.timeout)
        except asyncio.TimeoutError:
            return f"error: tool '{name}' timeout"
        except Exception as exc:
            logger.warning("[CyberV2] 工具 %s 失敗: %s", name, exc)
            return f"error: tool '{name}' failed: {exc}"

    async def _web_search(self, args: dict[str, Any]) -> str:
        query = str(args.get("query") or args.get("q") or "").strip()
        if not query:
            return "error: missing query"
        from handlers.social.tools.search_tools import fetch_web_and_entity_context

        context, sources = await fetch_web_and_entity_context(query)
        text = str(context or "").strip()
        if sources:
            text += f"\n\n來源：{sources}"
        return text[:4000] or "（沒有找到可用結果）"

    async def _fetch_url(self, args: dict[str, Any]) -> str:
        url = str(args.get("url") or "").strip()
        if not url:
            return "error: missing url"
        from handlers.social.tools.search_tools import fetch_and_extract_url

        title, content, method = await fetch_and_extract_url(url)
        body = f"{title}\n{url}\n{content}\n（萃取方式：{method}）".strip()
        return body[:4000] or "（頁面沒有可讀內容）"
