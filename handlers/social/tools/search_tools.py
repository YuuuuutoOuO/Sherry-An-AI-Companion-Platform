"""
handlers/social/tools/search_tools.py
=====================================
網頁與實體檢索工具庫。
包含 URL 分析總結工具 (URLSummaryTool) 與 CHAT 檢索輔助函式 (fetch_web_and_entity_context)。
"""

from __future__ import annotations

import asyncio
import logging
import re
import urllib.parse
import aiohttp
import discord
from discord.ext import commands
import settings
from handlers.bot_brain import BotBrain, InferenceTask
from handlers.social.persona import build_dynamic_persona
from handlers.social.tool_base import BaseTool
from handlers.social.utils import simulate_typing_and_send

logger = logging.getLogger(__name__)

DEFAULT_HTTP_TIMEOUT: int = settings.DEFAULT_HTTP_TIMEOUT


class URLSummaryTool(BaseTool):
    """傳送網址 (HTTP/HTTPS) 時，自動抓取網頁全文並進行總結分析。"""

    @property
    def intent_name(self) -> str:
        return "SUMMARY"

    @property
    def llm_description(self) -> str:
        return "只有當使用者的文字中『包含 http 開頭的網址』時才觸發，用來總結網頁大意。若無網址絕對不可選！"

    def can_handle_by_regex(self, text: str) -> bool:
        if "youtu" in text.lower():
            return False
        return bool(re.search(r"https?:\/\/[^\s]+", text.strip()))

    async def execute(
        self,
        bot: commands.Bot,
        message: discord.Message,
        user_query: str,
        vibe_snippet: str,
        full_context: str,
    ) -> None:
        brain: BotBrain | None = bot.get_cog("BotBrain")
        if not brain:
            return

        url_match = re.search(r"https?:\/\/[^\s]+", user_query.strip())
        if not url_match:
            from handlers.social.registry import get_fallback_tool

            chat_tool = get_fallback_tool()
            return await chat_tool.execute(
                bot, message, user_query, vibe_snippet, full_context
            )

        url = url_match.group(0)

        def fetch_sync(tgt: str) -> str:
            try:
                import trafilatura
                downloaded = trafilatura.fetch_url(tgt)
                if downloaded:
                    extracted = trafilatura.extract(downloaded)
                    if extracted:
                        return extracted
                return "無法連線抓取！"
            except Exception as e:
                logger.error("URLSummaryTool 抓取失敗: %s", e)
                return "無法連接。"

        try:
            site_content = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(None, fetch_sync, url),
                timeout=DEFAULT_HTTP_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning("URLSummaryTool: fetch_sync 超時")
            site_content = "無法連線抓取！"

        head_persona, _tail = build_dynamic_persona(
            user_query, self.intent_name, channel_id=message.channel.id
        )
        dynamic_persona = head_persona
        sys_prompt = (
            f"{dynamic_persona}\n"
            f"【獲取情報內容】：\n{site_content[:6000]}\n"
            "請用妳的語氣，盡可能詳細、豐富地為大家總結這篇網頁的核心內容！"
        )

        await brain.enqueue_task(
            InferenceTask(
                priority=settings.PRIORITY_TAG_RESPONSE,
                system_prompt=sys_prompt,
                user_prompt="這篇網頁的大意是什麼呢？",
                callback=lambda reply: simulate_typing_and_send(message, reply),
                model=settings.PRIMARY_MODEL,
                task_id="url-summary",
            )
        )


