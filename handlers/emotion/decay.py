"""handlers/emotion/decay.py
========================
情感衰減模型 (Affect Decay Engine)。
基於半衰期指數衰減，使情感狀態隨時間平滑回歸中性基線。
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Mapping

from handlers.emotion.state import AffectState, clamp


DEFAULT_HALF_LIFE_HOURS: float = 4.0

# 預設各維度半衰期（小時）：
# 愉悅度 (Pleasure): 4 小時
# 喚起度 (Arousal): 2 小時（興奮/緊張退散較快）
# 支配感 (Dominance): 6 小時（掌控感/自信具較長慣性）
DEFAULT_AXIS_HALF_LIFE: dict[str, float] = {
    "pleasure": 4.0,
    "arousal": 2.0,
    "dominance": 6.0,
}


def compute_decay_factor(elapsed_seconds: float, half_life_hours: float) -> float:
    """計算指定時間差下的半衰期衰減係數。"""
    if elapsed_seconds <= 0:
        return 1.0
    half_life_seconds = max(60.0, float(half_life_hours) * 3600.0)
    # factor = 0.5 ^ (elapsed / half_life) = exp(-ln(2) * elapsed / half_life)
    return math.pow(0.5, elapsed_seconds / half_life_seconds)


def decay_affect_state(
    state: AffectState,
    *,
    now: datetime | None = None,
    half_life_hours: float | Mapping[str, float] | None = None,
) -> AffectState:
    """計算情感狀態經過時間推移後的衰減狀態。

    Args:
        state: 原始情感狀態實體。
        now: 當前時間（預設為 UTC 現在）。
        half_life_hours: 半衰期小時數或各軸對應表。若為 None 則使用預設配置。

    Returns:
        AffectState: 衰減後的全新狀態實體。
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    # 解析前次更新時間
    try:
        last_dt = datetime.fromisoformat(state.updated_at)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
    except Exception:
        last_dt = now

    elapsed = (now - last_dt).total_seconds()
    if elapsed <= 0:
        return AffectState(
            entity_type=state.entity_type,
            entity_id=state.entity_id,
            pleasure=state.pleasure,
            arousal=state.arousal,
            dominance=state.dominance,
            last_event=state.last_event,
            updated_at=state.updated_at,
        )

    # 取得各軸半衰期
    if isinstance(half_life_hours, Mapping):
        hl_p = half_life_hours.get("pleasure", DEFAULT_AXIS_HALF_LIFE["pleasure"])
        hl_a = half_life_hours.get("arousal", DEFAULT_AXIS_HALF_LIFE["arousal"])
        hl_d = half_life_hours.get("dominance", DEFAULT_AXIS_HALF_LIFE["dominance"])
    elif isinstance(half_life_hours, (int, float)):
        hl_p = hl_a = hl_d = float(half_life_hours)
    else:
        hl_p = DEFAULT_AXIS_HALF_LIFE["pleasure"]
        hl_a = DEFAULT_AXIS_HALF_LIFE["arousal"]
        hl_d = DEFAULT_AXIS_HALF_LIFE["dominance"]

    factor_p = compute_decay_factor(elapsed, hl_p)
    factor_a = compute_decay_factor(elapsed, hl_a)
    factor_d = compute_decay_factor(elapsed, hl_d)

    new_p = clamp(state.pleasure * factor_p)
    new_a = clamp(state.arousal * factor_a)
    new_d = clamp(state.dominance * factor_d)

    # 極微小殘值歸零（防止浮點數噪聲）
    if abs(new_p) < 0.005:
        new_p = 0.0
    if abs(new_a) < 0.005:
        new_a = 0.0
    if abs(new_d) < 0.005:
        new_d = 0.0

    return AffectState(
        entity_type=state.entity_type,
        entity_id=state.entity_id,
        pleasure=new_p,
        arousal=new_a,
        dominance=new_d,
        last_event=state.last_event,
        updated_at=now.isoformat(),
    )
