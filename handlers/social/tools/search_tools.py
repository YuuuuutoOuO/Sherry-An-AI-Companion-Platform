"""
handlers/social/tools/search_tools.py
=====================================
網頁與實體檢索工具庫。
包含 URL 分析總結工具 (URLSummaryTool) 與 CHAT 檢索輔助函式 (fetch_web_and_entity_context)。
支援 Discord Markdown 連結解析、SPA 網頁多層次備援萃取與 Slug 搜尋聯網回退。
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import time
import urllib.parse
from typing import Any

import aiohttp
from bs4 import BeautifulSoup
import discord
from discord.ext import commands

import settings
from handlers.bot_brain import InferenceTask
from handlers.social.contracts import ToolResult
from handlers.social.persona import build_dynamic_persona
from handlers.social.tool_base import BaseTool
from handlers.social.utils import simulate_typing_and_send
from mod.deep_search.repositories.search_client import DuckDuckGoSearchClient

try:
    import trafilatura
except ImportError:
    trafilatura = None

logger = logging.getLogger(__name__)

DEFAULT_HTTP_TIMEOUT: int = getattr(settings, "DEFAULT_HTTP_TIMEOUT", 10)
_search_session: aiohttp.ClientSession | None = None


async def _get_search_session() -> aiohttp.ClientSession:
    """取得或延遲初始化共用之 aiohttp.ClientSession。"""
    global _search_session
    if _search_session is None or _search_session.closed:
        timeout = aiohttp.ClientTimeout(total=DEFAULT_HTTP_TIMEOUT)
        headers = {
            "User-Agent": getattr(
                settings,
                "DEFAULT_USER_AGENT",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            ),
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        _search_session = aiohttp.ClientSession(timeout=timeout, headers=headers)
    return _search_session


def extract_clean_url(text: str) -> str:
    """從使用者訊息或字串中萃取乾淨且單一的有效 URL。

    支援處理：
    1. Discord Markdown 格式：[label](https://...) 或 [https://...](https://...)
    2. 角括號格式：<https://...>
    3. 一般文字夾帶 URL：過濾結尾標點符號與多餘括號。

    Args:
        text: 使用者輸入之原始文字。

    Returns:
        str: 萃取出的乾淨 URL 字串，若未找到則回傳空字串。
    """
    raw = text.strip()
    if not raw:
        return ""

    # 1. 優先匹配 Markdown 語法 [label](url)
    md_match = re.search(r"\[.*?\]\((https?://[^\s\)]+)\)", raw)
    if md_match:
        return md_match.group(1).strip()

    # 2. 匹配角括號 <url>
    angle_match = re.search(r"<(https?://[^>]+)>", raw)
    if angle_match:
        return angle_match.group(1).strip()

    # 3. 匹配標準 https?:// 並去除結尾標點
    raw_match = re.search(r"https?://[^\s<>\[\]\(\)\"\']+", raw)
    if raw_match:
        url = raw_match.group(0)
        # 過濾結尾可能殘留的標點符號或括號
        url = re.sub(r"[\.,;:!\?\)\]\>\"\']+$", "", url)
        return url

    return ""


def _extract_slug_keywords(url: str) -> str:
    """從 URL 路徑 Slug 提取有意義的主題關鍵字，用於 SPA 頁面備援搜尋。

    Args:
        url: 目標網頁 URL。

    Returns:
        str: 提取出之主題關鍵字字串。
    """
    try:
        unquoted = urllib.parse.unquote(url)
        parsed = urllib.parse.urlparse(unquoted)
        # 過濾通用路由字眼與編號
        ignored_tokens = {
            "zh-tw", "zh-cn", "zh-hk", "en-us", "en-gb", "weather", "news",
            "sports", "finance", "article", "index", "html", "htm", "php",
            "tw", "us", "jp", "cn", "hk",
        }
        parts: list[str] = []
        for segment in parsed.path.split("/"):
            segment = segment.strip()
            if not segment:
                continue
            # 略過單純純數字或短英文代碼（如 ar-AA2aJgDB）
            if re.match(r"^[a-zA-Z0-9_\-]{1,12}$", segment) and not any(ch in segment for ch in "一-龥"):
                continue
            cleaned = segment.replace("-", " ").replace("_", " ").replace(".", " ")
            subparts = [p.strip() for p in cleaned.split() if p.lower() not in ignored_tokens and len(p.strip()) > 1]
            parts.extend(subparts)

        return " ".join(parts).strip()
    except Exception as exc:
        logger.debug("[search_tools] Slug 關鍵字提取跳過: %s", exc)
        return ""


def is_safe_public_url(url: str) -> bool:
    """檢查目標網址是否為安全的公網 HTTP/HTTPS 位址，防禦 SSRF 攻擊。

    阻擋私有 IP、迴路位址、Link-local、雲端 Metadata 服務 (如 169.254.169.254)、
    Carrier-Grade NAT (RFC 6598) 與非 HTTP(S) 協定。

    Args:
        url: 待檢查的目標網址字串。

    Returns:
        bool: 若為安全公網位址則回傳 True，若存在安全風險或無法解析則回傳 False。
    """
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme.lower() not in ("http", "https"):
            return False
        hostname = parsed.hostname
        if not hostname:
            return False

        clean_host = hostname.strip("[]")

        # 阻擋本機常用別名
        if clean_host.lower() in ("localhost", "local", "broadcasthost"):
            return False

        # 嘗試直接解析為 IP 物件
        try:
            ip = ipaddress.ip_address(clean_host)
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
                or ip.is_unspecified
                or not ip.is_global
            ):
                return False
            return True
        except ValueError:
            # clean_host 為網域名稱，繼續進行 DNS 解析
            pass

        # 解析 DNS 記錄並檢查所有解析後的 IP
        addr_info = socket.getaddrinfo(clean_host, None)
        if not addr_info:
            return False

        for res in addr_info:
            sockaddr = res[4]
            ip_str = sockaddr[0]
            ip = ipaddress.ip_address(ip_str)
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
                or ip.is_unspecified
                or not ip.is_global
            ):
                return False

        return True
    except Exception as exc:
        logger.debug("[search_tools] URL 安全檢查判定不通過: %s (%s)", url, exc)
        return False


async def _check_redirect_safety(
    url: str,
    timeout_sec: int = DEFAULT_HTTP_TIMEOUT,
    max_redirects: int = 5,
) -> str | None:
    """檢查目標網址及其所有跳轉鏈 (Redirects) 是否皆為安全公網位址。

    Args:
        url: 待檢查的目標網址。
        timeout_sec: 連線超時秒數。
        max_redirects: 最大允許跳轉次數。

    Returns:
        str | None: 若所有跳轉目的地皆安全，回傳最終目標 URL；若偵測到非安全跳轉或異常則回傳 None。
    """
    current = url
    try:
        session = await _get_search_session()
        for _ in range(max_redirects):
            if not is_safe_public_url(current):
                return None
            async with session.get(
                current,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=timeout_sec),
            ) as resp:
                if resp.status in (301, 302, 303, 307, 308):
                    location = resp.headers.get("Location")
                    if not location:
                        return current
                    next_url = urllib.parse.urljoin(current, location.strip())
                    if not is_safe_public_url(next_url):
                        return None
                    current = next_url
                else:
                    return current
        return current
    except Exception as exc:
        logger.debug("[search_tools] 跳轉安全性預檢例外: %s (%s)", current, exc)
        return current if is_safe_public_url(current) else None


async def fetch_and_extract_url(url: str, timeout_sec: int = DEFAULT_HTTP_TIMEOUT) -> tuple[str, str, str]:
    """多層次高品質網頁內文與情報萃取管線。

    管線架構：
    1. 第一層：trafilatura 深度清洗提取（針對傳統靜態文章）。
    2. 第二層（Meta/DOM 備援）：BeautifulSoup 解析 OpenGraph、Meta Description、JSON-LD 與 <p> 標籤。
    3. 第三層（SPA Slug 搜尋聯網回退）：若內文為空（如 MSN 等 SPA 架構），自動自 URL 路徑與標題提取關鍵字，並調用 DuckDuckGo 檢索即時新聞補足情資。

    Args:
        url: 目標網頁 URL。
        timeout_sec: 請求逾時秒數。

    Returns:
        tuple[str, str, str]: (網頁標題, 萃取內文或情資摘要, 萃取方式標記)。
    """
    clean_target = extract_clean_url(url) or url
    if not is_safe_public_url(clean_target):
        logger.warning("[search_tools] SSRF 防護攔截不安全或內部網址: %s", clean_target)
        return clean_target, "無法存取該網址：基於安全性考量，系統禁止存取內部網路、本機或保留網段位址。", "blocked"

    # 預檢 HTTP 跳轉鏈，全面防禦跳轉繞過 (Redirect-based SSRF)
    safe_target = await _check_redirect_safety(clean_target, timeout_sec=timeout_sec)
    if not safe_target or not is_safe_public_url(safe_target):
        logger.warning("[search_tools] SSRF 防護攔截跳轉至內部或不安全網址: %s -> %s", clean_target, safe_target)
        return clean_target, "無法存取該網址：基於安全性考量，系統禁止存取內部網路、本機或保留網段位址。", "blocked"

    title = ""
    content = ""
    meta_desc = ""

    # ── [第一層] Trafilatura 靜態內文萃取 ──────────────────────────────────
    def _fetch_trafilatura(tgt: str) -> tuple[str | None, str, str]:
        if trafilatura is None:
            return None, "", ""
        try:
            downloaded = trafilatura.fetch_url(tgt)
            if downloaded:
                extracted = trafilatura.extract(
                    downloaded,
                    include_links=False,
                    include_comments=False,
                    include_tables=True,
                    output_format="txt",
                )
                t = ""
                try:
                    meta = trafilatura.extract_metadata(downloaded)
                    if meta and meta.title:
                        t = meta.title.strip()
                except Exception:
                    pass
                return downloaded, extracted or "", t
            return None, "", ""
        except Exception as e:
            logger.debug("[search_tools] Trafilatura 執行例外: %s", e)
            return None, "", ""

    loop = asyncio.get_running_loop()
    raw_html: str | None = None
    try:
        raw_html, traf_content, traf_title = await asyncio.wait_for(
            loop.run_in_executor(None, _fetch_trafilatura, safe_target),
            timeout=float(timeout_sec),
        )
        if traf_title:
            title = traf_title
        if traf_content and len(traf_content.strip()) >= 50:
            content = traf_content.strip()
            return title or safe_target, content, "trafilatura"
    except asyncio.TimeoutError:
        logger.warning("[search_tools] Trafilatura 擷取超時 (%ds): %s", timeout_sec, safe_target)
    except Exception as exc:
        logger.debug("[search_tools] Trafilatura 略過: %s", exc)

    # ── [第二層] BeautifulSoup 解析 DOM, Meta, OpenGraph, JSON-LD ─────────
    if not raw_html:
        try:
            session = await _get_search_session()
            async with session.get(
                safe_target,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=timeout_sec),
            ) as resp:
                if resp.status == 200:
                    raw_html = await resp.text(errors="replace")
        except Exception as exc:
            logger.debug("[search_tools] aiohttp 下載網頁失敗: %s", exc)

    if raw_html:
        try:
            soup = BeautifulSoup(raw_html, "html.parser")
            if not title:
                og_title = soup.find("meta", property="og:title")
                if og_title and og_title.get("content"):
                    title = str(og_title["content"]).strip()
                elif soup.title and soup.title.string:
                    title = soup.title.string.strip()

            # 抓取 Meta 描述
            og_desc = soup.find("meta", property="og:description")
            if og_desc and og_desc.get("content"):
                meta_desc = str(og_desc["content"]).strip()
            if not meta_desc:
                meta_d = soup.find("meta", attrs={"name": "description"})
                if meta_d and meta_d.get("content"):
                    meta_desc = str(meta_d["content"]).strip()

            # 抓取 JSON-LD
            for script in soup.find_all("script", type="application/ld+json"):
                if script.string:
                    try:
                        import json
                        data = json.loads(script.string)
                        if isinstance(data, dict):
                            if not title and data.get("headline"):
                                title = str(data["headline"]).strip()
                            if data.get("articleBody"):
                                content = str(data["articleBody"]).strip()
                                break
                            elif data.get("description") and not meta_desc:
                                meta_desc = str(data["description"]).strip()
                    except Exception:
                        pass

            # 抓取內文段落 <article> / <p>
            if not content:
                for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                    tag.decompose()
                paragraphs = [p.get_text().strip() for p in soup.find_all(["p", "article"]) if len(p.get_text().strip()) > 20]
                if paragraphs:
                    extracted_p = "\n\n".join(paragraphs)
                    if len(extracted_p) >= 50:
                        content = extracted_p
        except Exception as exc:
            logger.debug("[search_tools] BeautifulSoup DOM 解析例外: %s", exc)

    if content and len(content.strip()) >= 50:
        return title or clean_target, content.strip(), "dom_fallback"

    # ── [第三層] SPA 頁面與 Slug 搜尋聯網回退 (Fallback Search) ───────────
    slug_kw = _extract_slug_keywords(clean_target)
    search_query = title if (title and title not in ("MSN", "Home", "Index")) else slug_kw

    search_snippets: list[str] = []
    if search_query:
        logger.info("[search_tools] 觸發 SPA 網頁關鍵字聯網回退檢索 => '%s'", search_query)
        try:
            ddg_client = DuckDuckGoSearchClient(timeout_seconds=timeout_sec)
            items = await ddg_client.search(search_query, max_results=4)
            for itm in items:
                if itm.snippet:
                    search_snippets.append(f"• 【{itm.title}】：{itm.snippet} (來源: {itm.url})")
        except Exception as search_err:
            logger.warning("[search_tools] 聯網回退檢索失敗: %s", search_err)

    assembled_parts: list[str] = []
    if title:
        assembled_parts.append(f"《網頁主題》：{title}")
    if meta_desc:
        assembled_parts.append(f"《頁面摘要》：{meta_desc}")
    if search_snippets:
        assembled_parts.append("《相關新聞與時事情報檢索》：\n" + "\n".join(search_snippets))

    if assembled_parts:
        final_info = "\n\n".join(assembled_parts)
        return title or search_query or clean_target, final_info, "search_fallback"

    fallback_text = f"此網頁 ({clean_target}) 目前未提供公開靜態文字，或需要特定客戶端/登入環境渲染。"
    return title or clean_target, fallback_text, "empty"


class URLSummaryTool(BaseTool):
    """傳送網址 (HTTP/HTTPS) 時，自動抓取網頁全文並進行總結分析。"""

    @property
    def intent_name(self) -> str:
        return "SUMMARY"

    @property
    def llm_description(self) -> str:
        return "只有當使用者的文字中包含『非 YouTube 的一般文章/新聞/網頁網址』並要求總結時才觸發。若為 YouTube 連結（youtube.com / youtu.be）絕對不可選，請選擇 MUSIC 工具！"

    def can_handle_by_regex(self, text: str) -> bool:
        if "youtu" in text.lower():
            return False
        clean_url = extract_clean_url(text)
        return bool(clean_url)

    async def execute(
        self,
        bot: commands.Bot,
        message: discord.Message,
        user_query: str,
        vibe_snippet: str,
        full_context: str,
        **kwargs: object,
    ) -> ToolResult:
        silent = bool(kwargs.get("silent", False))
        brain_cog = bot.get_cog("BotBrain")
        if not brain_cog:
            return ToolResult.error("BotBrain 未就緒。", is_direct_rendered=not silent)
        brain: Any = brain_cog

        url = extract_clean_url(user_query)
        if not url:
            from handlers.social.registry import get_fallback_tool

            chat_tool = get_fallback_tool()
            if silent or kwargs:
                pass_kwargs = dict(kwargs)
                pass_kwargs["silent"] = silent
                res = await chat_tool.execute(
                    bot, message, user_query, vibe_snippet, full_context, **pass_kwargs
                )
            else:
                res = await chat_tool.execute(
                    bot, message, user_query, vibe_snippet, full_context
                )
            return res if isinstance(res, ToolResult) else ToolResult.success(str(res) if res else "", is_direct_rendered=not silent)

        # 若為 YouTube 網址且使用者未明確要求總結文章/影片內容，自動轉導至 MusicTool 點歌
        if re.search(r"(www\.youtube\.com|youtu\.?be)", url):
            if not re.search(r"(解析|總結|大意|文章|內容|新聞|看過|重點|這部|這張|這篇|介紹|心得)", user_query):
                from handlers.social.tools.music_tool import MusicTool

                logger.info("URLSummaryTool: 偵測到純 YouTube 連結，自動轉導至 MusicTool。")
                music_tool = MusicTool()
                if silent or kwargs:
                    pass_kwargs = dict(kwargs)
                    pass_kwargs["silent"] = silent
                    res = await music_tool.execute(
                        bot, message, user_query, vibe_snippet, full_context, **pass_kwargs
                    )
                else:
                    res = await music_tool.execute(
                        bot, message, user_query, vibe_snippet, full_context
                    )
                return res if isinstance(res, ToolResult) else ToolResult.success(str(res) if res else "", is_direct_rendered=True)

        title, site_content, source_type = await fetch_and_extract_url(
            url, timeout_sec=DEFAULT_HTTP_TIMEOUT
        )

        logger.info(
            "URLSummaryTool: URL 萃取完成 -> url=%s type=%s len=%d",
            url,
            source_type,
            len(site_content),
        )

        if source_type == "blocked":
            if silent:
                return ToolResult.error(site_content, is_direct_rendered=False)
            await simulate_typing_and_send(
                message,
                f"❌ {site_content}",
                intent_name="URL_SUMMARY",
            )
            return ToolResult.success(site_content, is_direct_rendered=True)

        if silent:
            summary_content = f"【網頁標題】：{title}\n【詳細情報內容】：\n{site_content[:2500]}"
            return ToolResult.success(summary_content, is_direct_rendered=False, extra_data={"url": url, "title": title})

        head_persona, _tail = build_dynamic_persona(
            user_query, self.intent_name, channel_id=message.channel.id
        )
        dynamic_persona = head_persona
        sys_prompt = (
            f"{dynamic_persona}\n"
            f"【獲取情報內容 - 目標網址】：{url}\n"
            f"【網頁標題】：{title}\n"
            f"【詳細情報內容】：\n{site_content[:5000]}\n\n"
            "請用符合妳當前人設與性格特質的語氣，生動、豐富且一針見血地為大家總結這篇網頁的核心內容與重點！"
        )

        start_time = time.time()
        loop = asyncio.get_running_loop()
        summary_future: asyncio.Future[str] = loop.create_future()

        async def _on_summary_reply(reply: str) -> None:
            try:
                elapsed_ms = int((time.time() - start_time) * 1000)
                await simulate_typing_and_send(
                    message,
                    reply,
                    intent_name="URL_SUMMARY",
                    prompt=url,
                    model=settings.PRIMARY_MODEL,
                    system_prompt=sys_prompt,
                    user_prompt="這篇網頁的大意是什麼呢？",
                    latency_ms=elapsed_ms,
                )
                if not summary_future.done():
                    summary_future.set_result(reply)
            except Exception as e:
                logger.error("URLSummaryTool: 回覆發送失敗: %s", e, exc_info=True)
                if not summary_future.done():
                    summary_future.set_result("")

        await brain.enqueue_task(
            InferenceTask(
                priority=settings.PRIORITY_TAG_RESPONSE,
                system_prompt=sys_prompt,
                user_prompt="這篇網頁的大意是什麼呢？",
                callback=_on_summary_reply,
                model=settings.PRIMARY_MODEL,
                task_id=f"url-summary-{message.id}",
            )
        )

        try:
            reply_text = await asyncio.wait_for(summary_future, timeout=float(settings.OLLAMA_TIMEOUT))
            return ToolResult.success(reply_text, is_direct_rendered=True)
        except asyncio.TimeoutError:
            logger.warning("URLSummaryTool: 等待總結發送逾時 (%ds)", settings.OLLAMA_TIMEOUT)
            return ToolResult.error("等待總結發送逾時", is_direct_rendered=False)


async def fetch_web_and_entity_context(user_query: str) -> tuple[str, str]:
    """通用即時聯網與實體檢索函式。

    供 ChatTool 調用，進行新聞、時事、股票與實體資料檢索。
    回傳 (intelligence_text, top_link)。

    Args:
        user_query: 使用者原始查詢語句。

    Returns:
        tuple[str, str]: (情報內文, 最佳來源連結)。
    """
    clean_kw = re.sub(
        r"(雪莉|請問|幫我|查一下|搜尋|查詢|你覺得|怎麼樣|如何|目前|最新消息|現在|你知道|什麼是)",
        "",
        user_query,
        flags=re.IGNORECASE,
    ).strip()
    if not clean_kw:
        clean_kw = user_query.strip()

    if not clean_kw:
        return "【🔍 即時網路與實體檢索情報】\n【網路檢索提示】：未提供有效檢索主題或關鍵字。", ""

    link = ""
    snippet = ""
    deep_text = ""

    logger.info("🔎 [WebContext] 開始檢索關鍵字 => '%s'", clean_kw)

    # 1. 執行 DuckDuckGo 檢索
    try:
        ddg_client = DuckDuckGoSearchClient(timeout_seconds=DEFAULT_HTTP_TIMEOUT)
        items = await ddg_client.search(clean_kw, max_results=3)
        if items:
            top_item = items[0]
            link = top_item.url
            snippet = f"《摘要節錄》：{top_item.snippet}\n"
    except Exception as e:
        logger.error("[WebContext] 搜尋受阻: %s", e)

    # 2. 若抓到連結，下載頁面內文
    if link:
        try:
            _title, page_data, _type = await fetch_and_extract_url(
                link, timeout_sec=DEFAULT_HTTP_TIMEOUT
            )
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
    """相容性搜尋工具 (現已整合至 ChatTool 聯網對話系統中)。

    當 Intent 強制為 SEARCH 時，會調用檢索並交由 ChatTool 個性化回覆。
    """

    @property
    def intent_name(self) -> str:
        return "SEARCH"

    @property
    def llm_description(self) -> str:
        return "網路情報檢索工具。當使用者詢問專有名詞、作品/歌曲/角色背景、ACG動漫梗、時事新聞、百科知識，或提出「什麼是/誰是/介紹/這是什麼」等資訊性問題時使用。"

    def can_handle_by_regex(self, text: str) -> bool:
        return False

    async def execute(
        self,
        bot: commands.Bot,
        message: discord.Message,
        user_query: str,
        vibe_snippet: str,
        full_context: str,
        **kwargs: object,
    ) -> ToolResult:
        silent = bool(kwargs.get("silent", False))
        if silent:
            context, link = await fetch_web_and_entity_context(user_query)
            return ToolResult.success(context, is_direct_rendered=False, extra_data={"link": link})

        from handlers.social.registry import get_fallback_tool

        chat_tool = get_fallback_tool()
        res = await chat_tool.execute(
            bot, message, user_query, vibe_snippet, full_context, silent=silent, **kwargs
        )
        return res if isinstance(res, ToolResult) else ToolResult.success(str(res) if res else "", is_direct_rendered=True)