"""
tests/unit/test_kv_cache_prompt_structure.py
============================================
單元測試：驗證 Prompt 前綴結構之靜態性與 KV Cache / Prefix Caching 友善性。

確保：
1. head_persona 不再包含隨時間變動之時間戳記，跨時間呼叫時完全一致。
2. tail_lore 正確承載時間戳記與即時備註。
3. IntegratedStrategy 產出之 sys_prompt 始終以靜態前綴 (Soul + Tools + Reply Rules) 起頭。
4. 跨使用者、跨時間、跨歷史情境下，Prompt 開頭數千字元保持 100% 精確一致，保證 Prefix Cache 命中。
"""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from handlers.social.dispatch_agent import DispatchAgentTool
from handlers.social.persona import build_dynamic_persona, get_current_time_block
from handlers.social.tool_base import BaseTool, ToolResponseMode


class DummyTool(BaseTool):
    def __init__(self, name: str, mode: ToolResponseMode = ToolResponseMode.DIRECT_UI) -> None:
        self._name = name
        self._mode = mode

    @property
    def intent_name(self) -> str:
        return self._name

    @property
    def llm_description(self) -> str:
        return f"Dummy tool {self._name} description for testing"

    @property
    def response_mode(self) -> ToolResponseMode:
        return self._mode

    def can_handle_by_regex(self, text: str) -> bool:
        return False

    async def execute(self, *args, **kwargs):
        return None


