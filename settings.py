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

import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
from enum import IntEnum
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

from file.AI_personality import BOT_NAME as BOT_NAME, CORE_PERSONA as CORE_PERSONA, LOREBOOK as LOREBOOK

# ---------------------------------------------------------------------------
# Project Paths
# ---------------------------------------------------------------------------
BASE_DIR: Path = Path(__file__).resolve().parent
DATA_DIR: Path = BASE_DIR / "data"
FILE_DIR: Path = BASE_DIR / "file"
SOUL_DIR: Path = FILE_DIR / "soul"
VIDEO_DIR: Path = BASE_DIR / "Video"

# ---------------------------------------------------------------------------
try:
    load_dotenv(dotenv_path=BASE_DIR / ".env", override=True, encoding="utf-8")
except Exception as e:
    logger.warning("Failed to load .env file: %s", e)

CURRENT_SOUL_NAME: str = "manosaba_sherry_default.md"
DEFAULT_SOUL_PATH: Path = SOUL_DIR / CURRENT_SOUL_NAME
COMMON_RULES_PATH: Path = SOUL_DIR / "common_rules.md"
SOUL_CONTENT: str = ""
# 頻道至 Soul 人設檔對映字典（頻道 ID -> Soul 檔名，例如 {123456789: "custom_default.md"}）
CHANNEL_SOUL_MAP: dict[int, str] = {}


def get_available_soul_names() -> list[str]:
    """自動掃描 SOUL_DIR 獲取所有 *_default.md 人設檔案名稱清單。

    Returns:
        list[str]: 依檔名排序的人設檔名列表。
    """
    if not SOUL_DIR.is_dir():
        return ["manosaba_sherry_default.md"]
    names = sorted([f.name for f in SOUL_DIR.glob("*_default.md") if f.is_file()])
    return names if names else ["manosaba_sherry_default.md"]

HEARTBEAT_PATH: Path = DATA_DIR / "heartbeat.md"
DEFAULT_HEARTBEAT_CHANNEL_ID: int = int(os.getenv("DEFAULT_HEARTBEAT_CHANNEL_ID", "1519967448982487201"))

CHANNEL_VIBE_PATH: Path = DATA_DIR / "channel_vibe.json"

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
OBSERVABILITY_PII_REDACTION: bool = os.getenv("OBSERVABILITY_PII_REDACTION", "true").lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# Schedule System
# ---------------------------------------------------------------------------
SCHEDULE_PATH: Path = BASE_DIR / "data" / "schedule.json"

# ---------------------------------------------------------------------------
# FFmpeg
# ---------------------------------------------------------------------------
FFMPEG = str(BASE_DIR / 'ffmpeg')

# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------
DISCORD_BOT_TOKEN: str = os.getenv("DISCORD_BOT_TOKEN", "")

def _parse_int_list_env(env_name: str, default: list[int] | None = None) -> list[int]:
    """自環境變數安全解析整數列表（支援逗號分隔、中括號與引號格式）。

    Args:
        env_name: 環境變數名稱。
        default: 預設整數列表。

    Returns:
        list[int]: 解析後的整數列表。
    """
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return list(default) if default is not None else []
    raw = raw.strip("[]")
    parsed: list[int] = []
    for token in raw.split(","):
        token = token.strip().strip("'\"")
        if token.isdigit():
            parsed.append(int(token))
    return parsed

ADMIN_ROLE_IDS: list[int] = _parse_int_list_env("ADMIN_ROLE_IDS", [])
ADMIN_USER_IDS: list[int] = (
    _parse_int_list_env("ADMIN_USER_IDS", [])
    or _parse_int_list_env("ADMIN_USER_ID", [])
    or _parse_int_list_env("DC_USER_ID", [])
    or _parse_int_list_env("DISCORD_USER_ID", [])
)
NSFW_ALLOWED_CHANNEL_IDS: list[int] = _parse_int_list_env("NSFW_ALLOWED_CHANNEL_IDS", [])

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
#   "groq:"      → Groq Cloud              (OpenAI 相容, 需 API Key)
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
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")

# --- 雲端 Base URLs（可自訂，預設為官方端點）---
OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
GEMINI_BASE_URL: str = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
CLAUDE_BASE_URL: str = os.getenv("CLAUDE_BASE_URL", "https://api.anthropic.com/v1")
OPENROUTER_BASE_URL: str = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
GROQ_BASE_URL: str = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")

# --- 預設模型定義 ---
# 全專案僅保留 5 個語言模型槽位，其餘用途一律沿用 PRIMARY_MODEL 或 LIGHT_MODEL。
# 新增用途前請先思考：真的需要獨立模型嗎？過細的槽位會讓設定難以維護、
# 測試難以覆蓋，fallback 鏈也會跟著膨脹。
#
#   PRIMARY_MODEL        主力：日常對話、ReAct Agent、記憶萃取、深夜整合、
#                        道德過濾、對話濃縮（全部沿用主力，不再另設槽位）
#   LIGHT_MODEL          輕量：快速回覆、插話評分、影片篩選、氛圍萃取、
#                        並行任務規劃（短輸出、低延遲、可容忍較弱推理）
#   VISION_MODEL         多模態看圖（留空 = 沿用 PRIMARY_MODEL 原生看圖）
#   CLOUD_SEARCH_MODEL   外部搜尋／深度研究（需要網路工具，與日常對話分開）
#   SELF_MONITORING_MODEL 自我稽核（獨立的可靠性守門員，不與其他用途共用）

# 主力模型：平衡智商與生成速度，適合日常深度對話與邏輯推演
PRIMARY_MODEL: str = os.getenv("PRIMARY_MODEL", "freetoken:mistralai/Mixtral-8x7B-Instruct-v0.1")

# 輕量模型：要求極速但需要足夠的基礎智商，處理短輸出小任務
LIGHT_MODEL: str = os.getenv("LIGHT_MODEL", "ollama:qwen2.5:3b")

# 視覺辨識模型 (多模態圖片對話模型)。
# 當訊息夾帶圖片時，系統直接由該模型同時執行看圖理解與角色性格回覆（單階段直出），避免重複調用主力模型導致顯卡負擔過重。
# 若留空 (預設)，代表啟用主力模型原生看圖，直接由 PRIMARY_MODEL 讀圖回覆。
_raw_vision_model: str = os.getenv("VISION_MODEL", "").strip()
VISION_MODEL: str = "" if (_raw_vision_model.endswith(":") or _raw_vision_model in ("freetoken", "ollama", "lms", "vllm", "openai", "deepseek", "gemini", "openrouter", "claude", "groq")) else _raw_vision_model

# Gemini 雲端模型輸出 Token 保障（Gemini 2.0/2.5 Flash 具備思考推理機制，需足夠 Token 空間避免被截斷）
GEMINI_MIN_OUTPUT_TOKENS: int = int(os.getenv("GEMINI_MIN_OUTPUT_TOKENS", "4096"))

# Gemini 思考強度（thinking_level，對應 OpenAI 相容端點的 reasoning_effort）。
# 官方文件：max_output_tokens 是包含思考 Token 的硬性總上限，且不會改變思考預算配置。
# 預設沿用模型的 medium，而非降為 low。
#
# 背景：Gemini 3.x Flash 預設 thinking_level=medium（8192 思考 Token）。OpenAI 相容端點的
# max_tokens 是「思考 + 輸出」的硬性總上限，若額度不足會回傳「空輸出」（HTTP 200、無內容）。
# 先前為解決空輸出而把思考強度調降為 low，等於用「牺牲推論品質」換取不空回應，
# 導致雪莉的日常對話思考深度變浅、回應變短變平。
#
# 現行做法是保留 medium 的思考品質，改由 client.py 依思考預算自動把 max_tokens 拉高
# （見 _GEMINI_THINKING_BUDGET），因此不需要用低思考量換取額度。
# 若雲端 token 成本成為負擔，可在此降到 low，額度會自動跟著收縮。
# 設為空字串可停用，僅沿用模型預設思考強度。
GEMINI_REASONING_EFFORT: str = os.getenv("GEMINI_REASONING_EFFORT", "medium").strip().lower()

# === 推論失敗備援鏈（Web UI 可設定）===
# 這是模型槽位之外「失敗後怎麼辦」的單一真相來源。
# 純地端部署：將 FALLBACK_ENABLED 設為 False，或 FALLBACK_MODELS 留空 → 失敗就明確失敗，不外送。
# 地端優先雲端救援：填 "ollama:xxx,gemini:gemini-3.5-flash-lite" → 前者失效才後送。
FALLBACK_ENABLED: bool = os.getenv("FALLBACK_ENABLED", "True").strip().lower() in ("1", "true", "yes", "on")

FALLBACK_MODELS: list[str] = [
    "gemini:gemini-3.5-flash-lite",
    "gemini:gemini-3.8-flash",
    "gemini:gemini-3.6-flash",
]

# 雲端搜尋專用模型
CLOUD_SEARCH_MODEL: str = os.getenv("CLOUD_SEARCH_MODEL", "deepseek:deepseek-v4-flash")

# 自我監控與延遲/品質稽核專用模型 (Self-Monitoring & Auditing Model)
# 獨立於主力對話與雲端搜尋模型，專門用於背景事故診斷、人設與回答品質審查、死循環攔截與自癒決策。
# 支援前綴格式，例如 "gemini:gemini-3.5-flash-lite" (推薦：速度極快、成本極低)、"claude:claude-3-5-haiku-20241022" 等
SELF_MONITORING_MODEL: str = os.getenv("SELF_MONITORING_MODEL", "gemini:gemini-3.5-flash-lite")

# 右鍵訊息應用程式是否使用私密回覆 (True: 僅自己可見並帶有「只有您才能看到這個」, False: 公開發布於頻道中)
CONTEXT_MENU_EPHEMERAL: bool = os.getenv("CONTEXT_MENU_EPHEMERAL", "False").lower() in ("true", "1", "yes")

