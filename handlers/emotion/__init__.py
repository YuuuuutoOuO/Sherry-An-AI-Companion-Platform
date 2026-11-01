"""handlers/emotion
================
動態情感狀態機 (Dynamic Affect State Machine)。
"""
from handlers.emotion.decay import decay_affect_state, DEFAULT_AXIS_HALF_LIFE
from handlers.emotion.service import EmotionService, get_emotion_service
from handlers.emotion.state import (
    AffectState,
    MoodBucket,
    bucket_mood,
    build_emotion_prompt_tail,
    clamp,
)

__all__ = [
    "AffectState",
    "MoodBucket",
    "clamp",
    "bucket_mood",
    "build_emotion_prompt_tail",
    "decay_affect_state",
    "DEFAULT_AXIS_HALF_LIFE",
    "EmotionService",
    "get_emotion_service",
]