class TestKVCachePromptStructure(unittest.IsolatedAsyncioTestCase):
    """測試 Prompt 結構之前綴快取 (Prefix Caching) 相容性。"""

    def test_head_persona_is_time_invariant(self) -> None:
        """驗證 head_persona 不包含動態時間，跨時間呼叫內容保持一致。"""
        # Act
        head1, tail1 = build_dynamic_persona("你好", "CHAT")
        head2, tail2 = build_dynamic_persona("你好", "CHAT")

        # Assert
        self.assertNotIn("【目前時間】", head1)
        self.assertNotIn("⏰", head1)
        self.assertIn("【目前時間】", tail1)
        self.assertIn("⏰", tail1)
        # 兩次呼叫的 head_persona 必須完全相同
        self.assertEqual(head1, head2)

    def test_head_persona_is_lorebook_invariant(self) -> None:
        """驗證 head_persona 永不因觸發 Lorebook 關鍵字（如漢娜、NSFW、開源）而變動，確保靜態前綴完全恆定。"""
        head_clean, tail_clean = build_dynamic_persona("早安雪莉！", "CHAT")
        head_hanna, tail_hanna = build_dynamic_persona("讓漢娜跟你貼貼", "CHAT")
        head_nsfw, tail_nsfw = build_dynamic_persona("摸摸胸部開車調情", "CHAT")

        # head_persona 在不同話題下必須 100% 完全相同，前綴快取絕不失效！
        self.assertEqual(head_clean, head_hanna)
        self.assertEqual(head_clean, head_nsfw)

        # 動態觸發的內容必須全數引流至 tail_lore
        self.assertIn("遠野漢娜", tail_hanna)
        self.assertIn("NSFW", tail_nsfw)
        self.assertNotIn("遠野漢娜", tail_clean)

    def test_get_current_time_block_format(self) -> None:
        """驗證時間標籤輔助函式格式正常。"""
        time_str = get_current_time_block()
        self.assertIn("⏰【目前時間】：", time_str)
        self.assertIn("台灣時間", time_str)

    def test_head_persona_contains_persona_and_common_rules(self) -> None:
        """驗證 head_persona 同時包含角色人設 (Soul) 與獨立通用規則 (Common Rules)。"""
        # Arrange & Act
        head, _tail = build_dynamic_persona("早安雪莉！", "CHAT")

        # Assert: 必須包含雪莉純粹人設內容
        self.assertIn("橘雪莉 (Tachibana Sherry) 預設靈魂設定", head)
        self.assertIn("死囚少女檔案", head)
        # Assert: 必須包含獨立通用規則檔案之內容
        self.assertIn("【防幻覺與互動鐵則 - 實幹反空話】", head)
        self.assertIn("實幹列舉鐵則", head)

    def test_soul_discovery_and_dynamic_switching(self) -> None:
        """驗證系統自動掃描 *_default.md 以及動態切換人設檔案時共用通用規則。"""
        import settings
        # 1. 驗證預設掃描
        available = settings.get_available_soul_names()
        self.assertTrue(
            "manosaba_sherry_default.md" in available or "sherry_default.md" in available,
            "預設人設檔案清單必須包含雪莉人設檔",
        )

        # 2. 建立暫時測試人設檔
        test_soul_file = settings.SOUL_DIR / "temp_hoshino_default.md"
        try:
            test_soul_file.write_text("# 小鳥遊星野 (Hoshino) 人設\n哎呀呀，大叔我好累喔～", encoding="utf-8")
            refreshed_list = settings.get_available_soul_names()
            self.assertIn("temp_hoshino_default.md", refreshed_list)

            # 3. 切換人設為測試人設
            settings.update_setting("CURRENT_SOUL_NAME", "temp_hoshino_default.md")
            head_hoshino, _ = build_dynamic_persona("你好", "CHAT")

            # Assert: 成功讀取新角色人設
            self.assertIn("小鳥遊星野 (Hoshino) 人設", head_hoshino)
            self.assertNotIn("橘雪莉 (Tachibana Sherry)", head_hoshino)
            # Assert: 通用規則仍自動注入並共用！
            self.assertIn("【防幻覺與互動鐵則 - 實幹反空話】", head_hoshino)
        finally:
            # 清理還原
            if test_soul_file.exists():
                test_soul_file.unlink()
            settings.update_setting("CURRENT_SOUL_NAME", "manosaba_sherry_default.md")

    def test_web_ui_settings_schema_active_soul_dropdown(self) -> None:
        """驗證 Web UI settings_schema 能為 CURRENT_SOUL_NAME 提供 select 型別與動態選項。"""
        from web_ui.settings_schema import get_editable_settings
        editable = get_editable_settings()
        self.assertIn("CURRENT_SOUL_NAME", editable)
        soul_setting = editable["CURRENT_SOUL_NAME"]
        self.assertEqual(soul_setting["type"], "select")
        self.assertTrue(
            "manosaba_sherry_default.md" in soul_setting.get("options", [])
            or "sherry_default.md" in soul_setting.get("options", [])
        )

    def test_web_ui_token_settings_whitelisted(self) -> None:
        """驗證 LLM_MAX_OUTPUT_TOKENS 與 LLM_MAX_CONTEXT_TOKENS 不會被敏感詞遮蔽，能正常於 Web UI 顯示。"""
        from web_ui.settings_schema import get_editable_settings
        editable = get_editable_settings()
        self.assertIn("LLM_MAX_OUTPUT_TOKENS", editable)
        self.assertIn("LLM_MAX_CONTEXT_TOKENS", editable)
        self.assertEqual(editable["LLM_MAX_OUTPUT_TOKENS"]["category"], "LLM 參數")

    def test_web_ui_soul_content_preview_and_editing(self) -> None:
        """驗證 Web UI settings_schema 包含 SOUL_CONTENT 且能讀寫當前人設檔案內容。"""
        import settings
        from web_ui.settings_schema import get_editable_settings
        # 1. 驗證 schema 包含 SOUL_CONTENT 且不含過時的 CORE_PERSONA
        editable = get_editable_settings()
        self.assertNotIn("CORE_PERSONA", editable)
        self.assertIn("SOUL_CONTENT", editable)
        self.assertIn("橘雪莉", editable["SOUL_CONTENT"]["value"])

        # 2. 測試 update_setting 寫入 SOUL_CONTENT
        test_file = settings.SOUL_DIR / "temp_edit_test_default.md"
        try:
            test_file.write_text("# 原始人設內容", encoding="utf-8")
            settings.update_setting("CURRENT_SOUL_NAME", "temp_edit_test_default.md")
            # 寫入新內容
            success = settings.update_setting("SOUL_CONTENT", "# 更新後的人設內容✨")
            self.assertTrue(success)
            self.assertEqual(test_file.read_text(encoding="utf-8"), "# 更新後的人設內容✨")
        finally:
            if test_file.exists():
                test_file.unlink()
            settings.update_setting("CURRENT_SOUL_NAME", "manosaba_sherry_default.md")

    @patch("handlers.social.dispatch_agent.LiveThinkingSession")
    async def test_integrated_strategy_static_prefix_caching(
        self, mock_session_cls: MagicMock
    ) -> None:
        """驗證 IntegratedStrategy 的 sys_prompt 以完全靜態的前綴起頭。"""
        mock_session = MagicMock()
        mock_session.is_enabled = True
        mock_session.placeholder_msg = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.finish = AsyncMock()
        mock_session_cls.return_value = mock_session

        bot = MagicMock()
        brain = MagicMock()

        # 情境 A：使用者 Alice，在 10:00 發問，帶特定記憶與歷史
        msg_a = MagicMock()
        msg_a.id = 1001
        msg_a.channel.id = 2001
        msg_a.author.id = 3001
        msg_a.author.display_name = "Alice"

        # 情境 B：使用者 Bob，在 10:05 發問，帶完全不同之記憶與歷史
        msg_b = MagicMock()
        msg_b.id = 1002
        msg_b.channel.id = 2001
        msg_b.author.id = 3002
        msg_b.author.display_name = "Bob"

        tools = [
            DummyTool("SEARCH", ToolResponseMode.DATA_PROVIDER),
            DummyTool("MUSIC", ToolResponseMode.DIRECT_UI),
        ]
        agent = DispatchAgentTool(tools, mode="INTEGRATED")

        captured_prompts: list[str] = []

        async def fake_call_llm(brain_cog, system_prompt, user_prompt, **kwargs):
            captured_prompts.append(system_prompt)
            return "回覆內容"

        with patch.object(agent, "_call_llm", side_effect=fake_call_llm):
            # 執行 A
            await agent._run_integrated(
                brain=brain,
                bot=bot,
                message=msg_a,
                user_query="推薦一首歌",
                vibe_snippet="早晨輕鬆氣氛",
                full_context="歷史對話A...",
                images=None,
                start_time=100.0,
            )

            # 執行 B
            await agent._run_integrated(
                brain=brain,
                bot=bot,
                message=msg_b,
                user_query="搜尋今天新聞",
                vibe_snippet="認真嚴肅話題",
                full_context="完全不同的歷史對話B...",
                images=None,
                start_time=200.0,
            )

        self.assertEqual(len(captured_prompts), 2)
        prompt_a = captured_prompts[0]
        prompt_b = captured_prompts[1]

        # 驗證兩者的 Prompt 開頭均包含核心人設、工具規格與回覆規則
        self.assertIn("【回覆規則】", prompt_a)
        self.assertIn("【工具優先】", prompt_a)

        # 計算兩次不同請求中，從開頭開始完全一致的字元長度 (Common Prefix)
        common_len = 0
        min_len = min(len(prompt_a), len(prompt_b))
        while common_len < min_len and prompt_a[common_len] == prompt_b[common_len]:
            common_len += 1

        # 靜態前綴 (Soul + Tools + Rules) 至少應有 2,000 字元以上完全一致
        self.assertGreater(
            common_len, 2000,
            f"前綴一致字數過低: {common_len} 字元，可能動態內容混入前綴！"
        )

        # 動態內容（聊天歷史、氛圍）必須排在前綴之後
        context_idx_a = prompt_a.find("歷史對話A...")
        self.assertGreaterEqual(context_idx_a, common_len)

    @patch("handlers.social.dispatch_agent.LiveThinkingSession")
    async def test_dynamic_context_order_maximizes_multi_user_caching(
        self, mock_session_cls: MagicMock
    ) -> None:
        """驗證動態區塊順序：同頻道對話歷史排在個人記憶之前，使跨群友發言時能最大化共享 KV Cache。"""
        mock_session = MagicMock()
        mock_session.is_enabled = True
        mock_session.placeholder_msg = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.finish = AsyncMock()
        mock_session_cls.return_value = mock_session

        agent = DispatchAgentTool([DummyTool("DUMMY")], mode="INTEGRATED")
        brain = MagicMock()
        bot = MagicMock()

        # 模擬同頻道中兩個不同使用者發言
        msg_user1 = MagicMock()
        msg_user1.id = 1001
        msg_user1.author.id = 111
        msg_user1.author.display_name = "群友小明"
        msg_user1.channel.id = 99999
        msg_user1.guild = None
        msg_user1.reply = AsyncMock()
        msg_user1.channel.send = AsyncMock()

        msg_user2 = MagicMock()
        msg_user2.id = 1002
        msg_user2.author.id = 222
        msg_user2.author.display_name = "群友小華"
        msg_user2.channel.id = 99999
        msg_user2.guild = None
        msg_user2.reply = AsyncMock()
        msg_user2.channel.send = AsyncMock()

        shared_history = "【頻道歷史對話】小明：你好\n雪莉：哈囉！"
        captured_prompts: list[str] = []

        async def fake_call_llm(brain_cog, system_prompt, user_prompt, **kwargs):
            captured_prompts.append(system_prompt)
            return "回覆內容"

        # Mock MemoryManager 提供不同使用者的個人記憶
        mock_mem_mgr = MagicMock()
        mock_mem_mgr.get_user_memory_snippet.side_effect = lambda uid: (
            "📌 小明喜歡吃蘋果" if uid == 111 else "📌 小華喜歡吃香蕉"
        )
        bot.get_cog.side_effect = lambda name: mock_mem_mgr if name == "MemoryManager" else None

        with patch.object(agent, "_call_llm", side_effect=fake_call_llm):
            await agent._run_integrated(
                brain=brain, bot=bot, message=msg_user1,
                user_query="今天吃什麼", vibe_snippet="熱鬧氣氛",
                full_context=shared_history, images=None, start_time=100.0,
            )
            await agent._run_integrated(
                brain=brain, bot=bot, message=msg_user2,
                user_query="我也想知道", vibe_snippet="熱鬧氣氛",
                full_context=shared_history, images=None, start_time=101.0,
            )

        self.assertEqual(len(captured_prompts), 2)
        p1, p2 = captured_prompts[0], captured_prompts[1]

        # 驗證 full_context 出現在 memory_block 之前
        hist_idx_1 = p1.find(shared_history)
        mem_idx_1 = p1.find("小明喜歡吃蘋果")
        self.assertGreater(hist_idx_1, 0, "Prompt 應包含共享對話歷史")
        self.assertGreater(mem_idx_1, 0, "Prompt 應包含個人記憶")
        self.assertLess(hist_idx_1, mem_idx_1, "對話歷史必須排在個人記憶之前以最大化 KV Cache 共享！")

        # 驗證跨使用者的 Common Prefix 包含了對話歷史
        common_len = 0
        min_len = min(len(p1), len(p2))
        while common_len < min_len and p1[common_len] == p2[common_len]:
            common_len += 1

        self.assertGreater(common_len, hist_idx_1 + len(shared_history), "跨使用者的共同快取前綴應完整涵蓋到對話歷史！")

    def test_hiro_persona_complete_isolation(self) -> None:
        """驗證切換為二階堂希羅時，前綴、意圖狀態、上下文標籤與 LOREBOOK 皆無雪莉/名偵探洩漏。"""
        import settings
        from handlers.social.persona import build_dynamic_persona

        original_soul = getattr(settings, "CURRENT_SOUL_NAME", "sherry_default.md")
        try:
            settings.update_setting("CURRENT_SOUL_NAME", "hiro_default.md")

            # 1. 驗證 head_persona 完全純粹為希羅人設，無雪莉殘留
            head, tail = build_dynamic_persona("你不是希羅嗎？有誰是妳朋友？", "CHAT")
            self.assertIn("二階堂希羅", head)
            self.assertNotIn("橘雪莉 (Tachibana Sherry) 預設靈魂設定", head)
            self.assertNotIn("本偵探", head)
            self.assertNotIn("名偵探模式", head)
            self.assertNotIn("15 歲高中女生", head)
            self.assertNotIn("15歲名偵探", head)
            self.assertNotIn("台灣女高中生", head)

            # 2. 驗證提問希羅時，觸發的是客觀中立檔案，不含雪莉主觀視角
            self.assertIn("檔案 659 二階堂希羅", tail)
            self.assertNotIn("推理解謎遊戲", tail)
            self.assertNotIn("無道德常識的偵探", tail)

            # 3. 驗證通用反空話規則正常觸發且不含名偵探或天然黑
            self.assertIn("【⚠️ 實證與反空話強烈指令】", tail)
            self.assertNotIn("名偵探實證", tail)
            self.assertNotIn("天然黑", tail)

        finally:
            settings.update_setting("CURRENT_SOUL_NAME", original_soul)


if __name__ == "__main__":
    unittest.main()
