"""core.sys_inspector.metrics
=============================
全景組件與模組效能度量收集器 (ComponentMetricsCollector)。
全面涵蓋日常核心工具 (SEARCH, STOCK, PIXIV, DRAW, MUSIC...)、
核心引擎 (BOT_BRAIN, MEMORY) 與專題外掛 (PAINTER, DEEP_SEARCH, SENTINEL...)。
"""

from __future__ import annotations

from collections import deque
import contextlib
from dataclasses import dataclass, field
import logging
import threading
import time
from typing import Any, AsyncGenerator, Generator

logger = logging.getLogger(__name__)

# 預設分類對照表
_DEFAULT_CATEGORIES: dict[str, str] = {
    "SEARCH": "core_tool",
    "STOCK": "core_tool",
    "PIXIV": "core_tool",
    "DRAW": "core_tool",
    "MUSIC": "core_tool",
    "SCHEDULE": "core_tool",
    "SYSTEM_INSPECT": "core_tool",
    "SUMMARY": "core_tool",
    "CHAT": "core_tool",
    "BOT_BRAIN": "core_engine",
    "MEMORY": "core_engine",
    "PAINTER": "mod_plugin",
    "DEEP_SEARCH": "mod_plugin",
    "MARKET_PLUGIN": "mod_plugin",
    "FEED_SENTINEL": "mod_plugin",
    "LIVESTREAM": "mod_plugin",
    "DND": "mod_plugin",
    "ROAST": "mod_plugin",
}


