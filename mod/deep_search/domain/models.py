"""
models.py (mod/deep_search/domain)
==================================
深度研究領域資料模型。
全部使用 @dataclass(frozen=True) 或嚴格型別定義，確保不變性與型別安全。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class SearchQuery:
    """單一檢索查詢定義。

    Attributes:
        query: 搜尋關鍵字字串。
        aspect: 探討之面向或維度簡述。
        round_index: 所屬研究輪次索引。
    """

    query: str
    aspect: str
    round_index: int = 1

    def to_dict(self) -> dict[str, Any]:
        """轉為字典格式。"""
        return asdict(self)


@dataclass(frozen=True)
class SearchResultItem:
    """單筆搜尋引擎結果項目。

    Attributes:
        title: 網頁標題。
        url: 網頁 URL。
        snippet: 搜尋引擎節錄摘要。
    """

    title: str
    url: str
    snippet: str

    def to_dict(self) -> dict[str, Any]:
        """轉為字典格式。"""
        return asdict(self)


@dataclass(frozen=True)
class SourceDoc:
    """已擷取之文獻與網頁資料。

    Attributes:
        url: 網頁來源 URL。
        title: 網頁標題。
        content: 經清洗後之純淨內文。
        snippet: 原始搜尋摘要。
        scraped_at: 擷取時間戳記 (ISO 格式)。
        char_count: 擷取字數。
    """

    url: str
    title: str
    content: str
    snippet: str = ""
    scraped_at: str = ""
    char_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """轉為字典格式。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceDoc:
        """自字典反序列化建立物件。"""
        return cls(
            url=str(data.get("url", "")),
            title=str(data.get("title", "")),
            content=str(data.get("content", "")),
            snippet=str(data.get("snippet", "")),
            scraped_at=str(data.get("scraped_at", "")),
            char_count=int(data.get("char_count", 0)),
        )


