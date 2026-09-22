"""
tests/unit/test_vision_prompt_isolation.py
==========================================
驗證視覺神經感知與使用者發言隔離機制（避免雪莉將視覺模型之畫面輸出誤認為使用者發言）。
"""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from handlers.social.dispatch.prompts import (
    build_user_turn_prompt,
    extract_vision_context,
)
from handlers.social.dispatch_agent import DispatchAgentTool
from handlers.social.tool_base import BaseTool, ToolResponseMode


class DummyTool(BaseTool):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def intent_name(self) -> str:
        return self._name

    @property
    def llm_description(self) -> str:
        return f"Dummy tool {self._name}"

    @property
    def response_mode(self) -> ToolResponseMode:
        return ToolResponseMode.DIRECT_UI

    def can_handle_by_regex(self, text: str) -> bool:
        return False

    async def execute(self, *args, **kwargs):
        return None


class TestVisionPromptIsolation(unittest.IsolatedAsyncioTestCase):
    """測試視覺感知與使用者發言之隔離機制。"""

    def test_extract_vision_context_with_clean_inputs(self) -> None:
        """驗證乾淨分離之輸入解析。"""
        user_query = "這張圖好可愛喔！"
        visual = "粉色頭髮，頭戴羊角飾品的女孩子"

        clean, vis = extract_vision_context(user_query, visual)
        self.assertEqual(clean, "這張圖好可愛喔！")
        self.assertEqual(vis, "粉色頭髮，頭戴羊角飾品的女孩子")

    def test_extract_vision_context_empty_user_query(self) -> None:
        """驗證使用者純發圖片未打字情境。"""
        clean, vis = extract_vision_context("", "一隻可愛的橘貓")
        self.assertEqual(clean, "")
        self.assertEqual(vis, "一隻可愛的橘貓")

    def test_extract_vision_context_legacy_format_backward_compat(self) -> None:
        """驗證歷史舊版包含『【群友上傳了一張圖片...】』之向下相容自動提取。"""
        legacy_input = "【群友上傳了一張圖片，系統視覺解析結果：客觀畫面特徵：粉色長髮、羊角配件】 這隻角色是誰？"
        clean, vis = extract_vision_context(legacy_input, "")
        self.assertEqual(clean, "這隻角色是誰？")
        self.assertEqual(vis, "客觀畫面特徵：粉色長髮、羊角配件")

    def test_build_user_turn_prompt_when_user_speaks_with_image(self) -> None:
        """驗證使用者出示圖片並附帶發言時，視覺畫面與發言嚴格分離。"""
        prompt = build_user_turn_prompt(
            display_name="Yuuto",
            user_query="這張圖可愛嗎？",
            visual_context="粉色長髮、羊角飾品",
        )
        # 視覺畫面在專屬區塊
        self.assertIn("【妳眼前的視覺畫面（妳親眼看到的景象）】：", prompt)
        self.assertIn("粉色長髮、羊角飾品", prompt)
        # 使用者真正說的話在引號中
        self.assertIn("使用者（Yuuto）向妳出示了上述圖片，並說：「這張圖可愛嗎？」", prompt)
        # 鐵則指引存在
        self.assertIn("絕對不是 Yuuto 打的字", prompt)
        # 絕對不可把畫面特徵當成使用者說的話
        self.assertNotIn("使用者（Yuuto）說：「粉色長髮", prompt)

    def test_build_user_turn_prompt_when_user_silent_with_image(self) -> None:
        """驗證使用者純出示圖片未打字時，不會將視覺特徵放入使用者發言中。"""
        prompt = build_user_turn_prompt(
            display_name="Yuuto",
            user_query="",
            visual_context="藍色長髮，身穿白大褂",
        )
        self.assertIn("【妳眼前的視覺畫面（妳親眼看到的景象）】：", prompt)
        self.assertIn("藍色長髮，身穿白大褂", prompt)
        self.assertIn("使用者（Yuuto）向妳出示了上述圖片，靜靜等待妳的反應，沒有額外打字。", prompt)
        self.assertIn("絕對不是 Yuuto 打的字", prompt)
        self.assertNotIn("使用者（Yuuto）說：「藍色長髮", prompt)

    def test_build_user_turn_prompt_pure_text_message(self) -> None:
        """驗證純文字訊息維持簡潔格式，不增加多餘視覺指示。"""
        prompt = build_user_turn_prompt(
            display_name="Yuuto",
            user_query="今天天氣如何？",
            visual_context="",
        )
        self.assertEqual(prompt, "使用者（Yuuto）說：「今天天氣如何？」")
        self.assertNotIn("【妳眼前的視覺畫面", prompt)

    @patch("handlers.social.dispatch_agent.LiveThinkingSession")
    async def test_integrated_strategy_injects_vision_awareness_and_self_lore(
        self, mock_session_cls: MagicMock
    ) -> None:
        """驗證在 INTEGRATED 模式下，有視覺感知時正確載入自我辨識與視覺認知規範。"""
        mock_session = MagicMock()
        mock_session.is_enabled = True
        mock_session.placeholder_msg = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.finish = AsyncMock()
        mock_session_cls.return_value = mock_session

        bot = MagicMock()
        brain = MagicMock()
        message = MagicMock()
        message.id = 9999
        message.channel.id = 1111
        message.author.id = 2222
        message.author.display_name = "Yuuto"

        agent = DispatchAgentTool([DummyTool("DUMMY")], mode="INTEGRATED")

        captured_system_prompt = ""
        captured_user_prompt = ""

        async def fake_call_llm(brain_cog, system_prompt, user_prompt, **kwargs):
            nonlocal captured_system_prompt, captured_user_prompt
            captured_system_prompt = system_prompt
            captured_user_prompt = user_prompt
            return "<thought>看到Yuuto分享的可愛插畫</thought> 哇～Yuuto你看這張圖好精緻喔！"

        with patch.object(agent, "_call_llm", side_effect=fake_call_llm):
            await agent._run_integrated(
                brain=brain,
                bot=bot,
                message=message,
                user_query="",  # 使用者未打字
                vibe_snippet="",
                full_context="",
                images=None,  # 模擬獨立視覺模型，images_to_pass 為 None
                start_time=100.0,
                visual_context="淺藍髮色、丸子頭、白鳥羽毛帽的少女偵探插畫",
            )

        # 1. 驗證 System Prompt 包含視覺感官認知原則
        self.assertIn("【視覺感官與圖片認知原則】", captured_system_prompt)
        # 2. 驗證獨立視覺模型下亦成功觸發自我形象感知提示（vision_self_lore）
        self.assertIn("【多模態視覺自我感知提示】：若圖片中出現淺藍髮色、丸子頭", captured_system_prompt)
        # 3. 驗證 User Prompt 正確區分視覺畫面與使用者靜默
        self.assertIn("【妳眼前的視覺畫面（妳親眼看到的景象）】：", captured_user_prompt)
        self.assertIn("淺藍髮色、丸子頭、白鳥羽毛帽的少女偵探插畫", captured_user_prompt)
        self.assertIn("使用者（Yuuto）向妳出示了上述圖片，靜靜等待妳的反應，沒有額外打字。", captured_user_prompt)
        # 4. 驗證絕對沒有將視覺描述偽造成 Yuuto 說的話
        self.assertNotIn("使用者（Yuuto）說：「淺藍髮色", captured_user_prompt)


if __name__ == "__main__":
    unittest.main()