@dataclass
class ComponentStat:
    """單一組件/功能之效能統計指標（企業級 APM 規格）。"""

    name: str
    category: str = "core_tool"
    call_count: int = 0
    total_latency_ms: int = 0
    min_latency_ms: int = 0
    max_latency_ms: int = 0
    active_tasks: int = 0
    error_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    vram_mb: int = 0
    last_active_timestamp: float = 0.0
    last_error_timestamp: float = 0.0
    last_error_msg: str = ""
    _recent_latencies: deque[int] = field(default_factory=lambda: deque(maxlen=100))
    _recent_call_timestamps: deque[float] = field(default_factory=lambda: deque(maxlen=200))
    _recent_traces: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=20))

    @property
    def avg_latency_ms(self) -> float:
        """平均延遲 (毫秒)。"""
        if self.call_count <= 0:
            return 0.0
        return round(self.total_latency_ms / self.call_count, 1)

    def _get_percentile(self, p: float) -> float:
        """計算指定分位數延遲 (毫秒)。"""
        if not self._recent_latencies:
            return 0.0
        sorted_lat = sorted(self._recent_latencies)
        idx = int(len(sorted_lat) * p)
        idx = min(max(0, idx), len(sorted_lat) - 1)
        return float(sorted_lat[idx])

    @property
    def p50_latency_ms(self) -> float:
        """近 100 次呼叫之 P50 (中位數) 延遲 (毫秒)。"""
        return self._get_percentile(0.50)

    @property
    def p90_latency_ms(self) -> float:
        """近 100 次呼叫之 P90 延遲 (毫秒)。"""
        return self._get_percentile(0.90)

    @property
    def p95_latency_ms(self) -> float:
        """近 100 次呼叫之 P95 延遲 (毫秒)。"""
        return self._get_percentile(0.95)

    @property
    def p99_latency_ms(self) -> float:
        """近 100 次呼叫之 P99 延遲 (毫秒)。"""
        return self._get_percentile(0.99)

    @property
    def rpm(self) -> float:
        """近 60 秒內之請求頻率 (Requests Per Minute)。"""
        now = time.time()
        one_min_ago = now - 60.0
        recent_count = sum(1 for ts in self._recent_call_timestamps if ts >= one_min_ago)
        return float(recent_count)

    @property
    def health_score(self) -> float:
        """服務水準健康度 (0.0 ~ 100.0%)。"""
        if self.call_count <= 0:
            return 100.0
        score = ((self.call_count - self.error_count) / self.call_count) * 100.0
        return round(max(0.0, min(100.0, score)), 1)

    @property
    def health_level(self) -> str:
        """健康等級評定 (HEALTHY, DEGRADED, CRITICAL, IDLE)。"""
        if self.call_count <= 0:
            return "IDLE"
        score = self.health_score
        if score >= 98.0:
            return "HEALTHY"
        elif score >= 90.0:
            return "DEGRADED"
        return "CRITICAL"

    @property
    def tps(self) -> float:
        """Token 生成速率 (Tokens/sec)。"""
        if self.completion_tokens <= 0 or self.total_latency_ms <= 0:
            return 0.0
        sec = self.total_latency_ms / 1000.0
        return round(self.completion_tokens / sec, 1) if sec > 0 else 0.0

    @property
    def estimated_cost_usd(self) -> float:
        """預估 Token 成本 (美元，基準常規模型)。"""
        # Prompt $0.15/1M, Completion $0.60/1M
        cost = (self.prompt_tokens / 1_000_000.0 * 0.15) + (self.completion_tokens / 1_000_000.0 * 0.60)
        return round(cost, 5)

    def to_dict(self) -> dict[str, Any]:
        """轉為完整 APM 字典格式。"""
        # 取近 15 次延遲作為微型 Sparkline 走勢序列
        trend = list(self._recent_latencies)[-15:] if self._recent_latencies else []

        total_tok = self.prompt_tokens + self.completion_tokens
        avg_tok = round(total_tok / self.call_count, 1) if self.call_count > 0 else 0.0

        return {
            "name": self.name,
            "category": self.category,
            "call_count": self.call_count,
            "total_latency_ms": self.total_latency_ms,
            "min_latency_ms": self.min_latency_ms,
            "max_latency_ms": self.max_latency_ms,
            "avg_latency_ms": self.avg_latency_ms,
            "p50_latency_ms": self.p50_latency_ms,
            "p90_latency_ms": self.p90_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "p99_latency_ms": self.p99_latency_ms,
            "recent_latencies_trend": trend,
            "rpm": self.rpm,
            "health_score": self.health_score,
            "health_level": self.health_level,
            "active_tasks": self.active_tasks,
            "error_count": self.error_count,
            # 雙重映射：兼顧舊前端欄位 total_prompt_tokens 與標準 prompt_tokens
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_prompt_tokens": self.prompt_tokens,
            "total_completion_tokens": self.completion_tokens,
            "total_tokens": total_tok,
            "avg_tokens_per_call": avg_tok,
            "tps": self.tps,
            "estimated_cost_usd": self.estimated_cost_usd,
            "tokens": {
                "prompt": self.prompt_tokens,
                "completion": self.completion_tokens,
                "total": total_tok,
            },
            "vram_mb": self.vram_mb,
            "last_active_timestamp": self.last_active_timestamp,
            "last_error_timestamp": self.last_error_timestamp,
            "last_error_msg": self.last_error_msg,
            "recent_traces": list(self._recent_traces),
        }


