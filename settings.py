"""
settings.py
===========
Central configuration hub for the Discord AI Agent.

All tuneable parameters, model definitions, persona text, and file paths
are declared here. Import this module throughout the project instead of
hardcoding values inline.

Environment variables (secrets) are loaded via python-dotenv; everything
else is a typed constant that operators can adjust without touching
business logic.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

from file.AI_personality import BOT_NAME, CORE_PERSONA, LOREBOOK

# ---------------------------------------------------------------------------
try:
    load_dotenv(override=True, encoding="utf-8")
except Exception as e:
    logger.warning("Failed to load .env file: %s", e)

# ---------------------------------------------------------------------------
# Project Paths
# ---------------------------------------------------------------------------
BASE_DIR: Path = Path(__file__).resolve().parent
DATA_DIR: Path = BASE_DIR / "data"
FILE_DIR: Path = BASE_DIR / "file"
SOUL_DIR: Path = FILE_DIR / "soul"
DEFAULT_SOUL_PATH: Path = SOUL_DIR / "sherry_default.md"
# 頻道至 Soul 人設檔對映字典（頻道 ID -> Soul 檔名，例如 {123456789: "sherry_serious.md"}）
CHANNEL_SOUL_MAP: dict[int, str] = {}

HEARTBEAT_PATH: Path = DATA_DIR / "heartbeat.md"
DEFAULT_HEARTBEAT_CHANNEL_ID: int = 1519967448982487201

CHANNEL_VIBE_PATH: Path = DATA_DIR / "channel_vibe.json"

DEEP_THINKING_LOG_PATH: Path = DATA_DIR / "deep_thinking.json"  # [deprecated] 將整合入 runtime_log.jsonl
DRAW_THINKING_LOG_PATH: Path = DATA_DIR / "draw_thinking.json"  # [deprecated] 將整合入 runtime_log.jsonl

USER_MEMORY_PATH: Path = DATA_DIR / "user_memory.json"
# 社交關係資料（好感度、關係等級）—— 獨立存檔，不與 facts 共用！
USER_RELATIONS_PATH: Path = DATA_DIR / "user_relations.json"
# （我們未來會靠這個檔案模擬最基本型的文字式 Document Store 當做 Vector DB 的銜接預備點）

# ---------------------------------------------------------------------------
# Observability (JSONL unified log)
# ---------------------------------------------------------------------------
RUNTIME_LOG_PATH: Path = DATA_DIR / "runtime_log.jsonl"   # 所有互動事件完整快照
ERROR_LOG_PATH: Path = DATA_DIR / "error_log.jsonl"       # 只記録異常與降級事件


# ---------------------------------------------------------------------------
# Schedule System
# ---------------------------------------------------------------------------
SCHEDULE_PATH: Path = BASE_DIR / "data" / "schedule.json"

# LLM 思考過程紀錄，供後續人工評估使用。
THINKING_LOG_PATH: Path = DATA_DIR / "thinking.json"   # [deprecated] 將整合入 runtime_log.jsonl
THINKING_LOG_DIR: Path = DATA_DIR / "thinking"         # [deprecated] 將整合入 runtime_log.jsonl
STT_THINKING_LOG_PATH: Path = DATA_DIR / "stt_thinking.log"  # [deprecated]

# ---------------------------------------------------------------------------
# FFmpeg
# ---------------------------------------------------------------------------
FFMPEG = str(BASE_DIR / 'ffmpeg')

# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------
DISCORD_BOT_TOKEN: str = os.getenv("DISCORD_BOT_TOKEN", "")
ADMIN_ROLE_IDS: list[int] = []
ADMIN_USER_IDS: list[int] = [425311733503229972]
NSFW_ALLOWED_CHANNEL_IDS: list[int] = []

# ---------------------------------------------------------------------------
# Ollama / LM Studio / Cloud LLMs
# ---------------------------------------------------------------------------
# 支援前綴決定後端路由：
#   "ollama:"   → Ollama 原生 API
#   "lms:"      → LM Studio (OpenAI 相容格式)
#   "openai:"   → OpenAI ChatGPT          (OpenAI 相容, 需 API Key)
#   "deepseek:" → DeepSeek                (OpenAI 相容, 需 API Key)
#   "gemini:"   → Google Gemini           (OpenAI 相容, 需 API Key)
#   "claude:"   → Anthropic Claude        (Anthropic Messages API, 需 API Key)
# 若無前綴，預設為 "ollama:"
# ---------------------------------------------------------------------------

# --- 本機後端 Base URLs ---
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
LMS_BASE_URL: str = os.getenv("LMS_BASE_URL", "http://localhost:1234/v1")
VLLM_BASE_URL: str = os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1")

# --- 雲端 API Keys ---
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
CLAUDE_API_KEY: str = os.getenv("CLAUDE_API_KEY", "")

# --- 雲端 Base URLs（可自訂，預設為官方端點）---
OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
GEMINI_BASE_URL: str = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
CLAUDE_BASE_URL: str = os.getenv("CLAUDE_BASE_URL", "https://api.anthropic.com/v1")

# 主力模型：平衡智商與生成速度，適合日常深度對話與邏輯推演
PRIMARY_MODEL: str = "lms:gemma4-12b-qat-uncensored-hauhaucs-balanced"

# 無道德限制模型：專門處理「橘雪莉」缺乏倫理觀的瘋狂人設
NOMORAL_MODEL: str = "lms:gemma4-12b-qat-uncensored-hauhaucs-balanced"

# 快速回覆模型：要求極速但需要足夠的基礎智商
QUICK_MODEL: str = "ollama:krith/mistral-nemo-instruct-2407-abliterated:IQ4_XS"

# 輕量分類模型：用於「插話評分」與「YouTube 影片篩選」
CLASSIFIER_MODEL: str = "ollama:qwen2.5:3b"

# 氛圍話題萃取模型。
VIBE_MODEL: str = "ollama:llama3.2:3b"

# 視覺辨識模型 (多模態圖片理解)
VISION_MODEL: str = "ollama:minicpm-v"

# 雲端搜尋專用模型
CLOUD_SEARCH_MODEL: str = "deepseek:deepseek-v4-flash"

OLLAMA_TIMEOUT: int = 120

# ---------------------------------------------------------------------------
# 代理人調度與多工架構設定 (Agent Dispatcher Settings)
# ---------------------------------------------------------------------------
# 任務調度模式：PARALLEL (高效能單次並行調度，預設) | REACT (經典多步驟 ReAct 迴圈) | CLASSIC (傳統單意圖快路徑)
AGENT_DISPATCH_MODE: str = os.getenv("AGENT_DISPATCH_MODE", "PARALLEL").upper()

# 行為自適應對話抑制開關：若為 True，當發動的工具全為 DIRECT_UI (如點歌/搜圖/畫圖) 且使用者無聊天需求時，動作完成即刻結束，不產生多餘廢話文字
ACTION_EARLY_TERMINATION_ENABLED: bool = os.getenv("ACTION_EARLY_TERMINATION_ENABLED", "True").lower() in ("true", "1", "yes")

# 並行調度模型（專門用於單次推論任務拆解與對話抑制標記）
PARALLEL_PLANNER_MODEL: str = os.getenv("PARALLEL_PLANNER_MODEL", CLASSIFIER_MODEL)
PARALLEL_MAX_TASKS: int = 5
PARALLEL_STEP_TIMEOUT: float = 30.0

# ReAct 多步驟 Agent 推理模型（「思考→行動→觀察」迭代迴圈，保留向下相容）
# 預設繼承 NOMORAL_MODEL（無道德限制模型），可用 .env 中 REACT_MODEL 覆寫
REACT_MODEL: str = os.getenv("REACT_MODEL", NOMORAL_MODEL)

# ReAct 多步驟 Agent 開關（保留向下相容）
REACT_ENABLED: bool = os.getenv("REACT_ENABLED", "True").lower() == "true"

# ReAct 迴圈控制參數
REACT_MAX_STEPS: int = 5                    # 最多步數（日常聊天 1 步即收斂）
REACT_STEP_TIMEOUT: float = 60.0            # 單步推理最長秒數（給予充分 prefill 時間）
REACT_MAX_OBSERVATION_LENGTH: int = 1200    # 單次 Observation 截斷上限
REACT_MAX_TRANSCRIPT_CHARS: int = 6000      # 總對話历史截斷防爆上限

LLM_MAX_CONTEXT_TOKENS: int = 8192
LLM_MAX_OUTPUT_TOKENS: int = 1024

# GPU 吞吐量最佳化：Prompt 批次處理量（n_batch）
# 預設 512 過於保守，A4000 可大幅提升至 2048–4096 以加速 Prefill（降低首字延遲）
LLM_NUM_BATCH: int = 2048

# Prompt Caching（KV 快取駐留時間）：讓模型與 System Prompt 維持在 VRAM 中
# 格式："60m"（60分鐘）、"24h"（24小時）、"0"（每次推論後立即卸載）
OLLAMA_KEEP_ALIVE: str = "60m"

# 調整生成參數以降低跳針（重複生成相同內容）機率
LLM_TEMPERATURE: float = 0.85
LLM_PRESENCE_PENALTY: float = 0.6
LLM_FREQUENCY_PENALTY: float = 0.6

# ---------------------------------------------------------------------------
# Priority Queue Weights
# ---------------------------------------------------------------------------
PRIORITY_TAG_RESPONSE: int = 1       # 被 @Tag → 最高優先
PRIORITY_INTERJECT: int = 10         # 主動插話（高於背景，低於 Tag 回應）
PRIORITY_HEARTBEAT: int = 20         # 心跳排程任務（主動發言）
PRIORITY_VIBE_UPDATE: int = 50       # 背景氛圍萃取
PRIORITY_DRAW: int = 80              # 圖片生成（對話優先）
PRIORITY_WEB_EXPLORER: int = 99      # 閒置爬蟲 → 最低優先

# ---------------------------------------------------------------------------
# Context / Memory
# ---------------------------------------------------------------------------
CONTEXT_HISTORY_LIMIT: int = 30  # ✅ [修復] 由 10 提升至 30，大幅改善多輪對話脈絡連貫性
CONTEXT_TIME_GAP_SECONDS: int = 900  # 15 分鐘斷層截斷

VIBE_UPDATE_MESSAGE_THRESHOLD: int = 50
VIBE_TOP_N_TOPICS: int = 3
VIBE_DECAY_INTERVAL_SECONDS: int = 3600
VIBE_DECAY_AMOUNT: int = 1

# 注入 prompt 的話題最低權重門檻（低於此值不列入）。
VIBE_MIN_WEIGHT_TO_INJECT: int = 5

# 話題再次出現時補滿的最高權重。
VIBE_REINFORCE_MAX_WEIGHT: int = 10

# ---------------------------------------------------------------------------
# Typing Simulation

# ---------------------------------------------------------------------------
TYPING_BASE_DELAY: float = 1.0
TYPING_DELAY_PER_50_CHARS: float = 0.3

# ---------------------------------------------------------------------------
# Web Explorer
# ---------------------------------------------------------------------------
_yt_key_raw: str = os.getenv("YOUTUBE_API_KEY", "")
YOUTUBE_API_KEY: str = _yt_key_raw if _yt_key_raw not in ("", "your_youtube_api_key_here") else ""

OPENWEATHERMAP_API_KEY: str = os.getenv("OPENWEATHERMAP_API_KEY", "")

EXPLORER_INTERVAL_SECONDS: int = 1800
YOUTUBE_MAX_RESULTS: int = 5
EXPLORER_TARGET_CHANNEL_IDS: list[int] = [1522439581658648636]

# ---------------------------------------------------------------------------
# Interject Engine（主動插話引擎）
# ---------------------------------------------------------------------------

# 每累積幾則真人訊息，觸發一次「好不好玩」評分。
INTERJECT_EVAL_THRESHOLD: int = 10

# 插話後的冷卻秒數（冷卻期間不再觸發插話評估）。
INTERJECT_COOLDOWN_SECONDS: int = 30

# 同一冷卻週期內最多允許連續插話幾次。
# 超過此次數後，必須等到下一個冷卻週期結束才能再插話。
INTERJECT_MAX_CONSECUTIVE: int = 3

# 評分門檻（1–10）：LLM 給分 >= 此值才實際插話。
INTERJECT_SCORE_THRESHOLD: int = 7  # ✅ [修復] 由 10 降至 7，讓高興致時主動插話可真正觸發

# ---------------------------------------------------------------------------
# Rate Limit
# ---------------------------------------------------------------------------
RATE_LIMIT_MAX_REQUESTS: int = 5
RATE_LIMIT_WINDOW_SECONDS: int = 30
RATE_LIMIT_REPLY_MESSAGE: str = "名偵探現在很忙！{cooldown:.0f} 秒後再來！"

# ---------------------------------------------------------------------------
# Health Monitor
# ---------------------------------------------------------------------------
HEALTH_CHECK_INTERVAL_SECONDS: int = 300
HEALTH_ALERT_CHANNEL_ID: int = 1503226173386133574  # 0 = 停用
DISCORD_LOG_LEVEL: str = "WARNING"  # 可設為 INFO, WARNING, ERROR 等

# ---------------------------------------------------------------------------
# Voice System
# ---------------------------------------------------------------------------
VOICE_STT_MODEL: str = str(BASE_DIR / "models" / "nemotron-3.5")
VOICE_STT_DEVICE: str = "cpu"
VOICE_STT_COMPUTE_TYPE: str = "float32"
VOICE_STT_LANGUAGE: str = "zh"
VOICE_TTS_MODEL_PATH: str = str(BASE_DIR / "models" / "xtts-v2")
VOICE_TTS_LANGUAGE: str = "zh-cn"
VOICE_TTS_SPEAKER_WAV: str = str(BASE_DIR / "file" / "shiry_voice_sample.wav")
VOICE_VAD_THRESHOLD: float = 0.5
VOICE_VAD_MIN_SILENCE_MS: int = 500
VOICE_SILENCE_TIMEOUT_SECONDS: float = 1.5
VOICE_MAX_RECORD_SECONDS: int = 30

# ---------------------------------------------------------------------------
# Pollinations.ai Cloud Image Generation (Primary)
# ---------------------------------------------------------------------------
POLLINATIONS_API_URL: str = "https://image.pollinations.ai/prompt"
POLLINATIONS_TIMEOUT_SECONDS: int = 30
# 可選值: "flux-anime" (推薦,二次元風格), "flux-anime2", "z-image-turbo" (通用寫實)
POLLINATIONS_MODEL: str = "flux-anime"

# ---------------------------------------------------------------------------
# Hugging Face Inference API (Primary Anime Image Generation)
# ---------------------------------------------------------------------------
# 是否使用線上模型 (Gemini Nano Banana / HF / Pollinations) 生成圖片。預設 True
USE_ONLINE_IMAGE_MODEL: bool = os.getenv("USE_ONLINE_IMAGE_MODEL", "True").lower() in ("true", "1", "yes")

# ---------------------------------------------------------------------------
# Google Gemini Image Generation (Nano Banana - Primary Online)
# ---------------------------------------------------------------------------
GEMINI_IMAGE_MODEL: str = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
GEMINI_IMAGE_TIMEOUT_SECONDS: int = 30

# 需要 HF Token：https://huggingface.co/settings/tokens
# 申請 Free 帳號即有免費 Serverless Inference 額度
# Animagine-XL-3.1 是專為動漫訓練的 SDXL 模型，對 Blue Archive 等遊戲角色有良好識別
HF_TOKEN: str = os.getenv("HF_TOKEN", "")
HF_IMAGE_MODEL: str = "cagliostrolab/animagine-xl-3.1"
HF_IMAGE_TIMEOUT_SECONDS: int = 120  # SDXL 生成較慢，給足夠時間

# ---------------------------------------------------------------------------
# Local SD Image Generation (Fallback)
# ---------------------------------------------------------------------------
SD_WEBUI_URL: str = "http://localhost:7860"
SD_DEFAULT_WIDTH: int = 768
SD_DEFAULT_HEIGHT: int = 1024
SD_DEFAULT_STEPS: int = 20
SD_DEFAULT_CFG_SCALE: float = 7.0
SD_DEFAULT_SAMPLER: str = "DPM++ 2M Karras"
SD_TIMEOUT_SECONDS: int = 120
SD_NEGATIVE_PROMPT_BASE: str = "lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, fewer digits, cropped, worst quality, low quality, normal quality, jpeg artifacts, signature, watermark, username, blurry, artist name"

# ---------------------------------------------------------------------------
# Micro Chain-of-Thought (Micro-CoT)
# ---------------------------------------------------------------------------
# 啟用後，雪莉在每次 CHAT 回覆前會先輸出 <thought>...</thought> 內心推理，
# 再給出正式回覆。thought 區塊對用戶不可見，會被存進 data/deep_thinking.json。
# 若發現模型因為思考步驟太長而變慢，可暫時設為 False 關閉。
MICRO_COT_ENABLED: bool = True

# ---------------------------------------------------------------------------
# Daily Chat Log + Nightly Memory Digest
# ---------------------------------------------------------------------------
CHAT_LOG_DIR: Path = DATA_DIR / "chat_logs"          # 每日聊天記錄 JSONL 目錄
NIGHTLY_DIGEST_HOUR: int = 4                          # 深夜整合觸發小時（台灣時間 UTC+8）
NIGHTLY_DIGEST_MINUTE: int = 30                        # 深夜整合觸發分鐘（台灣時間 UTC+8，預設 04:30）
NIGHTLY_DIGEST_MODEL: str = PRIMARY_MODEL             # 深夜整合使用較強模型
NIGHTLY_MAX_MSGS_PER_USER: int = 200                  # 每位使用者最多送入 LLM 的訊息數

# ---------------------------------------------------------------------------
# Web UI Server
# ---------------------------------------------------------------------------
WEB_UI_HOST: str = os.getenv("WEB_UI_HOST", "0.0.0.0")
WEB_UI_PORT: int = int(os.getenv("WEB_UI_PORT", "8080"))
WEB_UI_HTTPS: bool = True
WEB_UI_SSL_CERT: str | None = os.getenv("WEB_UI_SSL_CERT", None)
WEB_UI_SSL_KEY: str | None = os.getenv("WEB_UI_SSL_KEY", None)
WEB_UI_RADMIN_IP: str | None = os.getenv("WEB_UI_RADMIN_IP", "26.108.83.196")  # Radmin VPN IP for SSL SAN
WEB_UI_HTTP_TIMEOUT: float = 5.0

# ---------------------------------------------------------------------------
# Music & Voice AI Models
# ---------------------------------------------------------------------------
YOUTUBE_COOKIES_PATH: Path = DATA_DIR / "cookies.txt"
MUSIC_PLAYLIST_MAX_ITEMS: int = 100
MUSIC_FFMPEG_BEFORE_OPTIONS: str = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -loglevel error"
MUSIC_FFMPEG_OPTIONS: str = '-vn -af "loudnorm=I=-9:LRA=11:TP=-1.0"'
MUSIC_DEFAULT_VOLUME: float = 1.2
MUSIC_QUEUE_VIEW_TIMEOUT: int = 180

VOICE_STT_REPO_ID: str = "nvidia/nemotron-3.5-asr-streaming-0.6b"
VOICE_TTS_MODEL_NAME: str = "tts_models/multilingual/multi-dataset/xtts_v2"
VOICE_VAD_REPO_OR_DIR: str = "snakers4/silero-vad"
VOICE_VAD_MODEL_NAME: str = "silero_vad"

# ---------------------------------------------------------------------------
# Tag Dictionary & World Memory Paths
# ---------------------------------------------------------------------------
PIXIV_TAG_PATH: Path = FILE_DIR / "pixiv_tag.json"
PIXIV_TAG_MIN_POST_COUNT: int = 100
PIXIV_TAG_MAX_PAGES: int = 60
PIXIV_TAG_REQUEST_DELAY: float = 0.5
DANBOORU_API_URL: str = "https://danbooru.donmai.us/tags.json"

WORLD_LOG_DIR: Path = DATA_DIR / "world_history"
WORLD_MEMORY_PATH: Path = DATA_DIR / "world_memory.json"
BOT_LOG_PATH: Path = DATA_DIR / "bot.log"

# ---------------------------------------------------------------------------
# Memory System & Digest Thresholds
# ---------------------------------------------------------------------------
MEMORY_MAX_FACTS_IN_CONTEXT: int = 5
MEMORY_MAX_FACTS_PER_USER: int = 15
MEMORY_PERMANENT_FACT_THRESHOLD: int = 4
NIGHTLY_MIN_MSGS_THRESHOLD: int = 5

# ---------------------------------------------------------------------------
# BotBrain Queue & Retries
# ---------------------------------------------------------------------------
BOT_BRAIN_MAX_RETRIES: int = 3
BOT_BRAIN_RETRY_DELAY: float = 2.0

# ---------------------------------------------------------------------------
# Heartbeat Active System
# ---------------------------------------------------------------------------
HEARTBEAT_CHECK_INTERVAL_MINUTES: int = 30
HEARTBEAT_CONDITION_START_HOUR: int = 9
HEARTBEAT_CONDITION_END_HOUR: int = 22
HEARTBEAT_CONDITION_MIN_GAP_SECONDS: int = 14400

# ---------------------------------------------------------------------------
# External APIs, Search & User-Agent
# ---------------------------------------------------------------------------
OPENWEATHERMAP_API_URL: str = "https://api.openweathermap.org/data/2.5/weather"
SAFEBOORU_API_URL: str = "https://safebooru.org/index.php?page=dapi&s=post"
SAFEBOORU_IMAGE_BASE_URL: str = "https://safebooru.org/images/"
DUCKDUCKGO_SEARCH_URL: str = "https://html.duckduckgo.com/html/?q="
DEFAULT_HTTP_TIMEOUT: int = 10
YOUTUBE_SEARCH_API_URL: str = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_RSS_FEED_URL: str = "https://www.youtube.com/feeds/videos.xml?channel_id="
YOUTUBE_RSS_NS: str = "http://www.w3.org/2005/Atom"
DEFAULT_USER_AGENT: str = "DiscordBot/1.0 (https://github.com/placeholder; contact@placeholder.com)"

# ---------------------------------------------------------------------------
# Module Timeouts
# ---------------------------------------------------------------------------
WEATHER_EXTRACT_TIMEOUT: float = 10.0
WEATHER_REPLY_TIMEOUT: float = 60.0
SAFEBOORU_HTTP_TIMEOUT: int = 12
VISION_ANALYSIS_TIMEOUT: int = 30
STOCK_EXTRACT_TIMEOUT: float = 10.0
STOCK_REPLY_TIMEOUT: float = 60.0
SCHEDULE_PARSE_TIMEOUT: float = 20.0
DRAW_RAW_TIMEOUT: float = 30.0
DRAW_EXPAND_TIMEOUT: float = 10.0
COMPOUND_PARSER_TIMEOUT: float = 10.0
INTERJECT_SCORE_TIMEOUT: float = 30.0
INTERJECT_REPLY_TIMEOUT: float = 60.0
YOUTUBE_EXPLORER_TIMEOUT: float = 20.0
NIGHTLY_DIGEST_TIMEOUT: float = 120.0
HEALTH_CHECK_TIMEOUT: float = 5.0
DISCORD_UI_VIEW_TIMEOUT: int = 300


# ---------------------------------------------------------------------------
# Runtime Settings Override Mechanism
# ---------------------------------------------------------------------------
CONFIG_OVERRIDE_PATH: Path = DATA_DIR / "config_override.json"

if CONFIG_OVERRIDE_PATH.exists():
    try:
        import json
        with open(CONFIG_OVERRIDE_PATH, "r", encoding="utf-8") as f:
            overrides = json.load(f)
            # 避免舊版 config_override.json 蓋掉全新的 Web UI 監聽位址與 Port
            overrides.pop("WEB_UI_HOST", None)
            overrides.pop("WEB_UI_PORT", None)
            overrides.pop("WEB_UI_HTTPS", None)
            # 若 .env 或環境變數有指定 OLLAMA / LMS / SD 網址，優先使用環境變數
            if os.getenv("OLLAMA_BASE_URL"):
                overrides.pop("OLLAMA_BASE_URL", None)
                globals()["OLLAMA_BASE_URL"] = os.getenv("OLLAMA_BASE_URL")
            if os.getenv("LMS_BASE_URL"):
                overrides.pop("LMS_BASE_URL", None)
                globals()["LMS_BASE_URL"] = os.getenv("LMS_BASE_URL")
            if os.getenv("VLLM_BASE_URL"):
                overrides.pop("VLLM_BASE_URL", None)
                globals()["VLLM_BASE_URL"] = os.getenv("VLLM_BASE_URL")
            if os.getenv("SD_WEBUI_URL"):
                overrides.pop("SD_WEBUI_URL", None)
                globals()["SD_WEBUI_URL"] = os.getenv("SD_WEBUI_URL")
            for k, v in overrides.items():
                if k in globals():
                    globals()[k] = v
    except Exception as e:
        logger.warning("Failed to load config_override.json: %s", e)

WEB_UI_PASSWORD: str = os.getenv("WEB_UI_PASSWORD", "1919810")  # 後台密碼
WEB_UI_MAX_LOGIN_ATTEMPTS: int = 5  # 1 分鐘內登入失敗最大次數，超過即封鎖 IP
WEB_UI_LOCKOUT_MINUTES: int = 15   # 觸發封鎖後禁止登入的分鐘數

def update_setting(key: str, value: any) -> bool:
    import json
    g = globals()
    key_str = str(key).strip()
    if not key_str.isidentifier() or not key_str.isupper() or key_str not in g:
        return False
    
    expected_type = type(g[key_str])
    if expected_type == str and not isinstance(value, str):
        value = str(value)
    elif expected_type != type(value) and g[key_str] is not None:
        try:
            value = expected_type(value)
        except (ValueError, TypeError):
            return False

    g[key_str] = value
    try:
        data = {}
        if CONFIG_OVERRIDE_PATH.exists():
            with open(CONFIG_OVERRIDE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        data[key_str] = value
        with open(CONFIG_OVERRIDE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        return True
    except Exception:
        return False

# ---------------------------------------------------------------------------
# Settings Metadata — 提供 Web UI 顯示說明、分組、控件型別
# ---------------------------------------------------------------------------
# type 說明：
#   "model"   → 前端渲染 [前綴下拉] + [模型名稱下拉/輸入]
#   "bool"    → Toggle 開關
#   "float"   → Slider + 數值輸入（需 min / max / step）
#   "int"     → 數值輸入框（可選 min / max）
#   "str"     → 文字輸入框
#   "url"     → URL 輸入框
# ---------------------------------------------------------------------------
SETTINGS_META: dict[str, dict] = {
    # === 模型設定 ===
    "PRIMARY_MODEL": {
        "label": "主力模型 (Primary Model)",
        "description": "平衡智商與生成速度，用於日常深度對話與邏輯推演。"
                        "格式：前綴:模型名（例：ollama:llama3.2 / lms:gemma-4）",
        "type": "model",
        "category": "模型設定",
    },
    "NOMORAL_MODEL": {
        "label": "無道德限制模型 (No-Moral Model)",
        "description": "專門處理橘雪莉缺乏倫理觀的瘋狂人設，通常使用 abliterated 版本模型。",
        "type": "model",
        "category": "模型設定",
    },
    "QUICK_MODEL": {
        "label": "快速回覆模型 (Quick Model)",
        "description": "要求極速回覆時使用，需要足夠基礎智商。適合簡短互動。",
        "type": "model",
        "category": "模型設定",
    },
    "CLASSIFIER_MODEL": {
        "label": "分類輕量模型 (Classifier Model)",
        "description": "用於插話評分與 YouTube 影片篩選，輕量優先（如 qwen2.5:3b）。",
        "type": "model",
        "category": "模型設定",
    },
    "VIBE_MODEL": {
        "label": "氛圍萃取模型 (Vibe Model)",
        "description": "從聊天記錄中萃取當前話題氛圍，輕量模型即可勝任。",
        "type": "model",
        "category": "模型設定",
    },
    "VISION_MODEL": {
        "label": "視覺辨識模型 (Vision Model)",
        "description": "多模態圖片理解，需要支援圖像的模型（如 minicpm-v）。",
        "type": "model",
        "category": "模型設定",
    },
    "CLOUD_SEARCH_MODEL": {
        "label": "雲端搜尋模型 (Cloud Search Model)",
        "description": "網路搜尋任務使用的雲端模型，需要良好的工具呼叫能力。",
        "type": "model",
        "category": "模型設定",
    },
    "AGENT_DISPATCH_MODE": {
        "label": "代理人調度模式 (Agent Dispatch Mode)",
        "description": "選擇訊息解析與工具調度核心：PARALLEL（高效能單次推論並行調度，秒級響應，推薦）、REACT（多步驟思考迭代迴圈）、CLASSIC（傳統單意圖快路徑）。",
        "type": "select",
        "options": [
            {"value": "PARALLEL", "label": "🚀 高效能並行調度 (Parallel) - 推薦"},
            {"value": "REACT", "label": "🔄 經典多步驟 ReAct (Sequential)"},
            {"value": "CLASSIC", "label": "⚡ 傳統單意圖快路徑 (Classic)"},
        ],
        "category": "模型設定",
    },
    "ACTION_EARLY_TERMINATION_ENABLED": {
        "label": "行為自適應對話抑制 (Action Early Termination)",
        "description": "開啟 (True) = 當發動的工具全為自帶 UI 渲染（如點歌、搜圖、畫圖）且無聊天需求時，動作完成即刻收工，不發送多餘文字；關閉 (False) = 動作完成後仍強制由主力模型生成文字閒聊回覆。",
        "type": "bool",
        "category": "模型設定",
    },
    "PARALLEL_PLANNER_MODEL": {
        "label": "並行調度模型 (Parallel Planner Model)",
        "description": "用於 Parallel Agent 單次任務拆解與對話抑制標記的輕量模型。",
        "type": "model",
        "category": "模型設定",
    },
    "REACT_MODEL": {
        "label": "ReAct 任務拆解模型 (ReAct Planner Model)",
        "description": "ReAct 多步驟 Agent 用於意圖分析與工具發動的規劃模型（負責「思考→行動→觀察」步驟調度）。最終對話生成會由 PRIMARY_MODEL (主力模型) 進行發言合成。",
        "type": "model",
        "category": "模型設定",
    },
    "OLLAMA_TIMEOUT": {
        "label": "Ollama 逾時秒數 (Timeout)",
        "description": "等待 Ollama 回應的最長秒數，較大模型需要更長時間。建議 60–180。",
        "type": "int",
        "min": 10,
        "max": 600,
        "category": "LLM 參數",
    },
    "LLM_MAX_CONTEXT_TOKENS": {
        "label": "最大 Context Token 數",
        "description": "送給 LLM 的最大上下文長度（包含 System Prompt + 歷史記錄）。越大越耗資源。",
        "type": "int",
        "min": 1024,
        "max": 131072,
        "category": "LLM 參數",
    },
    "LLM_MAX_OUTPUT_TOKENS": {
        "label": "最大輸出 Token 數",
        "description": "模型單次生成的最大 token 數量。太少會截斷回覆，太多會降低速度。",
        "type": "int",
        "min": 128,
        "max": 8192,
        "category": "LLM 參數",
    },
    "LLM_TEMPERATURE": {
        "label": "生成溫度 (Temperature)",
        "description": "控制輸出隨機性。0 = 完全確定性；1 = 高隨機創意。日常對話建議 0.7–0.9。",
        "type": "float",
        "min": 0.0,
        "max": 2.0,
        "step": 0.05,
        "category": "LLM 參數",
    },
    "LLM_PRESENCE_PENALTY": {
        "label": "Presence Penalty（話題多樣性）",
        "description": "懲罰已出現過的 token，迫使模型引入新話題。建議 0.4–0.8。",
        "type": "float",
        "min": -2.0,
        "max": 2.0,
        "step": 0.05,
        "category": "LLM 參數",
    },
    "LLM_FREQUENCY_PENALTY": {
        "label": "Frequency Penalty（降低重複）",
        "description": "懲罰重複出現的 token，降低跳針（重複生成相同句子）機率。建議 0.4–0.8。",
        "type": "float",
        "min": -2.0,
        "max": 2.0,
        "step": 0.05,
        "category": "LLM 參數",
    },
    "LLM_NUM_BATCH": {
        "label": "Prompt 批次處理量 (n_batch)",
        "description": "GPU 單次平行處理的 Prompt Token 批次大小（對應 llama.cpp n_batch）。數值越大首字延遲越低，但 VRAM 佔用略增。RTX A4000 (16GB) 建議 2048–4096。",
        "type": "int",
        "min": 512,
        "max": 8192,
        "category": "LLM 參數",
    },
    "OLLAMA_KEEP_ALIVE": {
        "label": "Ollama 模型駐留時間 (Keep-Alive)",
        "description": "推論完成後模型與 KV 快取在 VRAM 中保留多久。支援時間格式：\"60m\"（60 分鐘）、\"24h\"（24 小時）、\"0\"（立即卸載，停用快取）。建議設為 \"60m\" 以發揮 Prompt Caching 效益。",
        "type": "str",
        "category": "LLM 參數",
    },
    # === 後端端點設定 ===
    "OLLAMA_BASE_URL": {
        "label": "Ollama 伺服器 URL",
        "description": "本機 Ollama 服務的基礎 URL，預設為 http://localhost:11434。",
        "type": "url",
        "category": "後端連線設定",
    },
    "LMS_BASE_URL": {
        "label": "LM Studio 伺服器 URL",
        "description": "本機 LM Studio 的 OpenAI 相容 API 位址，預設為 http://localhost:1234/v1。",
        "type": "url",
        "category": "後端連線設定",
    },
    "OPENAI_BASE_URL": {
        "label": "OpenAI API URL",
        "description": "OpenAI 相容 API 端點，可指向 OpenAI 或第三方代理。",
        "type": "url",
        "category": "後端連線設定",
    },
    "DEEPSEEK_BASE_URL": {
        "label": "DeepSeek API URL",
        "description": "DeepSeek API 端點 URL。",
        "type": "url",
        "category": "後端連線設定",
    },
    "GEMINI_BASE_URL": {
        "label": "Gemini API URL",
        "description": "Google Gemini OpenAI 相容端點 URL。",
        "type": "url",
        "category": "後端連線設定",
    },
    "CLAUDE_BASE_URL": {
        "label": "Claude API URL",
        "description": "Anthropic Claude Messages API 端點。",
        "type": "url",
        "category": "後端連線設定",
    },
    # === 記憶體與上下文 ===
    "CONTEXT_HISTORY_LIMIT": {
        "label": "歷史記錄筆數限制",
        "description": "注入 LLM Prompt 的最大對話輪數（USER+BOT 各算一輪）。越高記憶越長但越耗 token。",
        "type": "int",
        "min": 2,
        "max": 100,
        "category": "記憶與上下文",
    },
    "CONTEXT_TIME_GAP_SECONDS": {
        "label": "上下文時間斷層門檻（秒）",
        "description": "兩則訊息相差超過此秒數，視為對話斷層並截斷歷史。預設 900 秒（15 分鐘）。",
        "type": "int",
        "min": 60,
        "max": 86400,
        "category": "記憶與上下文",
    },
    # === 氛圍系統 ===
    "VIBE_UPDATE_MESSAGE_THRESHOLD": {
        "label": "氛圍更新訊息門檻",
        "description": "累積幾則訊息後才觸發氛圍（話題）萃取更新。",
        "type": "int",
        "min": 5,
        "max": 500,
        "category": "氛圍系統",
    },
    "VIBE_TOP_N_TOPICS": {
        "label": "話題萃取數量 (Top N)",
        "description": "每次氛圍萃取保留前 N 個主要話題注入 Prompt。",
        "type": "int",
        "min": 1,
        "max": 10,
        "category": "氛圍系統",
    },
    "VIBE_DECAY_INTERVAL_SECONDS": {
        "label": "話題衰減間隔（秒）",
        "description": "每隔多少秒話題權重自動降低一次，模擬話題逐漸淡忘。",
        "type": "int",
        "min": 300,
        "max": 86400,
        "category": "氛圍系統",
    },
    "VIBE_DECAY_AMOUNT": {
        "label": "話題衰減量",
        "description": "每次衰減時話題權重減少的數值。",
        "type": "int",
        "min": 1,
        "max": 10,
        "category": "氛圍系統",
    },
    "VIBE_MIN_WEIGHT_TO_INJECT": {
        "label": "話題注入最低權重",
        "description": "低於此權重的話題不會注入 Prompt（避免注入過時或無關話題）。",
        "type": "int",
        "min": 1,
        "max": 20,
        "category": "氛圍系統",
    },
    "VIBE_REINFORCE_MAX_WEIGHT": {
        "label": "話題強化最高權重上限",
        "description": "話題再次出現時可補滿到的最高權重值。",
        "type": "int",
        "min": 5,
        "max": 50,
        "category": "氛圍系統",
    },
    # === 插話引擎 ===
    "INTERJECT_EVAL_THRESHOLD": {
        "label": "插話觸發門檻（訊息數）",
        "description": "每累積幾則真人訊息，觸發一次插話評分。",
        "type": "int",
        "min": 1,
        "max": 100,
        "category": "插話引擎",
    },
    "INTERJECT_COOLDOWN_SECONDS": {
        "label": "插話冷卻時間（秒）",
        "description": "插話後的冷卻秒數，冷卻期間不再觸發插話評估。",
        "type": "int",
        "min": 0,
        "max": 600,
        "category": "插話引擎",
    },
    "INTERJECT_MAX_CONSECUTIVE": {
        "label": "最大連續插話次數",
        "description": "同一冷卻週期內最多允許連續插話幾次，超過後需等下一週期。",
        "type": "int",
        "min": 1,
        "max": 20,
        "category": "插話引擎",
    },
    "INTERJECT_SCORE_THRESHOLD": {
        "label": "插話評分門檻 (1–10)",
        "description": "LLM 插話評分 >= 此值才實際插話。設為 10 = 幾乎不主動插話。",
        "type": "int",
        "min": 1,
        "max": 10,
        "category": "插話引擎",
    },
    # === 速率限制 ===
    "RATE_LIMIT_MAX_REQUESTS": {
        "label": "速率限制：最大請求數",
        "description": "在時間窗口內，同一用戶最多可以發送的請求數量。",
        "type": "int",
        "min": 1,
        "max": 100,
        "category": "速率限制",
    },
    "RATE_LIMIT_WINDOW_SECONDS": {
        "label": "速率限制：時間窗口（秒）",
        "description": "速率限制的統計時間窗口長度（秒）。",
        "type": "int",
        "min": 5,
        "max": 3600,
        "category": "速率限制",
    },
    # === 打字模擬 ===
    "TYPING_BASE_DELAY": {
        "label": "打字延遲基礎值（秒）",
        "description": "雪莉開始打字前的最小等待秒數，模擬人類思考時間。",
        "type": "float",
        "min": 0.0,
        "max": 10.0,
        "step": 0.1,
        "category": "打字模擬",
    },
    "TYPING_DELAY_PER_50_CHARS": {
        "label": "每 50 字打字追加延遲（秒）",
        "description": "回覆每 50 個字增加的打字延遲，模擬長文打字速度。",
        "type": "float",
        "min": 0.0,
        "max": 5.0,
        "step": 0.1,
        "category": "打字模擬",
    },
    # === Chain-of-Thought ===
    "MICRO_COT_ENABLED": {
        "label": "啟用 Micro Chain-of-Thought",
        "description": "開啟後，雪莉在每次回覆前先輸出內心推理 <thought>。"
                        "會增加推理品質但略微降低速度。",
        "type": "bool",
        "category": "Chain-of-Thought",
    },
    # === 圖片生成 ===
    "USE_ONLINE_IMAGE_MODEL": {
        "label": "使用線上圖片生成模型",
        "description": "True = 優先使用 Gemini/HF/Pollinations 線上生成；False = 使用本機 SD WebUI。",
        "type": "bool",
        "category": "圖片生成",
    },
    "POLLINATIONS_MODEL": {
        "label": "Pollinations 圖片模型",
        "description": "Pollinations.ai 使用的模型名稱。推薦：flux-anime（二次元）、z-image-turbo（寫實）。",
        "type": "str",
        "category": "圖片生成",
    },
    "GEMINI_IMAGE_MODEL": {
        "label": "Gemini 圖片生成模型",
        "description": "Google Gemini 圖片生成使用的模型 ID。",
        "type": "str",
        "category": "圖片生成",
    },
    "SD_WEBUI_URL": {
        "label": "Stable Diffusion WebUI URL",
        "description": "本機 SD WebUI（AUTOMATIC1111）的服務位址，作為圖片生成後備方案。",
        "type": "url",
        "category": "圖片生成",
    },
    "SD_DEFAULT_WIDTH": {
        "label": "SD 預設圖片寬度（px）",
        "description": "Stable Diffusion 生成圖片的預設寬度，建議為 64 的倍數。",
        "type": "int",
        "min": 256,
        "max": 2048,
        "category": "圖片生成",
    },
    "SD_DEFAULT_HEIGHT": {
        "label": "SD 預設圖片高度（px）",
        "description": "Stable Diffusion 生成圖片的預設高度，建議為 64 的倍數。",
        "type": "int",
        "min": 256,
        "max": 2048,
        "category": "圖片生成",
    },
    "SD_DEFAULT_STEPS": {
        "label": "SD 推理步數 (Steps)",
        "description": "步數越多品質越高但速度越慢，一般推薦 20–30 步。",
        "type": "int",
        "min": 5,
        "max": 150,
        "category": "圖片生成",
    },
    "SD_DEFAULT_CFG_SCALE": {
        "label": "SD CFG Scale（提示詞遵從度）",
        "description": "越高越嚴格遵從 Prompt，過高會過度飽和。建議 5–9。",
        "type": "float",
        "min": 1.0,
        "max": 30.0,
        "step": 0.5,
        "category": "圖片生成",
    },
    # === 健康監控 ===
    "HEALTH_CHECK_INTERVAL_SECONDS": {
        "label": "健康監控間隔（秒）",
        "description": "Bot 健康狀態檢查的頻率，單位為秒。",
        "type": "int",
        "min": 30,
        "max": 3600,
        "category": "健康監控",
    },
    "DISCORD_LOG_LEVEL": {
        "label": "Discord 日誌等級",
        "description": "discord.py 的日誌輸出等級。可選：DEBUG、INFO、WARNING、ERROR、CRITICAL。",
        "type": "str",
        "category": "健康監控",
    },
    # === Web 爬蟲 ===
    "EXPLORER_INTERVAL_SECONDS": {
        "label": "爬蟲執行間隔（秒）",
        "description": "閒置爬蟲（Web Explorer）的觸發間隔。",
        "type": "int",
        "min": 60,
        "max": 86400,
        "category": "Web 爬蟲",
    },
    "YOUTUBE_MAX_RESULTS": {
        "label": "YouTube 搜尋最大結果數",
        "description": "每次 YouTube 搜尋的最大候選影片數量。",
        "type": "int",
        "min": 1,
        "max": 50,
        "category": "Web 爬蟲",
    },
    # === 深夜整合 ===
    "NIGHTLY_DIGEST_HOUR": {
        "label": "深夜整合觸發時間（小時）",
        "description": "每天深夜幾點觸發記憶整合任務（台灣時間 UTC+8）。預設 3（凌晨 3 點）。",
        "type": "int",
        "min": 0,
        "max": 23,
        "category": "深夜記憶整合",
    },
    "NIGHTLY_MAX_MSGS_PER_USER": {
        "label": "深夜整合每用戶最大訊息數",
        "description": "每位使用者最多送入 LLM 進行整合的訊息數量，過多會超出 context。",
        "type": "int",
        "min": 10,
        "max": 2000,
        "category": "深夜記憶整合",
    },
    "NIGHTLY_DIGEST_MODEL": {
        "label": "深夜記憶整合模型",
        "description": "進行每日深夜對話記憶摘要與精煉的模型前綴與名稱。",
        "type": "model",
        "category": "深夜記憶整合",
    },
    "NIGHTLY_DIGEST_TIMEOUT": {
        "label": "深夜整合逾時秒數",
        "description": "深夜記憶整合任務的最長執行等待秒數。",
        "type": "int",
        "min": 30,
        "max": 600,
        "category": "深夜記憶整合",
    },
    "NIGHTLY_MIN_MSGS_THRESHOLD": {
        "label": "深夜整合最低訊息門檻",
        "description": "使用者當日發送超過此數量訊息時，才會觸發該用戶的深夜記憶總結。",
        "type": "int",
        "min": 1,
        "max": 50,
        "category": "深夜記憶整合",
    },
    # === 雪莉人設與心跳 ===
    "BOT_NAME": {
        "label": "雪莉稱呼名稱 (Bot Name)",
        "description": "橘雪莉對外顯示與對話中被稱呼的名字。",
        "type": "str",
        "category": "雪莉人設與心跳",
    },
    "CORE_PERSONA": {
        "label": "橘雪莉核心人設 Prompt",
        "description": "設定橘雪莉為 15 歲活潑台灣女高中生及自稱名偵探的核心靈魂設定。",
        "type": "str",
        "category": "雪莉人設與心跳",
    },
    "DEFAULT_HEARTBEAT_CHANNEL_ID": {
        "label": "預設心跳主動發言頻道 ID",
        "description": "當雪莉主動觸發心跳聊天或日常閒聊時，預設發布的 Discord 頻道 ID。",
        "type": "int",
        "category": "雪莉人設與心跳",
    },
    "HEARTBEAT_CHECK_INTERVAL_MINUTES": {
        "label": "心跳巡邏檢查間隔（分鐘）",
        "description": "系統多久檢查一次是否該在頻道中主動發起問候或閒聊。",
        "type": "int",
        "min": 5,
        "max": 1440,
        "category": "雪莉人設與心跳",
    },
    "HEARTBEAT_CONDITION_START_HOUR": {
        "label": "心跳活躍起始時間（時）",
        "description": "每天幾點開始允許橘雪莉主動在頻道裡說話（0-23）。",
        "type": "int",
        "min": 0,
        "max": 23,
        "category": "雪莉人設與心跳",
    },
    "HEARTBEAT_CONDITION_END_HOUR": {
        "label": "心跳活躍結束時間（時）",
        "description": "每天幾點後停止主動在頻道發言，進入夜晚休息（0-23）。",
        "type": "int",
        "min": 0,
        "max": 23,
        "category": "雪莉人設與心跳",
    },
    "HEARTBEAT_CONDITION_MIN_GAP_SECONDS": {
        "label": "心跳兩次主動發言最短間隔（秒）",
        "description": "兩次主動閒聊之間的最短間隔，預設 14400 秒（4 小時），避免洗版。",
        "type": "int",
        "min": 600,
        "max": 86400,
        "category": "雪莉人設與心跳",
    },
    # === 記憶與事實庫 ===
    "MEMORY_MAX_FACTS_IN_CONTEXT": {
        "label": "單次對話注入事實上限",
        "description": "每次與用戶對話時，自資料庫取出並注入 Prompt 的用戶個人記憶上限筆數。",
        "type": "int",
        "min": 1,
        "max": 20,
        "category": "記憶與上下文",
    },
    "MEMORY_MAX_FACTS_PER_USER": {
        "label": "每用戶最多記憶事實數",
        "description": "每位 Discord 使用者在資料庫中最多儲存的事實記憶上限。",
        "type": "int",
        "min": 5,
        "max": 100,
        "category": "記憶與上下文",
    },
    "MEMORY_PERMANENT_FACT_THRESHOLD": {
        "label": "記憶轉為永久保留門檻",
        "description": "當事實權重超過此數值，將轉為永久記憶不被淘汰。",
        "type": "int",
        "min": 1,
        "max": 20,
        "category": "記憶與上下文",
    },
    # === 語音辨識與合成 (TTS/STT) ===
    "VOICE_STT_MODEL": {
        "label": "語音辨識 STT 本地模型路徑",
        "description": "本機 Nemotron-3.5 語音轉文字模型的存放資料夾路徑。",
        "type": "str",
        "category": "語音辨識與合成",
    },
    "VOICE_STT_REPO_ID": {
        "label": "STT 於 HuggingFace Repo ID",
        "description": "Nemotron 語音辨識於 Hugging Face 的模型名稱。",
        "type": "str",
        "category": "語音辨識與合成",
    },
    "VOICE_STT_LANGUAGE": {
        "label": "語音辨識目標語言",
        "description": "STT 辨識預設語言代碼，例如 'zh' 代表中文。",
        "type": "str",
        "category": "語音辨識與合成",
    },
    "VOICE_STT_DEVICE": {
        "label": "STT 運算設備",
        "description": "執行語音辨識的設備（'cpu' 或 'cuda'）。",
        "type": "str",
        "category": "語音辨識與合成",
    },
    "VOICE_STT_COMPUTE_TYPE": {
        "label": "STT 運算精度",
        "description": "模型推論使用的浮點數精度（如 'float32', 'float16'）。",
        "type": "str",
        "category": "語音辨識與合成",
    },
    "VOICE_TTS_MODEL_NAME": {
        "label": "語音合成 TTS 模型名稱",
        "description": "發音合成模型，預設採用 XTTS v2 進行多語情感合成。",
        "type": "str",
        "category": "語音辨識與合成",
    },
    "VOICE_TTS_LANGUAGE": {
        "label": "TTS 語音發音語言",
        "description": "語音合成目標發音語言（例如 'zh-cn'）。",
        "type": "str",
        "category": "語音辨識與合成",
    },
    "VOICE_TTS_SPEAKER_WAV": {
        "label": "TTS 參考音訊樣本檔路徑",
        "description": "用於聲音音色選用之樣本檔案（如 shiry_voice_sample.wav）。",
        "type": "str",
        "category": "語音辨識與合成",
    },
    "VOICE_VAD_MODEL_NAME": {
        "label": "VAD 靜音檢測模型名稱",
        "description": "用於判斷使用者說話起止的 VAD 引擎名稱（如 silero_vad）。",
        "type": "str",
        "category": "語音辨識與合成",
    },
    "VOICE_VAD_THRESHOLD": {
        "label": "VAD 靜音檢測靈敏度",
        "description": "判定是否為人聲的聲音強度閥值（0.1–1.0），越高越不易誤判噪音。",
        "type": "float",
        "min": 0.1,
        "max": 1.0,
        "step": 0.05,
        "category": "語音辨識與合成",
    },
    "VOICE_VAD_MIN_SILENCE_MS": {
        "label": "判定說話結束之靜音毫秒數",
        "description": "持續靜音超過幾毫秒後，認定使用者講話完畢並開始回復。",
        "type": "int",
        "min": 100,
        "max": 3000,
        "category": "語音辨識與合成",
    },
    "VOICE_MAX_RECORD_SECONDS": {
        "label": "單次語音最長錄製秒數",
        "description": "每次錄製使用者語音的最長秒數限制，防呆與防濫用。",
        "type": "int",
        "min": 5,
        "max": 120,
        "category": "語音辨識與合成",
    },
    "VOICE_SILENCE_TIMEOUT_SECONDS": {
        "label": "語音對話靜音逾時秒數",
        "description": "當通話中持續沉靜多久（秒）判定為發話間隔結尾。",
        "type": "float",
        "min": 0.5,
        "max": 10.0,
        "step": 0.5,
        "category": "語音辨識與合成",
    },
    "FFMPEG": {
        "label": "FFmpeg 執行檔路徑",
        "description": "系統中的 FFmpeg 執行檔位址，用於音訊解碼與轉檔。",
        "type": "str",
        "category": "語音辨識與合成",
    },
    # === 音樂播放與隊列 ===
    "MUSIC_FFMPEG_OPTIONS": {
        "label": "音樂 FFmpeg 轉碼參數",
        "description": "播放 YouTube 或音訊串流時傳遞給 FFmpeg 的音量與標準化參數。",
        "type": "str",
        "category": "音樂播放與隊列",
    },
    "MUSIC_DEFAULT_VOLUME": {
        "label": "音樂預設播放音量倍率",
        "description": "傳送至 Discord 語音頻道時的 PCM 數位音量增益倍率（預設 1.2 倍）。",
        "type": "float",
        "min": 0.1,
        "max": 3.0,
        "step": 0.1,
        "category": "音樂播放與隊列",
    },
    "MUSIC_FFMPEG_BEFORE_OPTIONS": {
        "label": "音樂 FFmpeg 串流重連參數",
        "description": "確保網路波動時串流可自動重連的 FFmpeg 參數。",
        "type": "str",
        "category": "音樂播放與隊列",
    },
    "MUSIC_PLAYLIST_MAX_ITEMS": {
        "label": "單次匯入歌單最大首數",
        "description": "使用 YouTube 播放清單時一次最多允許載入的歌曲數量。",
        "type": "int",
        "min": 5,
        "max": 200,
        "category": "音樂播放與隊列",
    },
    "MUSIC_QUEUE_VIEW_TIMEOUT": {
        "label": "音樂介面按鈕逾時（秒）",
        "description": "音樂點歌介面按鈕（上一首/下一首/暫停）過多長秒數後自動停用。",
        "type": "int",
        "min": 30,
        "max": 600,
        "category": "音樂播放與隊列",
    },
    # === 圖片生成與圖庫 API ===
    "POLLINATIONS_API_URL": {
        "label": "Pollinations 圖片 API 網址",
        "description": "使用 Pollinations 進行免費二次元/寫實圖片生成時的請求端點。",
        "type": "str",
        "category": "圖片生成",
    },
    "POLLINATIONS_TIMEOUT_SECONDS": {
        "label": "Pollinations 生成逾時秒數",
        "description": "等待 Pollinations 返回圖片的最長等待時間。",
        "type": "int",
        "min": 10,
        "max": 120,
        "category": "圖片生成",
    },
    "GEMINI_IMAGE_TIMEOUT_SECONDS": {
        "label": "Gemini 生圖逾時秒數",
        "description": "呼叫 Google Gemini 圖片生成模型的最大逾時限制。",
        "type": "int",
        "min": 10,
        "max": 120,
        "category": "圖片生成",
    },
    "HF_IMAGE_MODEL": {
        "label": "HuggingFace 繪圖模型 ID",
        "description": "若啟用 HF API 繪圖，採用之模型名稱（如 animagine-xl-3.1）。",
        "type": "str",
        "category": "圖片生成",
    },
    "HF_IMAGE_TIMEOUT_SECONDS": {
        "label": "HuggingFace 生圖逾時秒數",
        "description": "調用 HF API 繪圖等待的最大限制時間。",
        "type": "int",
        "min": 10,
        "max": 300,
        "category": "圖片生成",
    },
    "SD_DEFAULT_SAMPLER": {
        "label": "SD 預設採樣器 (Sampler)",
        "description": "Stable Diffusion WebUI 預設採樣器名稱（例如 DPM++ 2M Karras）。",
        "type": "str",
        "category": "圖片生成",
    },
    "SD_NEGATIVE_PROMPT_BASE": {
        "label": "SD 預設負面提示詞",
        "description": "生成圖片時自動帶入的負面過濾提示，防崩壞與低畫質。",
        "type": "str",
        "category": "圖片生成",
    },
    "SD_TIMEOUT_SECONDS": {
        "label": "SD WebUI 生圖逾時（秒）",
        "description": "本機 Stable Diffusion WebUI 最多允許生成的秒數。",
        "type": "int",
        "min": 30,
        "max": 300,
        "category": "圖片生成",
    },
    "DANBOORU_API_URL": {
        "label": "Danbooru 圖庫 API 網址",
        "description": "找圖功能使用的 Danbooru 標籤檢索端點。",
        "type": "str",
        "category": "二次元圖庫 API",
    },
    "SAFEBOORU_API_URL": {
        "label": "Safebooru API 檢索網址",
        "description": "安全全齡向動漫圖庫 Safebooru 的 XML/JSON 查詢 API 網址。",
        "type": "str",
        "category": "二次元圖庫 API",
    },
    "SAFEBOORU_IMAGE_BASE_URL": {
        "label": "Safebooru 圖片來源 URL",
        "description": "Safebooru 下載與展示圖片的基底 URL。",
        "type": "str",
        "category": "二次元圖庫 API",
    },
    "SAFEBOORU_HTTP_TIMEOUT": {
        "label": "Safebooru 請求逾時（秒）",
        "description": "抓取 Safebooru 圖片時的 HTTP 連線逾時限制。",
        "type": "int",
        "min": 5,
        "max": 60,
        "category": "二次元圖庫 API",
    },
    "PIXIV_TAG_MIN_POST_COUNT": {
        "label": "Pixiv 標籤最低作品門檻",
        "description": "在 Pixiv 找圖時，標籤必須具有的基本作品數，確保圖片熱度品質。",
        "type": "int",
        "min": 10,
        "max": 10000,
        "category": "二次元圖庫 API",
    },
    "PIXIV_TAG_MAX_PAGES": {
        "label": "Pixiv 搜尋最大翻頁數",
        "description": "在 Pixiv 搜尋標籤作品時最多允許翻看的頁數。",
        "type": "int",
        "min": 1,
        "max": 100,
        "category": "二次元圖庫 API",
    },
    "PIXIV_TAG_REQUEST_DELAY": {
        "label": "Pixiv API 請求間隔（秒）",
        "description": "連續抓取 Pixiv 時的防護等待秒數，避免被伺服器限流。",
        "type": "float",
        "min": 0.1,
        "max": 5.0,
        "step": 0.1,
        "category": "二次元圖庫 API",
    },
    # === 外部資訊檢索 (Web/天氣/股市) ===
    "DUCKDUCKGO_SEARCH_URL": {
        "label": "DuckDuckGo 搜尋網址端點",
        "description": "雪莉進行即時時事與情報檢索所呼叫的搜尋網址。",
        "type": "str",
        "category": "外部資訊檢索",
    },
    "OPENWEATHERMAP_API_URL": {
        "label": "OpenWeatherMap 天氣 API",
        "description": "詢問各地區氣象預報所呼叫之 OpenWeatherMap 網址。",
        "type": "str",
        "category": "外部資訊檢索",
    },
    "WEATHER_EXTRACT_TIMEOUT": {
        "label": "天氣資料分析逾時（秒）",
        "description": "處理天氣 API 回傳資訊的最大逾時時間。",
        "type": "int",
        "min": 5,
        "max": 60,
        "category": "外部資訊檢索",
    },
    "WEATHER_REPLY_TIMEOUT": {
        "label": "天氣對話生成逾時（秒）",
        "description": "雪莉根據氣象預報產生對話的推論逾時秒數。",
        "type": "int",
        "min": 10,
        "max": 120,
        "category": "外部資訊檢索",
    },
    "STOCK_EXTRACT_TIMEOUT": {
        "label": "股市報價解析逾時（秒）",
        "description": "抓取與解析台股、美股行情資訊的逾時限制。",
        "type": "int",
        "min": 5,
        "max": 60,
        "category": "外部資訊檢索",
    },
    "STOCK_REPLY_TIMEOUT": {
        "label": "股市分析回覆逾時（秒）",
        "description": "雪莉產生股價走勢與名偵探評語對話的推論限制。",
        "type": "int",
        "min": 10,
        "max": 120,
        "category": "外部資訊檢索",
    },
    "YOUTUBE_SEARCH_API_URL": {
        "label": "YouTube 搜尋 API URL",
        "description": "Google YouTube v3 搜尋 API 端點。",
        "type": "str",
        "category": "YouTube 爬蟲",
    },
    "YOUTUBE_RSS_FEED_URL": {
        "label": "YouTube RSS 訂閱端點 URL",
        "description": "透過 RSS 快訊檢查特定頻道最新發片的 XML 基底網址。",
        "type": "str",
        "category": "YouTube 爬蟲",
    },
    "YOUTUBE_RSS_NS": {
        "label": "YouTube RSS Atom 命名空間",
        "description": "解析 YouTube Feed 用的 XML Atom Namespace。",
        "type": "str",
        "category": "YouTube 爬蟲",
    },
    "YOUTUBE_EXPLORER_TIMEOUT": {
        "label": "YouTube 爬蟲執行逾時（秒）",
        "description": "執行單次 YouTube 最新上傳檢查的最大限制秒數。",
        "type": "int",
        "min": 5,
        "max": 120,
        "category": "YouTube 爬蟲",
    },
    # === 任務佇列優先級 ===
    "PRIORITY_TAG_RESPONSE": {
        "label": "對話回覆優先權 (Priority)",
        "description": "真人點名與主動聊天的佇列優先序（數值越小越優先）。預設 1（最高）。",
        "type": "int",
        "min": 1,
        "max": 100,
        "category": "任務佇列優先級",
    },
    "PRIORITY_INTERJECT": {
        "label": "主動插話優先權 (Priority)",
        "description": "雪莉插話推論在 BotBrain 中的優先等級。預設 10。",
        "type": "int",
        "min": 1,
        "max": 100,
        "category": "任務佇列優先級",
    },
    "PRIORITY_HEARTBEAT": {
        "label": "日常心跳聊天優先權",
        "description": "主動巡邏聊天推論任務優先等第。預設 20。",
        "type": "int",
        "min": 1,
        "max": 100,
        "category": "任務佇列優先級",
    },
    "PRIORITY_VIBE_UPDATE": {
        "label": "話題氛圍萃取優先權",
        "description": "背景萃取聊天室話題與氣氛推論優先等第。預設 50。",
        "type": "int",
        "min": 1,
        "max": 100,
        "category": "任務佇列優先級",
    },
    "PRIORITY_DRAW": {
        "label": "二次元生圖與修圖優先權",
        "description": "繪圖與圖片描述推論優先級別。預設 80。",
        "type": "int",
        "min": 1,
        "max": 100,
        "category": "任務佇列優先級",
    },
    "PRIORITY_WEB_EXPLORER": {
        "label": "閒置網路爬蟲優先權",
        "description": "後台網路時事爬蟲推論任務優先等第。預設 99（最低）。",
        "type": "int",
        "min": 1,
        "max": 100,
        "category": "任務佇列優先級",
    },
    # === 系統與網路進階參數 ===
    "BOT_BRAIN_MAX_RETRIES": {
        "label": "BotBrain LLM 重試次數",
        "description": "如果模型推論發生異常或空訊，自動重試的最多次數。",
        "type": "int",
        "min": 1,
        "max": 10,
        "category": "系統與網路",
    },
    "BOT_BRAIN_RETRY_DELAY": {
        "label": "BotBrain 重試等候秒數",
        "description": "每次失敗後等待幾秒鐘再重新發起推論。",
        "type": "int",
        "min": 1,
        "max": 30,
        "category": "系統與網路",
    },
    "COMPOUND_PARSER_TIMEOUT": {
        "label": "複合意圖解析逾時（秒）",
        "description": "解析使用者複雜多句對話語境意圖的上限秒數。",
        "type": "int",
        "min": 3,
        "max": 60,
        "category": "系統與網路",
    },
    "DEFAULT_HTTP_TIMEOUT": {
        "label": "預設對外 HTTP 請求逾時",
        "description": "發起對外網路 API 呼叫（如搜尋、抓圖）的預設秒數門檻。",
        "type": "int",
        "min": 3,
        "max": 60,
        "category": "系統與網路",
    },
    "DEFAULT_USER_AGENT": {
        "label": "對外爬蟲請求 User-Agent",
        "description": "爬取網頁與 API 時所宣告之瀏覽器/機器人識別字串。",
        "type": "str",
        "category": "系統與網路",
    },
    "DISCORD_UI_VIEW_TIMEOUT": {
        "label": "Discord 互動選單按鈕逾時",
        "description": "發布至 Discord 的按鈕與選單最長可維持可點擊狀態的時間（秒）。",
        "type": "int",
        "min": 30,
        "max": 1800,
        "category": "系統與網路",
    },
    "DRAW_RAW_TIMEOUT": {
        "label": "原圖描述生成逾時（秒）",
        "description": "將自然對話文字轉換為 SD 或繪圖提示詞推論的時間上限。",
        "type": "int",
        "min": 10,
        "max": 120,
        "category": "系統與網路",
    },
    "DRAW_EXPAND_TIMEOUT": {
        "label": "繪圖提示詞擴充逾時（秒）",
        "description": "擴充美術畫風細節提示詞的推理最長等候時間。",
        "type": "int",
        "min": 5,
        "max": 60,
        "category": "系統與網路",
    },
    "INTERJECT_SCORE_TIMEOUT": {
        "label": "插話評分推論逾時（秒）",
        "description": "判斷當前話題是否適合雪莉插話的推論逾時限制。",
        "type": "int",
        "min": 5,
        "max": 60,
        "category": "系統與網路",
    },
    "INTERJECT_REPLY_TIMEOUT": {
        "label": "插話發文生成逾時（秒）",
        "description": "判定為適合插話後，實際產生對話的最高逾時秒數。",
        "type": "int",
        "min": 10,
        "max": 120,
        "category": "系統與網路",
    },
    "SCHEDULE_PARSE_TIMEOUT": {
        "label": "自然語言排程解析逾時",
        "description": "把對話語句解讀為定時提醒或 Cron 任務的推理時間上限。",
        "type": "int",
        "min": 5,
        "max": 60,
        "category": "系統與網路",
    },
    "VISION_ANALYSIS_TIMEOUT": {
        "label": "圖片視覺識別推論逾時",
        "description": "把使用者上傳的圖片傳給 Vision 模型分析看懂的時間上限。",
        "type": "int",
        "min": 10,
        "max": 120,
        "category": "系統與網路",
    },
    "HEALTH_CHECK_TIMEOUT": {
        "label": "健康檢查心跳逾時（秒）",
        "description": "監控後端 Ollama 與 Discord 狀態的回應逾時判定秒數。",
        "type": "int",
        "min": 2,
        "max": 30,
        "category": "健康監控",
    },
    "HEALTH_ALERT_CHANNEL_ID": {
        "label": "系統警報 Discord 頻道 ID",
        "description": "當系統狀態異常或模型離線時，推播警報通知的頻道 ID。",
        "type": "int",
        "category": "健康監控",
    },
    "RATE_LIMIT_REPLY_MESSAGE": {
        "label": "高頻速率限制提示訊息",
        "description": "對手速太快或短時間頻繁送請求的使用者推播的溫馨冷卻文字。",
        "type": "str",
        "category": "速率限制",
    },
    "WEB_UI_HOST": {
        "label": "Web UI 監聽 Host IP",
        "description": "網頁後端監聽之網路介面地址，預設 0.0.0.0 代表允許所有 IP 訪問。",
        "type": "str",
        "category": "系統與網路",
    },
    "WEB_UI_PORT": {
        "label": "Web UI 伺服器連接埠",
        "description": "網頁控制台開啟的連接埠（Port），預設為 8763。",
        "type": "int",
        "min": 1024,
        "max": 65535,
        "category": "系統與網路",
    },
    "WEB_UI_HTTPS": {
        "label": "Web UI 啟用 HTTPS 加密",
        "description": "是否啟用 SSL/TLS 證書提供加密 https:// 連線，強烈建議 ON。",
        "type": "bool",
        "category": "系統與網路",
    },
    "WEB_UI_HTTP_TIMEOUT": {
        "label": "Web UI 內部呼叫 API 逾時",
        "description": "網頁伺服器請求 Ollama 與 LM Studio 模型清單的最大等待秒數。",
        "type": "int",
        "min": 2,
        "max": 30,
        "category": "系統與網路",
    },
    "WEB_UI_RADMIN_IP": {
        "label": "Radmin VPN 主機推薦 IP",
        "description": "供介面上提示或快速連線參考的 Radmin VPN IP 位址。",
        "type": "str",
        "category": "系統與網路",
    },
}


def get_editable_settings() -> dict:
    """回傳所有可編輯設定，含 meta 資訊（label、description、type、category 等）。

    Returns:
        dict: key → {value, label, description, type, category, ...constraints}
    """
    g = globals()
    editable: dict = {}
    _SENSITIVE_KEYWORDS = ("PASSWORD", "PATH", "DIR", "KEY", "TOKEN", "SECRET")

    for k, v in g.items():
        if not k.isupper() or k.startswith("_"):
            continue
        if type(v) not in (int, float, str, bool):
            continue
        if any(kw in k for kw in _SENSITIVE_KEYWORDS):
            continue

        meta = SETTINGS_META.get(k, {})
        entry: dict = {
            "value": v,
            "label": meta.get("label", k),
            "description": meta.get("description", ""),
            "type": meta.get("type", type(v).__name__),
            "category": meta.get("category", "其他"),
        }
        # 選擇性加入數值約束與下拉選單選項
        for constraint in ("min", "max", "step", "options"):
            if constraint in meta:
                entry[constraint] = meta[constraint]

        editable[k] = entry
    return editable

