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

            device = 0 if settings.VOICE_STT_DEVICE.lower() == "cuda" and torch.cuda.is_available() else -1

            # Use generate_kwargs if specific prompt is needed, but pipeline handles basic initialization
            self._model = pipeline(
                "automatic-speech-recognition",
                model=model_dir,
                device=device,
                trust_remote_code=True,
            )
            logger.info("STT 模型 %s 加載成功 (Device: %s)", model_dir, "cuda" if device == 0 else "cpu")
        except ImportError as e:
            logger.warning("STT 相依套件未安裝 (transformers/torch/huggingface_hub): %s", e)
            self._model = None
        except Exception as e:
            logger.error("STT 模型加載失敗: %s", e, exc_info=True)
            self._model = None

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
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except (ImportError, Exception):
            pass
