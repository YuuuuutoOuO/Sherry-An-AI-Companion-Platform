"""handlers/voice/recorder.py
==========================
語音錄製器與語句切分器 (Voice Recorder & Utterance Segmenter)。
支援 PCM 串流緩衝、VAD 語音活動切分、靜音超時切句與音訊佇列管理，
並相容 discord-ext-voice-recv 音訊接收 Sink 介面。
"""
from __future__ import annotations

import asyncio
import io
import logging
import time
from typing import Any, Callable
import wave

from handlers.voice.vad import VoiceActivityDetector

try:  # discord-ext-voice-recv 是選用依賴；未安裝時仍可載入既有語音工具。
    from discord.ext.voice_recv import AudioSink as _VoiceRecvAudioSink
except Exception:  # pragma: no cover - 由整合環境決定
    class _VoiceRecvAudioSink:  # type: ignore[no-redef]
        """未安裝 voice_recv 時的空基底類別。"""

logger = logging.getLogger(__name__)


def resample_pcm_to_16k_mono(
    pcm_bytes: bytes,
    *,
    source_rate: int = 48000,
    source_channels: int = 2,
    target_rate: int = 16000,
    sample_width: int = 2,
) -> bytes:
    """將 Discord 語音封包（通常 48kHz stereo）轉為 16kHz mono PCM。

    Discord voice_recv 預設輸出 48kHz、16-bit、stereo PCM；VAD 與 STT 模型
    則以 16kHz mono 為基準。若直接餵入原始資料，會同時造成音高/語速錯誤與
    位元組長度估算錯誤。此函式先下混為單聲道，再以整數降採樣或線性插值
    轉到目標取樣率。
    """
    if not pcm_bytes or sample_width != 2 or source_rate <= 0 or target_rate <= 0:
        return pcm_bytes

    try:
        import numpy as np
    except Exception:  # pragma: no cover - numpy 是必要依賴
        return pcm_bytes

    if len(pcm_bytes) % 2:
        pcm_bytes = pcm_bytes[:-1]
    samples = np.frombuffer(pcm_bytes, dtype="<i2")
    if samples.size == 0:
        return b""

    channels = max(1, int(source_channels))
    if channels > 1:
        usable = (samples.size // channels) * channels
        if usable == 0:
            return b""
        samples = samples[:usable].reshape(-1, channels).mean(axis=1)

    if source_rate != target_rate:
        if source_rate % target_rate == 0:
            factor = source_rate // target_rate
            usable = (samples.size // factor) * factor
            if usable == 0:
                return b""
            # 整數降採樣：以區塊平均做最基本的抗鋸齒，避免高頻直接折返。
            samples = samples[:usable].reshape(-1, factor).mean(axis=1)
        else:
            target_len = max(1, int(samples.size * target_rate / source_rate))
            source_x = np.linspace(0.0, 1.0, num=samples.size, endpoint=False)
            target_x = np.linspace(0.0, 1.0, num=target_len, endpoint=False)
            samples = np.interp(target_x, source_x, samples)

    clipped = np.clip(np.rint(samples), -32768, 32767).astype("<i2")
    return clipped.tobytes()


def pcm_to_wav(
    pcm_bytes: bytes,
    sample_rate: int = 16000,
    channels: int = 1,
    sample_width: int = 2,
) -> bytes:
    """將原始 PCM 音訊位元組包裝為標準 WAV 容器位元組。"""
    with io.BytesIO() as buf:
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(sample_width)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_bytes)
        return buf.getvalue()


class VoiceRecorder:
    """使用者語音串流收集與語句切分器。"""

    def __init__(
        self,
        vad: VoiceActivityDetector | None = None,
        *,
        sample_rate: int = 16000,
        channels: int = 1,
        sample_width: int = 2,
        max_buffer_seconds: float = 30.0,
        min_silence_ms: int = 500,
        on_speech_detected: Callable[[], None] | None = None,
    ) -> None:
        self.vad = vad or VoiceActivityDetector(min_silence_ms=min_silence_ms)
        self.sample_rate = sample_rate
        self.channels = channels
        self.sample_width = sample_width
        self.min_silence_ms = min_silence_ms
        self.on_speech_detected = on_speech_detected

        self.bytes_per_second = sample_rate * channels * sample_width
        self.max_buffer_bytes = int(max_buffer_seconds * self.bytes_per_second)

        # 佇列保存完整語句 (user_id, wav_bytes)
        self.queue: asyncio.Queue[tuple[int, bytes]] = asyncio.Queue()

        # 每位使用者之在途狀態
        self._user_buffers: dict[int, bytearray] = {}
        self._silence_ms: dict[int, float] = {}
        self._has_speech: dict[int, bool] = {}

    async def get_next_utterance(self) -> tuple[int, bytes] | None:
        """非同步等待並取得下一個已完成切分的使用者語句。"""
        try:
            return await self.queue.get()
        except asyncio.CancelledError:
            return None

    def write(self, user_id: int, data: bytes) -> None:
        """接收語音接收 Sink 寫入之 PCM 區塊並進行 VAD 語句切分。

        Args:
            user_id: 說話者之 Discord 使用者 ID。
            data: 16-bit Mono/Stereo PCM 音訊資料。
        """
        if not data:
            return

        # 計算區塊時長（毫秒）
        chunk_ms = (len(data) / self.bytes_per_second) * 1000.0

        is_voice = self.vad.is_speech(data)

        if user_id not in self._user_buffers:
            self._user_buffers[user_id] = bytearray()
            self._silence_ms[user_id] = 0.0
            self._has_speech[user_id] = False

        buf = self._user_buffers[user_id]

        if is_voice:
            # 只在「靜音 → 開口」的上升緣通知 barge-in，避免每個語音 chunk
            # 都重複取消播放任務。
            if not self._has_speech[user_id] and self.on_speech_detected:
                try:
                    self.on_speech_detected()
                except Exception as exc:
                    logger.debug("VoiceRecorder on_speech_detected failed: %s", exc)
            self._has_speech[user_id] = True
            self._silence_ms[user_id] = 0.0
            buf.extend(data)

            # 超出記憶體預算上限（預設 30 秒）時強制切句，防止記憶體無界膨脹
            if len(buf) >= self.max_buffer_bytes:
                self._flush_utterance(user_id)
        else:
            if self._has_speech[user_id]:
                # 已有語音開始，累積後續靜音長度
                buf.extend(data)
                self._silence_ms[user_id] += chunk_ms

                # 靜音時長達到門檻，判定語句結束
                if self._silence_ms[user_id] >= self.min_silence_ms:
                    self._flush_utterance(user_id)

    def _flush_utterance(self, user_id: int) -> None:
        """將使用者已錄妥之音訊包裝為 WAV 並排入佇列。"""
        buf = self._user_buffers.pop(user_id, None)
        self._silence_ms.pop(user_id, None)
        self._has_speech.pop(user_id, None)

        if not buf or len(buf) < (self.bytes_per_second * 0.2):
            # 極短音訊（< 0.2 秒）視為雜訊丟棄
            return

        wav_data = pcm_to_wav(
            bytes(buf),
            sample_rate=self.sample_rate,
            channels=self.channels,
            sample_width=self.sample_width,
        )
        self.queue.put_nowait((user_id, wav_data))

    def clear(self) -> None:
        """清空佇列與所有在途使用者暫存。"""
        self._user_buffers.clear()
        self._silence_ms.clear()
        self._has_speech.clear()

        while not self.queue.empty():
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except asyncio.QueueEmpty:
                break


class VoiceReceiverSink(_VoiceRecvAudioSink):  # type: ignore[misc]
    """相容 discord-ext-voice-recv 之音訊接收 Sink。"""

    def __init__(self, recorder: VoiceRecorder) -> None:
        self.recorder = recorder

    def wants_opus(self) -> bool:
        """要求 voice_recv 提供已解碼 PCM，而非 Opus 原始封包。"""
        return False

    def write(self, user: Any, data: Any) -> None:
        """接收底層 VoiceClient 或 voice_recv 傳遞之語音封包。"""
        user_id = getattr(user, "id", 0) if user else 0
        pcm = getattr(data, "pcm", data)
        if not isinstance(pcm, (bytes, bytearray)):
            return

        payload = bytes(pcm)
        raw_rate = getattr(data, "sampling_rate", None)
        raw_channels = getattr(data, "channels", None)
        raw_width = getattr(data, "sample_width", None)
        has_audio_meta = any(
            isinstance(value, int) for value in (raw_rate, raw_channels, raw_width)
        )
        if payload and has_audio_meta:
            source_rate = raw_rate if isinstance(raw_rate, int) else 48000
            source_channels = raw_channels if isinstance(raw_channels, int) else 2
            width = raw_width if isinstance(raw_width, int) else 2
            target_rate = int(getattr(self.recorder, "sample_rate", 16000))
            target_channels = int(getattr(self.recorder, "channels", 1))
            if source_rate != target_rate or source_channels != target_channels:
                payload = resample_pcm_to_16k_mono(
                    payload,
                    source_rate=source_rate,
                    source_channels=source_channels,
                    target_rate=target_rate,
                    sample_width=width,
                )
        if payload:
            self.recorder.write(user_id, payload)

    def cleanup(self) -> None:
        """清理資源。"""
        self.recorder.clear()
