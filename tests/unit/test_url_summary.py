"""
tests/unit/test_url_summary.py
==============================
URLSummaryTool、網址清洗與多層次備援萃取單元測試。
驗證：
  1. extract_clean_url 能正確處理 Discord Markdown、角括號與結尾標點。
  2. _extract_slug_keywords 能從 URL 路徑正確提取中文與有意義主題。
  3. fetch_and_extract_url 在不同層次（Trafilatura、DOM/Meta、搜尋回退）正常運作。
  4. URLSummaryTool.execute 流程與 BotBrain 異常快速失敗保證。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import discord
from discord.ext import commands

from handlers.bot_brain.models import InferenceTask
from handlers.social.tools.search_tools import (
    URLSummaryTool,
    extract_clean_url,
    _extract_slug_keywords,
    fetch_and_extract_url,
)
from mod.deep_search.domain.models import SearchResultItem


class TestExtractCleanUrl:
    """驗證 URL 清洗與萃取功能。"""

    def test_extract_markdown_url(self) -> None:
        text = "@雪莉 [https://www.msn.com/zh-tw/weather/ar-AA2aJgDB](https://www.msn.com/zh-tw/weather/ar-AA2aJgDB) 請解析"
        url = extract_clean_url(text)
        assert url == "https://www.msn.com/zh-tw/weather/ar-AA2aJgDB"

    def test_extract_angle_bracket_url(self) -> None:
        text = "查看這篇：<https://example.com/news/123>"
        url = extract_clean_url(text)
        assert url == "https://example.com/news/123"

    def test_extract_raw_url_with_trailing_punct(self) -> None:
        text = "請看 https://example.com/article?id=99."
        url = extract_clean_url(text)
        assert url == "https://example.com/article?id=99"

    def test_no_url_returns_empty(self) -> None:
        text = "今天天氣真好，沒有附帶任何網址。"
        url = extract_clean_url(text)
        assert url == ""


class TestExtractSlugKeywords:
    """驗證從 URL 路徑提取關鍵字。"""

    def test_extract_msn_typhoon_slug(self) -> None:
        url = "https://www.msn.com/zh-tw/weather/氣象學/又要放颱風假-沙德爾-這兩日襲台-今年首登陸台灣機率全曝光/ar-AA2aJgDB?ocid=winp2"
        kw = _extract_slug_keywords(url)
        assert "颱風" in kw or "沙德爾" in kw or "氣象學" in kw
        assert "ar-AA2aJgDB" not in kw


class TestFetchAndExtractUrl:
    """驗證多層次網頁萃取管線。"""

    def test_trafilatura_success(self) -> None:
        async def _run() -> None:
            url = "https://example.com/article"
            mock_html = "<html><body><p>內文段落測試超過五十個字元以符合長度要求一二三四五六七八九十</p></body></html>"

            with patch("handlers.social.tools.search_tools.trafilatura") as mock_traf:
                mock_traf.fetch_url.return_value = mock_html
                mock_traf.extract.return_value = "這是一篇完整的測試新聞內文，內容詳盡豐富超過五十個字元以符合長度要求。今天的天氣非常晴朗，適合外出踏青旅遊並探索大自然的美好風光。"
                mock_meta = MagicMock()
                mock_meta.title = "測試標題"
                mock_traf.extract_metadata.return_value = mock_meta

                title, content, source_type = await fetch_and_extract_url(url, timeout_sec=5)
                assert title == "測試標題"
                assert "測試新聞內文" in content
                assert source_type == "trafilatura"

        asyncio.run(_run())

    def test_spa_search_fallback(self) -> None:
        async def _run() -> None:
            url = "https://www.msn.com/zh-tw/weather/氣象學/又要放颱風假-沙德爾-這兩日襲台/ar-AA2aJgDB"

            with patch("handlers.social.tools.search_tools.trafilatura") as mock_traf, \
                 patch("handlers.social.tools.search_tools.DuckDuckGoSearchClient") as mock_ddg_cls:
                mock_traf.fetch_url.return_value = "<html><head><title>MSN</title></head><body><div id=\"root\"></div></body></html>"
                mock_traf.extract.return_value = None
                mock_traf.extract_metadata.return_value = None

                mock_ddg_instance = MagicMock()
                mock_ddg_instance.search = AsyncMock(return_value=[
                    SearchResultItem(title="沙德爾颱風最新動態", url="https://news.com/1", snippet="颱風即將逼近台灣東部海面。")
                ])
                mock_ddg_cls.return_value = mock_ddg_instance

                title, content, source_type = await fetch_and_extract_url(url, timeout_sec=5)
                assert source_type == "search_fallback"
                assert "沙德爾颱風" in content or "颱風" in content

        asyncio.run(_run())


class TestURLSummaryTool:
    """驗證 URLSummaryTool 執行與生命週期。"""

    def test_url_summary_tool_enqueues_task(self) -> None:
        async def _run() -> None:
            tool = URLSummaryTool()
            bot = MagicMock(spec=commands.Bot)
            mock_brain = MagicMock()

            async def fake_enqueue(task: InferenceTask) -> None:
                await task.callback("這是名偵探雪莉的總結回覆！")

            mock_brain.enqueue_task = AsyncMock(side_effect=fake_enqueue)
            bot.get_cog.return_value = mock_brain

            message = MagicMock(spec=discord.Message)
            message.id = 12345
            message.channel.id = 8888

            user_query = "[https://example.com/news](https://example.com/news) 請總結"

            with patch("handlers.social.tools.search_tools.fetch_and_extract_url", AsyncMock(return_value=("新聞標題", "詳細的新聞內文內容", "trafilatura"))), \
                 patch("handlers.social.tools.search_tools.simulate_typing_and_send", AsyncMock()):
                await tool.execute(bot, message, user_query, "", "")
                mock_brain.enqueue_task.assert_called_once()
                task: InferenceTask = mock_brain.enqueue_task.call_args[0][0]
                assert task.task_id == "url-summary-12345"
                assert "新聞標題" in task.system_prompt

        asyncio.run(_run())

    def test_url_summary_tool_redirects_youtube_to_music(self) -> None:
        async def _run() -> None:
            tool = URLSummaryTool()
            bot = MagicMock(spec=commands.Bot)
            message = MagicMock(spec=discord.Message)
            user_query = "https://www.youtube.com/watch?v=_QoWpFAQuM0"

            with patch("handlers.social.tools.music_tool.MusicTool.execute", AsyncMock(return_value=None)) as mock_music_exec:
                await tool.execute(bot, message, user_query, "", "")
                mock_music_exec.assert_called_once_with(bot, message, user_query, "", "")

        asyncio.run(_run())

    def test_can_handle_by_regex_rejects_youtube(self) -> None:
        tool = URLSummaryTool()
        assert tool.can_handle_by_regex("https://www.youtube.com/watch?v=_QoWpFAQuM0") is False
        assert tool.can_handle_by_regex("https://youtu.be/_QoWpFAQuM0") is False
        assert tool.can_handle_by_regex("https://example.com/news") is True

