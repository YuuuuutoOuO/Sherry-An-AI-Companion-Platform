"""
handlers/social/dispatch_agent.py
===================================
統一調度 Agent（Coordinator），依據 settings.AGENT_DISPATCH_MODE 或 mode 參數
將請求分派給對應的策略實作（Integrated / Parallel / React）。

本模組維持 100% 向下相容之介面與匯出。
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Literal

import discord
from discord.ext import commands

import settings
from handlers.bot_brain import BotBrain, InferenceTask
from handlers.social.action_recorder import record_tool_action
from handlers.social.contracts import ToolCategory, ToolResult
from handlers.social.dispatch.base import BaseDispatchStrategy
from handlers.social.dispatch.integrated import IntegratedStrategy
from handlers.social.dispatch.models import (
    ExecutionPlan,
    PlannedTask,
    _extract_xml_tag,
    _parse_actions,
    _parse_plan_json,
    _parse_react_output,
)
from handlers.social.dispatch.parallel import ParallelStrategy
from handlers.social.dispatch.prompts import (
    VISION_AWARENESS_RULE,
    _INTEGRATED_TOOL_TEMPLATE,
    _OBSERVATION_TEMPLATE,
    _PARALLEL_PLANNER_SYSTEM_TEMPLATE,
    _REACT_CONTINUATION_TEMPLATE,
    _REACT_SYSTEM_TEMPLATE,
    _TOOL_ALIASES,
    _TOOL_DESC_TEMPLATE,
    _USER_MESSAGE_TEMPLATE,
    _load_tool_rules,
    build_user_turn_prompt,
    extract_vision_context,
)
from handlers.social.dispatch.react import ReactStrategy
from handlers.social.memory_builder import build_user_memory_block
from handlers.social.observability import log_tool_exec, make_timer
from handlers.social.persona import build_dynamic_persona
from handlers.social.streaming_thinking import LiveThinkingSession
from handlers.social.tool_base import BaseTool, ToolResponseMode
from handlers.social.tool_specs import TOOL_SPECS

logger = logging.getLogger(__name__)

parse_plan_json = _parse_plan_json

__all__ = [
    "PlannedTask",
    "ExecutionPlan",
    "DispatchAgentTool",
    "IntegratedAgentTool",
    "ParallelAgentTool",
    "ReactAgentTool",
    "LiveThinkingSession",
    "_parse_actions",
    "_parse_plan_json",
    "parse_plan_json",
    "_parse_react_output",
    "_extract_xml_tag",
    "_load_tool_rules",
    "_TOOL_ALIASES",
    "_TOOL_DESC_TEMPLATE",
    "_USER_MESSAGE_TEMPLATE",
    "_INTEGRATED_TOOL_TEMPLATE",
    "_PARALLEL_PLANNER_SYSTEM_TEMPLATE",
    "_REACT_SYSTEM_TEMPLATE",
    "_OBSERVATION_TEMPLATE",
    "_REACT_CONTINUATION_TEMPLATE",
]


class DispatchAgentTool(BaseTool):
    """統一調度 Agent（Coordinator）。

    支援 INTEGRATED（單階段整合）、PARALLEL（雙階段並行）與 REACT（多步驟）三種執行模式。
    內部採用策略模式（Strategy Pattern）將實際調度細節委任至 dispatch/ 子模組。
    """

    def __init__(
        self,
        tool_registry: list[BaseTool],
        mode: Literal["INTEGRATED", "PARALLEL", "REACT"] = "INTEGRATED",
    ) -> None:
        """初始化調度器並掛載策略實例。

        Args:
            tool_registry: 所有可用工具的列表。
            mode: 執行模式，'INTEGRATED'（預設）、'PARALLEL' 或 'REACT'。
        """
        self._mode = mode
        self._tools: dict[str, BaseTool] = {t.intent_name.upper(): t for t in tool_registry}
        self._strategies: dict[str, BaseDispatchStrategy] = {
            "INTEGRATED": IntegratedStrategy(self),
            "PARALLEL": ParallelStrategy(self),
            "REACT": ReactStrategy(self),
        }
        logger.info(
            "DispatchAgentTool 初始化完成，模式: %s，已載入工具：%s",
            self._mode,
            list(self._tools.keys()),
        )

    @property
    def intent_name(self) -> str:
        """Returns: 工具意圖名稱（依模式區分）。"""
        return f"{self._mode}_AGENT"

    @property
    def llm_description(self) -> str:
        """Returns: 工具說明。"""
        return f"統一調度 Agent（{self._mode} 模式，由 router 直接路由）"

    def can_handle_by_regex(self, text: str) -> bool:
        """Returns: 永遠回傳 False，由 router 直接呼叫。"""
        return False

    @property
    def current_strategy(self) -> BaseDispatchStrategy:
        """取得當前模式對應之策略實作。"""
        return self._strategies.get(self._mode, self._strategies["INTEGRATED"])

    # ------------------------------------------------------------------
    # 共用與測試相容方法 (Shared Infrastructure)
    # ------------------------------------------------------------------

    def _build_tool_descriptions(self, *, include_chat: bool = False) -> str:
        """組裝工具描述清單（順序固定跟隨 ``TOOL_SPECS``）。"""
        excluded = {"PARALLEL_AGENT", "REACT_AGENT", "DISPATCH_AGENT", "INTEGRATED_AGENT", "CHAT"}
        lines: list[str] = []
        for spec in TOOL_SPECS:
            if spec.name in excluded:
                continue
            tool = self._tools.get(spec.name)
            if tool is None:
                continue
            lines.append(
                _TOOL_DESC_TEMPLATE.format(name=spec.name, description=tool.llm_description)
            )
        if include_chat:
            lines.append("- **CHAT**：一般聊天回覆或問候，不需要呼叫外部工具時使用")
        return "\n".join(lines)

    async def _call_llm(
        self,
        brain: BotBrain,
        system_prompt: str,
        user_prompt: str,
        task_id: str,
        model: str,
        temperature: float = 0.1,
        timeout: float | None = None,
        images: list[str] | None = None,
        stream_callback: Any | None = None,
        user_id: int | str | None = None,
    ) -> str:
        """向 BotBrain 發送推理任務並等待結果。可供測試 mock。"""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()

        async def _on_reply(reply: str) -> None:
            if not future.done():
                future.set_result(reply)

        task = InferenceTask(
            priority=settings.PRIORITY_TAG_RESPONSE,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            callback=_on_reply,
            stream_callback=stream_callback,
            model=model,
            task_id=task_id,
            temperature=temperature,
            images=images,
            user_id=user_id,
        )
        await brain.enqueue_task(task)

        if timeout is not None:
            _timeout = float(timeout)
        elif images:
            _timeout = float(getattr(settings, "VISION_ANALYSIS_TIMEOUT", getattr(settings, "LLM_TIMEOUT", 180.0)))
        else:
            _timeout = float(getattr(settings, "LLM_TIMEOUT", getattr(settings, "REACT_STEP_TIMEOUT", 120.0)))

        try:
            return await asyncio.wait_for(future, timeout=_timeout)
        except asyncio.TimeoutError:
            # 真實取消傳播：取消底層串流 Task，立即關閉上游 HTTP 連線，
            # 而不是只設旗標等模型停止吐 chunk。
            task.cancel()
            logger.warning(
                "DispatchAgent[%s]: LLM 推理超時 (model=%s, %.1fs)，已取消底層請求",
                self._mode,
                model,
                _timeout,
            )
            return ""

    async def _parallel_execute_single_task(
        self,
        task: PlannedTask,
        bot: commands.Bot,
        message: discord.Message,
        vibe_snippet: str,
        full_context: str,
        images: list[str] | None = None,
    ) -> tuple[str, ToolResponseMode, str]:
        """非同步執行單一規劃工具任務。可供測試 mock。"""
        canonical_name = _TOOL_ALIASES.get(task.tool.upper(), task.tool.upper())
        tool = self._tools.get(canonical_name)
        if not tool:
            err_msg = f"找不到工具 [{task.tool}]"
            logger.warning("DispatchAgent[PARALLEL]: %s", err_msg)
            return canonical_name, ToolCategory.DATA_PROVIDER, err_msg

        logger.info(
            "[Parallel Tool] 工具: [%s] | 模式: %s | 參數: '%s'",
            canonical_name,
            tool.response_mode.value,
            task.input,
        )
        timer = make_timer()
        try:
            result = await tool.execute(
                bot,
                message,
                task.input,
                vibe_snippet,
                full_context,
                silent=True,
                images=images,
            )
            if isinstance(result, ToolResult):
                category = ToolCategory.DIRECT_UI if result.is_direct_rendered else ToolCategory.DATA_PROVIDER
                obs = result.summary_text
            else:
                category = tool.response_mode
                obs = str(result) if result is not None else f"工具 {canonical_name} 執行完畢。"

            latency = timer()
            log_tool_exec(
                tool_name=canonical_name,
                tool_input=task.input,
                observation=obs,
                latency_ms=latency,
                status="success",
                user=message.author.display_name,
                channel_id=message.channel.id,
                message_id=message.id,
            )
            # 行為記憶：讓雪莉記得自己剛剛做過什麼（播放、搜尋、繪圖…）
            record_tool_action(message, canonical_name, task.input, obs, status="success")
            max_obs = getattr(settings, "REACT_MAX_OBSERVATION_LENGTH", 1200)
            return canonical_name, category, obs[:max_obs]
        except Exception as exc:
            latency = timer()
            logger.error(
                "DispatchAgent[PARALLEL]: 工具 %s 執行失敗: %s", canonical_name, exc, exc_info=True
            )
            log_tool_exec(
                tool_name=canonical_name,
                tool_input=task.input,
                observation="",
                latency_ms=latency,
                status="error",
                error_msg=str(exc),
                user=message.author.display_name,
                channel_id=message.channel.id,
                message_id=message.id,
            )
            record_tool_action(
                message, canonical_name, task.input, f"執行失敗：{exc}", status="error"
            )
            return canonical_name, ToolCategory.DATA_PROVIDER, f"工具 {canonical_name} 執行失敗: {exc}"

    def _parallel_should_early_terminate(
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

    def _should_early_terminate(
        self,
        plan: ExecutionPlan,
        executed_results: list[tuple[str, ToolResponseMode, str]],
        user_query: str,
    ) -> bool:
        """舊版相容早期終止方法。"""
        return self._parallel_should_early_terminate(plan, executed_results, user_query)

    async def _react_execute_tool(
        self,
        tool_name: str,
        action_input: str,
        bot: commands.Bot,
        message: discord.Message,
        vibe_snippet: str,
        full_context: str,
        images: list[str] | None = None,
    ) -> str:
        """ReAct 模式執行工具方法。"""
        canonical_name = _TOOL_ALIASES.get(tool_name.upper(), tool_name.upper())
        tool = self._tools.get(canonical_name)
        if not tool:
            return f"找不到工具 {tool_name}，請確認工具名稱正確。"
        timer = make_timer()
        try:
            result = await tool.execute(
                bot,
                message,
                action_input,
                vibe_snippet,
                full_context,
                silent=True,
                images=images,
            )
            if isinstance(result, ToolResult):
                obs = result.summary_text
            elif result is None:
                obs = f"工具 {canonical_name} 執行完畢（無文字回傳）。"
            else:
                obs = str(result)
            latency = timer()
            log_tool_exec(
                tool_name=canonical_name,
                tool_input=action_input,
                observation=obs,
                latency_ms=latency,
                status="success",
                user=message.author.display_name,
                channel_id=message.channel.id,
                message_id=message.id,
            )
            record_tool_action(message, canonical_name, action_input, obs, status="success")
            return obs[:settings.REACT_MAX_OBSERVATION_LENGTH]
        except Exception as exc:
            latency = timer()
            logger.error("DispatchAgent[REACT]: 工具 %s 執行失敗: %s", canonical_name, exc, exc_info=True)
            log_tool_exec(
                tool_name=canonical_name,
                tool_input=action_input,
                observation="",
                latency_ms=latency,
                status="error",
                error_msg=str(exc),
                user=message.author.display_name,
                channel_id=message.channel.id,
                message_id=message.id,
            )
            record_tool_action(
                message, canonical_name, action_input, f"執行失敗：{exc}", status="error"
            )
            return f"工具 {canonical_name} 執行時發生錯誤：{exc}"

    async def _generate_final_response(
        self,
        brain: BotBrain,
        user_query: str,
        channel_id: int | None,
        bot: commands.Bot,
        user_id: int,
        display_name: str,
        observations: list[tuple[str, str]],
        vibe_snippet: str = "",
        full_context: str = "",
        draft_answer: str = "",
        images: list[str] | None = None,
        session: LiveThinkingSession | None = None,
        server_id: str = "",
        visual_context: str = "",
    ) -> tuple[str, str, str, str]:
        """使用 PRIMARY_MODEL 合成最終給使用者的對話文本。"""
        clean_query, active_vis = extract_vision_context(user_query, visual_context)

        recent_text = (full_context[-1500:] + "\n" + clean_query) if full_context else clean_query
        head_persona, tail_lore = build_dynamic_persona(
            recent_text,
            "CHAT",
            channel_id=channel_id,
            server_id=server_id,
            user_query=clean_query,
            bot=bot,
            guild_id=server_id or None,
            include_memory=not bool(getattr(settings, "MEMORY_CONTEXT_OWNS_PERSONA_MEMORY", True)),
        )
        memory_block = ""
        if bool(getattr(settings, "MEMORY_CONTEXT_SERVICE_ENABLED", True)):
            try:
                from handlers.memory.context_service import MemoryContextService

                bundle = MemoryContextService.build(
                    bot=bot,
                    user_id=user_id,
                    user_name=display_name,
                    query=clean_query,
                    channel_id=channel_id or "",
                    guild_id=server_id,
                    mode="mention",
                )
                memory_block = bundle.render()
            except Exception as exc:
                logger.debug("DispatchAgent: MemoryContextService 失敗，改用舊記憶注入: %s", exc)
        if not memory_block:
            memory_block = build_user_memory_block(bot, user_id, display_name, query_text=clean_query)

        if session and session.is_enabled and not any(s[0] == "memory" for s in session.steps):
            if memory_block and memory_block.strip():
                mem_lines = [line.strip() for line in memory_block.splitlines() if line.strip().startswith("-")]
                mem_count = len(mem_lines) if mem_lines else 1
                session.push_step("memory", f"檢索群友 [{display_name}] 互動記憶 ({mem_count} 筆相關事實與親密度)")

        vibe_block = (
            "【頻道近期話題氛圍】(這只是讓妳用來讀空氣的背景氣氛！)\n"
            f"{vibe_snippet}\n\n"
        ) if vibe_snippet else ""

        is_sherry = getattr(settings, "CURRENT_SOUL_NAME", "manosaba_sherry_default.md") in (
            "sherry_default.md",
            "manosaba_sherry_default.md",
        )
        vision_self_lore = (
            "\n【多模態視覺自我感知提示】：若圖片中出現淺藍髮色、丸子頭、白鳥羽毛帽、偵探斗篷等特徵的少女，這就是妳自己（橘雪莉）！\n"
        ) if (is_sherry and (images or active_vis)) else ""

        author_note = f"\n{tail_lore}\n{vision_self_lore}" if (tail_lore or vision_self_lore) else ""

        tool_guidelines = ""
        # 圖片訊息：聚焦於看圖對話與視覺互動，不載入工具自主調用規則（避免視覺模型誤觸發多餘工具）
        if not (bool(images) or bool(active_vis) or "【群友上傳了一張圖片" in user_query):
            tool_guidelines = _load_tool_rules().format(
                tool_descriptions=self._build_tool_descriptions()
            )

        length_rule = (
            "2. 【自然生動且精準的篇幅】：徹底貫徹角色個性與說話口吻。\n"
            "   - 【日常互動與閒聊】：篇幅請保持精簡自然（約 30~80 字，1~3 句話為佳），有溫度且口語化，如同身邊好友面對面隨口交流。可帶至多 1 個簡短神態或動作點綴，嚴禁輸出大段小說式心理戲、繁冗動作描寫或說教式長文！\n"
            "   - 【分析、搜尋與深度解答】：僅在處理工具回傳數據（如情報搜尋、股票分析、長文總結）或對方明確要求「詳細說明/深度分析」時，才展開條理分明、豐富詳實的完整回答。"
        )

        cot_instruction = ""
        if getattr(settings, "MICRO_COT_ENABLED", True):
            cot_instruction = (
                "0. 🧠 【深度思考機制】：在給出任何動作或回覆前，妳「必須」先用 <thought>...</thought> 標籤包覆妳的內心思考、局勢分析與回覆策略。\n"
            )

        vision_rule = f"{VISION_AWARENESS_RULE}\n" if active_vis else ""

        # ── [第一區：靜態系統前綴 (Static Prefix) — 與 Pass-1 100% 嚴格對齊以最大化 KV Cache] ──
        # 包含：核心靈魂人設 (Soul) + 工具調用規範 + 恆定回覆原則 (CoT, 篇幅, 工具優先與誠實原則)
        # 此區塊在相同頻道與意圖下保持完全靜態，確保推論引擎首 3,500+ 字 100% 命中前綴快取。
        static_reply_rules = (
            "【回覆規則】\n"
            f"{cot_instruction}"
            "1. 【對話連貫與話題推進】：自然接續剛才的對話脈絡與互動情境。若當前話題正在進行，請順著剛才的情緒與情境深入延續，嚴禁每輪重複「欸～笨蛋」、「你在說什麼」等初次驚訝的模板化開場白！\n"
            f"{length_rule}\n"
            "3. 【工具優先】：調用工具時僅輸出 <action> 標籤，無須工具的純閒聊則直接輸出性格對白；若已知事實不足或查無資料請如實告知，嚴禁臆造不存在之事實。"
        )

        static_parts = [head_persona]
        if tool_guidelines:
            static_parts.append(tool_guidelines)
        static_parts.append(static_reply_rules)
        static_prefix = "\n\n".join(static_parts)

        # ── [第二區：動態情境 (Dynamic Context)] ──
        # 與 Pass-1 保持完全一致的排列順序以最大化同頻道與跨階段的 KV Cache / Prefix Caching 共享：
        # 1. 頻道近期氣氛 (同頻道全體共用)
        # 2. 頻道歷史對話 (同頻道全體共用且持續 Append)
        # 3. 視覺感官認知規則 (若有圖片)
        # 4. 當前發話者個人記憶 (針對個人)
        # 5. 作者備註與動態尾部 (世界記憶 + 時間戳記 + 即時線索，緊貼生成點)
        dynamic_parts = []
        if vibe_block:
            dynamic_parts.append(vibe_block.strip())
        if full_context:
            dynamic_parts.append(full_context.strip())
        if vision_rule:
            dynamic_parts.append(vision_rule.strip())
        if memory_block:
            dynamic_parts.append(memory_block)
        if author_note:
            dynamic_parts.append(author_note.strip())

        dynamic_suffix = "\n\n".join(dynamic_parts)
        sys_prompt = f"{static_prefix}\n\n{dynamic_suffix}".strip() if dynamic_suffix else static_prefix

        obs_text = ""
        if observations:
            if self._mode == "REACT":
                obs_lines = [f"- 【{name}】：{text}" for name, text in observations]
                obs_text = "【系統資訊 - 工具已完成之動作結果】：\n" + "\n".join(obs_lines) + "\n\n請結合上述工具動作結果與妳的性格與口吻回應對方。"
            else:
                obs_lines = [f"- 工具 [{name}]：{res}" for name, res in observations]
                obs_text = (
                    "【工具執行記錄與數據回傳】\n"
                    + "\n".join(obs_lines)
                    + "\n\n【第二階段合成指令】：上述工具已於前一步驟完整執行完畢，請依據上述真實數據並以妳的人設口吻直接回覆對方（嚴禁再次輸出任何 <action> 標籤）。"
                )

        user_prompt = build_user_turn_prompt(
            display_name=display_name,
            user_query=clean_query,
            visual_context=active_vis,
            observations_text=obs_text,
        )

        if session and session.is_enabled:
            session.prepare_synthesis_pass()
            stream_cb = session.on_stream_chunk
        else:
            stream_cb = None

        vision_model_setting = str(getattr(settings, "VISION_MODEL", "") or "").strip()
        active_model = vision_model_setting if (images and vision_model_setting) else settings.PRIMARY_MODEL

        raw_response = await self._call_llm(
            brain,
            system_prompt=sys_prompt,
            user_prompt=user_prompt,
            task_id=f"{self._mode.lower()}-final-{int(time.time() * 1000)}",
            model=active_model,
            temperature=getattr(settings, "LLM_TEMPERATURE", 0.85),
            timeout=float(settings.OLLAMA_TIMEOUT),
            images=images,
            stream_callback=stream_cb,
            user_id=user_id,
        )

        persona_thought = _extract_xml_tag(raw_response, "thought") or ""
        extracted = _extract_xml_tag(raw_response, "final_answer") or raw_response
        clean_speech = re.sub(r"(?is)<(?:thought|think|action)[^>]*>.*?</(?:thought|think|action)>", "", extracted).strip()
        clean_speech = re.sub(r"<\/?(?:thought|think|final_answer|action)[^>]*>", "", clean_speech).strip()

        if not clean_speech or len(clean_speech.strip()) < 2:
            clean_speech = draft_answer or "已經為你處理完畢囉！"

        return clean_speech, persona_thought, sys_prompt, user_prompt

    async def _run_integrated(
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
        """INTEGRATED 模式委派。"""
        await self._strategies["INTEGRATED"].execute(
            brain, bot, message, user_query, vibe_snippet, full_context, images, start_time, visual_context=visual_context
        )

    # ------------------------------------------------------------------
    # 統一主執行入口
    # ------------------------------------------------------------------

    async def execute(  # type: ignore[override]
        self,
        bot: commands.Bot,
        message: discord.Message,
        user_query: str,
        vibe_snippet: str,
        full_context: str,
        silent: bool = False,
        images: list[str] | None = None,
        visual_context: str = "",
        **kwargs: Any,
    ) -> ToolResult | str | None:
        """根據 self._mode 委派對應策略執行。"""
        start_time = time.time()
        brain_cog = bot.get_cog("BotBrain")
        if not brain_cog:
            logger.error("DispatchAgent[%s]: 未找到 BotBrain Cog，無法執行推理", self._mode)
            chat_tool = self._tools.get("CHAT")
            if chat_tool:
                await chat_tool.execute(
                    bot, message, user_query, vibe_snippet, full_context, images=images, visual_context=visual_context
                )
            return None

        brain: BotBrain = brain_cog  # type: ignore[assignment]

        strategy = self.current_strategy
        await strategy.execute(
            brain=brain,
            bot=bot,
            message=message,
            user_query=user_query,
            vibe_snippet=vibe_snippet,
            full_context=full_context,
            images=images,
            start_time=start_time,
            visual_context=visual_context,
        )
        return None


class IntegratedAgentTool(DispatchAgentTool):
    """單階段整合式自主調度 Agent（固定 INTEGRATED 模式）。"""

    def __init__(self, tool_registry: list[BaseTool]) -> None:
        super().__init__(tool_registry, mode="INTEGRATED")


class ParallelAgentTool(DispatchAgentTool):
    """雙階段並行調度 Agent（固定 PARALLEL 模式）。"""

    def __init__(self, tool_registry: list[BaseTool]) -> None:
        super().__init__(tool_registry, mode="PARALLEL")


class ReactAgentTool(DispatchAgentTool):
    """多步驟 ReAct 調度 Agent（固定 REACT 模式）。"""

    def __init__(self, tool_registry: list[BaseTool]) -> None:
        super().__init__(tool_registry, mode="REACT")
