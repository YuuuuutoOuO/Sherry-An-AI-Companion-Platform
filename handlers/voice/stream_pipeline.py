"""handlers/voice/stream_pipeline.py
==================================
多模態 M3：低延遲串流語音對話管線 (Low-Latency Streaming Voice Pipeline)。
提供：
1. 句子級即時切分器 (SentenceStreamSplitter)：在 LLM 串流輸出時及時切句；
2. 串流即刻 TTS (Streaming TTS)：首句生成即刻進入合成，大幅降低首音延遲 (TTFA)；
3. 使用者插話打斷 (Barge-in)：偵測到使用者開口時立即中斷播放並清空在途任務；
4. 端到端延遲量測與 P50/P95 統計追蹤。
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, AsyncIterator, Awaitable, Callable

logger = logging.getLogger(__name__)

# 一級斷句標點（強制切句）
_PRIMARY_DELIMITERS = re.compile(r"([。！？!?\n]+)")
# 二級斷句標點（長度達標時切句）
_SECONDARY_DELIMITERS = re.compile(r"([，,；;…—]+)")


class SentenceStreamSplitter:
    """句子級串流文本切分器。

    在 LLM 串流輸出 token 時，依標點符號與語意邊界動態切分出完整可發音句子，
    使 TTS 不必等待全部生成完成即可開始首句合成。
    """

    def __init__(self, min_sentence_len: int = 4, max_sentence_len: int = 50) -> None:
        self.min_sentence_len = min_sentence_len
        self.max_sentence_len = max_sentence_len
        self._buffer = ""

    def feed(self, chunk: str) -> list[str]:
        """餵入新抵達的 token 區塊，回傳已完成的句子列表。"""
        if not chunk:
            return []

        self._buffer += chunk
        sentences: list[str] = []

        while True:
            matched = False

            # 1. 優先搜尋一級斷句標點（句號/驚嘆號/問號/換行）
            for m in _PRIMARY_DELIMITERS.finditer(self._buffer):
                idx = m.end()
                candidate = self._buffer[:idx].strip()
                if len(candidate) >= self.min_sentence_len:
                    sentences.append(candidate)
                    self._buffer = self._buffer[idx:].lstrip()
                    matched = True
                    break

            if matched:
                continue

            # 2. 若緩衝長度達 12 字，搜尋二級斷句標點（逗號/分號等）
            if len(self._buffer) >= 12:
                for m in _SECONDARY_DELIMITERS.finditer(self._buffer):
                    idx = m.end()
                    candidate = self._buffer[:idx].strip()
                    if len(candidate) >= self.min_sentence_len:
                        sentences.append(candidate)
                        self._buffer = self._buffer[idx:].lstrip()
                        matched = True
                        break

            if matched:
                continue

            # 3. 若長度超過上限仍無標點，強制在上限處切句
            if len(self._buffer) >= self.max_sentence_len:
                candidate = self._buffer[: self.max_sentence_len].strip()
                self._buffer = self._buffer[self.max_sentence_len :].lstrip()
                sentences.append(candidate)
                continue

            break

        return sentences

    def flush(self) -> str | None:
        """串流結束時倒出剩餘的殘留字串。"""
        residual = self._buffer.strip()
        self._buffer = ""
        return residual if residual else None


class BargeInController:
    """使用者插話打斷控制器 (Barge-in)。

    當雪莉正在播音或準備播音時，若 VAD 偵測到使用者開始說話，
    立即中斷播放器並取消在途 TTS 任務。
    """

    def __init__(
        self,
        stop_playback_callback: Callable[[], None] | None = None,
    ) -> None:
        self.stop_playback_callback = stop_playback_callback
        self.is_speaking = False
        self._current_tasks: set[asyncio.Task[Any]] = set()
        self._barge_in_count = 0

    def register_task(self, task: asyncio.Task[Any]) -> None:
        """註冊正在執行之 TTS 或推論任務。"""
        self._current_tasks.add(task)
        task.add_done_callback(self._current_tasks.discard)

    def on_user_speech_detected(self) -> bool:
        """當偵測到使用者語音時觸發打斷。

        Returns:
            bool: 若成功觸發打斷（原先正在播音或有在途任務）回傳 True。
        """
        interrupted = False
        if self.is_speaking or bool(self._current_tasks):
            interrupted = True
            self._barge_in_count += 1
            logger.info("⚡ [BARGE-IN] 偵測到使用者開口，立即打斷雪莉的語音播放與在途任務！")

            # 1. 停止底層 Discord 語音播放
            if self.stop_playback_callback:
                try:
                    self.stop_playback_callback()
                except Exception as exc:
                    logger.debug("stop_playback_callback failed: %s", exc)

            # 2. 取消所有在途任務
            for task in list(self._current_tasks):
                if not task.done():
                    task.cancel()
            self._current_tasks.clear()

            self.is_speaking = False

        return interrupted


class LowLatencyVoicePipeline:
    """低延遲端到端語音串流管線。"""

    def __init__(
        self,
        barge_in_controller: BargeInController | None = None,
    ) -> None:
        self.barge_in = barge_in_controller or BargeInController()
        self.splitter = SentenceStreamSplitter()
        self._latencies: list[float] = []

    async def run_streaming_conversation(
        self,
        token_stream: AsyncIterator[str],
        synthesize_func: Callable[[str], Awaitable[bytes]],
        on_audio_ready: Callable[[bytes, str], Awaitable[None]],
        *,
        start_time: float | None = None,
    ) -> dict[str, Any]:
        """執行串流對話：邊收 token、邊切句、即刻 TTS、按序送出。

        Args:
            token_stream: LLM 產生的文字 chunk 串流。
            synthesize_func: 句子合成音訊函式 (sentence -> audio_bytes)。
            on_audio_ready: 音訊備妥回呼 (audio_bytes, sentence_text)。
            start_time: 說話結束時間戳記（用於量測端到端 TTFA 延遲）。

        Returns:
            dict: 本次對話的指標統計（包含句子數、TTFA 延遲等）。
        """
        t0 = start_time if start_time is not None else time.time()
        ttfa_ms: float | None = None
        sentence_count = 0

        self.barge_in.is_speaking = True
        current_task = asyncio.current_task()
        if current_task is not None:
            self.barge_in.register_task(current_task)

        try:
            async for token in token_stream:
                if not self.barge_in.is_speaking:
                    break
                sentences = self.splitter.feed(token)
                for sentence in sentences:
                    if not self.barge_in.is_speaking:
                        break
                    sentence_count += 1
                    audio = await synthesize_func(sentence)
                    if not audio:
                        continue
                    if ttfa_ms is None:
                        ttfa_ms = (time.time() - t0) * 1000.0
                        self._latencies.append(ttfa_ms)

                    await on_audio_ready(audio, sentence)
                    if not self.barge_in.is_speaking:
                        break

            # 結尾殘留處理
            if self.barge_in.is_speaking:
                residual = self.splitter.flush()
                if residual:
                    sentence_count += 1
                    audio = await synthesize_func(residual)
                    if audio:
                        if ttfa_ms is None:
                            ttfa_ms = (time.time() - t0) * 1000.0
                            self._latencies.append(ttfa_ms)
                        await on_audio_ready(audio, residual)

        except asyncio.CancelledError:
            logger.info("Streaming conversation cancelled (barge-in).")
        finally:
            self.barge_in.is_speaking = False

        return {
            "sentence_count": sentence_count,
            "ttfa_ms": round(ttfa_ms, 2) if ttfa_ms is not None else 0.0,
            "total_elapsed_ms": round((time.time() - t0) * 1000.0, 2),
        }

    def get_latency_stats(self) -> dict[str, float]:
        """計算歷史端到端首音延遲 P50 與 P95 指標。"""
        if not self._latencies:
            return {"count": 0, "p50_ms": 0.0, "p95_ms": 0.0, "min_ms": 0.0, "max_ms": 0.0}

        sorted_latencies = sorted(self._latencies)
        n = len(sorted_latencies)
        idx_p50 = int(n * 0.50)
        idx_p95 = min(n - 1, int(n * 0.95))

        return {
            "count": float(n),
            "p50_ms": round(sorted_latencies[idx_p50], 2),
            "p95_ms": round(sorted_latencies[idx_p95], 2),
            "min_ms": round(sorted_latencies[0], 2),
            "max_ms": round(sorted_latencies[-1], 2),
        }