class ComponentMetricsCollector:
    """全域組件效能度量收集器。"""

    _instance: ComponentMetricsCollector | None = None
    _lock = threading.Lock()

    def __init__(self, auto_bootstrap: bool = False) -> None:
        self._stats: dict[str, ComponentStat] = {}
        self._stat_lock = threading.Lock()
        self._bootstrapped = False
        # 初始化常見核心與外掛
        for name, cat in _DEFAULT_CATEGORIES.items():
            self._stats[name] = ComponentStat(name=name, category=cat)
        if auto_bootstrap:
            self.bootstrap_from_db()

    @classmethod
    def get_instance(cls) -> ComponentMetricsCollector:
        """取得單例實例。"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = ComponentMetricsCollector()
        return cls._instance

    def bootstrap_from_db(self, db_path: Any | None = None) -> int:
        """從 chat_history.db 中的 runtime_events 表格自動繼承歷史調用紀錄。

        Args:
            db_path: chat_history.db 路徑，預設取 settings.DATA_DIR / 'chat_history.db'。

        Returns:
            int: 成功還原之歷史事件筆數。
        """
        import json
        from pathlib import Path
        import sqlite3
        import settings

        target_path = Path(db_path) if db_path else (settings.DATA_DIR / "chat_history.db")
        if not target_path.exists():
            self._bootstrapped = True
            return 0

        try:
            conn = sqlite3.connect(str(target_path))
            cur = conn.cursor()
            rows = cur.execute(
                "SELECT event, latency_ms, data_json, trace_id, timestamp "
                "FROM runtime_events ORDER BY id ASC"
            ).fetchall()
            conn.close()
        except Exception as exc:
            logger.warning("[ComponentMetrics] 讀取歷史資料庫失敗: %s", exc)
            self._bootstrapped = True
            return 0

        count = 0
        for event, lat_raw, data_str, tid, ts in rows:
            try:
                data = json.loads(data_str) if data_str else {}
            except Exception:
                data = {}

            lat = float(lat_raw or 0)
            if lat < 0 or lat > 300000:
                lat = 1500.0

            extra = data.get("extra") or {}
            raw_in = data.get("raw_input") or ""
            reply_prev = data.get("reply_preview") or ""
            cot = data.get("cot_thought") or ""
            p_tok = len(raw_in) if raw_in else 0
            c_tok = len(reply_prev) + len(cot)
            if "prompt_length" in extra and isinstance(extra["prompt_length"], (int, float)):
                p_tok = int(extra["prompt_length"])
            if "completion_length" in extra and isinstance(extra["completion_length"], (int, float)):
                c_tok = int(extra["completion_length"])

            actual_tid = tid or data.get("trace_id", "")

            if event == "chat_reply":
                self.record_execution("CHAT", latency_ms=lat, prompt_tokens=p_tok, completion_tokens=c_tok, trace_id=actual_tid)
                self.record_execution("BOT_BRAIN", latency_ms=lat, prompt_tokens=p_tok, completion_tokens=c_tok, trace_id=actual_tid)
                count += 1
            elif event == "tool_exec":
                t_name = (extra.get("tool_name") or data.get("intent", "").replace("TOOL: ", "")).upper()
                if t_name:
                    self.record_execution(t_name, latency_ms=lat, prompt_tokens=p_tok, completion_tokens=c_tok, trace_id=actual_tid)
                    count += 1
            elif event == "inference_done":
                self.record_execution("BOT_BRAIN", latency_ms=lat, prompt_tokens=p_tok, completion_tokens=c_tok, trace_id=actual_tid)
                count += 1
            elif event == "stock_query":
                self.record_execution("STOCK", latency_ms=lat, trace_id=actual_tid)
                count += 1
            elif event == "search_rag":
                self.record_execution("SEARCH", latency_ms=lat, prompt_tokens=p_tok, completion_tokens=c_tok, trace_id=actual_tid)
                count += 1
            elif event == "image_search":
                self.record_execution("PIXIV", latency_ms=lat, trace_id=actual_tid)
                count += 1
            elif event == "draw":
                self.record_execution("DRAW", latency_ms=lat, trace_id=actual_tid)
                count += 1
            elif event == "music_action":
                self.record_execution("MUSIC", latency_ms=lat, trace_id=actual_tid)
                count += 1
            elif event == "schedule_action":
                self.record_execution("SCHEDULE", latency_ms=lat, trace_id=actual_tid)
                count += 1

        self._bootstrapped = True
        logger.info("[ComponentMetrics] 已成功從 %s 還原 %d 筆歷史執行度量", target_path.name, count)
        return count

    def get_component(self, name: str) -> ComponentStat:
        """取得指定組件之統計物件。"""
        with self._stat_lock:
            return self._get_or_create(name)

    def _get_or_create(self, name: str, category: str | None = None) -> ComponentStat:
        canonical = name.upper()
        if canonical not in self._stats:
            cat = category or _DEFAULT_CATEGORIES.get(canonical, "mod_plugin")
            self._stats[canonical] = ComponentStat(name=canonical, category=cat)
        return self._stats[canonical]

    def record_execution(
        self,
        name: str,
        latency_ms: int | float,
        success: bool = True,
        error_msg: str = "",
        category: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        trace_id: str = "",
    ) -> None:
        """記錄一次組件/工具之執行成果。"""
        now = time.time()
        lat_int = int(max(0, latency_ms))
        p_tok = int(max(0, prompt_tokens))
        c_tok = int(max(0, completion_tokens))

        with self._stat_lock:
            stat = self._get_or_create(name, category)
            stat.call_count += 1
            stat.total_latency_ms += lat_int

            # 更新極值
            if stat.min_latency_ms == 0 or lat_int < stat.min_latency_ms:
                stat.min_latency_ms = lat_int
            if lat_int > stat.max_latency_ms:
                stat.max_latency_ms = lat_int

            stat._recent_latencies.append(lat_int)
            stat._recent_call_timestamps.append(now)
            stat.last_active_timestamp = now

            if p_tok > 0:
                stat.prompt_tokens += p_tok
            if c_tok > 0:
                stat.completion_tokens += c_tok

            if not success:
                stat.error_count += 1
                stat.last_error_timestamp = now
                if error_msg:
                    stat.last_error_msg = error_msg

            # 記錄微型 trace (最多 20 筆)
            stat._recent_traces.append({
                "timestamp": now,
                "trace_id": trace_id or "-",
                "latency_ms": lat_int,
                "success": success,
                "prompt_tokens": p_tok,
                "completion_tokens": c_tok,
                "total_tokens": p_tok + c_tok,
                "error_msg": error_msg[:200] if error_msg else "",
            })

    @contextlib.contextmanager
    def track_active(
        self,
        name: str,
        category: str | None = None,
        trace_id: str = "",
    ) -> Generator[ComponentStat, None, None]:
        """同步脈絡管理器：記錄活躍任務數與自動累計。"""
        t0 = time.monotonic()
        with self._stat_lock:
            stat = self._get_or_create(name, category)
            stat.active_tasks += 1
        error_msg = ""
        success = True
        try:
            yield stat
        except Exception as exc:
            success = False
            error_msg = str(exc)
            raise
        finally:
            latency_ms = int((time.monotonic() - t0) * 1000)
            with self._stat_lock:
                stat.active_tasks = max(0, stat.active_tasks - 1)
            self.record_execution(
                name=name,
                latency_ms=latency_ms,
                success=success,
                error_msg=error_msg,
                category=category,
                trace_id=trace_id,
            )

    def record_tokens(self, name: str, prompt_tokens: int, completion_tokens: int) -> None:
        """記錄組件消耗的 LLM Token。"""
        with self._stat_lock:
            stat = self._get_or_create(name)
            stat.prompt_tokens += max(0, prompt_tokens)
            stat.completion_tokens += max(0, completion_tokens)
            stat.last_active_timestamp = time.time()

    def record_vram(self, name: str, vram_mb: int) -> None:
        """登記組件載入之模型顯存佔用 (MB)。"""
        with self._stat_lock:
            stat = self._get_or_create(name)
            stat.vram_mb = max(0, vram_mb)

    @contextlib.asynccontextmanager
    async def track(
        self,
        name: str,
        action: str = "",
        category: str | None = None,
        trace_id: str = "",
    ) -> AsyncGenerator[None, None]:
        """非同步上下文管理器：自動計時、計次與錯誤攔截。"""
        t0 = time.monotonic()
        with self._stat_lock:
            stat = self._get_or_create(name, category)
            stat.active_tasks += 1

        error_msg = ""
        success = True
        try:
            yield
        except Exception as exc:
            success = False
            error_msg = str(exc)
            raise
        finally:
            latency_ms = int((time.monotonic() - t0) * 1000)
            with self._stat_lock:
                stat.active_tasks = max(0, stat.active_tasks - 1)
            self.record_execution(
                name=name,
                latency_ms=latency_ms,
                success=success,
                error_msg=error_msg,
                category=category,
                trace_id=trace_id,
            )

    def get_summary(self) -> dict[str, Any]:
        """取得全域統計快照、排行與總計。"""
        # 若尚未啟動歷史資料庫繼承且為初次讀取，自動載入歷史數據
        if not getattr(self, "_bootstrapped", False):
            self.bootstrap_from_db()

        with self._stat_lock:
            components_dict = {k: v.to_dict() for k, v in self._stats.items()}
            # 蒐集所有近期的延遲採樣以計算全域 P95 與分佈
            all_recent_lats: list[int] = []
            for s in self._stats.values():
                all_recent_lats.extend(s._recent_latencies)

        all_items = list(components_dict.values())

        # 排行榜
        by_latency = sorted(
            [item for item in all_items if item["call_count"] > 0],
            key=lambda x: x["total_latency_ms"],
            reverse=True,
        )
        by_calls = sorted(
            [item for item in all_items if item["call_count"] > 0],
            key=lambda x: x["call_count"],
            reverse=True,
        )
        by_tokens = sorted(
            [item for item in all_items if item["tokens"]["total"] > 0],
            key=lambda x: x["tokens"]["total"],
            reverse=True,
        )
        by_errors = sorted(
            [item for item in all_items if item["error_count"] > 0],
            key=lambda x: x["error_count"],
            reverse=True,
        )

        total_calls = sum(x["call_count"] for x in all_items)
        total_tokens = sum(x["tokens"]["total"] for x in all_items)
        total_prompt_tokens = sum(x["tokens"]["prompt"] for x in all_items)
        total_completion_tokens = sum(x["tokens"]["completion"] for x in all_items)
        total_errors = sum(x["error_count"] for x in all_items)
        active_tasks = sum(x["active_tasks"] for x in all_items)
        total_rpm = round(sum(x.get("rpm", 0.0) for x in all_items), 1)

        # 計算全域健康度與 P95
        global_health_score = round(((total_calls - total_errors) / total_calls * 100.0), 1) if total_calls > 0 else 100.0
        global_p95_ms = 0.0
        global_avg_ms = 0.0
        if all_recent_lats:
            sorted_all = sorted(all_recent_lats)
            idx = int(len(sorted_all) * 0.95)
            idx = min(max(0, idx), len(sorted_all) - 1)
            global_p95_ms = float(sorted_all[idx])
            global_avg_ms = round(sum(sorted_all) / len(sorted_all), 1)

        active_components_count = len([x for x in all_items if x["call_count"] > 0])
        total_cost_usd = round(sum(x.get("estimated_cost_usd", 0.0) for x in all_items), 4)

        kpi = {
            "health_score": global_health_score,
            "total_calls": total_calls,
            "total_rpm": total_rpm,
            "global_p95_ms": global_p95_ms,
            "global_avg_ms": global_avg_ms,
            "total_tokens": total_tokens,
            "prompt_tokens": total_prompt_tokens,
            "completion_tokens": total_completion_tokens,
            "active_tasks": active_tasks,
            "active_components_count": active_components_count,
            "total_components_count": len(all_items),
            "total_cost_usd": total_cost_usd,
        }

        return {
            "status": "success",
            "timestamp": time.time(),
            "kpi": kpi,
            "components": components_dict,
            "rankings": {
                "by_latency": by_latency,
                "by_calls": by_calls,
                "by_tokens": by_tokens,
                "by_errors": by_errors,
            },
            "totals": {
                "total_calls": total_calls,
                "total_tokens": total_tokens,
                "total_errors": total_errors,
                "active_tasks": active_tasks,
            },
        }

    def reset(self, re_bootstrap: bool = False) -> None:
        """重設所有計數（測試或管理員維護用）。"""
        with self._stat_lock:
            self._stats.clear()
            for name, cat in _DEFAULT_CATEGORIES.items():
                self._stats[name] = ComponentStat(name=name, category=cat)
            self._bootstrapped = not re_bootstrap
        if re_bootstrap:
            self.bootstrap_from_db()


component_metrics = ComponentMetricsCollector.get_instance()
