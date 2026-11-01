"""handlers/voice/vad.py
=====================
語音活動偵測 (Voice Activity Detection, VAD)。
支援神經網路 (Silero VAD) 與強韌的 PCM RMS 能量雙重檢測機制，
在無 GPU 或缺少相依套件環境下自動無縫降級，確保語音切分與端到端測試穩定可用。
"""
from __future__ import annotations

import logging
import math
import struct
from typing import Any

import settings

logger = logging.getLogger(__name__)


class VoiceActivityDetector:
    """語音活動偵測器。"""

    def __init__(
        self,
        threshold: float = 0.5,
        min_silence_ms: int = 500,
        energy_threshold: float = 350.0,
    ) -> None:
        """初始化 VAD 偵測器。

        Args:
            threshold: 神經網路 VAD 機率門檻 (0.0 ~ 1.0)。
            min_silence_ms: 語句切分判定之最短靜音毫秒數。
            energy_threshold: 降級 RMS 能量門檻值（16-bit PCM）。
        """
        self.threshold = threshold
        self.min_silence_ms = min_silence_ms
        self.energy_threshold = energy_threshold
        self.model: Any = None
        self._get_speech_timestamps = None

        try:
            import torch

            repo = getattr(settings, "VOICE_VAD_REPO_OR_DIR", "snakers4/silero-vad")
            model_name = getattr(settings, "VOICE_VAD_MODEL_NAME", "silero_vad")
            self.model, utils = torch.hub.load(
                repo_or_dir=repo,
                model=model_name,
                force_reload=False,
                trust_repo=True,
            )
            self._get_speech_timestamps = utils[0]
            logger.info("Silero VAD 神經網路模型載入成功")
        except Exception as exc:
            logger.info("Silero VAD 載入略過或不可用，切換至 RMS 能量降級偵測模式: %s", exc)
            self.model = None

    @staticmethod
    def compute_rms(audio_chunk: bytes) -> float:
        """計算 16-bit Mono PCM 音訊區塊之 Root-Mean-Square (RMS) 能量。"""
        if not audio_chunk or len(audio_chunk) < 2:
            return 0.0

        sample_count = len(audio_chunk) // 2
        # 16-bit signed PCM
        fmt = f"<{sample_count}h"
        try:
            samples = struct.unpack(fmt, audio_chunk[: sample_count * 2])
        except struct.error:
            return 0.0

        sum_squares = sum(s * s for s in samples)
        return math.sqrt(sum_squares / sample_count)

    def is_speech(self, audio_chunk: bytes) -> bool:
        """評估輸入之 PCM 音訊區塊是否包含語音活動。"""
        if not audio_chunk or len(audio_chunk) < 32:
            return False

        # 優先嘗試神經網路模型
        if self.model is not None:
            try:
                import torch

                sample_count = len(audio_chunk) // 2
                samples = struct.unpack(f"<{sample_count}h", audio_chunk[: sample_count * 2])
                # 正規化至 [-1.0, 1.0] float32 tensor
                tensor = torch.tensor(samples, dtype=torch.float32) / 32768.0
                if tensor.dim() == 1:
                    tensor = tensor.unsqueeze(0)

                speech_prob = float(self.model(tensor, 16000).item())
                return speech_prob >= self.threshold
            except Exception as exc:
                logger.debug("Silero VAD 推論失敗，回退至 RMS 能量比對: %s", exc)

        # 回退至 RMS 能量門檻檢測
        rms = self.compute_rms(audio_chunk)
        return rms >= self.energy_threshold

    def reset(self) -> None:
        """重置內部 VAD 狀態。"""
        if self.model and hasattr(self.model, "reset_states"):
            try:
                self.model.reset_states()
            except Exception:
                pass
