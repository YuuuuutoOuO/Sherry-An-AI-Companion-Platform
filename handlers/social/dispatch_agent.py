"""
handlers/social/dispatch_agent.py
===================================
統一調度 Agent（原 parallel_agent + react_agent 合併版）。

支援兩種執行模式，由 settings.AGENT_DISPATCH_MODE 控制：

PARALLEL 模式（預設）：
  - 單次推論（Single-Pass Inference）呼叫 PLANNER_MODEL，輸出結構化任務 JSON。
  - asyncio.gather 非同步並行執行所有無依賴工具。
  - 行為自適應終止（Action Early Termination）：
    若所有工具皆為自帶 UI 渲染（DIRECT_UI）且無對話需求，直接秒級結束，不發廢話。
  - 若包含純資料工具（DATA_ONLY）或使用者有閒聊需求，由 PRIMARY_MODEL 合成最終發言。

REACT 模式：
  - 多步驟推論迴圈（最多 REACT_MAX_STEPS 步）。
  - 每步：呼叫 LLM -> 解析 action/action_input/final_answer。
  - 發動工具 -> 取得 Observation -> 追加進對話歷史 -> 繼續推理。
  - Smart Termination：純媒體工具且無閒聊需求時不額外發文。
  - 超過步數限制時交由 PRIMARY_MODEL 生成回覆。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import discord
from discord.ext import commands

import settings
from handlers.bot_brain import BotBrain, InferenceTask
from handlers.social.contracts import ToolCategory, ToolResult
from handlers.social.memory_builder import build_user_memory_block
from handlers.social.observability import log_event, log_tool_exec, make_timer
from handlers.social.persona import build_dynamic_persona
from handlers.social.tool_base import BaseTool, ToolResponseMode
from handlers.social.utils import remove_hourglass_reaction, simulate_typing_and_send
from handlers.social.streaming_thinking import LiveThinkingSession

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 共用常數
# ---------------------------------------------------------------------------

_TOOL_DESC_TEMPLATE = "- **{name}**：{description}"
_USER_MESSAGE_TEMPLATE = "使用者（{author}）說：「{content}」"

#: 兩種模式共用的工具別名映射表
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
    "WEATHER_LOOKUP": "WEATHER",
    "WEATHER_REPORT": "WEATHER",
}

# ---------------------------------------------------------------------------
# PARALLEL 模式專用資料結構
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlannedTask:
    """規劃器輸出的單一工具任務。"""

    tool: str
    input: str


@dataclass(frozen=True)
class ExecutionPlan:
    """規劃器輸出的完整執行計畫。"""

    thought: str
    tasks: list[PlannedTask] = field(default_factory=list)
    needs_persona_reply: bool = True


# ---------------------------------------------------------------------------
# INTEGRATED 模式專用：Prompt 與 Action 標籤解析
# ---------------------------------------------------------------------------

_INTEGRATED_TOOL_TEMPLATE = """
【外部工具調用特權】
若對方的問題需要即時情報、多媒體操作或外部功能（如查股市、放歌、搜圖、畫圖、查天氣、定時提醒、推薦美食/時事），妳可以在回覆最開頭使用 `<action name="TOOL_NAME">參數</action>` 標籤。
若只是日常問候、情感交流、閒聊或不需要外部工具的問題，請直接進行性格回覆，嚴禁輸出任何 <action> 標籤！

可用工具清單：
{tool_descriptions}

調用格式範例：
- 搜尋美食/時事/推薦：<action name="SEARCH">台北 晚餐推薦</action>
- 查詢股票/幣價行情：<action name="STOCK">2330.TW</action>
- 點播音樂/放歌：<action name="MUSIC">周杰倫 晴天</action>
- 搜尋插畫/美圖：<action name="PIXIV">橘雪莉</action>
- 繪製自訂圖片：<action name="DRAW">一隻戴著偵探帽的小白貓</action>
- 查詢即時天氣：<action name="WEATHER">台北市</action>
- 設定定時提醒：<action name="SCHEDULE">10分鐘後 提醒我出門</action>
- 網址重點解析：<action name="SUMMARY">https://example.com</action>
"""


def _parse_actions(text: str) -> list[PlannedTask]:
    """從 LLM 生成文字中解析 <action name="TOOL">input</action> 或相關變體。

    Args:
        text: LLM 原始生成文字。

    Returns:
        list[PlannedTask]: 解析出的工具任務列表。
    """
    tasks: list[PlannedTask] = []
    # 模式 1: <action name="TOOL">input</action> 或 <action tool="TOOL">input</action>
    pattern1 = re.compile(
        r'<action\s+(?:name|tool)=["\']([A-Za-z0-9_]+)["\']\s*>(.*?)</action>',
        re.DOTALL | re.IGNORECASE,
    )
    for m in pattern1.finditer(text):
        tool = m.group(1).strip().upper()
        inp = m.group(2).strip()
        tasks.append(PlannedTask(tool=tool, input=inp))

    # 模式 2: <action>TOOL: input</action>
    if not tasks:
        pattern2 = re.compile(
            r"<action>\s*([A-Za-z0-9_]+)\s*[:：]\s*(.*?)</action>",
            re.DOTALL | re.IGNORECASE,
        )
        for m in pattern2.finditer(text):
            tool = m.group(1).strip().upper()
            inp = m.group(2).strip()
            tasks.append(PlannedTask(tool=tool, input=inp))

    # 模式 3: <action>TOOL(input)</action>
    if not tasks:
        pattern3 = re.compile(
            r"<action>\s*([A-Za-z0-9_]+)\s*\((.*?)\)\s*</action>",
            re.DOTALL | re.IGNORECASE,
        )
        for m in pattern3.finditer(text):
            tool = m.group(1).strip().upper()
            inp = m.group(2).strip()
            tasks.append(PlannedTask(tool=tool, input=inp))

    return tasks


# ---------------------------------------------------------------------------
# PARALLEL 模式專用：Prompt 與 JSON 解析
# ---------------------------------------------------------------------------

_PARALLEL_PLANNER_SYSTEM_TEMPLATE = """[SYSTEM: PARALLEL TASK DISPATCH & CHAT SUPPRESSION ENGINE]
你是一個高效率、極致冷靜的任務排程與對話抑制引擎。你的任務是分析使用者的輸入，拆解為待執行的工具清單，並精準判斷是否需要後續的性格對話回覆。