# --- 逾時與連線保護設定 (MoE 專家搬移適配) ---
LLM_TIMEOUT: int = int(os.getenv("LLM_TIMEOUT", "180"))
# 單一 SSE chunk 的 socket 讀取逾時。放寬避免「本機 GPU 慢速生成 → 60s 內無新 chunk 就被斷線重試」，
# 否則會與 OLLAMA_TIMEOUT / LLM_TIMEOUT 疊加重試，導致整體超過 2 分鐘。
LLM_STREAM_CHUNK_TIMEOUT: float = float(os.getenv("LLM_STREAM_CHUNK_TIMEOUT", "120.0"))
OLLAMA_TIMEOUT: int = int(os.getenv("OLLAMA_TIMEOUT", str(LLM_TIMEOUT)))
# 要求串流引擎附帶真實 usage。True: 送 stream_options.include_usage，讓 log 量到真實
# prompt/completion tokens（並可算 tok/s）；部分不認識此欄位的引擎會忽略它，無副作用。
LLM_STREAM_INCLUDE_USAGE: bool = os.getenv("LLM_STREAM_INCLUDE_USAGE", "True").lower() in ("true", "1", "yes")

# ---------------------------------------------------------------------------
# 代理人調度與多工架構設定 (Agent Dispatcher Settings)
# ---------------------------------------------------------------------------
# 任務調度模式：INTEGRATED (單階段整合自主調度，預設推薦) | PARALLEL (雙階段並行) | REACT (多步驟 ReAct)
AGENT_DISPATCH_MODE: str = os.getenv("AGENT_DISPATCH_MODE", "INTEGRATED").upper()

# 行為自適應對話抑制開關：若為 True，當發動的工具全為 DIRECT_UI (如點歌/搜圖/畫圖) 且使用者無聊天需求時，動作完成即刻結束，不產生多餘廢話文字
ACTION_EARLY_TERMINATION_ENABLED: bool = os.getenv("ACTION_EARLY_TERMINATION_ENABLED", "True").lower() in ("true", "1", "yes")

# 並行任務調度（任務拆解模型沿用 LIGHT_MODEL，單次輸出短且需低延遲）
PARALLEL_MAX_TASKS: int = int(os.getenv("PARALLEL_MAX_TASKS", "8"))
PARALLEL_STEP_TIMEOUT: float = float(os.getenv("PARALLEL_STEP_TIMEOUT", "120.0"))

# ReAct 多步驟 Agent 推理（「思考→行動→觀察」迭代迴圈，推理模型沿用 PRIMARY_MODEL）

# ReAct 迴圈控制參數
REACT_MAX_STEPS: int = int(os.getenv("REACT_MAX_STEPS", "5"))                    # 最多步數（日常聊天 1 步即收斂）
REACT_STEP_TIMEOUT: float = float(os.getenv("REACT_STEP_TIMEOUT", "120.0"))            # 單步推理最長秒數（給予充分 prefill 與多模態圖片時間）
REACT_MAX_OBSERVATION_LENGTH: int = int(os.getenv("REACT_MAX_OBSERVATION_LENGTH", "1200"))    # 單次 Observation 截斷上限
REACT_MAX_TRANSCRIPT_CHARS: int = int(os.getenv("REACT_MAX_TRANSCRIPT_CHARS", "6000"))      # 總對話历史截斷防爆上限

LLM_MAX_CONTEXT_TOKENS: int = int(os.getenv("LLM_MAX_CONTEXT_TOKENS", "8192"))
LLM_MAX_OUTPUT_TOKENS: int = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "2048"))

# GPU 吞吐量最佳化：Prompt 批次處理量（n_batch）
# 預設 512 過於保守，A4000 可大幅提升至 2048–4096 以加速 Prefill（降低首字延遲）
LLM_NUM_BATCH: int = int(os.getenv("LLM_NUM_BATCH", "2048"))

# Prompt Caching（KV 快取駐留時間）：讓模型與 System Prompt 維持在 VRAM 中
# 格式："60m"（60分鐘）、"24h"（24小時）、"0"（每次推論後立即卸載）
OLLAMA_KEEP_ALIVE: str = os.getenv("OLLAMA_KEEP_ALIVE", "60m")

# 調整生成參數以降低跳針（重複生成相同內容）機率
LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.85"))
LLM_PRESENCE_PENALTY: float = float(os.getenv("LLM_PRESENCE_PENALTY", "0.6"))
LLM_FREQUENCY_PENALTY: float = float(os.getenv("LLM_FREQUENCY_PENALTY", "0.6"))

# ---------------------------------------------------------------------------
# Priority Queue Weights
# ---------------------------------------------------------------------------
class Priority(IntEnum):
    """推論任務的語意優先序（數值越小越優先）。

    以語意層級取代散落各處的魔術數字；實際數值仍由下方 ``PRIORITY_*``
    設定（含 ``config_override.json``）決定，``priority_for()`` 負責映射。
    """

    INTERACTIVE = 1       # 使用者可感知的即時回覆（@Tag、天氣解析等）
    DRAW = 5              # 使用者主動圖片生成
    INTERJECT = 10        # 主動插話
    TOOL_BACKGROUND = 20  # 工具輔助／背景摘要
    MAINTENANCE = 50      # 氛圍萃取、記憶整理
    IDLE = 99             # 閒置爬蟲等最低優先


def _parse_priority_env(name: str, fallback: Priority) -> int:
    val = os.getenv(name)
    if val is None or not str(val).strip():
        return int(fallback.value)
    val = str(val).strip()
    if val.startswith("Priority."):
        val = val[len("Priority."):]
    try:
        return int(val)
    except ValueError:
        try:
            return int(Priority[val].value)
        except KeyError:
            return int(fallback.value)


PRIORITY_TAG_RESPONSE: int = _parse_priority_env("PRIORITY_TAG_RESPONSE", Priority.INTERACTIVE)       # 被 @Tag → 最高優先
PRIORITY_DRAW: int = _parse_priority_env("PRIORITY_DRAW", Priority.DRAW)               # 圖片生成（使用者主動指令 → 高於背景自動任務）
PRIORITY_INTERJECT: int = _parse_priority_env("PRIORITY_INTERJECT", Priority.INTERJECT)         # 主動插話（高於背景，低於 Tag 回應）
PRIORITY_VISION_REACTION: int = _parse_priority_env("PRIORITY_VISION_REACTION", Priority.IDLE)       # 主動視覺搭話（低於插話，閒置時執行）
PRIORITY_HEARTBEAT: int = _parse_priority_env("PRIORITY_HEARTBEAT", Priority.TOOL_BACKGROUND)         # 心跳排程任務（主動發言）
PRIORITY_VIBE_UPDATE: int = _parse_priority_env("PRIORITY_VIBE_UPDATE", Priority.MAINTENANCE)       # 背景氛圍萃取
PRIORITY_WEB_EXPLORER: int = _parse_priority_env("PRIORITY_WEB_EXPLORER", Priority.IDLE)      # 閒置爬蟲 → 最低優先


def priority_for(tier: Priority) -> int:
    """將語意優先序映射為當前設定值（支援執行期覆寫後的動態讀取）。

    Args:
        tier: 語意優先序層級。

    Returns:
        int: 對應的 ``PRIORITY_*`` 設定值；未知層級回退列舉原始數值。
    """
    mapping = {
        Priority.INTERACTIVE: PRIORITY_TAG_RESPONSE,
        Priority.DRAW: PRIORITY_DRAW,
        Priority.INTERJECT: PRIORITY_INTERJECT,
        Priority.TOOL_BACKGROUND: PRIORITY_HEARTBEAT,
        Priority.MAINTENANCE: PRIORITY_VIBE_UPDATE,
        Priority.IDLE: PRIORITY_WEB_EXPLORER,
    }
    return int(mapping.get(tier, int(tier)))

# ---------------------------------------------------------------------------
# Context / Memory
# ---------------------------------------------------------------------------
CONTEXT_HISTORY_LIMIT: int = int(os.getenv("CONTEXT_HISTORY_LIMIT", "30"))  # ✅ [修復] 由 10 提升至 30，大幅改善多輪對話脈絡連貫性
CONTEXT_TIME_GAP_SECONDS: int = int(os.getenv("CONTEXT_TIME_GAP_SECONDS", "900"))  # 15 分鐘斷層截斷

# 對話上下文濃縮：當 chat history 字數超過此值才觸發濃縮（避免密集長對話撐爆 prefill）
CONTEXT_COMPRESS_THRESHOLD: int = int(os.getenv("CONTEXT_COMPRESS_THRESHOLD", "2000"))
# 濃縮摘要結果的 60 秒記憶體快取，避免同頻道短時間重複濃縮
CONTEXT_COMPRESS_CACHE_TTL: float = float(os.getenv("CONTEXT_COMPRESS_CACHE_TTL", "60.0"))
# 硬性封頂：歷史區塊的 token 估算上限。即使濃縮失敗／不可用，
# 也絕不讓歷史超過此值，從根源封死「使用者每句 2-300 字」造成的快速膨脹
# （估算較保守，實際 token 數通常更低，屬安全上限）
CONTEXT_HISTORY_MAX_TOKENS: int = int(os.getenv("CONTEXT_HISTORY_MAX_TOKENS", "1500"))
# 水位節流：距離上次濃縮至少累積幾則「新訊息」才允許再次濃縮，
# 避免每則訊息都觸發一次 20 秒濃縮造成回壓與狂跳提示卡片
CONTEXT_COMPRESS_MIN_GAP_MESSAGES: int = int(os.getenv("CONTEXT_COMPRESS_MIN_GAP_MESSAGES", "8"))
# 濃縮後保留的最近原文則數（供當下精準接話），越多越連貫但越吃 token
CONTEXT_COMPRESS_KEEP_RECENT: int = int(os.getenv("CONTEXT_COMPRESS_KEEP_RECENT", "4"))

