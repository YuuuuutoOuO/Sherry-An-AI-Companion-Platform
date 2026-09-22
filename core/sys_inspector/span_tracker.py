"""core.sys_inspector.span_tracker
=================================
全鏈路微觀耗時追蹤與遙測工具 (SpanTracker)。

提供零侵入、微秒級之分段計時器，支援上下文管理器與非同步包裝，
精確捕捉 RAG 檢索、Prompt 構建、TTFT (首字延遲)、模型推論與工具調用之分段耗時。
"""

from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
import time
from typing import AsyncIterator, Iterator

from core.sys_inspector.models import MicroSpanMetrics


class SpanTracker:
    """單次請求微觀耗時收集器。"""

    def __init__(self) -> None:
        """初始化追蹤器。"""
        self._start_time: float = time.monotonic()
        self._spans: dict[str, int] = {}
        self.rag_fetch_ms: int = 0
        self.prompt_build_ms: int = 0
        self.ttft_ms: int = 0
        self.inference_ms: int = 0
        self.tool_ms: int = 0
        self.cache_hit: bool = False
        self.token_count: int = 0

    @contextmanager
    def time_span(self, name: str) -> Iterator[None]:
        """同步區塊計時上下文管理器。

        Args:
            name: Span 名稱。
        """
        start = time.monotonic()
        try:
            yield
        finally:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            self._spans[name] = elapsed_ms
            self._record_standard_span(name, elapsed_ms)

    @asynccontextmanager
    async def time_span_async(self, name: str) -> AsyncIterator[None]:
        """非同步區塊計時上下文管理器。

        Args:
            name: Span 名稱。
        """
        start = time.monotonic()
        try:
            yield
        finally:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            self._spans[name] = elapsed_ms
            self._record_standard_span(name, elapsed_ms)

    def record_ttft(self, ttft_ms: int) -> None:
        """紀錄首字生成延遲 (Time to First Token)。

        Args:
            ttft_ms: 首字延遲（毫秒）。
        """
        self.ttft_ms = max(0, ttft_ms)
        self._spans["ttft"] = self.ttft_ms

    def record_cache_hit(self, hit: bool) -> None:
        """紀錄 Prompt 快取命中狀態。

        Args:
            hit: 是否命中快取。
        """
        self.cache_hit = hit

    def record_token_count(self, count: int) -> None:
        """紀錄生成 Token 數。

        Args:
            count: Token 數量。
        """
        self.token_count = max(0, count)

    def _record_standard_span(self, name: str, elapsed_ms: int) -> None:
        """將常用 span 名稱映射到標準欄位。"""
        if name in ("rag", "rag_fetch", "memory_search"):
            self.rag_fetch_ms += elapsed_ms
        elif name in ("prompt", "prompt_build", "context_assembly"):
            self.prompt_build_ms += elapsed_ms
        elif name in ("inference", "llm_inference", "model_generate"):
            self.inference_ms += elapsed_ms
        elif name in ("tool", "tool_call", "tools"):
            self.tool_ms += elapsed_ms

    def export_metrics(self) -> MicroSpanMetrics:
        """匯出強型別 MicroSpanMetrics 物件。

        Returns:
            MicroSpanMetrics: 彙整後之微觀耗時度量結構。
        """
        total = int((time.monotonic() - self._start_time) * 1000)
        return MicroSpanMetrics(
            rag_fetch_ms=self.rag_fetch_ms,
            prompt_build_ms=self.prompt_build_ms,
            ttft_ms=self.ttft_ms,
            inference_ms=self.inference_ms,
            tool_ms=self.tool_ms,
            total_latency_ms=total,
            cache_hit=self.cache_hit,
            token_count=self.token_count,
            spans=dict(self._spans),
        )
