from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from .contracts import ToolPolicy

logger = logging.getLogger(__name__)


class ToolDenied(RuntimeError):
    pass


class ToolBroker:
    """Autonomous lane 的唯讀工具後端。

    不建立 Discord View、不播放音訊、不排程、不繪圖。
    所有輸出都會被包成不可信資料，回填 prompt。
    """

    def __init__(self, bot: Any, *, timeout: float = 20.0) -> None:
        self.bot = bot
        self.timeout = max(3.0, float(timeout))

    async def run_with_policy(self, policy: ToolPolicy, name: str, args: dict[str, Any]) -> str:
        return await self.run(policy, name, args)

    async def run(self, policy: ToolPolicy, name: str, args: dict[str, Any]) -> str:
        canonical = str(name or "").strip().upper()
        try:
            from handlers.care.cyber.model import canonical_tool_name

            canonical = canonical_tool_name(name)
        except Exception:
            pass
        if canonical not in policy.allowed:
            raise ToolDenied(f"tool '{name}' is not allowed by policy")
        if policy.backend != "readonly":
            raise ToolDenied(f"tool backend '{policy.backend}' is not readonly")

        handler = {
            "SEARCH": self._search,
            "STOCK": self._stock,
            "SUMMARY": self._summary,
        }.get(canonical)
        if handler is None:
            raise ToolDenied(f"tool '{name}' is not implemented")
        try:
            return await asyncio.wait_for(handler(args or {}), timeout=self.timeout)
        except asyncio.TimeoutError:
            return f"error: tool '{canonical}' timeout"
        except ToolDenied:
            raise
        except Exception as exc:
            logger.warning("[ToolBroker] %s 失敗: %s", canonical, exc)
            return f"error: tool '{canonical}' failed: {exc}"

    async def _search(self, args: dict[str, Any]) -> str:
        query = str(args.get("query") or args.get("q") or "").strip()
        if not query:
            return "error: missing query"
        from handlers.social.tools.search_tools import fetch_web_and_entity_context

        context, link = await fetch_web_and_entity_context(query)
        text = str(context or "").strip()
        if link:
            text += f"\n\n來源：{link}"
        return text[:4000] or "（沒有找到可用結果）"

    async def _summary(self, args: dict[str, Any]) -> str:
        url = str(args.get("url") or "").strip()
        if not url:
            return "error: missing url"
        from handlers.social.tools.search_tools import fetch_and_extract_url

        title, content, method = await fetch_and_extract_url(url)
        body = f"{title}\n{url}\n{content}\n（萃取方式：{method}）".strip()
        return body[:4000] or "（頁面沒有可讀內容）"

    async def _stock(self, args: dict[str, Any]) -> str:
        query = str(args.get("query") or args.get("ticker") or "").strip()
        if not query:
            return "error: missing ticker"
        from handlers.social.tools.stock_tool import (
            _quick_extract_ticker,
            fetch_and_plot_bounded,
        )

        ticker = _quick_extract_ticker(query) or query.upper()
        resolved, _png, indicators = await fetch_and_plot_bounded(ticker)
        if not indicators:
            return f"error: no stock data for {ticker}"
        payload = {"ticker": resolved, "indicators": indicators}
        return json.dumps(payload, ensure_ascii=False, default=str)[:4000]