# ---------------------------------------------------------------------------
# Long-Term Memory（長期記憶系統參數）
# ---------------------------------------------------------------------------
# 每次注入 LLM Prompt 的 facts 數量（不含 rule 類，rule 類另行全數注入）
MEMORY_MAX_FACTS_IN_CONTEXT: int = int(os.getenv("MEMORY_MAX_FACTS_IN_CONTEXT", "8"))

# 每位使用者最多保留的 facts 數量（即時萃取與夜間整合共用上限）
MEMORY_MAX_FACTS_PER_USER: int = int(os.getenv("MEMORY_MAX_FACTS_PER_USER", "25"))

# importance >= 此值的 facts 永久保留，不受衰減與上限影響
MEMORY_PERMANENT_FACT_THRESHOLD: int = int(os.getenv("MEMORY_PERMANENT_FACT_THRESHOLD", "4"))

# 每累積幾句話觸發一次即時記憶萃取（每位使用者獨立計數）。拉高可減少
# memory-sync 在 RP 進行中偷取 GPU 的次數（萃取本身是完整 LLM 呼叫）。
MEM_DIGEST_THRESHOLD: int = int(os.getenv("MEM_DIGEST_THRESHOLD", "15"))

# 即時記憶萃取直接使用 PRIMARY_MODEL，確保萃取品質一致

# R14：長期記憶檢索（FTS5＋權重骨架）。權重會自動正規化，總和不必為 1。
MEMORY_RETRIEVAL_W_BM25: float = float(os.getenv("MEMORY_RETRIEVAL_W_BM25", "0.40"))
# R15：embedding cosine 相似度與關係親和度（0–1）。
MEMORY_RETRIEVAL_W_COS_SIM: float = float(os.getenv("MEMORY_RETRIEVAL_W_COS_SIM", "0.25"))
MEMORY_RETRIEVAL_W_RECENCY: float = float(os.getenv("MEMORY_RETRIEVAL_W_RECENCY", "0.10"))
MEMORY_RETRIEVAL_W_AFFINITY: float = float(os.getenv("MEMORY_RETRIEVAL_W_AFFINITY", "0.10"))
MEMORY_RETRIEVAL_W_IMPORTANCE: float = float(os.getenv("MEMORY_RETRIEVAL_W_IMPORTANCE", "0.10"))
MEMORY_RETRIEVAL_W_SOURCE_PRIORITY: float = float(os.getenv("MEMORY_RETRIEVAL_W_SOURCE_PRIORITY", "0.05"))
# 時效分數半衰期（天）與單次查詢最多解析的關鍵字數。
MEMORY_RETRIEVAL_RECENCY_HALFLIFE_DAYS: float = float(os.getenv("MEMORY_RETRIEVAL_RECENCY_HALFLIFE_DAYS", "30.0"))
MEMORY_RETRIEVAL_MAX_TERMS: int = int(os.getenv("MEMORY_RETRIEVAL_MAX_TERMS", "8"))

# R15：Ollama embeddings 向量化管線（未安裝／離線時自動降級純關鍵字）。
MEMORY_EMBEDDING_ENABLED: bool = os.getenv("MEMORY_EMBEDDING_ENABLED", "True").lower() in ("true", "1", "yes", "on")
MEMORY_EMBEDDING_MODEL: str = os.getenv("MEMORY_EMBEDDING_MODEL", "nomic-embed-text")
MEMORY_EMBEDDING_TIMEOUT: float = float(os.getenv("MEMORY_EMBEDDING_TIMEOUT", "8.0"))
MEMORY_EMBEDDING_MAX_RETRIES: int = int(os.getenv("MEMORY_EMBEDDING_MAX_RETRIES", "2"))
MEMORY_EMBEDDING_MAX_CONCURRENCY: int = int(os.getenv("MEMORY_EMBEDDING_MAX_CONCURRENCY", "2"))
MEMORY_EMBEDDING_BATCH_SIZE: int = int(os.getenv("MEMORY_EMBEDDING_BATCH_SIZE", "8"))
MEMORY_EMBEDDING_MIN_SIMILARITY: float = float(os.getenv("MEMORY_EMBEDDING_MIN_SIMILARITY", "0.25"))
MEMORY_EMBEDDING_QUERY_CACHE_SIZE: int = int(os.getenv("MEMORY_EMBEDDING_QUERY_CACHE_SIZE", "128"))
# Prompt 動態尾段最多注入幾條「依當前話題檢索」的記憶。
MEMORY_RETRIEVAL_TAIL_FACTS: int = int(os.getenv("MEMORY_RETRIEVAL_TAIL_FACTS", "4"))

VIBE_UPDATE_MESSAGE_THRESHOLD: int = int(os.getenv("VIBE_UPDATE_MESSAGE_THRESHOLD", "50"))
VIBE_TOP_N_TOPICS: int = int(os.getenv("VIBE_TOP_N_TOPICS", "3"))
VIBE_DECAY_INTERVAL_SECONDS: int = int(os.getenv("VIBE_DECAY_INTERVAL_SECONDS", "3600"))
VIBE_DECAY_AMOUNT: int = int(os.getenv("VIBE_DECAY_AMOUNT", "1"))

# 注入 prompt 的話題最低權重門檻（低於此值不列入）。
VIBE_MIN_WEIGHT_TO_INJECT: int = int(os.getenv("VIBE_MIN_WEIGHT_TO_INJECT", "5"))

# 話題再次出現時補滿的最高權重。
VIBE_REINFORCE_MAX_WEIGHT: int = int(os.getenv("VIBE_REINFORCE_MAX_WEIGHT", "10"))

# ---------------------------------------------------------------------------
# Typing Simulation

# ---------------------------------------------------------------------------
TYPING_BASE_DELAY: float = float(os.getenv("TYPING_BASE_DELAY", "1.0"))
TYPING_DELAY_PER_50_CHARS: float = float(os.getenv("TYPING_DELAY_PER_50_CHARS", "0.3"))

# ---------------------------------------------------------------------------
# Web Explorer
# ---------------------------------------------------------------------------
_yt_key_raw: str = os.getenv("YOUTUBE_API_KEY", "")
YOUTUBE_API_KEY: str = _yt_key_raw if _yt_key_raw not in ("", "your_youtube_api_key_here") else ""

OPENWEATHERMAP_API_KEY: str = os.getenv("OPENWEATHERMAP_API_KEY", "")

EXPLORER_INTERVAL_SECONDS: int = int(os.getenv("EXPLORER_INTERVAL_SECONDS", "1800"))
YOUTUBE_MAX_RESULTS: int = int(os.getenv("YOUTUBE_MAX_RESULTS", "5"))
EXPLORER_TARGET_CHANNEL_IDS: list[int] = _parse_int_list_env("EXPLORER_TARGET_CHANNEL_IDS", [])

# ---------------------------------------------------------------------------
# Interject Engine（主動插話引擎）
# ---------------------------------------------------------------------------

# 每累積幾則真人訊息，觸發一次「好不好玩」評分。
INTERJECT_EVAL_THRESHOLD: int = int(os.getenv("INTERJECT_EVAL_THRESHOLD", "10"))

# 插話後的冷卻秒數（冷卻期間不再觸發插話評估）。
INTERJECT_COOLDOWN_SECONDS: int = int(os.getenv("INTERJECT_COOLDOWN_SECONDS", "30"))

# 同一冷卻週期內最多允許連續插話幾次。
# 超過此次數後，必須等到下一個冷卻週期結束才能再插話。
INTERJECT_MAX_CONSECUTIVE: int = int(os.getenv("INTERJECT_MAX_CONSECUTIVE", "3"))

# 評分門檻（1–10）：LLM 給分 >= 此值才實際插話。
INTERJECT_SCORE_THRESHOLD: int = int(os.getenv("INTERJECT_SCORE_THRESHOLD", "7"))  # ✅ [修復] 由 10 降至 7，讓高興致時主動插話可真正觸發

# ---------------------------------------------------------------------------
# Rate Limit
# ---------------------------------------------------------------------------
RATE_LIMIT_MAX_REQUESTS: int = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "5"))
RATE_LIMIT_WINDOW_SECONDS: int = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "30"))
RATE_LIMIT_REPLY_MESSAGE: str = os.getenv("RATE_LIMIT_REPLY_MESSAGE", "名偵探現在很忙！{cooldown:.0f} 秒後再來！")

# ---------------------------------------------------------------------------
# Health Monitor
# ---------------------------------------------------------------------------
HEALTH_CHECK_INTERVAL_SECONDS: int = int(os.getenv("HEALTH_CHECK_INTERVAL_SECONDS", "300"))
HEALTH_ALERT_CHANNEL_ID: int = int(os.getenv("HEALTH_ALERT_CHANNEL_ID", "0"))  # 0 = 停用頻道推送
HEALTH_ALERT_USER_IDS: list[int] = _parse_int_list_env("HEALTH_ALERT_USER_IDS", [])  # 額外指定接收警報私訊的 Discord User ID
DISCORD_LOG_LEVEL: str = os.getenv("DISCORD_LOG_LEVEL", "WARNING")  # 可設為 DEBUG, INFO, WARNING, ERROR, CRITICAL


