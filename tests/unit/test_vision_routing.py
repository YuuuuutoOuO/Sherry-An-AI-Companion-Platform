from __future__ import annotations
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import settings
from handlers.social.router import route_and_execute_tag

class TestVisionRouting(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.bot = MagicMock()
        self.brain = MagicMock()
        self.bot.get_cog.side_effect = lambda name: self.brain if name == 'BotBrain' else None
        self.message = MagicMock()
        self.message.id = 123456
        self.message.author.id = 789
        self.message.author.display_name = 'TestUser'
        self.message.channel.id = 999
        self.message.clean_content = 'look at this image'
        self.message.reference = None
        self.message.reply = AsyncMock()
        self.message.add_reaction = AsyncMock()
        self.attachment = MagicMock()
        self.attachment.content_type = 'image/png'
        self.attachment.read = AsyncMock(return_value=b'fake_image_bytes')
        self.message.attachments = [self.attachment]

    @patch('handlers.social.dispatch_agent.DispatchAgentTool.execute', new_callable=AsyncMock)
    @patch('handlers.social.router.rate_limiter.is_allowed', return_value=True)
    async def test_vision_model_flow(
        self,
        mock_rate: MagicMock,
        mock_dispatch: AsyncMock,
    ) -> None:
        """測試設定 VISION_MODEL 時，啟用單階段直出：不推送中繼 caption 任務，直接將圖片傳給調度 Agent。"""
        with patch.object(settings, 'VISION_MODEL', 'lms:thesby_qwen2.5-vl-7b-nsfw-caption-v3'):
            await route_and_execute_tag(self.bot, self.message)

        # 單階段直出下，router 不會單獨調用 brain 執行中繼 caption 任務
        self.assertEqual(self.brain.enqueue_task.call_count, 0)
        mock_dispatch.assert_called_once()
        call_kwargs = mock_dispatch.call_args[1]
        self.assertIn('images', call_kwargs)
        self.assertIsNotNone(call_kwargs['images'])
        self.assertEqual(len(call_kwargs['images']), 1)

    @patch('handlers.social.dispatch_agent.DispatchAgentTool.execute', new_callable=AsyncMock)
    @patch('handlers.social.router.rate_limiter.is_allowed', return_value=True)
    async def test_native_multimodal_when_vision_model_empty(
        self,
        mock_rate: MagicMock,
        mock_dispatch: AsyncMock,
    ) -> None:
        """測試 VISION_MODEL 留空時，直接由調度 Agent 處理原生多模態圖片。"""
        with patch.object(settings, 'VISION_MODEL', ''):
            await route_and_execute_tag(self.bot, self.message)

        mock_dispatch.assert_called_once()
        call_kwargs = mock_dispatch.call_args[1]
        self.assertIn('images', call_kwargs)
        self.assertIsNotNone(call_kwargs['images'])
        self.assertEqual(len(call_kwargs['images']), 1)

    @patch('handlers.social.dispatch_agent.LiveThinkingSession')
    async def test_integrated_strategy_routes_to_vision_model_when_images_present(
        self,
        mock_session_cls: MagicMock,
    ) -> None:
        """測試 IntegratedStrategy 在有圖片且設定 VISION_MODEL 時，推論模型自動切換為 VISION_MODEL。"""
        from handlers.social.dispatch_agent import DispatchAgentTool
        from handlers.social.tool_base import BaseTool, ToolResponseMode

        mock_session = MagicMock()
        mock_session.is_enabled = True
        mock_session.placeholder_msg = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.finish = AsyncMock()
        mock_session_cls.return_value = mock_session

        class DummyTool(BaseTool):
            @property
            def intent_name(self) -> str:
                return "DUMMY"
            @property
            def llm_description(self) -> str:
                return "Dummy tool"
            @property
            def response_mode(self) -> ToolResponseMode:
                return ToolResponseMode.DIRECT_UI
            def can_handle_by_regex(self, text: str) -> bool:
                return False
            async def execute(self, *args, **kwargs):
                return None

        agent = DispatchAgentTool([DummyTool()], mode="INTEGRATED")
        captured_model = ""

        async def fake_call_llm(brain, system_prompt, user_prompt, model, **kwargs):
            nonlocal captured_model
            captured_model = model
            return "<thought>看到圖片</thought> 這張圖真好看！"

        with patch.object(agent, '_call_llm', side_effect=fake_call_llm):
            with patch.object(settings, 'VISION_MODEL', 'lms:my_qwen2.5_vl'):
                await agent._run_integrated(
                    brain=self.brain,
                    bot=self.bot,
                    message=self.message,
                    user_query="看看這張圖",
                    vibe_snippet="",
                    full_context="",
                    images=["fake_base64_img"],
                    start_time=100.0,
                    visual_context="【原生端到端視覺】：妳正親眼直接注視著眼前這張圖片。",
                )

        self.assertEqual(captured_model, "lms:my_qwen2.5_vl")

    @patch('handlers.social.dispatch_agent.LiveThinkingSession')
    async def test_integrated_strategy_falls_back_to_primary_model_when_vision_empty(
        self,
        mock_session_cls: MagicMock,
    ) -> None:
        """測試 IntegratedStrategy 在有圖片但 VISION_MODEL 為空時，推論模型回退為 PRIMARY_MODEL。"""
        from handlers.social.dispatch_agent import DispatchAgentTool
        from handlers.social.tool_base import BaseTool, ToolResponseMode

        mock_session = MagicMock()
        mock_session.is_enabled = True
        mock_session.placeholder_msg = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.finish = AsyncMock()
        mock_session_cls.return_value = mock_session

        class DummyTool(BaseTool):
            @property
            def intent_name(self) -> str:
                return "DUMMY"
            @property
            def llm_description(self) -> str:
                return "Dummy tool"
            @property
            def response_mode(self) -> ToolResponseMode:
                return ToolResponseMode.DIRECT_UI
            def can_handle_by_regex(self, text: str) -> bool:
                return False
            async def execute(self, *args, **kwargs):
                return None

        agent = DispatchAgentTool([DummyTool()], mode="INTEGRATED")
        captured_model = ""

        async def fake_call_llm(brain, system_prompt, user_prompt, model, **kwargs):
            nonlocal captured_model
            captured_model = model
            return "<thought>看到圖片</thought> 這張圖真好看！"

        with patch.object(agent, '_call_llm', side_effect=fake_call_llm):
            with patch.object(settings, 'VISION_MODEL', ''):
                with patch.object(settings, 'PRIMARY_MODEL', 'freetoken:my_primary_model'):
                    await agent._run_integrated(
                        brain=self.brain,
                        bot=self.bot,
                        message=self.message,
                        user_query="看看這張圖",
                        vibe_snippet="",
                        full_context="",
                        images=["fake_base64_img"],
                        start_time=100.0,
                        visual_context="【原生端到端視覺】：妳正親眼直接注視著眼前這張圖片。",
                    )

        self.assertEqual(captured_model, "freetoken:my_primary_model")
