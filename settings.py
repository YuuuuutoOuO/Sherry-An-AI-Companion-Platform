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
from typing import Any

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
VIDEO_DIR: Path = BASE_DIR / "Video"
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

# 吐槽清單系統（關鍵字觸發 + 獨立機率 + 名偵探毒舌回覆）
ROAST_RULES_PATH: Path = DATA_DIR / "roast_rules.json"

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
# FreeToken / Ollama / LM Studio / Cloud LLMs
# ---------------------------------------------------------------------------
# 支援前綴決定後端路由：
#   "freetoken:" → FreeToken MoE 推理引擎 (OpenAI 相容格式)
#   "ollama:"    → Ollama 原生 API
#   "lms:"       → LM Studio (OpenAI 相容格式, 向下相容至 LLM_BASE_URL)
#   "vllm:"      → vLLM 引擎 (OpenAI 相容格式)
#   "openai:"    → OpenAI ChatGPT          (OpenAI 相容, 需 API Key)
#   "deepseek:"  → DeepSeek                (OpenAI 相容, 需 API Key)
#   "gemini:"    → Google Gemini           (OpenAI 相容, 需 API Key)
#   "openrouter:" → OpenRouter              (OpenAI 相容, 需 API Key)
#   "claude:"    → Anthropic Claude        (Anthropic Messages API, 需 API Key)
# 若無前綴，預設為 "freetoken:"
# ---------------------------------------------------------------------------

# --- 本機後端 Base URLs ---
LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", os.getenv("FREETOKEN_BASE_URL", "http://localhost:1919/v1"))
FREETOKEN_BASE_URL: str = os.getenv("FREETOKEN_BASE_URL", "http://localhost:1919/v1")
LMS_BASE_URL: str = os.getenv("LMS_BASE_URL", "http://localhost:1234/v1")
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
VLLM_BASE_URL: str = os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1")

# --- 雲端 API Keys ---
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
CLAUDE_API_KEY: str = os.getenv("CLAUDE_API_KEY", "")
OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")

# --- 雲端 Base URLs（可自訂，預設為官方端點）---
OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
GEMINI_BASE_URL: str = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
CLAUDE_BASE_URL: str = os.getenv("CLAUDE_BASE_URL", "https://api.anthropic.com/v1")
OPENROUTER_BASE_URL: str = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

# --- 預設模型定義 ---
LLM_MODEL_NAME: str = os.getenv("LLM_MODEL_NAME", "mistralai/Mixtral-8x7B-Instruct-v0.1")

# 主力模型：平衡智商與生成速度，適合日常深度對話與邏輯推演
PRIMARY_MODEL: str = os.getenv("PRIMARY_MODEL", f"freetoken:{LLM_MODEL_NAME}")

# 無道德限制模型：專門處理「橘雪莉」缺乏倫理觀的瘋狂人設
NOMORAL_MODEL: str = os.getenv("NOMORAL_MODEL", PRIMARY_MODEL)

# 快速回覆模型：要求極速但需要足夠的基礎智商
QUICK_MODEL: str = os.getenv("QUICK_MODEL", "ollama:krith/mistral-nemo-instruct-2407-abliterated:IQ4_XS")

# 輕量分類模型：用於「插話評分」與「YouTube 影片篩選」
CLASSIFIER_MODEL: str = os.getenv("CLASSIFIER_MODEL", "ollama:qwen2.5:3b")

# 氛圍話題萃取模型。
VIBE_MODEL: str = os.getenv("VIBE_MODEL", "ollama:llama3.2:3b")

# 視覺辨識模型 (多模態圖片理解)。
# 留空 (預設) 代表啟用「端到端原生多模態」，直接由主力模型 (PRIMARY_MODEL) 讀圖，極速秒回。
# 若指定模型名稱 (如 "ollama:minicpm-v")，則採用二階段獨立視覺模型解析流程。
_raw_vision_model: str = os.getenv("VISION_MODEL", "").strip()
VISION_MODEL: str = "" if (_raw_vision_model.endswith(":") or _raw_vision_model in ("freetoken", "ollama", "lms", "vllm", "openai", "deepseek", "gemini", "openrouter", "claude")) else _raw_vision_model

# 雲端搜尋專用模型
CLOUD_SEARCH_MODEL: str = "deepseek:deepseek-v4-flash"

# --- 逾時與連線保護設定 (MoE 專家搬移適配) ---
LLM_TIMEOUT: int = int(os.getenv("LLM_TIMEOUT", "180"))
LLM_STREAM_CHUNK_TIMEOUT: float = float(os.getenv("LLM_STREAM_CHUNK_TIMEOUT", "60.0"))
OLLAMA_TIMEOUT: int = int(os.getenv("OLLAMA_TIMEOUT", str(LLM_TIMEOUT)))

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
PARALLEL_STEP_TIMEOUT: float = 60.0

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

# ---------------------------------------------------------------------------
# Long-Term Memory（長期記憶系統參數）
# ---------------------------------------------------------------------------
# 每次注入 LLM Prompt 的 facts 數量（不含 rule 類，rule 類另行全數注入）
MEMORY_MAX_FACTS_IN_CONTEXT: int = int(os.getenv("MEMORY_MAX_FACTS_IN_CONTEXT", "15"))