def get_alert_recipient_user_ids() -> list[int]:
    """取得應接收系統警報與錯誤日誌私訊的 Discord User ID 列表。

    自動合併 HEALTH_ALERT_USER_IDS、ADMIN_USER_IDS 與多種常見環境變數別名
    （如 HEALTH_ALERT_USER_ID, ADMIN_USER_ID, DC_USER_ID, DISCORD_USER_ID 等），
    並保留順序去除重複項。

    Returns:
        list[int]: 欲接收私訊通知的 Discord User ID 清單。
    """
    candidates: list[int] = []
    raw_lists = (
        globals().get("HEALTH_ALERT_USER_IDS", []),
        globals().get("ADMIN_USER_IDS", []),
    )
    for rlist in raw_lists:
        if isinstance(rlist, (list, tuple, set)):
            for item in rlist:
                if isinstance(item, int):
                    candidates.append(item)
                elif isinstance(item, str) and item.strip().isdigit():
                    candidates.append(int(item.strip()))
        elif isinstance(rlist, int):
            candidates.append(rlist)
        elif isinstance(rlist, str) and rlist.strip().isdigit():
            candidates.append(int(rlist.strip()))

    for env_key in (
        "HEALTH_ALERT_USER_ID",
        "ADMIN_USER_ID",
        "DC_USER_ID",
        "DISCORD_USER_ID",
        "ALERT_USER_ID",
        "ALERT_USER_IDS",
    ):
        candidates.extend(_parse_int_list_env(env_key, []))

    uids: list[int] = []
    for uid in candidates:
        if isinstance(uid, int) and uid > 0 and uid not in uids:
            uids.append(uid)
    return uids


# --- 引擎效能看門狗（重啟提醒）---
# 監控每則回應的真實 tok/s，偵測「跑久變慢、需重啟」的引擎衰退。
# 觀察窗口內樣本數達 SAMPLE_MIN 且平均 tok/s < THRESHOLD，且連續兩窗口皆慢，
# 才對 HEALTH_ALERT_CHANNEL_ID 發一則提醒（並受限於 COOLDOWN）。
PERF_WATCH_ENABLED: bool = os.getenv("PERF_WATCH_ENABLED", "True").lower() in ("true", "1", "yes")
PERF_WATCH_TOK_S_THRESHOLD: float = float(os.getenv("PERF_WATCH_TOK_S_THRESHOLD", "6.0"))  # 低於此視為衰退
PERF_WATCH_WINDOW_SECONDS: int = int(os.getenv("PERF_WATCH_WINDOW_SECONDS", "900"))  # 觀察窗口 15 分鐘
PERF_WATCH_MIN_SAMPLES: int = int(os.getenv("PERF_WATCH_MIN_SAMPLES", "5"))  # 窗口內至少幾則才可信
PERF_WATCH_CONSECUTIVE_WINDOWS: int = int(os.getenv("PERF_WATCH_CONSECUTIVE_WINDOWS", "2"))  # 需連續慢幾窗口
PERF_WATCH_COOLDOWN_SECONDS: int = int(os.getenv("PERF_WATCH_COOLDOWN_SECONDS", "21600"))  # 兩則提醒間隔 6 小時

# ---------------------------------------------------------------------------
# Voice System
# ---------------------------------------------------------------------------
VOICE_STT_MODEL: str = os.getenv("VOICE_STT_MODEL", str(BASE_DIR / "models" / "nemotron-3.5"))
VOICE_STT_DEVICE: str = os.getenv("VOICE_STT_DEVICE", "cpu")
VOICE_STT_COMPUTE_TYPE: str = os.getenv("VOICE_STT_COMPUTE_TYPE", "float32")
VOICE_STT_LANGUAGE: str = os.getenv("VOICE_STT_LANGUAGE", "zh")
VOICE_TTS_LANGUAGE: str = os.getenv("VOICE_TTS_LANGUAGE", "zh-cn")
VOICE_TTS_SPEAKER_WAV: str = os.getenv("VOICE_TTS_SPEAKER_WAV", str(BASE_DIR / "file" / "shiry_voice_sample.wav"))
VOICE_TTS_DEVICE: str = os.getenv("VOICE_TTS_DEVICE", "cpu")
VOICE_VAD_THRESHOLD: float = float(os.getenv("VOICE_VAD_THRESHOLD", "0.5"))
VOICE_VAD_MIN_SILENCE_MS: int = int(os.getenv("VOICE_VAD_MIN_SILENCE_MS", "500"))
VOICE_SILENCE_TIMEOUT_SECONDS: float = float(os.getenv("VOICE_SILENCE_TIMEOUT_SECONDS", "1.5"))
VOICE_MAX_RECORD_SECONDS: int = int(os.getenv("VOICE_MAX_RECORD_SECONDS", "30"))

# ---------------------------------------------------------------------------
# Pollinations.ai Cloud Image Generation (Primary)
# ---------------------------------------------------------------------------
POLLINATIONS_API_URL: str = os.getenv("POLLINATIONS_API_URL", "https://image.pollinations.ai/prompt")
POLLINATIONS_TIMEOUT_SECONDS: int = int(os.getenv("POLLINATIONS_TIMEOUT_SECONDS", "30"))
# 可選值: "flux-anime" (推薦,二次元風格), "flux-anime2", "z-image-turbo" (通用寫實)
POLLINATIONS_MODEL: str = os.getenv("POLLINATIONS_MODEL", "flux-anime")

# ---------------------------------------------------------------------------
# Hugging Face Inference API (Primary Anime Image Generation)
# ---------------------------------------------------------------------------
# 是否使用線上模型 (Gemini Nano Banana / HF / Pollinations) 生成圖片。預設 True
USE_ONLINE_IMAGE_MODEL: bool = os.getenv("USE_ONLINE_IMAGE_MODEL", "True").lower() in ("true", "1", "yes")

# ---------------------------------------------------------------------------
# Google Gemini Image Generation (Nano Banana - Primary Online)
# ---------------------------------------------------------------------------
GEMINI_IMAGE_MODEL: str = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
GEMINI_IMAGE_TIMEOUT_SECONDS: int = int(os.getenv("GEMINI_IMAGE_TIMEOUT_SECONDS", "30"))

# 需要 HF Token：https://huggingface.co/settings/tokens
# 申請 Free 帳號即有免費 Serverless Inference 額度
# Animagine-XL-3.1 是專為動漫訓練的 SDXL 模型，對 Blue Archive 等遊戲角色有良好識別
HF_TOKEN: str = os.getenv("HF_TOKEN", "")
HF_IMAGE_MODEL: str = os.getenv("HF_IMAGE_MODEL", "cagliostrolab/animagine-xl-3.1")
HF_IMAGE_TIMEOUT_SECONDS: int = int(os.getenv("HF_IMAGE_TIMEOUT_SECONDS", "120"))  # SDXL 生成較慢，給足夠時間

# ---------------------------------------------------------------------------
# Local SD Image Generation (Fallback)
# ---------------------------------------------------------------------------
SD_WEBUI_URL: str = os.getenv("SD_WEBUI_URL", "http://localhost:7860")
SD_DEFAULT_WIDTH: int = int(os.getenv("SD_DEFAULT_WIDTH", "768"))
SD_DEFAULT_HEIGHT: int = int(os.getenv("SD_DEFAULT_HEIGHT", "1024"))
SD_DEFAULT_STEPS: int = int(os.getenv("SD_DEFAULT_STEPS", "20"))
SD_DEFAULT_CFG_SCALE: float = float(os.getenv("SD_DEFAULT_CFG_SCALE", "7.0"))
SD_DEFAULT_SAMPLER: str = os.getenv("SD_DEFAULT_SAMPLER", "DPM++ 2M Karras")
SD_TIMEOUT_SECONDS: int = int(os.getenv("SD_TIMEOUT_SECONDS", "120"))
SD_NEGATIVE_PROMPT_BASE: str = os.getenv(
    "SD_NEGATIVE_PROMPT_BASE",
    "lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, fewer digits, cropped, worst quality, low quality, normal quality, jpeg artifacts, signature, watermark, username, blurry, artist name",
)

# ---------------------------------------------------------------------------
# Micro Chain-of-Thought (Micro-CoT) & Debug Detail Mode
# ---------------------------------------------------------------------------
# 啟用後，雪莉在每次 CHAT 回覆前會先輸出 <thought>...</thought> 內心推理，
# 再給出正式回覆。thought 區塊對用戶不可見，會被存進 data/deep_thinking.json。
# 若發現模型因為思考步驟太長而變慢，可暫時設為 False 關閉。
MICRO_COT_ENABLED: bool = os.getenv("MICRO_COT_ENABLED", "True").lower() in ("true", "1", "yes")

# Discord 對話卡片式偵錯模式（顯示 Intent、CoT 思考過程、耗時毫秒、模型名稱等，與 WebUI 對齊）
DEBUG_DETAIL: bool = os.getenv("DEBUG_DETAIL", "False").lower() in ("true", "1", "yes")

# 即時動態編輯思考推演流程（以 message.edit 原地背景更新思考與執行進度）
LIVE_THINKING_EDIT: bool = os.getenv("LIVE_THINKING_EDIT", "True").lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# Daily Chat Log + Nightly Memory Digest
# ---------------------------------------------------------------------------
CHAT_LOG_DIR: Path = DATA_DIR / "chat_logs"          # 每日聊天記錄 JSONL 目錄
NIGHTLY_DIGEST_HOUR: int = int(os.getenv("NIGHTLY_DIGEST_HOUR", "4"))                          # 深夜整合觸發小時（台灣時間 UTC+8）
NIGHTLY_DIGEST_MINUTE: int = int(os.getenv("NIGHTLY_DIGEST_MINUTE", "30"))                        # 深夜整合觸發分鐘（台灣時間 UTC+8，預設 04:30）
NIGHTLY_MAX_MSGS_PER_USER: int = int(os.getenv("NIGHTLY_MAX_MSGS_PER_USER", "200"))                  # 每位使用者最多送入 LLM 的訊息數

# ---------------------------------------------------------------------------
# Web UI Server
# ---------------------------------------------------------------------------
WEB_UI_HOST: str = os.getenv("WEB_UI_HOST", "127.0.0.1")
WEB_UI_PORT: int = int(os.getenv("WEB_UI_PORT", "8080"))
WEB_UI_HTTPS: bool = True
WEB_UI_LIVE2D_ENABLED: bool = os.getenv("WEB_UI_LIVE2D_ENABLED", "True").lower() in ("true", "1", "yes", "on")
WEB_UI_SSL_CERT: str | None = os.getenv("WEB_UI_SSL_CERT", None)
WEB_UI_SSL_KEY: str | None = os.getenv("WEB_UI_SSL_KEY", None)
WEB_UI_RADMIN_IP: str | None = os.getenv("WEB_UI_RADMIN_IP", "26.108.83.196")  # Radmin VPN IP for SSL SAN
WEB_UI_HTTP_TIMEOUT: float = 5.0

