"""
config.py (mod/deep_search)
============================
深度研究模組設定檔。
管理檢索輪數、篇幅限制、逾時參數與儲存目錄。
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

try:
    import settings as _settings
except ImportError:
    _settings = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Module Root & Project Paths
# ---------------------------------------------------------------------------
MOD_DIR: Path = Path(__file__).resolve().parent
PROJECT_ROOT: Path = MOD_DIR.parent.parent
DATA_DIR: Path = PROJECT_ROOT / "data"
DEEP_SEARCH_DIR: Path = DATA_DIR / "deep_search"
SESSIONS_DIR: Path = DEEP_SEARCH_DIR / "sessions"
DEEP_SEARCH_DIR.mkdir(parents=True, exist_ok=True)
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

# 研究推演參數
DEFAULT_RESEARCH_ROUNDS: int = 4            # 預設多輪深度鑽研輪數 (1 輪全景掃描 + 3 輪子維度)
MIN_RESEARCH_ROUNDS: int = 2
MAX_RESEARCH_ROUNDS: int = 8

QUERIES_PER_ROUND: int = 2                 # 每一輪產生的檢索關鍵字數量
MAX_RESULTS_PER_QUERY: int = 4             # 每個關鍵字取得的候選結果數
MAX_PAGES_TO_SCRAPE_PER_ROUND: int = 3     # 每一輪實際深入抓取內文的網頁上限
MAX_CHARS_PER_PAGE: int = 4000             # 單一網頁擷取最大字元數 (適配地端 SLM context)

# 由 Web UI 設定覆寫 (config_override.json)
if _settings is not None:
    DEFAULT_RESEARCH_ROUNDS = int(getattr(_settings, "DEEP_SEARCH_DEFAULT_ROUNDS", DEFAULT_RESEARCH_ROUNDS))
    MIN_RESEARCH_ROUNDS = int(getattr(_settings, "DEEP_SEARCH_MIN_ROUNDS", MIN_RESEARCH_ROUNDS))
    MAX_RESEARCH_ROUNDS = int(getattr(_settings, "DEEP_SEARCH_MAX_ROUNDS", MAX_RESEARCH_ROUNDS))
    QUERIES_PER_ROUND = int(getattr(_settings, "DEEP_SEARCH_QUERIES_PER_ROUND", QUERIES_PER_ROUND))
    MAX_RESULTS_PER_QUERY = int(getattr(_settings, "DEEP_SEARCH_MAX_RESULTS_PER_QUERY", MAX_RESULTS_PER_QUERY))
    MAX_PAGES_TO_SCRAPE_PER_ROUND = int(getattr(_settings, "DEEP_SEARCH_MAX_PAGES_PER_ROUND", MAX_PAGES_TO_SCRAPE_PER_ROUND))
    MAX_CHARS_PER_PAGE = int(getattr(_settings, "DEEP_SEARCH_MAX_CHARS_PER_PAGE", MAX_CHARS_PER_PAGE))

# 網路與模型逾時 (秒)
DEFAULT_HTTP_TIMEOUT: int = int(os.getenv("DEFAULT_HTTP_TIMEOUT", "12"))
DEFAULT_LLM_TIMEOUT: int = int(os.getenv("OLLAMA_TIMEOUT", "180"))

# 搜尋端點與 User-Agent
DUCKDUCKGO_SEARCH_URL: str = os.getenv(
    "DUCKDUCKGO_SEARCH_URL", "https://html.duckduckgo.com/html/?q="
)
USER_AGENT: str = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

def get_default_model() -> str:
    """取得系統當前配置之主力推論模型。

    優先讀取 settings.PRIMARY_MODEL 或環境變數 PRIMARY_MODEL，
    若皆未設定則回退至預設之 FreeToken 模型字串。

    Returns:
        str: 主力推論模型完整標識符（含前綴）。
    """
    try:
        import settings
        val = getattr(settings, "PRIMARY_MODEL", None)
        if val and str(val).strip():
            return str(val).strip()
    except Exception:
        pass
    return os.getenv(
        "PRIMARY_MODEL",
        f"freetoken:{os.getenv('LLM_MODEL_NAME', 'mistralai/Mixtral-8x7B-Instruct-v0.1')}",
    )


# 預設思考與綜合模型 (向下相容)
DEFAULT_MODEL: str = get_default_model()
