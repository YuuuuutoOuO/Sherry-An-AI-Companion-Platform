# 🏠 module_root — 根目錄模組文件

> 最後更新：2026-08-30 18:04  |  自動生成自 AST 掃描

涵蓋根目錄的所有 `.py` 檔案：`main.py`, `settings.py`, `build_tag_dict.py`

---


## 📄 `build_tag_dict.py`

> build_tag_dict.py


### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `OUTPUT_TAG_FILE` | `—` | `str(settings.PIXIV_TAG_PATH)` | L33 |
| `OUTPUT_META_FILE` | `—` | `str(settings.TAG_METADATA_PATH)` | L34 |
| `SOURCES` | `dict[str, str]` | `{'danbooru_csv': 'https://raw.githubusercontent.com/DominikDoom/a1111-sd-webui-t...` | L36 |
| `BUILTIN_ANIME_MAPPING` | `dict[str, str]` | `{'芙莉蓮': 'frieren_(sousou_no_frieren)', '費倫': 'fern_(sousou_no_frieren)', '修塔爾克':...` | L48 |
| `logger` | `—` | `logging.getLogger(__name__)` | L203 |
| `_CC` | `—` | `_make_converter()` | L218 |


### Functions

#### `_make_converter`  (L206)

```python
def _make_converter()
```

> 嘗試載入 OpenCC 簡轉繁，失敗時回傳 None。

#### `to_traditional`  (L221)

```python
def to_traditional(text: str) -> str
```

> 簡轉繁工具。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `text` | `str` | `—` |

#### `_clean_zh`  (L226)

```python
def _clean_zh(raw: str) -> str
```

> 清洗中文字串。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `raw` | `str` | `—` |

#### `_get`  (L233)

```python
def _get(url: str, timeout: int = 30) -> str
```

> 同步 HTTP GET。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `url` | `str` | `—` |
| `timeout` | `int` | `30` |

#### `_parse_danbooru_csv`  (L243)

```python
def _parse_danbooru_csv(text: str) -> tuple[dict[str, dict[str, object]], dict[str, str]]
```

> 解析 DominikDoom danbooru.csv。  \n>   \n>     格式：name, category, post_count, aliases  \n>     category 定義：  \n>       0 = general (一般特徵/姿勢/服裝)  \n>       1 = artist (畫師)  \n>       3 = copyright (作品/版權)  \n>       4 = character (角色)  \n>       5 = meta (元標籤)  \n>   \n>     Returns:  \n>         (metadata_dict, alias_to_canonical_dict)

| 參數 | 型別 | 預設值 |
|---|---|---|
| `text` | `str` | `—` |

#### `_parse_byzod_csv`  (L304)

```python
def _parse_byzod_csv(text: str) -> Iterator[tuple[str, str]]
```

> 解析 byzod 中文翻譯包 CSV。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `text` | `str` | `—` |

#### `build`  (L321)

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
| `_stream_handler` | `—` | `logging.StreamHandler(sys.stdout)` | L63 |
| `_file_handler` | `—` | `logging.FileHandler(settings.BOT_LOG_PATH, encoding='utf-8')` | L64 |
| `_trace_filter` | `—` | `TraceIdFilter()` | L65 |
| `logger` | `—` | `logging.getLogger(__name__)` | L75 |
| `EXTENSIONS` | `list[str]` | `['handlers.bot_brain', 'handlers.social', 'handlers.vibe_manager', 'handlers.mus...` | L81 |
| `BASE_DIR` | `Path` | `Path(__file__).resolve().parent` | L103 |
| `MOD_DIR` | `Path` | `BASE_DIR / 'mod'` | L104 |


### Functions

#### `_discover_optional_extensions`  (L107)

```python
def _discover_optional_extensions() -> list[str]
```

> 掃描 ./mod/ 目錄，自動找出含有 cog.py 的子資料夾。

#### `build_bot`  (L122)

```python
def build_bot() -> commands.Bot
```

> Construct and return the configured Bot instance.

