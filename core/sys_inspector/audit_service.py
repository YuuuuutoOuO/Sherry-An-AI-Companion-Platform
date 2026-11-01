"""core.sys_inspector.audit_service
==================================
系統自我監控與延遲稽核領域服務層。

提供：
1. 5 分鐘運行日誌與事件快照安全擷取（嚴格對話內容脫敏）。
2. 調用雲端搜尋模型 (Cloud Search Model) 執行深度技術診斷。
3. 系統阻塞、慢速回覆 (>90s) 與推論崩潰/超時 (>120s) 判定與告警防抖。
4. 儲存於集中化資料庫 data/sys_inspector/audit.db 並產出結構化指標。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import re
import time
from typing import Any

from core.sys_inspector.audit_repository import AuditDBRepository
from core.sys_inspector.models import (
    DiagnosticResult,
    IncidentReport,
    IncidentSeverity,
    IncidentType,
    LogSnapshot,
    QualityScoreResult,
    SelfHealingAction,
    SelfHealingStrategy,
)
from core.contracts import CloudInferenceProtocol
import settings

logger = logging.getLogger(__name__)

_TZ_TW = timezone(timedelta(hours=8))  # 台灣時間 UTC+8

DEFAULT_SLOW_THRESHOLD_SEC: float = 90.0
DEFAULT_TIMEOUT_THRESHOLD_SEC: float = 120.0
DEFAULT_LOG_WINDOW_MINUTES: int = 5

_DEFAULT_CLOUD_CLIENT: CloudInferenceProtocol | Any | None = None
DEFAULT_ALERT_COOLDOWN_SEC: float = 300.0


# ---------------------------------------------------------------------------
# 1. LogRepository: 日誌收集與脫敏快照
# ---------------------------------------------------------------------------

class LogRepository:
    """系統日誌存取與安全快照倉儲。"""

    def __init__(
        self,
        bot_log_path: Path | None = None,
        runtime_log_path: Path | None = None,
        error_log_path: Path | None = None,
    ) -> None:
        """初始化日誌倉儲。"""
        self._bot_log_path: Path = bot_log_path if bot_log_path is not None else Path(getattr(settings, "BOT_LOG_PATH", settings.DATA_DIR / "bot.log"))
        self._runtime_log_path: Path = runtime_log_path if runtime_log_path is not None else Path(getattr(settings, "RUNTIME_LOG_PATH", settings.DATA_DIR / "runtime_log.jsonl"))
        self._error_log_path: Path = error_log_path if error_log_path is not None else Path(getattr(settings, "ERROR_LOG_PATH", settings.DATA_DIR / "error_log.jsonl"))

    async def get_recent_snapshot(self, window_minutes: int = DEFAULT_LOG_WINDOW_MINUTES) -> LogSnapshot:
        """非同步擷取過去指定分鐘數的日誌快照。"""
        return await asyncio.to_thread(self._get_recent_snapshot_sync, window_minutes)

    def _get_recent_snapshot_sync(self, window_minutes: int = DEFAULT_LOG_WINDOW_MINUTES) -> LogSnapshot:
        """同步讀取日誌檔案並以時間窗口過濾。"""
        now_tw = datetime.now(_TZ_TW)
        cutoff_time = now_tw - timedelta(minutes=window_minutes)

        bot_lines: list[str] = self._filter_bot_log(cutoff_time)
        error_entries: list[dict[str, Any]] = self._filter_jsonl(self._error_log_path, cutoff_time)
        runtime_entries: list[dict[str, Any]] = self._filter_jsonl(self._runtime_log_path, cutoff_time)

        total_lines = len(bot_lines) + len(error_entries) + len(runtime_entries)
        error_count = len(error_entries) + sum(1 for line in bot_lines if "[ERROR]" in line or "[CRITICAL]" in line)
        warning_count = sum(1 for line in bot_lines if "[WARNING]" in line)

        critical_bot_lines = [ln for ln in bot_lines if any(k in ln for k in ("[ERROR]", "[CRITICAL]", "[WARNING]"))]
        recent_bot_lines = bot_lines[-30:] if len(bot_lines) > 30 else bot_lines
        merged_bot_lines = list(dict.fromkeys(critical_bot_lines[-20:] + recent_bot_lines))
        bot_log_snippet = "\n".join(merged_bot_lines[-40:])

        event_lines: list[str] = []
        for err in error_entries[-15:]:
            event_lines.append(
                f"[ERR_EVENT] {err.get('ts', '')} [Trace:{err.get('trace_id', '-')}] {err.get('event', '')}: "
                f"{err.get('error_type', '')} - {_sanitize_text(err.get('message', ''))}"
            )

        for evt in runtime_entries[-25:]:
            lat = evt.get("latency_ms", 0)
            flag = "⚡SLOW " if lat > 10000 else ""
            tools = evt.get("tools_invoked", [])
            tools_str = f" tools={','.join(tools)}" if tools else ""
            user = evt.get("user", "")
            user_str = f" user={user}" if user else ""
            intent = evt.get("intent", "")
            intent_str = f" intent={intent}" if intent else f" {evt.get('event', '')}"
            event_lines.append(
                f"[RT_EVENT] {evt.get('ts', '')} [Trace:{evt.get('trace_id', '-')}] {flag}{intent_str.strip()} "
                f"({lat}ms){tools_str}{user_str} status={evt.get('status', 'ok')}"
            )
        runtime_event_snippet = "\n".join(event_lines[-40:])

        combined_text = (
            f"=== 過去 {window_minutes} 分鐘系統日誌快照 (統計: 錯誤={error_count}, 警告={warning_count}) ===\n\n"
            f"[主程式日誌摘要 (bot.log)]:\n{bot_log_snippet or '(無主程式日誌輸出)'}\n\n"
            f"[結構化觀測事件摘要 (runtime/error)]:\n{runtime_event_snippet or '(無觀測事件紀錄)'}\n"
        )

        return LogSnapshot(
            window_minutes=window_minutes,
            total_lines=total_lines,
            error_count=error_count,
            warning_count=warning_count,
            bot_log_snippet=bot_log_snippet,
            runtime_event_snippet=runtime_event_snippet,
            combined_text=combined_text,
        )

    def _filter_bot_log(self, cutoff_time: datetime) -> list[str]:
        """過濾 bot.log 中在截止時間之後的行。"""
        if not self._bot_log_path.exists():
            return []
        matched: list[str] = []
        ts_pattern = re.compile(r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})")
        try:
            with open(self._bot_log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            for line in lines[-800:]:
                line_str = line.strip()
                if not line_str:
                    continue
                m = ts_pattern.match(line_str)
                if m:
                    try:
                        line_dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=_TZ_TW)
                        if line_dt >= cutoff_time:
                            matched.append(line_str)
                    except ValueError:
                        matched.append(line_str)
                else:
                    if matched:
                        matched.append(line_str)
        except Exception as exc:
            logger.warning("LogRepository: 讀取 bot.log 失敗: %s", exc)
        return matched

    def _filter_jsonl(self, file_path: Path, cutoff_time: datetime) -> list[dict[str, Any]]:
        """過濾 JSONL 檔案中在截止時間之後的項目。"""
        if not file_path.exists():
            return []
        matched: list[dict[str, Any]] = []
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            for line in lines[-500:]:
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    data = json.loads(line_str)
                    ts_str = data.get("ts") or data.get("timestamp")
                    if ts_str:
                        dt: datetime | None = None
                        try:
                            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(_TZ_TW)
                        except Exception:
                            try:
                                dt = datetime.strptime(ts_str[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=_TZ_TW)
                            except Exception:
                                pass
                        if dt and dt < cutoff_time:
                            continue
                    matched.append(data)
                except json.JSONDecodeError:
                    continue
        except Exception as exc:
            logger.warning("LogRepository: 讀取 JSONL %s 失敗: %s", file_path, exc)
        return matched

def _sanitize_text(text: str) -> str:
    """對字串進行基礎去識別化與長度截斷。"""
    if not text:
        return ""
    clean = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}", "[EMAIL]", text)
    clean = re.sub(r"\b\d{17,20}\b", "[ID]", clean)
    return clean[:150]


# ---------------------------------------------------------------------------
# 2. CloudAnalysisClient: 雲端搜尋模型客觀技術稽核
# ---------------------------------------------------------------------------

DIAGNOSTIC_SYSTEM_PROMPT = """你是一位客觀、嚴謹且高度專業的「系統架構與效能稽核員（System Performance Auditor）」。
你的職責是依據系統發生的「嚴重延遲 (>90s)」、「推論超時/崩潰 (>120s)」或「系統阻塞 (Event Loop Lag / Queue Block)」事故，徹底審查並稽核所提供的【5 分鐘系統運行日誌】與【事件快照】。

