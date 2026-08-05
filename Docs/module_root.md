# 🏠 module_root — 根目錄模組文件

> 最後更新：2026-08-05 16:43  |  自動生成自 AST 掃描

涵蓋根目錄的所有 `.py` 檔案：`main.py`, `settings.py`, `build_tag_dict.py`

---


## 📄 `benchmark_nbatch_cache.py`

> benchmark_nbatch_cache.py


### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `OLLAMA_URL` | `—` | `'http://localhost:11434'` | L25 |
| `MODEL` | `—` | `'krith/mistral-nemo-instruct-2407-abliterated:IQ4_XS'` | L29 |
| `SYSTEM_PROMPT` | `—` | `'你是橘雪莉，一個 15 歲活潑可愛的台灣女高中生，同時自稱是名偵探。\n你說話風格生動活潑，喜歡用台灣年輕人的口氣，偶爾夾雜台灣俗語和流行語。\n你有強烈的好...` | L32 |
| `USER_PROMPT` | `—` | `'嗨雪莉！今天有沒有什麼有趣的事情發生啊？'` | L45 |


### Functions

#### `do_request`  (L49)

```python
async def do_request(session: aiohttp.ClientSession, keep_alive: str, num_batch: int, label: str) -> dict
```

> Send a single /api/chat request and return timing + Ollama metrics.

| 參數 | 型別 | 預設值 |
|---|---|---|
| `session` | `aiohttp.ClientSession` | `—` |
| `keep_alive` | `str` | `—` |
| `num_batch` | `int` | `—` |
| `label` | `str` | `—` |

#### `print_result`  (L103)

