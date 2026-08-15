"""
handlers/social/react_agent.py
================================
ReAct（Reasoning and Acting）多步驟 Agent 核心。

流程：
  1. 接收 @Tag 訊息
  2. 組裝包含人格、工具描述、上下文的系統提示
  3. 進入迭代迴圈（最多 REACT_MAX_STEPS 步）：
     a. 呼叫 LLM → 解析 <action>/<action_input>/<final_answer>
     b. 若為工具動作 → 呼叫工具，取得 Observation
     c. 將 Thought + Action + Observation 追加進對話歷史
  4. 輸出 <final_answer> 或超過步數限制時強制 ChatTool 生成回覆
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

import settings
from handlers.bot_brain import BotBrain, InferenceTask
from handlers.social.persona import build_dynamic_persona
from handlers.social.memory_builder import build_user_memory_block
from handlers.social.utils import simulate_typing_and_send, remove_hourglass_reaction
from handlers.social.tool_base import BaseTool
from handlers.social.observability import log_event

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Agent Prompt 模板（ReAct 格式 - 專為 2B/7B 輕量模型極致優化）
# ---------------------------------------------------------------------------

_REACT_SYSTEM_TEMPLATE = """[SYSTEM: TASK PLANNING & TOOL DISPATCH ENGINE]
你是一個冷靜、精準的任務規劃與工具調度引擎。你的任務是分析使用者的需求並調度合適的工具。
⚠️ 嚴格鐵則：
1. 絕對禁止輸出任何問候、角色扮演、聊天對話或道德說教。
2. 每次回覆只能輸出指定的 XML 格式，不得輸出多餘文字。

## 可用工具清單（Tools）
{tool_descriptions}

## 輸出格式（每次回覆必須嚴格輸出以下其一，禁止混合）：

### 格式一：需要發動工具
<thought>簡要分析需求與下一步行動</thought>
<action>TOOL_NAME</action>
<action_input>給工具的指令或關鍵字</action_input>

### 格式二：任務已全數完成 / 準備回覆
<thought>分析確認任務已全數完成或無須調用外部工具</thought>
<final_answer>準備完成</final_answer>

## 示範案例 (Few-Shot Examples)

案例 1（多任務組合：搜圖 + 定時提醒）：
使用者說：「幫我找一張雪莉的圖，然後 10 分鐘後提醒我出門」
<thought>使用者有兩個獨立需求：[1] 搜圖 (PIXIV), [2] 設定定時提醒 (SCHEDULE)。先執行任務 1。</thought>
<action>PIXIV</action>
<action_input>橘雪莉</action_input>

案例 2（單一媒體與操作：繪圖）：
使用者說：「幫我畫一隻戴著偵探帽的小貓」
<thought>使用者要求 AI 繪圖生成圖片，直接調用 DRAW 工具。</thought>
<action>DRAW</action>
<action_input>一隻戴著偵探帽的小貓</action_input>

案例 3（股市或行情查詢）：
使用者說：「查一下 2330 走勢如何」
<thought>使用者詢問股票技術走勢與行情，發動 STOCK 工具。</thought>
<action>STOCK</action>
<action_input>2330.TW</action_input>

案例 4（純日常對話 / 打招呼）：
使用者說：「今天天氣真好，妳在做什麼呢？」
<thought>此為一般日常閒聊，無須調度任何查詢或操作工具，直接發動 CHAT。</thought>
<action>CHAT</action>
<action_input>日常對話</action_input>
"""

_TOOL_ALIASES: dict[str, str] = {
    "PIXIV_SEARCH": "PIXIV",
    "PIXIV_TOOL": "PIXIV",
    "FIND_IMAGE": "PIXIV",
    "SEARCH_IMAGE": "PIXIV",
    "GET_IMAGE": "PIXIV",
    "GET_PIC": "PIXIV",
    "PLAY_MUSIC": "MUSIC",
    "MUSIC_PLAYER": "MUSIC",
    "PLAY_SONG": "MUSIC",
    "SONG": "MUSIC",
    "SET_ALARM": "SCHEDULE",
    "SET_REMINDER": "SCHEDULE",
    "ALARM": "SCHEDULE",
    "REMINDER": "SCHEDULE",
    "SCHEDULE_REMINDER": "SCHEDULE",
    "STOCK_ANALYSIS": "STOCK",
    "STOCK_LOOKUP": "STOCK",
    "STOCK_QUOTE": "STOCK",
    "DRAW_IMAGE": "DRAW",
    "GENERATE_IMAGE": "DRAW",
    "CREATE_IMAGE": "DRAW",
    "SEARCH_WEB": "SEARCH",
    "WEB_SEARCH": "SEARCH",
    "GOOGLE_SEARCH": "SEARCH",
}

_TOOL_DESC_TEMPLATE = "- **{name}**：{description}"

_USER_MESSAGE_TEMPLATE = "使用者（{author}）說：「{content}」"

_OBSERVATION_TEMPLATE = """<observation>
工具 [{tool}] 執行完畢，結果如下：
{result}
</observation>"""

_REACT_CONTINUATION_TEMPLATE = """以下是目前的對話歷史與工具執行記錄。請根據這些資訊繼續推理，決定下一步或給出最終答案：