#### `main`  (L140)

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
| `BASE_DIR` | `Path` | `Path(__file__).resolve().parent` | L37 |
| `DATA_DIR` | `Path` | `BASE_DIR / 'data'` | L38 |
| `FILE_DIR` | `Path` | `BASE_DIR / 'file'` | L39 |
| `SOUL_DIR` | `Path` | `FILE_DIR / 'soul'` | L40 |
| `VIDEO_DIR` | `Path` | `BASE_DIR / 'Video'` | L41 |
| `DEFAULT_SOUL_PATH` | `Path` | `SOUL_DIR / 'sherry_default.md'` | L42 |
| `CHANNEL_SOUL_MAP` | `dict[int, str]` | `{}` | L44 |
| `HEARTBEAT_PATH` | `Path` | `DATA_DIR / 'heartbeat.md'` | L46 |
| `DEFAULT_HEARTBEAT_CHANNEL_ID` | `int` | `1519967448982487201` | L47 |
| `CHANNEL_VIBE_PATH` | `Path` | `DATA_DIR / 'channel_vibe.json'` | L49 |
| `DEEP_THINKING_LOG_PATH` | `Path` | `DATA_DIR / 'deep_thinking.json'` | L51 |
| `DRAW_THINKING_LOG_PATH` | `Path` | `DATA_DIR / 'draw_thinking.json'` | L52 |
| `USER_MEMORY_PATH` | `Path` | `DATA_DIR / 'user_memory.json'` | L54 |
| `USER_RELATIONS_PATH` | `Path` | `DATA_DIR / 'user_relations.json'` | L56 |
| `ROAST_RULES_PATH` | `Path` | `DATA_DIR / 'roast_rules.json'` | L60 |
| `RUNTIME_LOG_PATH` | `Path` | `DATA_DIR / 'runtime_log.jsonl'` | L65 |
| `ERROR_LOG_PATH` | `Path` | `DATA_DIR / 'error_log.jsonl'` | L66 |
| `SCHEDULE_PATH` | `Path` | `BASE_DIR / 'data' / 'schedule.json'` | L72 |
| `THINKING_LOG_PATH` | `Path` | `DATA_DIR / 'thinking.json'` | L75 |
| `THINKING_LOG_DIR` | `Path` | `DATA_DIR / 'thinking'` | L76 |
| `STT_THINKING_LOG_PATH` | `Path` | `DATA_DIR / 'stt_thinking.log'` | L77 |
| `FFMPEG` | `—` | `str(BASE_DIR / 'ffmpeg')` | L82 |
| `DISCORD_BOT_TOKEN` | `str` | `os.getenv('DISCORD_BOT_TOKEN', '')` | L87 |
| `ADMIN_ROLE_IDS` | `list[int]` | `[]` | L88 |
| `ADMIN_USER_IDS` | `list[int]` | `[425311733503229972]` | L89 |
| `NSFW_ALLOWED_CHANNEL_IDS` | `list[int]` | `[]` | L90 |
| `LLM_BASE_URL` | `str` | `os.getenv('LLM_BASE_URL', os.getenv('FREETOKEN_BASE_URL', 'http://localhost:1919...` | L109 |
| `FREETOKEN_BASE_URL` | `str` | `os.getenv('FREETOKEN_BASE_URL', 'http://localhost:1919/v1')` | L110 |
| `LMS_BASE_URL` | `str` | `os.getenv('LMS_BASE_URL', 'http://localhost:1234/v1')` | L111 |
| `OLLAMA_BASE_URL` | `str` | `os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')` | L112 |
| `VLLM_BASE_URL` | `str` | `os.getenv('VLLM_BASE_URL', 'http://localhost:8000/v1')` | L113 |
| `OPENAI_API_KEY` | `str` | `os.getenv('OPENAI_API_KEY', '')` | L116 |
| `DEEPSEEK_API_KEY` | `str` | `os.getenv('DEEPSEEK_API_KEY', '')` | L117 |
| `GEMINI_API_KEY` | `str` | `os.getenv('GEMINI_API_KEY', '')` | L118 |
| `CLAUDE_API_KEY` | `str` | `os.getenv('CLAUDE_API_KEY', '')` | L119 |
| `OPENROUTER_API_KEY` | `str` | `os.getenv('OPENROUTER_API_KEY', '')` | L120 |
| `OPENAI_BASE_URL` | `str` | `os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1')` | L123 |
| `DEEPSEEK_BASE_URL` | `str` | `os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com/v1')` | L124 |
| `GEMINI_BASE_URL` | `str` | `os.getenv('GEMINI_BASE_URL', 'https://generativelanguage.googleapis.com/v1beta/o...` | L125 |
| `CLAUDE_BASE_URL` | `str` | `os.getenv('CLAUDE_BASE_URL', 'https://api.anthropic.com/v1')` | L126 |
| `OPENROUTER_BASE_URL` | `str` | `os.getenv('OPENROUTER_BASE_URL', 'https://openrouter.ai/api/v1')` | L127 |
| `LLM_MODEL_NAME` | `str` | `os.getenv('LLM_MODEL_NAME', 'mistralai/Mixtral-8x7B-Instruct-v0.1')` | L130 |
| `PRIMARY_MODEL` | `str` | `os.getenv('PRIMARY_MODEL', f'freetoken:{LLM_MODEL_NAME}')` | L133 |
| `NOMORAL_MODEL` | `str` | `os.getenv('NOMORAL_MODEL', PRIMARY_MODEL)` | L136 |
| `QUICK_MODEL` | `str` | `os.getenv('QUICK_MODEL', 'ollama:krith/mistral-nemo-instruct-2407-abliterated:IQ...` | L139 |
| `CLASSIFIER_MODEL` | `str` | `os.getenv('CLASSIFIER_MODEL', 'ollama:qwen2.5:3b')` | L142 |
| `VIBE_MODEL` | `str` | `os.getenv('VIBE_MODEL', 'ollama:llama3.2:3b')` | L145 |
| `_raw_vision_model` | `str` | `os.getenv('VISION_MODEL', '').strip()` | L150 |
| `VISION_MODEL` | `str` | `'' if _raw_vision_model.endswith(':') or _raw_vision_model in ('freetoken', 'oll...` | L151 |
| `CLOUD_SEARCH_MODEL` | `str` | `'deepseek:deepseek-v4-flash'` | L154 |
| `LLM_TIMEOUT` | `int` | `int(os.getenv('LLM_TIMEOUT', '180'))` | L157 |
| `LLM_STREAM_CHUNK_TIMEOUT` | `float` | `float(os.getenv('LLM_STREAM_CHUNK_TIMEOUT', '60.0'))` | L158 |
| `OLLAMA_TIMEOUT` | `int` | `int(os.getenv('OLLAMA_TIMEOUT', str(LLM_TIMEOUT)))` | L159 |
| `AGENT_DISPATCH_MODE` | `str` | `os.getenv('AGENT_DISPATCH_MODE', 'INTEGRATED').upper()` | L165 |
| `ACTION_EARLY_TERMINATION_ENABLED` | `bool` | `os.getenv('ACTION_EARLY_TERMINATION_ENABLED', 'True').lower() in ('true', '1', '...` | L168 |
| `PARALLEL_PLANNER_MODEL` | `str` | `os.getenv('PARALLEL_PLANNER_MODEL', CLASSIFIER_MODEL)` | L171 |
| `PARALLEL_MAX_TASKS` | `int` | `5` | L172 |
| `PARALLEL_STEP_TIMEOUT` | `float` | `60.0` | L173 |
| `REACT_MODEL` | `str` | `os.getenv('REACT_MODEL', NOMORAL_MODEL)` | L177 |
| `REACT_ENABLED` | `bool` | `os.getenv('REACT_ENABLED', 'True').lower() == 'true'` | L180 |
| `REACT_MAX_STEPS` | `int` | `5` | L183 |
| `REACT_STEP_TIMEOUT` | `float` | `60.0` | L184 |
| `REACT_MAX_OBSERVATION_LENGTH` | `int` | `1200` | L185 |
| `REACT_MAX_TRANSCRIPT_CHARS` | `int` | `6000` | L186 |
| `LLM_MAX_CONTEXT_TOKENS` | `int` | `8192` | L188 |
| `LLM_MAX_OUTPUT_TOKENS` | `int` | `1024` | L189 |
| `LLM_NUM_BATCH` | `int` | `2048` | L193 |
| `OLLAMA_KEEP_ALIVE` | `str` | `'60m'` | L197 |
| `LLM_TEMPERATURE` | `float` | `0.85` | L200 |
| `LLM_PRESENCE_PENALTY` | `float` | `0.6` | L201 |
| `LLM_FREQUENCY_PENALTY` | `float` | `0.6` | L202 |
| `PRIORITY_TAG_RESPONSE` | `int` | `1` | L207 |
| `PRIORITY_INTERJECT` | `int` | `10` | L208 |
| `PRIORITY_HEARTBEAT` | `int` | `20` | L209 |
| `PRIORITY_VIBE_UPDATE` | `int` | `50` | L210 |
| `PRIORITY_DRAW` | `int` | `80` | L211 |
| `PRIORITY_WEB_EXPLORER` | `int` | `99` | L212 |
| `CONTEXT_HISTORY_LIMIT` | `int` | `30` | L217 |
| `CONTEXT_TIME_GAP_SECONDS` | `int` | `900` | L218 |
| `MEMORY_MAX_FACTS_IN_CONTEXT` | `int` | `int(os.getenv('MEMORY_MAX_FACTS_IN_CONTEXT', '15'))` | L224 |
| `MEMORY_MAX_FACTS_PER_USER` | `int` | `int(os.getenv('MEMORY_MAX_FACTS_PER_USER', '25'))` | L227 |
| `MEMORY_PERMANENT_FACT_THRESHOLD` | `int` | `int(os.getenv('MEMORY_PERMANENT_FACT_THRESHOLD', '4'))` | L230 |
| `MEM_DIGEST_THRESHOLD` | `int` | `int(os.getenv('MEM_DIGEST_THRESHOLD', '10'))` | L233 |
| `MEMORY_EXTRACT_MODEL` | `str` | `os.getenv('MEMORY_EXTRACT_MODEL', PRIMARY_MODEL)` | L236 |
| `VIBE_UPDATE_MESSAGE_THRESHOLD` | `int` | `50` | L238 |
| `VIBE_TOP_N_TOPICS` | `int` | `3` | L239 |
| `VIBE_DECAY_INTERVAL_SECONDS` | `int` | `3600` | L240 |
| `VIBE_DECAY_AMOUNT` | `int` | `1` | L241 |
| `VIBE_MIN_WEIGHT_TO_INJECT` | `int` | `5` | L244 |
| `VIBE_REINFORCE_MAX_WEIGHT` | `int` | `10` | L247 |
| `TYPING_BASE_DELAY` | `float` | `1.0` | L253 |
| `TYPING_DELAY_PER_50_CHARS` | `float` | `0.3` | L254 |
| `_yt_key_raw` | `str` | `os.getenv('YOUTUBE_API_KEY', '')` | L259 |
| `YOUTUBE_API_KEY` | `str` | `_yt_key_raw if _yt_key_raw not in ('', 'your_youtube_api_key_here') else ''` | L260 |
| `OPENWEATHERMAP_API_KEY` | `str` | `os.getenv('OPENWEATHERMAP_API_KEY', '')` | L262 |
| `EXPLORER_INTERVAL_SECONDS` | `int` | `1800` | L264 |
| `YOUTUBE_MAX_RESULTS` | `int` | `5` | L265 |
| `EXPLORER_TARGET_CHANNEL_IDS` | `list[int]` | `[1522439581658648636]` | L266 |
| `INTERJECT_EVAL_THRESHOLD` | `int` | `10` | L273 |
| `INTERJECT_COOLDOWN_SECONDS` | `int` | `30` | L276 |
| `INTERJECT_MAX_CONSECUTIVE` | `int` | `3` | L280 |
| `INTERJECT_SCORE_THRESHOLD` | `int` | `7` | L283 |
| `RATE_LIMIT_MAX_REQUESTS` | `int` | `5` | L288 |
| `RATE_LIMIT_WINDOW_SECONDS` | `int` | `30` | L289 |
| `RATE_LIMIT_REPLY_MESSAGE` | `str` | `'名偵探現在很忙！{cooldown:.0f} 秒後再來！'` | L290 |
| `HEALTH_CHECK_INTERVAL_SECONDS` | `int` | `300` | L295 |
| `HEALTH_ALERT_CHANNEL_ID` | `int` | `1503226173386133574` | L296 |
| `DISCORD_LOG_LEVEL` | `str` | `'WARNING'` | L297 |
| `VOICE_STT_MODEL` | `str` | `str(BASE_DIR / 'models' / 'nemotron-3.5')` | L302 |
| `VOICE_STT_DEVICE` | `str` | `'cpu'` | L303 |
| `VOICE_STT_COMPUTE_TYPE` | `str` | `'float32'` | L304 |
| `VOICE_STT_LANGUAGE` | `str` | `'zh'` | L305 |
| `VOICE_TTS_MODEL_PATH` | `str` | `str(BASE_DIR / 'models' / 'xtts-v2')` | L306 |
| `VOICE_TTS_LANGUAGE` | `str` | `'zh-cn'` | L307 |
| `VOICE_TTS_SPEAKER_WAV` | `str` | `str(BASE_DIR / 'file' / 'shiry_voice_sample.wav')` | L308 |
| `VOICE_VAD_THRESHOLD` | `float` | `0.5` | L309 |
| `VOICE_VAD_MIN_SILENCE_MS` | `int` | `500` | L310 |
| `VOICE_SILENCE_TIMEOUT_SECONDS` | `float` | `1.5` | L311 |
| `VOICE_MAX_RECORD_SECONDS` | `int` | `30` | L312 |
| `POLLINATIONS_API_URL` | `str` | `'https://image.pollinations.ai/prompt'` | L317 |
| `POLLINATIONS_TIMEOUT_SECONDS` | `int` | `30` | L318 |
| `POLLINATIONS_MODEL` | `str` | `'flux-anime'` | L320 |
| `USE_ONLINE_IMAGE_MODEL` | `bool` | `os.getenv('USE_ONLINE_IMAGE_MODEL', 'True').lower() in ('true', '1', 'yes')` | L326 |
| `GEMINI_IMAGE_MODEL` | `str` | `os.getenv('GEMINI_IMAGE_MODEL', 'gemini-2.5-flash-image')` | L331 |
| `GEMINI_IMAGE_TIMEOUT_SECONDS` | `int` | `30` | L332 |
| `HF_TOKEN` | `str` | `os.getenv('HF_TOKEN', '')` | L337 |
| `HF_IMAGE_MODEL` | `str` | `'cagliostrolab/animagine-xl-3.1'` | L338 |
| `HF_IMAGE_TIMEOUT_SECONDS` | `int` | `120` | L339 |
| `SD_WEBUI_URL` | `str` | `'http://localhost:7860'` | L344 |
| `SD_DEFAULT_WIDTH` | `int` | `768` | L345 |
| `SD_DEFAULT_HEIGHT` | `int` | `1024` | L346 |
| `SD_DEFAULT_STEPS` | `int` | `20` | L347 |
| `SD_DEFAULT_CFG_SCALE` | `float` | `7.0` | L348 |
| `SD_DEFAULT_SAMPLER` | `str` | `'DPM++ 2M Karras'` | L349 |
| `SD_TIMEOUT_SECONDS` | `int` | `120` | L350 |
| `SD_NEGATIVE_PROMPT_BASE` | `str` | `'lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, fewe...` | L351 |
| `MICRO_COT_ENABLED` | `bool` | `True` | L359 |
| `DEBUG_DETAIL` | `bool` | `False` | L362 |
| `LIVE_THINKING_EDIT` | `bool` | `True` | L365 |
| `CHAT_LOG_DIR` | `Path` | `DATA_DIR / 'chat_logs'` | L371 |
| `NIGHTLY_DIGEST_HOUR` | `int` | `4` | L372 |
| `NIGHTLY_DIGEST_MINUTE` | `int` | `30` | L373 |
| `NIGHTLY_DIGEST_MODEL` | `str` | `PRIMARY_MODEL` | L374 |
| `NIGHTLY_MAX_MSGS_PER_USER` | `int` | `200` | L375 |
| `WEB_UI_HOST` | `str` | `os.getenv('WEB_UI_HOST', '0.0.0.0')` | L380 |
| `WEB_UI_PORT` | `int` | `int(os.getenv('WEB_UI_PORT', '8080'))` | L381 |
| `WEB_UI_HTTPS` | `bool` | `True` | L382 |
| `WEB_UI_SSL_CERT` | `str | None` | `os.getenv('WEB_UI_SSL_CERT', None)` | L383 |
| `WEB_UI_SSL_KEY` | `str | None` | `os.getenv('WEB_UI_SSL_KEY', None)` | L384 |
| `WEB_UI_RADMIN_IP` | `str | None` | `os.getenv('WEB_UI_RADMIN_IP', '26.108.83.196')` | L385 |
| `WEB_UI_HTTP_TIMEOUT` | `float` | `5.0` | L386 |
| `YOUTUBE_COOKIES_PATH` | `Path` | `DATA_DIR / 'cookies.txt'` | L391 |
| `MUSIC_PLAYLIST_MAX_ITEMS` | `int` | `100` | L392 |
| `MUSIC_FFMPEG_BEFORE_OPTIONS` | `str` | `'-reconnect 1 -reconnect_streamed 1 -reconnect_at_eof 1 -reconnect_on_network_er...` | L393 |
| `MUSIC_FFMPEG_OPTIONS` | `str` | `'-vn -af "loudnorm=I=-9:LRA=11:TP=-1.0"'` | L394 |
| `MUSIC_DEFAULT_VOLUME` | `float` | `1.2` | L395 |
| `MUSIC_QUEUE_VIEW_TIMEOUT` | `int` | `180` | L396 |
| `VOICE_STT_REPO_ID` | `str` | `'nvidia/nemotron-3.5-asr-streaming-0.6b'` | L398 |
| `VOICE_TTS_MODEL_NAME` | `str` | `'tts_models/multilingual/multi-dataset/xtts_v2'` | L399 |
| `VOICE_VAD_REPO_OR_DIR` | `str` | `'snakers4/silero-vad'` | L400 |
| `VOICE_VAD_MODEL_NAME` | `str` | `'silero_vad'` | L401 |
| `PIXIV_TAG_PATH` | `Path` | `FILE_DIR / 'pixiv_tag.json'` | L406 |
| `TAG_METADATA_PATH` | `Path` | `FILE_DIR / 'tag_metadata.json'` | L407 |
| `PIXIV_TAG_MIN_POST_COUNT` | `int` | `100` | L408 |
| `PIXIV_TAG_MAX_PAGES` | `int` | `60` | L409 |
| `PIXIV_TAG_REQUEST_DELAY` | `float` | `0.5` | L410 |
| `DANBOORU_API_URL` | `str` | `'https://danbooru.donmai.us/tags.json'` | L411 |
| `DANBOORU_POSTS_API_URL` | `str` | `'https://danbooru.donmai.us/posts.json'` | L412 |
| `WORLD_LOG_DIR` | `Path` | `DATA_DIR / 'world_history'` | L414 |
| `WORLD_MEMORY_PATH` | `Path` | `DATA_DIR / 'world_memory.json'` | L415 |
| `BOT_LOG_PATH` | `Path` | `DATA_DIR / 'bot.log'` | L416 |
| `WEB_UI_LOG_PATH` | `Path` | `DATA_DIR / 'web_ui.log'` | L417 |
| `NIGHTLY_MIN_MSGS_THRESHOLD` | `int` | `int(os.getenv('NIGHTLY_MIN_MSGS_THRESHOLD', '5'))` | L423 |
| `BOT_BRAIN_MAX_RETRIES` | `int` | `3` | L428 |
| `BOT_BRAIN_RETRY_DELAY` | `float` | `2.0` | L429 |
| `HEARTBEAT_CHECK_INTERVAL_MINUTES` | `int` | `30` | L434 |
| `HEARTBEAT_CONDITION_START_HOUR` | `int` | `9` | L435 |
| `HEARTBEAT_CONDITION_END_HOUR` | `int` | `22` | L436 |
| `HEARTBEAT_CONDITION_MIN_GAP_SECONDS` | `int` | `14400` | L437 |
| `OPENWEATHERMAP_API_URL` | `str` | `'https://api.openweathermap.org/data/2.5/weather'` | L442 |
| `SAFEBOORU_API_URL` | `str` | `'https://safebooru.org/index.php?page=dapi&s=post'` | L443 |
| `SAFEBOORU_IMAGE_BASE_URL` | `str` | `'https://safebooru.org/images/'` | L444 |
| `DUCKDUCKGO_SEARCH_URL` | `str` | `'https://html.duckduckgo.com/html/?q='` | L445 |
| `DEFAULT_HTTP_TIMEOUT` | `int` | `10` | L446 |
| `YOUTUBE_SEARCH_API_URL` | `str` | `'https://www.googleapis.com/youtube/v3/search'` | L447 |
| `YOUTUBE_RSS_FEED_URL` | `str` | `'https://www.youtube.com/feeds/videos.xml?channel_id='` | L448 |
| `YOUTUBE_RSS_NS` | `str` | `'http://www.w3.org/2005/Atom'` | L449 |
| `DEFAULT_USER_AGENT` | `str` | `'DiscordBot/1.0 (https://github.com/placeholder; contact@placeholder.com)'` | L450 |
| `WEATHER_EXTRACT_TIMEOUT` | `float` | `10.0` | L455 |
| `WEATHER_REPLY_TIMEOUT` | `float` | `60.0` | L456 |
| `SAFEBOORU_HTTP_TIMEOUT` | `int` | `12` | L457 |
| `DANBOORU_HTTP_TIMEOUT` | `int` | `12` | L458 |
| `VISION_ANALYSIS_TIMEOUT` | `int` | `120` | L459 |
| `STOCK_EXTRACT_TIMEOUT` | `float` | `10.0` | L460 |
| `STOCK_REPLY_TIMEOUT` | `float` | `60.0` | L461 |
| `SCHEDULE_PARSE_TIMEOUT` | `float` | `20.0` | L462 |
| `DRAW_RAW_TIMEOUT` | `float` | `30.0` | L463 |
| `DRAW_EXPAND_TIMEOUT` | `float` | `10.0` | L464 |
| `INTERJECT_SCORE_TIMEOUT` | `float` | `30.0` | L465 |
| `INTERJECT_REPLY_TIMEOUT` | `float` | `60.0` | L466 |
| `YOUTUBE_EXPLORER_TIMEOUT` | `float` | `20.0` | L467 |
| `NIGHTLY_DIGEST_TIMEOUT` | `float` | `120.0` | L468 |
| `HEALTH_CHECK_TIMEOUT` | `float` | `5.0` | L469 |
| `DISCORD_UI_VIEW_TIMEOUT` | `int` | `300` | L470 |
| `ROAST_REPLY_TIMEOUT` | `float` | `float(os.getenv('ROAST_REPLY_TIMEOUT', '30.0'))` | L471 |
| `CONFIG_OVERRIDE_PATH` | `Path` | `DATA_DIR / 'config_override.json'` | L477 |
| `WEB_UI_PASSWORD` | `str` | `os.getenv('WEB_UI_PASSWORD', '1919810')` | L514 |
| `WEB_UI_MAX_LOGIN_ATTEMPTS` | `int` | `5` | L515 |
| `WEB_UI_LOCKOUT_MINUTES` | `int` | `15` | L516 |


### Functions

#### `update_setting`  (L518)

```python
def update_setting(key: str, value: Any) -> bool
```

> 更新全域設定值並持久化至 config_override.json。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `key` | `str` | `—` |
| `value` | `Any` | `—` |

#### `get_editable_settings`  (L563)

```python
def get_editable_settings() -> dict[str, Any]
```

> 回傳所有可編輯設定，含 meta 資訊（委託 web_ui.settings_schema 解析）。  \n>   \n>     Returns:  \n>         dict: key -> {value, label, description, type, category, ...constraints}


---


## 📄 `tests\unit\test_integrated_action_agent.py`

> ⚠️ **語法錯誤**：invalid non-printable character U+FEFF (test_integrated_action_agent.py, line 1)
