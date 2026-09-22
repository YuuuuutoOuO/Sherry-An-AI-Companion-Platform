"""
handlers/social/dispatch/
=========================
雪莉調度引擎策略套件 (Strategy Pattern Dispatch Engine)。
包含單階段自主調度 (Integrated)、雙階段並行調度 (Parallel) 與多步推理調度 (React)。
"""
from __future__ import annotations

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
from handlers.social.dispatch.prompts import _TOOL_ALIASES, _load_tool_rules
from handlers.social.dispatch.react import ReactStrategy

__all__ = [
    "BaseDispatchStrategy",
    "IntegratedStrategy",
    "ParallelStrategy",
    "ReactStrategy",
    "PlannedTask",
    "ExecutionPlan",
    "_parse_actions",
    "_parse_plan_json",
    "_parse_react_output",
    "_extract_xml_tag",
    "_load_tool_rules",
    "_TOOL_ALIASES",
]