@dataclass(frozen=True)
class ThoughtLogEntry:
    """思考歷程節點記錄。

    Attributes:
        timestamp: 記錄時間字串 (HH:MM:SS 或 ISO)。
        phase: 思考階段代碼 (PLANNING, SEARCHING, READING, REFLECTING, SYNTHESIZING)。
        title: 本步驟核心動作或標題。
        content: 詳細思考推演與中間結論。
        round_index: 當前所處輪次。
    """

    timestamp: str
    phase: str
    title: str
    content: str
    round_index: int = 0

    def to_dict(self) -> dict[str, Any]:
        """轉為字典格式。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ThoughtLogEntry:
        """自字典反序列化建立物件。"""
        return cls(
            timestamp=str(data.get("timestamp", "")),
            phase=str(data.get("phase", "GENERAL")),
            title=str(data.get("title", "")),
            content=str(data.get("content", "")),
            round_index=int(data.get("round_index", 0)),
        )


@dataclass(frozen=True)
class DeepResearchReport:
    """最終產出之論文級深度研究報告。

    Attributes:
        topic: 研究主題。
        model_used: 執行推演使用之 LLM 模型名稱。
        executive_summary: 執行摘要。
        full_content: 完整 Markdown 報告本文。
        sources: 引用之文獻來源清單。
        total_time_seconds: 總耗時秒數。
        created_at: 產出時間字串。
    """

    topic: str
    model_used: str
    executive_summary: str
    full_content: str
    sources: list[SourceDoc] = field(default_factory=list)
    total_time_seconds: float = 0.0
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """轉為字典格式。"""
        return {
            "topic": self.topic,
            "model_used": self.model_used,
            "executive_summary": self.executive_summary,
            "full_content": self.full_content,
            "sources": [s.to_dict() for s in self.sources],
            "total_time_seconds": self.total_time_seconds,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeepResearchReport:
        """自字典反序列化建立物件。"""
        raw_sources = data.get("sources", [])
        sources = [
            s if isinstance(s, SourceDoc) else SourceDoc.from_dict(s)
            for s in raw_sources
            if isinstance(s, (dict, SourceDoc))
        ]
        return cls(
            topic=str(data.get("topic", "")),
            model_used=str(data.get("model_used", "")),
            executive_summary=str(data.get("executive_summary", "")),
            full_content=str(data.get("full_content", "")),
            sources=sources,
            total_time_seconds=float(data.get("total_time_seconds", 0.0)),
            created_at=str(data.get("created_at", "")),
        )


@dataclass(frozen=True)
class ResearchProgress:
    """即時研究進度事件廣播模型。

    Attributes:
        session_id: 研究階段唯一識別碼。
        status: 當前狀態 (RUNNING, COMPLETED, FAILED, CANCELLED)。
        progress_pct: 整體完成百分比 (0 ~ 100)。
        current_step: 當前進行步驟描述。
        latest_thought: 最新一筆思考歷程節點 (可選)。
    """

    session_id: str
    status: str
    progress_pct: int
    current_step: str
    latest_thought: ThoughtLogEntry | None = None

    def to_dict(self) -> dict[str, Any]:
        """轉為字典格式。"""
        return {
            "session_id": self.session_id,
            "status": self.status,
            "progress_pct": self.progress_pct,
            "current_step": self.current_step,
            "latest_thought": self.latest_thought.to_dict() if self.latest_thought else None,
        }


@dataclass(frozen=True)
class ResearchSession:
    """深度研究專案完整狀態模型 (包含進行中與歷史紀錄)。

    Attributes:
        session_id: 唯一識別碼 (例如: rs-20260818-123456)。
        topic: 研究核心主題。
        model: 使用之模型。
        status: 狀態 (PENDING, RUNNING, COMPLETED, FAILED, CANCELLED)。
        progress_pct: 完成進度百分比 (0 ~ 100)。
        current_step: 當前進行步驟。
        max_rounds: 規劃研究輪數。
        current_round: 當前輪次。
        thought_logs: 完整思考推演軌跡。
        sources: 已收集之來源文獻。
        final_report: 產出之完整報告 (若尚未完成則為 None)。
        started_at: 開始時間 (ISO)。
        finished_at: 完成時間 (ISO)。
        error_message: 異常錯誤訊息 (若有)。
        discord_channel_id: 發起此研究之 Discord 頻道 ID (可選)。
        discord_message_id: Discord 即時進度訊息 ID (可選)。
    """

    session_id: str
    topic: str
    model: str
    status: str = "PENDING"
    progress_pct: int = 0
    current_step: str = "準備啟動研究..."
    max_rounds: int = 4
    current_round: int = 0
    thought_logs: tuple[ThoughtLogEntry, ...] = field(default_factory=tuple)
    sources: tuple[SourceDoc, ...] = field(default_factory=tuple)
    final_report: DeepResearchReport | None = None
    started_at: str = ""
    finished_at: str = ""
    error_message: str = ""
    discord_channel_id: int | None = None
    discord_message_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """轉為可 JSON 序列化的字典。"""
        return {
            "session_id": self.session_id,
            "topic": self.topic,
            "model": self.model,
            "status": self.status,
            "progress_pct": self.progress_pct,
            "current_step": self.current_step,
            "max_rounds": self.max_rounds,
            "current_round": self.current_round,
            "thought_logs": [t.to_dict() for t in self.thought_logs],
            "sources": [s.to_dict() for s in self.sources],
            "final_report": self.final_report.to_dict() if self.final_report else None,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error_message": self.error_message,
            "discord_channel_id": self.discord_channel_id,
            "discord_message_id": self.discord_message_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchSession:
        """自字典反序列化建立 ResearchSession 物件。"""
        raw_thoughts = data.get("thought_logs", [])
        thought_logs = tuple(
            t if isinstance(t, ThoughtLogEntry) else ThoughtLogEntry.from_dict(t)
            for t in raw_thoughts
            if isinstance(t, (dict, ThoughtLogEntry))
        )

        raw_sources = data.get("sources", [])
        sources = tuple(
            s if isinstance(s, SourceDoc) else SourceDoc.from_dict(s)
            for s in raw_sources
            if isinstance(s, (dict, SourceDoc))
        )

        raw_report = data.get("final_report")
        if isinstance(raw_report, DeepResearchReport):
            final_report = raw_report
        elif isinstance(raw_report, dict):
            final_report = DeepResearchReport.from_dict(raw_report)
        else:
            final_report = None

        return cls(
            session_id=str(data.get("session_id", "")),
            topic=str(data.get("topic", "")),
            model=str(data.get("model", "")),
            status=str(data.get("status", "PENDING")),
            progress_pct=int(data.get("progress_pct", 0)),
            current_step=str(data.get("current_step", "")),
            max_rounds=int(data.get("max_rounds", 4)),
            current_round=int(data.get("current_round", 0)),
            thought_logs=thought_logs,
            sources=sources,
            final_report=final_report,
            started_at=str(data.get("started_at", "")),
            finished_at=str(data.get("finished_at", "")),
            error_message=str(data.get("error_message", "")),
            discord_channel_id=data.get("discord_channel_id"),
            discord_message_id=data.get("discord_message_id"),
        )
