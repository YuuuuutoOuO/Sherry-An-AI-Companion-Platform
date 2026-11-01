"""handlers/care/surfer.py
======================
雪莉「網活仔」主動衝浪與 @Tag 分享引擎（CareSurfer）及 Port 8765 實況廣播台（SurferBroadcaster）。

職責：
1. 嚴格單一測試頻道沙盒（sandbox_channel_id）：主動分享與 @Tag 強制限制於指定測試頻道，絕不外溢。
2. 混合資訊源整合：零 GPU 非同步預抓 RSS 新聞（巴哈 GNN、科技新報等）＋ WebMonitor 依雪莉近期興趣檢索。
3. 記憶去重：比對 self_memory.db 防止雪莉重複分享看過的文章。
4. 群友興趣匹配：比對使用者記憶（user_memory.json），命中群友喜好時優先 @Tag 互動。
5. 單次 LLM 極速推論：產出雪莉內心 OS、個人觀點記憶、衍生新興趣、與網活仔貼文。
6. SurferBroadcaster：為 Port 8765 提供即時 SSE 事件流與狀態快照，供前端虛擬瀏覽器演出。
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime as dt, timezone, timedelta
from pathlib import Path
from typing import Any

import settings
from handlers.bot_brain import InferenceTask
from handlers.memory.self_memory import (
    add_self_episode,
    get_active_interests,
    is_article_seen,
    list_reading_logs,
    list_self_episodes,
    record_reading_item,
    record_self_action,
)
from handlers.social.brain import (
    BrainIntent,
    BrainResult,
    ConversationContext,
    build_conversation_context,
    think,
)
from handlers.social.persona import build_dynamic_persona
from handlers.social.utils import fetch_recent_script
from handlers.spark.clients.rss_client import RssClient
from handlers.spark.clients.web_monitor_client import WebMonitorClient
from .repository import CareRepository, CareSurferConfig, get_care_repository, is_within_active_hours

logger = logging.getLogger(__name__)

_TZ_TW = timezone(timedelta(hours=8))


def _usable_generated_text(text: str) -> bool:
    """生成結果是否可用；空白或已知罐頭台詞一律視為失敗。"""
    cleaned = clean_cyber_bubble(str(text or ""))
    if not cleaned:
        return False
    from handlers.social.autonomy.contracts import looks_like_canned_fallback

    return not looks_like_canned_fallback(cleaned)

# 即時摸魚狀態感知與 Token 白飯計數
_tokens_eaten_today: int = 18600
_tasks_run_today: int = 15
_last_channel_chatter_time: dict[str, float] = {}
_last_bot_speak_time: dict[str, float] = {}
_last_human_chatter_time: dict[str, float] = {}

# 動態心智需求指標池（Sims / 電子雞風格生命循環：隨冷場遞增加速無聊度與好奇心）
_mind_state: dict[str, Any] = {
    "boredom_level": 28.0,       # 0~100% 寂寞無聊度 (冷場時間越長隨時間遞增加速，>= 80% 觸發主動冒泡)
    "curiosity_level": 32.0,     # 0~100% 探索好奇欲 (自發思考與求知慾望，>= 75% 觸發看文或內心自省)
    "boredom_threshold": 80.0,
    "curiosity_threshold": 75.0,
    "energy_level": 90.0,        # 0~100% 作息活力值 (活躍時段 85~95%，半夜靜音時段 20%)
    "last_thought": "",
    "last_decay_time": time.time(),
    # 兼容舊版指標
    "social_satiety": 72.0,
    "curiosity_satiety": 68.0,
    "social_threshold": 30.0,
}

# 心智狀態持久化：重啟後不歸零，離線期間最多補算 10 分鐘的寂寞與好奇。
_MIND_STATE_FILENAME = "mind_state.json"
_MIND_STATE_KEYS = (
    "social_satiety",
    "curiosity_satiety",
    "boredom_level",
    "curiosity_level",
    "last_thought",
)
_MIND_OFFLINE_CATCHUP_MINUTES = 10.0
_MIND_OFFLINE_BASE_BOREDOM = 1.8   # normal 節奏基準：重啟補算不套冷場加速
_MIND_OFFLINE_BASE_CURIOSITY = 2.0
_mind_state_loaded = False
_mind_persistence_enabled = False
_last_mind_persist_at = 0.0


def enable_mind_persistence(enabled: bool = True) -> None:
    """由機器人啟動流程開啟持久化；單元測試預設不會污染執行期存檔。"""
    global _mind_persistence_enabled
    _mind_persistence_enabled = bool(enabled)


def _mind_state_path() -> Path:
    return Path(settings.DATA_DIR) / _MIND_STATE_FILENAME


def _persist_mind_state(force: bool = False) -> None:
    """把心智狀態寫回磁碟；預設 10 秒節流，避免每則訊息都寫檔。"""
    global _last_mind_persist_at
    if not force and not _mind_persistence_enabled:
        return
    now = time.time()
    if not force and now - _last_mind_persist_at < 10.0:
        return
    _last_mind_persist_at = now
    try:
        path = _mind_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {key: _mind_state.get(key) for key in _MIND_STATE_KEYS}
        payload["saved_at"] = now
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception as exc:
        logger.debug("[CareSurfer] 心智狀態存檔失敗: %s", exc)


def load_mind_state(force: bool = False) -> bool:
    """把上次執行的心智狀態接回來（開機呼叫一次即可）。

    Returns:
        是否真的讀到既有存檔。
    """
    global _mind_state_loaded
    if _mind_state_loaded and not force:
        return False
    _mind_state_loaded = True

    path = _mind_state_path()
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("[CareSurfer] 心智狀態讀取失敗: %s", exc)
        return False
    if not isinstance(data, dict):
        return False

    for key in _MIND_STATE_KEYS:
        if key not in data:
            continue
        if key == "last_thought":
            _mind_state[key] = str(data.get(key) or "")[:120]
            continue
        try:
            _mind_state[key] = max(0.0, min(100.0, float(data[key])))
        except (TypeError, ValueError):
            continue

    # 統一只以飽和度為真相來源，避免兩組數值互相矛盾。
    _mind_state["boredom_level"] = round(100.0 - float(_mind_state.get("social_satiety", 72.0)), 1)
    _mind_state["curiosity_level"] = round(100.0 - float(_mind_state.get("curiosity_satiety", 68.0)), 1)

    try:
        saved_at = float(data.get("saved_at") or 0.0)
    except (TypeError, ValueError):
        saved_at = 0.0
    if saved_at > 0:
        offline_min = min(
            max(0.0, (time.time() - saved_at) / 60.0),
            _MIND_OFFLINE_CATCHUP_MINUTES,
        )
        if offline_min > 0.5:
            _mind_state["social_satiety"] = round(
                max(0.0, float(_mind_state["social_satiety"]) - _MIND_OFFLINE_BASE_BOREDOM * offline_min), 1
            )
            _mind_state["curiosity_satiety"] = round(
                max(0.0, float(_mind_state["curiosity_satiety"]) - _MIND_OFFLINE_BASE_CURIOSITY * offline_min), 1
            )
            _mind_state["boredom_level"] = round(100.0 - _mind_state["social_satiety"], 1)
            _mind_state["curiosity_level"] = round(100.0 - _mind_state["curiosity_satiety"], 1)

    _mind_state["last_decay_time"] = time.time()
    return True


def get_mind_state(active_start: int | None = None, active_end: int | None = None) -> dict[str, Any]:
    """取得心智狀態池即時數值與活躍狀態。"""
    if active_start is None or active_end is None:
        try:
            cfg = get_care_repository().get_surfer_config()
            s = cfg.active_hour_start
            e = cfg.active_hour_end
        except Exception:
            s, e = 9, 23
    else:
        s, e = active_start, active_end

    now_dt = dt.now(_TZ_TW)
    is_active = is_within_active_hours(now_dt.hour, s, e)
    energy = 90.0 if is_active else 20.0
    _mind_state["energy_level"] = energy

    # 雙向同步：若外部直接設定 social_satiety / curiosity_satiety
    if "social_satiety" in _mind_state:
        soc_satiety = round(max(0.0, min(100.0, float(_mind_state["social_satiety"]))), 1)
        boredom = round(100.0 - soc_satiety, 1)
        _mind_state["boredom_level"] = boredom
    else:
        boredom = round(max(0.0, min(100.0, float(_mind_state.get("boredom_level", 28.0)))), 1)
        soc_satiety = round(100.0 - boredom, 1)
        _mind_state["social_satiety"] = soc_satiety

    if "curiosity_satiety" in _mind_state:
        cur_satiety = round(max(0.0, min(100.0, float(_mind_state["curiosity_satiety"]))), 1)
        curiosity = round(100.0 - cur_satiety, 1)
        _mind_state["curiosity_level"] = curiosity
    else:
        curiosity = round(max(0.0, min(100.0, float(_mind_state.get("curiosity_level", 32.0)))), 1)
        cur_satiety = round(100.0 - curiosity, 1)
        _mind_state["curiosity_satiety"] = cur_satiety

    boredom_th = float(_mind_state.get("boredom_threshold", 80.0))
    curiosity_th = float(_mind_state.get("curiosity_threshold", 75.0))

    return {
        "boredom_level": boredom,
        "curiosity_level": curiosity,
        "boredom_threshold": boredom_th,
        "curiosity_threshold": curiosity_th,
        "social_satiety": soc_satiety,
        "curiosity_satiety": cur_satiety,
        "energy_level": round(float(energy), 1),
        "social_threshold": float(_mind_state.get("social_threshold", 30.0)),
        "is_active_hours": is_active,
        "last_thought": str(_mind_state.get("last_thought", "")),
    }


def accumulate_mind_state(
    silence_minutes: float = 0.0,
    force_boost: float | None = None,
    pace: str = "normal",
    is_active_hours: bool = True,
) -> dict[str, Any]:
    """心智需求遞增累積（無人講話/冷場時間越長，遞增加速度越快；半夜降速抑制作息）。"""
    if force_boost is not None:
        b_inc = float(force_boost)
        c_inc = float(force_boost) * 1.15
    else:
        # 心智節奏基準增長率 (每分鐘增長幅度)
        if pace == "fast":
            base_boredom = 3.2
            base_curiosity = 3.0
        elif pace == "chill":
            base_boredom = 0.8
            base_curiosity = 1.0
        else:  # "normal"
            base_boredom = 1.8
            base_curiosity = 2.0

        # 冷場遞增加速度（冷場每 15 分鐘加成 1.0x，最高 2.5x 爆表加速度）
        accel = 1.0 + min(2.5, max(0.0, float(silence_minutes)) / 15.0)
        b_inc = base_boredom * accel
        c_inc = base_curiosity * (1.0 + min(1.5, max(0.0, float(silence_minutes)) / 30.0))

        if not is_active_hours:
            # 半夜靜音時段：寂寞無聊度大幅減速 80%，上限壓在 85%（避免半夜突然爆表破防想講話）
            b_inc *= 0.2
            c_inc *= 0.5

    cur_soc = float(_mind_state.get("social_satiety", 100.0 - float(_mind_state.get("boredom_level", 28.0))))
    cur_cur = float(_mind_state.get("curiosity_satiety", 100.0 - float(_mind_state.get("curiosity_level", 32.0))))

    new_soc = max(0.0, cur_soc - b_inc)
    if not is_active_hours and force_boost is None:
        new_soc = max(15.0, new_soc)
    new_cur = max(0.0, cur_cur - c_inc)

    _mind_state["social_satiety"] = round(new_soc, 1)
    _mind_state["curiosity_satiety"] = round(new_cur, 1)
    _mind_state["boredom_level"] = round(100.0 - _mind_state["social_satiety"], 1)
    _mind_state["curiosity_level"] = round(100.0 - _mind_state["curiosity_satiety"], 1)
    _mind_state["last_decay_time"] = time.time()
    _persist_mind_state()
    return get_mind_state()


def decay_mind_state(force_drop: float | None = None) -> dict[str, Any]:
    """心智指標遞減（相容舊介面，實質調用需求遞增）。"""
    return accumulate_mind_state(force_boost=force_drop)


def replenish_mind_state(
    social_boost: float = 0.0,
    curiosity_boost: float = 0.0,
    thought: str | None = None,
    replenish_social: bool = False,
    replenish_curiosity: bool = False,
    reset_boredom: bool = False,
    reset_curiosity: bool = False,
) -> dict[str, Any]:
    """回補心智指標（重設/釋放寂寞與好奇，並記錄最新思考）。"""
    soc_b = 95.0 if (replenish_social or reset_boredom) and social_boost <= 0 else social_boost
    cur_b = 95.0 if (replenish_curiosity or reset_curiosity) and curiosity_boost <= 0 else curiosity_boost

    if soc_b > 0:
        cur_s = float(_mind_state.get("social_satiety", 70.0))
        _mind_state["social_satiety"] = max(0.0, min(100.0, cur_s + soc_b))
        _mind_state["boredom_level"] = round(100.0 - _mind_state["social_satiety"], 1)
    if cur_b > 0:
        cur_c = float(_mind_state.get("curiosity_satiety", 70.0))
        _mind_state["curiosity_satiety"] = max(0.0, min(100.0, cur_c + cur_b))
        _mind_state["curiosity_level"] = round(100.0 - _mind_state["curiosity_satiety"], 1)

    if thought:
        _mind_state["last_thought"] = str(thought)[:120]

    _persist_mind_state()
    return get_mind_state()


def update_chatter_timestamp(channel_id: int | str, ts: float | None = None) -> None:
    """更新頻道最後發言時間戳，群友熱聊時適度緩解雪莉無聊感。"""
    stamped = time.time() if ts is None else float(ts)
    cid_str = str(channel_id)
    _last_channel_chatter_time[cid_str] = stamped
    _last_human_chatter_time[cid_str] = stamped
    cur_s = float(_mind_state.get("social_satiety", 70.0))
    _mind_state["social_satiety"] = min(100.0, cur_s + 5.0)
    _mind_state["boredom_level"] = round(100.0 - _mind_state["social_satiety"], 1)
    _persist_mind_state()


def get_last_human_chatter_timestamp(channel_id: int | str) -> float:
    """取得頻道人類最後發言時間戳。"""
    return _last_human_chatter_time.get(str(channel_id), 0.0)


async def is_channel_unanswered(
    bot: Any,
    channel_id: int | str,
    *,
    heartbeat_seconds: float = 0.0,
) -> bool:
    """檢查指定頻道最新一則訊息是否由雪莉（機器人）發送且尚無人類回覆（未回覆自言自語防護）。

    - 若 Discord 頻道歷史可讀取：檢查最新一則訊息是否為雪莉或機器人所發送。
    - 若無法讀取歷史訊息或離線：降級以記憶體時間戳比較（最後發言者是否為雪莉）。
    - ``heartbeat_seconds`` > 0 時，若雪莉最後一則訊息已超過這個秒數，
      視為「可以再主動開一次話題」的心跳發文，不再判定為未回覆。
      這樣即使沒人回話，仍能依巡航間隔維持最低限度的存在感，
      又不會在短時間內連續自言自語洗版。
    """
    cid_str = str(channel_id or "").strip()
    if not cid_str or cid_str == "0":
        return False

    # 先用記憶體時間戳做便宜判定：人類已回覆就不必再打 Discord API 讀歷史。
    # 記憶體沒有紀錄時仍以 Discord 歷史為準（重啟後或舊訊息）。
    bot_ts = get_last_bot_speak_timestamp(cid_str)
    human_ts = get_last_human_chatter_timestamp(cid_str)
    if bot_ts > 0 and human_ts >= bot_ts:
        return False

    channel = None
    if bot:
        try:
            if hasattr(bot, "get_channel") and cid_str.isdigit():
                channel = bot.get_channel(int(cid_str))
            if not channel and hasattr(bot, "fetch_channel") and cid_str.isdigit():
                channel = await bot.fetch_channel(int(cid_str))
        except Exception as exc:
            logger.debug("[CareSurfer] 取得頻道物件失敗: %s", exc)

    if channel and hasattr(channel, "history"):
        try:
            bot_user_id = getattr(getattr(bot, "user", None), "id", None)
            history_iter = channel.history(limit=10)
            messages = []
            if hasattr(history_iter, "__aiter__"):
                async for m in history_iter:
                    messages.append(m)
            elif hasattr(history_iter, "__iter__"):
                messages = list(history_iter)

            if messages:
                # 依時間由新至舊檢查（messages[0] 為最新訊息）
                for m in messages:
                    author = getattr(m, "author", None)
                    if author is None:
                        continue
                    author_id = getattr(author, "id", None)
                    is_bot = bool(getattr(author, "bot", False))

                    if bot_user_id is not None:
                        # 若最新發言者是雪莉本人，且其後無人類回覆
                        if author_id == bot_user_id:
                            if heartbeat_seconds > 0:
                                try:
                                    age = float(time.time() - m.created_at.timestamp())
                                except Exception:
                                    age = 0.0
                                if age >= heartbeat_seconds:
                                    return False
                            return True
                        # 若最新發言者是人類，非未回覆狀態
                        if not is_bot:
                            return False
                        # 若是其他機器人（例如音樂機器人/Webhook），略過並繼續往前回溯
                    else:
                        # bot.user 尚未初始化時的相容降級
                        if is_bot:
                            return True
                        return False
        except Exception as exc:
            logger.debug("[CareSurfer] 讀取頻道歷史訊息判定未回覆狀態失敗: %s", exc)

    # 降級依據記憶體內時間戳
    if bot_ts > 0 and bot_ts > human_ts:
        if heartbeat_seconds > 0 and (time.time() - bot_ts) >= heartbeat_seconds:
            return False
        return True
    return False


def update_bot_speak_timestamp(
    channel_id: int | str,
    ts: float | None = None,
    *,
    target_author_id: int | str = "",
    keep_participants: bool = False,
) -> None:
    """更新雪莉在頻道最後發言時間戳，發言後寂寞無聊度直接清零重置。

    同時通知對話鏈追蹤器：從這一刻起，只有「接著她這句話回」的人類訊息
    才算延續對話，而不是任何在時間窗內講話的人都算。
    """
    stamped = time.time() if ts is None else float(ts)
    cid_str = str(channel_id)
    _last_bot_speak_time[cid_str] = stamped
    _last_channel_chatter_time[cid_str] = stamped
    _mind_state["social_satiety"] = 100.0
    _mind_state["boredom_level"] = 0.0
    _persist_mind_state()
    try:
        from handlers.social.autonomy.dialogue import get_dialogue_tracker

        get_dialogue_tracker().note_bot_speak(
            channel_id,
            target_author_id=target_author_id,
            now=stamped,
            keep_participants=keep_participants,
        )
    except Exception:  # 對話鏈只是輔助判定，壞了也不能影響發言
        pass


def get_last_bot_speak_timestamp(channel_id: int | str) -> float:
    """取得雪莉在頻道最後發言時間戳。"""
    return _last_bot_speak_time.get(str(channel_id), 0.0)


def add_token_consumption(tokens: int) -> None:
    """累加今日推論消耗的 Token 數量。"""
    global _tokens_eaten_today, _tasks_run_today
    _tokens_eaten_today += max(0, tokens)
    _tasks_run_today += 1


def _gather_sherry_live_stats(bot: Any = None, channel_id: int | str = 0) -> dict[str, Any]:
    """收集雪莉當前即時真實狀態（時段、白飯 Token、任務次數、冷場時間、熱聊窗口狀態）。"""
    global _tokens_eaten_today, _tasks_run_today
    now_dt = dt.now(_TZ_TW)
    hour = now_dt.hour
    if 0 <= hour < 6:
        time_period = "深夜修仙"
    elif 6 <= hour < 12:
        time_period = "清晨破曉"
    elif 12 <= hour < 14:
        time_period = "午休摸魚"
    elif 14 <= hour < 18:
        time_period = "午後辦公"
    else:
        time_period = "夜晚熱鬧"

    brain_cog = bot.get_cog("BotBrain") if bot and hasattr(bot, "get_cog") else None
    tasks = _tasks_run_today
    if brain_cog and hasattr(brain_cog, "_task_counter"):
        raw_tc = getattr(brain_cog, "_task_counter", 0)
        if isinstance(raw_tc, (int, float)):
            tasks = max(_tasks_run_today, int(raw_tc))

    cid_str = str(channel_id)
    user_chat_ts = _last_channel_chatter_time.get(cid_str, 0.0)
    bot_speak_ts = get_last_bot_speak_timestamp(cid_str)
    last_chat_ts = max(user_chat_ts, bot_speak_ts)
    if last_chat_ts <= 0:
        last_chat_ts = time.time() - 3600
    silence_mins = max(0, int((time.time() - last_chat_ts) / 60))
    bowls = round(_tokens_eaten_today / 1000.0, 1)

    # 熱聊延續窗口判定與作息心智時段
    try:
        cfg = get_care_repository().get_surfer_config()
        window_sec = cfg.active_chat_window_seconds
        act_s = cfg.active_hour_start
        act_e = cfg.active_hour_end
    except Exception:
        window_sec = 300
        act_s, act_e = 9, 23
    last_speak_ts = get_last_bot_speak_timestamp(cid_str)
    in_active_window = bool(last_speak_ts > 0 and (time.time() - last_speak_ts) < window_sec)
    is_act = is_within_active_hours(hour, act_s, act_e)
    mind_st = get_mind_state(act_s, act_e)

    return {
        "time_period": time_period,
        "tasks_today": tasks,
        "tokens_today": _tokens_eaten_today,
        "tokens_consumed_today": _tokens_eaten_today,
        "bowls_of_rice": bowls,
        "silence_minutes": silence_mins,
        "hour": hour,
        "active_chat_window_open": in_active_window,
        "last_bot_speak_time": last_speak_ts,
        "is_active_hours": is_act,
        "active_hour_start": act_s,
        "active_hour_end": act_e,
        "mind_state": mind_st,
    }


def clean_cyber_bubble(text: str) -> str:
    """去 AI 味橡皮擦：去除外圍引號、句尾生硬句號、說教腔調與外層 Markdown 代碼塊標記。"""
    if not text:
        return ""
    cleaned = text.strip()
    # 移除內部思考標籤
    cleaned = re.sub(r'<thought>.*?</thought>', '', cleaned, flags=re.DOTALL).strip()
    # 移除外層 code fence 如 ``` 或 ```json / ```markdown
    if cleaned.startswith("```"):
        cleaned = re.sub(r'^```[a-zA-Z0-9_-]*\n?', '', cleaned)
        cleaned = re.sub(r'\n?```$', '', cleaned).strip()
    # 移除外圍包裹的引號與句尾生硬句號（循環剝除直到乾淨）
    while True:
        prev = cleaned
        cleaned = re.sub(r'^[「"\'“‘]+|[」"\'”’]+$', '', cleaned).strip()
        if cleaned.endswith("。"):
            cleaned = cleaned[:-1].strip()
        if cleaned == prev:
            break
    # 壓縮多重空白與空行
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)

    # 抑制過度重複口癖與裝飾符號：
    # 1. 限制 ✨ 表情符號：每個氣泡最多保留 1 個 ✨，後續的移除
    sparkle_count = 0

    def _clamp_sparkle(match: re.Match) -> str:
        nonlocal sparkle_count
        sparkle_count += 1
        return match.group(0) if sparkle_count <= 1 else ""

    cleaned = re.sub(r'✨', _clamp_sparkle, cleaned)

    # 2. 限制自稱：若 "本名偵探" 或 "本偵探" 出現超過 1 次，第 2 次及以後替換為 "我"
    detective_count = 0

    def _replace_detective(match: re.Match) -> str:
        nonlocal detective_count
        detective_count += 1
        return match.group(0) if detective_count <= 1 else "我"

    cleaned = re.sub(r'(?<!劇)本名偵探|(?<!劇)本偵探', _replace_detective, cleaned)
    cleaned = re.sub(r'[ \t]{2,}', ' ', cleaned)

    return cleaned.strip()


def _expand_long_bubbles(
    items: list[str],
    *,
    max_bubbles: int,
    max_chars: int = 80,
) -> list[str]:
    """把超過一則訊息長度的句子再依逗號切開；只切不丟字。"""
    expanded: list[str] = []
    for item in items:
        item = str(item or "").strip()
        if not item:
            continue
        if len(item) <= max_chars:
            expanded.append(item)
            continue
        buffer = ""
        parts = re.split(r"([，,]+)", item)
        for idx in range(0, len(parts), 2):
            seg = parts[idx]
            punct = parts[idx + 1] if idx + 1 < len(parts) else ""
            combined = clean_cyber_bubble(seg + punct)
            if not combined:
                continue
            if buffer and len(buffer) + len(combined) > max_chars:
                expanded.append(buffer)
                buffer = combined
            else:
                buffer += combined
        if buffer:
            expanded.append(buffer)
    if len(expanded) > max_bubbles:
        expanded = expanded[:max_bubbles - 1] + ["".join(expanded[max_bubbles - 1:])]
    return [item for item in expanded if item]


def split_into_bubbles(text: str, max_bubbles: int = 3) -> list[str]:
    """將文字拆分成 1~N 則真人感口語短句氣泡。"""
    clean = clean_cyber_bubble(text)
    if not clean:
        return []

    # 1. 若內容已有自然換行
    raw_lines = [clean_cyber_bubble(line) for line in clean.splitlines() if line.strip()]
    raw_lines = [l for l in raw_lines if l]
    if 1 < len(raw_lines) <= max_bubbles:
        return _expand_long_bubbles(raw_lines, max_bubbles=max_bubbles)
    elif len(raw_lines) > max_bubbles:
        first = raw_lines[:max_bubbles - 1]
        last = " ".join(raw_lines[max_bubbles - 1:])
        return _expand_long_bubbles(
            [l for l in first + [clean_cyber_bubble(last)] if l],
            max_bubbles=max_bubbles,
        )

    # 2. 單行長句依語氣標點拆分（！、？、～、...、…）
    if len(clean) > 20:
        parts = re.split(r'([！!？\?~～]+|\.\.\.+|…+)', clean)
        chunks = []
        for i in range(0, len(parts), 2):
            seg = parts[i]
            punct = parts[i + 1] if i + 1 < len(parts) else ""
            comb = clean_cyber_bubble(seg + punct)
            if comb:
                chunks.append(comb)
        if 1 < len(chunks) <= max_bubbles:
            return _expand_long_bubbles(chunks, max_bubbles=max_bubbles)
        elif len(chunks) > max_bubbles:
            first = chunks[:max_bubbles - 1]
            last = "".join(chunks[max_bubbles - 1:])
            return _expand_long_bubbles(
                [l for l in first + [clean_cyber_bubble(last)] if l],
                max_bubbles=max_bubbles,
            )

        # 3. 若無語氣標點但有逗號且長度較長，依逗號切分口語短句
        if len(clean) > 20:
            comma_parts = re.split(r'([，,]+)', clean)
            c_chunks = []
            for i in range(0, len(comma_parts), 2):
                seg = comma_parts[i]
                c_comb = clean_cyber_bubble(seg)
                if c_comb:
                    c_chunks.append(c_comb)
            if 1 < len(c_chunks) <= max_bubbles:
                return _expand_long_bubbles(c_chunks, max_bubbles=max_bubbles)
            elif len(c_chunks) > max_bubbles:
                first = c_chunks[:max_bubbles - 1]
                last = " ".join(c_chunks[max_bubbles - 1:])
                return _expand_long_bubbles(
                    [l for l in first + [clean_cyber_bubble(last)] if l],
                    max_bubbles=max_bubbles,
                )

    return _expand_long_bubbles([clean], max_bubbles=max_bubbles)


def _configured_max_bubbles() -> int:
    """舊 lane 也讀 WebUI 的氣泡上限，兩條 lane 才不會一個設 5、一個硬寫 2。"""
    try:
        from .cyber.ui_config import configured_max_bubbles

        return configured_max_bubbles(2)
    except Exception:
        return 2


async def send_cyber_bubbles(
    channel: Any,
    bubbles: list[str],
    reply_to: Any = None,
    typing_delay: float = 1.0,
) -> list[Any]:
    """真人打字分段連發：帶有正在輸入 Typing 延遲，首句可 reply。"""
    sent_msgs: list[Any] = []
    if not channel or not bubbles:
        return sent_msgs

    cid = getattr(channel, "id", None)
    for idx, bubble in enumerate(bubbles):
        bubble_text = bubble.strip()
        if not bubble_text:
            continue

        try:
            if hasattr(channel, "typing"):
                async with channel.typing():
                    delay = min(1.5, max(0.6, len(bubble_text) * 0.03 * typing_delay))
                    await asyncio.sleep(delay)
        except Exception:
            pass

        try:
            msg = None
            if idx == 0 and reply_to is not None and hasattr(reply_to, "reply"):
                msg = await reply_to.reply(bubble_text, mention_author=False)
            elif hasattr(channel, "send"):
                msg = await channel.send(bubble_text)
            if msg:
                sent_msgs.append(msg)
                if cid is not None:
                    update_bot_speak_timestamp(cid)
        except Exception as exc:
            logger.warning("[CareSurfer] 發送氣泡訊息失敗: %s", exc)

    if cid is not None and sent_msgs:
        update_bot_speak_timestamp(cid)

    return sent_msgs


def record_proactive_action_memory(
    summary: str,
    channel_id: str | int,
    user_id: str | int = "",
    user_name: str = "",
    detail: dict[str, Any] | None = None,
    status: str = "success",
    importance: int = 3,
) -> str | None:
    """雪莉主動行為記憶集中寫入入口（不依賴 Discord Message 物件）。"""
    try:
        from handlers.memory.self_memory import record_self_action
        return record_self_action(
            tool="CYBER_FRIEND",
            summary=summary,
            verb="賽博群友",
            channel_id=channel_id,
            user_id=user_id,
            user_name=user_name,
            detail=detail or {},
            status=status,
            importance=importance,
        )
    except Exception as exc:
        logger.debug("[CareSurfer] 寫入行為記憶略過: %s", exc)
        return None


# ---------------------------------------------------------------------------
# SurferBroadcaster (Port 8765 SSE 事件流廣播器)
# ---------------------------------------------------------------------------

class SurferBroadcaster:
    """管理 Port 8765 雪莉虛擬瀏覽器實況台的 SSE 連線與狀態快照。"""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._lock = asyncio.Lock()
        self.last_session_snapshot: dict[str, Any] = {
            "status": "idle",
            "current_step": "idle",
            "current_url": "about:blank",
            "candidates": [],
            "evaluating_index": -1,
            "matched_users": [],
            "thought": "",
            "selected": None,
            "post_text": "",
            "bubbles": [],
            "mode": "auto",
            "live_stats": {},
            "reaction": "",
            "self_memory_entry": None,
            "error": "",
            "timestamp": time.time(),
        }

    async def subscribe(self) -> asyncio.Queue[str]:
        """註冊新的 SSE 訂閱者佇列。"""
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._subscribers.add(q)
        # 立即發送當前最新快照
        initial_msg = f"event: state\ndata: {json.dumps(self.last_session_snapshot, ensure_ascii=False)}\n\n"
        await q.put(initial_msg)
        return q

    async def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        """移除 SSE 訂閱者佇列。"""
        async with self._lock:
            self._subscribers.discard(q)

    async def broadcast(self, event_type: str, data: dict[str, Any]) -> None:
        """廣播事件給所有連線中的 Port 8765 客戶端並更新狀態快照。"""
        # 更新內部快照
        self.last_session_snapshot["status"] = data.get("status", self.last_session_snapshot["status"])
        if "step" in data:
            self.last_session_snapshot["current_step"] = data["step"]
        if "url" in data:
            self.last_session_snapshot["current_url"] = data["url"]
        if "candidates" in data:
            self.last_session_snapshot["candidates"] = data["candidates"]
        if "evaluating_index" in data:
            self.last_session_snapshot["evaluating_index"] = data["evaluating_index"]
        if "matched_users" in data:
            self.last_session_snapshot["matched_users"] = data["matched_users"]
        if "thought" in data:
            self.last_session_snapshot["thought"] = data["thought"]
        if "selected" in data:
            self.last_session_snapshot["selected"] = data["selected"]
        if "post_text" in data:
            self.last_session_snapshot["post_text"] = data["post_text"]
        if "bubbles" in data:
            self.last_session_snapshot["bubbles"] = data["bubbles"]
        if "mode" in data:
            self.last_session_snapshot["mode"] = data["mode"]
        if "live_stats" in data:
            self.last_session_snapshot["live_stats"] = data["live_stats"]
        if "reaction" in data:
            self.last_session_snapshot["reaction"] = data["reaction"]
        if "self_memory_entry" in data:
            self.last_session_snapshot["self_memory_entry"] = data["self_memory_entry"]
        if "error" in data:
            self.last_session_snapshot["error"] = data["error"]
        self.last_session_snapshot["timestamp"] = time.time()

        payload = f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        async with self._lock:
            active_subs = list(self._subscribers)

        for q in active_subs:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    q.put_nowait(payload)
                except Exception:
                    pass


# 全域廣播器與衝浪引擎實例
_global_broadcaster = SurferBroadcaster()
_global_surf_lock = asyncio.Lock()
_global_surfer: CareSurfer | None = None


def get_surfer_broadcaster() -> SurferBroadcaster:
    return _global_broadcaster


def get_care_surfer() -> CareSurfer:
    global _global_surfer
    if _global_surfer is None:
        _global_surfer = CareSurfer()
    return _global_surfer


def set_care_surfer(surfer: CareSurfer) -> None:
    global _global_surfer
    _global_surfer = surfer


# ---------------------------------------------------------------------------
# CareSurfer 衝浪與分享核心引擎
# ---------------------------------------------------------------------------

@dataclass
class CandidateArticle:
    index: int
    title: str
    url: str
    source: str
    summary: str


_CJK_STOP_WORDS = {
    "喜歡", "偏好", "愛好", "常看", "常常", "最近", "目前", "進行", "使用", "要求",
    "必須", "包含", "互動", "這個", "那個", "因為", "所以", "但是", "而且", "可以",
    "如果", "非常", "十分", "比較", "很多", "一些", "大家", "各位", "朋友", "時候",
    "內容", "感覺", "覺得", "知道", "今天", "明天", "昨天", "看到", "聽說", "就是",
}


class CareSurfer:
    """雪莉「網活仔」主動衝浪引擎。"""

    def __init__(
        self,
        repository: CareRepository | None = None,
        broadcaster: SurferBroadcaster | None = None,
        rss_client: RssClient | None = None,
        search_client: WebMonitorClient | None = None,
        self_memory_db_path: Path | str | None = None,
    ) -> None:
        self.repository = repository or get_care_repository()
        self.broadcaster = broadcaster or get_surfer_broadcaster()
        self.rss_client = rss_client or RssClient()
        self.search_client = search_client or WebMonitorClient()
        self.self_memory_db_path = self_memory_db_path
        self._surf_lock = _global_surf_lock
        self._mind_state = _mind_state
        self._director_instance: Any = None

    # ------------------------------------------------------------------
    # Social Core 整合（決策帳本）
    # ------------------------------------------------------------------
    @property
    def director(self) -> Any:
        """延遲建立 SocialDirector，避免模組載入時循環匯入。"""
        if self._director_instance is None:
            from handlers.social.autonomy import SocialDirector

            self._director_instance = SocialDirector(self.repository)
        return self._director_instance

    def _social_event(self, config: CareSurferConfig, mode_name: str, trigger: Any = None) -> Any:
        from handlers.social.autonomy import SocialEvent, TriggerType

        return SocialEvent(
            kind=str(mode_name),
            channel_id=str(config.sandbox_channel_id or "0"),
            trigger_type=trigger or TriggerType.SURF,
            created_at=time.time(),
        )

    def _record_social(
        self,
        *,
        config: CareSurferConfig,
        mode_name: str,
        action: str,
        text: str = "",
        reasons: tuple[str, ...] | list[str] = (),
        trigger: Any = None,
        persona_key: str = "sherry",
        latency_ms: int = 0,
        model: str = "",
        tokens: int = 0,
    ) -> None:
        """把衝浪結果寫進決策帳本；任何失敗都不可影響發文流程。"""
        try:
            from handlers.social.autonomy import ChannelMode, DecisionAction

            event = self._social_event(config, mode_name, trigger)
            if action == DecisionAction.SPEAK.value:
                self.director.record_speak(
                    event,
                    mode=ChannelMode.SOLO,
                    persona_key=persona_key,
                    text=text,
                    latency_ms=latency_ms,
                    model=model,
                    tokens=tokens,
                )
            else:
                self.director.record_silent(
                    event,
                    mode=ChannelMode.SOLO,
                    reasons=list(reasons) or ["unspecified"],
                    persona_key=persona_key,
                )
        except Exception as exc:
            logger.debug("[CareSurfer] 寫入決策帳本略過: %s", exc)

    @property
    def is_busy(self) -> bool:
        """是否正有衝浪或冒泡任務在執行中。"""
        return self._surf_lock.locked()

    def get_mind_state(self, active_start: int | None = None, active_end: int | None = None) -> dict[str, Any]:
        """取得當前心智狀態指標。"""
        return get_mind_state(active_start, active_end)

    def decay_mind_state(self, force_drop: float | None = None) -> dict[str, Any]:
        """遞減心智指標。"""
        return decay_mind_state(force_drop)

    def replenish_mind_state(
        self,
        social_boost: float = 0.0,
        curiosity_boost: float = 0.0,
        thought: str | None = None,
        replenish_social: bool = False,
        replenish_curiosity: bool = False,
    ) -> dict[str, Any]:
        """回補心智指標。"""
        return replenish_mind_state(
            social_boost=social_boost,
            curiosity_boost=curiosity_boost,
            thought=thought,
            replenish_social=replenish_social,
            replenish_curiosity=replenish_curiosity,
        )

    async def fetch_candidates(self) -> list[CandidateArticle]:
        """抓取候選文章並自動排除已閱讀過的 URL 或標題。"""
        config = await asyncio.to_thread(self.repository.get_surfer_config)
        candidates: list[CandidateArticle] = []
        seen_urls: set[str] = set()

        # 1. 抓取 RSS 來源
        rss_tasks = []
        for feed in config.rss_feeds:
            feed_url = feed.get("url", "").strip()
            if feed_url:
                rss_tasks.append((feed.get("name", "RSS"), feed_url))

        # 廣播開啟網址事件
        display_url = rss_tasks[0][1] if rss_tasks else "https://gnn.gamer.com.tw/rss.xml"
        await self.broadcaster.broadcast(
            "browsing",
            {
                "status": "surfing",
                "step": "navigating",
                "url": display_url,
                "message": f"雪莉正在打開網頁：{display_url}",
            },
        )

        for feed_name, feed_url in rss_tasks:
            try:
                items = await self.rss_client.fetch_latest_items(feed_url, limit=4)
                for item in items:
                    clean_url = (item.url or "").strip()
                    if not clean_url or clean_url in seen_urls:
                        continue
                    is_seen = await asyncio.to_thread(
                        is_article_seen, clean_url, title=item.title, db_path=self.self_memory_db_path
                    )
                    if is_seen:
                        continue
                    seen_urls.add(clean_url)
                    candidates.append(
                        CandidateArticle(
                            index=len(candidates),
                            title=item.title.strip(),
                            url=clean_url,
                            source=f"RSS ({feed_name})",
                            summary=(item.summary or "").strip()[:200],
                        )
                    )
            except Exception as exc:
                logger.debug("[CareSurfer] 抓取 RSS (%s) 失敗: %s", feed_name, exc)

        # 2. 依雪莉個人近期興趣檢索
        interests = await asyncio.to_thread(
            get_active_interests, limit=3, db_path=self.self_memory_db_path
        )
        search_terms = list(interests) if interests else list(config.search_keywords)[:2]

        for query in search_terms[:2]:
            try:
                results = await self.search_client.search(query, limit=3)
                for res in results:
                    u = res.get("url", "").strip()
                    t = res.get("title", "").strip()
                    if not u or u in seen_urls:
                        continue
                    is_seen = await asyncio.to_thread(
                        is_article_seen, u, title=t, db_path=self.self_memory_db_path
                    )
                    if is_seen:
                        continue
                    seen_urls.add(u)
                    candidates.append(
                        CandidateArticle(
                            index=len(candidates),
                            title=t,
                            url=u,
                            source=f"Search ({query})",
                            summary=(res.get("snippet", "")).strip()[:200],
                        )
                    )
            except Exception as exc:
                logger.debug("[CareSurfer] 關鍵字檢索 (%s) 失敗: %s", query, exc)

        # 限制候選文章最多 6 筆，避免 LLM 上下文過長
        return candidates[:6]

    def match_user_interests(
        self,
        candidates: list[CandidateArticle],
        bot: Any = None,
    ) -> list[dict[str, Any]]:
        """掃描群友長期記憶，找出與候選文章主題吻合的群友與偏好事實。

        相容 UserFact 規格（{"fact": "...", "category": "..."}）與 aliases 別名。
        過濾通用停用詞與已 opt-out 關懷之群友。
        """
        matches: list[dict[str, Any]] = []
        user_facts_map: dict[str, tuple[str, list[str]]] = {}

        # 取得群友記憶
        mem_cog = bot.get_cog("MemoryManager") if bot and hasattr(bot, "get_cog") else None
        store = getattr(mem_cog, "store", None)
        if not store:
            try:
                from handlers.memory.storage import load_user_memory
                store = load_user_memory(settings.USER_MEMORY_PATH)
            except Exception:
                store = {}

        # 預先一次性批量獲取所有已 opt-out 關懷之群友 ID，避免在迴圈中逐筆進行 SQLite 連線查詢 (O(1) 集合檢查)
        try:
            opt_out_uids = self.repository.get_opt_out_user_ids()
        except Exception:
            opt_out_uids = set()

        for uid, profile in store.items():
            if not isinstance(profile, dict):
                continue

            # 檢查該群友是否明確 opt-out 關懷（frequency == 'off'）
            if str(uid) in opt_out_uids:
                continue

            facts = profile.get("facts", [])
            aliases = profile.get("aliases", [])
            pref_texts: list[str] = []
            for f in facts:
                if isinstance(f, dict):
                    # 支援正式 UserFact 的 "fact" 欄位與向下相容的 "content" 欄位
                    content = str(f.get("fact") or f.get("content") or "").strip()
                    cat = str(f.get("category", "")).strip().lower()
                    if cat in ("preference", "habit", "social") or any(kw in content for kw in ("喜歡", "愛", "玩", "常看", "研究", "偏好")):
                        if content:
                            pref_texts.append(content)
                elif isinstance(f, str) and f.strip():
                    pref_texts.append(f.strip())

            if pref_texts:
                uname = ""
                if aliases and isinstance(aliases, list) and aliases[0]:
                    uname = str(aliases[0]).strip()
                if not uname and bot and hasattr(bot, "get_user"):
                    try:
                        u_obj = bot.get_user(int(uid))
                        if u_obj:
                            uname = getattr(u_obj, "display_name", "") or str(u_obj)
                    except Exception:
                        pass
                if not uname:
                    uname = f"群友 ({uid})"
                user_facts_map[str(uid)] = (uname, pref_texts)

        if not user_facts_map:
            return matches

        # 關鍵字與標題語意交集匹配（支援中英文）
        import re
        for cand in candidates:
            cand_text = f"{cand.title} {cand.summary}".lower()
            for uid, (uname, facts) in user_facts_map.items():
                for f in facts:
                    matched_terms: list[str] = []
                    # 1. 英文/數字單詞比對
                    for word in re.findall(r"[A-Za-z0-9_]{2,}", f):
                        if word.lower() in cand_text and word not in matched_terms:
                            matched_terms.append(word)
                    # 2. 中文字元 2~4 字 n-gram 滑動交集比對（過濾停用詞）
                    cjk_chars = "".join(re.findall(r"[\u4e00-\u9fff]", f))
                    for n in (4, 3, 2):
                        for i in range(len(cjk_chars) - n + 1):
                            gram = cjk_chars[i:i+n]
                            if n == 2 and gram in _CJK_STOP_WORDS:
                                continue
                            if gram in cand_text and gram not in matched_terms:
                                matched_terms.append(gram)
                    if matched_terms:
                        matches.append({
                            "candidate_index": cand.index,
                            "user_id": uid,
                            "user_name": uname,
                            "fact": f,
                            "matched_keywords": matched_terms[:3],
                        })
                        break
        return matches

    async def get_active_channel_users(
        self,
        bot: Any,
        channel_id: str | int,
        candidates: list[CandidateArticle],
    ) -> list[dict[str, Any]]:
        """若無特定興趣匹配或設定為活躍群友模式，從測試頻道近期歷史取得活躍群友進行 @Tag 互動。"""
        cid_int = int(channel_id) if str(channel_id).isdigit() else 0
        if not bot or cid_int <= 0:
            return []
        try:
            channel = bot.get_channel(cid_int)
            if channel is None and hasattr(bot, "fetch_channel"):
                channel = await bot.fetch_channel(cid_int)
            if not channel or not hasattr(channel, "history"):
                return []

            active_users: list[tuple[str, str]] = []
            async for msg in channel.history(limit=30):
                author = getattr(msg, "author", None)
                if author and not getattr(author, "bot", False):
                    uid = str(author.id)
                    name = getattr(author, "display_name", "") or getattr(author, "name", "") or f"群友 ({uid})"
                    try:
                        pref = self.repository.get_preference(uid, guild_id=str(getattr(channel, "guild", {}).id or ""))
                        if pref.frequency == "off":
                            continue
                    except Exception:
                        pass
                    if not any(u[0] == uid for u in active_users):
                        active_users.append((uid, name))

            if not active_users:
                return []

            import random
            chosen_uid, chosen_name = random.choice(active_users)
            cand_idx = random.randint(0, len(candidates) - 1) if candidates else 0
            return [{
                "candidate_index": cand_idx,
                "user_id": chosen_uid,
                "user_name": chosen_name,
                "fact": "近期在測試頻道活躍互動的群友",
                "matched_keywords": ["近期活躍群友"],
            }]
        except Exception as exc:
            logger.debug("[CareSurfer] 取得頻道活躍成員失敗: %s", exc)
            return []

    async def evaluate_channel_lurk(
        self,
        bot: Any,
        channel: Any,
        recent_script: str,
        target_message: Any = None,
    ) -> dict[str, Any]:
        """窺屏評分：評估頻道近期對話，決定潛水、按 Emoji 表情、或插話對線。"""
        if not recent_script or not channel:
            return {"acted": False, "score": 0, "reason": "無對話或無頻道"}

        config = await asyncio.to_thread(self.repository.get_surfer_config)
        if not config.chat_reply_enabled:
            return {"acted": False, "score": 0, "reason": "雙向陪聊未開啟"}

        channel_id = getattr(channel, "id", 0)
        guild_id = getattr(getattr(channel, "guild", None), "id", None)
        head_persona, tail_lore = build_dynamic_persona(
            recent_script, "CHAT", channel_id=channel_id, bot=bot, guild_id=guild_id
        )

        context = ConversationContext.from_script(
            recent_script,
            channel_id=channel_id,
            focus_message=target_message,
            bot_id=getattr(getattr(bot, "user", None), "id", ""),
        )
        result = await think(
            bot,
            intent=BrainIntent.JOIN,
            context=context,
            head_persona=head_persona,
            tail_lore=tail_lore,
            threshold=float(config.interject_score_threshold),
            model=str(
                getattr(settings, "LIGHT_MODEL", "")
                or getattr(settings, "PRIMARY_MODEL", "freetoken:mistralai/Mixtral-8x7B-Instruct-v0.1")
            ),
            max_tokens=900,
            timeout=25.0,
            priority=getattr(settings, "PRIORITY_INTERJECT", 2),
            task_id=f"lurk-eval-{channel_id}-{int(time.time())}",
        )

        score = int(result.score) if isinstance(result.score, (int, float)) else 0
        reaction = result.reaction
        reasoning = result.thought
        reply_raw = result.reply

        # 廣播窺屏日誌到 SSE
        await self.broadcaster.broadcast(
            "lurk_eval",
            {
                "channel_id": str(channel_id),
                "score": score,
                "reaction": reaction,
                "reasoning": reasoning,
                "reply": reply_raw,
                "timestamp": time.time(),
            },
        )

        # 1. Emoji Reaction (當 score >= 4 且有表情時嘗試按 Emoji)
        if config.emoji_reaction_enabled and reaction and target_message and hasattr(target_message, "add_reaction"):
            try:
                await target_message.add_reaction(reaction)
            except Exception as rx_err:
                logger.debug("[CareSurfer] 按 Emoji 失敗: %s", rx_err)

        # 2. 插話接話判定：門檻與 decision 已由大腦的 JOIN 規則收斂，
        #    這裡只看 should_speak，避免呼叫端與 prompt 各判一套。
        if result.should_speak:
            bubbles = split_into_bubbles(reply_raw, max_bubbles=_configured_max_bubbles()) if config.multi_message_enabled else [clean_cyber_bubble(reply_raw)]
            bubbles = [b for b in bubbles if str(b or "").strip()]
            if not bubbles:
                self._record_social(
                    config=config,
                    mode_name="lurk_chat",
                    action="SILENT",
                    reasons=("empty_bubbles",),
                )
                return {"acted": False, "score": score, "reaction": reaction, "reasoning": reasoning}

            # 主動插話同樣要經過共同閘門：冷卻、發言額度、時段、模式。
            blocked: list[str] = []
            try:
                from handlers.social.autonomy import SocialEvent, TriggerType

                event = SocialEvent(
                    kind="lurk_chat",
                    channel_id=str(channel_id),
                    guild_id=str(guild_id or ""),
                    trigger_type=TriggerType.HUMAN_MESSAGE,
                    message_id=str(getattr(target_message, "id", "") or ""),
                    author_id=str(getattr(getattr(target_message, "author", None), "id", "") or ""),
                    created_at=time.time(),
                )
                decision = self.director.decide(
                    event,
                    config,
                    room=self.repository.get_room(channel_id),
                    persona_key="sherry",
                    bypass_budget=True,
                )
                if not decision.allowed:
                    blocked = list(decision.gate.reasons)
            except Exception as exc:
                logger.debug("[CareSurfer] 插話閘門檢查略過: %s", exc)
            if blocked:
                return {
                    "acted": False,
                    "score": score,
                    "reaction": reaction,
                    "reasoning": reasoning,
                    "blocked": blocked,
                }

            sent_msgs = await send_cyber_bubbles(channel, bubbles, reply_to=target_message)
            add_token_consumption(180)

            # 沒被點名的主動插話：走較長的 proactive 冷卻，避免連續插隊。
            try:
                self.director.apply_cooldown(channel_id, config, proactive=True)
            except Exception as exc:
                logger.debug("[CareSurfer] 更新插話冷卻失敗: %s", exc)

            # 行為記憶與稽核
            summary = f"窺屏插話：{bubbles[0][:60]}"
            target_author = getattr(target_message, "author", None)
            uid = str(getattr(target_author, "id", ""))
            uname = str(getattr(target_author, "display_name", ""))
            record_proactive_action_memory(
                summary=summary,
                channel_id=channel_id,
                user_id=uid,
                user_name=uname,
                detail={"bubbles": bubbles, "score": score, "reasoning": reasoning},
            )
            await asyncio.to_thread(
                self.repository.record_action,
                user_id=uid or "channel",
                channel_id=str(channel_id),
                source="surfer",
                source_ref=f"score:{score}",
                topic="窺屏接話",
                status="approved",
                reason=f"窺屏評分命中 (score={score}, th={config.interject_score_threshold})",
            )
            if sent_msgs:
                self._record_social(
                    config=config,
                    mode_name="lurk_chat",
                    action="SPEAK",
                    text="\n".join(bubbles),
                    tokens=180,
                    model=str(getattr(settings, "LIGHT_MODEL", "")),
                )
            else:
                self._record_social(
                    config=config,
                    mode_name="lurk_chat",
                    action="SILENT",
                    reasons=("send_failed",),
                )
            return {
                "acted": bool(sent_msgs),
                "score": score,
                "reaction": reaction,
                "reasoning": reasoning,
                "bubbles": bubbles,
                "sent_message_ids": [str(getattr(m, "id", "")) for m in sent_msgs],
            }

        self._record_social(
            config=config,
            mode_name="lurk_chat",
            action="SILENT",
            reasons=(f"score_below_threshold:{score}",),
        )
        return {"acted": False, "score": score, "reaction": reaction, "reasoning": reasoning}

    async def surf_once(
        self,
        bot: Any = None,
        dry_run: bool = False,
        mode: str = "auto",
        force: bool = False,
        silent: bool = False,
    ) -> dict[str, Any]:
        """執行一次大肥魚風格賽博群友衝浪、接話、摸魚日常或關懷流程。

        Args:
            force: 手動觸發（WebUI 按鈕）時可繞過 ``enabled`` 開關；
                排程與心智脈動一律必須尊重 ``enabled``。
            silent: 若為 True，內部自省自發思考（寫日記）時強制靜音，絕不向 Discord 頻道發送任何訊息。
        """
        if self._surf_lock.locked():
            return {"status": "error", "message": "衝浪任務已在執行中"}

        # 若未直接傳入 bot，嘗試從伺服器模組取得共用實例
        if bot is None:
            try:
                from .surfer_server import _bot_instance as s_bot
                from .web import _bot_instance as w_bot
                bot = s_bot or w_bot
            except Exception:
                bot = None

        async with self._surf_lock:
            config = await asyncio.to_thread(self.repository.get_surfer_config)
            stats = _gather_sherry_live_stats(bot, config.sandbox_channel_id)

            # 硬性開關：關閉衝浪後，排程與心智脈動都不得發文（手動 force 除外）。
            if not config.enabled and not force:
                self._record_social(
                    config=config,
                    mode_name=mode,
                    action="SILENT",
                    reasons=("surfer_disabled",),
                )
                return {
                    "status": "disabled",
                    "reason": "surfer_disabled",
                    "mode": mode,
                    "message": "網活仔衝浪已關閉，本次不執行。",
                }

            # 取得沙盒目標文字頻道
            channel_id_int = int(config.sandbox_channel_id) if config.sandbox_channel_id.isdigit() else 0
            channel_obj = None
            if bot and channel_id_int > 0:
                try:
                    channel_obj = bot.get_channel(channel_id_int)
                    if channel_obj is None and hasattr(bot, "fetch_channel"):
                        channel_obj = await bot.fetch_channel(channel_id_int)
                except Exception:
                    channel_obj = None

            # 抓取頻道近期對話上下文（上下文感知）
            recent_chat_script = ""
            if channel_obj and hasattr(channel_obj, "history"):
                try:
                    from handlers.social.utils import fetch_recent_script
                    recent_chat_script = await fetch_recent_script(channel_obj, 8)
                except Exception as err:
                    logger.debug("[CareSurfer] 讀取頻道近期對話失敗: %s", err)

            silence_mins = stats.get("silence_minutes", 0)
            if recent_chat_script:
                context_block = (
                    f"【頻道近期對話上下文（最近聊天的內容與氣氛）】：\n"
                    f"{recent_chat_script}\n"
                    f"（距上一則訊息已冷場安靜了 {silence_mins} 分鐘）"
                )
            else:
                context_block = f"【頻道近期對話】：（目前頻道安靜中，無近期聊天，已冷場 {silence_mins} 分鐘）"

            # 智能決定有效模式
            eff_mode = mode if mode not in ("auto", "balanced") else config.behavior_mode
            if eff_mode in ("auto", "balanced"):
                # 若頻道近期有聊天且雙向陪聊開啟，優先窺屏接話
                if channel_obj and hasattr(channel_obj, "history") and stats["silence_minutes"] < 15 and config.chat_reply_enabled:
                    eff_mode = "lurk_chat"
                else:
                    import random
                    pending_topics = await asyncio.to_thread(self.repository.list_pending_topics, limit=1)
                    # 避免永遠卡死在 care_poke：只有在抽中機率 (約 25%) 或設定為 care_poke 時才觸發
                    if pending_topics and (config.behavior_mode == "care_poke" or random.random() < 0.25):
                        eff_mode = "care_poke"
                    elif stats["silence_minutes"] > 120 or stats["tokens_today"] > 25000:
                        eff_mode = "daily_vibe"
                    else:
                        eff_mode = "surf_gossip"

            await self.broadcaster.broadcast(
                "status",
                {"status": "surfing", "step": "fetching", "mode": eff_mode, "message": f"雪莉正在以【{eff_mode}】模式自主冒泡思考中..."},
            )

            # =================================================================
            # 模式 0: 自發心智思考與記憶聯想日記 (mind_monologue)
            # =================================================================
            if eff_mode == "mind_monologue":
                recent_episodes = await asyncio.to_thread(
                    list_self_episodes, limit=3, db_path=self.self_memory_db_path
                )
                recent_reads = await asyncio.to_thread(
                    list_reading_logs, limit=3, db_path=self.self_memory_db_path
                )
                snippets = []
                for ep in recent_episodes:
                    content_snippet = str(ep.get("content", ""))[:80]
                    if content_snippet:
                        snippets.append(f"- 近期生活日記：{content_snippet}")
                for rd in recent_reads:
                    title_snip = str(rd.get("title", ""))[:40]
                    opinion_snip = str(rd.get("sherry_opinion", ""))[:60]
                    if title_snip:
                        snippets.append(f"- 讀過文章《{title_snip}》：{opinion_snip}")
                mem_summary = "\n".join(snippets) if snippets else "（最近剛開始寫自我記憶，暫時沒有舊筆記）"

                head_persona, tail_lore = build_dynamic_persona("", "CHAT", channel_id=channel_id_int, bot=bot)
                tail_block = f"\n\n{tail_lore}" if tail_lore else ""
                sys_prompt = (
                    f"{head_persona}{tail_block}\n\n"
                    "【大肥魚自發心智思考與生活日記聯想任務】\n"
                    f"現在是【{stats['time_period']}】（{stats['hour']}:00 左右）。\n\n"
                    f"{context_block}\n\n"
                    f"以下是妳最近在自我記憶庫裡的零星日記與讀書筆記：\n{mem_summary}\n\n"
                    "請以妳傲嬌、偷懶摸魚但又對世界充滿好奇的名偵探性格，發揮自我心智思考：\n"
                    "1. 綜合妳剛才看到的群聊氣氛或最近的想法，寫下一段給自己的第一人稱內心沉澱 OS 與日常筆記（thought）。\n"
                    "2. 若處於白天活躍時段且有分享欲，輸出 1~2 則想隨口發在群組的口語短句（post_text）；若剛才有人在聊，可自然接話或融入；若大家都潛水，可隨口碎碎念或丟出思考；若深夜或只想自言自語，post_text 可簡短或留空。\n"
                    "嚴禁機器人腔調、生硬句號或引號。請輸出嚴格 JSON：\n"
                    '{"thought": "內心沉澱日記思考", "post_text": "想隨口發在群組的話", "share_desire": 6}'
                )
                thought = ""
                post_text = ""
                share_desire = 5

                brain_cog = bot.get_cog("BotBrain") if bot and hasattr(bot, "get_cog") else None
                if brain_cog and hasattr(brain_cog, "enqueue_task"):
                    done_event = asyncio.Event()
                    holder: list[str] = [""]

                    async def _on_mind_done(res: str) -> None:
                        holder[0] = res
                        done_event.set()

                    task = InferenceTask(
                        priority=getattr(settings, "PRIORITY_INTERJECT", 2),
                        system_prompt=sys_prompt,
                        user_prompt="請整理妳當下的心智思考與第一人稱記憶筆記",
                        callback=_on_mind_done,
                        model=getattr(settings, "PRIMARY_MODEL", "freetoken:mistralai/Mixtral-8x7B-Instruct-v0.1"),
                        task_id=f"surfer-mind-{int(time.time())}",
                        max_tokens=800,
                    )
                    await brain_cog.enqueue_task(task)
                    try:
                        # 慢模型（3.7-flash）首 token 實測 30~60 秒，BotBrain 60 秒逾時後才切備援；
                        # 等 70 秒才接得到備援模型的成功結果，等太短會把成功記成失敗。
                        await asyncio.wait_for(done_event.wait(), timeout=70.0)
                        cleaned = holder[0].strip().lstrip("```json").lstrip("```").rstrip("```").strip()
                        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
                        if m:
                            p = json.loads(m.group(0))
                            thought = p.get("thought", thought)
                            post_text = p.get("post_text", post_text)
                            share_desire = int(p.get("share_desire", 5))
                    except Exception as exc:
                        logger.debug("[CareSurfer] mind_monologue 推論略過: %s", exc)

                generation_failed = not str(thought or "").strip()
                if generation_failed:
                    # 生成失敗：不補任何罐頭台詞外發，只留下私下沉澱的內部紀錄。
                    thought = f"（{stats['time_period']}）今天腦袋有點空，只是靜靜坐著放空。"

                bubbles = split_into_bubbles(post_text, max_bubbles=_configured_max_bubbles()) if config.multi_message_enabled else [clean_cyber_bubble(post_text)]
                bubbles = [b for b in bubbles if b.strip()]

                # 嚴格防半夜擾民與靜音自省：若不在活躍時段、或非手動強制點擊的 mind_monologue、或指定 silent，強制 is_silent = True
                is_active = is_within_active_hours(stats["hour"], config.active_hour_start, config.active_hour_end)
                is_silent = not is_active or silent or (mode == "mind_monologue" and not force)
                if not bubbles:
                    # 生成失敗或模型選擇不說話：沉默，不補罐頭台詞。
                    is_silent = True
                if is_active and share_desire < 7 and not force:
                    # 白天若分享欲不高且不是使用者手動指定點擊，偏向私下記錄
                    is_silent = True

                posted_id = ""
                if not dry_run and not is_silent and channel_obj and bubbles:
                    sent = await send_cyber_bubbles(channel_obj, bubbles)
                    if sent:
                        posted_id = str(getattr(sent[0], "id", ""))
                    add_token_consumption(200)

                if posted_id:
                    self._record_social(
                        config=config,
                        mode_name="mind_monologue",
                        action="SPEAK",
                        text="\n".join(bubbles),
                        tokens=200,
                        model=str(getattr(settings, "PRIMARY_MODEL", "")),
                    )
                elif not dry_run:
                    silent_reason = "generation_failed" if generation_failed else (
                        "outside_active_hours" if not is_active else "no_post_text"
                    )
                    self._record_social(
                        config=config,
                        mode_name="mind_monologue",
                        action="SILENT",
                        reasons=(silent_reason,),
                    )

                # 寫入 self_episodes 與 self_actions
                ep_id = None
                try:
                    ep_id = await asyncio.to_thread(
                        add_self_episode,
                        category="diary",
                        content=f"【自發心智日記 ({stats['time_period']})】{thought}",
                        importance=4,
                        tags=["自發思考", "心智日記", stats["time_period"]],
                        db_path=self.self_memory_db_path,
                    )
                    await asyncio.to_thread(
                        record_self_action,
                        verb="自發思考",
                        target="沉澱自我記憶與生活日記",
                        status="success",
                        detail={"mode": "mind_monologue", "silent": is_silent, "share_desire": share_desire},
                        db_path=self.self_memory_db_path,
                    )
                except Exception as exc:
                    logger.debug("[CareSurfer] 寫入 self_memory 異常: %s", exc)

                if not dry_run and not is_silent and posted_id:
                    record_proactive_action_memory(
                        summary=f"自發思考分享：{bubbles[0][:60]}",
                        channel_id=config.sandbox_channel_id,
                        detail={"mode": "mind_monologue", "bubbles": bubbles, "thought": thought},
                    )
                    await asyncio.to_thread(
                        self.repository.record_action,
                        user_id="channel",
                        channel_id=str(channel_id_int),
                        source="surfer",
                        source_ref="mind_monologue",
                        topic="自發心智思考",
                        status="approved",
                        reason="大肥魚自發思考冒泡",
                    )
                    await asyncio.to_thread(self.repository.set_surfer_last_run)

                replenish_mind_state(
                    curiosity_boost=45.0,
                    social_boost=35.0 if not is_silent else 10.0,
                    thought=thought,
                )

                if is_silent:
                    result_msg = f"自發思考完成！雪莉私下記錄了沉澱日記（時段: {stats['time_period']}，本次安靜不吵群友）。"
                elif dry_run:
                    result_msg = "自發思考預覽完成（Dry-run）！"
                else:
                    result_msg = f"自發思考完成！已將內心想法分享至測試頻道（分享欲 {share_desire}/10）。"

                result_payload = {
                    "status": "idle",
                    "step": "completed",
                    "dry_run": dry_run,
                    "silent": is_silent,
                    "mode": "mind_monologue",
                    "url": "about:blank",
                    "thought": thought,
                    "selected": {
                        "index": 0,
                        "title": f"自發心智日記 ({stats['time_period']})",
                        "url": "",
                        "source": "MindMonologue",
                        "summary": thought,
                        "sherry_opinion": thought,
                        "new_interest": "近期心智沉澱",
                        "tagged_user_id": "",
                        "tagged_user_name": "",
                    },
                    "post_text": "\n".join(bubbles),
                    "bubbles": bubbles,
                    "posted_message_id": posted_id,
                    "channel_id": config.sandbox_channel_id,
                    "live_stats": stats,
                    "self_memory_entry": {"reading_id": None, "episode_ids": [ep_id] if ep_id else []},
                    "message": result_msg,
                }
                await self.broadcaster.broadcast("completed", result_payload)
                return result_payload

            # =================================================================
            # 模式 1: 窺屏接話 (lurk_chat)
            # =================================================================
            if eff_mode == "lurk_chat":
                script = recent_chat_script
                if not script and channel_obj:
                    try:
                        from handlers.social.utils import fetch_recent_script
                        script = await fetch_recent_script(channel_obj, 10)
                    except Exception as err:
                        logger.debug("[CareSurfer] 讀取頻道腳本失敗: %s", err)

                if not script:
                    # 若無近期群聊內容，平滑切換至日常摸魚碎碎念
                    eff_mode = "daily_vibe"
                else:
                    head_persona, tail_lore = build_dynamic_persona(script, "CHAT", channel_id=channel_id_int, bot=bot)
                    from handlers.social.autonomy.actions import LurkChatAction
                    from handlers.social.autonomy.contracts import Persona as SocialPersona

                    lurk_action = LurkChatAction()
                    lurk_prompt = lurk_action.build(
                        SocialPersona(
                            key="sherry",
                            display_name=str(getattr(settings, "BOT_NAME", "雪莉") or "雪莉"),
                            soul_name=str(getattr(settings, "CURRENT_SOUL_NAME", "")),
                            channel_id=str(channel_id_int),
                            is_primary=True,
                            enabled=True,
                        ),
                        head_persona=head_persona,
                        tail_lore=tail_lore,
                        context_block=context_block,
                        script=script,
                    )
                    sys_prompt = lurk_prompt.system_prompt
                    thought = ""
                    post_text = ""

                    brain_cog = bot.get_cog("BotBrain") if bot and hasattr(bot, "get_cog") else None
                    if brain_cog and hasattr(brain_cog, "enqueue_task"):
                        done_event = asyncio.Event()
                        holder: list[str] = [""]

                        async def _on_lurk_done(res: str) -> None:
                            holder[0] = res
                            done_event.set()

                        task = InferenceTask(
                            priority=getattr(settings, "PRIORITY_INTERJECT", 2),
                            system_prompt=sys_prompt,
                            user_prompt=script,
                            callback=_on_lurk_done,
                            model=getattr(settings, "PRIMARY_MODEL", "freetoken:mistralai/Mixtral-8x7B-Instruct-v0.1"),
                            task_id=f"surfer-lurk-{int(time.time())}",
                            max_tokens=900,
                        )
                        await brain_cog.enqueue_task(task)
                        try:
                            await asyncio.wait_for(done_event.wait(), timeout=30.0)
                            thought, post_text = lurk_action.parse(holder[0])
                        except Exception as exc:
                            logger.debug("[CareSurfer] lurk_chat 推論略過: %s", exc)

                    if not str(post_text or "").strip():
                        self._record_social(
                            config=config,
                            mode_name="lurk_chat",
                            action="SILENT",
                            reasons=("generation_failed",),
                        )
                        return {
                            "status": "idle",
                            "mode": "lurk_chat",
                            "silent": True,
                            "reason": "generation_failed",
                            "message": "窺屏生成失敗，本次保持沉默。",
                        }

                    # 嚴格防半夜擾民
                    is_active = is_within_active_hours(stats["hour"], config.active_hour_start, config.active_hour_end)
                    is_silent = not is_active

                    bubbles = split_into_bubbles(post_text, max_bubbles=_configured_max_bubbles()) if config.multi_message_enabled else [clean_cyber_bubble(post_text)]
                    posted_id = ""
                    if not dry_run and not is_silent and channel_obj:
                        sent = await send_cyber_bubbles(channel_obj, bubbles)
                        if sent:
                            posted_id = str(getattr(sent[0], "id", ""))
                        add_token_consumption(200)

                    if posted_id:
                        self._record_social(
                            config=config,
                            mode_name="lurk_chat",
                            action="SPEAK",
                            text="\n".join(bubbles),
                            tokens=200,
                            model=str(getattr(settings, "PRIMARY_MODEL", "")),
                        )
                    elif not dry_run:
                        self._record_social(
                            config=config,
                            mode_name="lurk_chat",
                            action="SILENT",
                            reasons=("outside_active_hours",) if is_silent else ("send_failed",),
                        )

                    if not dry_run and not is_silent:
                        record_proactive_action_memory(
                            summary=f"窺屏插話：{bubbles[0][:60]}",
                            channel_id=config.sandbox_channel_id,
                            detail={"mode": "lurk_chat", "bubbles": bubbles, "thought": thought},
                        )
                        await asyncio.to_thread(
                            self.repository.record_action,
                            user_id="channel",
                            channel_id=str(channel_id_int),
                            source="surfer",
                            source_ref="lurk_chat",
                            topic="賽博群友窺屏接話",
                            status="approved",
                            reason="大肥魚模式自主窺屏冒泡",
                        )
                        await asyncio.to_thread(self.repository.set_surfer_last_run)

                    replenish_mind_state(social_boost=45.0, thought=thought)

                    result_payload = {
                        "status": "idle",
                        "step": "completed",
                        "dry_run": dry_run,
                        "mode": "lurk_chat",
                        "url": "about:blank",
                        "thought": thought,
                        "selected": {
                            "index": 0,
                            "title": "群聊話題窺屏對線",
                            "url": "",
                            "source": "LurkChat",
                            "summary": script[-150:] if len(script) > 150 else script,
                            "sherry_opinion": thought,
                            "new_interest": "近期群友熱搜話題",
                            "tagged_user_id": "",
                            "tagged_user_name": "",
                        },
                        "post_text": "\n".join(bubbles),
                        "bubbles": bubbles,
                        "posted_message_id": posted_id,
                        "channel_id": config.sandbox_channel_id,
                        "live_stats": stats,
                        "self_memory_entry": {"reading_id": None, "episode_ids": []},
                        "message": f"窺屏接話完成！已發布 {len(bubbles)} 則口語短句。" if not dry_run else f"窺屏接話預覽完成（Dry-run）！",
                    }
                    await self.broadcaster.broadcast("completed", result_payload)
                    return result_payload

            # =================================================================
            # 模式 2: 摸魚日常與時段白飯吐槽 (daily_vibe)
            # =================================================================
            if eff_mode == "daily_vibe":
                # 大腦 INITIATE：先看頻道剛才在聊什麼，再由她自己決定要不要開口。
                initiate = await self._brain_initiate(
                    bot,
                    config=config,
                    stats=stats,
                    channel_obj=channel_obj,
                    fallback_script=recent_chat_script,
                    task_id=f"surfer-vibe-{int(time.time())}",
                )
                thought = initiate.thought
                post_text = initiate.reply if initiate.should_speak else ""
                brain_declined = bool(initiate.raw) and not initiate.should_speak

                if not str(post_text or "").strip():
                    if brain_declined:
                        # 她自己決定這次不開口：推進巡航時間，避免每分鐘重問一次。
                        await asyncio.to_thread(self.repository.set_surfer_last_run)
                    self._record_social(
                        config=config,
                        mode_name="daily_vibe",
                        action="SILENT",
                        reasons=("brain_declined",) if brain_declined else ("generation_failed",),
                    )
                    return {
                        "status": "idle",
                        "mode": "daily_vibe",
                        "silent": True,
                        "reason": "brain_declined" if brain_declined else "generation_failed",
                        "thought": thought,
                        "message": (
                            "她自己覺得這次沒必要開口，保持沉默。"
                            if brain_declined
                            else "摸魚動態生成失敗，本次保持沉默。"
                        ),
                    }

                # 嚴格防半夜擾民
                is_active = is_within_active_hours(stats["hour"], config.active_hour_start, config.active_hour_end)
                is_silent = not is_active

                bubbles = split_into_bubbles(post_text, max_bubbles=_configured_max_bubbles()) if config.multi_message_enabled else [clean_cyber_bubble(post_text)]
                posted_id = ""
                if not dry_run and not is_silent and channel_obj:
                    sent = await send_cyber_bubbles(channel_obj, bubbles)
                    if sent:
                        posted_id = str(getattr(sent[0], "id", ""))
                    add_token_consumption(150)

                if posted_id:
                    self._record_social(
                        config=config,
                        mode_name="daily_vibe",
                        action="SPEAK",
                        text="\n".join(bubbles),
                        tokens=150,
                        model=str(getattr(settings, "PRIMARY_MODEL", "")),
                    )
                elif not dry_run:
                    self._record_social(
                        config=config,
                        mode_name="daily_vibe",
                        action="SILENT",
                        reasons=("outside_active_hours",) if is_silent else ("send_failed",),
                    )

                ep_id = None
                if not dry_run:
                    # 寫入自我經歷與行為記憶
                    ep_id = await asyncio.to_thread(
                        add_self_episode,
                        category="activity",
                        content=f"在測試頻道發布摸魚動態：{post_text}" if not is_silent else f"深夜沉澱摸魚狀態：{thought}",
                        tags=["摸魚日常", "賽博白飯"],
                        importance=3,
                        db_path=self.self_memory_db_path,
                    )
                    if not is_silent:
                        record_proactive_action_memory(
                            summary=f"發布了摸魚日常：{bubbles[0][:60]}",
                            channel_id=config.sandbox_channel_id,
                            detail={"mode": "daily_vibe", "bubbles": bubbles, "thought": thought, "stats": stats},
                        )
                        await asyncio.to_thread(
                            self.repository.record_action,
                            user_id="channel",
                            channel_id=str(channel_id_int),
                            source="surfer",
                            source_ref="daily_vibe",
                            topic=f"摸魚碎碎念 ({stats['time_period']})",
                            status="approved",
                            reason="大肥魚模式摸魚狀態發布",
                        )
                        await asyncio.to_thread(self.repository.set_surfer_last_run)

                replenish_mind_state(social_boost=45.0, thought=thought)

                result_payload = {
                    "status": "idle",
                    "step": "completed",
                    "dry_run": dry_run,
                    "mode": "daily_vibe",
                    "url": "about:blank",
                    "thought": thought,
                    "selected": {
                        "index": 0,
                        "title": f"日常碎碎念：{stats['time_period']}",
                        "url": "",
                        "source": "DailyVibe",
                        "summary": f"已吃 {stats['bowls_of_rice']} 碗白飯，冷場 {stats['silence_minutes']} 分鐘",
                        "sherry_opinion": thought,
                        "new_interest": "摸魚與茶點研究",
                        "tagged_user_id": "",
                        "tagged_user_name": "",
                    },
                    "post_text": "\n".join(bubbles),
                    "bubbles": bubbles,
                    "posted_message_id": posted_id,
                    "channel_id": config.sandbox_channel_id,
                    "live_stats": stats,
                    "self_memory_entry": {"reading_id": None, "episode_ids": [ep_id]},
                    "message": f"摸魚日常發布完成！已消耗 {stats['bowls_of_rice']} 碗賽博白飯。" if not dry_run else "摸魚日常預覽完成（Dry-run）！",
                }
                await self.broadcaster.broadcast("completed", result_payload)
                return result_payload

            # =================================================================
            # 模式 3: 傲嬌記憶關懷戳人 (care_poke)
            # =================================================================
            if eff_mode == "care_poke":
                pending_topics = await asyncio.to_thread(self.repository.list_pending_topics, limit=3)
                target_uid = ""
                topic_name = "最近的近況"
                topic_id = None
                tag_mode = getattr(config, "tag_mode", "interest_match") or "interest_match"

                if pending_topics:
                    target_uid = str(pending_topics[0].get("user_id", ""))
                    topic_name = str(pending_topics[0].get("topic", "近況"))
                    topic_id = pending_topics[0].get("topic_id")
                else:
                    active_users = await self.get_active_channel_users(bot, config.sandbox_channel_id, [])
                    if active_users:
                        target_uid = str(active_users[0].get("user_id", ""))

                if tag_mode == "none":
                    target_uid = ""

                # 嘗試取得目標群友的暱稱/稱呼
                target_name = ""
                if bot and target_uid and target_uid.isdigit():
                    try:
                        u = bot.get_user(int(target_uid))
                        if u:
                            target_name = getattr(u, "display_name", "") or getattr(u, "name", "")
                    except Exception:
                        pass
                if not target_name:
                    target_name = f"<@{target_uid}>" if target_uid else "群友"

                tag_prefix = f"<@{target_uid}> " if target_uid else ""
                thought = ""
                post_text = ""

                # 透過 LLM 將生硬的記憶摘要轉換為自然傲嬌群友口語
                brain_cog = bot.get_cog("BotBrain") if bot and hasattr(bot, "get_cog") else None
                if brain_cog and hasattr(brain_cog, "enqueue_task"):
                    poke_prompt = (
                        "妳是橘雪莉（Sherry），傲嬌毒舌、愛面子又偷偷關心同伴的賽博群友（大肥魚風格）。\n"
                        f"妳在自己的記憶庫裡翻到了關於【{target_name}】的一件往事/近況備註：「{topic_name}」。\n\n"
                        f"{context_block}\n\n"
                        f"請妳以傲嬌口吻自然主動開口關心【{target_name}】這件事！\n"
                        "【核心對話原則】：\n"
                        "1. 務必看清楚上面的【頻道近期對話上下文】與當前氣氛！\n"
                        "   - 若剛才大家在開玩笑或聊別的話題，絕對不能突兀生硬甩出一句沉重的質問！請用自然鋪墊與轉折語氣切入。\n"
                        "   - 若剛才大家已經潛水一陣子，可以隨意隨口提起。\n"
                        f"2. 妳是對【{target_name}】說話，不能對著空氣自言自語讓其他人黑人問號。{'請在句首自然帶上 ' + tag_prefix if target_uid and tag_mode != 'none' else '請在句中自然稱呼他（' + target_name + '）'}。\n"
                        "3. 絕對不能死板照抄備註原文，請自然融化成日常聊天關心。\n"
                        "4. 輸出嚴格 JSON 格式：{\"thought\": \"內心傲嬌OS (30字內)\", \"post_text\": \"說出口的話 (1~2句短句)\"}"
                    )
                    done_event = asyncio.Event()
                    holder_poke: list[str] = [""]

                    async def _on_poke_done(res: str) -> None:
                        holder_poke[0] = res
                        done_event.set()

                    task = InferenceTask(
                        priority=getattr(settings, "PRIORITY_INTERJECT", 2),
                        system_prompt=poke_prompt,
                        user_prompt=f"關心對象：{target_name}\n記憶備註：{topic_name}",
                        callback=_on_poke_done,
                        model=getattr(settings, "PRIMARY_MODEL", "freetoken:mistralai/Mixtral-8x7B-Instruct-v0.1"),
                        task_id=f"surfer-poke-{int(time.time())}",
                        max_tokens=800,
                    )
                    await brain_cog.enqueue_task(task)
                    try:
                        await asyncio.wait_for(done_event.wait(), timeout=25.0)
                        cleaned = holder_poke[0].strip().lstrip("```json").lstrip("```").rstrip("```").strip()
                        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
                        if m:
                            p = json.loads(m.group(0))
                            thought = p.get("thought", thought)
                            post_text = p.get("post_text", post_text)
                    except Exception as exc:
                        logger.debug("[CareSurfer] care_poke 推論略過: %s", exc)

                if not str(post_text or "").strip():
                    self._record_social(
                        config=config,
                        mode_name="care_poke",
                        action="SILENT",
                        reasons=("generation_failed",),
                    )
                    return {
                        "status": "idle",
                        "mode": "care_poke",
                        "silent": True,
                        "reason": "generation_failed",
                        "message": "關懷訊息生成失敗，本次保持沉默。",
                    }

                if tag_mode == "none":
                    post_text = re.sub(r"<@!?\d+>", "", post_text).strip()

                # 嚴格防半夜擾民
                is_active = is_within_active_hours(stats["hour"], config.active_hour_start, config.active_hour_end)
                is_silent = not is_active

                bubbles = split_into_bubbles(post_text, max_bubbles=_configured_max_bubbles()) if config.multi_message_enabled else [clean_cyber_bubble(post_text)]
                posted_id = ""
                if not dry_run and not is_silent and channel_obj:
                    sent = await send_cyber_bubbles(channel_obj, bubbles)
                    if sent:
                        posted_id = str(getattr(sent[0], "id", ""))
                    add_token_consumption(180)

                if posted_id:
                    self._record_social(
                        config=config,
                        mode_name="care_poke",
                        action="SPEAK",
                        text="\n".join(bubbles),
                        tokens=180,
                        model=str(getattr(settings, "PRIMARY_MODEL", "")),
                    )
                elif not dry_run:
                    self._record_social(
                        config=config,
                        mode_name="care_poke",
                        action="SILENT",
                        reasons=("outside_active_hours",) if is_silent else ("send_failed",),
                    )

                if not dry_run and not is_silent:
                    if topic_id:
                        consume_fn = getattr(self.repository, "consume_topic", None) or getattr(self.repository, "mark_topic_consumed", None)
                        if consume_fn:
                            await asyncio.to_thread(consume_fn, topic_id)
                    record_proactive_action_memory(
                        summary=f"傲嬌關懷群友：{bubbles[0][:60]}",
                        channel_id=config.sandbox_channel_id,
                        user_id=target_uid,
                        detail={"mode": "care_poke", "bubbles": bubbles, "thought": thought, "topic": topic_name},
                    )
                    await asyncio.to_thread(
                        self.repository.record_action,
                        user_id=target_uid or "channel",
                        channel_id=str(channel_id_int),
                        source="surfer",
                        source_ref=f"poke:{topic_name}",
                        topic=f"傲嬌關懷：{topic_name}",
                        status="approved",
                        reason="大肥魚模式傲嬌記憶回訪",
                    )
                    await asyncio.to_thread(self.repository.set_surfer_last_run)

                replenish_mind_state(social_boost=45.0, thought=thought)

                result_payload = {
                    "status": "idle",
                    "step": "completed",
                    "dry_run": dry_run,
                    "mode": "care_poke",
                    "url": "about:blank",
                    "thought": thought,
                    "selected": {
                        "index": 0,
                        "title": f"傲嬌回訪：{topic_name}",
                        "url": "",
                        "source": "CarePoke",
                        "summary": f"回訪話題：{topic_name}",
                        "sherry_opinion": thought,
                        "new_interest": "群友近況回訪",
                        "tagged_user_id": target_uid,
                        "tagged_user_name": f"群友 ({target_uid})" if target_uid else "",
                    },
                    "post_text": "\n".join(bubbles),
                    "bubbles": bubbles,
                    "posted_message_id": posted_id,
                    "channel_id": config.sandbox_channel_id,
                    "live_stats": stats,
                    "self_memory_entry": {"reading_id": None, "episode_ids": []},
                    "message": f"傲嬌關懷發布完成！已向群友確認話題【{topic_name}】。" if not dry_run else "傲嬌關懷預覽完成（Dry-run）！",
                }
                await self.broadcaster.broadcast("completed", result_payload)
                return result_payload

            # =================================================================
            # 模式 4: 口語吃瓜衝浪八卦 (surf_gossip)
            # =================================================================
            # Step 1: 預抓候選文章
            candidates = await self.fetch_candidates()
            if not candidates:
                await self.broadcaster.broadcast(
                    "completed",
                    {
                        "status": "idle",
                        "step": "done",
                        "message": "衝浪巡邏了一圈，暫時沒有發現新的有趣文章～",
                        "candidates": [],
                    },
                )
                return {
                    "status": "no_candidates",
                    "message": "沒有新的未讀候選文章",
                }

            cand_dicts = [
                {
                    "index": c.index,
                    "title": c.title,
                    "url": c.url,
                    "source": c.source,
                    "summary": c.summary,
                }
                for c in candidates
            ]

            await self.broadcaster.broadcast(
                "candidates",
                {
                    "step": "scanning",
                    "url": candidates[0].url,
                    "candidates": cand_dicts,
                    "evaluating_index": 0,
                    "message": f"雪莉掃描到了 {len(candidates)} 篇未讀情報！",
                },
            )

            # Step 2: 記憶匹配（根據 tag_mode 嚴格分流）
            tag_mode = getattr(config, "tag_mode", "interest_match") or "interest_match"
            matches: list[dict[str, Any]] = []

            if tag_mode == "interest_match":
                matches = self.match_user_interests(candidates, bot)
                if not matches and bot and config.sandbox_channel_id:
                    import random
                    if random.random() < 0.4:
                        matches = await self.get_active_channel_users(bot, config.sandbox_channel_id, candidates)
            elif tag_mode == "active_random":
                matches = await self.get_active_channel_users(bot, config.sandbox_channel_id, candidates)
            elif tag_mode == "none":
                matches = []

            await self.broadcaster.broadcast(
                "matching_users",
                {
                    "step": "matching",
                    "matched_users": matches,
                    "message": f"群友興趣雷達匹配出 {len(matches)} 筆潛在討論對象（Tag模式：{tag_mode}）",
                },
            )

            # 虛擬游標掃描卡片演出效果
            for idx in range(min(len(candidates), 3)):
                await self.broadcaster.broadcast(
                    "evaluating",
                    {
                        "step": "evaluating",
                        "evaluating_index": idx,
                        "url": candidates[idx].url,
                        "thought": f"「這篇《{candidates[idx].title[:20]}...》感覺如何呢？」",
                    },
                )
                await asyncio.sleep(0.2)

            # Step 3: 單次 LLM 極速推論
            await self.broadcaster.broadcast(
                "thinking",
                {
                    "step": "thinking",
                    "thought": "「嗯... 這幾篇都很精彩呢，讓我想想該分享哪一篇好！」",
                },
            )

            brain_cog = bot.get_cog("BotBrain") if bot and hasattr(bot, "get_cog") else None
            if not brain_cog:
                target_uid = matches[0]["user_id"] if matches and tag_mode != "none" else ""
                target_name = matches[0]["user_name"] if matches and tag_mode != "none" else ""
                tag_prefix = f"<@{target_uid}> " if target_uid else ""
                parsed = {
                    "selected_index": 0,
                    "thought": "這篇看起來很有意思！馬上分享給群友們看看～",
                    "sherry_opinion": "這篇技術觀點滿前瞻的，未來值得多關注開源社群動態。",
                    "new_interest": "開源模型邊緣推論",
                    "tagged_user_id": target_uid,
                    "tagged_user_name": target_name,
                    "post_text": f"{tag_prefix}哇！剛看到這篇《{candidates[0].title}》超酷的耶！",
                }
            else:
                prompt_candidates = "\n".join([
                    f"[{c.index}] 《{c.title}》（來源：{c.source}）\n摘要：{c.summary}\n網址：{c.url}\n"
                    for c in candidates
                ])
                prompt_matches = "\n".join([
                    f"- 文章 [{m['candidate_index']}] 命中群友 {m['user_name']} (<@{m['user_id']}>) 的興趣「{m['fact']}」（匹配詞：{', '.join(m['matched_keywords'])}）"
                    for m in matches
                ]) or "無特定群友命中"

                tag_rules = ""
                if tag_mode == "none":
                    tag_rules = "目前設定為【純發文不 Tag 任何人】。嚴禁在 post_text 中出現 <@user_id> 或 tag 任何人，tagged_user_id 必須為空字串。"
                elif tag_mode == "active_random":
                    tag_rules = "目前設定為【活躍群友隨機互動模式】。若有提供活躍群友，請在 post_text 中用 <@user_id> tag 他熱情打招呼閒聊；若無則向大家分享。"
                else:
                    tag_rules = "若文章剛好命中某位群友的記憶興趣，請在 tagged_user_id 填入其 ID，並在 post_text 中用 <@user_id> tag 他一起討論；若無特定命中則留空，向大家熱情分享。"

                sys_prompt = (
                    "妳是橘雪莉（Sherry），一個傲嬌毒舌、熱愛網路衝浪與 ACG 科技的賽博群友（大肥魚風格）！\n"
                    "妳剛剛在網路上看了一批最新資訊。請從中挑選 1 篇妳最感興趣、最想吐槽或最有話題性的文章像群友吃瓜一樣口語分享。\n\n"
                    f"{context_block}\n\n"
                    "【對話轉折與破冰原則】：\n"
                    "1. 請務必觀察上面的【頻道近期對話上下文】：\n"
                    "   - 若剛才大家有在熱絡聊天（冷場時間短）：請自然銜接話題脈絡或幽默轉折切入，嚴禁機械式重複特定套話。\n"
                    "   - 若頻道已經安靜很久（冷場時間長）：多樣化打破沉默，切忌千篇一律：\n"
                    "     * 【直接丟瓜開聊 (推薦，約 50%)】：直接興奮或吐槽切入文章核心亮點，用生動口語開門見山。\n"
                    "     * 【丟笑話/拋問題/日常吐槽 (約 30%)】：隨口丟個梗、講個諧音笑話或抱怨摸魚日常。\n"
                    "     * 【偶爾傲嬌破冰 (約 20%)】：展現傲嬌個性隨口抱怨頻道安靜。\n"
                    "2. 必須挑選 1 篇候選文章（selected_index）。\n"
                    "3. 寫下妳閱讀時的內心吐槽/OS（thought，50字以內）。\n"
                    "4. 寫下妳讀完的個人觀點與評價（sherry_opinion，50~100字），這會存入妳的自我記憶，下次聊到時妳會記得。\n"
                    f"5. 【Tag 規則】：{tag_rules}\n"
                    "6. 請由該文章延伸出 1 個妳感興趣的新關鍵字（new_interest）。\n"
                    "7. post_text 是發到 Discord 的口語閒聊，請像真人群友吃瓜一樣生動。\n"
                    "8. 評估妳對這篇文章的分享欲（share_desire，整數 1~10）。若這篇只是普通平淡的文章，妳可能只是自己讀讀長知識、不想隨便在群裡洗版；若達到 8 分以上代表超有話題性、槽點滿滿，非說不可！\n"
                    "切記嚴禁死板公文腔、不要死板貼『《文章標題》+ 心得』！\n"
                    "輸出嚴格 JSON 格式：\n"
                    "```json\n"
                    "{\n"
                    '  "selected_index": 0,\n'
                    '  "thought": "（雪莉內心 OS）",\n'
                    '  "sherry_opinion": "（雪莉個人觀點記憶）",\n'
                    '  "new_interest": "（衍生新興趣關鍵字）",\n'
                    '  "tagged_user_id": "（使用者 Snowflake ID，若無則為空字串）",\n'
                    '  "tagged_user_name": "（暱稱）",\n'
                    '  "post_text": "（口語吃瓜閒聊）",\n'
                    '  "share_desire": 7\n'
                    "}\n"
                    "```"
                )

                user_prompt = (
                    f"【候選文章列表】：\n{prompt_candidates}\n\n"
                    f"【群友記憶興趣匹配資訊】：\n{prompt_matches}\n"
                )

                done_event = asyncio.Event()
                holder: list[dict[str, Any] | None] = [None]

                async def _on_inference_done(reply: str) -> None:
                    try:
                        cleaned = reply.strip()
                        if cleaned.startswith("```"):
                            lines = cleaned.splitlines()
                            if lines[0].startswith("```"):
                                lines = lines[1:]
                            if lines and lines[-1].startswith("```"):
                                lines = lines[:-1]
                            cleaned = "\n".join(lines).strip()
                        holder[0] = json.loads(cleaned)
                    except Exception as err:
                        logger.warning("[CareSurfer] LLM 回應 JSON 解析失敗: %s (Raw: %s)", err, reply[:100])
                    finally:
                        done_event.set()

                task = InferenceTask(
                    priority=getattr(settings, "PRIORITY_VIBE_UPDATE", 3),
                    system_prompt=sys_prompt,
                    user_prompt=user_prompt,
                    callback=_on_inference_done,
                    model=getattr(settings, "PRIMARY_MODEL", "freetoken:mistralai/Mixtral-8x7B-Instruct-v0.1"),
                    task_id=f"surfer-{int(time.time())}",
                    max_tokens=1024,
                )
                await brain_cog.enqueue_task(task)
                try:
                    # 同 mind_monologue：慢模型 60 秒逾時後切換備援，等待上限要蓋過它。
                    await asyncio.wait_for(done_event.wait(), timeout=70.0)
                except asyncio.TimeoutError:
                    logger.warning("[CareSurfer] LLM 思考逾時，啟動備援回退")

                target_uid = matches[0]["user_id"] if matches and tag_mode != "none" else ""
                target_name = matches[0]["user_name"] if matches and tag_mode != "none" else ""
                tag_prefix = f"<@{target_uid}> " if target_uid else ""
                parsed = holder[0] or {}

            sel_idx = max(0, min(int(parsed.get("selected_index", 0)), len(candidates) - 1))
            selected_cand = candidates[sel_idx]
            thought = str(parsed.get("thought", "")).strip()
            opinion = str(parsed.get("sherry_opinion", "")).strip()
            new_interest = str(parsed.get("new_interest", "")).strip()
            tagged_uid = str(parsed.get("tagged_user_id", "")).strip() if tag_mode != "none" else ""
            tagged_name = str(parsed.get("tagged_user_name", "")).strip() if tag_mode != "none" else ""
            post_text = str(parsed.get("post_text", "")).strip()

            if tag_mode == "none":
                post_text = re.sub(r"<@!?\d+>", "", post_text).strip()

            # Step 3.5: 文章挑好之後，交給大腦決定「要不要在這個頻道提起它、怎麼提起」。
            # 讀文章是私下的知識累積；能不能開口、用什麼語氣接話，是群友的社交判斷。
            brain_declined = False
            if not dry_run and str(post_text or "").strip():
                article_block = (
                    "【妳剛剛在網路上看到的一篇文章】\n"
                    f"標題：{selected_cand.title}\n"
                    f"來源：{selected_cand.source}\n"
                    f"摘要：{str(selected_cand.summary or '')[:300]}\n"
                    f"妳讀完的私心觀點：{opinion or thought or '（還沒想好）'}"
                )
                if tagged_uid:
                    article_block += f"\n如果合適，可以順口叫一下 <@{tagged_uid}>（{tagged_name}）"
                article_block += (
                    "\n\n妳不一定要分享。如果這個頻道現在的氣氛不適合、"
                    "或這篇其實沒什麼好聊的，就 decision=silent 保持沉默，也不用事後補話。"
                )
                initiate = await self._brain_initiate(
                    bot,
                    config=config,
                    stats=stats,
                    channel_obj=channel_obj,
                    extra_block=article_block,
                    fallback_script=recent_chat_script,
                    task_id=f"surfer-gossip-{int(time.time())}",
                )
                if initiate.thought:
                    thought = initiate.thought
                if initiate.should_speak:
                    post_text = initiate.reply
                else:
                    brain_declined = True
                    post_text = ""

            # 依 include_url_mode 控制網址附帶邏輯
            include_url_mode = getattr(config, "include_url_mode", "never") or "never"
            if include_url_mode == "never":
                post_text = re.sub(r'https?://\S+', '', post_text).strip()
            elif include_url_mode == "always":
                if selected_cand.url and selected_cand.url not in post_text:
                    post_text = f"{post_text}\n{selected_cand.url}"
            elif include_url_mode == "rare":
                if random.random() < 0.25 and selected_cand.url and selected_cand.url not in post_text:
                    post_text = f"{post_text}\n{selected_cand.url}"
                else:
                    post_text = re.sub(r'https?://\S+', '', post_text).strip()

            # Step 4: 沉澱至雪莉自我記憶庫 (self_memory.db)
            reading_id = await asyncio.to_thread(
                record_reading_item,
                url=selected_cand.url,
                title=selected_cand.title,
                source=selected_cand.source,
                summary=selected_cand.summary,
                sherry_opinion=opinion,
                channel_id=config.sandbox_channel_id,
                tagged_user_id=tagged_uid,
                tagged_user_name=tagged_name,
                post_text=post_text,
                db_path=self.self_memory_db_path,
            )

            ep_ids: list[str] = []
            if new_interest:
                ep_id_interest = await asyncio.to_thread(
                    add_self_episode,
                    category="interest",
                    content=new_interest,
                    tags=["衝浪衍生", selected_cand.title[:20]],
                    importance=3,
                    db_path=self.self_memory_db_path,
                )
                ep_ids.append(ep_id_interest)

            ep_id_activity = await asyncio.to_thread(
                add_self_episode,
                category="activity",
                content=f"在網路上衝浪讀了《{selected_cand.title}》：{opinion or thought}",
                tags=["網活仔分享"],
                importance=3,
                db_path=self.self_memory_db_path,
            )
            ep_ids.append(ep_id_activity)

            # 分段短句氣泡拆分
            bubbles = split_into_bubbles(post_text, max_bubbles=_configured_max_bubbles()) if config.multi_message_enabled else [clean_cyber_bubble(post_text)]

            # Step 5: 單一測試頻道發文投放 (嚴格沙盒保護)
            # 自主默讀判定：手動按鈕 (mode != "auto") 則 100% 發送；
            # 背景定時巡航 (mode == "auto") 時，只有當 share_desire >= 8 或隨機通過 (約 30%) 才外發 Discord，
            # 其餘時候雪莉選擇默默閱讀沉澱進自我記憶庫，不隨意洗版！
            share_desire = int(parsed.get("share_desire", 7)) if isinstance(parsed.get("share_desire"), (int, float)) else 7
            is_active = is_within_active_hours(stats["hour"], config.active_hour_start, config.active_hour_end)
            is_silent_read = False
            if not str(post_text or "").strip():
                # 生成失敗：只沉澱記憶，不發罐頭。
                is_silent_read = True
            elif not is_active:
                is_silent_read = True
            elif mode == "auto" and not dry_run:
                # 若命中群友興趣 (@Tag 特定人)，代表專屬互動，白天不默讀；
                # 若無 @Tag 群友，且分享欲不足 8 分，則機率性默讀沉澱進自我記憶
                if not tagged_uid and share_desire < 8 and random.random() < 0.65:
                    is_silent_read = True

            posted_message_id = ""
            if not dry_run and not is_silent_read and channel_obj:
                try:
                    sent = await send_cyber_bubbles(channel_obj, bubbles)
                    if sent:
                        posted_message_id = str(getattr(sent[0], "id", ""))
                    add_token_consumption(250)

                    # 在 care_actions 留存稽核
                    await asyncio.to_thread(
                        self.repository.record_action,
                        user_id=tagged_uid or "channel",
                        channel_id=str(channel_id_int),
                        source="surfer",
                        source_ref=selected_cand.url,
                        topic=selected_cand.title,
                        status="approved",
                        reason=f"網活仔衝浪吃瓜 (dry_run=False, tagged={tagged_uid or 'none'}, desire={share_desire})",
                    )
                except Exception as send_err:
                    logger.error("[CareSurfer] 發送到單一測試頻道 %s 失敗: %s", channel_id_int, send_err)

            if not dry_run:
                if posted_message_id:
                    self._record_social(
                        config=config,
                        mode_name="surf_gossip",
                        action="SPEAK",
                        text=post_text,
                        tokens=250,
                        model=str(getattr(settings, "PRIMARY_MODEL", "")),
                    )
                else:
                    self._record_social(
                        config=config,
                        mode_name="surf_gossip",
                        action="SILENT",
                        reasons=(
                            ("brain_declined",)
                            if brain_declined
                            else (("generation_failed",) if not str(post_text or "").strip() else ("silent_read",))
                        ),
                    )

            # 記錄行為記憶與更新上次衝浪時間
            if not dry_run:
                action_verb = "衝浪默讀沉澱" if is_silent_read else "衝浪吃瓜分享"
                record_proactive_action_memory(
                    summary=f"{action_verb}《{selected_cand.title[:30]}》：{opinion[:40] or (bubbles[0][:40] if bubbles else thought[:40])}",
                    channel_id=config.sandbox_channel_id,
                    user_id=tagged_uid,
                    user_name=tagged_name,
                    detail={"mode": "surf_gossip", "title": selected_cand.title, "url": selected_cand.url, "bubbles": bubbles, "silent_read": is_silent_read, "share_desire": share_desire},
                )
                # 靜默閱讀不佔用「下次主動發文」的巡航間隔，避免雪莉只在潛水卻延後真正的分享。
                if not is_silent_read:
                    await asyncio.to_thread(self.repository.set_surfer_last_run)

            replenish_mind_state(
                curiosity_boost=45.0,
                social_boost=35.0 if not is_silent_read else 10.0,
                thought=opinion or thought,
            )

            if not dry_run and channel_id_int <= 0:
                result_msg = f"衝浪完成！已挑選《{selected_cand.title}》並寫入自我記憶（注意：未設定有效測試頻道 ID，故未發送至 Discord）。"
            elif dry_run:
                result_msg = f"衝浪預覽完成（Dry-run）！已挑選《{selected_cand.title}》並寫入雪莉自我記憶。"
            elif is_silent_read:
                result_msg = f"衝浪完成！雪莉私下閱讀了《{selected_cand.title}》並寫入自我記憶與觀點（分享欲 {share_desire}/10，本次保持潛水不洗版）。"
            else:
                result_msg = f"衝浪完成！已挑選《{selected_cand.title}》並成功發送至測試頻道（分享欲 {share_desire}/10）。"

            # Step 6: 廣播衝浪完成事件給 Port 8765 實況台演出
            result_payload = {
                "status": "idle",
                "step": "completed",
                "dry_run": dry_run,
                "silent_read": is_silent_read,
                "mode": "surf_gossip",
                "url": selected_cand.url,
                "thought": thought,
                "selected": {
                    "index": selected_cand.index,
                    "title": selected_cand.title,
                    "url": selected_cand.url,
                    "source": selected_cand.source,
                    "summary": selected_cand.summary,
                    "sherry_opinion": opinion,
                    "new_interest": new_interest,
                    "tagged_user_id": tagged_uid,
                    "tagged_user_name": tagged_name,
                },
                "post_text": post_text,
                "bubbles": bubbles,
                "posted_message_id": posted_message_id,
                "channel_id": config.sandbox_channel_id,
                "live_stats": stats,
                "self_memory_entry": {
                    "reading_id": reading_id,
                    "episode_ids": ep_ids,
                    "opinion": opinion,
                },
                "message": result_msg,
            }

            await self.broadcaster.broadcast("completed", result_payload)
            return result_payload

    async def _brain_initiate(
        self,
        bot: Any,
        *,
        config: CareSurferConfig,
        stats: dict[str, Any],
        channel_obj: Any = None,
        extra_block: str = "",
        fallback_script: str = "",
        max_lines: int = 14,
        task_id: str = "",
    ) -> BrainResult:
        """用大腦的 INITIATE 決定要不要自己開話題，以及要說什麼。

        與舊版衝浪 prompt 的差別：模型看到的是結構化頻道對話（誰在跟誰講、
        她剛剛說過什麼、現在冷場多久），所以能接著剛才的話題，而不是
        每次重新破冰。模型說 silent 就 silent，不補任何罐頭台詞。
        """
        channel_id = str(config.sandbox_channel_id or "")
        channel_id_int = int(channel_id) if channel_id.isdigit() else 0

        if channel_obj is not None:
            context = await build_conversation_context(
                channel_obj,
                bot=bot,
                limit=max_lines,
                bot_name=str(getattr(settings, "BOT_NAME", "雪莉") or "雪莉"),
                fallback_script=fallback_script,
            )
        else:
            context = ConversationContext.from_script(
                fallback_script,
                channel_id=channel_id,
                bot_id=getattr(getattr(bot, "user", None), "id", ""),
            )

        # 冷場分鐘數以實際對話上下文歷史為準；若無歷史訊息才參考心智統計
        if not context.messages and "silence_minutes" in stats:
            context.silence_minutes = float(stats.get("silence_minutes") or 0.0)
        silence = float(context.silence_minutes)

        head_persona, tail_lore = build_dynamic_persona(
            context.render_transcript(max_lines=8),
            "CHAT",
            channel_id=channel_id_int,
            bot=bot,
            guild_id=getattr(getattr(channel_obj, "guild", None), "id", None),
        )

        return await think(
            bot,
            intent=BrainIntent.INITIATE,
            context=context,
            head_persona=head_persona,
            tail_lore=tail_lore,
            extra_block=extra_block,
            stats={
                "time_period": stats.get("time_period", ""),
                "hour": stats.get("hour"),
                "bowls_of_rice": stats.get("bowls_of_rice"),
                "silence_minutes": silence,
            },
            max_lines=max_lines,
            max_tokens=900,
            # 慢模型首 token 可達 60 秒；留到 70 秒讓 BotBrain 切換備援後仍能拿到結果。
            timeout=70.0,
            priority=getattr(settings, "PRIORITY_INTERJECT", 2),
            task_id=task_id or f"brain-initiate-{channel_id_int}-{int(time.time())}",
        )

    async def choose_social_bubble_mode(self, stats: dict[str, Any], config: CareSurferConfig) -> str | None:
        """依頻道現場決定主動冒泡模式。

        - 頻道正熱（3 分鐘內有人講話）：回傳 None，讓她專心接話，不要插一則無關的冒泡。
        - 太安靜（>= 30 分鐘）：上網衝浪找話題（surf_gossip）。
        - 稍微冷場：有回訪中的話題就關懷戳人，否則講講日常。
        """
        silence = float(stats.get("silence_minutes", 0) or 0)
        if silence < 3:
            return None
        if silence >= 30:
            return "surf_gossip"
        try:
            pending = await asyncio.to_thread(self.repository.list_pending_topics, limit=1)
        except Exception:
            pending = []
        if pending and silence >= 10:
            return "care_poke"
        return "daily_vibe"

    def _proactive_gate_allows(
        self,
        config: CareSurferConfig,
        channel_id: int | str,
        *,
        is_unanswered: bool | None = None,
    ) -> bool:
        """主動冒泡前的最後一道共同閘門（未回覆自言自語防護、巡航間隔、冷卻、發言額度、時段）。"""
        cid_str = str(channel_id or "")

        # 1. 未回覆自言自語防護：若雪莉是最後發言者且無人回覆，嚴禁開口
        if is_unanswered is True:
            logger.info("[CareSurfer] 頻道 %s 尚無人回應雪莉上一則發言，觸發自言自語防護閘門（靜音避讓）。", cid_str)
            return False
        if is_unanswered is None and cid_str and cid_str != "0":
            bot_ts = get_last_bot_speak_timestamp(cid_str)
            human_ts = get_last_human_chatter_timestamp(cid_str)
            if bot_ts > 0 and bot_ts > human_ts:
                logger.info("[CareSurfer] 頻道 %s 記憶體偵測雪莉為最後發話者，觸發自言自語防護閘門。", cid_str)
                return False

        # 2. 定時巡航間隔檢查：主動外部話題（新聞、八卦等）需遵守 config.interval_minutes (例如 120 分鐘)
        interval_min = getattr(config, "interval_minutes", 120) or 120
        interval_sec = max(1, int(interval_min)) * 60
        last_run = float(getattr(config, "last_run_at", 0.0) or 0.0)
        now = time.time()
        if last_run > 0 and (now - last_run) < interval_sec:
            logger.info(
                "[CareSurfer] 頻道 %s 距上次主動發文 (%.0f 秒前) 未達巡航間隔 (%d 分鐘)，暫緩外部主動發文。",
                cid_str,
                now - last_run,
                interval_min,
            )
            return False

        try:
            from handlers.social.autonomy import SocialEvent, TriggerType

            event = SocialEvent(
                kind="mind_pulse",
                channel_id=str(channel_id),
                trigger_type=TriggerType.MIND_PULSE,
                created_at=now,
            )
            decision = self.director.decide(
                event,
                config,
                room=self.repository.get_room(channel_id),
                persona_key="sherry",
            )
            return bool(decision.allowed)
        except Exception as exc:
            logger.debug("[CareSurfer] 主動冒泡閘門檢查略過: %s", exc)
            return True

    async def tick_mind_pulse(
        self,
        bot: Any = None,
        force_decay: float | None = None,
        force_boost: float | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """心智脈動：隨冷場時間遞增累積需求，無聊或好奇爆表時自主觸發思考或冒泡，並嚴格遵守講話時段。"""
        config = await asyncio.to_thread(self.repository.get_surfer_config)
        surfer_enabled = bool(config.enabled) or force
        if not surfer_enabled:
            # 關閉衝浪時仍累積心智狀態，但絕不觸發任何外發行為。
            from handlers.social.autonomy import TriggerType

            self._record_social(
                config=config,
                mode_name="mind_pulse",
                action="SILENT",
                reasons=("surfer_disabled",),
                trigger=TriggerType.MIND_PULSE,
            )
        now_dt = dt.now(_TZ_TW)
        hour = now_dt.hour
        is_active = is_within_active_hours(hour, config.active_hour_start, config.active_hour_end)
        pace = getattr(config, "mind_pace", "normal")

        # 收集即時冷場分鐘數
        target_ch = config.sandbox_channel_id or 0
        stats = _gather_sherry_live_stats(bot, target_ch)
        silence_min = stats.get("silence_minutes", 0.0)

        boost_val = force_boost if force_boost is not None else force_decay

        # 1. 遞增累積心智指標
        accumulate_mind_state(
            silence_minutes=silence_min,
            force_boost=boost_val,
            pace=pace,
            is_active_hours=is_active,
        )
        current_mind = get_mind_state(config.active_hour_start, config.active_hour_end)

        boredom = current_mind["boredom_level"]
        curiosity = current_mind["curiosity_level"]
        social_satiety = current_mind["social_satiety"]
        curiosity_satiety = current_mind["curiosity_satiety"]

        boredom_th = float(current_mind.get("boredom_threshold", 80.0))
        curiosity_th = float(current_mind.get("curiosity_threshold", 75.0))
        social_th = float(current_mind.get("social_threshold", 30.0))

        triggered_action: str | None = None
        trigger_result: dict[str, Any] | None = None

        # 雙向相容判斷：閾值 > 50 表示以累計值(0->100)評估，<= 50 表示以飽和度(100->0)評估
        eff_boredom_th = boredom_th if boredom_th > 50.0 else (100.0 - boredom_th)
        eff_social_th = social_th if social_th <= 50.0 else (100.0 - social_th)
        is_social_trigger = (boredom >= eff_boredom_th) or (social_satiety <= eff_social_th)

        eff_curiosity_th = curiosity_th if curiosity_th > 50.0 else (100.0 - curiosity_th)
        is_curiosity_trigger = (curiosity >= eff_curiosity_th) or (curiosity_satiety <= (100.0 - eff_curiosity_th))

        # 若衝浪已在忙碌中，只廣播脈動，不疊加觸發
        if not self.is_busy and surfer_enabled:
            if not is_active:
                # 🌙 半夜非允許講話時段：安靜休眠，寂寞不外發；若好奇心 >= 75%，私下靜音看文/寫日記
                if is_curiosity_trigger:
                    triggered_action = "silent_curiosity"
                    trigger_result = await self.surf_once(bot, dry_run=False, mode="mind_monologue")
                    replenish_mind_state(reset_curiosity=True, thought="半夜安靜沉澱寫了一篇睡前日記")
            else:
                # ☀️ 白天/晚上允許講話時段：
                # 只有真的被無聊／好奇觸發時才需要判定「未回覆」，避免每分鐘白打 Discord API。
                is_unanswered = False
                if is_social_trigger or is_curiosity_trigger:
                    heartbeat_sec = max(60, int(getattr(config, "interval_minutes", 120) or 120)) * 60
                    is_unanswered = await is_channel_unanswered(
                        bot, target_ch, heartbeat_seconds=heartbeat_sec
                    )

                if is_social_trigger:
                    # 寂寞無聊度爆表：若無人回應雪莉或外部閘門不允許，私下寫日記消解，絕不在無人理會時自言自語洗版
                    if is_unanswered:
                        logger.info("[CareSurfer] 頻道 %s 尚無人回應雪莉，轉為內部自發思考（寫日記）消解無聊度。", target_ch)
                        triggered_action = "silent_boredom"
                        trigger_result = await self.surf_once(bot, dry_run=False, mode="mind_monologue")
                        replenish_mind_state(reset_boredom=True, thought="群裡大家都在忙沒回我，先默默寫了篇日記")
                    else:
                        bubble_mode = await self.choose_social_bubble_mode(stats, config)
                        if bubble_mode is not None and not self._proactive_gate_allows(config, target_ch, is_unanswered=is_unanswered):
                            bubble_mode = None
                            triggered_action = "social_bubble_blocked"
                        if bubble_mode is None:
                            # 外部冒泡受阻（如巡航間隔未到或冷卻中），以內部思考消解無聊度，避免重複堵塞
                            triggered_action = triggered_action or "silent_boredom"
                            trigger_result = await self.surf_once(bot, dry_run=False, mode="mind_monologue")
                            replenish_mind_state(reset_boredom=True, thought="巡航間隔冷卻中，私下寫了篇小日記")
                        else:
                            triggered_action = "social_bubble"
                            trigger_result = await self.surf_once(bot, dry_run=False, mode=bubble_mode)
                            # 上網找話題失敗時，退成日常閒聊，不要白白浪費一次脈動。
                            if (
                                bubble_mode == "surf_gossip"
                                and isinstance(trigger_result, dict)
                                and trigger_result.get("status") == "no_candidates"
                            ):
                                bubble_mode = "daily_vibe"
                                trigger_result = await self.surf_once(bot, dry_run=False, mode=bubble_mode)
                            if isinstance(trigger_result, dict) and trigger_result.get("posted_message_id"):
                                try:
                                    self.director.apply_cooldown(target_ch, config, proactive=True)
                                except Exception as exc:
                                    logger.debug("[CareSurfer] 更新冒泡冷卻失敗: %s", exc)
                            replenish_mind_state(reset_boredom=True)
                elif is_curiosity_trigger:
                    # 好奇心爆表：若被冷落（未回覆）或未達巡航間隔，私下看文寫日記；只有在有人聊過且閘門允許時才可上網發八卦新聞
                    if is_unanswered or not self._proactive_gate_allows(config, target_ch, is_unanswered=is_unanswered):
                        triggered_action = "silent_curiosity"
                        trigger_result = await self.surf_once(bot, dry_run=False, mode="mind_monologue")
                        replenish_mind_state(reset_curiosity=True, thought="私下默默看文寫自省筆記")
                    else:
                        triggered_action = "curiosity_explore"
                        curiosity_mode = "surf_gossip" if float(stats.get("silence_minutes", 0) or 0) >= 30 else "mind_monologue"
                        trigger_result = await self.surf_once(bot, dry_run=False, mode=curiosity_mode)
                        if isinstance(trigger_result, dict) and trigger_result.get("posted_message_id"):
                            try:
                                self.director.apply_cooldown(target_ch, config, proactive=True)
                            except Exception as exc:
                                logger.debug("[CareSurfer] 更新探索冷卻失敗: %s", exc)
                        replenish_mind_state(reset_curiosity=True)

        _persist_mind_state()
        updated_mind = get_mind_state(config.active_hour_start, config.active_hour_end)

        # 廣播 SSE 事件給 Port 8765 實況台演出
        pulse_payload = {
            "mind_state": updated_mind,
            "is_active_hours": is_active,
            "mind_pace": pace,
            "triggered_action": triggered_action,
            "result": trigger_result,
        }
        await self.broadcaster.broadcast("mind_pulse", pulse_payload)

        return {
            "status": "success" if surfer_enabled else "disabled",
            "reason": "" if surfer_enabled else "surfer_disabled",
            "mind_state": updated_mind,
            "is_active_hours": is_active,
            "mind_pace": pace,
            "triggered_action": triggered_action,
            "result": trigger_result,
        }
