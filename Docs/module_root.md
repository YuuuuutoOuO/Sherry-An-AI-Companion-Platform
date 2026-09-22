# 🏠 module_root — 根目錄模組文件

> 最後更新：2026-09-22 14:34  |  自動生成自 AST 掃描

涵蓋根目錄的所有 `.py` 檔案：`main.py`, `settings.py`, `build_tag_dict.py`

---


## 📄 `build_tag_dict.py`

> build_tag_dict.py


### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `OUTPUT_TAG_FILE` | `—` | `str(settings.PIXIV_TAG_PATH)` | L32 |
| `OUTPUT_META_FILE` | `—` | `str(settings.TAG_METADATA_PATH)` | L33 |
| `SOURCES` | `dict[str, str]` | `{'danbooru_csv': 'https://raw.githubusercontent.com/DominikDoom/a1111-sd-webui-t...` | L35 |
| `BUILTIN_ANIME_MAPPING` | `dict[str, str]` | `{'芙莉蓮': 'frieren_(sousou_no_frieren)', '費倫': 'fern_(sousou_no_frieren)', '修塔爾克':...` | L47 |
| `logger` | `—` | `logging.getLogger(__name__)` | L202 |
| `_CC` | `—` | `_make_converter()` | L217 |


### Functions

#### `_make_converter`  (L205)

```python
def _make_converter()
```

> 嘗試載入 OpenCC 簡轉繁，失敗時回傳 None。

#### `to_traditional`  (L220)

```python
def to_traditional(text: str) -> str
```

> 簡轉繁工具。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `text` | `str` | `—` |

#### `_clean_zh`  (L228)

```python
def _clean_zh(raw: str) -> str
```

> 清洗中文字串。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `raw` | `str` | `—` |

#### `_get`  (L235)

```python
def _get(url: str, timeout: int = 30) -> str
```

> 同步 HTTP GET。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `url` | `str` | `—` |
| `timeout` | `int` | `30` |

#### `_parse_danbooru_csv`  (L246)

```python
def _parse_danbooru_csv(text: str) -> tuple[dict[str, dict[str, object]], dict[str, str]]
```

> 解析 DominikDoom danbooru.csv。  \n>   \n>     格式：name, category, post_count, aliases  \n>     category 定義：  \n>       0 = general (一般特徵/姿勢/服裝)  \n>       1 = artist (畫師)  \n>       3 = copyright (作品/版權)  \n>       4 = character (角色)  \n>       5 = meta (元標籤)  \n>   \n>     Returns:  \n>         (metadata_dict, alias_to_canonical_dict)

| 參數 | 型別 | 預設值 |
|---|---|---|
| `text` | `str` | `—` |

#### `_parse_byzod_csv`  (L307)

```python
def _parse_byzod_csv(text: str) -> Iterator[tuple[str, str]]
```

> 解析 byzod 中文翻譯包 CSV。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `text` | `str` | `—` |

#### `build`  (L324)

```python
def build() -> None
```

> 執行全量標籤庫建置。


---


## 📄 `main.py`

> main.py


### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `_stream_handler` | `—` | `logging.StreamHandler(sys.stdout)` | L62 |
| `_file_handler` | `—` | `RotatingFileHandler(settings.BOT_LOG_PATH, maxBytes=10 * 1024 * 1024, backupCoun...` | L63 |
| `_trace_filter` | `—` | `TraceIdFilter()` | L69 |
| `logger` | `—` | `logging.getLogger(__name__)` | L79 |
| `EXTENSIONS` | `list[str]` | `['handlers.bot_brain', 'handlers.social', 'handlers.vibe_manager', 'handlers.mus...` | L85 |
| `BASE_DIR` | `Path` | `Path(__file__).resolve().parent` | L110 |
| `MOD_DIR` | `Path` | `BASE_DIR / 'mod'` | L111 |
| `MOD_CONFIG_PATH` | `Path` | `settings.DATA_DIR / 'mod_config.json'` | L112 |


### Functions

#### `_load_mod_config`  (L115)

```python
def _load_mod_config() -> dict[str, bool]
```

> 載入 data/mod_config.json 模組開關設定。  \n>   \n>     Returns:  \n>         dict[str, bool]: 模組鍵名對應是否啟用之字典。

#### `_is_mod_enabled`  (L132)

```python
def _is_mod_enabled(mod_key: str, config: dict[str, bool]) -> bool
```

> 檢查模組是否啟用（未明確設定者預設為 True）。  \n>   \n>     Args:  \n>         mod_key: 模組識別碼。  \n>         config: 模組設定字典。  \n>   \n>     Returns:  \n>         bool: 若啟用回傳 True，否則回傳 False。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `mod_key` | `str` | `—` |
| `config` | `dict[str, bool]` | `—` |

#### `_discover_optional_extensions`  (L145)

```python
def _discover_optional_extensions() -> list[str]
```

> 掃描 ./mod/ 目錄，自動找出含有 cog.py 且未被停用的子資料夾。  \n>   \n>     Returns:  \n>         list[str]: 待載入之模組擴充套件路徑清單。

#### `build_bot`  (L169)

```python
def build_bot() -> commands.Bot
```

> Construct and return the configured Bot instance.

#### `main`  (L187)

```python
async def main() -> None
```

> Async entry point: load extensions then connect to Discord.


---


## 📄 `settings.py`

> settings.py


### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L24 |
| `BASE_DIR` | `Path` | `Path(__file__).resolve().parent` | L31 |
| `DATA_DIR` | `Path` | `BASE_DIR / 'data'` | L32 |
| `FILE_DIR` | `Path` | `BASE_DIR / 'file'` | L33 |
| `SOUL_DIR` | `Path` | `FILE_DIR / 'soul'` | L34 |
| `VIDEO_DIR` | `Path` | `BASE_DIR / 'Video'` | L35 |
| `CURRENT_SOUL_NAME` | `str` | `'manosaba_sherry_default.md'` | L43 |
| `DEFAULT_SOUL_PATH` | `Path` | `SOUL_DIR / CURRENT_SOUL_NAME` | L44 |
| `COMMON_RULES_PATH` | `Path` | `SOUL_DIR / 'common_rules.md'` | L45 |
| `SOUL_CONTENT` | `str` | `''` | L46 |
| `CHANNEL_SOUL_MAP` | `dict[int, str]` | `{}` | L48 |
| `HEARTBEAT_PATH` | `Path` | `DATA_DIR / 'heartbeat.md'` | L62 |
| `DEFAULT_HEARTBEAT_CHANNEL_ID` | `int` | `int(os.getenv('DEFAULT_HEARTBEAT_CHANNEL_ID', '1519967448982487201'))` | L63 |
| `CHANNEL_VIBE_PATH` | `Path` | `DATA_DIR / 'channel_vibe.json'` | L65 |
| `USER_MEMORY_PATH` | `Path` | `DATA_DIR / 'user_memory.json'` | L67 |
| `USER_RELATIONS_PATH` | `Path` | `DATA_DIR / 'user_relations.json'` | L69 |
| `ROAST_RULES_PATH` | `Path` | `DATA_DIR / 'roast_rules.json'` | L73 |
| `RUNTIME_LOG_PATH` | `Path` | `DATA_DIR / 'runtime_log.jsonl'` | L78 |
| `ERROR_LOG_PATH` | `Path` | `DATA_DIR / 'error_log.jsonl'` | L79 |
| `SCHEDULE_PATH` | `Path` | `BASE_DIR / 'data' / 'schedule.json'` | L85 |
| `FFMPEG` | `—` | `str(BASE_DIR / 'ffmpeg')` | L90 |
| `DISCORD_BOT_TOKEN` | `str` | `os.getenv('DISCORD_BOT_TOKEN', '')` | L95 |
| `ADMIN_ROLE_IDS` | `list[int]` | `_parse_int_list_env('ADMIN_ROLE_IDS', [])` | L118 |
| `ADMIN_USER_IDS` | `list[int]` | `_parse_int_list_env('ADMIN_USER_IDS', []) or _parse_int_list_env('ADMIN_USER_ID'...` | L119 |
| `NSFW_ALLOWED_CHANNEL_IDS` | `list[int]` | `_parse_int_list_env('NSFW_ALLOWED_CHANNEL_IDS', [])` | L125 |
| `LLM_BASE_URL` | `str` | `os.getenv('LLM_BASE_URL', os.getenv('FREETOKEN_BASE_URL', 'http://localhost:1919...` | L144 |
| `FREETOKEN_BASE_URL` | `str` | `os.getenv('FREETOKEN_BASE_URL', 'http://localhost:1919/v1')` | L145 |
| `LMS_BASE_URL` | `str` | `os.getenv('LMS_BASE_URL', 'http://localhost:1234/v1')` | L146 |
| `OLLAMA_BASE_URL` | `str` | `os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')` | L147 |
| `VLLM_BASE_URL` | `str` | `os.getenv('VLLM_BASE_URL', 'http://localhost:8000/v1')` | L148 |
| `OPENAI_API_KEY` | `str` | `os.getenv('OPENAI_API_KEY', '')` | L151 |
| `DEEPSEEK_API_KEY` | `str` | `os.getenv('DEEPSEEK_API_KEY', '')` | L152 |
| `GEMINI_API_KEY` | `str` | `os.getenv('GEMINI_API_KEY', '')` | L153 |
| `CLAUDE_API_KEY` | `str` | `os.getenv('CLAUDE_API_KEY', '')` | L154 |
| `OPENROUTER_API_KEY` | `str` | `os.getenv('OPENROUTER_API_KEY', '')` | L155 |
| `OPENAI_BASE_URL` | `str` | `os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1')` | L158 |
| `DEEPSEEK_BASE_URL` | `str` | `os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com/v1')` | L159 |
| `GEMINI_BASE_URL` | `str` | `os.getenv('GEMINI_BASE_URL', 'https://generativelanguage.googleapis.com/v1beta/o...` | L160 |
| `CLAUDE_BASE_URL` | `str` | `os.getenv('CLAUDE_BASE_URL', 'https://api.anthropic.com/v1')` | L161 |
| `OPENROUTER_BASE_URL` | `str` | `os.getenv('OPENROUTER_BASE_URL', 'https://openrouter.ai/api/v1')` | L162 |
| `LLM_MODEL_NAME` | `str` | `os.getenv('LLM_MODEL_NAME', 'mistralai/Mixtral-8x7B-Instruct-v0.1')` | L165 |
| `PRIMARY_MODEL` | `str` | `os.getenv('PRIMARY_MODEL', f'freetoken:{LLM_MODEL_NAME}')` | L168 |
| `NOMORAL_MODEL` | `str` | `os.getenv('NOMORAL_MODEL', PRIMARY_MODEL)` | L171 |
| `QUICK_MODEL` | `str` | `os.getenv('QUICK_MODEL', 'ollama:krith/mistral-nemo-instruct-2407-abliterated:IQ...` | L174 |
| `CLASSIFIER_MODEL` | `str` | `os.getenv('CLASSIFIER_MODEL', 'ollama:qwen2.5:3b')` | L177 |
| `VIBE_MODEL` | `str` | `os.getenv('VIBE_MODEL', 'ollama:llama3.2:3b')` | L180 |
| `_raw_vision_model` | `str` | `os.getenv('VISION_MODEL', '').strip()` | L185 |
| `VISION_MODEL` | `str` | `'' if _raw_vision_model.endswith(':') or _raw_vision_model in ('freetoken', 'oll...` | L186 |
| `GEMINI_MIN_OUTPUT_TOKENS` | `int` | `int(os.getenv('GEMINI_MIN_OUTPUT_TOKENS', '4096'))` | L189 |
| `GEMINI_FALLBACK_MODELS` | `list[str]` | `['gemini:gemini-2.5-flash', 'gemini:gemini-2.0-flash', 'gemini:gemini-1.5-flash'...` | L192 |
| `CLOUD_SEARCH_MODEL` | `str` | `os.getenv('CLOUD_SEARCH_MODEL', 'deepseek:deepseek-v4-flash')` | L199 |
| `SELF_MONITORING_MODEL` | `str` | `os.getenv('SELF_MONITORING_MODEL', 'gemini:gemini-3.6-flash')` | L204 |
| `CONTEXT_MENU_EPHEMERAL` | `bool` | `os.getenv('CONTEXT_MENU_EPHEMERAL', 'False').lower() in ('true', '1', 'yes')` | L207 |
| `LLM_TIMEOUT` | `int` | `int(os.getenv('LLM_TIMEOUT', '180'))` | L210 |
| `LLM_STREAM_CHUNK_TIMEOUT` | `float` | `float(os.getenv('LLM_STREAM_CHUNK_TIMEOUT', '120.0'))` | L213 |
| `OLLAMA_TIMEOUT` | `int` | `int(os.getenv('OLLAMA_TIMEOUT', str(LLM_TIMEOUT)))` | L214 |
| `LLM_STREAM_INCLUDE_USAGE` | `bool` | `os.getenv('LLM_STREAM_INCLUDE_USAGE', 'True').lower() in ('true', '1', 'yes')` | L217 |
| `AGENT_DISPATCH_MODE` | `str` | `os.getenv('AGENT_DISPATCH_MODE', 'INTEGRATED').upper()` | L223 |
| `ACTION_EARLY_TERMINATION_ENABLED` | `bool` | `os.getenv('ACTION_EARLY_TERMINATION_ENABLED', 'True').lower() in ('true', '1', '...` | L226 |
| `PARALLEL_PLANNER_MODEL` | `str` | `os.getenv('PARALLEL_PLANNER_MODEL', CLASSIFIER_MODEL)` | L229 |
| `PARALLEL_MAX_TASKS` | `int` | `int(os.getenv('PARALLEL_MAX_TASKS', '8'))` | L230 |
| `PARALLEL_STEP_TIMEOUT` | `float` | `float(os.getenv('PARALLEL_STEP_TIMEOUT', '120.0'))` | L231 |
| `REACT_MODEL` | `str` | `os.getenv('REACT_MODEL', NOMORAL_MODEL)` | L235 |
| `REACT_MAX_STEPS` | `int` | `int(os.getenv('REACT_MAX_STEPS', '5'))` | L238 |
| `REACT_STEP_TIMEOUT` | `float` | `float(os.getenv('REACT_STEP_TIMEOUT', '120.0'))` | L239 |
| `REACT_MAX_OBSERVATION_LENGTH` | `int` | `int(os.getenv('REACT_MAX_OBSERVATION_LENGTH', '1200'))` | L240 |
| `REACT_MAX_TRANSCRIPT_CHARS` | `int` | `int(os.getenv('REACT_MAX_TRANSCRIPT_CHARS', '6000'))` | L241 |
| `LLM_MAX_CONTEXT_TOKENS` | `int` | `int(os.getenv('LLM_MAX_CONTEXT_TOKENS', '8192'))` | L243 |
| `LLM_MAX_OUTPUT_TOKENS` | `int` | `int(os.getenv('LLM_MAX_OUTPUT_TOKENS', '1024'))` | L244 |
| `LLM_NUM_BATCH` | `int` | `int(os.getenv('LLM_NUM_BATCH', '2048'))` | L248 |
| `OLLAMA_KEEP_ALIVE` | `str` | `os.getenv('OLLAMA_KEEP_ALIVE', '60m')` | L252 |
| `LLM_TEMPERATURE` | `float` | `float(os.getenv('LLM_TEMPERATURE', '0.85'))` | L255 |
| `LLM_PRESENCE_PENALTY` | `float` | `float(os.getenv('LLM_PRESENCE_PENALTY', '0.6'))` | L256 |
| `LLM_FREQUENCY_PENALTY` | `float` | `float(os.getenv('LLM_FREQUENCY_PENALTY', '0.6'))` | L257 |
| `PRIORITY_TAG_RESPONSE` | `int` | `int(os.getenv('PRIORITY_TAG_RESPONSE', '1'))` | L262 |
| `PRIORITY_DRAW` | `int` | `int(os.getenv('PRIORITY_DRAW', '5'))` | L263 |
| `PRIORITY_INTERJECT` | `int` | `int(os.getenv('PRIORITY_INTERJECT', '10'))` | L264 |
| `PRIORITY_HEARTBEAT` | `int` | `int(os.getenv('PRIORITY_HEARTBEAT', '20'))` | L265 |
| `PRIORITY_VIBE_UPDATE` | `int` | `int(os.getenv('PRIORITY_VIBE_UPDATE', '50'))` | L266 |
| `PRIORITY_WEB_EXPLORER` | `int` | `int(os.getenv('PRIORITY_WEB_EXPLORER', '99'))` | L267 |
| `CONTEXT_HISTORY_LIMIT` | `int` | `int(os.getenv('CONTEXT_HISTORY_LIMIT', '30'))` | L272 |
| `CONTEXT_TIME_GAP_SECONDS` | `int` | `int(os.getenv('CONTEXT_TIME_GAP_SECONDS', '900'))` | L273 |
| `CONTEXT_COMPRESS_THRESHOLD` | `int` | `int(os.getenv('CONTEXT_COMPRESS_THRESHOLD', '2000'))` | L276 |
| `CONTEXT_COMPRESS_MODEL` | `str` | `os.getenv('CONTEXT_COMPRESS_MODEL', '')` | L278 |
| `CONTEXT_COMPRESS_CACHE_TTL` | `float` | `float(os.getenv('CONTEXT_COMPRESS_CACHE_TTL', '60.0'))` | L280 |
| `CONTEXT_HISTORY_MAX_TOKENS` | `int` | `int(os.getenv('CONTEXT_HISTORY_MAX_TOKENS', '1500'))` | L284 |
| `CONTEXT_COMPRESS_MIN_GAP_MESSAGES` | `int` | `int(os.getenv('CONTEXT_COMPRESS_MIN_GAP_MESSAGES', '8'))` | L287 |
| `CONTEXT_COMPRESS_KEEP_RECENT` | `int` | `int(os.getenv('CONTEXT_COMPRESS_KEEP_RECENT', '4'))` | L289 |
| `MEMORY_MAX_FACTS_IN_CONTEXT` | `int` | `int(os.getenv('MEMORY_MAX_FACTS_IN_CONTEXT', '8'))` | L295 |
| `MEMORY_MAX_FACTS_PER_USER` | `int` | `int(os.getenv('MEMORY_MAX_FACTS_PER_USER', '25'))` | L298 |
| `MEMORY_PERMANENT_FACT_THRESHOLD` | `int` | `int(os.getenv('MEMORY_PERMANENT_FACT_THRESHOLD', '4'))` | L301 |
| `MEM_DIGEST_THRESHOLD` | `int` | `int(os.getenv('MEM_DIGEST_THRESHOLD', '15'))` | L305 |
| `MEMORY_EXTRACT_MODEL` | `str` | `os.getenv('MEMORY_EXTRACT_MODEL', PRIMARY_MODEL)` | L308 |
| `VIBE_UPDATE_MESSAGE_THRESHOLD` | `int` | `int(os.getenv('VIBE_UPDATE_MESSAGE_THRESHOLD', '50'))` | L310 |
| `VIBE_TOP_N_TOPICS` | `int` | `int(os.getenv('VIBE_TOP_N_TOPICS', '3'))` | L311 |
| `VIBE_DECAY_INTERVAL_SECONDS` | `int` | `int(os.getenv('VIBE_DECAY_INTERVAL_SECONDS', '3600'))` | L312 |
| `VIBE_DECAY_AMOUNT` | `int` | `int(os.getenv('VIBE_DECAY_AMOUNT', '1'))` | L313 |
| `VIBE_MIN_WEIGHT_TO_INJECT` | `int` | `int(os.getenv('VIBE_MIN_WEIGHT_TO_INJECT', '5'))` | L316 |
| `VIBE_REINFORCE_MAX_WEIGHT` | `int` | `int(os.getenv('VIBE_REINFORCE_MAX_WEIGHT', '10'))` | L319 |
| `TYPING_BASE_DELAY` | `float` | `float(os.getenv('TYPING_BASE_DELAY', '1.0'))` | L325 |
| `TYPING_DELAY_PER_50_CHARS` | `float` | `float(os.getenv('TYPING_DELAY_PER_50_CHARS', '0.3'))` | L326 |
| `_yt_key_raw` | `str` | `os.getenv('YOUTUBE_API_KEY', '')` | L331 |
| `YOUTUBE_API_KEY` | `str` | `_yt_key_raw if _yt_key_raw not in ('', 'your_youtube_api_key_here') else ''` | L332 |
| `OPENWEATHERMAP_API_KEY` | `str` | `os.getenv('OPENWEATHERMAP_API_KEY', '')` | L334 |
| `EXPLORER_INTERVAL_SECONDS` | `int` | `int(os.getenv('EXPLORER_INTERVAL_SECONDS', '1800'))` | L336 |
| `YOUTUBE_MAX_RESULTS` | `int` | `int(os.getenv('YOUTUBE_MAX_RESULTS', '5'))` | L337 |
| `EXPLORER_TARGET_CHANNEL_IDS` | `list[int]` | `_parse_int_list_env('EXPLORER_TARGET_CHANNEL_IDS', [])` | L338 |
| `INTERJECT_EVAL_THRESHOLD` | `int` | `int(os.getenv('INTERJECT_EVAL_THRESHOLD', '10'))` | L345 |
| `INTERJECT_COOLDOWN_SECONDS` | `int` | `int(os.getenv('INTERJECT_COOLDOWN_SECONDS', '30'))` | L348 |
| `INTERJECT_MAX_CONSECUTIVE` | `int` | `int(os.getenv('INTERJECT_MAX_CONSECUTIVE', '3'))` | L352 |
| `INTERJECT_SCORE_THRESHOLD` | `int` | `int(os.getenv('INTERJECT_SCORE_THRESHOLD', '7'))` | L355 |
| `RATE_LIMIT_MAX_REQUESTS` | `int` | `int(os.getenv('RATE_LIMIT_MAX_REQUESTS', '5'))` | L360 |
| `RATE_LIMIT_WINDOW_SECONDS` | `int` | `int(os.getenv('RATE_LIMIT_WINDOW_SECONDS', '30'))` | L361 |
| `RATE_LIMIT_REPLY_MESSAGE` | `str` | `os.getenv('RATE_LIMIT_REPLY_MESSAGE', '名偵探現在很忙！{cooldown:.0f} 秒後再來！')` | L362 |
| `HEALTH_CHECK_INTERVAL_SECONDS` | `int` | `int(os.getenv('HEALTH_CHECK_INTERVAL_SECONDS', '300'))` | L367 |
| `HEALTH_ALERT_CHANNEL_ID` | `int` | `int(os.getenv('HEALTH_ALERT_CHANNEL_ID', '0'))` | L368 |
| `HEALTH_ALERT_USER_IDS` | `list[int]` | `_parse_int_list_env('HEALTH_ALERT_USER_IDS', [])` | L369 |
| `DISCORD_LOG_LEVEL` | `str` | `os.getenv('DISCORD_LOG_LEVEL', 'WARNING')` | L370 |
| `PERF_WATCH_ENABLED` | `bool` | `os.getenv('PERF_WATCH_ENABLED', 'True').lower() in ('true', '1', 'yes')` | L421 |
| `PERF_WATCH_TOK_S_THRESHOLD` | `float` | `float(os.getenv('PERF_WATCH_TOK_S_THRESHOLD', '6.0'))` | L422 |
| `PERF_WATCH_WINDOW_SECONDS` | `int` | `int(os.getenv('PERF_WATCH_WINDOW_SECONDS', '900'))` | L423 |
| `PERF_WATCH_MIN_SAMPLES` | `int` | `int(os.getenv('PERF_WATCH_MIN_SAMPLES', '5'))` | L424 |
| `PERF_WATCH_CONSECUTIVE_WINDOWS` | `int` | `int(os.getenv('PERF_WATCH_CONSECUTIVE_WINDOWS', '2'))` | L425 |
| `PERF_WATCH_COOLDOWN_SECONDS` | `int` | `int(os.getenv('PERF_WATCH_COOLDOWN_SECONDS', '21600'))` | L426 |
| `VOICE_STT_MODEL` | `str` | `os.getenv('VOICE_STT_MODEL', str(BASE_DIR / 'models' / 'nemotron-3.5'))` | L431 |
| `VOICE_STT_DEVICE` | `str` | `os.getenv('VOICE_STT_DEVICE', 'cpu')` | L432 |
| `VOICE_STT_COMPUTE_TYPE` | `str` | `os.getenv('VOICE_STT_COMPUTE_TYPE', 'float32')` | L433 |
| `VOICE_STT_LANGUAGE` | `str` | `os.getenv('VOICE_STT_LANGUAGE', 'zh')` | L434 |
| `VOICE_TTS_LANGUAGE` | `str` | `os.getenv('VOICE_TTS_LANGUAGE', 'zh-cn')` | L435 |
| `VOICE_TTS_SPEAKER_WAV` | `str` | `os.getenv('VOICE_TTS_SPEAKER_WAV', str(BASE_DIR / 'file' / 'shiry_voice_sample.w...` | L436 |
| `VOICE_VAD_THRESHOLD` | `float` | `float(os.getenv('VOICE_VAD_THRESHOLD', '0.5'))` | L437 |
| `VOICE_VAD_MIN_SILENCE_MS` | `int` | `int(os.getenv('VOICE_VAD_MIN_SILENCE_MS', '500'))` | L438 |
| `VOICE_SILENCE_TIMEOUT_SECONDS` | `float` | `float(os.getenv('VOICE_SILENCE_TIMEOUT_SECONDS', '1.5'))` | L439 |
| `VOICE_MAX_RECORD_SECONDS` | `int` | `int(os.getenv('VOICE_MAX_RECORD_SECONDS', '30'))` | L440 |
| `POLLINATIONS_API_URL` | `str` | `os.getenv('POLLINATIONS_API_URL', 'https://image.pollinations.ai/prompt')` | L445 |
| `POLLINATIONS_TIMEOUT_SECONDS` | `int` | `int(os.getenv('POLLINATIONS_TIMEOUT_SECONDS', '30'))` | L446 |
| `POLLINATIONS_MODEL` | `str` | `os.getenv('POLLINATIONS_MODEL', 'flux-anime')` | L448 |
| `USE_ONLINE_IMAGE_MODEL` | `bool` | `os.getenv('USE_ONLINE_IMAGE_MODEL', 'True').lower() in ('true', '1', 'yes')` | L454 |
| `GEMINI_IMAGE_MODEL` | `str` | `os.getenv('GEMINI_IMAGE_MODEL', 'gemini-2.5-flash-image')` | L459 |
| `GEMINI_IMAGE_TIMEOUT_SECONDS` | `int` | `int(os.getenv('GEMINI_IMAGE_TIMEOUT_SECONDS', '30'))` | L460 |
| `HF_TOKEN` | `str` | `os.getenv('HF_TOKEN', '')` | L465 |
| `HF_IMAGE_MODEL` | `str` | `os.getenv('HF_IMAGE_MODEL', 'cagliostrolab/animagine-xl-3.1')` | L466 |
| `HF_IMAGE_TIMEOUT_SECONDS` | `int` | `int(os.getenv('HF_IMAGE_TIMEOUT_SECONDS', '120'))` | L467 |
| `SD_WEBUI_URL` | `str` | `os.getenv('SD_WEBUI_URL', 'http://localhost:7860')` | L472 |
| `SD_DEFAULT_WIDTH` | `int` | `int(os.getenv('SD_DEFAULT_WIDTH', '768'))` | L473 |
| `SD_DEFAULT_HEIGHT` | `int` | `int(os.getenv('SD_DEFAULT_HEIGHT', '1024'))` | L474 |
| `SD_DEFAULT_STEPS` | `int` | `int(os.getenv('SD_DEFAULT_STEPS', '20'))` | L475 |
| `SD_DEFAULT_CFG_SCALE` | `float` | `float(os.getenv('SD_DEFAULT_CFG_SCALE', '7.0'))` | L476 |
| `SD_DEFAULT_SAMPLER` | `str` | `os.getenv('SD_DEFAULT_SAMPLER', 'DPM++ 2M Karras')` | L477 |
| `SD_TIMEOUT_SECONDS` | `int` | `int(os.getenv('SD_TIMEOUT_SECONDS', '120'))` | L478 |
| `SD_NEGATIVE_PROMPT_BASE` | `str` | `os.getenv('SD_NEGATIVE_PROMPT_BASE', 'lowres, bad anatomy, bad hands, text, erro...` | L479 |
| `MICRO_COT_ENABLED` | `bool` | `os.getenv('MICRO_COT_ENABLED', 'True').lower() in ('true', '1', 'yes')` | L490 |
| `DEBUG_DETAIL` | `bool` | `os.getenv('DEBUG_DETAIL', 'False').lower() in ('true', '1', 'yes')` | L493 |
| `LIVE_THINKING_EDIT` | `bool` | `os.getenv('LIVE_THINKING_EDIT', 'True').lower() in ('true', '1', 'yes')` | L496 |
| `CHAT_LOG_DIR` | `Path` | `DATA_DIR / 'chat_logs'` | L502 |
| `NIGHTLY_DIGEST_HOUR` | `int` | `int(os.getenv('NIGHTLY_DIGEST_HOUR', '4'))` | L503 |
| `NIGHTLY_DIGEST_MINUTE` | `int` | `int(os.getenv('NIGHTLY_DIGEST_MINUTE', '30'))` | L504 |
| `NIGHTLY_DIGEST_MODEL` | `str` | `os.getenv('NIGHTLY_DIGEST_MODEL', PRIMARY_MODEL)` | L505 |
| `NIGHTLY_MAX_MSGS_PER_USER` | `int` | `int(os.getenv('NIGHTLY_MAX_MSGS_PER_USER', '200'))` | L506 |
| `WEB_UI_HOST` | `str` | `os.getenv('WEB_UI_HOST', '127.0.0.1')` | L511 |
| `WEB_UI_PORT` | `int` | `int(os.getenv('WEB_UI_PORT', '8080'))` | L512 |
| `WEB_UI_HTTPS` | `bool` | `True` | L513 |
| `WEB_UI_SSL_CERT` | `str | None` | `os.getenv('WEB_UI_SSL_CERT', None)` | L514 |
| `WEB_UI_SSL_KEY` | `str | None` | `os.getenv('WEB_UI_SSL_KEY', None)` | L515 |
| `WEB_UI_RADMIN_IP` | `str | None` | `os.getenv('WEB_UI_RADMIN_IP', '26.108.83.196')` | L516 |
| `WEB_UI_HTTP_TIMEOUT` | `float` | `5.0` | L517 |
| `YOUTUBE_COOKIES_PATH` | `Path` | `DATA_DIR / 'cookies.txt'` | L522 |
| `MUSIC_PLAYLIST_MAX_ITEMS` | `int` | `int(os.getenv('MUSIC_PLAYLIST_MAX_ITEMS', '100'))` | L523 |
| `MUSIC_FFMPEG_BEFORE_OPTIONS` | `str` | `os.getenv('MUSIC_FFMPEG_BEFORE_OPTIONS', '-reconnect 1 -reconnect_streamed 1 -re...` | L524 |
| `MUSIC_FFMPEG_OPTIONS` | `str` | `os.getenv('MUSIC_FFMPEG_OPTIONS', '-vn -af "loudnorm=I=-9:LRA=11:TP=-1.0"')` | L528 |
| `MUSIC_DEFAULT_VOLUME` | `float` | `float(os.getenv('MUSIC_DEFAULT_VOLUME', '1.2'))` | L529 |
| `MUSIC_QUEUE_VIEW_TIMEOUT` | `int` | `int(os.getenv('MUSIC_QUEUE_VIEW_TIMEOUT', '180'))` | L530 |
| `VOICE_STT_REPO_ID` | `str` | `os.getenv('VOICE_STT_REPO_ID', 'nvidia/nemotron-3.5-asr-streaming-0.6b')` | L532 |
| `VOICE_TTS_MODEL_NAME` | `str` | `os.getenv('VOICE_TTS_MODEL_NAME', 'tts_models/multilingual/multi-dataset/xtts_v2...` | L533 |
| `VOICE_VAD_REPO_OR_DIR` | `str` | `os.getenv('VOICE_VAD_REPO_OR_DIR', 'snakers4/silero-vad')` | L534 |
| `VOICE_VAD_MODEL_NAME` | `str` | `os.getenv('VOICE_VAD_MODEL_NAME', 'silero_vad')` | L535 |
| `PIXIV_TAG_PATH` | `Path` | `FILE_DIR / 'pixiv_tag.json'` | L540 |
| `TAG_METADATA_PATH` | `Path` | `FILE_DIR / 'tag_metadata.json'` | L541 |
| `PIXIV_TAG_MIN_POST_COUNT` | `int` | `int(os.getenv('PIXIV_TAG_MIN_POST_COUNT', '100'))` | L542 |
| `PIXIV_TAG_MAX_PAGES` | `int` | `int(os.getenv('PIXIV_TAG_MAX_PAGES', '60'))` | L543 |
| `PIXIV_TAG_REQUEST_DELAY` | `float` | `float(os.getenv('PIXIV_TAG_REQUEST_DELAY', '0.5'))` | L544 |
| `DANBOORU_API_URL` | `str` | `os.getenv('DANBOORU_API_URL', 'https://danbooru.donmai.us/tags.json')` | L545 |
| `WORLD_LOG_DIR` | `Path` | `DATA_DIR / 'world_history'` | L547 |
| `WORLD_MEMORY_PATH` | `Path` | `DATA_DIR / 'world_memory.json'` | L548 |
| `WORLD_MEMORY_SNIPPET_LIMIT` | `int` | `int(os.getenv('WORLD_MEMORY_SNIPPET_LIMIT', '6'))` | L549 |
| `WORLD_ARCHIVE_DIR` | `Path` | `DATA_DIR / 'memory' / 'world'` | L554 |
| `WORLD_ARCHIVE_TRIGGER_WEEKDAY` | `int` | `int(os.getenv('WORLD_ARCHIVE_TRIGGER_WEEKDAY', '6'))` | L556 |
| `WORLD_ARCHIVE_KEEP_WEEKS` | `int` | `int(os.getenv('WORLD_ARCHIVE_KEEP_WEEKS', '52'))` | L558 |
| `NIGHTLY_WORLD_SAMPLE_COUNT` | `int` | `int(os.getenv('NIGHTLY_WORLD_SAMPLE_COUNT', '200'))` | L561 |
| `BOT_LOG_PATH` | `Path` | `DATA_DIR / 'bot.log'` | L562 |
| `WEB_UI_LOG_PATH` | `Path` | `DATA_DIR / 'web_ui.log'` | L563 |
| `NIGHTLY_MIN_MSGS_THRESHOLD` | `int` | `int(os.getenv('NIGHTLY_MIN_MSGS_THRESHOLD', '5'))` | L569 |
| `BOT_BRAIN_MAX_RETRIES` | `int` | `int(os.getenv('BOT_BRAIN_MAX_RETRIES', '3'))` | L574 |
| `BOT_BRAIN_RETRY_DELAY` | `float` | `float(os.getenv('BOT_BRAIN_RETRY_DELAY', '2.0'))` | L575 |
| `HEARTBEAT_CHECK_INTERVAL_MINUTES` | `int` | `int(os.getenv('HEARTBEAT_CHECK_INTERVAL_MINUTES', '30'))` | L580 |
| `HEARTBEAT_CONDITION_START_HOUR` | `int` | `int(os.getenv('HEARTBEAT_CONDITION_START_HOUR', '9'))` | L581 |
| `HEARTBEAT_CONDITION_END_HOUR` | `int` | `int(os.getenv('HEARTBEAT_CONDITION_END_HOUR', '22'))` | L582 |
| `HEARTBEAT_CONDITION_MIN_GAP_SECONDS` | `int` | `int(os.getenv('HEARTBEAT_CONDITION_MIN_GAP_SECONDS', '14400'))` | L583 |
| `OPENWEATHERMAP_API_URL` | `str` | `os.getenv('OPENWEATHERMAP_API_URL', 'https://api.openweathermap.org/data/2.5/wea...` | L588 |
| `SAFEBOORU_API_URL` | `str` | `os.getenv('SAFEBOORU_API_URL', 'https://safebooru.org/index.php?page=dapi&s=post...` | L589 |
| `SAFEBOORU_IMAGE_BASE_URL` | `str` | `os.getenv('SAFEBOORU_IMAGE_BASE_URL', 'https://safebooru.org/images/')` | L590 |
| `DUCKDUCKGO_SEARCH_URL` | `str` | `os.getenv('DUCKDUCKGO_SEARCH_URL', 'https://html.duckduckgo.com/html/?q=')` | L591 |
| `DEFAULT_HTTP_TIMEOUT` | `int` | `int(os.getenv('DEFAULT_HTTP_TIMEOUT', '10'))` | L592 |
| `YOUTUBE_SEARCH_API_URL` | `str` | `os.getenv('YOUTUBE_SEARCH_API_URL', 'https://www.googleapis.com/youtube/v3/searc...` | L593 |
| `YOUTUBE_RSS_FEED_URL` | `str` | `os.getenv('YOUTUBE_RSS_FEED_URL', 'https://www.youtube.com/feeds/videos.xml?chan...` | L594 |
| `YOUTUBE_RSS_NS` | `str` | `os.getenv('YOUTUBE_RSS_NS', 'http://www.w3.org/2005/Atom')` | L595 |
| `DEFAULT_USER_AGENT` | `str` | `os.getenv('DEFAULT_USER_AGENT', 'DiscordBot/1.0 (https://github.com/placeholder;...` | L596 |
| `WEATHER_EXTRACT_TIMEOUT` | `float` | `float(os.getenv('WEATHER_EXTRACT_TIMEOUT', '10.0'))` | L604 |
| `WEATHER_REPLY_TIMEOUT` | `float` | `float(os.getenv('WEATHER_REPLY_TIMEOUT', '90.0'))` | L605 |
| `SAFEBOORU_HTTP_TIMEOUT` | `int` | `int(os.getenv('SAFEBOORU_HTTP_TIMEOUT', '12'))` | L606 |
| `VISION_ANALYSIS_TIMEOUT` | `int` | `int(os.getenv('VISION_ANALYSIS_TIMEOUT', '120'))` | L607 |
| `STOCK_EXTRACT_TIMEOUT` | `float` | `float(os.getenv('STOCK_EXTRACT_TIMEOUT', '10.0'))` | L608 |
| `STOCK_REPLY_TIMEOUT` | `float` | `float(os.getenv('STOCK_REPLY_TIMEOUT', '90.0'))` | L609 |
| `SCHEDULE_PARSE_TIMEOUT` | `float` | `float(os.getenv('SCHEDULE_PARSE_TIMEOUT', '20.0'))` | L610 |
| `DRAW_RAW_TIMEOUT` | `float` | `float(os.getenv('DRAW_RAW_TIMEOUT', '30.0'))` | L611 |
| `DRAW_EXPAND_TIMEOUT` | `float` | `float(os.getenv('DRAW_EXPAND_TIMEOUT', '10.0'))` | L612 |
| `INTERJECT_SCORE_TIMEOUT` | `float` | `float(os.getenv('INTERJECT_SCORE_TIMEOUT', '30.0'))` | L613 |
| `INTERJECT_REPLY_TIMEOUT` | `float` | `float(os.getenv('INTERJECT_REPLY_TIMEOUT', '60.0'))` | L614 |
| `YOUTUBE_EXPLORER_TIMEOUT` | `float` | `float(os.getenv('YOUTUBE_EXPLORER_TIMEOUT', '20.0'))` | L615 |
| `NIGHTLY_DIGEST_TIMEOUT` | `float` | `float(os.getenv('NIGHTLY_DIGEST_TIMEOUT', '120.0'))` | L616 |
| `HEALTH_CHECK_TIMEOUT` | `float` | `float(os.getenv('HEALTH_CHECK_TIMEOUT', '5.0'))` | L617 |
| `DISCORD_UI_VIEW_TIMEOUT` | `int` | `int(os.getenv('DISCORD_UI_VIEW_TIMEOUT', '300'))` | L618 |
| `ROAST_REPLY_TIMEOUT` | `float` | `float(os.getenv('ROAST_REPLY_TIMEOUT', '30.0'))` | L619 |
| `SPARK_DEFAULT_COOLDOWN_SECONDS` | `int` | `int(os.getenv('SPARK_DEFAULT_COOLDOWN_SECONDS', '3600'))` | L626 |
| `SPARK_CHECK_INTERVAL_MINUTES` | `int` | `int(os.getenv('SPARK_CHECK_INTERVAL_MINUTES', '1'))` | L627 |
| `DEEP_SEARCH_DEFAULT_ROUNDS` | `int` | `int(os.getenv('DEEP_SEARCH_DEFAULT_ROUNDS', '4'))` | L628 |
| `DEEP_SEARCH_MIN_ROUNDS` | `int` | `int(os.getenv('DEEP_SEARCH_MIN_ROUNDS', '2'))` | L629 |
| `DEEP_SEARCH_MAX_ROUNDS` | `int` | `int(os.getenv('DEEP_SEARCH_MAX_ROUNDS', '8'))` | L630 |
| `DEEP_SEARCH_QUERIES_PER_ROUND` | `int` | `int(os.getenv('DEEP_SEARCH_QUERIES_PER_ROUND', '2'))` | L631 |
| `DEEP_SEARCH_MAX_RESULTS_PER_QUERY` | `int` | `int(os.getenv('DEEP_SEARCH_MAX_RESULTS_PER_QUERY', '4'))` | L632 |
| `DEEP_SEARCH_MAX_PAGES_PER_ROUND` | `int` | `int(os.getenv('DEEP_SEARCH_MAX_PAGES_PER_ROUND', '3'))` | L633 |
| `DEEP_SEARCH_MAX_CHARS_PER_PAGE` | `int` | `int(os.getenv('DEEP_SEARCH_MAX_CHARS_PER_PAGE', '4000'))` | L634 |
| `DND_MAX_HISTORY_ROUNDS` | `int` | `int(os.getenv('DND_MAX_HISTORY_ROUNDS', '6'))` | L635 |
| `DRAW_TOOL_MAX_PROMPT_LENGTH` | `int` | `int(os.getenv('DRAW_TOOL_MAX_PROMPT_LENGTH', '300'))` | L636 |
| `LOREBOOK_MAX_GENERAL_ENTRIES` | `int` | `int(os.getenv('LOREBOOK_MAX_GENERAL_ENTRIES', '3'))` | L637 |
| `PAINTER_MAX_PAINTING_TIMEOUT_SEC` | `float` | `float(os.getenv('PAINTER_MAX_PAINTING_TIMEOUT_SEC', '120.0'))` | L638 |
| `STOCK_TRADER_INITIAL_CAPITAL_USD` | `float` | `float(os.getenv('STOCK_TRADER_INITIAL_CAPITAL_USD', '10000.0'))` | L639 |
| `STOCK_TRADER_FEE_RATE` | `float` | `float(os.getenv('STOCK_TRADER_FEE_RATE', '0.001'))` | L640 |
| `STOCK_TRADER_MIN_SHARES` | `float` | `float(os.getenv('STOCK_TRADER_MIN_SHARES', '0.01'))` | L641 |
| `CONFIG_OVERRIDE_PATH` | `Path` | `DATA_DIR / 'config_override.json'` | L647 |
| `_configured_web_ui_pw` | `str` | `os.getenv('WEB_UI_PASSWORD', '').strip()` | L695 |
| `WEB_UI_PASSWORD` | `str` | `—` | L696 |
| `_WEB_UI_PASSWORD_GENERATED` | `bool` | `—` | L697 |
| `WEB_UI_MAX_LOGIN_ATTEMPTS` | `int` | `5` | L705 |
| `WEB_UI_LOCKOUT_MINUTES` | `int` | `15` | L706 |
| `_configured_trusted_proxies` | `str` | `os.getenv('WEB_UI_TRUSTED_PROXIES', '').strip()` | L707 |
| `WEB_UI_TRUSTED_PROXIES` | `list[str]` | `[p.strip() for p in _configured_trusted_proxies.split(',') if p.strip()] if _con...` | L708 |


