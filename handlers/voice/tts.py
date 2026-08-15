import logging
import tempfile
import os

logger = logging.getLogger(__name__)

class TextToSpeech:
    def __init__(self) -> None:
        try:
            from TTS.api import TTS
            import settings
            # 確保模型路徑存在，或是依賴線上自動下載
            self._model = TTS(model_name=settings.VOICE_TTS_MODEL_NAME, progress_bar=False).to("cuda")
            self.language = settings.VOICE_TTS_LANGUAGE
            self.speaker_wav = settings.VOICE_TTS_SPEAKER_WAV
            
            if not os.path.exists(self.speaker_wav):
                logger.warning(f"TTS 聲音樣本不存在: {self.speaker_wav}")
                
            logger.info("TTS 模型加載成功")
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
        self._model = None
