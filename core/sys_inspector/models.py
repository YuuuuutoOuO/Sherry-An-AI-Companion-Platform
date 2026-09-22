"""core.sys_inspector.models
===========================
系統自我監控與延遲稽核核心領域資料模型。

定義事故事件、日誌快照與雲端模型診斷結果之強型別資料結構。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class IncidentType(str, Enum):
    """異常事件類型枚舉。"""

    SLOW_RESPONSE_90S = "SLOW_RESPONSE_90S"  # 回覆耗時超過 90 秒
    FAILURE_120S = "FAILURE_120S"            # 耗時超過 120 秒失敗或超時
    SYSTEM_BLOCKING = "SYSTEM_BLOCKING"      # 事件迴圈或佇列卡死阻塞
    INFERENCE_FAILURE = "INFERENCE_FAILURE"  # 推論連線中斷 / API 崩潰 / 徹查放棄 (即時)
    SLOW_TTFT_WARNING = "SLOW_TTFT_WARNING"  # 首字延遲 (TTFT) 偏高預警 (如 >5s)
    ACTION_LOOP = "ACTION_LOOP"              # Agent 重複工具調用死循環
    QUALITY_ANOMALY = "QUALITY_ANOMALY"      # 人設偏離、幻覺或品質異常
    MANUAL_CHECK = "MANUAL_CHECK"            # 管理員手動 /self-monitoring action:check 觸發


class IncidentSeverity(str, Enum):
    """異常嚴重等級枚舉。"""

    INFO = "INFO"          # 資訊提示 / 手動體檢
    WARNING = "WARNING"    # 慢速警告 (90s)
    CRITICAL = "CRITICAL"  # 嚴重異常 (120s 失敗 / 系統阻塞)


@dataclass(frozen=True)
class IncidentReport:
    """單次異常事件報告結構。

    Attributes:
        incident_type: 異常事件類型。
        severity: 異常嚴重等級。
        latency_ms: 耗時（毫秒）。
        trigger_reason: 觸發診斷原因說明。
        trace_id: 追蹤鏈路 ID。
        channel_id: 關聯 Discord 頻道 ID。
        user_name: 關聯使用者名稱。
        task_id: 關聯大腦推論任務 ID。
        raw_prompt: 觸發之使用者輸入或任務描述（截斷）。
        extra_metrics: 系統額外度量資訊（佇列大小、顯存等）。
    """

    incident_type: IncidentType
    severity: IncidentSeverity
    latency_ms: int = 0
    trigger_reason: str = ""
    trace_id: str = ""
    channel_id: int = 0
    user_name: str = ""
    task_id: str = ""
    raw_prompt: str = ""
    extra_metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LogSnapshot:
    """擷取之 5 分鐘運行日誌快照。

    Attributes:
        window_minutes: 回溯視窗（分鐘）。
        total_lines: 讀取到的總行數。
        error_count: 錯誤行數計數。
        warning_count: 警告行數計數。
        bot_log_snippet: bot.log 關鍵節錄。
        runtime_event_snippet: runtime_log.jsonl 事件節錄。
        combined_text: 經壓縮整理後供 LLM 診斷之純文字。
    """

    window_minutes: int
    total_lines: int
    error_count: int
    warning_count: int
    bot_log_snippet: str
    runtime_event_snippet: str
    combined_text: str


@dataclass(frozen=True)
class DiagnosticResult:
    """雲端搜尋模型產出之延遲診斷成果。

    Attributes:
        root_cause: 根本原因剖析說明。
        bottleneck_category: 瓶頸類型（如 GPU_LOCK, NETWORK_TIMEOUT, PROMPT_OVERFLOW, IO_BLOCKING 等）。
        key_evidence: 關鍵日誌證據摘要。
        recommendations: 具體改善與修復建議清單。
        raw_model_response: 雲端模型原始回覆內容。
        model_name: 執行診斷之模型名稱。
        diagnosis_latency_ms: 診斷本身耗時（毫秒）。
    """

    root_cause: str
    bottleneck_category: str
    key_evidence: list[str]
    recommendations: list[str]
    raw_model_response: str
    model_name: str
    diagnosis_latency_ms: int = 0


@dataclass(frozen=True)
class MicroSpanMetrics:
    """全鏈路微觀耗時與執行指標。

    Attributes:
        rag_fetch_ms: 記憶與向量庫檢索耗時（毫秒）。
        prompt_build_ms: 上下文與提示詞組裝耗時（毫秒）。
        ttft_ms: 首字延遲 (Time to First Token)（毫秒）。
        inference_ms: 模型純生成推論耗時（毫秒）。
        tool_ms: 外部工具調用執行耗時（毫秒）。
        total_latency_ms: 總端到端耗時（毫秒）。
        cache_hit: 是否命中 Prompt 前綴快取。
        token_count: 生成或消耗之 Token 總數。
        spans: 各自訂 Span 的詳細耗時字典。
    """

    rag_fetch_ms: int = 0
    prompt_build_ms: int = 0
    ttft_ms: int = 0
    inference_ms: int = 0
    tool_ms: int = 0
    total_latency_ms: int = 0
    cache_hit: bool = False
    token_count: int = 0
    spans: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """轉換為便於寫入 extra_metrics 或 JSON 序列化之字典。"""
        return {
            "rag_fetch_ms": self.rag_fetch_ms,
            "prompt_build_ms": self.prompt_build_ms,
            "ttft_ms": self.ttft_ms,
            "inference_ms": self.inference_ms,
            "tool_ms": self.tool_ms,
            "total_latency_ms": self.total_latency_ms,
            "cache_hit": self.cache_hit,
            "token_count": self.token_count,
            "spans": dict(self.spans),
        }


@dataclass(frozen=True)
class QualityScoreResult:
    """非同步認知與人設品質審查成果。

    Attributes:
        persona_score: 人設符合度評分 (1-5)。
        faithfulness_score: 上下文忠實度評分 (1-5)。
        is_hallucination: 是否存在明顯事實幻覺或自相矛盾。
        critique: 審判員評論摘要。
        improvement_advice: 具體改進建議。
        raw_response: 雲端模型原始回覆。
        model_name: 執行審查之模型名稱。
        audit_latency_ms: 審查執行耗時（毫秒）。
    """

    persona_score: int
    faithfulness_score: int
    is_hallucination: bool
    critique: str
    improvement_advice: str
    raw_response: str = ""
    model_name: str = ""
    audit_latency_ms: int = 0


class SelfHealingStrategy(str, Enum):
    """自癒應對策略類型。"""

    NONE = "NONE"                          # 無需動作，維持現狀
    CONTEXT_COMPRESSION = "CONTEXT_COMPRESSION"  # 上下文壓縮 / 摘要清理 (針對 PROMPT_OVERFLOW)
    FALLBACK_MODEL = "FALLBACK_MODEL"      # 切換備用輕量模型 (針對 GPU_LOCK / TIMEOUT)
    INJECT_REFLECTION = "INJECT_REFLECTION"# 注入反思提示詞 (針對 ACTION_LOOP)
    RESET_STATE = "RESET_STATE"            # 重置對話工作區狀態


@dataclass(frozen=True)
class SelfHealingAction:
    """自癒應對決策動作。

    Attributes:
        strategy: 採用的自癒策略。
        trigger_incident: 觸發之異常事件類型。
        rationale: 採取此策略之原因說明。
        parameters: 執行此策略所需之參數字典。
    """

    strategy: SelfHealingStrategy
    trigger_incident: IncidentType
    rationale: str
    parameters: dict[str, Any] = field(default_factory=dict)

