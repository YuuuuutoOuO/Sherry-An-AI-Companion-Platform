"""
handlers/social/parallel_agent.py
===================================
高效能並行任務調度器 (Parallel Agent) 核心。

機制：
  1. 接收 @Tag 訊息或複合指令。
  2. 單次推論（Single-Pass Inference）呼叫 PLANNER_MODEL，輸出結構化任務 JSON：
     - 任務清單 (tasks)
     - 對話抑制標記 (needs_persona_reply)
  3. 非同步並行（asyncio.gather）同時執行所有無依賴工具。
  4. 行為自適應終止（Action Early Termination）：
     若所有工具皆為自帶 UI 渲染（DIRECT_UI，如 MUSIC、PIXIV、DRAW）且無對話需求，
     直接秒級結束任務，不發送多餘文字廢話。
  5. 若包含純資料工具 (DATA_ONLY) 或使用者有明確閒聊需求，由 PRIMARY_MODEL 進行最終發言合成。
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import discord
from discord.ext import commands

import settings
from handlers.bot_brain import BotBrain, InferenceTask
from handlers.social.contracts import ToolCategory, ToolResult
from handlers.social.memory_builder import build_user_memory_block
from handlers.social.observability import log_event
from handlers.social.persona import build_dynamic_persona
from handlers.social.tool_base import BaseTool, ToolResponseMode
from handlers.social.utils import remove_hourglass_reaction, simulate_typing_and_send

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 資料結構定義
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
# 工具別名映射表
# ---------------------------------------------------------------------------

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

_TOOL_DESC_TEMPLATE = "- **{name}**：{description}"

# ---------------------------------------------------------------------------
# 提示詞模板
# ---------------------------------------------------------------------------

_PARALLEL_PLANNER_SYSTEM_TEMPLATE = """[SYSTEM: PARALLEL TASK DISPATCH & CHAT SUPPRESSION ENGINE]
你是一個高效率、極致冷靜的任務排程與對話抑制引擎。你的任務是分析使用者的輸入，拆解為待執行的工具清單，並精準判斷是否需要後續的性格對話回覆。

⚠️ 嚴格鐵則：
1. 絕對禁止輸出任何問候、角色扮演、聊天對話或道德說教。
2. 輸出格式「只能」是符合 JSON Schema 的純 JSON 物件，嚴禁附加 Markdown 解釋文字。
3. 支援多工並行拆解：若使用者同時要求多個動作（如同時播兩首歌、搜圖又設提醒），必須在 tasks 陣列中列出所有工具。

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
- 設為 false：所有發動的工具全為「自帶UI/媒體播放」工具（如 MUSIC, PIXIV, DRAW），且使用者「沒有」提出心得詢問、觀點看法或聊天要求。
- 設為 true：
  1. 發動了純資料查詢工具（如 STOCK, SEARCH, WEATHER），需要 AI 將資料轉述為口語回覆。
  2. 使用者明確表達了聊天、互動、心得、評價、早晚安問候等對話需求。
  3. 純日常閒聊（此時 tasks 為空陣列 `[]`）。

## 示範案例 (Few-Shot Examples)

案例 1（多重自帶 UI 動作，無須廢話文字）：
使用者：「請幫我播放莫札特的歌與巴哈的歌」
{{"thought": "使用者要求依序播放兩首不同音樂，拆解為兩個獨立 MUSIC 任務。均為自帶 UI 動作，無額外聊天需求，抑制對話。", "tasks": [{{"tool": "MUSIC", "input": "莫札特"}}, {{"tool": "MUSIC", "input": "巴哈"}}], "needs_persona_reply": false}}

案例 2（單一自帶 UI 動作，無須多餘文字）：
使用者：「找一張雪莉的圖片」
{{"thought": "使用者要求搜尋圖片，發動 PIXIV 工具。自帶 UI 發布，無須文字廢話。", "tasks": [{{"tool": "PIXIV", "input": "橘雪莉"}}], "needs_persona_reply": false}}

案例 3（動作 + 伴隨閒聊/心得詢問）：
使用者：「幫我放首周杰倫的晴天，順便跟我說妳喜不喜歡這首歌」
{{"thought": "使用者要求播放音樂，並主動詢問對歌曲的喜好看法。發動 MUSIC 並開啟性格對話回覆。", "tasks": [{{"tool": "MUSIC", "input": "周杰倫 晴天"}}], "needs_persona_reply": true}}