# ---------------------------------------------------------------------------
# Music & Voice AI Models
# ---------------------------------------------------------------------------
YOUTUBE_COOKIES_PATH: Path = DATA_DIR / "cookies.txt"
MUSIC_PLAYLIST_MAX_ITEMS: int = int(os.getenv("MUSIC_PLAYLIST_MAX_ITEMS", "100"))
MUSIC_FFMPEG_BEFORE_OPTIONS: str = os.getenv(
    "MUSIC_FFMPEG_BEFORE_OPTIONS",
    "-reconnect 1 -reconnect_streamed 1 -reconnect_at_eof 1 -reconnect_on_network_error 1 -reconnect_on_http_error 4xx,5xx -reconnect_delay_max 5 -loglevel error",
)
MUSIC_FFMPEG_OPTIONS: str = os.getenv("MUSIC_FFMPEG_OPTIONS", '-vn -af "loudnorm=I=-9:LRA=11:TP=-1.0"')
MUSIC_DEFAULT_VOLUME: float = float(os.getenv("MUSIC_DEFAULT_VOLUME", "1.2"))
MUSIC_QUEUE_VIEW_TIMEOUT: int = int(os.getenv("MUSIC_QUEUE_VIEW_TIMEOUT", "180"))

VOICE_STT_REPO_ID: str = os.getenv("VOICE_STT_REPO_ID", "nvidia/nemotron-3.5-asr-streaming-0.6b")
VOICE_TTS_MODEL_NAME: str = os.getenv("VOICE_TTS_MODEL_NAME", "tts_models/multilingual/multi-dataset/xtts_v2")
VOICE_VAD_REPO_OR_DIR: str = os.getenv("VOICE_VAD_REPO_OR_DIR", "snakers4/silero-vad")
VOICE_VAD_MODEL_NAME: str = os.getenv("VOICE_VAD_MODEL_NAME", "silero_vad")

# ---------------------------------------------------------------------------
# Tag Dictionary & World Memory Paths
# ---------------------------------------------------------------------------
PIXIV_TAG_PATH: Path = FILE_DIR / "pixiv_tag.json"
TAG_METADATA_PATH: Path = FILE_DIR / "tag_metadata.json"
PIXIV_TAG_MIN_POST_COUNT: int = int(os.getenv("PIXIV_TAG_MIN_POST_COUNT", "100"))
PIXIV_TAG_MAX_PAGES: int = int(os.getenv("PIXIV_TAG_MAX_PAGES", "60"))
PIXIV_TAG_REQUEST_DELAY: float = float(os.getenv("PIXIV_TAG_REQUEST_DELAY", "0.5"))
DANBOORU_API_URL: str = os.getenv("DANBOORU_API_URL", "https://danbooru.donmai.us/tags.json")

WORLD_LOG_DIR: Path = DATA_DIR / "world_history"
WORLD_MEMORY_PATH: Path = DATA_DIR / "world_memory.json"
WORLD_MEMORY_SNIPPET_LIMIT: int = int(os.getenv("WORLD_MEMORY_SNIPPET_LIMIT", "6"))  # 注入 LLM prompt 的世界記憶摘要條數上限（預算 ~300 tokens）

# 世界記憶長期歸檔（每週/每月的 Markdown 永久儲存，只存不注入）
# 週檔存在 {WORLD_ARCHIVE_DIR}/weekly/{週一日期}_{ISO週}.md
# 月檔存在 {WORLD_ARCHIVE_DIR}/monthly/{YYYY-MM}.md
WORLD_ARCHIVE_DIR: Path = DATA_DIR / "memory" / "world"
# 哪一天之夜把「整個週」歸檔：0=週一 … 6=週日（=6 表示週日結束該週，接著週日深夜歸檔整週）
WORLD_ARCHIVE_TRIGGER_WEEKDAY: int = int(os.getenv("WORLD_ARCHIVE_TRIGGER_WEEKDAY", "6"))
# 週檔保留週數（超過自動清理；月檔視為永久庫不清理）
WORLD_ARCHIVE_KEEP_WEEKS: int = int(os.getenv("WORLD_ARCHIVE_KEEP_WEEKS", "52"))

# 每日世界事件提煉送入 LLM 的最大取樣訊息數（深夜非同步，可放心取多）
NIGHTLY_WORLD_SAMPLE_COUNT: int = int(os.getenv("NIGHTLY_WORLD_SAMPLE_COUNT", "200"))
BOT_LOG_PATH: Path = DATA_DIR / "bot.log"
WEB_UI_LOG_PATH: Path = DATA_DIR / "web_ui.log"
# 觀測寫入佇列上限：暴量時有界丟棄並計數，避免記憶體無上限成長。
OBSERVABILITY_WRITE_QUEUE_MAXSIZE: int = int(os.getenv("OBSERVABILITY_WRITE_QUEUE_MAXSIZE", "10000"))


# ---------------------------------------------------------------------------
# Memory System & Digest Thresholds
# ---------------------------------------------------------------------------
NIGHTLY_MIN_MSGS_THRESHOLD: int = int(os.getenv("NIGHTLY_MIN_MSGS_THRESHOLD", "5"))

# ---------------------------------------------------------------------------
# BotBrain Queue & Retries
# ---------------------------------------------------------------------------
BOT_BRAIN_MAX_RETRIES: int = int(os.getenv("BOT_BRAIN_MAX_RETRIES", "3"))
BOT_BRAIN_RETRY_DELAY: float = float(os.getenv("BOT_BRAIN_RETRY_DELAY", "2.0"))
# 逾時重試上限獨立於一般重試（避免本機 GPU 被反覆疊加佔用）；
# 退避上限避免長時間 sleep 佔住 worker。
BOT_BRAIN_MAX_TIMEOUT_RETRIES: int = int(os.getenv("BOT_BRAIN_MAX_TIMEOUT_RETRIES", "1"))
BOT_BRAIN_RETRY_MAX_DELAY: float = float(os.getenv("BOT_BRAIN_RETRY_MAX_DELAY", "30.0"))
# R10 任務治理：雲端雙層併發閘門 + Worker 韌性。
# 全域上限避免一次打出過多雲端請求；per-user 上限避免單一使用者壟斷配額。
BOT_BRAIN_CLOUD_MAX_CONCURRENCY: int = int(os.getenv("BOT_BRAIN_CLOUD_MAX_CONCURRENCY", "4"))
BOT_BRAIN_CLOUD_PER_USER_CONCURRENCY: int = int(os.getenv("BOT_BRAIN_CLOUD_PER_USER_CONCURRENCY", "1"))
# Worker 連續失敗上限（達到後標記降級）與指數退避上限（秒）。
BOT_BRAIN_WORKER_MAX_FAILURES: int = int(os.getenv("BOT_BRAIN_WORKER_MAX_FAILURES", "5"))
BOT_BRAIN_WORKER_BACKOFF_CAP: float = float(os.getenv("BOT_BRAIN_WORKER_BACKOFF_CAP", "60.0"))

# ---------------------------------------------------------------------------
# Heartbeat Active System
# ---------------------------------------------------------------------------
HEARTBEAT_CHECK_INTERVAL_MINUTES: int = int(os.getenv("HEARTBEAT_CHECK_INTERVAL_MINUTES", "30"))
HEARTBEAT_CONDITION_START_HOUR: int = int(os.getenv("HEARTBEAT_CONDITION_START_HOUR", "9"))
HEARTBEAT_CONDITION_END_HOUR: int = int(os.getenv("HEARTBEAT_CONDITION_END_HOUR", "22"))
HEARTBEAT_CONDITION_MIN_GAP_SECONDS: int = int(os.getenv("HEARTBEAT_CONDITION_MIN_GAP_SECONDS", "14400"))
# 每輪心跳掃描最多處理的任務數；避免 heartbeat.md 過長時單輪卡住或一次灌爆佇列。
HEARTBEAT_MAX_TASKS_PER_ROUND: int = int(os.getenv("HEARTBEAT_MAX_TASKS_PER_ROUND", "10"))

# ---------------------------------------------------------------------------
# External APIs, Search & User-Agent
# ---------------------------------------------------------------------------
OPENWEATHERMAP_API_URL: str = os.getenv("OPENWEATHERMAP_API_URL", "https://api.openweathermap.org/data/2.5/weather")
SAFEBOORU_API_URL: str = os.getenv("SAFEBOORU_API_URL", "https://safebooru.org/index.php?page=dapi&s=post")
SAFEBOORU_IMAGE_BASE_URL: str = os.getenv("SAFEBOORU_IMAGE_BASE_URL", "https://safebooru.org/images/")
DUCKDUCKGO_SEARCH_URL: str = os.getenv("DUCKDUCKGO_SEARCH_URL", "https://html.duckduckgo.com/html/?q=")
DEFAULT_HTTP_TIMEOUT: int = int(os.getenv("DEFAULT_HTTP_TIMEOUT", "10"))
YOUTUBE_SEARCH_API_URL: str = os.getenv("YOUTUBE_SEARCH_API_URL", "https://www.googleapis.com/youtube/v3/search")
YOUTUBE_RSS_FEED_URL: str = os.getenv("YOUTUBE_RSS_FEED_URL", "https://www.youtube.com/feeds/videos.xml?channel_id=")
YOUTUBE_RSS_NS: str = os.getenv("YOUTUBE_RSS_NS", "http://www.w3.org/2005/Atom")
DEFAULT_USER_AGENT: str = os.getenv(
    "DEFAULT_USER_AGENT",
    "DiscordBot/1.0 (https://github.com/placeholder; contact@placeholder.com)",
)

