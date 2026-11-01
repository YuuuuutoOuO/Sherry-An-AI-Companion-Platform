"""tests/unit/test_voice_pipeline_r24.py
======================================
R24: 多模態 M2（語音管線現代化）單元測試。
驗證：
1. VoiceActivityDetector (VAD) RMS 能量計算與語音/靜音判斷；
2. VoiceRecorder 緩衝寫入、語句切分 (Utterance Segmentation)、WAV 包裝與佇列排入；
3. VoiceReceiverSink 相容介面；
4. TTS 設備選擇（移除硬編 cuda）、CPU Fallback 與顯存釋放 (unload)；
5. STT 暖機常駐機制 (warmup) 與顯存預算防護。
"""
from __future__ import annotations

import io
import math
import struct
from unittest.mock import MagicMock
import wave

from handlers.voice.recorder import (
    VoiceReceiverSink,
    VoiceRecorder,
    resample_pcm_to_16k_mono,
)
from handlers.voice.stt import SpeechToText
from handlers.voice.tts import TextToSpeech
from handlers.voice.vad import VoiceActivityDetector
import settings


# ---------------------------------------------------------------------------
# 輔助函式：產生合成 16kHz 16-bit Mono PCM 音訊
# ---------------------------------------------------------------------------

def make_pcm_sine(freq: float = 440.0, duration_seconds: float = 0.1, amplitude: float = 10000.0) -> bytes:
    """產生指定頻率與振幅之正弦波 PCM 音訊。"""
    sample_rate = 16000
    sample_count = int(sample_rate * duration_seconds)
    samples = [
        int(amplitude * math.sin(2 * math.pi * freq * i / sample_rate))
        for i in range(sample_count)
    ]
    return struct.pack(f"<{len(samples)}h", *samples)


def make_pcm_silence(duration_seconds: float = 0.1) -> bytes:
    """產生完全靜音（全零）PCM 音訊。"""
    sample_rate = 16000
    sample_count = int(sample_rate * duration_seconds)
    return b"\x00\x00" * sample_count


# ---------------------------------------------------------------------------
# 1. VAD 檢測機制
# ---------------------------------------------------------------------------

def test_vad_rms_computation_and_speech_detection():
    vad = VoiceActivityDetector(energy_threshold=350.0)

    # 完全靜音
    silence = make_pcm_silence(0.1)
    assert vad.compute_rms(silence) == 0.0
    assert not vad.is_speech(silence)

    # 響亮語音 (振幅 10000) -> RMS 約 7071 > 350
    speech = make_pcm_sine(freq=440.0, duration_seconds=0.1, amplitude=10000.0)
    rms = vad.compute_rms(speech)
    assert rms > 5000.0
    assert vad.is_speech(speech)

    # 極微小噪聲 (振幅 50) -> RMS 約 35 < 350
    noise = make_pcm_sine(freq=440.0, duration_seconds=0.1, amplitude=50.0)
    assert not vad.is_speech(noise)


# ---------------------------------------------------------------------------
# 2. VoiceRecorder 與語句切分
# ---------------------------------------------------------------------------

def test_voice_recorder_segmentation_and_wav_generation():
    vad = VoiceActivityDetector(energy_threshold=350.0, min_silence_ms=300)
    recorder = VoiceRecorder(vad=vad, min_silence_ms=300)

    user_id = 9988

    # 1. 寫入 0.4 秒語音 (4 個 100ms chunk)
    speech_chunk = make_pcm_sine(duration_seconds=0.1, amplitude=8000.0)
    for _ in range(4):
        recorder.write(user_id, speech_chunk)

    # 此時仍在說話，佇列中尚未產出完整語句
    assert recorder.queue.empty()

    # 2. 寫入 0.35 秒靜音 (超過 min_silence_ms 300ms)
    silence_chunk = make_pcm_silence(duration_seconds=0.1)
    for _ in range(4):
        recorder.write(user_id, silence_chunk)

    # 3. 驗證已成功產出切分語句
    assert not recorder.queue.empty()
    queued_user, wav_bytes = recorder.queue.get_nowait()
    assert queued_user == user_id

    # 4. 驗證產出的音訊為合法 WAV 格式
    assert wav_bytes.startswith(b"RIFF")
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 16000
        # 總時長應約為 0.4s + 0.3s = 0.7s (約 11200 frames)
        assert wf.getnframes() > 6000