【稽核原則與立場】：
1. 嚴格保持第三方專業稽核員的客觀、中立與技術嚴謹立場，絕不具備任何聊天伴侶或情感人設。
2. 直指技術病灶，精確剖析資源競爭、鎖阻塞、長文字 Prefill、外部依賴超時、請求排隊或非同步卡頓之技術根因。

【資安與隱私防護原則（最高優先級強制執行）】：
1. 嚴格禁止在診斷報告中的任何欄位輸出、引述或洩露任何使用者的聊天對話原文或提示詞具體字句！
2. 當事故成因為前一位使用者連續發送請求佔用資源時，必須一律採用去識別化的技術標號與抽象指標描述。
3. 所有跡證引用一律僅限於系統級日誌、Trace ID、執行耗時 (ms)、錯誤類型與模組名稱。

【輸出格式規範】：
【瓶頸分類】：[GPU_LOCK | EXTERNAL_API_TIMEOUT | PROMPT_OVERFLOW | EVENT_LOOP_BLOCK | QUEUE_CONGESTION | MEMORY_PRESSURE | UNKNOWN]
【根因剖析】：(客觀說明導致本次延遲或阻塞的代碼路徑、資源競爭或架構問題，2~3 句話)
【關鍵日誌跡證】：
- (引述日誌中最關鍵的 1~3 筆系統級 Log / Trace 行，不得含對話內容)
【具體修復建議】：
1. (立即可採取的應急復原措施)
2. (長期架構調優、限流或配置改善建議)

請一律使用繁體中文（台灣工程用語）撰寫，切中要害，嚴禁冗餘贅詞。"""


QUALITY_AUDIT_SYSTEM_PROMPT = """你是一位客觀、具備同理心且標準嚴謹的「AI 陪伴對話質量與人設審判員 (AI Persona & Quality Auditor)」。
你的任務是評估 AI 陪伴助手「雪莉 (Sherry)」對使用者的單次回應品質。

【評估標準】：
1. 人設符合度 (persona_score: 1-5 分)：
   - 5分：極佳符合雪莉溫暖、聰慧、略帶俏皮且重視陪伴的個性。
   - 3分：普通、像一般無感情的機器人或助手。
   - 1分：嚴重出戲、冷漠刻薄、或產生系統 Prompt 洩漏。