{transcript}

現在輪到你回覆："""


# ---------------------------------------------------------------------------
# 解析輔助
# ---------------------------------------------------------------------------

def _extract_xml_tag(text: str, tag: str) -> str | None:
    """從文字中提取 <tag>content</tag>。"""
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None


def _parse_react_output(raw: str) -> dict[str, str]:
    """解析 LLM 的 ReAct 格式輸出（支援 XML 標籤與標準文字 fallback）。

    Returns:
        dict with keys: thought (optional), action/action_input OR final_answer.
        'type' key is 'action' | 'final'
    """
    # 移除外層 ```xml 或 ``` 程式碼區塊包裹
    cleaned_raw = re.sub(r"^```(?:xml|json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned_raw = re.sub(r"\s*```$", "", cleaned_raw.strip())

    thought = _extract_xml_tag(cleaned_raw, "thought") or ""
    final_answer = _extract_xml_tag(cleaned_raw, "final_answer")
    action = _extract_xml_tag(cleaned_raw, "action")
    action_input = _extract_xml_tag(cleaned_raw, "action_input") or ""

    # XML 標籤解析成功的路徑
    if final_answer is not None:
        return {"type": "final", "thought": thought, "final_answer": final_answer}
    if action:
        return {"type": "action", "thought": thought, "action": action.strip().upper(), "action_input": action_input}

    # 文字型備援解析（Thought: / Action: / Final Answer:）
    thought_m = re.search(r"Thought:\s*(.*?)(?=Action:|Final Answer:|$)", cleaned_raw, re.DOTALL | re.IGNORECASE)
    action_m = re.search(r"Action:\s*([A-Za-z_]+)", cleaned_raw, re.IGNORECASE)
    action_in_m = re.search(r"Action Input:\s*(.*?)(?=Thought:|Action:|$)", cleaned_raw, re.DOTALL | re.IGNORECASE)
    final_m = re.search(r"Final Answer:\s*(.*)", cleaned_raw, re.DOTALL | re.IGNORECASE)

    if thought_m and not thought:
        thought = thought_m.group(1).strip()

    if final_m:
        return {"type": "final", "thought": thought, "final_answer": final_m.group(1).strip()}

    if action_m:
        act_name = action_m.group(1).strip().upper()
        act_in = action_in_m.group(1).strip() if action_in_m else ""
        return {"type": "action", "thought": thought, "action": act_name, "action_input": act_in}

    # 無任何標籤或結構 → 全段視為最終回覆
    return {"type": "final", "thought": thought, "final_answer": cleaned_raw.strip()}


# ---------------------------------------------------------------------------
# ReactAgentTool
# ---------------------------------------------------------------------------

class ReactAgentTool(BaseTool):
    """ReAct 多步驟 Agent，作為 @Tag 訊息的統一入口。

    本工具本身不直接呼叫外部 API，而是協調其他工具（ImageSearchTool、
    DrawTool、MusicTool 等）以完成需要多個步驟的任務。
    """

    def __init__(self, tool_registry: list[BaseTool]) -> None:
        """初始化 Agent。

        Args:
            tool_registry: 所有可用工具的列表（排除 ReactAgentTool 本身）。
        """
        self._tools: dict[str, BaseTool] = {
            t.intent_name.upper(): t for t in tool_registry
        }
        logger.info(
            "ReactAgentTool 初始化完成，已載入工具：%s",
            list(self._tools.keys()),
        )

    @property
    def intent_name(self) -> str:
        return "REACT_AGENT"

    @property
    def llm_description(self) -> str:
        return "ReAct 多步驟 Agent（不應由 LLM 分類器選擇，由 router 直接路由）"

    def can_handle_by_regex(self, text: str) -> bool:
        return False  # Agent 由 router 直接呼叫，不走正則

    def _build_tool_descriptions(self) -> str:
        """組裝工具描述清單（供 Agent Prompt 使用）。"""
        lines: list[str] = []
        for name, tool in self._tools.items():
            # 排除不暴露給 Agent 的工具
            if name in ("REACT_AGENT", "CHAT"):
                continue
            lines.append(_TOOL_DESC_TEMPLATE.format(
                name=name,
                description=tool.llm_description,
            ))
        # CHAT 是特殊保留工具，代表「直接回話」
        lines.append("- **CHAT**：一般聊天回覆或問候，不需要呼叫外部工具時使用")
        return "\n".join(lines)

    def _build_system_prompt(self) -> str:
        """組裝極簡的 ReAct Agent 系統提示（零人設干擾、專注邏輯調度）。"""
        tool_descriptions = self._build_tool_descriptions()
        return _REACT_SYSTEM_TEMPLATE.format(
            tool_descriptions=tool_descriptions,
        )

    async def _call_llm(
        self,
        brain: BotBrain,
        system_prompt: str,
        user_prompt: str,
        message_id: int,
        step: int,
        model: str | None = None,
        temperature: float | None = None,
    ) -> str:
        """向 BotBrain 發送推理任務，等待並回傳結果。

        Args:
            brain: BotBrain Cog 實例。
            system_prompt: Agent 系統提示。
            user_prompt: 使用者提示（含對話歷史）。
            message_id: 來源訊息 ID（用於 task_id）。
            step: 當前步驟編號。
            model: 指定使用之 LLM 模型名稱，未指定則使用 settings.REACT_MODEL。
            temperature: 採樣溫度，ReAct 規劃階段預設 0.1。

        Returns:
            LLM 生成的文字。
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()

        async def _on_reply(reply: str) -> None:
            if not future.done():
                future.set_result(reply)

        target_model = model or settings.REACT_MODEL
        target_temp = 0.1 if temperature is None else temperature

        task = InferenceTask(
            priority=settings.PRIORITY_TAG_RESPONSE,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            callback=_on_reply,
            model=target_model,
            task_id=f"react-step{step}-{message_id}",
            temperature=target_temp,
        )
        await brain.enqueue_task(task)

        try:
            return await asyncio.wait_for(future, timeout=settings.REACT_STEP_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("ReactAgent step %d (model=%s) 超時 (%.1fs)", step, target_model, settings.REACT_STEP_TIMEOUT)
            return ""

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
    ) -> tuple[str, str, str, str]:
        """使用 PRIMARY_MODEL（主力模型）合成最終給使用者的對話文本。

        完整包含前文脈絡 (full_context)、頻道話題氛圍 (vibe_snippet)、
        長期記憶 (memory_block) 與 15 歲台灣女高中生對話規則 (length_rule)。
        回傳 (clean_speech, persona_thought, sys_prompt, user_prompt)。
        """
        recent_text = (full_context[-1500:] + "\n" + user_query) if full_context else user_query

        head_persona, tail_lore = build_dynamic_persona(recent_text, "CHAT", channel_id=channel_id)
        memory_block = build_user_memory_block(bot, user_id, display_name)

        vibe_block = (
            "【頻道近期話題氛圍】(⚠️這只是讓妳用來『讀空氣』的背景氣氛！請不要像鸚鵡一樣把這些詞彙硬念出來，除非對方現在的話語剛好有關聯！)\n"
            f"{vibe_snippet}\n\n"
        ) if vibe_snippet else ""

        author_note = f"\n{tail_lore}\n" if tail_lore else ""

        length_rule = (
            "2. 【豐富生動的對話】：請像個話多、熱情又愛互動的 15 歲台灣女高中生一樣聊天！"
            "可以講 2～4 句話（不需刻意簡短），內容要有情緒起伏、表達想法與可愛的互動，但不要變成嚴肅說教或論文長篇大論。"
        )

        cot_instruction = ""
        if getattr(settings, "MICRO_COT_ENABLED", True):
            cot_instruction = "0. 🧠 【深度思考機制】：在給出最終回覆前，妳「必須」先用 <thought>...</thought> 標籤包覆妳的內心想法、局勢分析與回覆策略。思考完後，再輸出對外說的話。\n"

        sys_prompt = (
            f"{head_persona}\n\n"
            + (f"{memory_block}\n\n" if memory_block else "")
            + f"{vibe_block}"
            + (f"{full_context}\n" if full_context else "")
            + f"{author_note}"
            "【回覆規則】\n"
            f"{cot_instruction}"
            "1. 🎯 將 100% 注意力集中在『當下必須回應的目標』，給出最直接的回覆。\n"
            f"{length_rule}\n"
            "3. 不知道的事請直說，不要臆造資訊。"
        )

        if observations:
            obs_lines = [f"- 【{name}】：{text}" for name, text in observations]
            obs_context = "\n".join(obs_lines)
            user_prompt = (
                f"使用者（{display_name}）對妳說：「{user_query}」\n\n"
                f"【系統資訊 - 工具已完成之動作結果】：\n{obs_context}\n\n"
                f"請結合上述工具動作結果與妳名偵探的性格與口吻回應對方。告知操作結果已順利完成，請直接回應對話！"
            )
        else:
            user_prompt = f"使用者（{display_name}）對妳說：「{user_query}」"

        logger.info("🧠 [ReAct -> 主力模型] 交由 PRIMARY_MODEL (%s) 生成最終對話文本...", settings.PRIMARY_MODEL)
        reply = await self._call_llm(
            brain,
            system_prompt=sys_prompt,
            user_prompt=user_prompt,
            message_id=id(user_query),
            step=99,
            model=settings.PRIMARY_MODEL,
            temperature=getattr(settings, "LLM_TEMPERATURE", 0.85),
        )

        persona_thought = _extract_xml_tag(reply, "thought") or ""
        extracted = _extract_xml_tag(reply, "final_answer") or reply
        clean_speech = re.sub(r'(?is)<thought>.*?</thought>', '', extracted).strip()
        clean_speech = re.sub(r'<\/?(?:thought|final_answer)>', '', clean_speech).strip()

        if not clean_speech or len(clean_speech.strip()) < 2:
            clean_speech = draft_answer or "哼哼～名偵探已經為你處理完畢囉！"

        return clean_speech, persona_thought, sys_prompt, user_prompt

    async def _execute_tool(
        self,
        tool_name: str,
        action_input: str,
        bot: commands.Bot,
        message: discord.Message,
        vibe_snippet: str,
        full_context: str,
    ) -> str:
        """呼叫指定工具，以 silent=True 模式執行並收集結果。

        Args:
            tool_name: 工具名稱（大寫）。
            action_input: 給工具的指令文字。
            bot: Bot 實例。
            message: 觸發訊息。
            vibe_snippet: 氛圍摘要。
            full_context: 完整上下文。

        Returns:
            工具執行的觀察結果字串。
        """
        canonical_name = _TOOL_ALIASES.get(tool_name.upper(), tool_name.upper())
        tool = self._tools.get(canonical_name)
        if not tool:
            return f"找不到工具 {tool_name}，請確認工具名稱正確。"

        try:
            result = await tool.execute(
                bot, message, action_input, vibe_snippet, full_context,
                silent=True,
            )
            if result is None:
                return f"工具 {canonical_name} 執行完畢（無文字回傳）。"
            return str(result)[:settings.REACT_MAX_OBSERVATION_LENGTH]
        except Exception as exc:
            logger.error("ReactAgent 工具 %s (canonical=%s) 執行失敗: %s", tool_name, canonical_name, exc)
            return f"工具 {canonical_name} 執行時發生錯誤：{exc}"

    async def execute(
        self,
        bot: commands.Bot,
        message: discord.Message,
        user_query: str,
        vibe_snippet: str,
        full_context: str,
        **kwargs: object,
    ) -> None:
        """ReAct Agent 主迴圈。

        Args:
            bot: Discord Bot 實例。
            message: 觸發的 Discord 訊息。
            user_query: 使用者的原始文字。
            vibe_snippet: 頻道近期氛圍摘要。
            full_context: 含上下文的完整提示文本。
        """
        import time
        start_time = time.time()

        brain: BotBrain | None = bot.get_cog("BotBrain")
        if not brain:
            logger.error("ReactAgent: BotBrain 未就緒")
            return

        channel_id = message.channel.id
        user_id    = message.author.id
        display_name = message.author.display_name

        # ── 組裝極簡 System Prompt（專注規劃、無人設負擔）─────────────────
        system_prompt = self._build_system_prompt()

        # ── 對話歷史、重複呼叫攔截與工具執行結果記錄 ────────────────────────
        transcript_parts: list[str] = []
        executed_observations: list[tuple[str, str]] = []
        called_tool_actions: set[tuple[str, str]] = set()
        step_thoughts: list[str] = []

        initial_user_msg = _USER_MESSAGE_TEMPLATE.format(
            author=display_name, content=user_query
        )

        final_answer: str | None = None

        # ── 階段一：ReAct 規劃與工具執行迴圈 (REACT_MODEL) ────────────────
        for step in range(1, settings.REACT_MAX_STEPS + 1):
            logger.info("ReactAgent: Step %d / %d", step, settings.REACT_MAX_STEPS)

            # 組裝本輪的 user_prompt
            if step == 1:
                user_prompt = initial_user_msg
            else:
                transcript_str = "\n\n".join(transcript_parts)
                # 防止 transcript 爆長
                if len(transcript_str) > settings.REACT_MAX_TRANSCRIPT_CHARS:
                    transcript_str = "...(歷史已截斷)...\n\n" + transcript_str[-settings.REACT_MAX_TRANSCRIPT_CHARS:]
                user_prompt = _REACT_CONTINUATION_TEMPLATE.format(
                    transcript=transcript_str
                )

            # 呼叫 LLM
            raw_output = await self._call_llm(
                brain, system_prompt, user_prompt, message.id, step
            )

            if not raw_output:
                logger.warning("ReactAgent: Step %d LLM 回傳空白，強制結束", step)
                break

            parsed = _parse_react_output(raw_output)
            thought_text = parsed.get("thought", "").strip()

            if thought_text:
                step_thoughts.append(f"[Step {step} 規劃] {thought_text}")
                logger.info("🧠 [ReAct Step %d/%d 思考]: %s", step, settings.REACT_MAX_STEPS, thought_text)

            # ── Final Answer ────────────────────────────────────────────
            if parsed["type"] == "final":
                final_answer = parsed.get("final_answer", "").strip()
                logger.info("💬 [ReAct Step %d/%d 規劃完成] 工具發動完畢，準備交由主力模型對話", step, settings.REACT_MAX_STEPS)
                break

            # ── Tool Action ─────────────────────────────────────────────
            if parsed["type"] == "action":
                raw_tool_name = parsed.get("action", "").upper()
                action_input  = parsed.get("action_input", user_query).strip()
                tool_name     = _TOOL_ALIASES.get(raw_tool_name, raw_tool_name)

                logger.info(
                    "⚡ [ReAct Step %d/%d 行動]: 呼叫工具 [%s] (原: %s) | 指令: '%s'",
                    step, settings.REACT_MAX_STEPS, tool_name, raw_tool_name, action_input
                )

                if thought_text:
                    transcript_parts.append(
                        f"<thought>\n{thought_text}\n</thought>"
                    )
                transcript_parts.append(
                    f"<action>{tool_name}</action>\n"
                    f"<action_input>{action_input}</action_input>"
                )

                # CHAT 是偽工具，直接以 LLM 回覆（不用 silent 執行）
                if tool_name == "CHAT":
                    logger.info("💬 [ReAct Step %d/%d]: 選擇 CHAT 工具，交由主力模型進行生成", step, settings.REACT_MAX_STEPS)
                    transcript_parts.append(
                        "<observation>CHAT 工具已觸發，直接交由主力模型回覆。</observation>"
                    )
                    break

                # 🛡️ 重複指令攔截防護 (Deduplication)
                tool_key = (tool_name, action_input)
                if tool_key in called_tool_actions:
                    logger.warning("ReactAgent: 攔截到重複呼叫指令 %s(input='%s')", tool_name, action_input)
                    observation = (
                        f"⚠️ 系統提示：你已經對 [{tool_name}] 執行過相同的指令 [{action_input}] 並已取得結果。"
                        f"請勿重複呼叫！請推進下一個任務或完成回覆。"
                    )
                    obs_str = _OBSERVATION_TEMPLATE.format(tool=tool_name, result=observation)
                    transcript_parts.append(obs_str)
                    continue

                called_tool_actions.add(tool_key)

                # 執行真實工具
                observation = await self._execute_tool(
                    tool_name, action_input, bot, message,
                    vibe_snippet, full_context,
                )
                obs_str = _OBSERVATION_TEMPLATE.format(
                    tool=tool_name, result=observation
                )
                transcript_parts.append(obs_str)
                executed_observations.append((tool_name, observation))

                logger.info(
                    "👁️ [ReAct Step %d/%d 觀察]: 工具 [%s] 執行結果: %s",
                    step, settings.REACT_MAX_STEPS, tool_name, observation[:150].replace('\n', ' ')
                )

        # ── 階段二：Smart Termination 判定與對話生成 ───────────────────────
        has_chat_request = bool(re.search(r"(並|而且|順便|跟我說|告訴我|分享|覺得|看法|心得|對話|聊天|喜不喜歡|喜愛)", user_query))

        # 僅當發動的工具是媒體/直發工具（PIXIV, DRAW, MUSIC），且該工具已直接在 Discord 發布 Embed/圖文/語音，
        # 且使用者無聊天/心得需求時，才不額外發送第 2 則純文字閒聊。
        if len(executed_observations) == 1 and not has_chat_request:
            only_tool, _ = executed_observations[0]
            if only_tool in ("PIXIV", "DRAW", "MUSIC"):
                await remove_hourglass_reaction(message, bot.user)
                logger.info("💬 [ReAct Smart Termination]: 媒體工具 [%s] 已直接發布圖文至 Discord，不輸出多餘閒聊", only_tool)
                elapsed_ms = int((time.time() - start_time) * 1000)
                thought_sections = []
                if step_thoughts:
                    thought_sections.append("【ReAct 任務調度思考】\n" + "\n".join(step_thoughts))
                full_cot_thought = "\n\n".join(thought_sections)
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
                    model_used=settings.REACT_MODEL,
                    latency_ms=elapsed_ms,
                    tools_invoked=[only_tool],
                )
                return

        final_speech, persona_thought, final_sys_prompt, final_user_prompt = await self._generate_final_response(
            brain=brain,
            user_query=user_query,
            channel_id=channel_id,
            bot=bot,
            user_id=user_id,
            display_name=display_name,
            observations=executed_observations,
            vibe_snippet=vibe_snippet,
            full_context=full_context,
            draft_answer=final_answer or "",
        )

        elapsed_ms = int((time.time() - start_time) * 1000)

        # 組合完整的思考鏈（ReAct 規劃思考 + 雪莉角色內心思考）
        thought_sections = []
        if step_thoughts:
            thought_sections.append("【ReAct 任務調度思考】\n" + "\n".join(step_thoughts))
        if persona_thought:
            thought_sections.append("【雪莉角色內心思考】\n" + persona_thought)
        full_cot_thought = "\n\n".join(thought_sections)

        if final_speech:
            clean_answer = re.sub(r'\*.*?\*', '', final_speech).strip()
            clean_answer = re.sub(r'\n{3,}', '\n\n', clean_answer).strip()

            if clean_answer:
                logger.info("💬 [PRIMARY_MODEL 主力模型對話輸出]: %s", clean_answer[:200].replace('\n', ' '))
                await simulate_typing_and_send(
                    message,
                    clean_answer,
                    intent_name="REACT",
                    prompt=user_query,
                    model=f"{settings.REACT_MODEL} ➔ {settings.PRIMARY_MODEL}",
                    system_prompt=final_sys_prompt,
                    user_prompt=final_user_prompt,
                    latency_ms=elapsed_ms,
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
                    model_used=f"{settings.REACT_MODEL} ➔ {settings.PRIMARY_MODEL}",
                    latency_ms=elapsed_ms,
                    tools_invoked=[t[0] for t in executed_observations],
                )
                return

        # ── Fallback：若主力模型無輸出，改用 ChatTool 兜底 ──────────────────
        logger.warning("ReactAgent: 主力模型對話無輸出，改用 ChatTool 兜底")
        chat_tool = self._tools.get("CHAT")
        if chat_tool:
            await chat_tool.execute(
                bot, message, user_query, vibe_snippet, full_context
            )
