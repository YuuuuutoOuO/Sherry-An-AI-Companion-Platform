"""
tests/unit/test_integrated_action_agent.py
===========================================
單階段整合式自主調度引擎 (Single-Pass Integrated Action Agent) 單元測試。
"""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from handlers.social.contracts import ToolCategory, ToolResult
from handlers.social.dispatch_agent import (
    DispatchAgentTool,
    IntegratedAgentTool,
    _parse_actions,
)
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
        return f"Dummy tool {self._name}"

    @property
    def response_mode(self) -> ToolResponseMode:
        return self._mode

    def can_handle_by_regex(self, text: str) -> bool:
        return False

    async def execute(self, *args, **kwargs) -> ToolResult:
        return ToolResult.success(f"{self._name} 執行成功", is_direct_rendered=(self._mode == ToolResponseMode.DIRECT_UI))


class TestIntegratedActionAgent(unittest.IsolatedAsyncioTestCase):
    """測試單階段整合自主調度引擎。"""

    def setUp(self) -> None:
        self.search_tool = DummyTool("SEARCH", ToolResponseMode.DATA_PROVIDER)
        self.music_tool = DummyTool("MUSIC", ToolResponseMode.DIRECT_UI)
        self.stock_tool = DummyTool("STOCK", ToolResponseMode.DATA_PROVIDER)
        self.tools = [self.search_tool, self.music_tool, self.stock_tool]
        self.agent = DispatchAgentTool(self.tools, mode="INTEGRATED")

    def test_parse_actions_single_tag(self) -> None:
        """驗證單一 <action name="..."> 標籤解析。"""
        text = "<thought>需要查美食</thought><action name=\"SEARCH\">台北 晚餐推薦</action>"
        tasks = _parse_actions(text)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].tool, "SEARCH")
        self.assertEqual(tasks[0].input, "台北 晚餐推薦")

    def test_parse_actions_multiple_parallel_tags(self) -> None:
        """驗證多工並行多標籤解析。"""
        text = (
            "<thought>同時查股票並放音樂</thought>\n"
            "<action name=\"STOCK\">2330.TW</action>\n"
            "<action name=\"MUSIC\">周杰倫 晴天</action>"
        )
        tasks = _parse_actions(text)
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0].tool, "STOCK")
        self.assertEqual(tasks[0].input, "2330.TW")
        self.assertEqual(tasks[1].tool, "MUSIC")
        self.assertEqual(tasks[1].input, "周杰倫 晴天")

    def test_parse_actions_variant_formats(self) -> None:
        """驗證多種 Action 格式容錯。"""
        text1 = "<action tool=\"PIXIV\">橘雪莉</action>"
        tasks1 = _parse_actions(text1)
        self.assertEqual(len(tasks1), 1)
        self.assertEqual(tasks1[0].tool, "PIXIV")

        text2 = "<action>WEATHER: 台北市</action>"
        tasks2 = _parse_actions(text2)
        self.assertEqual(len(tasks2), 1)
        self.assertEqual(tasks2[0].tool, "WEATHER")
        self.assertEqual(tasks2[0].input, "台北市")

    def test_parse_actions_no_tags(self) -> None:
        """驗證純日常對話（無標籤）解析為空列表。"""
        text = "<thought>打招呼</thought> 早安呀笨蛋！今天過得如何？"
        tasks = _parse_actions(text)
        self.assertEqual(len(tasks), 0)

    @patch("handlers.social.dispatch_agent.LiveThinkingSession")
    async def test_integrated_run_pure_chat_single_pass(self, mock_session_cls: MagicMock) -> None:
        """驗證純日常閒聊情境：僅調用 1 次 LLM 即直接回覆（單次推論秒回）。"""
        mock_session = MagicMock()
        mock_session.is_enabled = True
        mock_session.placeholder_msg = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.finish = AsyncMock()
        mock_session_cls.return_value = mock_session

        bot = MagicMock()
        brain = MagicMock()

        message = MagicMock()
        message.id = 1001
        message.channel.id = 2001
        message.author.id = 3001
        message.author.display_name = "小明"

        llm_reply = "<thought>小明打招呼，開心地回應他。</thought> 早安啊小明！今天精神很好喔～"

        with patch.object(self.agent, "_call_llm", new_callable=AsyncMock) as mock_call_llm:
            mock_call_llm.return_value = llm_reply

            await self.agent._run_integrated(
                brain=brain,
                bot=bot,
                message=message,
                user_query="早安雪莉！",
                vibe_snippet="",
                full_context="",
                images=None,
                start_time=100.0,
            )

            # 驗證只調用了一次 LLM
            self.assertEqual(mock_call_llm.await_count, 1)
            # 驗證 session.finish 被呼叫且內容已過濾 thought
            mock_session.finish.assert_awaited_once()
            args, kwargs = mock_session.finish.call_args
            self.assertEqual(args[0], "早安啊小明！今天精神很好喔～")
            self.assertEqual(kwargs.get("intent_name"), "INTEGRATED_CHAT")

    @patch("handlers.social.dispatch_agent.LiveThinkingSession")
    async def test_integrated_run_empty_pass1_degradation(self, mock_session_cls: MagicMock) -> None:
        """驗證 Pass-1 規劃空回覆時，跳過工具規劃直接降級合成回覆使用者。"""
        mock_session = MagicMock()
        mock_session.is_enabled = True
        mock_session.placeholder_msg = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.push_step = MagicMock()
        mock_session.finish = AsyncMock()
        mock_session_cls.return_value = mock_session

        bot = MagicMock()
        brain = MagicMock()

        message = MagicMock()
        message.id = 1005
        message.channel.id = 2001
        message.author.id = 3001
        message.author.display_name = "小明"

        pass2_reply = "<thought>直接回答使用者</thought> 沒問題！雪莉直接回答你喔。"
        fallback_reply = "沒問題！雪莉直接回答你喔。"

        with patch.object(self.agent, "_call_llm", new_callable=AsyncMock) as mock_call_llm, \
             patch.object(self.agent, "_parallel_execute_single_task", new_callable=AsyncMock) as mock_exec_task:

            mock_call_llm.side_effect = ["", pass2_reply]

            await self.agent._run_integrated(
                brain=brain,
                bot=bot,
                message=message,
                user_query="雪莉，幫我看一下天氣",
                vibe_snippet="",
                full_context="",
                images=None,
                start_time=100.0,
            )

            # 驗證 Pass-1 空回覆仍完成 2 次 LLM（規劃失敗 + 降級合成）
            self.assertEqual(mock_call_llm.await_count, 2)
            # 驗證未發動任何工具
            mock_exec_task.assert_not_awaited()
            # 驗證 session.finish 得到降級合成的最終回覆
            mock_session.finish.assert_awaited_once()
            args, kwargs = mock_session.finish.call_args
            self.assertEqual(args[0], fallback_reply)
            self.assertEqual(kwargs.get("intent_name"), "INTEGRATED_CHAT")

    @patch("handlers.social.dispatch_agent.LiveThinkingSession")
    async def test_integrated_run_with_action_two_passes(self, mock_session_cls: MagicMock) -> None:
        """驗證隱含需求（如推薦晚餐）：第一輪觸發 SEARCH，第二輪生成性格回覆。"""
        mock_session = MagicMock()
        mock_session.is_enabled = True
        mock_session.placeholder_msg = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.push_step = MagicMock()
        mock_session.finish = AsyncMock()
        mock_session_cls.return_value = mock_session

        bot = MagicMock()
        brain = MagicMock()

        message = MagicMock()
        message.id = 1002
        message.channel.id = 2001
        message.author.id = 3001
        message.author.display_name = "小明"

        pass1_reply = "<thought>推薦晚餐需要搜尋美食</thought><action name=\"SEARCH\">台北 晚餐推薦</action>"
        pass2_reply = "<thought>結合拉麵資料推薦</thought> 哼哼～名偵探推薦你去吃這家豚骨拉麵喔！"

        with patch.object(self.agent, "_call_llm", new_callable=AsyncMock) as mock_call_llm, \
             patch.object(self.agent, "_parallel_execute_single_task", new_callable=AsyncMock) as mock_exec_task:

            mock_call_llm.side_effect = [pass1_reply, pass2_reply]
            mock_exec_task.return_value = ("SEARCH", ToolCategory.DATA_PROVIDER, "1. 一蘭拉麵 2. 居酒屋")

            await self.agent._run_integrated(
                brain=brain,
                bot=bot,
                message=message,
                user_query="雪莉，今晚有什麼推薦的晚餐嗎？",
                vibe_snippet="",
                full_context="",
                images=None,
                start_time=100.0,
            )

            # 驗證執行了 2 次 LLM（Pass 1 決策 + Pass 2 合成）
            self.assertEqual(mock_call_llm.await_count, 2)
            # 驗證工具被執行
            mock_exec_task.assert_awaited_once()
            # 驗證 session.finish 得到最終回覆
            mock_session.finish.assert_awaited_once()
            args, kwargs = mock_session.finish.call_args
            self.assertIn("豚骨拉麵", args[0])
            self.assertEqual(kwargs.get("intent_name"), "INTEGRATED_ACTION")

    @patch("handlers.social.dispatch_agent.LiveThinkingSession")
    async def test_integrated_run_direct_ui_early_termination(self, mock_session_cls: MagicMock) -> None:
        """驗證純播放音樂（DIRECT_UI）且無閒聊提問時觸發早期終止。"""
        mock_session = MagicMock()
        mock_session.is_enabled = True
        mock_session.placeholder_msg = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.push_step = MagicMock()
        mock_session.finish = AsyncMock()
        mock_session_cls.return_value = mock_session

        bot = MagicMock()
        brain = MagicMock()

        message = MagicMock()
        message.id = 1003
        message.channel.id = 2001
        message.author.id = 3001
        message.author.display_name = "小明"

        pass1_reply = "<thought>播放音樂</thought><action name=\"MUSIC\">周杰倫 晴天</action>"

        with patch.object(self.agent, "_call_llm", new_callable=AsyncMock) as mock_call_llm, \
             patch.object(self.agent, "_parallel_execute_single_task", new_callable=AsyncMock) as mock_exec_task:

            mock_call_llm.return_value = pass1_reply
            mock_exec_task.return_value = ("MUSIC", ToolCategory.DIRECT_UI, "正在播放 周杰倫 晴天")

            await self.agent._run_integrated(
                brain=brain,
                bot=bot,
                message=message,
                user_query="幫我放首周杰倫的晴天",
                vibe_snippet="",
                full_context="",
                images=None,
                start_time=100.0,
            )

            # 驗證只調用了 1 次 LLM（早期終止，不跑第 2 次）
            self.assertEqual(mock_call_llm.await_count, 1)
            # 驗證工具被執行
            mock_exec_task.assert_awaited_once()
            # 驗證 session.finish 顯示發動成功
            mock_session.finish.assert_awaited_once()
            args, kwargs = mock_session.finish.call_args
            self.assertIn("已為您發動", args[0])

    @patch("handlers.social.dispatch_agent.LiveThinkingSession")
    async def test_integrated_run_multi_turn_doubt_triggers_search(self, mock_session_cls: MagicMock) -> None:
        """驗證多輪對話質疑情境：上文聊下北澤，群友質疑遺漏時成功觸發 SEARCH 二次檢索。"""
        mock_session = MagicMock()
        mock_session.is_enabled = True
        mock_session.placeholder_msg = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.push_step = MagicMock()
        mock_session.finish = AsyncMock()
        mock_session_cls.return_value = mock_session

        bot = MagicMock()
        brain = MagicMock()

        message = MagicMock()
        message.id = 1004
        message.channel.id = 2001
        message.author.id = 3001
        message.author.display_name = "Yuuto"

        pass1_reply = (
            "<thought>使用者覺得還有其他東西，對照上一輪討論下北澤，應進行指代消解並深入檢索網路梗</thought>\n"
            "<action name=\"SEARCH\">下北澤 梗 迷因 朝聖原因</action>"
        )
        pass2_reply = "<thought>查到了淫夢與野獸先輩相關迷因</thought> 哼哼～名偵探查到了！除了孤獨搖滾，原來你說的是那個惡搞的網路迷因對吧！"

        with patch.object(self.agent, "_call_llm", new_callable=AsyncMock) as mock_call_llm, \
             patch.object(self.agent, "_parallel_execute_single_task", new_callable=AsyncMock) as mock_exec_task:

            mock_call_llm.side_effect = [pass1_reply, pass2_reply]
            mock_exec_task.return_value = ("SEARCH", ToolCategory.DATA_PROVIDER, "下北澤與野獸先輩、仲夏夜之淫夢聖地巡禮相關情報")

            await self.agent._run_integrated(
                brain=brain,
                bot=bot,
                message=message,
                user_query="看起來不是欸 感覺還有其他東西@@",
                vibe_snippet="",
                full_context="群友 [Yuuto]：「為什麼那麼多人喜歡去下北澤朝聖?」\n妳自己：「主要是因為孤獨搖滾跟古著咖啡店啦！」",
                images=None,
                start_time=100.0,
            )

            # 驗證執行了 2 次 LLM（Pass 1 決策 + Pass 2 合成）
            self.assertEqual(mock_call_llm.await_count, 2)
            # 驗證 SEARCH 工具被調用，且參數正確包含了指代消解關鍵字
            mock_exec_task.assert_awaited_once()
            called_task = mock_exec_task.call_args[0][0]
            self.assertEqual(called_task.tool, "SEARCH")
            self.assertEqual(called_task.input, "下北澤 梗 迷因 朝聖原因")
            # 驗證 session.finish 得到最終回覆
            mock_session.finish.assert_awaited_once()
            args, kwargs = mock_session.finish.call_args
            self.assertIn("網路迷因", args[0])
            self.assertEqual(kwargs.get("intent_name"), "INTEGRATED_ACTION")

    @patch("handlers.social.dispatch_agent.LiveThinkingSession")
    async def test_pass1_mixed_preamble_and_action_does_not_leak_preamble(
        self, mock_session_cls: MagicMock
    ) -> None:
        """驗證當 Pass 1 產生前置鋪墊對白 + <action> 混合輸出時，系統正確執行工具且絕不洩漏 Pass 1 廢話。"""
        mock_session = MagicMock()
        mock_session.is_enabled = True
        mock_session.placeholder_msg = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.push_step = MagicMock()
        mock_session.finish = AsyncMock()
        mock_session_cls.return_value = mock_session

        bot = MagicMock()
        brain = MagicMock()
        message = MagicMock()
        message.id = 1005
        message.channel.id = 2001
        message.author.id = 3001
        message.author.display_name = "Yuuto"

        # Pass 1 同時產生了角色對白與 action 標籤
        pass1_mixed = (
            "真是不讓人省心，明明自己就在參與這種高風險博弈... 稍微等我一下，我確認一下。\n"
            "<action name=\"SEARCH\">2026年 美聯儲 升息預期 宏觀經濟展望</action>"
        )
        pass2_clean = (
            "（端正整理衣褶，眼神清冷）根據最新專業數據，市場預期今年可能升息兩碼至三碼。"
        )

        with patch.object(self.agent, "_call_llm", new_callable=AsyncMock) as mock_call_llm, \
             patch.object(self.agent, "_parallel_execute_single_task", new_callable=AsyncMock) as mock_exec_task:

            mock_call_llm.side_effect = [pass1_mixed, pass2_clean]
            mock_exec_task.return_value = ("SEARCH", ToolCategory.DATA_PROVIDER, "聯準會升息預期報告數據")

            await self.agent._run_integrated(
                brain=brain,
                bot=bot,
                message=message,
                user_query="今年有可能升息三次以上嗎",
                vibe_snippet="",
                full_context="",
                images=None,
                start_time=100.0,
            )

            # 驗證成功解析出 Action 並發動工具
            mock_exec_task.assert_awaited_once()
            called_task = mock_exec_task.call_args[0][0]
            self.assertEqual(called_task.tool, "SEARCH")
            self.assertEqual(called_task.input, "2026年 美聯儲 升息預期 宏觀經濟展望")

            # 驗證發送到 Discord 的是 Pass 2 的乾淨對白，Pass 1 的鋪墊對白與標籤已被過濾丟棄
            mock_session.finish.assert_awaited_once()
            final_sent_text = mock_session.finish.call_args[0][0]
            self.assertIn("市場預期今年可能升息兩碼至三碼", final_sent_text)
            self.assertNotIn("<action", final_sent_text)
            self.assertNotIn("真是不讓人省心", final_sent_text)

    @patch("handlers.social.dispatch_agent.LiveThinkingSession")
    async def test_pass2_sys_prompt_preserves_kv_cache_alignment(
        self, mock_session_cls: MagicMock
    ) -> None:
        """驗證 Pass 2 (對白合成階段) 的 System Prompt 與 Pass 1 保持 100% 嚴格一致以最大化 KV Cache 複用。"""
        mock_session = MagicMock()
        mock_session.is_enabled = False
        mock_session.placeholder_msg = None
        mock_session_cls.return_value = mock_session

        bot = MagicMock()
        brain = MagicMock()

        captured_sys_prompts: list[str] = []

        async def fake_call_llm(brain_cog, system_prompt, user_prompt, **kwargs):
            captured_sys_prompts.append(system_prompt)
            return "Pass 2 回覆"

        with patch.object(self.agent, "_call_llm", side_effect=fake_call_llm):
            speech, thought, sys_prompt, user_prompt = await self.agent._generate_final_response(
                brain=brain,
                user_query="測試查詢",
                channel_id=123,
                bot=bot,
                user_id=456,
                display_name="Tester",
                observations=[("SEARCH", "搜尋到的市場數據")],
            )

        # 驗證 Pass 2 的 System Prompt 中包含完整的靜態前綴與工具調用規範以保持 KV Cache 對齊
        self.assertIn("【外部工具自主調用特權】", sys_prompt)
        self.assertIn("【回覆規則】", sys_prompt)
        self.assertIn("【工具優先】", sys_prompt)
        # 驗證 Pass 2 的 User Prompt 包含第二階段合成與防再次調用指令
        self.assertIn("【第二階段合成指令】", user_prompt)
        self.assertIn("嚴禁再次輸出任何 <action> 標籤", user_prompt)

    async def test_pass2_sanitization_strips_leaked_action_tags(self) -> None:
        """驗證若 Pass 2 LLM 意外再次產生 <action> 標籤，後端清洗正則仍能 100% 剝離。"""
        brain = MagicMock()
        bot = MagicMock()

        # 模擬 Pass 2 意外吐出 action 標籤
        leaked_output = (
            "<thought>深入分析結果</thought>\n"
            "以下是為您查詢的結果：聯準會目前傾向升息兩碼。\n"
            "<action name=\"SEARCH\">多餘標籤</action>"
        )

        with patch.object(self.agent, "_call_llm", new_callable=AsyncMock, return_value=leaked_output):
            speech, thought, sys_p, user_p = await self.agent._generate_final_response(
                brain=brain,
                user_query="測試查詢",
                channel_id=123,
                bot=bot,
                user_id=456,
                display_name="Tester",
                observations=[("SEARCH", "數據")],
            )

        self.assertNotIn("<action", speech)
        self.assertNotIn("</action>", speech)
        self.assertNotIn("<thought>", speech)
        self.assertIn("聯準會目前傾向升息兩碼", speech)

    def test_alias_class_creation(self) -> None:
        """驗證 IntegratedAgentTool 別名類別運作正常。"""
        tool = IntegratedAgentTool(self.tools)
        self.assertEqual(tool._mode, "INTEGRATED")
        self.assertEqual(tool.intent_name, "INTEGRATED_AGENT")


if __name__ == "__main__":
    unittest.main()