2. 忠實度與幻覺 (faithfulness_score: 1-5 分 & is_hallucination: 是/否)：
   - 是否忠實於上下文，有無捏造事實、邏輯自相矛盾或胡言亂語。
3. 審判短評與具體改進建議。

【輸出格式規範】：
【人設評分】：[1-5]
【忠實評分】：[1-5]
【存在幻覺】：[是/否]
【審判短評】：(簡短 1-2 句客觀說明)
【改進建議】：(簡短 1 句具體改進建議)

請一律使用繁體中文撰寫，切中要害，嚴禁多餘前言。"""


class CloudAnalysisClient:
    """雲端模型診斷客戶端。"""

    def __init__(self, cloud_client: CloudInferenceProtocol | Any | None = None) -> None:
        self._cloud_client = cloud_client if cloud_client is not None else _DEFAULT_CLOUD_CLIENT

    async def analyze_incident(
        self, incident: IncidentReport, log_snapshot: LogSnapshot
    ) -> DiagnosticResult:
        """調用自我監控專用雲端模型分析異常事件與日誌。"""
        start_time = time.monotonic()
        model_name = getattr(
            settings,
            "SELF_MONITORING_MODEL",
            getattr(settings, "CLOUD_SEARCH_MODEL", "gemini:gemini-3.5-flash-lite"),
        )

        user_prompt = self._build_prompt(incident, log_snapshot)

        raw_response = ""
        try:
            logger.info("CloudAnalysisClient: 正在調用雲端模型 %s 分析延遲事故...", model_name)
            raw_response = await self._call_cloud_model(user_prompt, DIAGNOSTIC_SYSTEM_PROMPT)
        except Exception as exc:
            logger.error("CloudAnalysisClient: 雲端模型診斷失敗: %s", exc)
            raw_response = self._build_rule_based_fallback(incident, log_snapshot, str(exc))

        latency_ms = int((time.monotonic() - start_time) * 1000)
        return self._parse_diagnosis(raw_response, model_name, latency_ms)

    async def audit_quality(
        self, user_msg: str, bot_response: str, trace_id: str = ""
    ) -> QualityScoreResult:
        """調用自我監控專用雲端模型審查單次回應之品質與人設。"""
        start_time = time.monotonic()
        model_name = getattr(
            settings,
            "SELF_MONITORING_MODEL",
            getattr(settings, "CLOUD_SEARCH_MODEL", "gemini:gemini-3.5-flash-lite"),
        )
        prompt = f"""【待審查對話】
- 追蹤標號 (Trace ID): `{trace_id or 'N/A'}`
- 使用者輸入: {_sanitize_text(user_msg)}
- 雪莉回覆內容: {bot_response[:400]}

請依據審查原則產出評分與建議。"""

        try:
            logger.info("CloudAnalysisClient: 正在調用模型 %s 執行品質與人設審查...", model_name)
            raw_response = await self._call_cloud_model(prompt, QUALITY_AUDIT_SYSTEM_PROMPT)
        except Exception as exc:
            logger.warning("CloudAnalysisClient: 雲端品質審查調用失敗 (%s)，啟用啟發式備援評估", exc)
            raw_response = self._build_quality_fallback(user_msg, bot_response, str(exc))

        latency_ms = int((time.monotonic() - start_time) * 1000)
        return self._parse_quality_result(raw_response, model_name, latency_ms)

    def _build_prompt(self, incident: IncidentReport, log_snapshot: LogSnapshot) -> str:
        """組裝發送給雲端模型的脫敏 Prompt。"""
        metrics_desc = "\n".join(f"- {k}: {v}" for k, v in incident.extra_metrics.items()) or "無"
        prompt_feature = (
            f"請求長度 {len(incident.raw_prompt)} 字元 (已自動脫敏遮蔽，切記禁止輸出任何使用者聊天原文)"
            if incident.raw_prompt
            else "無 (切記禁止輸出任何使用者聊天原文)"
        )

        prompt = f"""🚨 【系統異常診斷請求】
- 異常類型: {incident.incident_type.value}
- 嚴重程度: {incident.severity.value}
- 執行耗時: {incident.latency_ms} 毫秒 ({incident.latency_ms / 1000:.1f} 秒)
- 觸發成因: {incident.trigger_reason}
- 追蹤標號 (Trace ID): `{incident.trace_id or 'N/A'}`
- 關聯任務: `{incident.task_id or 'N/A'}`
- 提示詞特徵: {prompt_feature}

📊 【額外系統與微觀指標】:
{metrics_desc}

📑 【近 5 分鐘運行日誌快照】:
{log_snapshot.combined_text}