案例 4（多個純數據查詢，需要轉述）：
使用者：「查一下 2330 和 2454 的走勢」
{{"thought": "使用者查詢兩檔股票行情，發動兩個 STOCK 查詢。為純數據資料，需由性格模型轉述結果。", "tasks": [{{"tool": "STOCK", "input": "2330.TW"}}, {{"tool": "STOCK", "input": "2454.TW"}}], "needs_persona_reply": true}}

案例 5（複合動作：繪圖 + 定時提醒）：
使用者：「幫我畫一隻戴偵探帽的小貓，然後 10 分鐘後提醒我出門」
{{"thought": "使用者要求生成圖片與設定鬧鐘。同時發動 DRAW 與 SCHEDULE 工具。", "tasks": [{{"tool": "DRAW", "input": "戴偵探帽的小貓"}}, {{"tool": "SCHEDULE", "input": "10分鐘後提醒出門"}}], "needs_persona_reply": true}}

案例 6（純日常對話 / 打招呼）：
使用者：「今天天氣真好，妳在做什麼呢？」
{{"thought": "一般日常社交閒聊，無需任何外部工具，直接交由性格模型回覆。", "tasks": [], "needs_persona_reply": true}}

案例 7（YouTube 網址點歌）：
使用者：「https://www.youtube.com/watch?v=dQw4w9WgXcQ」
{{"thought": "使用者提供 YouTube 網址，為音樂播放請求，發動 MUSIC 工具。自帶 UI 播放，抑制多餘對話。", "tasks": [{{"tool": "MUSIC", "input": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}}], "needs_persona_reply": false}}
"""

_USER_MESSAGE_TEMPLATE = "使用者（{author}）說：「{content}」"

# ---------------------------------------------------------------------------
# JSON 解析輔助
# ---------------------------------------------------------------------------

def parse_plan_json(raw: str, default_query: str) -> ExecutionPlan:
    """從 LLM 原始輸出中解析 ExecutionPlan，具備多層容錯回退機制。

    Args:
        raw: LLM 原始文字輸出。
        default_query: 解析失敗時的兜底查詢文字。

    Returns:
        ExecutionPlan 實例。
    """
    cleaned = raw.strip()
    # 移除外層 markdown 程式碼區塊標記
    cleaned = re.sub(r"^```(?:json|xml)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()

    # 尋找最外層的 {...}
    json_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if json_match:
        target_str = json_match.group(0)
        try:
            data = json.loads(target_str)
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

                return ExecutionPlan(
                    thought=thought,
                    tasks=tasks,
                    needs_persona_reply=needs_reply,
                )
        except Exception as exc:
            logger.warning("ParallelAgent: JSON 解析失敗 (%s)，嘗試啟動 Regex 容錯修復: %s", exc, raw[:100])

    # Regex 容錯提取（處理 LLM JSON 缺少閉合大括號等格式瑕疵）
    tasks_extracted: list[PlannedTask] = []
    task_pattern = re.compile(r'["\']tool["\']\s*:\s*["\']([A-Za-z0-9_]+)["\']\s*,\s*["\']input["\']\s*:\s*["\']([^"\']*)["\']', re.IGNORECASE)
    for m in task_pattern.finditer(cleaned):
        t_name = m.group(1).strip().upper()
        t_input = m.group(2).strip()
        tasks_extracted.append(PlannedTask(tool=t_name, input=t_input or default_query))

    thought_m = re.search(r'["\']thought["\']\s*:\s*["\']([^"\']*)["\']', cleaned)
    extracted_thought = thought_m.group(1).strip() if thought_m else ""
    needs_reply_m = re.search(r'["\']needs_persona_reply["\']\s*:\s*(true|false)', cleaned, re.IGNORECASE)
    extracted_needs_reply = (needs_reply_m.group(1).lower() == "true") if needs_reply_m else True

    if tasks_extracted:
        return ExecutionPlan(
            thought=extracted_thought,
            tasks=tasks_extracted,
            needs_persona_reply=extracted_needs_reply,
        )

    # 完全無工具特徵 → 預設為純閒聊
    return ExecutionPlan(
        thought="未能提取結構化工具任務，回退為純對話",
        tasks=[],
        needs_persona_reply=True,
    )


# ---------------------------------------------------------------------------
# ParallelAgentTool 實作
# ---------------------------------------------------------------------------

class ParallelAgentTool(BaseTool):
    """高效能並行任務調度器，單次推論完成多工拆解並行發動。"""

    def __init__(self, tool_registry: list[BaseTool]) -> None:
        """初始化並行調度器。

        Args:
            tool_registry: 所有可用工具的列表。
        """
        self._tools: dict[str, BaseTool] = {
            t.intent_name.upper(): t for t in tool_registry
        }
        logger.info(
            "ParallelAgentTool 初始化完成，已載入工具：%s",
            list(self._tools.keys()),
        )

    @property
    def intent_name(self) -> str:
        return "PARALLEL_AGENT"

    @property
    def llm_description(self) -> str:
        return "Parallel 多任務並行調度器（由 router 直接路由）"

    def can_handle_by_regex(self, text: str) -> bool:
        return False

    def _build_tool_descriptions(self) -> str:
        """組裝工具描述清單（供 Planner Prompt 使用）。"""
        lines: list[str] = []
        for name, tool in self._tools.items():
            if name in ("PARALLEL_AGENT", "REACT_AGENT", "CHAT"):
                continue
            lines.append(_TOOL_DESC_TEMPLATE.format(
                name=name,
                description=tool.llm_description,
            ))
        return "\n".join(lines)

    def _build_system_prompt(self) -> str:
        """組裝極簡的 Planner 系統提示（專注結構化拆解與排程）。"""
        tool_descriptions = self._build_tool_descriptions()
        return _PARALLEL_PLANNER_SYSTEM_TEMPLATE.format(
            tool_descriptions=tool_descriptions,
        )

    async def _call_planner_llm(
        self,
        brain: BotBrain,
        system_prompt: str,
        user_prompt: str,
        message_id: int,
    ) -> str:
        """向 BotBrain 發送單次規劃推理任務。"""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()

        async def _on_reply(reply: str) -> None:
            if not future.done():
                future.set_result(reply)

        target_model = getattr(settings, "PARALLEL_PLANNER_MODEL", settings.CLASSIFIER_MODEL)
        step_timeout = getattr(settings, "PARALLEL_STEP_TIMEOUT", 60.0)

        task = InferenceTask(
            priority=settings.PRIORITY_TAG_RESPONSE,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            callback=_on_reply,
            model=target_model,
            task_id=f"parallel-plan-{message_id}",
            temperature=0.1,
        )
        await brain.enqueue_task(task)

        try:
            return await asyncio.wait_for(future, timeout=step_timeout)
        except asyncio.TimeoutError:
            logger.warning("ParallelAgent: 規劃器推理超時 (model=%s, %.1fs)", target_model, step_timeout)
            return ""

    async def _execute_single_task(
        self,
        task: PlannedTask,
        bot: commands.Bot,
        message: discord.Message,
        vibe_snippet: str,
        full_context: str,
        images: list[str] | None = None,
    ) -> tuple[str, ToolResponseMode, str]:
        """非同步執行單一工具任務。

        Returns:
            (canonical_tool_name, response_mode, observation_result)
        """
        canonical_name = _TOOL_ALIASES.get(task.tool.upper(), task.tool.upper())
        tool = self._tools.get(canonical_name)
        if not tool:
            err_msg = f"找不到工具 [{task.tool}]"
            logger.warning("ParallelAgent: %s", err_msg)
            return canonical_name, ToolCategory.DATA_PROVIDER, err_msg

        logger.info(
            "⚡ [Parallel Tool 發動] 工具: [%s] | 模式: %s | 參數: '%s'",
            canonical_name, tool.response_mode.value, task.input
        )

        try:
            # 以 silent=True 執行工具，若工具自帶 UI 發送則已完成 Discord 渲染
            result = await tool.execute(
                bot, message, task.input, vibe_snippet, full_context,
                silent=True,
                images=images,
            )
            if isinstance(result, ToolResult):
                is_direct = result.is_direct_rendered
                obs = result.summary_text
                category = ToolCategory.DIRECT_UI if is_direct else ToolCategory.DATA_PROVIDER
            else:
                category = tool.response_mode
                obs = str(result) if result is not None else f"工具 {canonical_name} 執行完畢。"

            obs_clipped = obs[:getattr(settings, "REACT_MAX_OBSERVATION_LENGTH", 1200)]
            return canonical_name, category, obs_clipped
        except Exception as exc:
            logger.error("ParallelAgent: 工具 %s 執行失敗: %s", canonical_name, exc, exc_info=True)
            return canonical_name, ToolCategory.DATA_PROVIDER, f"工具 {canonical_name} 執行失敗: {exc}"

    def _should_early_terminate(
        self,
        plan: ExecutionPlan,
        executed_results: list[tuple[str, ToolResponseMode, str]],
        user_query: str,
    ) -> bool:
        """判定是否觸發行為自適應對話抑制（Action Early Termination）。

        條件：
          1. 全域開關 ACTION_EARLY_TERMINATION_ENABLED 為 True
          2. 有執行工具任務且所有執行的工具皆為 DIRECT_UI
          3. Planner 判定 needs_persona_reply 為 False
          4. 使用者文字中未包含明確閒聊/觀點/心得關鍵字
        """
        if not getattr(settings, "ACTION_EARLY_TERMINATION_ENABLED", True):
            return False

        if not executed_results:
            return False

        # 若有任何一個工具屬於 DATA_PROVIDER（非 DIRECT_UI），必須由 AI 轉述數據，不能終止
        has_data_only_tool = any(mode != ToolCategory.DIRECT_UI for _, mode, _ in executed_results)
        if has_data_only_tool:
            return False

        # 若 Planner 判定需要對話
        if plan.needs_persona_reply:
            return False

        # 正則雙重防護：檢查使用者是否有明確的聊天/評論要求
        has_chat_keywords = bool(re.search(
            r"(並|而且|順便|跟我說|告訴我|分享|覺得|看法|心得|對話|聊天|喜不喜歡|喜愛|早安|晚安)",
            user_query,
        ))
        if has_chat_keywords:
            return False

        return True

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
        images: list[str] | None = None,
    ) -> tuple[str, str, str, str]:
        """使用 PRIMARY_MODEL 合成最終給使用者的對話文本。"""
        recent_text = (full_context[-1500:] + "\n" + user_query) if full_context else user_query

        head_persona, tail_lore = build_dynamic_persona(recent_text, "CHAT", channel_id=channel_id)
        memory_block = build_user_memory_block(bot, user_id, display_name)

        vibe_block = (
            "【頻道近期話題氛圍】(⚠️這只是讓妳用來『讀空氣』的背景氣氛！請不要像鸚鵡一樣把這些詞彙硬念出來，除非對方現在的話語剛好有關聯！)\n"
            f"{vibe_snippet}\n\n"
        ) if vibe_snippet else ""

        vision_self_lore = (
            "\n【多模態視覺自我感知提示】：若使用者傳入的圖片中出現淺藍/藍色髮色、丸子頭/甜甜圈髮髻、白鳥羽毛帽、福爾摩斯偵探斗篷/披風等特徵的少女，這就是妳自己（橘雪莉）！請以名偵探看到自己自拍或同人圖的視角自然回應。\n"
        ) if images else ""

        author_note = f"\n{tail_lore}\n{vision_self_lore}" if (tail_lore or vision_self_lore) else ""

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

        # 組裝工具執行的觀察結果
        obs_text = ""
        if observations:
            obs_lines = [f"- 工具 [{name}]：{res}" for name, res in observations]
            obs_text = "\n\n【工具執行記錄與數據回傳】\n" + "\n".join(obs_lines)

        user_prompt = f"使用者（{display_name}）說：「{user_query}」{obs_text}"

        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()

        async def _on_reply(reply: str) -> None:
            if not future.done():
                future.set_result(reply)

        await brain.enqueue_task(InferenceTask(
            priority=settings.PRIORITY_TAG_RESPONSE,
            system_prompt=sys_prompt,
            user_prompt=user_prompt,
            callback=_on_reply,
            model=settings.PRIMARY_MODEL,
            task_id=f"parallel-final-{int(time.time()*1000)}",
            images=images,
        ))

        try:
            raw_response = await asyncio.wait_for(future, timeout=settings.OLLAMA_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("ParallelAgent: 主力模型合成回覆超時 (%ds)", settings.OLLAMA_TIMEOUT)
            raw_response = ""

        # 拆解思考標籤
        persona_thought = ""
        m = re.search(r"<thought>(.*?)</thought>", raw_response, re.DOTALL | re.IGNORECASE)
        if m:
            persona_thought = m.group(1).strip()
            clean_speech = re.sub(r"<thought>.*?</thought>", "", raw_response, flags=re.DOTALL | re.IGNORECASE).strip()
        else:
            clean_speech = raw_response.strip()

        if not clean_speech:
            clean_speech = "哼哼～名偵探已經為你處理完畢囉！"

        return clean_speech, persona_thought, sys_prompt, user_prompt

    async def execute(
        self,
        bot: commands.Bot,
        message: discord.Message,
        user_query: str,
        vibe_snippet: str,
        full_context: str,
        **kwargs: object,
    ) -> ToolResult | str | None:
        """Parallel Agent 主執行入口。"""
        raw_images = kwargs.get("images")
        images: list[str] | None = [str(x) for x in raw_images] if isinstance(raw_images, list) else None
        start_time = time.time()
        brain_cog = bot.get_cog("BotBrain")
        if not brain_cog:
            logger.error("ParallelAgent: BotBrain 未就緒")
            return None
        brain: Any = brain_cog

        channel_id = message.channel.id
        user_id = message.author.id
        display_name = message.author.display_name

        # ── 階段一：單次推論任務規劃 (PLANNER_MODEL) ─────────────────────────
        system_prompt = self._build_system_prompt()
        initial_user_msg = _USER_MESSAGE_TEMPLATE.format(
            author=display_name, content=user_query
        )

        planner_raw = await self._call_planner_llm(
            brain, system_prompt, initial_user_msg, message.id
        )

        plan = parse_plan_json(planner_raw, user_query)
        logger.info(
            "🧠 [Parallel Planner 思考]: %s | 拆解任務數: %d | 需性格回覆: %s",
            plan.thought or "無思考摘要",
            len(plan.tasks),
            plan.needs_persona_reply,
        )

        # ── 階段二：非同步並行執行所有工具 (asyncio.gather) ──────────────────
        max_tasks = getattr(settings, "PARALLEL_MAX_TASKS", 5)
        trimmed_tasks = plan.tasks[:max_tasks]

        # 去重相同 (tool, input)
        seen_keys: set[tuple[str, str]] = set()
        deduped_tasks: list[PlannedTask] = []
        for t in trimmed_tasks:
            key = (t.tool.upper(), t.input.strip())
            if key not in seen_keys:
                seen_keys.add(key)
                deduped_tasks.append(t)

        executed_results: list[tuple[str, ToolResponseMode, str]] = []
        if deduped_tasks:
            coros = [
                self._execute_single_task(t, bot, message, vibe_snippet, full_context, images=images)
                for t in deduped_tasks
            ]
            executed_results = await asyncio.gather(*coros)

        # ── 階段三：行為自適應終止判定 (Action Early Termination) ────────────
        should_terminate = self._should_early_terminate(plan, executed_results, user_query)
        invoked_tool_names = [name for name, _, _ in executed_results]

        if should_terminate:
            await remove_hourglass_reaction(message, bot.user)
            elapsed_ms = int((time.time() - start_time) * 1000)
            logger.info(
                "⚡ [Parallel Early Termination]: 自帶 UI 工具 [%s] 執行完成，終止對話合成以避免重複發言 (耗時: %dms)",
                ", ".join(invoked_tool_names),
                elapsed_ms,
            )
            cot_thought = f"【Parallel 規劃思考】\n{plan.thought}\n\n【已發動工具】\n" + "\n".join(
                [f"- {name}: {obs}" for name, _, obs in executed_results]
            )
            log_event(
                "chat_reply",
                message_id=message.id,
                user=display_name,
                channel_id=channel_id,
                raw_input=user_query,
                intent="PARALLEL_AGENT",
                intent_source="parallel_early_term",
                cot_thought=cot_thought,
                reply_preview=f"已並行發動動作工具: {invoked_tool_names}",
                model_used=getattr(settings, "PARALLEL_PLANNER_MODEL", settings.CLASSIFIER_MODEL),
                latency_ms=elapsed_ms,
                tools_invoked=invoked_tool_names,
            )
            return None

        # ── 階段四：性格發言合成 (PRIMARY_MODEL) ─────────────────────────────
        obs_pairs = [(name, obs) for name, _, obs in executed_results]
        final_speech, persona_thought, sys_prompt, user_prompt = await self._generate_final_response(
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
            message_id=message.id,
            user=display_name,
            channel_id=channel_id,
            raw_input=user_query,
            intent="PARALLEL_AGENT",
            intent_source="parallel_agent_full",
            cot_thought=full_cot_thought,
            reply_preview=final_speech[:120],
            model_used=settings.PRIMARY_MODEL,
            latency_ms=elapsed_ms,
            tools_invoked=invoked_tool_names,
        )

        await simulate_typing_and_send(
            message,
            final_speech,
            intent_name="PARALLEL_AGENT",
            prompt=user_query,
            model=f"{getattr(settings, 'PARALLEL_PLANNER_MODEL', settings.CLASSIFIER_MODEL)} ➔ {settings.PRIMARY_MODEL}",
            system_prompt=sys_prompt,
            user_prompt=user_prompt,
            latency_ms=elapsed_ms,
            thought_log=full_cot_thought,
        )
        return None