# ---------------------------------------------------------------------------
# Module Timeouts
# ---------------------------------------------------------------------------
WEATHER_EXTRACT_TIMEOUT: float = float(os.getenv("WEATHER_EXTRACT_TIMEOUT", "10.0"))
WEATHER_REPLY_TIMEOUT: float = float(os.getenv("WEATHER_REPLY_TIMEOUT", "90.0"))
SAFEBOORU_HTTP_TIMEOUT: int = int(os.getenv("SAFEBOORU_HTTP_TIMEOUT", "12"))
VISION_ANALYSIS_TIMEOUT: int = int(os.getenv("VISION_ANALYSIS_TIMEOUT", "120"))
STOCK_EXTRACT_TIMEOUT: float = float(os.getenv("STOCK_EXTRACT_TIMEOUT", "10.0"))
STOCK_REPLY_TIMEOUT: float = float(os.getenv("STOCK_REPLY_TIMEOUT", "90.0"))
# 全域 yfinance／matplotlib 抓取併發上限；避免多個頻道同時查詢時塞滿執行器。
STOCK_MAX_CONCURRENT_FETCHES: int = int(os.getenv("STOCK_MAX_CONCURRENT_FETCHES", "3"))
SCHEDULE_PARSE_TIMEOUT: float = float(os.getenv("SCHEDULE_PARSE_TIMEOUT", "20.0"))
DRAW_RAW_TIMEOUT: float = float(os.getenv("DRAW_RAW_TIMEOUT", "30.0"))
DRAW_EXPAND_TIMEOUT: float = float(os.getenv("DRAW_EXPAND_TIMEOUT", "10.0"))
INTERJECT_SCORE_TIMEOUT: float = float(os.getenv("INTERJECT_SCORE_TIMEOUT", "30.0"))
INTERJECT_REPLY_TIMEOUT: float = float(os.getenv("INTERJECT_REPLY_TIMEOUT", "60.0"))
YOUTUBE_EXPLORER_TIMEOUT: float = float(os.getenv("YOUTUBE_EXPLORER_TIMEOUT", "20.0"))
NIGHTLY_DIGEST_TIMEOUT: float = float(os.getenv("NIGHTLY_DIGEST_TIMEOUT", "120.0"))
HEALTH_CHECK_TIMEOUT: float = float(os.getenv("HEALTH_CHECK_TIMEOUT", "5.0"))
DISCORD_UI_VIEW_TIMEOUT: int = int(os.getenv("DISCORD_UI_VIEW_TIMEOUT", "300"))
ROAST_REPLY_TIMEOUT: float = float(os.getenv("ROAST_REPLY_TIMEOUT", "30.0"))