def test_voice_recorder_buffer_cap():
    # 限制最大緩衝 0.5 秒 (8000 bytes)
    recorder = VoiceRecorder(max_buffer_seconds=0.5, min_silence_ms=1000)
    chunk = make_pcm_sine(duration_seconds=0.2, amplitude=8000.0)

    user_id = 777
    # 寫入 4 次 (0.8 秒)，超過 0.5 秒上限
    for _ in range(4):
        recorder.write(user_id, chunk)

    # 達到上限時自動強制切句排入佇列，防止記憶體無界膨脹
    assert not recorder.queue.empty()


def test_voice_receiver_sink_delegation():
    recorder = MagicMock(spec=VoiceRecorder)
    sink = VoiceReceiverSink(recorder)

    user = MagicMock(id=1234)
    data = MagicMock(pcm=b"\x01\x02\x03\x04")

    sink.write(user, data)
    recorder.write.assert_called_once_with(1234, b"\x01\x02\x03\x04")

    sink.cleanup()
    recorder.clear.assert_called_once()


def test_resample_48k_stereo_to_16k_mono():
    """Discord 48kHz stereo PCM 必須先下混再降採樣，不可當成 16kHz mono。"""
    source_rate = 48000
    duration = 0.1
    frame_count = int(source_rate * duration)
    samples = [
        int(8000 * math.sin(2 * math.pi * 440 * i / source_rate))
        for i in range(frame_count)
    ]
    stereo = b"".join(struct.pack("<hh", s, s) for s in samples)

    converted = resample_pcm_to_16k_mono(
        stereo,
        source_rate=source_rate,
        source_channels=2,
        target_rate=16000,
    )
    assert len(converted) == int(16000 * duration) * 2

    recorder = VoiceRecorder(vad=VoiceActivityDetector(energy_threshold=350.0))
    sink = VoiceReceiverSink(recorder)
    data = MagicMock(
        pcm=stereo,
        sampling_rate=source_rate,
        channels=2,
        sample_width=2,
    )
    sink.write(MagicMock(id=4321), data)
    assert len(recorder._user_buffers[4321]) == len(converted)


def test_voice_recorder_notifies_barge_in_on_speech_start():
    calls: list[str] = []
    recorder = VoiceRecorder(
        vad=VoiceActivityDetector(energy_threshold=350.0),
        on_speech_detected=lambda: calls.append("speech"),
    )
    speech = make_pcm_sine(duration_seconds=0.1, amplitude=8000.0)
    recorder.write(1001, speech)
    recorder.write(1001, speech)
    # 只在靜音→開口的上升緣通知一次，不能每個 chunk 都重複打斷。
    assert calls == ["speech"]


# ---------------------------------------------------------------------------
# 3. TTS 設備 Fallback 與顯存釋放
# ---------------------------------------------------------------------------

def test_tts_initialization_cpu_fallback(monkeypatch):
    monkeypatch.setattr(settings, "VOICE_TTS_DEVICE", "cpu")
    tts = TextToSpeech()
    # 確保不會因為沒有 CUDA 而引發未捕獲例外
    # 若環境未安裝 TTS 套件，_model 為 None；若有安裝，device 為 cpu
    if tts._model is not None:
        assert tts.device == "cpu"

    tts.unload()
    assert tts._model is None


# ---------------------------------------------------------------------------
# 4. STT 暖機常駐與顯存預算
# ---------------------------------------------------------------------------

def test_stt_warmup_and_unload():
    stt = SpeechToText()
    # 測試 warmup 安全調用
    stt.warmup()

    # 測試 unload 釋放顯存
    stt.unload()
    assert stt._model is None
    assert stt._is_warmed_up is False
