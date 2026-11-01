import logging
import tempfile
import os

logger = logging.getLogger(__name__)

class TextToSpeech:
    def __init__(self) -> None:
        try:
            from TTS.api import TTS
            import torch
            import settings

            # 依設備可用性自動決定執行端，避免在無 GPU 環境拋出異常
            req_device = getattr(settings, "VOICE_TTS_DEVICE", "cuda").lower()
            device = "cuda" if (req_device == "cuda" and torch.cuda.is_available()) else "cpu"

            # 確保模型路徑存在，或是依賴線上自動下載
            self._model = TTS(model_name=settings.VOICE_TTS_MODEL_NAME, progress_bar=False).to(device)
            self.language = settings.VOICE_TTS_LANGUAGE
            self.speaker_wav = settings.VOICE_TTS_SPEAKER_WAV
            self.device = device
            
            if not os.path.exists(self.speaker_wav):
                logger.warning(f"TTS 聲音樣本不存在: {self.speaker_wav}")
                
            logger.info("TTS 模型加載成功 (Device: %s)", device)
        except ImportError:
            logger.error("TTS 套件未安裝")
            self._model = None
        except Exception as e:
            logger.error(f"TTS 模型加載失敗: {e}")
            self._model = None

    async def synthesize(self, text: str) -> bytes:
        if not self._model:
            return b""
            
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as f:
            tmp_name = f.name
            
        try:
            def _do_synthesize():
                self._model.tts_to_file(text=text, speaker_wav=self.speaker_wav, language=self.language, file_path=tmp_name)
                with open(tmp_name, "rb") as f:
                    return f.read()

            import asyncio
            return await asyncio.to_thread(_do_synthesize)
        except Exception as e:
            logger.error(f"TTS 合成失敗: {e}")
            return b""
        finally:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)

    def unload(self) -> None:
        """卸載 TTS 語音模型並釋放 GPU 快取顯存。"""
        self._model = None
        try:
            import gc
            import torch
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except (ImportError, Exception):
            pass