嚴格鐵則：
1. 絕對禁止輸出任何問候、角色扮演、聊天對話或道德說教。
2. 輸出格式只能是符合 JSON Schema 的純 JSON 物件，嚴禁附加 Markdown 解釋文字。
3. 支援多工並行拆解：若使用者同時要求多個動作，必須在 tasks 陣列中列出所有工具。

## 可用工具清單（Tools）
{tool_descriptions}

## 輸出 JSON 格式定義
{{
  "thought": "簡要分析使用者意圖與任務拆解思考",
  "tasks": [
    {{"tool": "TOOL_NAME", "input": "給該工具的精確關鍵字或參數"}}
  ],
  "needs_persona_reply": true/false
}}

【needs_persona_reply 判斷準則】：
- 設為 false：所有發動的工具全為自帶UI/媒體播放工具（如 MUSIC, PIXIV, DRAW），且使用者沒有提出心得詢問或聊天要求。
- 設為 true：1.發動了純資料查詢工具（如STOCK,SEARCH,WEATHER）2.使用者明確表達聊天需求 3.純日常閒聊（tasks為空）

## 示範案例

案例 1：
使用者：「請幫我播放莫札特的歌與巴哈的歌」
{{"thought": "兩首音樂各自獨立任務。", "tasks": [{{"tool": "MUSIC", "input": "莫札特"}}, {{"tool": "MUSIC", "input": "巴哈"}}], "needs_persona_reply": false}}

案例 2：
使用者：「找一張雪莉的圖片」
{{"thought": "搜尋圖片任務。", "tasks": [{{"tool": "PIXIV", "input": "橘雪莉"}}], "needs_persona_reply": false}}

案例 3：
使用者：「幫我放首周杰倫的晴天，順便跟我說妳喜不喜歡這首歌」
{{"thought": "播放音樂並詢問喜好。", "tasks": [{{"tool": "MUSIC", "input": "周杰倫 晴天"}}], "needs_persona_reply": true}}

案例 4：
使用者：「查一下 2330 和 2454 的走勢」
{{"thought": "兩檔股票查詢，需轉述。", "tasks": [{{"tool": "STOCK", "input": "2330.TW"}}, {{"tool": "STOCK", "input": "2454.TW"}}], "needs_persona_reply": true}}

案例 5：
使用者：「今天天氣真好，妳在做什麼呢？」
{{"thought": "日常閒聊，無需工具。", "tasks": [], "needs_persona_reply": true}}
"""


def _parse_plan_json(raw: str, default_query: str) -> ExecutionPlan:
    """從 LLM 原始輸出中解析 ExecutionPlan，具備多層容錯回退機制。

    Args:
        raw: LLM 原始文字輸出。
        default_query: 解析失敗時的兜底查詢文字。

    Returns:
        ExecutionPlan 實例。
    """
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json|xml)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    json_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(0))
            if isinstance(data, dict):
                thought = str(data.get("thought", "")).strip()
                needs_reply = bool(data.get("needs_persona_reply", True))
                raw_tasks = data.get("tasks", [])
                tasks: list[PlannedTask] = []
                if isinstance(raw_tasks, list):
                    for item in raw_tasks:
                        if isinstance(item, dict):
                            t_name = str(item.get("tool", "")).strip().upper()
                            t_input = str(item.get("input", "")).strip()
                            if t_name:
                                tasks.append(PlannedTask(tool=t_name, input=t_input or default_query))
                return ExecutionPlan(thought=thought, tasks=tasks, needs_persona_reply=needs_reply)
        except Exception as exc:
            logger.warning(
                "DispatchAgent[PARALLEL]: JSON 解析失敗 (%s)，啟動 Regex 容錯: %s", exc, raw[:100]
            )

    tasks_extracted: list[PlannedTask] = []
    task_pattern = re.compile(
        r'["\']tool["\']\s*:\s*["\']([A-Za-z0-9_]+)["\']\s*,\s*["\']input["\']\s*:\s*["\']([^"\']*)["\']',
        re.IGNORECASE,
    )
    for m in task_pattern.finditer(cleaned):
        tasks_extracted.append(
            PlannedTask(tool=m.group(1).strip().upper(), input=m.group(2).strip() or default_query)
        )

    thought_m = re.search(r'["\']thought["\']\s*:\s*["\']([^"\']*)["\']', cleaned)
    extracted_thought = thought_m.group(1).strip() if thought_m else ""
    needs_reply_m = re.search(r'["\']needs_persona_reply["\']\s*:\s*(true|false)', cleaned, re.IGNORECASE)
    extracted_needs_reply = (needs_reply_m.group(1).lower() == "true") if needs_reply_m else True

    if tasks_extracted:
        return ExecutionPlan(
            thought=extracted_thought, tasks=tasks_extracted, needs_persona_reply=extracted_needs_reply
        )
    return ExecutionPlan(thought="未能提取結構化工具任務，回退為純對話", tasks=[], needs_persona_reply=True)


# ---------------------------------------------------------------------------
# REACT 模式專用：Prompt 與 XML 解析
# ---------------------------------------------------------------------------

_REACT_SYSTEM_TEMPLATE = """[SYSTEM: TASK PLANNING & TOOL DISPATCH ENGINE]
你是一個冷靜、精準的任務規劃與工具調度引擎。
嚴格鐵則：
1. 絕對禁止輸出任何問候、角色扮演、聊天對話或道德說教。
2. 每次回覆只能輸出指定的 XML 格式，不得輸出多餘文字。

## 可用工具清單（Tools）
{tool_descriptions}

## 輸出格式

格式一（需要發動工具）：
<thought>簡要分析</thought>
<action>TOOL_NAME</action>
<action_input>給工具的指令</action_input>

格式二（任務完成）：
<thought>確認完成</thought>
<final_answer>準備完成</final_answer>

## 示範案例

案例 1：
使用者：「幫我找一張雪莉的圖，然後 10 分鐘後提醒我出門」
<thought>兩個需求：搜圖 (PIXIV) 與定時提醒 (SCHEDULE)，先執行搜圖。</thought>
<action>PIXIV</action>
<action_input>橘雪莉</action_input>

案例 2：
使用者：「查一下 2330 走勢如何」
<thought>股票技術走勢，發動 STOCK 工具。</thought>
<action>STOCK</action>
<action_input>2330.TW</action_input>

案例 3：
使用者：「今天天氣真好，妳在做什麼呢？」
<thought>日常閒聊，直接發動 CHAT。</thought>
<action>CHAT</action>
<action_input>日常對話</action_input>
"""

_OBSERVATION_TEMPLATE = """<observation>
工具 [{tool}] 執行完畢，結果如下：
{result}
</observation>"""

_REACT_CONTINUATION_TEMPLATE = """以下是目前的對話歷史與工具執行記錄。請根據這些資訊繼續推理：