# 每位使用者最多保留的 facts 數量（即時萃取與夜間整合共用上限）
MEMORY_MAX_FACTS_PER_USER: int = int(os.getenv("MEMORY_MAX_FACTS_PER_USER", "25"))

# importance >= 此值的 facts 永久保留，不受衰減與上限影響
MEMORY_PERMANENT_FACT_THRESHOLD: int = int(os.getenv("MEMORY_PERMANENT_FACT_THRESHOLD", "4"))

# 每累積幾句話觸發一次即時記憶萃取（每位使用者獨立計數）
MEM_DIGEST_THRESHOLD: int = int(os.getenv("MEM_DIGEST_THRESHOLD", "10"))

# 即時萃取使用的模型（建議與 PRIMARY_MODEL 一致，確保萃取品質）
MEMORY_EXTRACT_MODEL: str = os.getenv("MEMORY_EXTRACT_MODEL", PRIMARY_MODEL)

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
# Micro Chain-of-Thought (Micro-CoT) & Debug Detail Mode
# ---------------------------------------------------------------------------
# 啟用後，雪莉在每次 CHAT 回覆前會先輸出 <thought>...</thought> 內心推理，
# 再給出正式回覆。thought 區塊對用戶不可見，會被存進 data/deep_thinking.json。
# 若發現模型因為思考步驟太長而變慢，可暫時設為 False 關閉。
MICRO_COT_ENABLED: bool = True

# Discord 對話卡片式偵錯模式（顯示 Intent、CoT 思考過程、耗時毫秒、模型名稱等，與 WebUI 對齊）
DEBUG_DETAIL: bool = False


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
MUSIC_FFMPEG_BEFORE_OPTIONS: str = "-reconnect 1 -reconnect_streamed 1 -reconnect_at_eof 1 -reconnect_on_network_error 1 -reconnect_on_http_error 4xx,5xx -reconnect_delay_max 5 -loglevel error"
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
TAG_METADATA_PATH: Path = FILE_DIR / "tag_metadata.json"
PIXIV_TAG_MIN_POST_COUNT: int = 100
PIXIV_TAG_MAX_PAGES: int = 60
PIXIV_TAG_REQUEST_DELAY: float = 0.5
DANBOORU_API_URL: str = "https://danbooru.donmai.us/tags.json"
DANBOORU_POSTS_API_URL: str = "https://danbooru.donmai.us/posts.json"

WORLD_LOG_DIR: Path = DATA_DIR / "world_history"
WORLD_MEMORY_PATH: Path = DATA_DIR / "world_memory.json"
BOT_LOG_PATH: Path = DATA_DIR / "bot.log"

# ---------------------------------------------------------------------------
# Memory System & Digest Thresholds
# ---------------------------------------------------------------------------
NIGHTLY_MIN_MSGS_THRESHOLD: int = int(os.getenv("NIGHTLY_MIN_MSGS_THRESHOLD", "5"))

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
DANBOORU_HTTP_TIMEOUT: int = 12
VISION_ANALYSIS_TIMEOUT: int = 120
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
            KNOWN_PREFIXES = ("ollama:", "lms:", "vllm:", "openai:", "deepseek:", "gemini:", "openrouter:", "claude:")
            for k, v in overrides.items():
                if k in globals():
                    if isinstance(v, str) and (v.strip().endswith(":") or v.strip() in ("ollama", "lms", "vllm", "openai", "deepseek", "gemini", "openrouter", "claude")):
                        globals()[k] = ""
                        os.environ[k] = ""
                    else:
                        globals()[k] = v
                        if isinstance(v, (str, int, float, bool)):
                            os.environ[k] = str(v)
    except Exception as e:
        logger.warning("Failed to load config_override.json: %s", e)

WEB_UI_PASSWORD: str = os.getenv("WEB_UI_PASSWORD", "1919810")  # 後台密碼
WEB_UI_MAX_LOGIN_ATTEMPTS: int = 5  # 1 分鐘內登入失敗最大次數，超過即封鎖 IP
WEB_UI_LOCKOUT_MINUTES: int = 15   # 觸發封鎖後禁止登入的分鐘數

def update_setting(key: str, value: Any) -> bool:
    """更新全域設定值並持久化至 config_override.json。"""
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

    if isinstance(value, str):
        value_str = value.strip()
        if value_str.endswith(":") or value_str in ("ollama", "lms", "vllm", "openai", "deepseek", "gemini", "openrouter", "claude"):
            value = ""
        else:
            value = value_str

    g[key_str] = value
    if isinstance(value, (str, int, float, bool)):
        os.environ[key_str] = str(value)

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
# Settings Metadata & Web UI Schema Delegate
# ---------------------------------------------------------------------------

def get_editable_settings() -> dict[str, Any]:
    """回傳所有可編輯設定，含 meta 資訊（委託 web_ui.settings_schema 解析）。

    Returns:
        dict: key -> {value, label, description, type, category, ...constraints}
    """
    from web_ui.settings_schema import get_editable_settings as _get_editable
    return _get_editable(globals())


