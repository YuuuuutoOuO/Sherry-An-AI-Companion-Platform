"""
handlers/social/dispatch/react.py
=================================
多步驟循環推理調度策略（ReactStrategy）。
遵循 Thought -> Action -> Observation 經典循環，最多執行 REACT_MAX_STEPS 步。
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

import discord
from discord.ext import commands

import settings
from handlers.social.dispatch.base import BaseDispatchStrategy
from handlers.social.dispatch.models import _parse_react_output
from handlers.social.dispatch.prompts import (
    _OBSERVATION_TEMPLATE,
    _REACT_CONTINUATION_TEMPLATE,
    _REACT_SYSTEM_TEMPLATE,
    _TOOL_ALIASES,
    build_user_turn_prompt,
    extract_vision_context,
)
from handlers.social.observability import log_event
from handlers.social.utils import remove_hourglass_reaction, simulate_typing_and_send

logger = logging.getLogger(__name__)


class ReactStrategy(BaseDispatchStrategy):
    """多步驟循環推理調度策略。"""

    @property
    def mode_name(self) -> str:
        return "REACT"

    async def execute(
        self,
        brain: Any,
        bot: commands.Bot,
        message: discord.Message,
        user_query: str,
        vibe_snippet: str,
        full_context: str,
        images: list[str] | None,
        start_time: float,
        visual_context: str = "",
    ) -> None:
        """REACT 模式主流程（多步推理迴圈）。"""
        import handlers.social.dispatch_agent as da

        channel_id = message.channel.id
        user_id = message.author.id
        display_name = message.author.display_name
        react_model = settings.PRIMARY_MODEL

        session = da.LiveThinkingSession(message)
        await session.start("🔍 思考第一步推理中")

        clean_query, active_vis = extract_vision_context(user_query, visual_context)

        system_prompt = _REACT_SYSTEM_TEMPLATE.format(
            tool_descriptions=self._build_tool_descriptions(include_chat=True)
        )

        transcript_parts: list[str] = []
        executed_observations: list[tuple[str, str]] = []
        called_tool_actions: set[tuple[str, str]] = set()
        step_thoughts: list[str] = []
        final_answer: str | None = None

        for step in range(1, settings.REACT_MAX_STEPS + 1):
            logger.info("DispatchAgent[REACT]: Step %d / %d", step, settings.REACT_MAX_STEPS)

            if step == 1:
                user_prompt = build_user_turn_prompt(
                    display_name=display_name,
                    user_query=clean_query,
                    visual_context=active_vis,
                )
            else:
                transcript_str = "\n\n".join(transcript_parts)
                max_chars = settings.REACT_MAX_TRANSCRIPT_CHARS
                if len(transcript_str) > max_chars:
                    transcript_str = "...(歷史已截斷)...\n\n" + transcript_str[-max_chars:]
                user_prompt = _REACT_CONTINUATION_TEMPLATE.format(transcript=transcript_str)

            raw_output = await self._agent._call_llm(
                brain,
                system_prompt,
                user_prompt,
                task_id=f"react-step{step}-{message.id}",
                model=react_model,
                temperature=0.1,
                timeout=settings.REACT_STEP_TIMEOUT,
                user_id=message.author.id,
            )

            if not raw_output:
                logger.warning("DispatchAgent[REACT]: Step %d LLM 空白，強制結束", step)
                break

            parsed = _parse_react_output(raw_output)
            thought_text = parsed.get("thought", "").strip()
            if thought_text:
                step_thoughts.append(f"[Step {step}] {thought_text}")
                session.push_step("plan", f"Step {step}: {thought_text}")
                logger.info("[ReAct Step %d] 思考: %s", step, thought_text)

            if parsed["type"] == "final":
                final_answer = parsed.get("final_answer", "").strip()
                logger.info("[ReAct Step %d] 規劃完成，準備交由主力模型", step)
                break

            if parsed["type"] == "action":
                raw_tool_name = parsed.get("action", "").upper()
                action_input = parsed.get("action_input", user_query).strip()
                tool_name = _TOOL_ALIASES.get(raw_tool_name, raw_tool_name)

                session.push_step("action", f"調用工具 [{tool_name}]: {action_input[:40]}")
                logger.info("[ReAct Step %d] 行動: 呼叫 [%s] | 指令: '%s'", step, tool_name, action_input)
                if thought_text:
                    transcript_parts.append(f"<thought>\n{thought_text}\n</thought>")
                transcript_parts.append(
                    f"<action>{tool_name}</action>\n<action_input>{action_input}</action_input>"
                )

                if tool_name == "CHAT":
                    transcript_parts.append("<observation>CHAT 已觸發，交由主力模型回覆。</observation>")
                    break

                tool_key = (tool_name, action_input)
                if tool_key in called_tool_actions:
                    logger.warning("[ReAct] 攔截重複呼叫 %s('%s')", tool_name, action_input)
                    transcript_parts.append(
                        _OBSERVATION_TEMPLATE.format(
                            tool=tool_name,
                            result=f"你已對 [{tool_name}] 執行過相同指令。請勿重複呼叫，推進下一步。",
                        )
                    )
                    continue

                called_tool_actions.add(tool_key)
                t_tool_start = time.monotonic()
                observation = await self._agent._react_execute_tool(
                    tool_name,
                    action_input,
                    bot,
                    message,
                    vibe_snippet,
                    full_context,
                    images=images,
                )
                tool_latency_ms = max(1, int((time.monotonic() - t_tool_start) * 1000))
                transcript_parts.append(_OBSERVATION_TEMPLATE.format(tool=tool_name, result=observation))
                executed_observations.append((tool_name, observation))
                session.push_step("obs", f"取得 [{tool_name}] 觀測: {observation[:40]}", duration_ms=tool_latency_ms)
                logger.info("[ReAct Step %d] 觀察 [%s]: %s", step, tool_name, observation[:150].replace("\n", " "))

        has_chat_request = bool(
            re.search(r"(並|而且|順便|跟我說|告訴我|分享|覺得|看法|心得|對話|聊天|喜不喜歡|喜愛)", user_query)
        )
        if len(executed_observations) == 1 and not has_chat_request:
            only_tool, _ = executed_observations[0]
            if only_tool in ("PIXIV", "DRAW", "MUSIC"):
                await remove_hourglass_reaction(message, bot.user)
                elapsed_ms = int((time.time() - start_time) * 1000)
                full_cot_thought = "【ReAct 任務調度思考】\n" + "\n".join(step_thoughts) if step_thoughts else ""
                log_event(
                    "chat_reply",
                    message_id=message.id,
                    user=display_name,
                    channel_id=channel_id,
                    raw_input=user_query,
                    intent="REACT",
                    intent_source="react_agent_smart_term",
                    cot_thought=full_cot_thought,
                    reply_preview=f"已發動媒體工具 [{only_tool}]",
                    model_used=react_model,
                    latency_ms=elapsed_ms,
                    tools_invoked=[only_tool],
                )
                if session.is_enabled and session.placeholder_msg:
                    await session.finish(
                        f"✨ 已為您發動: {only_tool}",
                        thought_log=full_cot_thought,
                        intent_name="REACT",
                        model=react_model,
                        latency_ms=elapsed_ms,
                        prompt=user_query,
                    )
                return

        final_speech, persona_thought, final_sys_prompt, final_user_prompt = await self._agent._generate_final_response(
            brain=brain,
            user_query=clean_query,
            channel_id=channel_id,
            bot=bot,
            user_id=user_id,
            display_name=display_name,
            observations=executed_observations,
            vibe_snippet=vibe_snippet,
            full_context=full_context,
            draft_answer=final_answer or "",
            images=images,
            session=session,
            server_id=str(message.guild.id) if message.guild else "",
            visual_context=active_vis,
        )

        elapsed_ms = int((time.time() - start_time) * 1000)
        thought_sections: list[str] = []
        if step_thoughts:
            thought_sections.append("【ReAct 任務調度思考】\n" + "\n".join(step_thoughts))
        if persona_thought:
            thought_sections.append(f"【雪莉角色內心思考】\n{persona_thought}")
        full_cot_thought = "\n\n".join(thought_sections)

        vision_model_setting = str(getattr(settings, "VISION_MODEL", "") or "").strip()
        active_model = vision_model_setting if (images and vision_model_setting) else settings.PRIMARY_MODEL

        if final_speech:
            clean_answer = re.sub(r"\*.*?\*", "", final_speech).strip()
            clean_answer = re.sub(r"\n{3,}", "\n\n", clean_answer).strip()
            if clean_answer:
                if session.is_enabled and session.placeholder_msg:
                    await session.finish(
                        clean_answer,
                        thought_log=full_cot_thought,
                        intent_name="REACT",
                        model=f"{react_model} -> {active_model}",
                        latency_ms=elapsed_ms,
                        prompt=user_query,
                    )
                else:
                    await simulate_typing_and_send(
                        message,
                        clean_answer,
                        intent_name="REACT",
                        prompt=user_query,
                        model=f"{react_model} -> {active_model}",
                        system_prompt=final_sys_prompt,
                        user_prompt=final_user_prompt,
                        latency_ms=elapsed_ms,
                        thought_log=full_cot_thought,
                    )
                log_event(
                    "chat_reply",
                    message_id=message.id,
                    user=display_name,
                    channel_id=channel_id,
                    raw_input=user_query,
                    intent="REACT",
                    intent_source="react_agent",
                    cot_thought=full_cot_thought,
                    reply_preview=clean_answer[:200],
                    model_used=f"{react_model} -> {active_model}",
                    latency_ms=elapsed_ms,
                    tools_invoked=[t[0] for t in executed_observations],
                )
                return

        logger.warning("DispatchAgent[REACT]: 主力模型無輸出，改用 ChatTool 兜底")
        chat_tool = self._tools.get("CHAT")
        if chat_tool:
            await chat_tool.execute(bot, message, user_query, vibe_snippet, full_context, images=images)