{transcript}

現在輪到你回覆："""


def _extract_xml_tag(text: str, tag: str) -> str | None:
    """從文字中提取 <tag>content</tag>。

    Args:
        text: 要解析的原始文字。
        tag: XML 標籤名稱。

    Returns:
        標籤內容字串，若未找到則回傳 None。
    """
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else None


def _parse_react_output(raw: str) -> dict[str, str]:
    """解析 LLM 的 ReAct 格式輸出（支援 XML 標籤與標準文字 fallback）。

    Args:
        raw: LLM 原始輸出文字。

    Returns:
        dict，含 type ('action'|'final')、thought、
        及 action+action_input 或 final_answer。
    """
    cleaned = re.sub(r"^```(?:xml|json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())

    thought = _extract_xml_tag(cleaned, "thought") or ""
    final_answer = _extract_xml_tag(cleaned, "final_answer")
    action = _extract_xml_tag(cleaned, "action")
    action_input = _extract_xml_tag(cleaned, "action_input") or ""

    if final_answer is not None:
        return {"type": "final", "thought": thought, "final_answer": final_answer}
    if action:
        return {"type": "action", "thought": thought, "action": action.strip().upper(), "action_input": action_input}

    thought_m = re.search(r"Thought:\s*(.*?)(?=Action:|Final Answer:|$)", cleaned, re.DOTALL | re.IGNORECASE)
    action_m = re.search(r"Action:\s*([A-Za-z_]+)", cleaned, re.IGNORECASE)
    action_in_m = re.search(r"Action Input:\s*(.*?)(?=Thought:|Action:|$)", cleaned, re.DOTALL | re.IGNORECASE)
    final_m = re.search(r"Final Answer:\s*(.*)", cleaned, re.DOTALL | re.IGNORECASE)

    if thought_m and not thought:
        thought = thought_m.group(1).strip()
    if final_m:
        return {"type": "final", "thought": thought, "final_answer": final_m.group(1).strip()}
    if action_m:
        return {
            "type": "action",
            "thought": thought,
            "action": action_m.group(1).strip().upper(),
            "action_input": action_in_m.group(1).strip() if action_in_m else "",
        }
    return {"type": "final", "thought": thought, "final_answer": cleaned.strip()}


# ---------------------------------------------------------------------------
# DispatchAgentTool 主類別
# ---------------------------------------------------------------------------


class DispatchAgentTool(BaseTool):
    """統一調度 Agent，支援 INTEGRATED（單階段整合）、PARALLEL（雙階段並行）與 REACT（多步驟）三種執行模式。

    三種模式共用：工具載入、別名解析、_generate_final_response、_call_llm。
    """

    def __init__(
        self,
        tool_registry: list[BaseTool],
        mode: Literal["INTEGRATED", "PARALLEL", "REACT"] = "INTEGRATED",
    ) -> None:
        """初始化調度器。

        Args:
            tool_registry: 所有可用工具的列表。
            mode: 執行模式，'INTEGRATED'（預設）、'PARALLEL' 或 'REACT'。
        """
        self._mode = mode
        self._tools: dict[str, BaseTool] = {t.intent_name.upper(): t for t in tool_registry}
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
        """Returns: 工具說明（不直接暴露給分類 LLM）。"""
        return f"統一調度 Agent（{self._mode} 模式，由 router 直接路由）"

    def can_handle_by_regex(self, text: str) -> bool:
        """Returns: 永遠回傳 False，由 router 直接呼叫。"""
        return False

    # ------------------------------------------------------------------
    # 共用：工具描述組裝
    # ------------------------------------------------------------------

    def _build_tool_descriptions(self, *, include_chat: bool = False) -> str:
        """組裝工具描述清單（供 Planner/ReAct Prompt 使用）。

        Args:
            include_chat: 是否在末尾加入 CHAT 虛擬工具描述（ReAct 模式需要）。

        Returns:
            格式化的工具描述字串。
        """
        excluded = {"PARALLEL_AGENT", "REACT_AGENT", "DISPATCH_AGENT", "INTEGRATED_AGENT", "CHAT"}
        lines: list[str] = [
            _TOOL_DESC_TEMPLATE.format(name=name, description=tool.llm_description)
            for name, tool in self._tools.items()
            if name not in excluded
        ]
        if include_chat:
            lines.append("- **CHAT**：一般聊天回覆或問候，不需要呼叫外部工具時使用")
        return "\n".join(lines)

    def _build_system_prompt(self) -> str:
        """組裝系統提示（供 Planner/ReAct/Integrated Prompt 使用）。"""
        if self._mode == "INTEGRATED":
            return _INTEGRATED_TOOL_TEMPLATE.format(
                tool_descriptions=self._build_tool_descriptions()
            )
        elif self._mode == "PARALLEL":
            return _PARALLEL_PLANNER_SYSTEM_TEMPLATE.format(
                tool_descriptions=self._build_tool_descriptions()
            )
        return _REACT_SYSTEM_TEMPLATE.format(
            tool_descriptions=self._build_tool_descriptions(include_chat=True)
        )

    def _should_early_terminate(
        self,
        plan: ExecutionPlan,
        executed_results: list[tuple[str, ToolResponseMode, str]],
        user_query: str,
    ) -> bool:
        """舊版 _should_early_terminate 相容方法。"""
        return self._parallel_should_early_terminate(plan, executed_results, user_query)

    async def _execute_single_task(
        self,
        task: PlannedTask,
        bot: commands.Bot,
        message: discord.Message,
        vibe_snippet: str,
        full_context: str,
        images: list[str] | None = None,
    ) -> tuple[str, ToolResponseMode, str]:
        """舊版 _execute_single_task 相容方法。"""
        return await self._parallel_execute_single_task(task, bot, message, vibe_snippet, full_context, images)

    async def _execute_tool(
        self,
        tool_name: str,
        action_input: str,
        bot: commands.Bot,
        message: discord.Message,
        vibe_snippet: str,
        full_context: str,
        images: list[str] | None = None,
    ) -> str:
        """舊版 _execute_tool 相容方法。"""
        return await self._react_execute_tool(tool_name, action_input, bot, message, vibe_snippet, full_context, images)

    # ------------------------------------------------------------------
    # 共用：LLM 推理呼叫
    # ------------------------------------------------------------------

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
    ) -> str:
        """向 BotBrain 發送推理任務並等待結果。

        Args:
            brain: BotBrain Cog 實例。
            system_prompt: 系統提示。
            user_prompt: 使用者提示（可含歷史記錄）。
            task_id: 任務識別 ID。
            model: 目標 LLM 模型名稱。
            temperature: 採樣溫度。
            timeout: 等待逾時秒數，None 則使用 REACT_STEP_TIMEOUT。
            images: 多模態圖片 Base64 列表（若有）。
            stream_callback: 即時 Token 串流回調協程（若有）。

        Returns:
            LLM 生成的文字，超時則回傳空字串。
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()

        async def _on_reply(reply: str) -> None:
            if not future.done():
                future.set_result(reply)

        await brain.enqueue_task(InferenceTask(
            priority=settings.PRIORITY_TAG_RESPONSE,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            callback=_on_reply,
            stream_callback=stream_callback,
            model=model,
            task_id=task_id,
            temperature=temperature,
            images=images,
        ))

        _timeout = timeout if timeout is not None else getattr(settings, "REACT_STEP_TIMEOUT", 60.0)
        try:
            return await asyncio.wait_for(future, timeout=_timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "DispatchAgent[%s]: LLM 推理超時 (model=%s, %.1fs)", self._mode, model, _timeout
            )
            return ""

    # ------------------------------------------------------------------
    # 共用：最終對話文本合成（PRIMARY_MODEL）
    # ------------------------------------------------------------------

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
    ) -> tuple[str, str, str, str]:
        """使用 PRIMARY_MODEL 合成最終給使用者的對話文本。

        Args:
            brain: BotBrain Cog 實例。
            user_query: 使用者原始訊息。
            channel_id: 頻道 ID（用於動態人設）。
            bot: Discord Bot 實例。
            user_id: 使用者 ID（用於記憶查詢）。
            display_name: 使用者顯示名稱。
            observations: 工具執行結果列表 (tool_name, result_text)。
            vibe_snippet: 頻道近期話題氛圍摘要。
            full_context: 完整上下文。
            draft_answer: 規劃階段產生的草稿答案（ReAct 模式用）。
            images: 多模態圖片 Base64 列表。
            session: 即時思考會話（用於串流思考鏈）。

        Returns:
            (clean_speech, persona_thought, sys_prompt, user_prompt) 四元組。
        """
        recent_text = (full_context[-1500:] + "\n" + user_query) if full_context else user_query
        head_persona, tail_lore = build_dynamic_persona(recent_text, "CHAT", channel_id=channel_id)
        memory_block = build_user_memory_block(bot, user_id, display_name)

        vibe_block = (
            "【頻道近期話題氛圍】(這只是讓妳用來讀空氣的背景氣氛！)\n"
            f"{vibe_snippet}\n\n"
        ) if vibe_snippet else ""

        vision_self_lore = (
            "\n【多模態視覺自我感知提示】：若圖片中出現淺藍髮色、丸子頭、白鳥羽毛帽、偵探斗篷等特徵的少女，這就是妳自己（橘雪莉）！\n"
        ) if images else ""

        author_note = f"\n{tail_lore}\n{vision_self_lore}" if (tail_lore or vision_self_lore) else ""

        length_rule = (
            "2. 【豐富生動的對話】：請像個話多、熱情又愛互動的 15 歲台灣女高中生一樣聊天！"
            "可以講 2～4 句話（不需刻意簡短），內容要有情緒起伏、表達想法與可愛的互動。"
        )

        cot_instruction = ""
        if getattr(settings, "MICRO_COT_ENABLED", True):
            cot_instruction = (
                "0. 在給出最終回覆前，妳必須先用 <thought>...</thought> 標籤包覆妳的內心想法。\n"
            )

        sys_prompt = (
            f"{head_persona}\n\n"
            + (f"{memory_block}\n\n" if memory_block else "")
            + f"{vibe_block}"
            + (f"{full_context}\n" if full_context else "")
            + f"{author_note}"
            + "【回覆規則】\n"
            + f"{cot_instruction}"
            + "1. 【對話連貫與話題推進】：自然接續剛才的對話脈絡與互動情境。若當前話題正在進行，請順著剛才的情緒與情境深入延續，嚴禁每輪重複「欸～笨蛋」、「你在說什麼」等初次驚訝的模板化開場白！\n"
            + f"{length_rule}\n"
            + "3. 不知道的事請直說，不要臆造資訊。"
        )

        if observations:
            if self._mode == "REACT":
                obs_lines = [f"- 【{name}】：{text}" for name, text in observations]
                user_prompt = (
                    f"使用者（{display_name}）對妳說：「{user_query}」\n\n"
                    "【系統資訊 - 工具已完成之動作結果】：\n"
                    + "\n".join(obs_lines)
                    + "\n\n請結合上述工具動作結果與妳名偵探的性格與口吻回應對方。"
                )
            else:
                obs_lines = [f"- 工具 [{name}]：{res}" for name, res in observations]
                obs_text = "\n\n【工具執行記錄與數據回傳】\n" + "\n".join(obs_lines)
                user_prompt = f"使用者（{display_name}）說：「{user_query}」{obs_text}"
        else:
            user_prompt = f"使用者（{display_name}）說：「{user_query}」"

        stream_cb = session.on_stream_chunk if (session and session.is_enabled) else None

        raw_response = await self._call_llm(
            brain,
            system_prompt=sys_prompt,
            user_prompt=user_prompt,
            task_id=f"{self._mode.lower()}-final-{int(time.time() * 1000)}",
            model=settings.PRIMARY_MODEL,
            temperature=getattr(settings, "LLM_TEMPERATURE", 0.85),
            timeout=float(settings.OLLAMA_TIMEOUT),
            images=images,
            stream_callback=stream_cb,
        )

        persona_thought = _extract_xml_tag(raw_response, "thought") or ""
        extracted = _extract_xml_tag(raw_response, "final_answer") or raw_response
        clean_speech = re.sub(r"(?is)<thought>.*?</thought>", "", extracted).strip()
        clean_speech = re.sub(r"<\/?(?:thought|final_answer)>", "", clean_speech).strip()

        if not clean_speech or len(clean_speech.strip()) < 2:
            clean_speech = draft_answer or "哼哼～名偵探已經為你處理完畢囉！"

        return clean_speech, persona_thought, sys_prompt, user_prompt

        return clean_speech, persona_thought, sys_prompt, user_prompt

    # ------------------------------------------------------------------
    # PARALLEL 模式內部方法
    # ------------------------------------------------------------------

    async def _parallel_execute_single_task(
        self,
        task: PlannedTask,
        bot: commands.Bot,
        message: discord.Message,
        vibe_snippet: str,
        full_context: str,
        images: list[str] | None = None,
    ) -> tuple[str, ToolResponseMode, str]:
        """非同步執行單一規劃工具任務。

        Args:
            task: PlannedTask 實例。
            bot: Discord Bot 實例。
            message: 觸發訊息。
            vibe_snippet: 氛圍摘要。
            full_context: 完整上下文。
            images: 多模態圖片列表。

        Returns:
            (canonical_tool_name, response_mode, observation_result) 三元組。
        """
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
            return canonical_name, ToolCategory.DATA_PROVIDER, f"工具 {canonical_name} 執行失敗: {exc}"

    def _parallel_should_early_terminate(
        self,
        plan: ExecutionPlan,
        executed_results: list[tuple[str, ToolResponseMode, str]],
        user_query: str,
    ) -> bool:
        """判定是否觸發 PARALLEL 模式的行為自適應對話抑制。

        條件：1.ACTION_EARLY_TERMINATION_ENABLED 為 True 2.所有工具皆為 DIRECT_UI
        3.Planner 判定 needs_persona_reply 為 False 4.無明確閒聊關鍵字。

        Args:
            plan: Planner 輸出的執行計畫。
            executed_results: 已執行工具結果列表。
            user_query: 使用者原始訊息。

        Returns:
            True 代表應終止並跳過對話合成。
        """
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

    # ------------------------------------------------------------------
    # INTEGRATED 模式內部方法（單階段整合自主調度）
    # ------------------------------------------------------------------

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
    ) -> None:
        """INTEGRATED 模式主流程（單階段內嵌 Action 標籤調度）。

        Args:
            brain: BotBrain Cog 實例。
            bot: Discord Bot 實例。
            message: 觸發訊息。
            user_query: 使用者原始訊息。
            vibe_snippet: 氛圍摘要。
            full_context: 完整上下文。
            images: 多模態圖片列表。
            start_time: 流程開始時間戳。
        """
        channel_id = message.channel.id
        display_name = message.author.display_name
        user_id = message.author.id

        # 啟動即時思考推演會話
        session = LiveThinkingSession(message)
        await session.start("💭 正在思考中")

        recent_text = (full_context[-1500:] + "\n" + user_query) if full_context else user_query
        head_persona, tail_lore = build_dynamic_persona(recent_text, "CHAT", channel_id=channel_id)
        memory_block = build_user_memory_block(bot, user_id, display_name, query_text=user_query)

        vibe_block = (
            "【頻道近期話題氛圍】(這只是讓妳用來讀空氣的背景氣氛！)\n"
            f"{vibe_snippet}\n\n"
        ) if vibe_snippet else ""

        vision_self_lore = (
            "\n【多模態視覺自我感知提示】：若圖片中出現淺藍髮色、丸子頭、白鳥羽毛帽、偵探斗篷等特徵的少女，這就是妳自己（橘雪莉）！\n"
        ) if images else ""

        author_note = f"\n{tail_lore}\n{vision_self_lore}" if (tail_lore or vision_self_lore) else ""

        tool_guidelines = _INTEGRATED_TOOL_TEMPLATE.format(
            tool_descriptions=self._build_tool_descriptions()
        )

        length_rule = (
            "2. 【豐富生動的對話】：請像個話多、熱情又愛互動的 15 歲台灣女高中生一樣聊天！"
            "可以講 2～4 句話（不需刻意簡短），內容要有情緒起伏、表達想法與可愛的互動。"
        )

        cot_instruction = ""
        if getattr(settings, "MICRO_COT_ENABLED", True):
            cot_instruction = (
                "0. 🧠 【深度思考機制】：在給出任何動作或回覆前，妳「必須」先用 <thought>...</thought> 標籤包覆妳的內心思考、局勢分析與回覆策略。\n"
            )

        sys_prompt = (
            f"{head_persona}\n\n"
            + (f"{memory_block}\n\n" if memory_block else "")
            + f"{vibe_block}"
            + (f"{full_context}\n" if full_context else "")
            + f"{author_note}\n"
            + f"{tool_guidelines}\n"
            + "【回覆規則】\n"
            + f"{cot_instruction}"
            + "1. 【對話連貫與話題推進】：自然接續剛才的對話脈絡與互動情境。若當前話題正在進行，請順著剛才的情緒與情境深入延續，嚴禁每輪重複「欸～笨蛋」、「你在說什麼」等初次驚訝的模板化開場白！\n"
            + f"{length_rule}\n"
            + "3. 不知道的事請直說，不要臆造資訊。"
        )

        user_prompt = f"使用者（{display_name}）說：「{user_query}」"
        stream_cb = session.on_stream_chunk if (session and session.is_enabled) else None

        raw_response = await self._call_llm(
            brain,
            system_prompt=sys_prompt,
            user_prompt=user_prompt,
            task_id=f"integrated-pass1-{int(time.time() * 1000)}",
            model=settings.PRIMARY_MODEL,
            temperature=getattr(settings, "LLM_TEMPERATURE", 0.85),
            timeout=float(settings.OLLAMA_TIMEOUT),
            images=images,
            stream_callback=stream_cb,
        )

        persona_thought = _extract_xml_tag(raw_response, "thought") or ""
        actions = _parse_actions(raw_response)

        # ── 【分支一：無 Action，純日常閒聊直接回覆（單次推論秒回）】 ─────
        if not actions:
            clean_speech = re.sub(r"(?is)<thought>.*?</thought>", "", raw_response).strip()
            clean_speech = re.sub(r"<\/?(?:thought|final_answer|action)>", "", clean_speech).strip()
            if not clean_speech or len(clean_speech.strip()) < 2:
                clean_speech = "（歪頭看著你）嗯？怎麼啦？"

            elapsed_ms = int((time.time() - start_time) * 1000)
            full_cot_thought = f"【雪莉內心思考】\n{persona_thought}" if persona_thought else ""

            log_event(
                "chat_reply",
                message_id=message.id,
                user=display_name,
                channel_id=channel_id,
                raw_input=user_query,
                intent="INTEGRATED_CHAT",
                intent_source="integrated_agent_direct",
                cot_thought=full_cot_thought,
                reply_preview=clean_speech[:120],
                model_used=settings.PRIMARY_MODEL,
                latency_ms=elapsed_ms,
                tools_invoked=[],
            )

            if session.is_enabled and session.placeholder_msg:
                await session.finish(
                    clean_speech,
                    thought_log=full_cot_thought,
                    intent_name="INTEGRATED_CHAT",
                    model=settings.PRIMARY_MODEL,
                    latency_ms=elapsed_ms,
                    prompt=user_query,
                )
            else:
                await simulate_typing_and_send(
                    message,
                    clean_speech,
                    intent_name="INTEGRATED_CHAT",
                    prompt=user_query,
                    model=settings.PRIMARY_MODEL,
                    system_prompt=sys_prompt,
                    user_prompt=user_prompt,
                    latency_ms=elapsed_ms,
                    thought_log=full_cot_thought,
                )
            return

        # ── 【分支二：偵測到 Action，發動工具並進行第二階段合成】 ─────────
        logger.info(
            "DispatchAgent[INTEGRATED]: 偵測到工具呼叫 -> %s",
            [(a.tool, a.input) for a in actions],
        )

        max_tasks = getattr(settings, "PARALLEL_MAX_TASKS", 5)
        seen_keys: set[tuple[str, str]] = set()
        deduped: list[PlannedTask] = []
        for a in actions[:max_tasks]:
            key = (a.tool.upper(), a.input.strip())
            if key not in seen_keys:
                seen_keys.add(key)
                deduped.append(a)

        session.push_step("action", f"調用工具: {', '.join([t.tool for t in deduped])}")

        executed_results = list(await asyncio.gather(*[
            self._parallel_execute_single_task(t, bot, message, vibe_snippet, full_context, images=images)
            for t in deduped
        ]))

        invoked_tool_names = [name for name, _, _ in executed_results]
        if executed_results:
            obs_desc = ", ".join([f"{name}: {obs[:50]}" for name, _, obs in executed_results])
            session.push_step("obs", f"取得數據: {obs_desc}")

        # 檢查 DIRECT_UI 自適應早期終止
        is_all_direct_ui = all(mode == ToolCategory.DIRECT_UI for _, mode, _ in executed_results)
        has_chat_intent = bool(re.search(r"(並|而且|順便|跟我說|告訴我|分享|覺得|看法|心得|對話|聊天|喜不喜歡|喜愛|早安|晚安)", user_query))
        if is_all_direct_ui and not has_chat_intent and getattr(settings, "ACTION_EARLY_TERMINATION_ENABLED", True):
            await remove_hourglass_reaction(message, bot.user)
            elapsed_ms = int((time.time() - start_time) * 1000)
            cot_thought = (
                f"【雪莉內心思考】\n{persona_thought}\n\n【已發動工具】\n"
                + "\n".join([f"- {name}: {obs}" for name, _, obs in executed_results])
            )
            log_event(
                "chat_reply",
                message_id=message.id,
                user=display_name,
                channel_id=channel_id,
                raw_input=user_query,
                intent="INTEGRATED_ACTION",
                intent_source="integrated_early_term",
                cot_thought=cot_thought,
                reply_preview=f"已為您發動: {invoked_tool_names}",
                model_used=settings.PRIMARY_MODEL,
                latency_ms=elapsed_ms,
                tools_invoked=invoked_tool_names,
            )
            if session.is_enabled and session.placeholder_msg:
                await session.finish(
                    f"✨ 已為您發動: {', '.join(invoked_tool_names)}",
                    thought_log=cot_thought,
                    intent_name="INTEGRATED_ACTION",
                    model=settings.PRIMARY_MODEL,
                    latency_ms=elapsed_ms,
                    prompt=user_query,
                )
            return

        # 純資料工具或需性格回覆：調用第二輪對話文本合成
        obs_pairs = [(name, obs) for name, _, obs in executed_results]
        final_speech, synth_thought, final_sys_prompt, final_user_prompt = await self._generate_final_response(
            brain=brain,
            user_query=user_query,
            channel_id=channel_id,
            bot=bot,
            user_id=user_id,
            display_name=display_name,
            observations=obs_pairs,
            vibe_snippet=vibe_snippet,
            full_context=full_context,
            images=images,
            session=session,
        )

        elapsed_ms = int((time.time() - start_time) * 1000)
        thought_sections: list[str] = []
        if persona_thought:
            thought_sections.append(f"【雪莉初始思考】\n{persona_thought}")
        if obs_pairs:
            thought_sections.append("【工具執行回傳數據】\n" + "\n".join([f"- {t}: {o}" for t, o in obs_pairs]))
        if synth_thought:
            thought_sections.append(f"【雪莉最終思考】\n{synth_thought}")
        full_cot_thought = "\n\n".join(thought_sections)

        log_event(
            "chat_reply",
            message_id=message.id,
            user=display_name,
            channel_id=channel_id,
            raw_input=user_query,
            intent="INTEGRATED_ACTION",
            intent_source="integrated_agent_full",
            cot_thought=full_cot_thought,
            reply_preview=final_speech[:120],
            model_used=settings.PRIMARY_MODEL,
            latency_ms=elapsed_ms,
            tools_invoked=invoked_tool_names,
        )

        if session.is_enabled and session.placeholder_msg:
            await session.finish(
                final_speech,
                thought_log=full_cot_thought,
                intent_name="INTEGRATED_ACTION",
                model=settings.PRIMARY_MODEL,
                latency_ms=elapsed_ms,
                prompt=user_query,
            )
        else:
            await simulate_typing_and_send(
                message,
                final_speech,
                intent_name="INTEGRATED_ACTION",
                prompt=user_query,
                model=settings.PRIMARY_MODEL,
                system_prompt=final_sys_prompt,
                user_prompt=final_user_prompt,
                latency_ms=elapsed_ms,
                thought_log=full_cot_thought,
            )

    async def _run_parallel(
        self,
        brain: Any,
        bot: commands.Bot,
        message: discord.Message,
        user_query: str,
        vibe_snippet: str,
        full_context: str,
        images: list[str] | None,
        start_time: float,
    ) -> None:
        """PARALLEL 模式主流程。

        Args:
            brain: BotBrain Cog 實例。
            bot: Discord Bot 實例。
            message: 觸發訊息。
            user_query: 使用者原始訊息。
            vibe_snippet: 氛圍摘要。
            full_context: 完整上下文。
            images: 多模態圖片列表。
            start_time: 流程開始時間戳。
        """
        channel_id = message.channel.id
        display_name = message.author.display_name
        user_id = message.author.id
        planner_model = getattr(settings, "PARALLEL_PLANNER_MODEL", settings.CLASSIFIER_MODEL)
        planner_timeout = getattr(settings, "PARALLEL_STEP_TIMEOUT", 60.0)

        # 啟動即時思考推演會話
        session = LiveThinkingSession(message)
        await session.start("📋 意圖解析中")

        system_prompt = _PARALLEL_PLANNER_SYSTEM_TEMPLATE.format(
            tool_descriptions=self._build_tool_descriptions()
        )
        planner_raw = await self._call_llm(
            brain, system_prompt,
            _USER_MESSAGE_TEMPLATE.format(author=display_name, content=user_query),
            task_id=f"parallel-plan-{message.id}",
            model=planner_model,
            temperature=0.1,
            timeout=planner_timeout,
        )

        plan = _parse_plan_json(planner_raw, user_query)
        logger.info(
            "[Parallel Planner] 思考: %s | 任務數: %d | 需性格回覆: %s",
            plan.thought or "無", len(plan.tasks), plan.needs_persona_reply,
        )

        plan_desc = plan.thought or (f"規劃調用 {', '.join([t.tool for t in plan.tasks])}" if plan.tasks else "日常閒聊對話")
        session.push_step("plan", plan_desc)

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
            executed_results = list(await asyncio.gather(*[
                self._parallel_execute_single_task(t, bot, message, vibe_snippet, full_context, images=images)
                for t in deduped
            ]))

        invoked_tool_names = [name for name, _, _ in executed_results]
        if executed_results:
            obs_desc = ", ".join([f"{name}: {obs[:50]}" for name, _, obs in executed_results])
            session.push_step("obs", obs_desc)

        if self._parallel_should_early_terminate(plan, executed_results, user_query):
            await remove_hourglass_reaction(message, bot.user)
            elapsed_ms = int((time.time() - start_time) * 1000)
            cot_thought = (
                f"【Parallel 規劃思考】\n{plan.thought}\n\n【已發動工具】\n"
                + "\n".join([f"- {name}: {obs}" for name, _, obs in executed_results])
            )
            log_event(
                "chat_reply",
                message_id=message.id, user=display_name, channel_id=channel_id,
                raw_input=user_query, intent="PARALLEL_AGENT", intent_source="parallel_early_term",
                cot_thought=cot_thought, reply_preview=f"已並行發動: {invoked_tool_names}",
                model_used=planner_model, latency_ms=elapsed_ms, tools_invoked=invoked_tool_names,
            )
            if session.is_enabled and session.placeholder_msg:
                await session.finish(
                    f"✨ 已為您發動: {', '.join(invoked_tool_names)}",
                    thought_log=cot_thought,
                    intent_name="PARALLEL_AGENT",
                    model=planner_model,
                    latency_ms=elapsed_ms,
                    prompt=user_query,
                )
            return

        obs_pairs = [(name, obs) for name, _, obs in executed_results]
        final_speech, persona_thought, sys_prompt, user_prompt = await self._generate_final_response(
            brain=brain, user_query=user_query, channel_id=channel_id,
            bot=bot, user_id=user_id, display_name=display_name,
            observations=obs_pairs, vibe_snippet=vibe_snippet,
            full_context=full_context, images=images, session=session,
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

        log_event(
            "chat_reply",
            message_id=message.id, user=display_name, channel_id=channel_id,
            raw_input=user_query, intent="PARALLEL_AGENT", intent_source="parallel_agent_full",
            cot_thought=full_cot_thought, reply_preview=final_speech[:120],
            model_used=settings.PRIMARY_MODEL, latency_ms=elapsed_ms, tools_invoked=invoked_tool_names,
        )
        if session.is_enabled and session.placeholder_msg:
            await session.finish(
                final_speech,
                thought_log=full_cot_thought,
                intent_name="PARALLEL_AGENT",
                model=f"{planner_model} -> {settings.PRIMARY_MODEL}",
                latency_ms=elapsed_ms,
                prompt=user_query,
            )
        else:
            await simulate_typing_and_send(
                message, final_speech,
                intent_name="PARALLEL_AGENT", prompt=user_query,
                model=f"{planner_model} -> {settings.PRIMARY_MODEL}",
                system_prompt=sys_prompt, user_prompt=user_prompt,
                latency_ms=elapsed_ms, thought_log=full_cot_thought,
            )

    # ------------------------------------------------------------------
    # REACT 模式內部方法
    # ------------------------------------------------------------------

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
        """呼叫指定工具（silent=True）並收集文字結果。

        Args:
            tool_name: 工具名稱（大寫）。
            action_input: 給工具的指令文字。
            bot: Bot 實例。
            message: 觸發訊息。
            vibe_snippet: 氛圍摘要。
            full_context: 完整上下文。
            images: 多模態圖片列表。

        Returns:
            工具執行的觀察結果字串。
        """
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
            return f"工具 {canonical_name} 執行時發生錯誤：{exc}"


    async def _run_react(
        self,
        brain: Any,
        bot: commands.Bot,
        message: discord.Message,
        user_query: str,
        vibe_snippet: str,
        full_context: str,
        images: list[str] | None,
        start_time: float,
    ) -> None:
        """REACT 模式主流程（多步推理迴圈）。

        Args:
            brain: BotBrain Cog 實例。
            bot: Discord Bot 實例。
            message: 觸發訊息。
            user_query: 使用者原始訊息。
            vibe_snippet: 氛圍摘要。
            full_context: 完整上下文。
            images: 多模態圖片列表。
            start_time: 流程開始時間戳。
        """
        channel_id = message.channel.id
        user_id = message.author.id
        display_name = message.author.display_name
        react_model = settings.REACT_MODEL

        session = LiveThinkingSession(message)
        await session.start("🔍 思考第一步推理中")

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
                user_prompt = _USER_MESSAGE_TEMPLATE.format(author=display_name, content=user_query)
            else:
                transcript_str = "\n\n".join(transcript_parts)
                max_chars = settings.REACT_MAX_TRANSCRIPT_CHARS
                if len(transcript_str) > max_chars:
                    transcript_str = "...(歷史已截斷)...\n\n" + transcript_str[-max_chars:]
                user_prompt = _REACT_CONTINUATION_TEMPLATE.format(transcript=transcript_str)

            raw_output = await self._call_llm(
                brain, system_prompt, user_prompt,
                task_id=f"react-step{step}-{message.id}",
                model=react_model,
                temperature=0.1,
                timeout=settings.REACT_STEP_TIMEOUT,
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
                    transcript_parts.append(_OBSERVATION_TEMPLATE.format(
                        tool=tool_name,
                        result=f"你已對 [{tool_name}] 執行過相同指令。請勿重複呼叫，推進下一步。",
                    ))
                    continue

                called_tool_actions.add(tool_key)
                observation = await self._react_execute_tool(
                    tool_name, action_input, bot, message, vibe_snippet, full_context, images=images,
                )
                transcript_parts.append(_OBSERVATION_TEMPLATE.format(tool=tool_name, result=observation))
                executed_observations.append((tool_name, observation))
                session.push_step("obs", f"取得 [{tool_name}] 觀測: {observation[:40]}")
                logger.info("[ReAct Step %d] 觀察 [%s]: %s", step, tool_name, observation[:150].replace("\n", " "))

        has_chat_request = bool(re.search(
            r"(並|而且|順便|跟我說|告訴我|分享|覺得|看法|心得|對話|聊天|喜不喜歡|喜愛)", user_query
        ))
        if len(executed_observations) == 1 and not has_chat_request:
            only_tool, _ = executed_observations[0]
            if only_tool in ("PIXIV", "DRAW", "MUSIC"):
                await remove_hourglass_reaction(message, bot.user)
                elapsed_ms = int((time.time() - start_time) * 1000)
                full_cot_thought = "【ReAct 任務調度思考】\n" + "\n".join(step_thoughts) if step_thoughts else ""
                log_event(
                    "chat_reply",
                    message_id=message.id, user=display_name, channel_id=channel_id,
                    raw_input=user_query, intent="REACT", intent_source="react_agent_smart_term",
                    cot_thought=full_cot_thought, reply_preview=f"已發動媒體工具 [{only_tool}]",
                    model_used=react_model, latency_ms=elapsed_ms, tools_invoked=[only_tool],
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

        final_speech, persona_thought, final_sys_prompt, final_user_prompt = await self._generate_final_response(
            brain=brain, user_query=user_query, channel_id=channel_id,
            bot=bot, user_id=user_id, display_name=display_name,
            observations=executed_observations, vibe_snippet=vibe_snippet,
            full_context=full_context, draft_answer=final_answer or "", images=images,
            session=session,
        )

        elapsed_ms = int((time.time() - start_time) * 1000)
        thought_sections: list[str] = []
        if step_thoughts:
            thought_sections.append("【ReAct 任務調度思考】\n" + "\n".join(step_thoughts))
        if persona_thought:
            thought_sections.append(f"【雪莉角色內心思考】\n{persona_thought}")
        full_cot_thought = "\n\n".join(thought_sections)

        if final_speech:
            clean_answer = re.sub(r"\*.*?\*", "", final_speech).strip()
            clean_answer = re.sub(r"\n{3,}", "\n\n", clean_answer).strip()
            if clean_answer:
                if session.is_enabled and session.placeholder_msg:
                    await session.finish(
                        clean_answer,
                        thought_log=full_cot_thought,
                        intent_name="REACT",
                        model=f"{react_model} -> {settings.PRIMARY_MODEL}",
                        latency_ms=elapsed_ms,
                        prompt=user_query,
                    )
                else:
                    await simulate_typing_and_send(
                        message, clean_answer,
                        intent_name="REACT", prompt=user_query,
                        model=f"{react_model} -> {settings.PRIMARY_MODEL}",
                        system_prompt=final_sys_prompt, user_prompt=final_user_prompt,
                        latency_ms=elapsed_ms, thought_log=full_cot_thought,
                    )
                log_event(
                    "chat_reply",
                    message_id=message.id, user=display_name, channel_id=channel_id,
                    raw_input=user_query, intent="REACT", intent_source="react_agent",
                    cot_thought=full_cot_thought, reply_preview=clean_answer[:200],
                    model_used=f"{react_model} -> {settings.PRIMARY_MODEL}",
                    latency_ms=elapsed_ms, tools_invoked=[t[0] for t in executed_observations],
                )
                return

        logger.warning("DispatchAgent[REACT]: 主力模型無輸出，改用 ChatTool 兜底")
        chat_tool = self._tools.get("CHAT")
        if chat_tool:
            await chat_tool.execute(bot, message, user_query, vibe_snippet, full_context, images=images)

    # ------------------------------------------------------------------
    # 統一執行入口
    # ------------------------------------------------------------------

    async def execute(
        self,
        bot: commands.Bot,
        message: discord.Message,
        user_query: str,
        vibe_snippet: str,
        full_context: str,
        **kwargs: object,
    ) -> ToolResult | str | None:
        """DispatchAgent 主執行入口，依 mode 分派至對應流程。

        Args:
            bot: Discord Bot 實例。
            message: 觸發的 Discord 訊息。
            user_query: 使用者原始文字。
            vibe_snippet: 頻道近期氛圍摘要。
            full_context: 含上下文的完整提示文本。
            **kwargs: 擴充參數，支援 images (list[str])。

        Returns:
            None（訊息已透過 Discord API 直接發送）。
        """
        raw_images = kwargs.get("images")
        images: list[str] | None = [str(x) for x in raw_images] if isinstance(raw_images, list) else None
        start_time = time.time()

        brain_cog = bot.get_cog("BotBrain")
        if not brain_cog:
            logger.error("DispatchAgent: BotBrain 未就緒")
            return None
        brain: Any = brain_cog

        if self._mode == "INTEGRATED":
            await self._run_integrated(brain, bot, message, user_query, vibe_snippet, full_context, images, start_time)
        elif self._mode == "PARALLEL":
            await self._run_parallel(brain, bot, message, user_query, vibe_snippet, full_context, images, start_time)
        else:
            await self._run_react(brain, bot, message, user_query, vibe_snippet, full_context, images, start_time)

        return None


# ---------------------------------------------------------------------------
# 相容性別名導出
# ---------------------------------------------------------------------------

parse_plan_json = _parse_plan_json


class IntegratedAgentTool(DispatchAgentTool):
    """IntegratedAgentTool 別名類別。"""

    def __init__(self, tool_registry: list[BaseTool]) -> None:
        super().__init__(tool_registry, mode="INTEGRATED")


class ParallelAgentTool(DispatchAgentTool):
    """ParallelAgentTool 相容別名類別。"""

    def __init__(self, tool_registry: list[BaseTool]) -> None:
        super().__init__(tool_registry, mode="PARALLEL")


class ReactAgentTool(DispatchAgentTool):
    """ReactAgentTool 相容別名類別。"""

    def __init__(self, tool_registry: list[BaseTool]) -> None:
        super().__init__(tool_registry, mode="REACT")