# ---------------------------------------------------------------------------
# Deep Behavior Tunables (editable via Web UI; module-config values are read at
# import time, so most of these take effect after a restart)
# ---------------------------------------------------------------------------
SPARK_DEFAULT_COOLDOWN_SECONDS: int = int(os.getenv("SPARK_DEFAULT_COOLDOWN_SECONDS", "3600"))
SPARK_CHECK_INTERVAL_MINUTES: int = int(os.getenv("SPARK_CHECK_INTERVAL_MINUTES", "1"))
SPARK_MAX_TRIGGERS_PER_USER: int = int(os.getenv("SPARK_MAX_TRIGGERS_PER_USER", "10"))
SPARK_MAX_PUSHES_PER_DAY: int = int(os.getenv("SPARK_MAX_PUSHES_PER_DAY", "30"))
SPARK_MAX_CONCURRENT_ACTIONS: int = int(os.getenv("SPARK_MAX_CONCURRENT_ACTIONS", "4"))
SPARK_MAX_CONCURRENT_CHECKS: int = int(os.getenv("SPARK_MAX_CONCURRENT_CHECKS", "8"))
SPARK_ACTION_TIMEOUT_SECONDS: float = float(os.getenv("SPARK_ACTION_TIMEOUT_SECONDS", "300.0"))
# 單一 checker 的網路／外部 API 上限；避免單一卡死拖垮整輪巡檢。
SPARK_CHECKER_TIMEOUT_SECONDS: float = float(os.getenv("SPARK_CHECKER_TIMEOUT_SECONDS", "45.0"))
# 單輪巡檢的整體 deadline；逾時即取消本輪剩餘檢查並記錄摘要。
SPARK_CHECK_CYCLE_TIMEOUT_SECONDS: float = float(os.getenv("SPARK_CHECK_CYCLE_TIMEOUT_SECONDS", "60.0"))
# daily_time 錯過目標分鐘後的補跑窗（分鐘）；0 表示關閉補跑。
SPARK_DAILY_CATCHUP_MINUTES: int = int(os.getenv("SPARK_DAILY_CATCHUP_MINUTES", "120"))
# R17：多實例 leader 租約（DB 租約；只有 leader 執行巡檢與推播）。
SPARK_LEADER_LEASE_ENABLED: bool = os.getenv("SPARK_LEADER_LEASE_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
SPARK_LEADER_LEASE_TTL_SECONDS: float = float(os.getenv("SPARK_LEADER_LEASE_TTL_SECONDS", "90.0"))
SPARK_LEADER_HEARTBEAT_SECONDS: float = float(os.getenv("SPARK_LEADER_HEARTBEAT_SECONDS", "30.0"))
# R19：主動關懷引擎（預設 opt-out；每人每週 2 條、全域每日 20 條）。
CARE_DB_PATH: Path = Path(os.getenv("SHERRY_CARE_DB", str(DATA_DIR / "care.db")))
CARE_WEEKLY_LIMIT_PER_USER: int = int(os.getenv("CARE_WEEKLY_LIMIT_PER_USER", "2"))
CARE_DAILY_LIMIT_GLOBAL: int = int(os.getenv("CARE_DAILY_LIMIT_GLOBAL", "20"))
CARE_MIN_AFFINITY: float = float(os.getenv("CARE_MIN_AFFINITY", "60.0"))
CARE_INACTIVE_DAYS: float = float(os.getenv("CARE_INACTIVE_DAYS", "14.0"))
CARE_ADAPTIVE_BUDGET_ENABLED: bool = os.getenv("CARE_ADAPTIVE_BUDGET_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
CARE_FEEDBACK_WINDOW_DAYS: float = float(os.getenv("CARE_FEEDBACK_WINDOW_DAYS", "30"))
CARE_MIN_FEEDBACK_SAMPLES: int = int(os.getenv("CARE_MIN_FEEDBACK_SAMPLES", "3"))
CARE_SILENCE_DOWNGRADE_THRESHOLD: float = float(os.getenv("CARE_SILENCE_DOWNGRADE_THRESHOLD", "0.6"))
CARE_SILENCE_STOP_THRESHOLD: float = float(os.getenv("CARE_SILENCE_STOP_THRESHOLD", "0.85"))
CARE_SILENCE_SETTLE_HOURS: float = float(os.getenv("CARE_SILENCE_SETTLE_HOURS", "48"))
# 雪莉專屬自我記憶與網活仔衝浪設定
SELF_MEMORY_DB_PATH: Path = Path(os.getenv("SHERRY_SELF_MEMORY_DB", str(DATA_DIR / "self_memory.db")))
# 行為記憶（Self Action Memory）：讓雪莉記得「自己剛剛做過什麼」（播放、搜尋、繪圖…）。
# 平常閒聊不注入（0 Token），僅在使用者主動詢問「你剛剛／你記得」時檢索 3~5 筆。
SELF_ACTION_MEMORY_ENABLED: bool = os.getenv("SELF_ACTION_MEMORY_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
SELF_ACTION_RETENTION_DAYS: int = int(os.getenv("SELF_ACTION_RETENTION_DAYS", "14"))
SELF_ACTION_SNIPPET_LIMIT: int = int(os.getenv("SELF_ACTION_SNIPPET_LIMIT", "5"))
SELF_ACTION_SNIPPET_MAX_CHARS: int = int(os.getenv("SELF_ACTION_SNIPPET_MAX_CHARS", "400"))
SELF_ACTION_LIVE_STATE_ENABLED: bool = os.getenv("SELF_ACTION_LIVE_STATE_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
CARE_SURFER_PORT: int = int(os.getenv("CARE_SURFER_PORT", "8765"))
CARE_SURFER_ENABLED: bool = os.getenv("CARE_SURFER_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
CARE_SURFER_CHANNEL_ID: int = int(os.getenv("CARE_SURFER_CHANNEL_ID", "0"))
CARE_SURFER_INTERVAL_MINUTES: int = int(os.getenv("CARE_SURFER_INTERVAL_MINUTES", "120"))
# === 統一互動（One Brain, Two Lanes） ===
# 預設關閉 gateway，避免未經 canary 就改變 @雪莉 路由；開啟後由 TurnGateway 接管 sandbox ambient + mention。
INTERACTION_GATEWAY_ENABLED: bool = os.getenv("INTERACTION_GATEWAY_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
INTERACTION_MENTION_SCOPE: str = os.getenv("INTERACTION_MENTION_SCOPE", "sandbox").strip().lower()
INTERACTION_NAME_MENTION_ENABLED: bool = os.getenv("INTERACTION_NAME_MENTION_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
INTERACTION_PREEMPT_AMBIENT: bool = os.getenv("INTERACTION_PREEMPT_AMBIENT", "1").strip().lower() in {"1", "true", "yes", "on"}
# Cyber v2：off / shadow / live。預設 shadow 只觀察，不發送。
CYBER_ENGINE_MODE: str = os.getenv("CYBER_ENGINE_MODE", "shadow").strip().lower()
CYBER_V2_OWNS_MENTIONS: bool = os.getenv("CYBER_V2_OWNS_MENTIONS", "0").strip().lower() in {"1", "true", "yes", "on"}
CYBER_V2_TOOLS_ENABLED: bool = os.getenv("CYBER_V2_TOOLS_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
# 記憶共用：由 MemoryContextService 統一組裝；persona 不再重複注入自憶。
MEMORY_CONTEXT_SERVICE_ENABLED: bool = os.getenv("MEMORY_CONTEXT_SERVICE_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
MEMORY_CONTEXT_OWNS_PERSONA_MEMORY: bool = os.getenv("MEMORY_CONTEXT_OWNS_PERSONA_MEMORY", "1").strip().lower() in {"1", "true", "yes", "on"}
MEMORY_OBSERVATORY_ENABLED: bool = os.getenv("MEMORY_OBSERVATORY_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
# === 賽博群友 Social Core：沙盒群像（預設全部關閉） ===
SOCIAL_SANDBOX_ENABLED: bool = os.getenv("SOCIAL_SANDBOX_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
SOCIAL_SANDBOX_COMPAT_MODE: bool = os.getenv("SOCIAL_SANDBOX_COMPAT_MODE", "0").strip().lower() in {"1", "true", "yes", "on"}
SOCIAL_SANDBOX_MAX_NPCS: int = int(os.getenv("SOCIAL_SANDBOX_MAX_NPCS", "3"))
SOCIAL_SCENE_MAX_TURNS: int = int(os.getenv("SOCIAL_SCENE_MAX_TURNS", "12"))
SOCIAL_SCENE_IDLE_SECONDS: int = int(os.getenv("SOCIAL_SCENE_IDLE_SECONDS", "600"))
SOCIAL_NPC_MAX_CONSECUTIVE: int = int(os.getenv("SOCIAL_NPC_MAX_CONSECUTIVE", "3"))
SOCIAL_NPC_MAX_PER_ROUND: int = int(os.getenv("SOCIAL_NPC_MAX_PER_ROUND", "2"))
SOCIAL_NPC_HOURLY_LIMIT: int = int(os.getenv("SOCIAL_NPC_HOURLY_LIMIT", "20"))
SOCIAL_NPC_DAILY_LIMIT: int = int(os.getenv("SOCIAL_NPC_DAILY_LIMIT", "120"))
SOCIAL_NPC_MIN_GAP_SECONDS: int = int(os.getenv("SOCIAL_NPC_MIN_GAP_SECONDS", "45"))
SOCIAL_NPC_DELAY_MIN: float = float(os.getenv("SOCIAL_NPC_DELAY_MIN", "2.0"))
SOCIAL_NPC_DELAY_MAX: float = float(os.getenv("SOCIAL_NPC_DELAY_MAX", "8.0"))
SOCIAL_WEBHOOK_AVATAR_MAX_BYTES: int = int(os.getenv("SOCIAL_WEBHOOK_AVATAR_MAX_BYTES", "262144"))
EMOTION_ENABLED: bool = os.getenv("EMOTION_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
EMOTION_HALF_LIFE_HOURS: float = float(os.getenv("EMOTION_HALF_LIFE_HOURS", "4.0"))
PERSONA_GUARD_ENABLED: bool = os.getenv("PERSONA_GUARD_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
PERSONA_DRIFT_EVAL_PATH: Path = DATA_DIR / "eval" / "persona_drift_cases.jsonl"
VISION_REACTION_ENABLED: bool = os.getenv("VISION_REACTION_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
VISION_REACTION_COOLDOWN_SECONDS: float = float(os.getenv("VISION_REACTION_COOLDOWN_SECONDS", "60.0"))
VISION_REACTION_DISABLED_CHANNELS: list[int] = _parse_int_list_env(
    "VISION_REACTION_DISABLED_CHANNELS", []
)
VISION_MAX_IMAGES: int = int(os.getenv("VISION_MAX_IMAGES", "3"))
VISION_MAX_FILE_BYTES: int = int(os.getenv("VISION_MAX_FILE_BYTES", str(8 * 1024 * 1024)))
DEEP_SEARCH_DEFAULT_ROUNDS: int = int(os.getenv("DEEP_SEARCH_DEFAULT_ROUNDS", "4"))
DEEP_SEARCH_MIN_ROUNDS: int = int(os.getenv("DEEP_SEARCH_MIN_ROUNDS", "2"))
DEEP_SEARCH_MAX_ROUNDS: int = int(os.getenv("DEEP_SEARCH_MAX_ROUNDS", "8"))
DEEP_SEARCH_QUERIES_PER_ROUND: int = int(os.getenv("DEEP_SEARCH_QUERIES_PER_ROUND", "2"))
DEEP_SEARCH_MAX_RESULTS_PER_QUERY: int = int(os.getenv("DEEP_SEARCH_MAX_RESULTS_PER_QUERY", "4"))
DEEP_SEARCH_MAX_PAGES_PER_ROUND: int = int(os.getenv("DEEP_SEARCH_MAX_PAGES_PER_ROUND", "3"))
DEEP_SEARCH_MAX_CHARS_PER_PAGE: int = int(os.getenv("DEEP_SEARCH_MAX_CHARS_PER_PAGE", "4000"))
DND_MAX_HISTORY_ROUNDS: int = int(os.getenv("DND_MAX_HISTORY_ROUNDS", "6"))
DRAW_TOOL_MAX_PROMPT_LENGTH: int = int(os.getenv("DRAW_TOOL_MAX_PROMPT_LENGTH", "300"))
LOREBOOK_MAX_GENERAL_ENTRIES: int = int(os.getenv("LOREBOOK_MAX_GENERAL_ENTRIES", "3"))
PAINTER_MAX_PAINTING_TIMEOUT_SEC: float = float(os.getenv("PAINTER_MAX_PAINTING_TIMEOUT_SEC", "120.0"))
STOCK_TRADER_INITIAL_CAPITAL_USD: float = float(os.getenv("STOCK_TRADER_INITIAL_CAPITAL_USD", "10000.0"))
STOCK_TRADER_FEE_RATE: float = float(os.getenv("STOCK_TRADER_FEE_RATE", "0.001"))
STOCK_TRADER_MIN_SHARES: float = float(os.getenv("STOCK_TRADER_MIN_SHARES", "0.01"))

# 系統重啟後工作自癒與狀態接續總開關（預設 True）
STATE_RECOVERY_ENABLED: bool = os.getenv("STATE_RECOVERY_ENABLED", "True").strip().lower() in ("1", "true", "yes", "on")
STATE_RECOVERY_DB_PATH: Path = DATA_DIR / "sherry_recovery.db"


# ---------------------------------------------------------------------------
# Runtime Settings Override Mechanism
# ---------------------------------------------------------------------------
CONFIG_OVERRIDE_PATH: Path = DATA_DIR / "config_override.json"

if CONFIG_OVERRIDE_PATH.exists():
    try:
        import json
        with open(CONFIG_OVERRIDE_PATH, "r", encoding="utf-8") as f:
            overrides = json.load(f)
            # 若 .env 或環境變數有指定後端網址或權限/警報 ID，優先使用環境變數（自 overrides 中剔除）
            _ENV_PRIORITY_KEYS = (
                "OLLAMA_BASE_URL",
                "LMS_BASE_URL",
                "VLLM_BASE_URL",
                "SD_WEBUI_URL",
                "ADMIN_USER_IDS",
                "ADMIN_USER_ID",
                "ADMIN_ROLE_IDS",
                "HEALTH_ALERT_USER_IDS",
                "HEALTH_ALERT_USER_ID",
                "HEALTH_ALERT_CHANNEL_ID",
                "DISCORD_LOG_LEVEL",
                "DC_USER_ID",
                "DISCORD_USER_ID",
            )
            for _k in _ENV_PRIORITY_KEYS:
                if os.getenv(_k):
                    overrides.pop(_k, None)
            PATH_KEYS = {"FFMPEG", "VOICE_STT_MODEL", "VOICE_TTS_SPEAKER_WAV"}
            for k, v in overrides.items():
                if k in globals():
                    # 若為檔案或目錄路徑，但本機不存在該路徑，則維持動態 BASE_DIR 預設值
                    if k in PATH_KEYS and isinstance(v, str) and (":\\" in v or ":/" in v):
                        if not Path(v).exists():
                            logger.warning("config_override 中 %s 的路徑不存在 (%s)，已自動回退為預設專案路徑", k, v)
                            continue
                    if isinstance(v, str) and (v.strip().endswith(":") or v.strip() in ("ollama", "lms", "vllm", "openai", "deepseek", "gemini", "openrouter", "claude", "groq")):
                        globals()[k] = ""
                        os.environ[k] = ""
                    else:
                        globals()[k] = v
                        if isinstance(v, (str, int, float, bool)):
                            os.environ[k] = str(v)
    except Exception as e:
        logger.warning("Failed to load config_override.json: %s", e)

    DEFAULT_SOUL_PATH = SOUL_DIR / CURRENT_SOUL_NAME

# ---------------------------------------------------------------------------
# Web UI 管理員密碼策略（R2／報告 §5.3.7）
#   - WEB_UI_PASSWORD_HASH 優先於明文 WEB_UI_PASSWORD（格式：pbkdf2_sha256$iterations$salt$digest_b64）
#   - 未配置任何憑證時「不再自動生成、不再列印」：登入 fail-closed（一律拒絕）
#   - 一次性產生請執行：python -m web_ui.server --gen-password
# ---------------------------------------------------------------------------
_WEB_UI_PASSWORD_HASH_ALGO: str = "pbkdf2_sha256"
_WEB_UI_PASSWORD_HASH_ITERATIONS: int = 260_000

WEB_UI_PASSWORD_HASH: str = os.getenv("WEB_UI_PASSWORD_HASH", "").strip()
_configured_web_ui_pw: str = os.getenv("WEB_UI_PASSWORD", "").strip()
WEB_UI_PASSWORD: str
_WEB_UI_PASSWORD_GENERATED: bool
if _configured_web_ui_pw:
    WEB_UI_PASSWORD = _configured_web_ui_pw
    _WEB_UI_PASSWORD_GENERATED = False
elif WEB_UI_PASSWORD_HASH:
    WEB_UI_PASSWORD = ""
    _WEB_UI_PASSWORD_GENERATED = False
else:
    # Fail-closed：無憑證時保持空密碼，登入端一律拒絕（不再生成隨機密碼）
    WEB_UI_PASSWORD = ""
    _WEB_UI_PASSWORD_GENERATED = True


def hash_web_ui_password(password: str, *, iterations: int = _WEB_UI_PASSWORD_HASH_ITERATIONS) -> str:
    """使用 stdlib PBKDF2-SHA256 產生可注入環境變數的密碼雜湊字串。

    Args:
        password: 明文密碼（僅於此函式記憶體中短暫存在，不落地）。
        iterations: PBKDF2 迭代次數（預設 260000）。

    Returns:
        str: 格式為 "pbkdf2_sha256$<iterations>$<salt_hex>$<digest_b64>" 的雜湊字串。
    """
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", str(password).encode("utf-8"), salt.encode("ascii"), int(iterations)
    )
    return f"{_WEB_UI_PASSWORD_HASH_ALGO}${int(iterations)}${salt}${base64.b64encode(digest).decode('ascii')}"


def is_web_ui_password_configured() -> bool:
    """檢查是否已配置管理員登入憑證（WEB_UI_PASSWORD_HASH 或 WEB_UI_PASSWORD 任一）。"""
    hash_spec = str(globals().get("WEB_UI_PASSWORD_HASH", "") or "").strip()
    plain = str(globals().get("WEB_UI_PASSWORD", "") or "")
    return bool(hash_spec or plain)


def verify_web_ui_password(password: str) -> bool:
    """恆定時間驗證 Web UI 管理員密碼（hash 優先；未配置或格式無效一律拒絕）。"""
    candidate = str(password or "")
    hash_spec = str(globals().get("WEB_UI_PASSWORD_HASH", "") or "").strip()
    if hash_spec:
        try:
            algo, iterations_s, salt, digest_b64 = hash_spec.split("$", 3)
            if algo != _WEB_UI_PASSWORD_HASH_ALGO:
                raise ValueError(f"unsupported algorithm: {algo}")
            expected = base64.b64decode(digest_b64.encode("ascii"), validate=True)
            actual = hashlib.pbkdf2_hmac(
                "sha256", candidate.encode("utf-8"), salt.encode("ascii"), int(iterations_s)
            )
            return hmac.compare_digest(actual, expected)
        except Exception as exc:
            logger.warning("WEB_UI_PASSWORD_HASH 格式無效，登入一律拒絕: %s", exc)
            return False
    configured = str(globals().get("WEB_UI_PASSWORD", "") or "")
    if not configured:
        return False
    return hmac.compare_digest(configured.encode("utf-8"), candidate.encode("utf-8"))

WEB_UI_MAX_LOGIN_ATTEMPTS: int = 5  # 1 分鐘內登入失敗最大次數，超過即封鎖 IP
WEB_UI_LOCKOUT_MINUTES: int = 15   # 觸發封鎖後禁止登入的分鐘數
_configured_trusted_proxies: str = os.getenv("WEB_UI_TRUSTED_PROXIES", "").strip()
WEB_UI_TRUSTED_PROXIES: list[str] = [
    p.strip() for p in _configured_trusted_proxies.split(",") if p.strip()
] if _configured_trusted_proxies else ["127.0.0.1", "::1", "localhost", "testclient"]


# ---------------------------------------------------------------------------
# R2：設定寫入白名單與靈魂檔路徑邊界（報告 §5.1.2 A3 斷點）
# ---------------------------------------------------------------------------
_SOUL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_\-]+\.(md|txt)$")


def _is_runtime_safe_setting(key: str) -> bool:
    """委託 web_ui.settings_schema 判定鍵是否允許執行期寫入（schema 白名單＋runtime_safe）。"""
    try:
        from web_ui.settings_schema import is_runtime_safe_key
    except Exception as exc:
        logger.warning("無法載入 settings_schema 白名單（沿用寬容模式）: %s", exc)
        return True
    return bool(is_runtime_safe_key(key))


def _is_soul_path_within_soul_dir(path: Any) -> bool:
    """確認目標人設檔路徑（resolve 後）仍位於 SOUL_DIR 之內。"""
    try:
        return Path(path).resolve().is_relative_to(SOUL_DIR.resolve())
    except (OSError, ValueError, TypeError):
        return False


def _is_safe_soul_name(name: str) -> bool:
    """靈魂檔名白名單：僅允許 [A-Za-z0-9_-]+ 加上 .md/.txt，且 resolve 後不得逸出 SOUL_DIR。"""
    candidate = str(name or "").strip()
    if not _SOUL_NAME_PATTERN.match(candidate):
        return False
    return _is_soul_path_within_soul_dir(SOUL_DIR / candidate)


def update_setting(key: str, value: Any) -> bool:
    """更新全域設定值並持久化至 config_override.json（R2：schema 白名單反轉）。"""
    import json
    g = globals()
    key_str = str(key).strip()
    if not key_str.isidentifier() or not key_str.isupper() or key_str not in g:
        return False

    # R2（§5.1.2 A3）：白名單反轉——僅允許 SETTINGS_META 登錄（且 runtime_safe）之鍵，
    # 另保留 WEB_UI_PASSWORD 的管理路徑；憑證/路徑/人格原文等不再可任意覆寫。
    if not _is_runtime_safe_setting(key_str):
        return False

    # R2：靈魂檔名白名單——路徑穿越樣本直接拒絕，且不變更任何全域狀態
    if key_str == "CURRENT_SOUL_NAME" and not _is_safe_soul_name(str(value)):
        logger.warning("拒絕不合法的人設檔名（未通過白名單）: %r", value)
        return False

    expected_type = type(g[key_str])
    if expected_type is list:
        if isinstance(value, str):
            val_str = value.strip()
            if val_str.startswith("[") and val_str.endswith("]"):
                try:
                    parsed = json.loads(val_str)
                    if isinstance(parsed, list):
                        value = parsed
                    else:
                        value = [parsed]
                except Exception:
                    value = []
            elif val_str:
                value = [item.strip() for item in val_str.split(",") if item.strip()]
            else:
                value = []
        if not isinstance(value, (list, tuple)):
            return False
        # 使用者在 Web UI 清空清單欄位會送出空白字串，應視為未填寫項忽略，
        # 否則 int("") / float("") 會拋例外並讓整個 key 儲存失敗。
        value = [v for v in value if not (isinstance(v, str) and not v.strip())]
        base = g[key_str]
        if base:
            elem_type = type(base[0])
        else:
            ann = str(g.get("__annotations__", {}).get(key_str, ""))
            if "bool" in ann:
                elem_type = bool
            elif "float" in ann:
                elem_type = float
            elif "int" in ann:
                elem_type = int
            else:
                elem_type = str
        try:
            if elem_type is bool:
                value = [
                    v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on")
                    for v in value
                ]
            elif elem_type is str:
                value = [str(v) for v in value]
            else:
                value = [elem_type(v) for v in value]
        except (ValueError, TypeError):
            return False
    elif expected_type is bool:
        if isinstance(value, str):
            value = value.strip().lower() in ("1", "true", "yes", "on")
        else:
            try:
                value = bool(value)
            except (ValueError, TypeError):
                return False
    elif expected_type is str and not isinstance(value, str):
        value = str(value)
    elif expected_type is not type(value) and g[key_str] is not None:
        try:
            value = expected_type(value)
        except (ValueError, TypeError):
            return False

    if isinstance(value, str):
        value_str = value.strip()
        if value_str.endswith(":") or value_str in ("ollama", "lms", "vllm", "openai", "deepseek", "gemini", "openrouter", "claude", "groq"):
            value = ""
        else:
            value = value_str

    g[key_str] = value
    if key_str == "WEB_UI_PASSWORD":
        g["_WEB_UI_PASSWORD_GENERATED"] = False

    if key_str == "CURRENT_SOUL_NAME":
        target_path = SOUL_DIR / str(value)
        if not target_path.exists() and not str(value).startswith("manosaba_"):
            fallback_path = SOUL_DIR / f"manosaba_{str(value)}"
            if fallback_path.exists():
                target_path = fallback_path
        if not _is_soul_path_within_soul_dir(target_path):
            logger.warning("拒絕人設路徑（超出 SOUL_DIR）: %s", target_path)
            return False
        g["DEFAULT_SOUL_PATH"] = target_path
        try:
            from handlers.social.persona import clear_soul_cache
            clear_soul_cache()
        except Exception:
            pass

    if key_str == "SOUL_CONTENT":
        target_soul = g.get("DEFAULT_SOUL_PATH")
        if not target_soul or not target_soul.exists():
            active_name = str(g.get("CURRENT_SOUL_NAME", "manosaba_sherry_default.md"))
            target_soul = SOUL_DIR / active_name
            if not target_soul.exists() and not active_name.startswith("manosaba_"):
                fallback_target = SOUL_DIR / f"manosaba_{active_name}"
                if fallback_target.exists():
                    target_soul = fallback_target
        if not _is_soul_path_within_soul_dir(target_soul):
            logger.warning("拒絕寫入人設檔（超出 SOUL_DIR）: %s", target_soul)
            return False
        try:
            Path(target_soul).resolve().write_text(str(value), encoding="utf-8")
            from handlers.social.persona import clear_soul_cache
            clear_soul_cache()
            return True
        except Exception as exc:
            logger.warning("寫入人設檔 %s 失敗: %s", target_soul, exc)
            return False

    if isinstance(value, (str, int, float, bool)):
        os.environ[key_str] = str(value)

    try:
        data = {}
        if CONFIG_OVERRIDE_PATH.exists():
            with open(CONFIG_OVERRIDE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        data.pop("CORE_PERSONA", None)
        data.pop("SOUL_CONTENT", None)
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


