# 🏠 module_root — 根目錄模組文件

> 最後更新：2026-08-03 17:38  |  自動生成自 AST 掃描

涵蓋根目錄的所有 `.py` 檔案：`main.py`, `settings.py`, `build_tag_dict.py`

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
| `BASE_DIR` | `Path` | `Path(__file__).resolve().parent` | L35 |
| `DATA_DIR` | `Path` | `BASE_DIR / 'data'` | L36 |
| `FILE_DIR` | `Path` | `BASE_DIR / 'file'` | L37 |
| `SOUL_DIR` | `Path` | `FILE_DIR / 'soul'` | L38 |
| `DEFAULT_SOUL_PATH` | `Path` | `SOUL_DIR / 'sherry_default.md'` | L39 |
| `CHANNEL_SOUL_MAP` | `dict[int, str]` | `{}` | L41 |
| `HEARTBEAT_PATH` | `Path` | `DATA_DIR / 'heartbeat.md'` | L43 |
| `DEFAULT_HEARTBEAT_CHANNEL_ID` | `int` | `1519967448982487201` | L44 |
| `CHANNEL_VIBE_PATH` | `Path` | `DATA_DIR / 'channel_vibe.json'` | L46 |
| `DEEP_THINKING_LOG_PATH` | `Path` | `DATA_DIR / 'deep_thinking.json'` | L48 |
| `DRAW_THINKING_LOG_PATH` | `Path` | `DATA_DIR / 'draw_thinking.json'` | L49 |
| `USER_MEMORY_PATH` | `Path` | `DATA_DIR / 'user_memory.json'` | L51 |
| `USER_RELATIONS_PATH` | `Path` | `DATA_DIR / 'user_relations.json'` | L53 |
| `RUNTIME_LOG_PATH` | `Path` | `DATA_DIR / 'runtime_log.jsonl'` | L59 |
| `ERROR_LOG_PATH` | `Path` | `DATA_DIR / 'error_log.jsonl'` | L60 |
| `SCHEDULE_PATH` | `Path` | `BASE_DIR / 'data' / 'schedule.json'` | L66 |
| `THINKING_LOG_PATH` | `Path` | `DATA_DIR / 'thinking.json'` | L69 |
| `THINKING_LOG_DIR` | `Path` | `DATA_DIR / 'thinking'` | L70 |
| `STT_THINKING_LOG_PATH` | `Path` | `DATA_DIR / 'stt_thinking.log'` | L71 |
| `FFMPEG` | `—` | `str(BASE_DIR / 'ffmpeg')` | L76 |
| `DISCORD_BOT_TOKEN` | `str` | `os.environ['DISCORD_BOT_TOKEN']` | L81 |
| `ADMIN_ROLE_IDS` | `list[int]` | `[]` | L82 |
| `ADMIN_USER_IDS` | `list[int]` | `[425311733503229972]` | L83 |
| `NSFW_ALLOWED_CHANNEL_IDS` | `list[int]` | `[]` | L84 |
| `OLLAMA_BASE_URL` | `str` | `os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')` | L100 |
| `LMS_BASE_URL` | `str` | `os.getenv('LMS_BASE_URL', 'http://localhost:1234/v1')` | L101 |
| `OPENAI_API_KEY` | `str` | `os.getenv('OPENAI_API_KEY', '')` | L104 |
| `DEEPSEEK_API_KEY` | `str` | `os.getenv('DEEPSEEK_API_KEY', '')` | L105 |
| `GEMINI_API_KEY` | `str` | `os.getenv('GEMINI_API_KEY', '')` | L106 |
| `CLAUDE_API_KEY` | `str` | `os.getenv('CLAUDE_API_KEY', '')` | L107 |
| `OPENAI_BASE_URL` | `str` | `os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1')` | L110 |
| `DEEPSEEK_BASE_URL` | `str` | `os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com/v1')` | L111 |
| `GEMINI_BASE_URL` | `str` | `os.getenv('GEMINI_BASE_URL', 'https://generativelanguage.googleapis.com/v1beta/o...` | L112 |
| `CLAUDE_BASE_URL` | `str` | `os.getenv('CLAUDE_BASE_URL', 'https://api.anthropic.com/v1')` | L113 |
| `PRIMARY_MODEL` | `str` | `'lms:huihui-gemma-4-12b-it-abliterated'` | L116 |
| `NOMORAL_MODEL` | `str` | `'lms:huihui-gemma-4-12b-it-abliterated'` | L119 |
| `QUICK_MODEL` | `str` | `'ollama:krith/mistral-nemo-instruct-2407-abliterated:IQ4_XS'` | L122 |
| `CLASSIFIER_MODEL` | `str` | `'ollama:qwen2.5:3b'` | L125 |
| `VIBE_MODEL` | `str` | `'ollama:llama3.2:3b'` | L128 |
| `VISION_MODEL` | `str` | `'ollama:minicpm-v'` | L131 |
| `CLOUD_SEARCH_MODEL` | `str` | `'deepseek:deepseek-v4-flash'` | L134 |
| `OLLAMA_TIMEOUT` | `int` | `120` | L136 |
| `LLM_MAX_CONTEXT_TOKENS` | `int` | `8192` | L137 |
| `LLM_MAX_OUTPUT_TOKENS` | `int` | `1024` | L138 |
| `LLM_TEMPERATURE` | `float` | `0.85` | L141 |
| `LLM_PRESENCE_PENALTY` | `float` | `0.6` | L142 |
| `LLM_FREQUENCY_PENALTY` | `float` | `0.6` | L143 |
| `PRIORITY_TAG_RESPONSE` | `int` | `1` | L148 |
| `PRIORITY_INTERJECT` | `int` | `10` | L149 |
| `PRIORITY_HEARTBEAT` | `int` | `20` | L150 |
| `PRIORITY_VIBE_UPDATE` | `int` | `50` | L151 |
| `PRIORITY_DRAW` | `int` | `80` | L152 |
| `PRIORITY_WEB_EXPLORER` | `int` | `99` | L153 |
| `CONTEXT_HISTORY_LIMIT` | `int` | `30` | L158 |
| `CONTEXT_TIME_GAP_SECONDS` | `int` | `900` | L159 |
| `VIBE_UPDATE_MESSAGE_THRESHOLD` | `int` | `50` | L161 |
| `VIBE_TOP_N_TOPICS` | `int` | `3` | L162 |
| `VIBE_DECAY_INTERVAL_SECONDS` | `int` | `3600` | L163 |
| `VIBE_DECAY_AMOUNT` | `int` | `1` | L164 |
| `VIBE_MIN_WEIGHT_TO_INJECT` | `int` | `5` | L167 |
| `VIBE_REINFORCE_MAX_WEIGHT` | `int` | `10` | L170 |
| `TYPING_BASE_DELAY` | `float` | `1.0` | L176 |
| `TYPING_DELAY_PER_50_CHARS` | `float` | `0.3` | L177 |
| `_yt_key_raw` | `str` | `os.getenv('YOUTUBE_API_KEY', '')` | L182 |
| `YOUTUBE_API_KEY` | `str` | `_yt_key_raw if _yt_key_raw not in ('', 'your_youtube_api_key_here') else ''` | L183 |
| `OPENWEATHERMAP_API_KEY` | `str` | `os.getenv('OPENWEATHERMAP_API_KEY', '')` | L185 |
| `EXPLORER_INTERVAL_SECONDS` | `int` | `1800` | L187 |
| `YOUTUBE_MAX_RESULTS` | `int` | `5` | L188 |
| `EXPLORER_TARGET_CHANNEL_IDS` | `list[int]` | `[1522439581658648636]` | L189 |
| `INTERJECT_EVAL_THRESHOLD` | `int` | `10` | L196 |
| `INTERJECT_COOLDOWN_SECONDS` | `int` | `30` | L199 |
| `INTERJECT_MAX_CONSECUTIVE` | `int` | `3` | L203 |
| `INTERJECT_SCORE_THRESHOLD` | `int` | `7` | L206 |
| `RATE_LIMIT_MAX_REQUESTS` | `int` | `5` | L211 |
| `RATE_LIMIT_WINDOW_SECONDS` | `int` | `30` | L212 |
| `RATE_LIMIT_REPLY_MESSAGE` | `str` | `'名偵探現在很忙！{cooldown:.0f} 秒後再來！'` | L213 |
| `HEALTH_CHECK_INTERVAL_SECONDS` | `int` | `300` | L218 |
| `HEALTH_ALERT_CHANNEL_ID` | `int` | `1503226173386133574` | L219 |
| `DISCORD_LOG_LEVEL` | `str` | `'WARNING'` | L220 |
| `VOICE_STT_MODEL` | `str` | `str(BASE_DIR / 'models' / 'nemotron-3.5')` | L225 |
| `VOICE_STT_DEVICE` | `str` | `'cpu'` | L226 |
| `VOICE_STT_COMPUTE_TYPE` | `str` | `'float32'` | L227 |
| `VOICE_STT_LANGUAGE` | `str` | `'zh'` | L228 |
| `VOICE_TTS_MODEL_PATH` | `str` | `str(BASE_DIR / 'models' / 'xtts-v2')` | L229 |
| `VOICE_TTS_LANGUAGE` | `str` | `'zh-cn'` | L230 |
| `VOICE_TTS_SPEAKER_WAV` | `str` | `str(BASE_DIR / 'file' / 'shiry_voice_sample.wav')` | L231 |
| `VOICE_VAD_THRESHOLD` | `float` | `0.5` | L232 |
| `VOICE_VAD_MIN_SILENCE_MS` | `int` | `500` | L233 |
| `VOICE_SILENCE_TIMEOUT_SECONDS` | `float` | `1.5` | L234 |
| `VOICE_MAX_RECORD_SECONDS` | `int` | `30` | L235 |
| `POLLINATIONS_API_URL` | `str` | `'https://image.pollinations.ai/prompt'` | L240 |
| `POLLINATIONS_TIMEOUT_SECONDS` | `int` | `30` | L241 |
| `POLLINATIONS_MODEL` | `str` | `'flux-anime'` | L243 |
| `USE_ONLINE_IMAGE_MODEL` | `bool` | `os.getenv('USE_ONLINE_IMAGE_MODEL', 'True').lower() in ('true', '1', 'yes')` | L249 |
| `GEMINI_IMAGE_MODEL` | `str` | `os.getenv('GEMINI_IMAGE_MODEL', 'gemini-2.5-flash-image')` | L254 |
| `GEMINI_IMAGE_TIMEOUT_SECONDS` | `int` | `30` | L255 |
| `HF_TOKEN` | `str` | `os.getenv('HF_TOKEN', '')` | L260 |
| `HF_IMAGE_MODEL` | `str` | `'cagliostrolab/animagine-xl-3.1'` | L261 |
| `HF_IMAGE_TIMEOUT_SECONDS` | `int` | `120` | L262 |
| `SD_WEBUI_URL` | `str` | `'http://localhost:7860'` | L267 |
| `SD_DEFAULT_WIDTH` | `int` | `768` | L268 |
| `SD_DEFAULT_HEIGHT` | `int` | `1024` | L269 |
| `SD_DEFAULT_STEPS` | `int` | `20` | L270 |
| `SD_DEFAULT_CFG_SCALE` | `float` | `7.0` | L271 |
| `SD_DEFAULT_SAMPLER` | `str` | `'DPM++ 2M Karras'` | L272 |
| `SD_TIMEOUT_SECONDS` | `int` | `120` | L273 |
| `SD_NEGATIVE_PROMPT_BASE` | `str` | `'lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, fewe...` | L274 |
| `MICRO_COT_ENABLED` | `bool` | `True` | L282 |
| `CHAT_LOG_DIR` | `Path` | `DATA_DIR / 'chat_logs'` | L287 |
| `NIGHTLY_DIGEST_HOUR` | `int` | `3` | L288 |
| `NIGHTLY_DIGEST_MODEL` | `str` | `PRIMARY_MODEL` | L289 |
| `NIGHTLY_MAX_MSGS_PER_USER` | `int` | `200` | L290 |
| `WEB_UI_HOST` | `str` | `os.getenv('WEB_UI_HOST', '0.0.0.0')` | L295 |
| `WEB_UI_PORT` | `int` | `int(os.getenv('WEB_UI_PORT', '8080'))` | L296 |
| `WEB_UI_HTTPS` | `bool` | `True` | L297 |
| `WEB_UI_SSL_CERT` | `str | None` | `os.getenv('WEB_UI_SSL_CERT', None)` | L298 |
| `WEB_UI_SSL_KEY` | `str | None` | `os.getenv('WEB_UI_SSL_KEY', None)` | L299 |
| `WEB_UI_RADMIN_IP` | `str | None` | `os.getenv('WEB_UI_RADMIN_IP', '26.108.83.196')` | L300 |
| `WEB_UI_HTTP_TIMEOUT` | `float` | `5.0` | L301 |
| `YOUTUBE_COOKIES_PATH` | `Path` | `DATA_DIR / 'cookies.txt'` | L306 |
| `MUSIC_PLAYLIST_MAX_ITEMS` | `int` | `100` | L307 |
| `MUSIC_FFMPEG_BEFORE_OPTIONS` | `str` | `'-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'` | L308 |
| `MUSIC_FFMPEG_OPTIONS` | `str` | `'-vn -af "loudnorm=I=-14:LRA=11:TP=-1.5"'` | L309 |
| `MUSIC_QUEUE_VIEW_TIMEOUT` | `int` | `180` | L310 |
| `VOICE_STT_REPO_ID` | `str` | `'nvidia/nemotron-3.5-asr-streaming-0.6b'` | L312 |
| `VOICE_TTS_MODEL_NAME` | `str` | `'tts_models/multilingual/multi-dataset/xtts_v2'` | L313 |
| `VOICE_VAD_REPO_OR_DIR` | `str` | `'snakers4/silero-vad'` | L314 |
| `VOICE_VAD_MODEL_NAME` | `str` | `'silero_vad'` | L315 |
| `PIXIV_TAG_PATH` | `Path` | `FILE_DIR / 'pixiv_tag.json'` | L320 |
| `PIXIV_TAG_MIN_POST_COUNT` | `int` | `100` | L321 |
| `PIXIV_TAG_MAX_PAGES` | `int` | `60` | L322 |
| `PIXIV_TAG_REQUEST_DELAY` | `float` | `0.5` | L323 |
| `DANBOORU_API_URL` | `str` | `'https://danbooru.donmai.us/tags.json'` | L324 |
| `WORLD_LOG_DIR` | `Path` | `DATA_DIR / 'world_history'` | L326 |
| `WORLD_MEMORY_PATH` | `Path` | `DATA_DIR / 'world_memory.json'` | L327 |
| `BOT_LOG_PATH` | `Path` | `DATA_DIR / 'bot.log'` | L328 |
| `MEMORY_MAX_FACTS_IN_CONTEXT` | `int` | `5` | L333 |
| `MEMORY_MAX_FACTS_PER_USER` | `int` | `15` | L334 |
| `MEMORY_PERMANENT_FACT_THRESHOLD` | `int` | `4` | L335 |
| `NIGHTLY_MIN_MSGS_THRESHOLD` | `int` | `5` | L336 |
| `BOT_BRAIN_MAX_RETRIES` | `int` | `3` | L341 |
| `BOT_BRAIN_RETRY_DELAY` | `float` | `2.0` | L342 |
| `HEARTBEAT_CHECK_INTERVAL_MINUTES` | `int` | `30` | L347 |
| `HEARTBEAT_CONDITION_START_HOUR` | `int` | `9` | L348 |
| `HEARTBEAT_CONDITION_END_HOUR` | `int` | `22` | L349 |
| `HEARTBEAT_CONDITION_MIN_GAP_SECONDS` | `int` | `7200` | L350 |
| `OPENWEATHERMAP_API_URL` | `str` | `'https://api.openweathermap.org/data/2.5/weather'` | L355 |
| `SAFEBOORU_API_URL` | `str` | `'https://safebooru.org/index.php?page=dapi&s=post'` | L356 |
| `SAFEBOORU_IMAGE_BASE_URL` | `str` | `'https://safebooru.org/images/'` | L357 |
| `DUCKDUCKGO_SEARCH_URL` | `str` | `'https://html.duckduckgo.com/html/?q='` | L358 |
| `DEFAULT_HTTP_TIMEOUT` | `int` | `10` | L359 |
| `YOUTUBE_SEARCH_API_URL` | `str` | `'https://www.googleapis.com/youtube/v3/search'` | L360 |
| `YOUTUBE_RSS_FEED_URL` | `str` | `'https://www.youtube.com/feeds/videos.xml?channel_id='` | L361 |
| `YOUTUBE_RSS_NS` | `str` | `'http://www.w3.org/2005/Atom'` | L362 |
| `DEFAULT_USER_AGENT` | `str` | `'DiscordBot/1.0 (https://github.com/placeholder; contact@placeholder.com)'` | L363 |
| `WEATHER_EXTRACT_TIMEOUT` | `float` | `10.0` | L368 |
| `WEATHER_REPLY_TIMEOUT` | `float` | `60.0` | L369 |
| `SAFEBOORU_HTTP_TIMEOUT` | `int` | `12` | L370 |
| `VISION_ANALYSIS_TIMEOUT` | `int` | `30` | L371 |
| `STOCK_EXTRACT_TIMEOUT` | `float` | `10.0` | L372 |
| `STOCK_REPLY_TIMEOUT` | `float` | `60.0` | L373 |
| `SCHEDULE_PARSE_TIMEOUT` | `float` | `20.0` | L374 |
| `DRAW_RAW_TIMEOUT` | `float` | `30.0` | L375 |
| `DRAW_EXPAND_TIMEOUT` | `float` | `10.0` | L376 |
| `COMPOUND_PARSER_TIMEOUT` | `float` | `10.0` | L377 |
| `INTERJECT_SCORE_TIMEOUT` | `float` | `30.0` | L378 |
| `INTERJECT_REPLY_TIMEOUT` | `float` | `60.0` | L379 |
| `YOUTUBE_EXPLORER_TIMEOUT` | `float` | `20.0` | L380 |
| `NIGHTLY_DIGEST_TIMEOUT` | `float` | `120.0` | L381 |
| `HEALTH_CHECK_TIMEOUT` | `float` | `5.0` | L382 |
| `DISCORD_UI_VIEW_TIMEOUT` | `int` | `300` | L383 |
| `CONFIG_OVERRIDE_PATH` | `Path` | `DATA_DIR / 'config_override.json'` | L389 |
| `WEB_UI_PASSWORD` | `str` | `os.getenv('WEB_UI_PASSWORD', '1919810')` | L406 |
| `WEB_UI_MAX_LOGIN_ATTEMPTS` | `int` | `5` | L407 |
| `WEB_UI_LOCKOUT_MINUTES` | `int` | `15` | L408 |
| `SETTINGS_META` | `dict[str, dict]` | `{'PRIMARY_MODEL': {'label': '主力模型 (Primary Model)', 'description': '平衡智商與生成速度，用於...` | L450 |


### Functions

#### `update_setting`  (L410)

```python
def update_setting(key: str, value: any) -> bool
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `key` | `str` | `—` |
| `value` | `any` | `—` |

#### `get_editable_settings`  (L1436)

```python
def get_editable_settings() -> dict
```

> 回傳所有可編輯設定，含 meta 資訊（label、description、type、category 等）。  \n>   \n>     Returns:  \n>         dict: key → {value, label, description, type, category, ...constraints}


---