請嚴格遵循稽核原則，剖析系統延遲或阻塞的架構與技術根因，並按指定格式輸出。"""
        return prompt

    async def _call_cloud_model(self, prompt: str, system_prompt: str) -> str:
        """調用雲端推理客戶端獲取結構化回覆。"""
        if self._cloud_client is None:
            raise RuntimeError("CloudAnalysisClient: 尚未注入雲端推論客戶端，無法執行雲端診斷")
        if hasattr(self._cloud_client, "_generate_async"):
            res = await self._cloud_client._generate_async(prompt, system_instruction=system_prompt)
            return (res or "").strip()
        if hasattr(self._cloud_client, "stream_inference"):
            response_text = ""
            async for chunk in self._cloud_client.stream_inference(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.2,
                max_tokens=800,
            ):
                response_text += chunk
            return response_text.strip()
        raise RuntimeError("Incompatible cloud client: neither _generate_async nor stream_inference supported")

    def _build_rule_based_fallback(
        self, incident: IncidentReport, log_snapshot: LogSnapshot, error_detail: str
    ) -> str:
        """當雲端模型呼叫失敗時之本地啟發式備援診斷。"""
        cat = "UNKNOWN"
        prefix = f"雲端分析模型調用中斷 ({error_detail})。"
        lat = incident.latency_ms

        if incident.incident_type == IncidentType.ACTION_LOOP:
            cat = "ACTION_LOOP"
            cause = f"{prefix} 偵測到 Agent 重複調用相同工具或參數，陷入執行死循環。"
        elif incident.incident_type == IncidentType.SLOW_TTFT_WARNING:
            cat = "GPU_LOCK" if incident.extra_metrics.get("ttft_ms", 0) > 3000 else "PROMPT_OVERFLOW"
            cause = f"{prefix} 首字延遲 (TTFT) 偏高 ({lat}ms)，可能因 GPU 顯存排隊或 Prefill 階段過載。"
        elif incident.incident_type == IncidentType.QUALITY_ANOMALY:
            cat = "PERSONA_DRIFT"
            cause = f"{prefix} 偵測到對話人設或語意偏離常態。"
        elif incident.incident_type == IncidentType.SYSTEM_BLOCKING:
            cat = "EVENT_LOOP_BLOCK"
            cause = f"{prefix} 檢測到事件迴圈嚴重阻塞或佇列推積，可能存在未妥善以 to_thread 包裝之同步 Blocking I/O。"
        elif incident.incident_type == IncidentType.INFERENCE_FAILURE:
            reason_lower = (incident.trigger_reason or "").lower()
            if "content_filter" in reason_lower:
                cat = "CONTENT_FILTER"
                cause = (
                    f"{prefix} 供應商安全審查機制回傳 finish_reason=content_filter 拒絕請求。"
                    f"此為確定性結果，重試相同輸入無效；需改寫被觸發的內容或改用其他供應商模型。"
                )
            else:
                cat = "EXTERNAL_API_TIMEOUT"
                cause = f"{prefix} 大腦推論任務在執行中斷或遭遇致命異常: {incident.trigger_reason}。"
        elif lat >= 120000:
            cat = "EXTERNAL_API_TIMEOUT"
            cause = f"{prefix} 單次請求超過 120 秒硬性超時限制 ({lat}ms)，通常由大模型提供商 API 連線卡頓或 GPU 排隊過載所引起。"
        elif lat >= 90000:
            cat = "PROMPT_OVERFLOW"
            cause = f"{prefix} 單次回覆耗時偏高 ({lat}ms > 90s)，可能因 Prefill 上下文過長或地端顯卡算力吃緊。"
        else:
            cause = f"{prefix} 暫時無法明確分析單次異常根因。"

        evidence = [
            f"觸發條件: {incident.trigger_reason}",
            f"追蹤鏈路: {incident.trace_id}",
            f"快照統計: 錯誤行數 {log_snapshot.error_count}, 警告行數 {log_snapshot.warning_count}",
        ]
        evidence_str = "\n".join(f"- {e}" for e in evidence)

        return f"""【瓶頸分類】：{cat}
【根因剖析】：{cause}
【關鍵日誌跡證】：
{evidence_str}
【具體修復建議】：
1. 檢視推論佇列狀態與地端/雲端 API 連線狀況。
2. 若頻繁發生，請適度調整推理逾時常數或降低單次 Prompt 上下文長度。"""

    def _build_quality_fallback(self, user_msg: str, bot_response: str, error_detail: str) -> str:
        """品質審查本地啟發式備援。"""
        p_score = 4
        f_score = 4
        hallucination = "否"
        if len(bot_response.strip()) < 3:
            p_score = 2
            critique = "回覆長度過短，可能缺乏足夠陪伴互動資訊。"
            advice = "增加生成 Token 長度或檢視生成截斷原因。"
        elif "Error" in bot_response or "Exception" in bot_response:
            p_score = 1
            f_score = 2
            hallucination = "是"
            critique = "回覆中包含底層異常或報錯字樣。"
            advice = "檢查錯誤攔截機制，避免內部異常洩漏給使用者。"
        else:
            critique = f"雲端審查離線 ({error_detail})，啟發式規則初步判定正常。"
            advice = "保持正常對話流並持續監測。"

        return f"""【人設評分】：{p_score}
