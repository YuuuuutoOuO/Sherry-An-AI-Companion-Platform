"""
handlers/social/dispatch/models.py
==================================
調度引擎專用資料結構與輸出解析器（支援 Action 標籤、JSON 計畫與 ReAct XML）。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 任務資料模型
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
# 解析器：Action 標籤（INTEGRATED 模式）
# ---------------------------------------------------------------------------


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
# 解析器：JSON 規劃（PARALLEL 模式）
# ---------------------------------------------------------------------------


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
# 解析器：XML 標籤與 ReAct 輸出（REACT 模式）
# ---------------------------------------------------------------------------


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