### Functions

#### `get_available_soul_names`  (L51)

```python
def get_available_soul_names() -> list[str]
```

> 自動掃描 SOUL_DIR 獲取所有 *_default.md 人設檔案名稱清單。  \n>   \n>     Returns:  \n>         list[str]: 依檔名排序的人設檔名列表。

#### `_parse_int_list_env`  (L97)

```python
def _parse_int_list_env(env_name: str, default: list[int] | None = None) -> list[int]
```

> 自環境變數安全解析整數列表（支援逗號分隔、中括號與引號格式）。  \n>   \n>     Args:  \n>         env_name: 環境變數名稱。  \n>         default: 預設整數列表。  \n>   \n>     Returns:  \n>         list[int]: 解析後的整數列表。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `env_name` | `str` | `—` |
| `default` | `list[int] | None` | `None` |

#### `get_alert_recipient_user_ids`  (L373)

```python
def get_alert_recipient_user_ids() -> list[int]
```

> 取得應接收系統警報與錯誤日誌私訊的 Discord User ID 列表。  \n>   \n>     自動合併 HEALTH_ALERT_USER_IDS、ADMIN_USER_IDS 與多種常見環境變數別名  \n>     （如 HEALTH_ALERT_USER_ID, ADMIN_USER_ID, DC_USER_ID, DISCORD_USER_ID 等），  \n>     並保留順序去除重複項。  \n>   \n>     Returns:  \n>         list[int]: 欲接收私訊通知的 Discord User ID 清單。

#### `update_setting`  (L713)

```python
def update_setting(key: str, value: Any) -> bool
```

> 更新全域設定值並持久化至 config_override.json。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `key` | `str` | `—` |
| `value` | `Any` | `—` |

#### `get_editable_settings`  (L822)

```python
def get_editable_settings() -> dict[str, Any]
```

> 回傳所有可編輯設定，含 meta 資訊（委託 web_ui.settings_schema 解析）。  \n>   \n>     Returns:  \n>         dict: key -> {value, label, description, type, category, ...constraints}


---


## 📄 `version.py`

> version.py


### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `APP_NAME` | `str` | `'橘雪莉 (Sherry)'` | L12 |
| `APP_VERSION` | `str` | `'v1.9.0-dev'` | L13 |
| `__version__` | `str` | `APP_VERSION` | L15 |
| `__all__` | `—` | `['APP_NAME', 'APP_VERSION', '__version__']` | L16 |


---
