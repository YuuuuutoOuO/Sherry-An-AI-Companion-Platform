"""
domain (mod/deep_search)
========================
領域實體、資料契約與抽象介面。
嚴格遵守 AGENTS.md，不包含任何外部 I/O 或套件相依。
"""

from __future__ import annotations

from mod.deep_search.domain.models import (
    DeepResearchReport,
    ResearchProgress,
    ResearchSession,
    SearchQuery,
    SearchResultItem,
    SourceDoc,
    ThoughtLogEntry,
)

__all__ = [
    "DeepResearchReport",
    "ResearchProgress",
    "ResearchSession",
    "SearchQuery",
    "SearchResultItem",
    "SourceDoc",
    "ThoughtLogEntry",
]
