import asyncio
import logging
import tempfile
import os

from huggingface_hub import snapshot_download
from transformers import pipeline
import torch

import settings

logger = logging.getLogger(__name__)

stt_logger = logging.getLogger("stt_thinking")
stt_logger.setLevel(logging.INFO)
stt_handler = logging.FileHandler(settings.STT_THINKING_LOG_PATH, encoding="utf-8")
stt_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
stt_logger.addHandler(stt_handler)
stt_logger.propagate = False

class SpeechToText:
    def __init__(self) -> None:
        try:
            model_dir = settings.VOICE_STT_MODEL
            
            # Check if directory exists and is not empty
            if not os.path.exists(model_dir) or not os.listdir(model_dir):
                logger.info(f"Downloading Nemotron 3.5 ASR to {model_dir}...")
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
                trust_remote_code=True
            )
            logger.info(f"STT 模型 {model_dir} 加載成功 (Device: {'cuda' if device == 0 else 'cpu'})")
        except Exception as e:
            logger.error(f"STT 模型加載失敗: {e}", exc_info=True)
            self._model = None

    async def transcribe(self, audio_bytes: bytes) -> str:
        if not self._model:
            stt_logger.warning("嘗試進行 STT，但模型未載入。")
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
            stt_logger.info(f"[SUCCESS] 語音大小: {len(audio_bytes)} bytes | 辨識結果: {text}")
            
            return text
        except Exception as e:
            # 記錄錯誤
            stt_logger.error(f"[ERROR] STT 辨識發生錯誤: {e}", exc_info=True)
            logger.error(f"STT 辨識失敗: {e}")
            return ""
        finally:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)

    def unload(self) -> None:
        self._model = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
