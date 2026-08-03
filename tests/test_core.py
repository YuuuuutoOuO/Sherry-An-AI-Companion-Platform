"""
tests/test_core.py
==================
自動化測試腳本：驗證雪莉的核心邏輯與 Regex 路由是否正常運作。
"""

import sys
from pathlib import Path
import unittest
import asyncio
import aiohttp

# 將專案根目錄加入 PYTHONPATH
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 引入核心模組 (避免載入 Discord Client，只測試純邏輯)
try:
    from handlers.social.registry import TOOL_LIST
    from handlers.social.compound_parser import parse_compound_command
    from handlers.social.tools.chat_tools import ChatTool
    from handlers.memory.cog import MemoryManager
    import settings
except ImportError as e:
    print(f"Error: 無法載入模組，請確定已進入虛擬環境並安裝 requirements.txt：{e}")
    sys.exit(1)


class TestRoutingRegex(unittest.TestCase):
    def test_music_regex(self):
        """測試音樂播放的正則是否正確命中"""
        music_tool = next((t for t in TOOL_LIST if t.intent_name == "MUSIC"), None)
        self.assertIsNotNone(music_tool)
        
        # 應該命中的
        self.assertTrue(music_tool.can_handle_by_regex("播放 YOASOBI 的歌"))
        self.assertTrue(music_tool.can_handle_by_regex("跳過這首歌"))
        self.assertTrue(music_tool.can_handle_by_regex("清空播放清單"))
        
        # 不該命中的
        self.assertFalse(music_tool.can_handle_by_regex("這首歌好好聽"))

    def test_draw_regex(self):
        """測試畫圖的正則"""
        draw_tool = next((t for t in TOOL_LIST if t.intent_name == "DRAW"), None)
        self.assertIsNotNone(draw_tool)
        
        self.assertTrue(draw_tool.can_handle_by_regex("幫我畫一隻貓"))
        self.assertTrue(draw_tool.can_handle_by_regex("生成 一張圖片"))
        
        self.assertFalse(draw_tool.can_handle_by_regex("畫面上有一隻貓"))

    def test_url_summary_regex(self):
        """測試網址分析總結正則"""
        summary_tool = next((t for t in TOOL_LIST if t.intent_name == "SUMMARY"), None)
        self.assertIsNotNone(summary_tool)
        self.assertTrue(summary_tool.can_handle_by_regex("https://example.com/article"))
        self.assertFalse(summary_tool.can_handle_by_regex("你看過這篇文章嗎"))

    def test_chat_search_trigger(self):
        """測試 ChatTool 的聯網觸發正則"""
        from handlers.social.tools.chat_tools import _should_trigger_search
        self.assertTrue(_should_trigger_search("雪莉，你覺得0050怎麼樣?"))
        self.assertTrue(_should_trigger_search("雪莉目前美伊戰爭怎麼樣了?"))
        self.assertTrue(_should_trigger_search("2330股價現況"))
        self.assertFalse(_should_trigger_search("雪莉今天心情好嗎"))


class TestServices(unittest.IsolatedAsyncioTestCase):
    async def test_ollama_connection(self):
        """測試能否連線到 Ollama"""
        url = f"{settings.OLLAMA_BASE_URL}/api/version"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=2) as resp:
                    self.assertEqual(resp.status, 200)
                    data = await resp.json()
                    self.assertIn("version", data)
        except Exception:
            self.skipTest(f"Ollama 未啟動或無法連線 ({settings.OLLAMA_BASE_URL})")

    async def test_sd_webui_connection(self):
        """測試能否連線到 SD WebUI"""
        url = f"{settings.SD_WEBUI_URL}/sdapi/v1/options"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=2) as resp:
                    self.assertEqual(resp.status, 200)
        except Exception:
            self.skipTest(f"SD WebUI 未啟動或無法連線 ({settings.SD_WEBUI_URL})")


if __name__ == "__main__":
    print("=" * 60)
    print("🧪 開始自動測試雪莉的核心功能與連線...")
    print("=" * 60)
    unittest.main(verbosity=2)
