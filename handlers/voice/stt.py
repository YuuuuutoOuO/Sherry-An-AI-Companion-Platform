import asyncio
import logging
import os
import tempfile
from typing import Any

import settings

logger = logging.getLogger(__name__)


class SpeechToText:
    """語音轉文字 (STT) 處理器。"""

    def __init__(self) -> None:
        """初始化 STT 模型。具備套件缺失保護 (ImportError) 與惰性載入。"""
        self._model: Any = None
        try:
            from huggingface_hub import snapshot_download
            from transformers import pipeline
            import torch

            model_dir = settings.VOICE_STT_MODEL

            # Check if directory exists and is not empty
            if not os.path.exists(model_dir) or not os.listdir(model_dir):
                logger.info("Downloading Nemotron 3.5 ASR to %s...", model_dir)
                os.makedirs(model_dir, exist_ok=True)
                snapshot_download(
                    repo_id=settings.VOICE_STT_REPO_ID,
                    local_dir=model_dir,
                )
                logger.info("Download complete.")

            device = -1
            if settings.VOICE_STT_DEVICE.lower() == "cuda" and torch.cuda.is_available():
                # 記憶體預算檢查：若剩餘顯存小於設定門檻則自動回退 CPU
                min_free_mb = int(getattr(settings, "VOICE_STT_MIN_FREE_VRAM_MB", 500))
                try:
                    free_bytes, total_bytes = torch.cuda.mem_get_info()
                    free_mb = free_bytes / (1024 * 1024)
                    if free_mb >= min_free_mb:
                        device = 0
                    else:
                        logger.warning("GPU 剩餘顯存不足 (%d MB < %d MB)，STT 回退至 CPU 執行", int(free_mb), min_free_mb)
                except Exception:
                    device = 0

            # Use generate_kwargs if specific prompt is needed, but pipeline handles basic initialization
            self._model = pipeline(
                "automatic-speech-recognition",
                model=model_dir,
                device=device,
                trust_remote_code=True,
            )
            self._is_warmed_up = False
            logger.info("STT 模型 %s 加載成功 (Device: %s)", model_dir, "cuda" if device == 0 else "cpu")
        except ImportError as e:
            logger.warning("STT 相依套件未安裝 (transformers/torch/huggingface_hub): %s", e)
            self._model = None
            self._is_warmed_up = False
        except Exception as e:
            logger.error("STT 模型加載失敗: %s", e, exc_info=True)
            self._model = None
            self._is_warmed_up = False

    def warmup(self) -> bool:
        """暖機常駐：預熱模型以避免首輪語音指令延遲。"""
        if not self._model or self._is_warmed_up:
            return self._is_warmed_up

        try:
            import numpy as np
            # 以 0.1 秒靜音陣列進行一次推論預熱
            dummy_pcm = np.zeros(1600, dtype=np.float32)
            self._model(dummy_pcm)
            self._is_warmed_up = True
            logger.info("STT 模型暖機常駐完成")
            return True
        except Exception as exc:
            logger.debug("STT 暖機跳過: %s", exc)
            return False

    async def transcribe(self, audio_bytes: bytes) -> str:
        """將音訊位元組資料轉譯為文字。

        Args:
            audio_bytes: 音訊檔案的二進位位元組資料（WAV 格式）。

        Returns:
            str: 轉譯出的文字字串，若模型未載入或失敗則回傳空字串。
        """
        if not self._model:
            logger.warning("嘗試進行 STT，但模型未載入。")
            return ""

        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as f:
            f.write(audio_bytes)
            tmp_name = f.name

        try:
            lang = settings.VOICE_STT_LANGUAGE
            if lang == "zh":
                lang = "zh-CN"

            # Using transformers pipeline in thread pool to prevent event loop blocking
            def _do_transcribe() -> str:
                res = self._model(tmp_name, generate_kwargs={"language": lang})
                return str(res.get("text", "")).strip()

            text = await asyncio.to_thread(_do_transcribe)

            # 記錄成功辨識的文字
            logger.info("[SUCCESS] 語音大小: %d bytes | 辨識結果: %s", len(audio_bytes), text)

            return text
        except Exception as e:
            # 記錄錯誤
            logger.error("[ERROR] STT 辨識發生錯誤: %s", e, exc_info=True)
            return ""
        finally:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)

    def unload(self) -> None:
        """卸載 STT 模型並釋放 GPU 快取顯存。"""
        self._model = None
        self._is_warmed_up = False
        try:
            import gc
            import torch
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except (ImportError, Exception):
            pass