【忠實評分】：{f_score}
【存在幻覺】：{hallucination}
【審判短評】：{critique}
【改進建議】：{advice}"""

    def _parse_quality_result(self, raw_text: str, model_name: str, latency_ms: int) -> QualityScoreResult:
        """解析雲端品質審查回覆。"""
        persona_score = 4
        faithfulness_score = 4
        is_hallucination = False
        critique = "正常對話。"
        advice = "維持現狀。"

        m_p = re.search(r"【人設評分】[：:]\s*(\d)", raw_text)
        if m_p:
            persona_score = max(1, min(5, int(m_p.group(1))))

        m_f = re.search(r"【忠實評分】[：:]\s*(\d)", raw_text)
        if m_f:
            faithfulness_score = max(1, min(5, int(m_f.group(1))))

        m_h = re.search(r"【存在幻覺】[：:]\s*(是|否|true|false)", raw_text, re.IGNORECASE)
        if m_h:
            is_hallucination = m_h.group(1).lower() in ("是", "true")

        m_c = re.search(r"【審判短評】[：:]\s*(.*?)(?=\n【|\Z)", raw_text, re.DOTALL)
        if m_c:
            critique = m_c.group(1).strip()

        m_a = re.search(r"【改進建議】[：:]\s*(.*?)(?=\n【|\Z)", raw_text, re.DOTALL)
        if m_a:
            advice = m_a.group(1).strip()

        return QualityScoreResult(
            persona_score=persona_score,
            faithfulness_score=faithfulness_score,
            is_hallucination=is_hallucination,
            critique=critique,
            improvement_advice=advice,
            raw_response=raw_text,
            model_name=model_name,
            audit_latency_ms=latency_ms,
        )

    def _parse_diagnosis(self, raw_text: str, model_name: str, latency_ms: int) -> DiagnosticResult:
        """解析雲端模型回傳之繁體中文診斷段落。"""
        root_cause = "未能取得明確根因剖析。"
        bottleneck = "UNKNOWN"
        evidence: list[str] = []
        recommendations: list[str] = []

        m_cat = re.search(r"【瓶頸分類】[：:]\s*([A-Za-z0-9_]+)", raw_text)
        if m_cat:
            bottleneck = m_cat.group(1).strip().upper()

        m_cause = re.search(r"【根因剖析】[：:]\s*(.*?)(?=\n【|\Z)", raw_text, re.DOTALL)
        if m_cause:
            root_cause = m_cause.group(1).strip()

        m_ev = re.search(r"【關鍵日誌跡證】[：:]\s*(.*?)(?=\n【|\Z)", raw_text, re.DOTALL)
        if m_ev:
            for line in m_ev.group(1).splitlines():
                l_s = line.strip().lstrip("-*0123456789. ")
                if l_s:
                    evidence.append(l_s)

        m_rec = re.search(r"【具體修復建議】[：:]\s*(.*?)(?=\n【|\Z)", raw_text, re.DOTALL)
        if m_rec:
            for line in m_rec.group(1).splitlines():
                l_s = line.strip().lstrip("-*0123456789. ")
                if l_s:
                    recommendations.append(l_s)

        if not evidence:
            evidence.append("日誌中未擷取到突發性致命堆疊。")
        if not recommendations:
            recommendations.append("建議保持系統監控並持續觀察延遲分位數。")

        return DiagnosticResult(
            root_cause=root_cause,
            bottleneck_category=bottleneck,
            key_evidence=evidence,
            recommendations=recommendations,
            raw_model_response=raw_text,
            model_name=model_name,
            diagnosis_latency_ms=latency_ms,
        )


class SelfHealingAdvisor:
    """自癒應對策略顧問。"""

    @staticmethod
    def evaluate_action(
        incident: IncidentReport, diagnostic: DiagnosticResult
    ) -> SelfHealingAction:
        """依據事故與診斷結果評估合適的自癒策略。

        Args:
            incident: 異常事件報告。
            diagnostic: 診斷結果。

        Returns:
            SelfHealingAction: 建議之自癒動作。
        """
        cat = diagnostic.bottleneck_category.upper()
        inc_type = incident.incident_type

        if inc_type == IncidentType.ACTION_LOOP or "LOOP" in cat:
            return SelfHealingAction(
                strategy=SelfHealingStrategy.INJECT_REFLECTION,
                trigger_incident=inc_type,
                rationale="偵測到工具調用死循環，建議截斷當前鏈路並注入反思提示詞引導收斂。",
                parameters={"max_consecutive_tools": 1, "force_reflection": True},
            )
        if cat in ("PROMPT_OVERFLOW", "CONTEXT_ROT") or (
            inc_type == IncidentType.SLOW_TTFT_WARNING
            and int(incident.extra_metrics.get("prompt_build_ms", 0)) > 1000
        ):
            return SelfHealingAction(
                strategy=SelfHealingStrategy.CONTEXT_COMPRESSION,
                trigger_incident=inc_type,
                rationale="上下文長度過長或 Prefill 負載過重，建議執行短期記憶自動摘要與 Context 垃圾回收。",
                parameters={"compression_ratio": 0.5, "gc_short_term": True},
            )
        if cat == "CONTENT_FILTER":
            # 換模型才可能繞過，且現行備援池常與主模型同廠同系列，切換無實質意義。
            return SelfHealingAction(
                strategy=SelfHealingStrategy.NONE,
                trigger_incident=inc_type,
                rationale=(
                    "內容安全過濾為確定性失敗，重試或降級至同廠備援模型皆無效，"
                    "不建議自動處置；應檢視觸發該請求的內容來源。"
                ),
                parameters={},
            )
        if cat in ("GPU_LOCK", "EXTERNAL_API_TIMEOUT", "EVENT_LOOP_BLOCK") or inc_type in (
            IncidentType.FAILURE_120S,
            IncidentType.INFERENCE_FAILURE,
        ):
            return SelfHealingAction(
                strategy=SelfHealingStrategy.FALLBACK_MODEL,
                trigger_incident=inc_type,
                rationale="主力模型推論逾時或顯卡資源鎖定，建議下一輪暫時降級至輕量快速備援模型。",
                parameters={
                    "fallback_target": getattr(settings, "LIGHT_MODEL", "light"),
                    "cooldown_sec": 180,
                },
            )
        if inc_type == IncidentType.QUALITY_ANOMALY:
            return SelfHealingAction(
                strategy=SelfHealingStrategy.RESET_STATE,
                trigger_incident=inc_type,
                rationale="偵測到人設嚴重偏離或語義幻覺，建議重設當前會話狀態並重新加載 Soul 人設檔。",
                parameters={"reload_soul": True},
            )

        return SelfHealingAction(
            strategy=SelfHealingStrategy.NONE,
            trigger_incident=inc_type,
            rationale="指標在常態波動範圍內，維持現狀持續監控。",
        )


# ---------------------------------------------------------------------------
# 3. AuditService: 自我監控與延遲稽核統一領域服務
# ---------------------------------------------------------------------------

class AuditService:
    """自我監控與延遲稽核領域服務。"""

    def __init__(
        self,
        log_repository: LogRepository | None = None,
        analysis_client: CloudAnalysisClient | None = None,
        db_repository: AuditDBRepository | None = None,
    ) -> None:
        """初始化領域服務。"""
        self._log_repo = log_repository or LogRepository()
        self._analysis_client = analysis_client or CloudAnalysisClient()
        self._db_repo = db_repository or AuditDBRepository()
        self._last_alert_times: dict[IncidentType, float] = {}
        self._incident_history: list[IncidentReport] = []

    def set_analysis_client(self, analysis_client: CloudAnalysisClient) -> None:
        """由 composition root 注入雲端分析客戶端，解除 core 對 mod 層之反向依賴。"""
        self._analysis_client = analysis_client

    async def diagnose_incident(
        self, incident: IncidentReport, window_minutes: int | None = None
    ) -> tuple[DiagnosticResult, LogSnapshot]:
        """執行完整診斷流程：抓取 5 分鐘日誌並交付雲端搜尋模型剖析，同時持久化至 SQLite。"""
        win = window_minutes or DEFAULT_LOG_WINDOW_MINUTES
        logger.info(
            "AuditService: 開始針對事件 [%s] (耗時 %dms) 執行最近 %d 分鐘日誌診斷",
            incident.incident_type.value,
            incident.latency_ms,
            win,
        )

        # 1. 抓取過去 5 分鐘日誌快照
        snapshot = await self._log_repo.get_recent_snapshot(window_minutes=win)

        # 2. 調用雲端搜尋模型進行根因診斷
        diagnostic = await self._analysis_client.analyze_incident(incident, snapshot)

        # 3. 寫入 SQLite 資料庫持久化儲存（同步 sqlite3 寫入移至工作執行緒，避免阻塞事件迴圈）
        try:
            await asyncio.to_thread(
                self._db_repo.insert_audit_record, incident, diagnostic, snapshot
            )
        except Exception as db_err:
            logger.warning("AuditService: 寫入 audit.db 失敗: %s", db_err)

        # 4. 紀錄記憶體歷史
        self._incident_history.append(incident)
        if len(self._incident_history) > 100:
            self._incident_history.pop(0)

        # 5. 更新冷卻時間戳記
        self._last_alert_times[incident.incident_type] = time.monotonic()

        return diagnostic, snapshot

    def check_system_health(
        self,
        queue_size: int = 0,
        oldest_task_age_sec: float = 0.0,
        event_loop_lag_sec: float = 0.0,
        event_loop_lag_ms: float = 0.0,
        current_inference_latency_ms: float = 0.0,
    ) -> IncidentReport | None:
        """檢查系統即時健康度，判定是否達到預警或嚴重超時標準。"""
        now = time.monotonic()
        lag_sec = event_loop_lag_sec if event_loop_lag_sec > 0 else (event_loop_lag_ms / 1000.0)

        # 1. 重大推論超時 (> 120s)
        if current_inference_latency_ms >= DEFAULT_TIMEOUT_THRESHOLD_SEC * 1000:
            if self._should_alert(IncidentType.FAILURE_120S, now):
                return IncidentReport(
                    incident_type=IncidentType.FAILURE_120S,
                    severity=IncidentSeverity.CRITICAL,
                    latency_ms=int(current_inference_latency_ms),
                    trigger_reason=f"單次推論耗時達到 {current_inference_latency_ms / 1000:.1f} 秒，超過 120 秒硬性門檻",
                    extra_metrics={
                        "event_loop_lag_sec": lag_sec,
                        "queue_size": queue_size,
                    },
                )

        # 2. 系統阻塞 (Event Loop Lag >= 3.0s 或 任務佇列阻塞 >= 60.0s)
        if lag_sec >= 3.0:
            if self._should_alert(IncidentType.SYSTEM_BLOCKING, now):
                return IncidentReport(
                    incident_type=IncidentType.SYSTEM_BLOCKING,
                    severity=IncidentSeverity.CRITICAL,
                    latency_ms=int(lag_sec * 1000),
                    trigger_reason=f"事件迴圈嚴重延遲 (Lag={lag_sec:.2f}s > 3.0s)",
                    extra_metrics={
                        "event_loop_lag_sec": lag_sec,
                        "queue_size": queue_size,
                    },
                )

        if queue_size > 0 and oldest_task_age_sec >= 60.0:
            if self._should_alert(IncidentType.SYSTEM_BLOCKING, now):
                return IncidentReport(
                    incident_type=IncidentType.SYSTEM_BLOCKING,
                    severity=IncidentSeverity.CRITICAL,
                    latency_ms=int(oldest_task_age_sec * 1000),
                    trigger_reason=f"任務佇列阻塞: 最舊任務已排隊等待 {oldest_task_age_sec:.1f} 秒 (佇列大小: {queue_size})",
                    extra_metrics={
                        "event_loop_lag_sec": lag_sec,
                        "queue_size": queue_size,
                        "oldest_task_age_sec": oldest_task_age_sec,
                    },
                )

        if queue_size >= 10:
            if self._should_alert(IncidentType.SYSTEM_BLOCKING, now):
                return IncidentReport(
                    incident_type=IncidentType.SYSTEM_BLOCKING,
                    severity=IncidentSeverity.CRITICAL,
                    latency_ms=0,
                    trigger_reason=f"任務佇列排隊過多堆積 ({queue_size} 筆)",
                    extra_metrics={
                        "event_loop_lag_sec": lag_sec,
                        "queue_size": queue_size,
                    },
                )

        # 3. 慢速回覆警告 (> 90s)
        if current_inference_latency_ms >= DEFAULT_SLOW_THRESHOLD_SEC * 1000:
            if self._should_alert(IncidentType.SLOW_RESPONSE_90S, now):
                return IncidentReport(
                    incident_type=IncidentType.SLOW_RESPONSE_90S,
                    severity=IncidentSeverity.WARNING,
                    latency_ms=int(current_inference_latency_ms),
                    trigger_reason=f"單次回覆耗時達到 {current_inference_latency_ms / 1000:.1f} 秒，超過 90 秒預警門檻",
                    extra_metrics={
                        "event_loop_lag_ms": event_loop_lag_ms,
                        "queue_size": queue_size,
                    },
                )

        return None

    def should_suppress_alert(self, incident_type: IncidentType) -> bool:
        """判斷該類型警報是否處於防抖冷卻期內。

        Args:
            incident_type: 異常事件類型。

        Returns:
            bool: 若應抑制回傳 True，否則回傳 False。
        """
        last_time = self._last_alert_times.get(incident_type)
        if last_time is None:
            return False
        elapsed = time.monotonic() - last_time
        return elapsed < DEFAULT_ALERT_COOLDOWN_SEC

    def create_slow_response_incident(
        self,
        latency_ms: int,
        trace_id: str = "",
        channel_id: int = 0,
        user_name: str = "",
        raw_prompt: str = "",
        extra_metrics: dict[str, Any] | None = None,
    ) -> IncidentReport:
        """建立單筆回覆超過 90 秒慢速事件。"""
        return IncidentReport(
            incident_type=IncidentType.SLOW_RESPONSE_90S,
            severity=IncidentSeverity.WARNING,
            latency_ms=latency_ms,
            trigger_reason=f"單次回覆生成耗時達 {latency_ms / 1000.0:.1f} 秒，超過 90 秒警報門檻",
            trace_id=trace_id,
            channel_id=channel_id,
            user_name=user_name,
            raw_prompt=raw_prompt,
            extra_metrics=extra_metrics or {},
        )

    def create_failure_incident(
        self,
        latency_ms: int,
        error_msg: str = "",
        trace_id: str = "",
        channel_id: int = 0,
        user_name: str = "",
        raw_prompt: str = "",
        extra_metrics: dict[str, Any] | None = None,
    ) -> IncidentReport:
        """建立單筆回覆超過 120 秒失敗/超時事件。"""
        reason = f"單次回覆耗時達 {latency_ms / 1000.0:.1f} 秒觸發失敗"
        if error_msg:
            reason += f" (原因: {error_msg})"

        return IncidentReport(
            incident_type=IncidentType.FAILURE_120S,
            severity=IncidentSeverity.CRITICAL,
            latency_ms=latency_ms,
            trigger_reason=reason,
            trace_id=trace_id,
            channel_id=channel_id,
            user_name=user_name,
            raw_prompt=raw_prompt,
            extra_metrics=extra_metrics or {},
        )

    def create_inference_failure_incident(
        self,
        latency_ms: int,
        error_msg: str = "",
        trace_id: str = "",
        channel_id: int = 0,
        user_name: str = "",
        raw_prompt: str = "",
        task_id: str = "",
        extra_metrics: dict[str, Any] | None = None,
    ) -> IncidentReport:
        """建立大腦推論中斷、API 崩潰或重試徹底放棄事故。"""
        reason = f"大腦推論連線異常或多次重試後徹底放棄 (task: {task_id or '-'})"
        if error_msg:
            reason += f": {error_msg}"

        return IncidentReport(
            incident_type=IncidentType.INFERENCE_FAILURE,
            severity=IncidentSeverity.CRITICAL,
            latency_ms=latency_ms,
            trigger_reason=reason,
            trace_id=trace_id,
            channel_id=channel_id,
            user_name=user_name,
            task_id=task_id,
            raw_prompt=raw_prompt,
            extra_metrics=extra_metrics or {},
        )

    def create_manual_check_incident(
        self,
        channel_id: int = 0,
        user_name: str = "",
        extra_metrics: dict[str, Any] | None = None,
    ) -> IncidentReport:
        """建立手動體檢事件。"""
        return IncidentReport(
            incident_type=IncidentType.MANUAL_CHECK,
            severity=IncidentSeverity.INFO,
            latency_ms=0,
            trigger_reason=f"管理員 {user_name or 'System'} 手動觸發系統技術稽核體檢",
            trace_id=f"manual-{int(time.time())}",
            channel_id=channel_id,
            user_name=user_name,
            extra_metrics=extra_metrics or {},
        )

    def create_ttft_warning_incident(
        self,
        ttft_ms: int,
        trace_id: str = "",
        channel_id: int = 0,
        user_name: str = "",
        raw_prompt: str = "",
        extra_metrics: dict[str, Any] | None = None,
    ) -> IncidentReport:
        """建立首字延遲 (TTFT) 偏高預警事件。"""
        metrics = extra_metrics or {}
        metrics.setdefault("ttft_ms", ttft_ms)
        return IncidentReport(
            incident_type=IncidentType.SLOW_TTFT_WARNING,
            severity=IncidentSeverity.WARNING,
            latency_ms=ttft_ms,
            trigger_reason=f"首字生成延遲 (TTFT) 達 {ttft_ms} 毫秒 ({ttft_ms / 1000.0:.1f} 秒)，超過 5.0 秒預警門檻",
            trace_id=trace_id,
            channel_id=channel_id,
            user_name=user_name,
            raw_prompt=raw_prompt,
            extra_metrics=metrics,
        )

    def create_action_loop_incident(
        self,
        loop_actions: list[str],
        trace_id: str = "",
        channel_id: int = 0,
        user_name: str = "",
        raw_prompt: str = "",
        extra_metrics: dict[str, Any] | None = None,
    ) -> IncidentReport:
        """建立 Agent 工具調用死循環事故。"""
        metrics = extra_metrics or {}
        metrics.setdefault("loop_actions", loop_actions)
        return IncidentReport(
            incident_type=IncidentType.ACTION_LOOP,
            severity=IncidentSeverity.CRITICAL,
            latency_ms=0,
            trigger_reason=f"偵測到 Agent 重複調用相同動作/工具死循環 (最後動作: {loop_actions[-1] if loop_actions else 'N/A'})",
            trace_id=trace_id,
            channel_id=channel_id,
            user_name=user_name,
            raw_prompt=raw_prompt,
            extra_metrics=metrics,
        )

    def create_quality_anomaly_incident(
        self,
        quality_result: QualityScoreResult,
        user_msg: str = "",
        bot_response: str = "",
        trace_id: str = "",
        channel_id: int = 0,
        user_name: str = "",
    ) -> IncidentReport:
        """建立人設或對話質量異常事件。"""
        return IncidentReport(
            incident_type=IncidentType.QUALITY_ANOMALY,
            severity=IncidentSeverity.WARNING if quality_result.persona_score >= 2 else IncidentSeverity.CRITICAL,
            latency_ms=quality_result.audit_latency_ms,
            trigger_reason=f"對話品質異常 (人設評分: {quality_result.persona_score}/5, 忠實度: {quality_result.faithfulness_score}/5, 幻覺: {quality_result.is_hallucination}): {quality_result.critique}",
            trace_id=trace_id,
            channel_id=channel_id,
            user_name=user_name,
            raw_prompt=user_msg,
            extra_metrics={
                "persona_score": quality_result.persona_score,
                "faithfulness_score": quality_result.faithfulness_score,
                "is_hallucination": quality_result.is_hallucination,
                "critique": quality_result.critique,
                "advice": quality_result.improvement_advice,
                "response_snippet": bot_response[:100],
            },
        )

    def detect_action_loop(self, recent_actions: list[str]) -> bool:
        """偵測動作序列是否陷入死循環。

        Args:
            recent_actions: 最近執行的動作標識清單。

        Returns:
            bool: 若偵測到循環回傳 True。
        """
        if not recent_actions or len(recent_actions) < 3:
            return False

        # 1. 連續 3 次相同動作
        if recent_actions[-1] == recent_actions[-2] == recent_actions[-3]:
            return True

        # 2. 雙步驟週期循環 (A, B, A, B)
        if len(recent_actions) >= 4:
            if (
                recent_actions[-1] == recent_actions[-3]
                and recent_actions[-2] == recent_actions[-4]
                and recent_actions[-1] != recent_actions[-2]
            ):
                return True

        return False

    async def audit_quality_async(
        self, user_msg: str, bot_response: str, trace_id: str = ""
    ) -> QualityScoreResult:
        """非同步審查單次回應之品質與人設。

        Args:
            user_msg: 使用者輸入內容。
            bot_response: 助手回覆內容。
            trace_id: 追蹤 ID。

        Returns:
            QualityScoreResult: 審查成果。
        """
        return await self._analysis_client.audit_quality(user_msg, bot_response, trace_id)

    def evaluate_self_healing(
        self, incident: IncidentReport, diagnostic: DiagnosticResult
    ) -> SelfHealingAction:
        """依據事故與診斷結果產出自癒應對決策。

        Args:
            incident: 異常事件。
            diagnostic: 診斷結果。

        Returns:
            SelfHealingAction: 建議採取的自癒措施。
        """
        return SelfHealingAdvisor.evaluate_action(incident, diagnostic)

    def _should_alert(self, inc_type: IncidentType, current_time: float) -> bool:
        """判定特定事故類型是否已脫離告警冷卻期。"""
        last_time = self._last_alert_times.get(inc_type, 0.0)
        return (current_time - last_time) >= DEFAULT_ALERT_COOLDOWN_SEC

    def get_audit_records(
        self,
        limit: int = 50,
        offset: int = 0,
        severity: str | None = None,
        incident_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """查詢歷史稽核紀錄清單。"""
        return self._db_repo.get_audit_records(limit, offset, severity, incident_type)

    def get_audit_record_by_id(self, record_id: int) -> dict[str, Any] | None:
        """依 ID 取得單筆稽核紀錄詳細內容。"""
        return self._db_repo.get_audit_record_by_id(record_id)

    def get_kpi_summary(self) -> dict[str, Any]:
        """取得總體 KPI 指標統計。"""
        return self._db_repo.get_stats_summary()


# 全域單例
audit_service = AuditService()


def set_cloud_inference_client(cloud_client: CloudInferenceProtocol | Any | None) -> None:
    """由 composition root 注入雲端推論客戶端至全域稽核單例。"""
    global _DEFAULT_CLOUD_CLIENT
    _DEFAULT_CLOUD_CLIENT = cloud_client
    audit_service.set_analysis_client(CloudAnalysisClient(cloud_client))
