"""
handlers/social/dispatch/parallel.py
====================================
雙階段並行調度策略（ParallelStrategy）。
由 Planner 模型進行意圖分析與任務分解，並行執行多工具，最後視需要由 Primary 模型合成回覆。
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

import discord
from discord.ext import commands

import settings
from handlers.social.contracts import ToolCategory
from handlers.social.dispatch.base import BaseDispatchStrategy
from handlers.social.dispatch.models import ExecutionPlan, PlannedTask, _parse_plan_json
from handlers.social.dispatch.prompts import (
    _PARALLEL_PLANNER_SYSTEM_TEMPLATE,
    build_user_turn_prompt,
    extract_vision_context,
)
from handlers.social.observability import log_event
from handlers.social.tool_base import ToolResponseMode
from handlers.social.utils import remove_hourglass_reaction, simulate_typing_and_send

logger = logging.getLogger(__name__)


class ParallelStrategy(BaseDispatchStrategy):
    """雙階段並行調度策略。"""

    @property
    def mode_name(self) -> str:
        return "PARALLEL"

    def should_early_terminate(
        self,
        plan: ExecutionPlan,
        executed_results: list[tuple[str, ToolResponseMode, str]],
        user_query: str,
    ) -> bool:
        """判定是否觸發 PARALLEL 模式的行為自適應對話抑制。"""
        if not getattr(settings, "ACTION_EARLY_TERMINATION_ENABLED", True):
            return False
        if not executed_results:
            return False
        if any(mode != ToolCategory.DIRECT_UI for _, mode, _ in executed_results):
            return False
        if plan.needs_persona_reply:
            return False
        if re.search(r"(並|而且|順便|跟我說|告訴我|分享|覺得|看法|心得|對話|聊天|喜不喜歡|喜愛|早安|晚安)", user_query):
            return False
        return True

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
        """PARALLEL 模式主流程。"""
        import handlers.social.dispatch_agent as da

        channel_id = message.channel.id
        display_name = message.author.display_name
        user_id = message.author.id
        planner_model = getattr(settings, "PARALLEL_PLANNER_MODEL", settings.CLASSIFIER_MODEL)
        planner_timeout = getattr(settings, "PARALLEL_STEP_TIMEOUT", 60.0)

        # 啟動即時思考推演會話
        session = da.LiveThinkingSession(message)
        await session.start("📋 意圖解析中")

        clean_query, active_vis = extract_vision_context(user_query, visual_context)

        system_prompt = _PARALLEL_PLANNER_SYSTEM_TEMPLATE.format(
            tool_descriptions=self._build_tool_descriptions()
        )
        planner_user_prompt = build_user_turn_prompt(
            display_name=display_name,
            user_query=clean_query,
            visual_context=active_vis,
        )
        t_plan_start = time.monotonic()
        planner_raw = await self._agent._call_llm(
            brain,
            system_prompt,
            planner_user_prompt,
            task_id=f"parallel-plan-{message.id}",
            model=planner_model,
            temperature=0.1,
            timeout=planner_timeout,
        )
        plan_latency_ms = max(1, int((time.monotonic() - t_plan_start) * 1000))

        plan = _parse_plan_json(planner_raw, clean_query or user_query)
        logger.info(
            "[Parallel Planner] 思考: %s | 任務數: %d | 需性格回覆: %s",
            plan.thought or "無",
            len(plan.tasks),
            plan.needs_persona_reply,
        )

        plan_desc = plan.thought or (
            f"規劃調用 {', '.join([t.tool for t in plan.tasks])}" if plan.tasks else "日常閒聊對話"
        )
        session.push_step("plan", plan_desc, duration_ms=plan_latency_ms)

        max_tasks = getattr(settings, "PARALLEL_MAX_TASKS", 5)
        seen_keys: set[tuple[str, str]] = set()
        deduped: list[PlannedTask] = []
        for t in plan.tasks[:max_tasks]:
            key = (t.tool.upper(), t.input.strip())
            if key not in seen_keys:
                seen_keys.add(key)
                deduped.append(t)

        executed_results: list[tuple[str, ToolResponseMode, str]] = []
        if deduped:
            t_tools_start = time.monotonic()
            executed_results = list(await asyncio.gather(*[
                self._agent._parallel_execute_single_task(t, bot, message, vibe_snippet, full_context, images=images)
                for t in deduped
            ]))
            tools_latency_ms = max(1, int((time.monotonic() - t_tools_start) * 1000))

        invoked_tool_names = [name for name, _, _ in executed_results]
        if executed_results:
            obs_desc = ", ".join([f"{name}: {obs[:50]}" for name, _, obs in executed_results])
            session.push_step("obs", obs_desc, duration_ms=tools_latency_ms)

        if self._agent._parallel_should_early_terminate(plan, executed_results, clean_query or user_query):
            await remove_hourglass_reaction(message, bot.user)
            elapsed_ms = int((time.time() - start_time) * 1000)
            cot_thought = (
                f"【Parallel 規劃思考】\n{plan.thought}\n\n【已發動工具】\n"
                + "\n".join([f"- {name}: {obs}" for name, _, obs in executed_results])
            )
            log_event(
                "chat_reply",
                message_id=message.id,
                user=display_name,
                channel_id=channel_id,
                raw_input=clean_query or user_query,
                intent="PARALLEL_AGENT",
                intent_source="parallel_early_term",
                cot_thought=cot_thought,
                reply_preview=f"已並行發動: {invoked_tool_names}",
                model_used=planner_model,
                latency_ms=elapsed_ms,
                tools_invoked=invoked_tool_names,
            )
            if session.is_enabled and session.placeholder_msg:
                await session.finish(
                    f"✨ 已為您發動: {', '.join(invoked_tool_names)}",
                    thought_log=cot_thought,
                    intent_name="PARALLEL_AGENT",
                    model=planner_model,
                    latency_ms=elapsed_ms,
                    prompt=clean_query or user_query,
                )
            return

        obs_pairs = [(name, obs) for name, _, obs in executed_results]
        final_speech, persona_thought, sys_prompt, user_prompt = await self._agent._generate_final_response(
            brain=brain,
            user_query=clean_query,
            channel_id=channel_id,
            bot=bot,
            user_id=user_id,
            display_name=display_name,
            observations=obs_pairs,
            vibe_snippet=vibe_snippet,
            full_context=full_context,
            images=images,
            session=session,
            server_id=str(message.guild.id) if message.guild else "",
            visual_context=active_vis,
        )

        elapsed_ms = int((time.time() - start_time) * 1000)
        thought_sections: list[str] = []
        if plan.thought:
            thought_sections.append(f"【Parallel 任務排程思考】\n{plan.thought}")
        if obs_pairs:
            thought_sections.append("【工具並行回傳數據】\n" + "\n".join([f"- {t}: {o}" for t, o in obs_pairs]))
        if persona_thought:
            thought_sections.append(f"【雪莉內心思考】\n{persona_thought}")
        full_cot_thought = "\n\n".join(thought_sections)

        vision_model_setting = str(getattr(settings, "VISION_MODEL", "") or "").strip()
        active_model = vision_model_setting if (images and vision_model_setting) else settings.PRIMARY_MODEL

        log_event(
            "chat_reply",
            message_id=message.id,
            user=display_name,
            channel_id=channel_id,
            raw_input=user_query,
            intent="PARALLEL_AGENT",
            intent_source="parallel_agent_full",
            cot_thought=full_cot_thought,
            reply_preview=final_speech[:120],
            model_used=active_model,
            latency_ms=elapsed_ms,
            tools_invoked=invoked_tool_names,
        )
        if session.is_enabled and session.placeholder_msg:
            await session.finish(
                final_speech,
                thought_log=full_cot_thought,
                intent_name="PARALLEL_AGENT",
                model=f"{planner_model} -> {active_model}",
                latency_ms=elapsed_ms,
                prompt=user_query,
            )
        else:
            await simulate_typing_and_send(
                message,
                final_speech,
                intent_name="PARALLEL_AGENT",
                prompt=user_query,
                model=f"{planner_model} -> {active_model}",
                system_prompt=sys_prompt,
                user_prompt=user_prompt,
                latency_ms=elapsed_ms,
                thought_log=full_cot_thought,
            )
