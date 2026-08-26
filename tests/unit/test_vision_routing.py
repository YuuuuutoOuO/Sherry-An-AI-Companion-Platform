"""
tests/unit/test_vision_routing.py
=================================
多模態視覺路由（VISION_MODEL 雙軌機制）單元測試。
驗證：
  1. VISION_MODEL 為空時：跳過中介獨立視覺推論，直接以「端到端原生多模態」將圖片傳入 PRIMARY_MODEL。
  2. VISION_MODEL 有值時：維持二階段獨立 Vision 模型圖轉文與自我感知特徵提取。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import discord
import pytest
from discord.ext import commands

from handlers.social.parallel_agent import ParallelAgentTool
from handlers.social.react_agent import ReactAgentTool
from handlers.social.router import route_and_execute_tag
from handlers.social.tool_base import BaseTool, ToolResponseMode
from handlers.bot_brain.models import InferenceTask
import settings


class DummyChatTool(BaseTool):
    @property
    def intent_name(self) -> str:
        return "CHAT"

    @property
    def llm_description(self) -> str:
        return "一般聊天"

    @property
    def response_mode(self) -> ToolResponseMode:
        return ToolResponseMode.DATA_ONLY

    async def execute(
        self,
        bot: commands.Bot,
        message: discord.Message,
        user_query: str,
        vibe_snippet: str,
        full_context: str,
        **kwargs: object,
    ) -> None:
        pass


@pytest.fixture
def mock_bot_and_message():
    bot = MagicMock(spec=commands.Bot)
    bot.user.display_name = "雪莉"

    mock_brain = MagicMock()
    mock_brain.enqueue_task = AsyncMock()
    bot.get_cog.return_value = mock_brain

    message = MagicMock(spec=discord.Message)
    message.id = 12345
    message.author.id = 9999
    message.author.display_name = "TestUser"
    message.channel.id = 8888
    message.clean_content = "@雪莉 這張圖片是什麼？"

    # 模擬 1 張圖片 attachment
    attachment = MagicMock(spec=discord.Attachment)
    attachment.content_type = "image/png"
    attachment.read = AsyncMock(return_value=b"fake_image_bytes")
    message.attachments = [attachment]

    return bot, message, mock_brain


def test_vision_routing_end_to_end_when_vision_model_empty(mock_bot_and_message):
    """當 VISION_MODEL 為空時，應略過獨立 vision task，直接走端到端原生多模態。"""
    async def _run():
        bot, message, mock_brain = mock_bot_and_message

        with patch.object(settings, "VISION_MODEL", ""), \
             patch("handlers.social.parallel_agent.ParallelAgentTool") as MockParallelAgent, \
             patch("handlers.social.router.fetch_context_script", new=AsyncMock(return_value="")), \
             patch("handlers.social.router.remove_hourglass_reaction", new=AsyncMock()):

            mock_instance = MagicMock()
            mock_instance.execute = AsyncMock()
            MockParallelAgent.return_value = mock_instance

            await route_and_execute_tag(bot, message)

            # 1. 驗證 brain.enqueue_task 沒有被呼叫去跑中介的 vision-* 任務
            vision_tasks = [
                call.args[0] for call in mock_brain.enqueue_task.call_args_list
                if getattr(call.args[0], "task_id", "").startswith("vision-")
            ]
            assert len(vision_tasks) == 0, "VISION_MODEL 為空時不應發起中介 vision task"

            # 2. 驗證 parallel_agent.execute 有接收到 images 參數
            mock_instance.execute.assert_awaited_once()
            _, kwargs = mock_instance.execute.call_args
            assert kwargs.get("images") is not None
            assert len(kwargs["images"]) == 1

    asyncio.run(_run())


def test_vision_routing_two_stage_when_vision_model_set(mock_bot_and_message):
    """當 VISION_MODEL 有設定時，應執行獨立 vision- 任務進行文字提取。"""
    async def _run():
        bot, message, mock_brain = mock_bot_and_message

        async def fake_enqueue(task: InferenceTask):
            if task.task_id.startswith("vision-"):
                # 模擬視覺模型回傳描述
                await task.callback("這是一張藍色丸子頭少女戴福爾摩斯帽的照片")

        mock_brain.enqueue_task.side_effect = fake_enqueue

        with patch.object(settings, "VISION_MODEL", "ollama:minicpm-v"), \
             patch("handlers.social.parallel_agent.ParallelAgentTool") as MockParallelAgent, \
             patch("handlers.social.router.fetch_context_script", new=AsyncMock(return_value="")), \
             patch("handlers.social.router.remove_hourglass_reaction", new=AsyncMock()):

            mock_instance = MagicMock()
            mock_instance.execute = AsyncMock()
            MockParallelAgent.return_value = mock_instance

            await route_and_execute_tag(bot, message)

            # 1. 驗證有發起獨立的 vision- 任務且 model 為指定模型
            vision_task_calls = [
                call.args[0] for call in mock_brain.enqueue_task.call_args_list
                if getattr(call.args[0], "task_id", "").startswith("vision-")
            ]
            assert len(vision_task_calls) == 1
            assert vision_task_calls[0].model == "ollama:minicpm-v"

            # 2. 驗證 query 包含了自我辨識與視覺解析文字
            mock_instance.execute.assert_awaited_once()
            args, kwargs = mock_instance.execute.call_args
            user_query_passed = args[2]
            assert "系統視覺解析結果" in user_query_passed
            assert "⚠️自我辨識" in user_query_passed
            # 由於已二階段轉譯成文字，不再向後傳遞 raw images
            assert kwargs.get("images") is None

    asyncio.run(_run())


def test_parallel_agent_passes_images_to_primary_model():
    """驗證 ParallelAgent 在生成最終回覆時將 images 正確傳給 PRIMARY_MODEL。"""
    async def _run():
        tools = [DummyChatTool()]
        agent = ParallelAgentTool(tools)

        bot = MagicMock(spec=commands.Bot)
        mock_brain = MagicMock()
        mock_brain.enqueue_task = AsyncMock()
        bot.get_cog.return_value = mock_brain

        async def fake_enqueue(task: InferenceTask):
            if "parallel-final-" in task.task_id:
                await task.callback("<thought>看到了圖片</thought>這是一張很有趣的圖片！")
            else:
                # Planner
                await task.callback('{"thought":"聊天","tasks":[{"tool":"CHAT","input":"這張圖如何"}],"needs_persona_reply":true}')

        mock_brain.enqueue_task.side_effect = fake_enqueue

        message = MagicMock(spec=discord.Message)
        message.id = 123
        message.author.id = 456
        message.author.display_name = "UserA"
        message.channel.id = 789

        with patch("handlers.social.parallel_agent.simulate_typing_and_send", new=AsyncMock()), \
             patch("handlers.social.parallel_agent.build_dynamic_persona", return_value=("head", "tail")), \
             patch("handlers.social.parallel_agent.build_user_memory_block", return_value=""), \
             patch("handlers.social.parallel_agent.remove_hourglass_reaction", new=AsyncMock()):

            await agent.execute(
                bot=bot,
                message=message,
                user_query="這張圖如何",
                vibe_snippet="",
                full_context="",
                images=["base64_test_img"],
            )

            # 尋找 parallel-final- 任務
            final_tasks = [
                call.args[0] for call in mock_brain.enqueue_task.call_args_list
                if getattr(call.args[0], "task_id", "").startswith("parallel-final-")
            ]
            assert len(final_tasks) == 1
            assert final_tasks[0].images == ["base64_test_img"]
            assert "多模態視覺自我感知提示" in final_tasks[0].system_prompt

    asyncio.run(_run())


@pytest.mark.parametrize("prefix_only_model", ["freetoken:", "lms:", "ollama:", "openai:", "freetoken:  ", "lms:  ", "   "])
def test_vision_routing_end_to_end_when_vision_model_is_prefix_only(mock_bot_and_message, prefix_only_model: str):
    """當 VISION_MODEL 僅有前綴（如 freetoken: 或 lms:）而無模型名稱時，應自動走端到端原生多模態。"""
    async def _run():
        bot, message, mock_brain = mock_bot_and_message

        with patch.object(settings, "VISION_MODEL", prefix_only_model), \
             patch("handlers.social.router.rate_limiter.is_allowed", return_value=True), \
             patch("handlers.social.parallel_agent.ParallelAgentTool") as MockParallelAgent, \
             patch("handlers.social.router.fetch_context_script", new=AsyncMock(return_value="")), \
             patch("handlers.social.router.remove_hourglass_reaction", new=AsyncMock()):

            mock_instance = MagicMock()
            mock_instance.execute = AsyncMock()
            MockParallelAgent.return_value = mock_instance

            await route_and_execute_tag(bot, message)

            # 1. 驗證 brain.enqueue_task 沒有被呼叫去跑中介的 vision-* 任務
            vision_tasks = [
                call.args[0] for call in mock_brain.enqueue_task.call_args_list
                if getattr(call.args[0], "task_id", "").startswith("vision-")
            ]
            assert len(vision_tasks) == 0, f"VISION_MODEL='{prefix_only_model}' 時不應發起中介 vision task"

            # 2. 驗證 parallel_agent.execute 有接收到 images 參數
            mock_instance.execute.assert_awaited_once()
            _, kwargs = mock_instance.execute.call_args
            assert kwargs.get("images") is not None
            assert len(kwargs["images"]) == 1

    asyncio.run(_run())


def test_ollama_client_parse_model_and_empty_fallback():
    """驗證 OllamaClient 針對只有前綴或空模型時的解析與回退邏輯。"""
    from handlers.bot_brain.client import OllamaClient

    # 1. 測試解析 FreeToken 前綴
    backend, model_name = OllamaClient._parse_model("freetoken:mistralai/Mixtral-8x7B-Instruct-v0.1")
    assert backend == "freetoken"
    assert model_name == "mistralai/Mixtral-8x7B-Instruct-v0.1"

    backend, model_name = OllamaClient._parse_model("freetoken:")
    assert backend == "freetoken"
    assert model_name == ""

    # 2. 測試解析 LMS 前綴
    backend, model_name = OllamaClient._parse_model("lms:gemma4-12b")
    assert backend == "lms"
    assert model_name == "gemma4-12b"

    backend, model_name = OllamaClient._parse_model("lms:")
    assert backend == "lms"
    assert model_name == ""

    # 3. 測試解析 Ollama 前綴
    backend, model_name = OllamaClient._parse_model("ollama:minicpm-v")
    assert backend == "ollama"
    assert model_name == "minicpm-v"

    backend, model_name = OllamaClient._parse_model("ollama:")
    assert backend == "ollama"
    assert model_name == ""