```python
def print_result(r: dict) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `r` | `dict` | `—` |

#### `main`  (L113)

```python
async def main() -> None
```


---


## 📄 `build_tag_dict.py`

> build_tag_dict.py


### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `OUTPUT_FILE` | `—` | `str(settings.PIXIV_TAG_PATH)` | L45 |
| `DANBOORU_MIN_POST_COUNT` | `—` | `settings.PIXIV_TAG_MIN_POST_COUNT` | L48 |
| `DANBOORU_MAX_PAGES` | `—` | `settings.PIXIV_TAG_MAX_PAGES` | L51 |
| `REQUEST_DELAY` | `—` | `settings.PIXIV_TAG_REQUEST_DELAY` | L54 |
| `SOURCES` | `dict[str, str]` | `{'byzod_zh': 'https://raw.githubusercontent.com/byzod/a1111-sd-webui-tagcomplete...` | L60 |
| `DANBOORU_API` | `—` | `settings.DANBOORU_API_URL` | L72 |
| `logger` | `—` | `logging.getLogger(__name__)` | L83 |
| `_CC` | `—` | `_make_converter()` | L100 |


### Functions

#### `_make_converter`  (L89)

```python
def _make_converter()
```

> 嘗試載入 OpenCC 簡轉繁，失敗時回傳 None。

#### `to_traditional`  (L102)

```python
def to_traditional(text: str) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `text` | `str` | `—` |

#### `_get`  (L109)

```python
def _get(url: str, timeout: int = 30) -> str
```

> 同步 HTTP GET，回傳 response body 字串。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `url` | `str` | `—` |
| `timeout` | `int` | `30` |

#### `_clean_zh`  (L122)

```python
def _clean_zh(raw: str) -> str
```

> 清洗中文字串：  \n>     - 簡轉繁  \n>     - 移除尾端括號注釋：「初音未來 (Vocaloid)」→「初音未來」  \n>     - 去除首尾空白

| 參數 | 型別 | 預設值 |
|---|---|---|
| `raw` | `str` | `—` |

#### `_parse_byzod_csv`  (L134)

```python
def _parse_byzod_csv(text: str) -> Iterator[tuple[str, str]]
```

> 解析 byzod 中文翻譯包 CSV。  \n>     格式：英文tag, 中文名稱[, 其他欄位...]  \n>     yield: (中文, 英文)

| 參數 | 型別 | 預設值 |
|---|---|---|
| `text` | `str` | `—` |

#### `_parse_e621_csv`  (L156)

```python
def _parse_e621_csv(text: str) -> Iterator[tuple[str, str]]
```

> 解析 e621 CSV（英文 tag 庫，無中文）。  \n>     格式：英文tag, 類型ID[, 其他欄位...]  \n>     這裡只建立「英文 → 英文」的備用條目，  \n>     主要目的是讓 byzod 能用英文 key 補充覆蓋。  \n>     實際上跳過純英文條目，只在被 byzod 引用時才有意義。  \n>   \n>     ※ e621 本身沒有中文，此函數保留給未來接中文翻譯層使用。  \n>        目前僅 log 統計用。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `text` | `str` | `—` |

#### `_fetch_danbooru_page`  (L176)

```python
def _fetch_danbooru_page(page: int) -> list[dict]
```

> 抓取 Danbooru tag 列表的一頁（1000 筆）。  \n>     回傳原始 JSON list。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `page` | `int` | `—` |

#### `_danbooru_name_to_zh`  (L192)

```python
def _danbooru_name_to_zh(name: str) -> str
```

> 將 Danbooru 英文 tag name 轉成「可讀的中文候選詞」。  \n>     這只是格式清洗，不是真正翻譯；真正的中文對照由 byzod 覆蓋。  \n>     規則：底線換空格，方便之後若要接翻譯 API 使用。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `name` | `str` | `—` |

#### `build`  (L205)

```python
def build() -> None
```


---


## 📄 `main.py`

> main.py


### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L43 |
| `EXTENSIONS` | `list[str]` | `['handlers.bot_brain', 'handlers.social', 'handlers.vibe_manager', 'handlers.mus...` | L48 |
| `BASE_DIR` | `Path` | `Path(__file__).resolve().parent` | L69 |
| `MOD_DIR` | `Path` | `BASE_DIR / 'mod'` | L70 |


### Functions

#### `_discover_optional_extensions`  (L73)

```python
def _discover_optional_extensions() -> list[str]
```

> 掃描 ./mod/ 目錄，自動找出含有 cog.py 的子資料夾。

#### `build_bot`  (L88)

```python
def build_bot() -> commands.Bot
```

> Construct and return the configured Bot instance.

#### `main`  (L106)

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
| `logger` | `—` | `logging.getLogger(__name__)` | L23 |
| `BASE_DIR` | `Path` | `Path(__file__).resolve().parent` | L36 |
| `DATA_DIR` | `Path` | `BASE_DIR / 'data'` | L37 |
| `FILE_DIR` | `Path` | `BASE_DIR / 'file'` | L38 |
| `SOUL_DIR` | `Path` | `FILE_DIR / 'soul'` | L39 |
| `DEFAULT_SOUL_PATH` | `Path` | `SOUL_DIR / 'sherry_default.md'` | L40 |
| `CHANNEL_SOUL_MAP` | `dict[int, str]` | `{}` | L42 |
| `HEARTBEAT_PATH` | `Path` | `DATA_DIR / 'heartbeat.md'` | L44 |
| `DEFAULT_HEARTBEAT_CHANNEL_ID` | `int` | `1519967448982487201` | L45 |
| `CHANNEL_VIBE_PATH` | `Path` | `DATA_DIR / 'channel_vibe.json'` | L47 |
| `DEEP_THINKING_LOG_PATH` | `Path` | `DATA_DIR / 'deep_thinking.json'` | L49 |
| `DRAW_THINKING_LOG_PATH` | `Path` | `DATA_DIR / 'draw_thinking.json'` | L50 |
| `USER_MEMORY_PATH` | `Path` | `DATA_DIR / 'user_memory.json'` | L52 |
| `USER_RELATIONS_PATH` | `Path` | `DATA_DIR / 'user_relations.json'` | L54 |
| `RUNTIME_LOG_PATH` | `Path` | `DATA_DIR / 'runtime_log.jsonl'` | L60 |
| `ERROR_LOG_PATH` | `Path` | `DATA_DIR / 'error_log.jsonl'` | L61 |
| `SCHEDULE_PATH` | `Path` | `BASE_DIR / 'data' / 'schedule.json'` | L67 |
| `THINKING_LOG_PATH` | `Path` | `DATA_DIR / 'thinking.json'` | L70 |
| `THINKING_LOG_DIR` | `Path` | `DATA_DIR / 'thinking'` | L71 |
| `STT_THINKING_LOG_PATH` | `Path` | `DATA_DIR / 'stt_thinking.log'` | L72 |
| `FFMPEG` | `—` | `str(BASE_DIR / 'ffmpeg')` | L77 |
| `DISCORD_BOT_TOKEN` | `str` | `os.environ['DISCORD_BOT_TOKEN']` | L82 |
| `ADMIN_ROLE_IDS` | `list[int]` | `[]` | L83 |
| `ADMIN_USER_IDS` | `list[int]` | `[425311733503229972]` | L84 |
| `NSFW_ALLOWED_CHANNEL_IDS` | `list[int]` | `[]` | L85 |
| `OLLAMA_BASE_URL` | `str` | `os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')` | L101 |
| `LMS_BASE_URL` | `str` | `os.getenv('LMS_BASE_URL', 'http://localhost:1234/v1')` | L102 |
| `VLLM_BASE_URL` | `str` | `os.getenv('VLLM_BASE_URL', 'http://localhost:8000/v1')` | L103 |
| `OPENAI_API_KEY` | `str` | `os.getenv('OPENAI_API_KEY', '')` | L106 |
| `DEEPSEEK_API_KEY` | `str` | `os.getenv('DEEPSEEK_API_KEY', '')` | L107 |
| `GEMINI_API_KEY` | `str` | `os.getenv('GEMINI_API_KEY', '')` | L108 |
| `CLAUDE_API_KEY` | `str` | `os.getenv('CLAUDE_API_KEY', '')` | L109 |
| `OPENAI_BASE_URL` | `str` | `os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1')` | L112 |
| `DEEPSEEK_BASE_URL` | `str` | `os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com/v1')` | L113 |
| `GEMINI_BASE_URL` | `str` | `os.getenv('GEMINI_BASE_URL', 'https://generativelanguage.googleapis.com/v1beta/o...` | L114 |
| `CLAUDE_BASE_URL` | `str` | `os.getenv('CLAUDE_BASE_URL', 'https://api.anthropic.com/v1')` | L115 |
| `PRIMARY_MODEL` | `str` | `'lms:gemma4-12b-qat-uncensored-hauhaucs-balanced'` | L118 |
| `NOMORAL_MODEL` | `str` | `'lms:gemma4-12b-qat-uncensored-hauhaucs-balanced'` | L121 |
| `QUICK_MODEL` | `str` | `'ollama:krith/mistral-nemo-instruct-2407-abliterated:IQ4_XS'` | L124 |
| `CLASSIFIER_MODEL` | `str` | `'ollama:qwen2.5:3b'` | L127 |
| `VIBE_MODEL` | `str` | `'ollama:llama3.2:3b'` | L130 |
| `VISION_MODEL` | `str` | `'ollama:minicpm-v'` | L133 |
| `CLOUD_SEARCH_MODEL` | `str` | `'deepseek:deepseek-v4-flash'` | L136 |
| `OLLAMA_TIMEOUT` | `int` | `120` | L138 |
| `LLM_MAX_CONTEXT_TOKENS` | `int` | `8192` | L139 |
| `LLM_MAX_OUTPUT_TOKENS` | `int` | `1024` | L140 |
| `LLM_NUM_BATCH` | `int` | `2048` | L144 |
| `OLLAMA_KEEP_ALIVE` | `str` | `'60m'` | L148 |
| `LLM_TEMPERATURE` | `float` | `0.85` | L151 |
| `LLM_PRESENCE_PENALTY` | `float` | `0.6` | L152 |
| `LLM_FREQUENCY_PENALTY` | `float` | `0.6` | L153 |
| `PRIORITY_TAG_RESPONSE` | `int` | `1` | L158 |
| `PRIORITY_INTERJECT` | `int` | `10` | L159 |
| `PRIORITY_HEARTBEAT` | `int` | `20` | L160 |
| `PRIORITY_VIBE_UPDATE` | `int` | `50` | L161 |
| `PRIORITY_DRAW` | `int` | `80` | L162 |
| `PRIORITY_WEB_EXPLORER` | `int` | `99` | L163 |
| `CONTEXT_HISTORY_LIMIT` | `int` | `30` | L168 |
| `CONTEXT_TIME_GAP_SECONDS` | `int` | `900` | L169 |
| `VIBE_UPDATE_MESSAGE_THRESHOLD` | `int` | `50` | L171 |
| `VIBE_TOP_N_TOPICS` | `int` | `3` | L172 |
| `VIBE_DECAY_INTERVAL_SECONDS` | `int` | `3600` | L173 |
| `VIBE_DECAY_AMOUNT` | `int` | `1` | L174 |
| `VIBE_MIN_WEIGHT_TO_INJECT` | `int` | `5` | L177 |
| `VIBE_REINFORCE_MAX_WEIGHT` | `int` | `10` | L180 |
| `TYPING_BASE_DELAY` | `float` | `1.0` | L186 |
| `TYPING_DELAY_PER_50_CHARS` | `float` | `0.3` | L187 |
| `_yt_key_raw` | `str` | `os.getenv('YOUTUBE_API_KEY', '')` | L192 |
| `YOUTUBE_API_KEY` | `str` | `_yt_key_raw if _yt_key_raw not in ('', 'your_youtube_api_key_here') else ''` | L193 |
| `OPENWEATHERMAP_API_KEY` | `str` | `os.getenv('OPENWEATHERMAP_API_KEY', '')` | L195 |
| `EXPLORER_INTERVAL_SECONDS` | `int` | `1800` | L197 |
| `YOUTUBE_MAX_RESULTS` | `int` | `5` | L198 |
| `EXPLORER_TARGET_CHANNEL_IDS` | `list[int]` | `[1522439581658648636]` | L199 |
| `INTERJECT_EVAL_THRESHOLD` | `int` | `10` | L206 |
| `INTERJECT_COOLDOWN_SECONDS` | `int` | `30` | L209 |
| `INTERJECT_MAX_CONSECUTIVE` | `int` | `3` | L213 |
| `INTERJECT_SCORE_THRESHOLD` | `int` | `7` | L216 |
| `RATE_LIMIT_MAX_REQUESTS` | `int` | `5` | L221 |
| `RATE_LIMIT_WINDOW_SECONDS` | `int` | `30` | L222 |
| `RATE_LIMIT_REPLY_MESSAGE` | `str` | `'名偵探現在很忙！{cooldown:.0f} 秒後再來！'` | L223 |
| `HEALTH_CHECK_INTERVAL_SECONDS` | `int` | `300` | L228 |
| `HEALTH_ALERT_CHANNEL_ID` | `int` | `1503226173386133574` | L229 |
| `DISCORD_LOG_LEVEL` | `str` | `'WARNING'` | L230 |
| `VOICE_STT_MODEL` | `str` | `str(BASE_DIR / 'models' / 'nemotron-3.5')` | L235 |
| `VOICE_STT_DEVICE` | `str` | `'cpu'` | L236 |
| `VOICE_STT_COMPUTE_TYPE` | `str` | `'float32'` | L237 |
| `VOICE_STT_LANGUAGE` | `str` | `'zh'` | L238 |
| `VOICE_TTS_MODEL_PATH` | `str` | `str(BASE_DIR / 'models' / 'xtts-v2')` | L239 |
| `VOICE_TTS_LANGUAGE` | `str` | `'zh-cn'` | L240 |
| `VOICE_TTS_SPEAKER_WAV` | `str` | `str(BASE_DIR / 'file' / 'shiry_voice_sample.wav')` | L241 |
| `VOICE_VAD_THRESHOLD` | `float` | `0.5` | L242 |
| `VOICE_VAD_MIN_SILENCE_MS` | `int` | `500` | L243 |
| `VOICE_SILENCE_TIMEOUT_SECONDS` | `float` | `1.5` | L244 |
| `VOICE_MAX_RECORD_SECONDS` | `int` | `30` | L245 |
| `POLLINATIONS_API_URL` | `str` | `'https://image.pollinations.ai/prompt'` | L250 |
| `POLLINATIONS_TIMEOUT_SECONDS` | `int` | `30` | L251 |
| `POLLINATIONS_MODEL` | `str` | `'flux-anime'` | L253 |
| `USE_ONLINE_IMAGE_MODEL` | `bool` | `os.getenv('USE_ONLINE_IMAGE_MODEL', 'True').lower() in ('true', '1', 'yes')` | L259 |
| `GEMINI_IMAGE_MODEL` | `str` | `os.getenv('GEMINI_IMAGE_MODEL', 'gemini-2.5-flash-image')` | L264 |
| `GEMINI_IMAGE_TIMEOUT_SECONDS` | `int` | `30` | L265 |
| `HF_TOKEN` | `str` | `os.getenv('HF_TOKEN', '')` | L270 |
| `HF_IMAGE_MODEL` | `str` | `'cagliostrolab/animagine-xl-3.1'` | L271 |
| `HF_IMAGE_TIMEOUT_SECONDS` | `int` | `120` | L272 |
| `SD_WEBUI_URL` | `str` | `'http://localhost:7860'` | L277 |
| `SD_DEFAULT_WIDTH` | `int` | `768` | L278 |
| `SD_DEFAULT_HEIGHT` | `int` | `1024` | L279 |
| `SD_DEFAULT_STEPS` | `int` | `20` | L280 |
| `SD_DEFAULT_CFG_SCALE` | `float` | `7.0` | L281 |
| `SD_DEFAULT_SAMPLER` | `str` | `'DPM++ 2M Karras'` | L282 |
| `SD_TIMEOUT_SECONDS` | `int` | `120` | L283 |
| `SD_NEGATIVE_PROMPT_BASE` | `str` | `'lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, fewe...` | L284 |
| `MICRO_COT_ENABLED` | `bool` | `True` | L292 |
| `CHAT_LOG_DIR` | `Path` | `DATA_DIR / 'chat_logs'` | L297 |
| `NIGHTLY_DIGEST_HOUR` | `int` | `3` | L298 |
| `NIGHTLY_DIGEST_MODEL` | `str` | `PRIMARY_MODEL` | L299 |
| `NIGHTLY_MAX_MSGS_PER_USER` | `int` | `200` | L300 |
| `WEB_UI_HOST` | `str` | `os.getenv('WEB_UI_HOST', '0.0.0.0')` | L305 |
| `WEB_UI_PORT` | `int` | `int(os.getenv('WEB_UI_PORT', '8080'))` | L306 |
| `WEB_UI_HTTPS` | `bool` | `True` | L307 |
| `WEB_UI_SSL_CERT` | `str | None` | `os.getenv('WEB_UI_SSL_CERT', None)` | L308 |
| `WEB_UI_SSL_KEY` | `str | None` | `os.getenv('WEB_UI_SSL_KEY', None)` | L309 |
| `WEB_UI_RADMIN_IP` | `str | None` | `os.getenv('WEB_UI_RADMIN_IP', '26.108.83.196')` | L310 |
| `WEB_UI_HTTP_TIMEOUT` | `float` | `5.0` | L311 |
| `YOUTUBE_COOKIES_PATH` | `Path` | `DATA_DIR / 'cookies.txt'` | L316 |
| `MUSIC_PLAYLIST_MAX_ITEMS` | `int` | `100` | L317 |
| `MUSIC_FFMPEG_BEFORE_OPTIONS` | `str` | `'-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -loglevel error'` | L318 |
| `MUSIC_FFMPEG_OPTIONS` | `str` | `'-vn -af "loudnorm=I=-14:LRA=11:TP=-1.5"'` | L319 |
| `MUSIC_QUEUE_VIEW_TIMEOUT` | `int` | `180` | L320 |
| `VOICE_STT_REPO_ID` | `str` | `'nvidia/nemotron-3.5-asr-streaming-0.6b'` | L322 |
| `VOICE_TTS_MODEL_NAME` | `str` | `'tts_models/multilingual/multi-dataset/xtts_v2'` | L323 |
| `VOICE_VAD_REPO_OR_DIR` | `str` | `'snakers4/silero-vad'` | L324 |
| `VOICE_VAD_MODEL_NAME` | `str` | `'silero_vad'` | L325 |
| `PIXIV_TAG_PATH` | `Path` | `FILE_DIR / 'pixiv_tag.json'` | L330 |
| `PIXIV_TAG_MIN_POST_COUNT` | `int` | `100` | L331 |
| `PIXIV_TAG_MAX_PAGES` | `int` | `60` | L332 |
| `PIXIV_TAG_REQUEST_DELAY` | `float` | `0.5` | L333 |
| `DANBOORU_API_URL` | `str` | `'https://danbooru.donmai.us/tags.json'` | L334 |
| `WORLD_LOG_DIR` | `Path` | `DATA_DIR / 'world_history'` | L336 |
| `WORLD_MEMORY_PATH` | `Path` | `DATA_DIR / 'world_memory.json'` | L337 |
| `BOT_LOG_PATH` | `Path` | `DATA_DIR / 'bot.log'` | L338 |
| `MEMORY_MAX_FACTS_IN_CONTEXT` | `int` | `5` | L343 |
| `MEMORY_MAX_FACTS_PER_USER` | `int` | `15` | L344 |
| `MEMORY_PERMANENT_FACT_THRESHOLD` | `int` | `4` | L345 |
| `NIGHTLY_MIN_MSGS_THRESHOLD` | `int` | `5` | L346 |
| `BOT_BRAIN_MAX_RETRIES` | `int` | `3` | L351 |
| `BOT_BRAIN_RETRY_DELAY` | `float` | `2.0` | L352 |
| `HEARTBEAT_CHECK_INTERVAL_MINUTES` | `int` | `30` | L357 |
| `HEARTBEAT_CONDITION_START_HOUR` | `int` | `9` | L358 |
| `HEARTBEAT_CONDITION_END_HOUR` | `int` | `22` | L359 |
| `HEARTBEAT_CONDITION_MIN_GAP_SECONDS` | `int` | `7200` | L360 |
| `OPENWEATHERMAP_API_URL` | `str` | `'https://api.openweathermap.org/data/2.5/weather'` | L365 |
| `SAFEBOORU_API_URL` | `str` | `'https://safebooru.org/index.php?page=dapi&s=post'` | L366 |
| `SAFEBOORU_IMAGE_BASE_URL` | `str` | `'https://safebooru.org/images/'` | L367 |
| `DUCKDUCKGO_SEARCH_URL` | `str` | `'https://html.duckduckgo.com/html/?q='` | L368 |
| `DEFAULT_HTTP_TIMEOUT` | `int` | `10` | L369 |
| `YOUTUBE_SEARCH_API_URL` | `str` | `'https://www.googleapis.com/youtube/v3/search'` | L370 |
| `YOUTUBE_RSS_FEED_URL` | `str` | `'https://www.youtube.com/feeds/videos.xml?channel_id='` | L371 |
| `YOUTUBE_RSS_NS` | `str` | `'http://www.w3.org/2005/Atom'` | L372 |
| `DEFAULT_USER_AGENT` | `str` | `'DiscordBot/1.0 (https://github.com/placeholder; contact@placeholder.com)'` | L373 |
| `WEATHER_EXTRACT_TIMEOUT` | `float` | `10.0` | L378 |
| `WEATHER_REPLY_TIMEOUT` | `float` | `60.0` | L379 |
| `SAFEBOORU_HTTP_TIMEOUT` | `int` | `12` | L380 |
| `VISION_ANALYSIS_TIMEOUT` | `int` | `30` | L381 |
| `STOCK_EXTRACT_TIMEOUT` | `float` | `10.0` | L382 |
| `STOCK_REPLY_TIMEOUT` | `float` | `60.0` | L383 |
| `SCHEDULE_PARSE_TIMEOUT` | `float` | `20.0` | L384 |
| `DRAW_RAW_TIMEOUT` | `float` | `30.0` | L385 |
| `DRAW_EXPAND_TIMEOUT` | `float` | `10.0` | L386 |
| `COMPOUND_PARSER_TIMEOUT` | `float` | `10.0` | L387 |
| `INTERJECT_SCORE_TIMEOUT` | `float` | `30.0` | L388 |
| `INTERJECT_REPLY_TIMEOUT` | `float` | `60.0` | L389 |
| `YOUTUBE_EXPLORER_TIMEOUT` | `float` | `20.0` | L390 |
| `NIGHTLY_DIGEST_TIMEOUT` | `float` | `120.0` | L391 |
| `HEALTH_CHECK_TIMEOUT` | `float` | `5.0` | L392 |
| `DISCORD_UI_VIEW_TIMEOUT` | `int` | `300` | L393 |
| `CONFIG_OVERRIDE_PATH` | `Path` | `DATA_DIR / 'config_override.json'` | L399 |
| `WEB_UI_PASSWORD` | `str` | `os.getenv('WEB_UI_PASSWORD', '1919810')` | L429 |
| `WEB_UI_MAX_LOGIN_ATTEMPTS` | `int` | `5` | L430 |
| `WEB_UI_LOCKOUT_MINUTES` | `int` | `15` | L431 |
| `SETTINGS_META` | `dict[str, dict]` | `{'PRIMARY_MODEL': {'label': '主力模型 (Primary Model)', 'description': '平衡智商與生成速度，用於...` | L473 |


### Functions

#### `update_setting`  (L433)

```python
def update_setting(key: str, value: any) -> bool
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `key` | `str` | `—` |
| `value` | `any` | `—` |

#### `get_editable_settings`  (L1473)

```python
def get_editable_settings() -> dict
```

> 回傳所有可編輯設定，含 meta 資訊（label、description、type、category 等）。  \n>   \n>     Returns:  \n>         dict: key → {value, label, description, type, category, ...constraints}


---
