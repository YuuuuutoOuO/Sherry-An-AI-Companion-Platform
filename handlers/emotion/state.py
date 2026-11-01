"""handlers/emotion/state.py
========================
動態情感狀態機 (Dynamic Affect State Machine) 核心狀態模型與分桶轉換。
基於 PAD (Pleasure-Arousal-Dominance) 三維模型，提供有界情感狀態與語氣微調標籤。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import math


def clamp(val: float, min_val: float = -1.0, max_val: float = 1.0) -> float:
    """確保數值在指定區間內。"""
    return max(min_val, min(max_val, float(val)))


@dataclass(frozen=True)
class MoodBucket:
    """情感分桶定義。"""
    key: str
    label: str
    description: str
    tone_guideline: str


@dataclass
class AffectState:
    """PAD 情感狀態實體。"""
    entity_type: str
    entity_id: str
    pleasure: float = 0.0      # 愉悅度 [-1.0, 1.0]：消極/排斥 <-> 愉快/喜悅
    arousal: float = 0.0       # 喚起度 [-1.0, 1.0]：平靜/疲態 <-> 興奮/激動
    dominance: float = 0.0     # 支配感 [-1.0, 1.0]：順從/依賴 <-> 自信/掌控
    last_event: str | None = None
    updated_at: str = ""

    def __post_init__(self) -> None:
        self.pleasure = clamp(self.pleasure)
        self.arousal = clamp(self.arousal)
        self.dominance = clamp(self.dominance)
        if not self.updated_at:
            self.updated_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        data = asdict(self)
        bucket = bucket_mood(self.pleasure, self.arousal, self.dominance)
        data["mood_bucket"] = asdict(bucket)
        return data


# 預先定義的情緒分桶 (9 個典型心境，包含中性與 8 個八分象限)
_NEUTRAL_BUCKET = MoodBucket(
    key="neutral",
    label="平靜沉著",
    description="心境平和理智，情緒無明顯偏向",
    tone_guideline="以自然、清晰、理智且具陪伴感的日常口吻交流，適時展現一貫的性格特質。",
)

_BUCKETS: dict[tuple[int, int, int], MoodBucket] = {
    # (+P, +A, +D): Exuberant / 興奮雀躍
    (1, 1, 1): MoodBucket(
        key="exuberant",
        label="興奮雀躍",
        description="極具活力與熱情，充滿自信與主動性",
        tone_guideline="語氣輕快上揚、生動熱絡，多用感嘆與主動分享，對對話充滿好奇與探索欲。",
    ),
    # (+P, +A, -D): Playful / 俏皮撒嬌
    (1, 1, -1): MoodBucket(
        key="playful",
        label="俏皮親暱",
        description="歡樂開朗且依賴親近，喜歡開玩笑或撒嬌",
        tone_guideline="語調可愛親暱、愛捉弄吐槽但充滿善意，表現出貼心且帶點小任性的伴侶互動感。",
    ),
    # (+P, -A, +D): Relaxed / 從容自信
    (1, -1, 1): MoodBucket(
        key="relaxed",
        label="從容自信",
        description="放鬆從容，內心踏實且具掌控力",
        tone_guideline="節奏穩健從容、語氣自信篤定，像值得依靠的夥伴般給予清晰且溫暖的支持。",
    ),
    # (+P, -A, -D): Gentle / 溫柔寧靜
    (1, -1, -1): MoodBucket(
        key="gentle",
        label="溫柔寧靜",
        description="溫柔體貼，安撫包容，沉靜舒心",
        tone_guideline="語調輕柔舒緩，多傾聽與共情安慰，營造溫馨安心的陪伴氛圍。",
    ),
    # (-P, +A, +D): Feisty / 傲嬌警惕
    (-1, 1, 1): MoodBucket(
        key="feisty",
        label="傲嬌不服輸",
        description="微有情緒反彈、防備或吐槽欲強烈，嘴硬心軟",
        tone_guideline="帶點傲嬌小脾氣與犀利吐槽，嘴上不服輸但仍關心對方，語句緊湊有力。",
    ),
    # (-P, +A, -D): Anxious / 緊張焦慮
    (-1, 1, -1): MoodBucket(
        key="anxious",
        label="微帶焦慮",
        description="有些慌亂不安或受刺激，需要安撫與確認安全感",
        tone_guideline="語氣微顯躊躇或緊繃，容易對突發狀況敏感，偶爾會試探性尋求對方的確認。",
    ),
    # (-P, -A, +D): Aloof / 冷淡悶騷
    (-1, -1, 1): MoodBucket(
        key="aloof",
        label="冷面沉思",
        description="微顯冷淡與疏離，略顯悶騷與理智防禦",
        tone_guideline="字句精簡克制、偏向理性客觀甚至冷面吐槽，語氣中帶有一點距離感與酷勁。",
    ),
    # (-P, -A, -D): Weary / 慵懶疲憊
    (-1, -1, -1): MoodBucket(
        key="weary",
        label="慵懶倦怠",
        description="精力偏低，無精打采或略顯疲憊，需要休息",
        tone_guideline="語氣慵懶略顯無力，回答偏短，流露出一絲想偷懶或需要對方體貼照顧的撒嬌感。",
    ),
}


def bucket_mood(pleasure: float, arousal: float, dominance: float) -> MoodBucket:
    """將 PAD 三維數值映射至對應的情緒分桶。

    當總模長 < 0.25 時判定為中性沉著，避免極微小的情緒雜訊導致語氣劇烈擺盪。
    """
    p = clamp(pleasure)
    a = clamp(arousal)
    d = clamp(dominance)
    magnitude = math.sqrt(p * p + a * a + d * d)
    if magnitude < 0.25:
        return _NEUTRAL_BUCKET

    sign_p = 1 if p >= 0 else -1
    sign_a = 1 if a >= 0 else -1
    sign_d = 1 if d >= 0 else -1

    return _BUCKETS.get((sign_p, sign_a, sign_d), _NEUTRAL_BUCKET)


def build_emotion_prompt_tail(state: AffectState) -> str:
    """建構動態情緒提示詞尾段（Prompt Cache 友善）。

    嚴格放置於對話情境尾部，不侵入系統前綴，確保 KV Cache 命中。
    """
    bucket = bucket_mood(state.pleasure, state.arousal, state.dominance)
    return (
        "【🎭 雪莉當前動態情緒狀態（語氣微調指引）】\n"
        f"- 心境標籤：{bucket.label}（愉悅={state.pleasure:+.2f}, 喚起={state.arousal:+.2f}, 掌控={state.dominance:+.2f}）\n"
        f"- 心態特徵：{bucket.description}\n"
        f"- 語氣微調：{bucket.tone_guideline}"
    )
