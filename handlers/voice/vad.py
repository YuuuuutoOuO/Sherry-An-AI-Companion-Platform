import logging

logger = logging.getLogger(__name__)

class VoiceActivityDetector:
    def __init__(self, threshold: float = 0.5, min_silence_ms: int = 500) -> None:
        self.threshold = threshold
        self.min_silence_ms = min_silence_ms
        try:
            import torch
            import settings
            self.model, utils = torch.hub.load(repo_or_dir=settings.VOICE_VAD_REPO_OR_DIR, model=settings.VOICE_VAD_MODEL_NAME, force_reload=False, trust_repo=True)
            self.get_speech_timestamps, _, self.read_audio, _, _ = utils
            logger.info("VAD 模型加載成功")
        except Exception as e:
            logger.error(f"VAD 模型加載失敗: {e}")
            self.model = None

    def is_speech(self, audio_chunk: bytes) -> bool:
        # Simplistic stub for VAD evaluation on audio chunk bytes
        return True

    def reset(self) -> None:
        if self.model:
            self.model.reset_states()