async def fetch_web_and_entity_context(user_query: str) -> tuple[str, str]:
    """
    通用即時聯網與實體檢索函式。
    供 ChatTool 調用，進行新聞、時事、股票與實體資料檢索。
    回傳 (intelligence_text, top_link)。
    """
    clean_kw = re.sub(
        r"(雪莉|請問|幫我|查一下|搜尋|查詢|你覺得|怎麼樣|如何|目前|最新消息|現在|你知道|什麼是)",
        "",
        user_query,
        flags=re.IGNORECASE,
    ).strip()
    if not clean_kw:
        clean_kw = user_query.strip()

    link = ""
    snippet = ""
    deep_text = ""

    logger.info("🔎 [WebContext] 開始檢索關鍵字 => '%s'", clean_kw)

    # 1. 執行 DuckDuckGo 檢索
    try:
        search_url = (
            f"{settings.DUCKDUCKGO_SEARCH_URL}{urllib.parse.quote(clean_kw)}"
        )
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8",
        }
        timeout = aiohttp.ClientTimeout(total=DEFAULT_HTTP_TIMEOUT)
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(search_url, timeout=timeout) as r:
                if r.status == 200:
                    html = await r.text()
                    if "No results" not in html:
                        a_matches = re.findall(
                            r'<a class="result__snippet[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                            html,
                            re.DOTALL | re.IGNORECASE,
                        )
                        if a_matches:
                            raw_href = a_matches[0][0]
                            u_match = re.search(r"uddg=(https?[^&]+)", raw_href)
                            link = (
                                urllib.parse.unquote(u_match.group(1))
                                if u_match
                                else raw_href
                            )
                            snippet_text = re.sub(
                                r"<[^>]+>", "", a_matches[0][1]
                            ).strip()
                            snippet = f"《摘要節錄》：{snippet_text}\n"
    except Exception as e:
        logger.error("[WebContext] 搜尋受阻: %s", e)

    # 2. 若抓到連結，下載頁面內文
    if link:
        try:

            def _extract_page(tgt_link: str) -> str:
                import trafilatura
                downloaded = trafilatura.fetch_url(tgt_link)
                if downloaded:
                    extracted = trafilatura.extract(downloaded)
                    return extracted if extracted else ""
                return ""

            try:
                page_data = await asyncio.wait_for(
                    asyncio.get_running_loop().run_in_executor(None, _extract_page, link),
                    timeout=DEFAULT_HTTP_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.debug("[WebContext] 頁面內文抓取超時")
                page_data = ""
            if page_data:
                deep_text = f"《網頁詳細內文》：\n{page_data[:2000]}\n"
        except Exception as exc:
            logger.debug("[WebContext] 頁面內文抓取跳過: %s", exc)

    # 3. 股票/ETF/加密貨幣 實體識別與數據獲取
    stock_info = ""
    try:
        from handlers.social.tools.stock_tool import (
            _fetch_and_plot,
            _quick_extract_ticker,
        )

        combined_text_for_ticker = f"{clean_kw} {snippet}"
        ticker = _quick_extract_ticker(combined_text_for_ticker)
        if ticker:
            logger.info("📈 [WebContext] 識別出金融資產實體 => '%s'", ticker)
            loop = asyncio.get_running_loop()
            _, indicators = await loop.run_in_executor(
                None, _fetch_and_plot, ticker
            )
            if indicators:
                change_str = (
                    f"+{indicators['change_pct']}%"
                    if indicators["change_pct"] >= 0
                    else f"{indicators['change_pct']}%"
                )
                trend = (
                    "多頭排列 📈"
                    if indicators["ema20"] > indicators["ema60"]
                    else "空頭排列 📉"
                )
                rsi_status = (
                    "超買區 ⚠️"
                    if indicators["rsi14"] > 70
                    else "超賣區 💡"
                    if indicators["rsi14"] < 30
                    else "中性區"
                )
                stock_info = (
                    f"\n【📊 實體即時技術指標數據 ({ticker})】\n"
                    f"最新收盤價：{indicators['close']} ({change_str})\n"
                    f"EMA20：{indicators['ema20']} | EMA60：{indicators['ema60']} → {trend}\n"
                    f"RSI14：{indicators['rsi14']} → {rsi_status}\n"
                    f"MACD：{indicators['macd']} | Signal：{indicators['macd_signal']}\n"
                )
    except Exception as exc:
        logger.warning("[WebContext] 金融實體解析失敗/跳過: %s", exc)

    final_result = (deep_text + snippet + stock_info).strip()
    if not final_result:
        final_result = "【網路檢索提示】：未搜尋到相關新聞或公開資料。"

    formatted_context = (
        f"【🔍 即時網路與實體檢索情報】\n"
        f"檢索主題：{clean_kw}\n"
        f"{final_result}"
    )

    return formatted_context, link


class AdvancedSearchTool(BaseTool):
    """
    相容性搜尋工具 (現已整合至 ChatTool 聯網對話系統中)。
    當 Intent 強制為 SEARCH 時，會調用檢索並交由 ChatTool 個性化回覆。
    """

    @property
    def intent_name(self) -> str:
        return "SEARCH"

    @property
    def llm_description(self) -> str:
        return "搜尋、查詢時事或最新資料時觸發。"

    def can_handle_by_regex(self, text: str) -> bool:
        return False

    async def execute(
        self,
        bot: commands.Bot,
        message: discord.Message,
        user_query: str,
        vibe_snippet: str,
        full_context: str,
    ) -> None:
        from handlers.social.registry import get_fallback_tool

        chat_tool = get_fallback_tool()
        await chat_tool.execute(
            bot, message, user_query, vibe_snippet, full_context
        )