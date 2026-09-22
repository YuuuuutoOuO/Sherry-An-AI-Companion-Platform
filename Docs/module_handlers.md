# ⚙️ module_handlers — `handlers/` 所有子模組文件

> 最後更新：2026-09-22 14:34  |  自動生成自 AST 掃描

## 📑 子模組目錄

- [bot_brain](#bot_brain)
- [social](#social)
- [memory](#memory)
- [vibe_manager](#vibe_manager)
- [music](#music)
- [voice](#voice)
- [admin](#admin)
- [executor](#executor)

---

## 📄 handlers/ 頂層檔案


### 📄 `handlers/__init__.py`

> handlers package — all Cog modules for the Discord AI Agent.


---


### 📄 `handlers/help_cog.py`

> handlers/help_cog.py


#### Class `HelpSelect` ← `discord.ui.Select`  (L17)

> 互動式說明手冊分類選單。

##### Methods

###### `__init__`  (L20)

```python
def __init__(self) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `callback`  (L35)

```python
async def callback(self, interaction: discord.Interaction) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |


#### Class `HelpView` ← `discord.ui.View`  (L262)

> 說明面板 View。

##### Methods

###### `__init__`  (L265)

```python
def __init__(self) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |


#### Class `HelpCog` ← `commands.Cog`  (L270)

> 提供 /help 全功能使用說明指南。

##### Methods

###### `__init__`  (L273)

```python
def __init__(self, bot: commands.Bot) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `bot` | `commands.Bot` | `—` |

###### `help_cmd`  (L277)

```python
@app_commands.command(name='help', description='顯示雪莉的所有功能說明與指令大全')
async def help_cmd(self, interaction: discord.Interaction) -> None
```

> 顯示雪莉全功能指南首頁。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |


#### Functions

##### `setup`  (L300)

```python
async def setup(bot: commands.Bot) -> None
```

> 掛載 HelpCog 擴展。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `bot` | `commands.Bot` | `—` |


---


### 📄 `handlers/schedule_storage.py`

> handlers/schedule_storage.py


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L19 |
| `DB_PATH` | `Path` | `settings.DATA_DIR / 'schedules.db'` | L21 |
| `_lock` | `—` | `asyncio.Lock()` | L22 |


#### Functions

##### `_init_schedule_db`  (L25)

```python
def _init_schedule_db() -> None
```

> 初始化 schedules.db 資料表與索引。

##### `_row_to_dict`  (L51)

```python
def _row_to_dict(r: Any) -> dict[str, Any]
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `r` | `Any` | `—` |

##### `load_all_tasks`  (L65)

```python
async def load_all_tasks() -> list[dict[str, Any]]
```

> 取得所有排程任務（WAL 模式多連線並發讀取）。

##### `load_tasks_by_user`  (L84)

```python
async def load_tasks_by_user(user_id: str) -> list[dict[str, Any]]
```

> 取得指定使用者的所有排程任務（WAL 模式多連線並發讀取）。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `user_id` | `str` | `—` |

##### `add_task`  (L103)

```python
async def add_task(user_id: str, channel_id: str, schedule_type: str, content: str, display_name: str = '', trigger_time: int | None = None, cron_expr: str | None = None) -> dict[str, Any]
```

> 新增一個排程任務。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `user_id` | `str` | `—` |
| `channel_id` | `str` | `—` |
| `schedule_type` | `str` | `—` |
| `content` | `str` | `—` |
| `display_name` | `str` | `''` |
| `trigger_time` | `int | None` | `None` |
| `cron_expr` | `str | None` | `None` |

##### `remove_task`  (L154)

```python
async def remove_task(task_id: str) -> bool
```

> 移除指定排程任務。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `task_id` | `str` | `—` |

##### `purge_once_tasks_before`  (L172)

```python
async def purge_once_tasks_before(ts: int) -> list[dict[str, Any]]
```

> 移除所有 trigger_time <= ts 的單次任務並回傳（單一原子交易）。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `ts` | `int` | `—` |


---


### 📄 `handlers/welcome_cog.py`


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L14 |


#### Class `WelcomeCog` ← `commands.Cog`  (L17)

> 新進成員歡迎模組，動態生成符合雪莉人設的歡迎問候語。

##### Methods

###### `__init__`  (L20)

```python
def __init__(self, bot: commands.Bot) -> None
```

> 初始化 WelcomeCog 模組。  \n>   \n>         Args:  \n>             bot: Discord Bot 實例。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `bot` | `commands.Bot` | `—` |

###### `on_member_join`  (L29)

```python
@commands.Cog.listener()
async def on_member_join(self, member: discord.Member) -> None
```

> 監聽成員加入伺服器事件並生成歡迎詞。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `member` | `discord.Member` | `—` |


#### Functions

##### `setup`  (L82)

```python
async def setup(bot: commands.Bot) -> None
```

> 載入 WelcomeCog 模組。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `bot` | `commands.Bot` | `—` |


---


<a id="bot_brain"></a>

## 🧠 `handlers/bot_brain/`


### 📄 `handlers/bot_brain/__init__.py`


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `__all__` | `—` | `['InferenceTask', 'BotBrain', 'OllamaClient', 'HealthMonitor', 'setup']` | L10 |


#### Functions

##### `setup`  (L13)

```python
async def setup(bot: commands.Bot) -> None
```

> 掛載機制。從底下的 cog 文件把真的組件找上來送去 main 的裝備清單之中。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `bot` | `commands.Bot` | `—` |


---


### 📄 `handlers/bot_brain/client.py`

> handlers/bot_brain/client.py


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L37 |


#### Class `OllamaClient` ← `object`  (L40)

> Async HTTP client for local & cloud inference backends.

    Supports prefix-based routing across FreeToken, Ollama, LM Studio, vLLM,
    OpenAI, DeepSeek, Gemini, OpenRouter, and Claude.

##### Methods

###### `__init__`  (L47)

```python
def __init__(self, base_url: str | None = None, freetoken_base_url: str | None = None, lms_base_url: str | None = None, openai_base_url: str | None = None, openai_api_key: str | None = None, deepseek_base_url: str | None = None, deepseek_api_key: str | None = None, gemini_base_url: str | None = None, gemini_api_key: str | None = None, claude_base_url: str | None = None, claude_api_key: str | None = None, openrouter_base_url: str | None = None, openrouter_api_key: str | None = None) -> None
```

> Initialize the inference client with optional override parameters.  \n>   \n>         Args:  \n>             base_url: Custom Ollama base URL override.  \n>             freetoken_base_url: Custom FreeToken base URL override.  \n>             lms_base_url: Custom LM Studio base URL override.  \n>             openai_base_url: Custom OpenAI base URL override.  \n>             openai_api_key: Custom OpenAI API Key.  \n>             deepseek_base_url: Custom DeepSeek base URL override.  \n>             deepseek_api_key: Custom DeepSeek API Key.  \n>             gemini_base_url: Custom Gemini base URL override.  \n>             gemini_api_key: Custom Gemini API Key.  \n>             claude_base_url: Custom Claude base URL override.  \n>             claude_api_key: Custom Claude API Key.  \n>             openrouter_base_url: Custom OpenRouter base URL override.  \n>             openrouter_api_key: Custom OpenRouter API Key.

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `base_url` | `str | None` | `None` |
| `freetoken_base_url` | `str | None` | `None` |
| `lms_base_url` | `str | None` | `None` |
| `openai_base_url` | `str | None` | `None` |
| `openai_api_key` | `str | None` | `None` |
| `deepseek_base_url` | `str | None` | `None` |
| `deepseek_api_key` | `str | None` | `None` |
| `gemini_base_url` | `str | None` | `None` |
| `gemini_api_key` | `str | None` | `None` |
| `claude_base_url` | `str | None` | `None` |
| `claude_api_key` | `str | None` | `None` |
| `openrouter_base_url` | `str | None` | `None` |
| `openrouter_api_key` | `str | None` | `None` |

###### `_base_url`  (L101)

```python
@property
def _base_url(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `_freetoken_base_url`  (L106)

```python
@property
def _freetoken_base_url(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `_lms_base_url`  (L114)

```python
@property
def _lms_base_url(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `_vllm_base_url`  (L122)

```python
@property
def _vllm_base_url(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `_openai_base_url`  (L130)

```python
@property
def _openai_base_url(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `_openai_api_key`  (L135)

```python
@property
def _openai_api_key(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `_deepseek_base_url`  (L139)

```python
@property
def _deepseek_base_url(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `_deepseek_api_key`  (L144)

```python
@property
def _deepseek_api_key(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `_gemini_base_url`  (L148)

```python
@property
def _gemini_base_url(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `_gemini_api_key`  (L153)

```python
@property
def _gemini_api_key(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `_claude_base_url`  (L157)

```python
@property
def _claude_base_url(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `_claude_api_key`  (L162)

```python
@property
def _claude_api_key(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `_openrouter_base_url`  (L166)

```python
@property
def _openrouter_base_url(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `_openrouter_api_key`  (L171)

```python
@property
def _openrouter_api_key(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `_get_session`  (L174)

```python
async def _get_session(self) -> aiohttp.ClientSession
```

> Get or lazily initialize the shared aiohttp.ClientSession.

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `_parse_model`  (L190)

```python
@staticmethod
def _parse_model(model: str) -> tuple[str, str]
```

> Parse model identifier into (backend_key, actual_model_name).  \n>   \n>         Args:  \n>             model: Full model string with optional prefix (e.g. 'freetoken:mistralai/...').  \n>   \n>         Returns:  \n>             tuple[str, str]: Backend key and actual model name.

| 參數 | 型別 | 預設值 |
|---|---|---|
| `model` | `str` | `—` |

###### `_resolve_openai_backend`  (L220)

```python
def _resolve_openai_backend(self, backend: str) -> tuple[str, str, dict[str, str], str]
```

> Resolve base_url, api_key, headers, and label for an OpenAI-compatible backend.  \n>   \n>         Args:  \n>             backend: The backend key (e.g. 'freetoken', 'openai', etc.).  \n>   \n>         Returns:  \n>             tuple[str, str, dict[str, str], str]: (base_url, api_key, extra_headers, backend_label)

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `backend` | `str` | `—` |

###### `_build_openai_messages`  (L257)

```python
@staticmethod
def _build_openai_messages(system_prompt: str, user_prompt: str, images: list[str] | None = None) -> list[dict[str, Any]]
```

> Construct OpenAI chat messages with optional multimodal image data.  \n>   \n>         Args:  \n>             system_prompt: System instructions.  \n>             user_prompt: User input.  \n>             images: Optional list of base64 encoded image strings.  \n>   \n>         Returns:  \n>             list[dict[str, Any]]: Formatted OpenAI message list.

| 參數 | 型別 | 預設值 |
|---|---|---|
| `system_prompt` | `str` | `—` |
| `user_prompt` | `str` | `—` |
| `images` | `list[str] | None` | `None` |

###### `_stream_openai_compatible`  (L292)

```python
async def _stream_openai_compatible(self, session: aiohttp.ClientSession, base_url: str, api_key: str, actual_model: str, system_prompt: str, user_prompt: str, backend_label: str, extra_headers: dict[str, str] | None = None, images: list[str] | None = None, temperature: float | None = None, max_tokens: int | None = None, timeout: float | None = None) -> AsyncIterator[str]
```

> Stream chat completions from an OpenAI-compatible endpoint using SSE (stream=True).  \n>   \n>         Args:  \n>             session: Active aiohttp ClientSession.  \n>             base_url: Target base URL.  \n>             api_key: Bearer token (if any).  \n>             actual_model: Model name/ID.  \n>             system_prompt: System instructions.  \n>             user_prompt: User input.  \n>             backend_label: Label for structured logging.  \n>             extra_headers: Optional additional request headers.  \n>             images: Optional base64 encoded images.  \n>             temperature: Sampling temperature override.  \n>             max_tokens: Maximum completion tokens override.  \n>             timeout: Request timeout override in seconds.  \n>   \n>         Yields:  \n>             str: Token delta strings as received.  \n>   \n>         Raises:  \n>             aiohttp.ClientResponseError: If server responds with HTTP error code.  \n>             ValueError: If response format is invalid.

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `session` | `aiohttp.ClientSession` | `—` |
| `base_url` | `str` | `—` |
| `api_key` | `str` | `—` |
| `actual_model` | `str` | `—` |
| `system_prompt` | `str` | `—` |
| `user_prompt` | `str` | `—` |
| `backend_label` | `str` | `—` |
| `extra_headers` | `dict[str, str] | None` | `None` |
| `images` | `list[str] | None` | `None` |
| `temperature` | `float | None` | `None` |
| `max_tokens` | `int | None` | `None` |
| `timeout` | `float | None` | `None` |

###### `_estimate_tokens`  (L522)

```python
@staticmethod
def _estimate_tokens(system_prompt: str, user_prompt: str) -> int
```

> 估算混合中英文本的真實 token 數（引擎未送回 usage 時使用）。  \n>   \n>         舊版用 1.5 tokens/字 虛胖估算（純中文約 0.75、英文約 4 字/token），  \n>         差到 2 倍以上，會誤導對 context／KV 容量的判斷。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `system_prompt` | `str` | `—` |
| `user_prompt` | `str` | `—` |

###### `_request_openai_compatible`  (L533)

```python
async def _request_openai_compatible(self, session: aiohttp.ClientSession, base_url: str, api_key: str, actual_model: str, system_prompt: str, user_prompt: str, backend_label: str, extra_headers: dict[str, str] | None = None, images: list[str] | None = None, temperature: float | None = None, max_tokens: int | None = None, timeout: float | None = None) -> str
```

> Send a chat completion request to any OpenAI-compatible endpoint with enforced streaming.  \n>   \n>         Args:  \n>             session: Active aiohttp ClientSession.  \n>             base_url: Target base URL.  \n>             api_key: Bearer token (if any).  \n>             actual_model: Model name/ID.  \n>             system_prompt: System instructions.  \n>             user_prompt: User input.  \n>             backend_label: Label for structured logging.  \n>             extra_headers: Optional additional request headers.  \n>             images: Optional base64 encoded images.  \n>             temperature: Sampling temperature override.  \n>             max_tokens: Maximum completion tokens override.  \n>             timeout: Request timeout override in seconds.  \n>   \n>         Returns:  \n>             str: Assembled full completion text.

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `session` | `aiohttp.ClientSession` | `—` |
| `base_url` | `str` | `—` |
| `api_key` | `str` | `—` |
| `actual_model` | `str` | `—` |
| `system_prompt` | `str` | `—` |
| `user_prompt` | `str` | `—` |
| `backend_label` | `str` | `—` |
| `extra_headers` | `dict[str, str] | None` | `None` |
| `images` | `list[str] | None` | `None` |
| `temperature` | `float | None` | `None` |
| `max_tokens` | `int | None` | `None` |
| `timeout` | `float | None` | `None` |

###### `_request_claude`  (L594)

```python
async def _request_claude(self, session: aiohttp.ClientSession, actual_model: str, system_prompt: str, user_prompt: str, temperature: float | None = None, max_tokens: int | None = None, timeout: float | None = None) -> str
```

> Send a request to Anthropic Claude Messages API.

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `session` | `aiohttp.ClientSession` | `—` |
| `actual_model` | `str` | `—` |
| `system_prompt` | `str` | `—` |
| `user_prompt` | `str` | `—` |
| `temperature` | `float | None` | `None` |
| `max_tokens` | `int | None` | `None` |
| `timeout` | `float | None` | `None` |

###### `chat_stream`  (L646)

```python
async def chat_stream(self, model: str, system_prompt: str, user_prompt: str, images: list[str] | None = None, temperature: float | None = None, max_tokens: int | None = None, timeout: float | None = None) -> AsyncIterator[str]
```

> Stream token chunks asynchronously from the appropriate backend.  \n>   \n>         Args:  \n>             model: Full model name with optional prefix (e.g. 'freetoken:...').  \n>             system_prompt: System persona/instructions.  \n>             user_prompt: User instruction or input text.  \n>             images: Optional base64 encoded images.  \n>             temperature: Sampling temperature override.  \n>             max_tokens: Maximum tokens override.  \n>             timeout: Timeout in seconds override.  \n>   \n>         Yields:  \n>             str: Token delta chunks.

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `model` | `str` | `—` |
| `system_prompt` | `str` | `—` |
| `user_prompt` | `str` | `—` |
| `images` | `list[str] | None` | `None` |
| `temperature` | `float | None` | `None` |
| `max_tokens` | `int | None` | `None` |
| `timeout` | `float | None` | `None` |

###### `chat`  (L765)

```python
async def chat(self, model: str, system_prompt: str, user_prompt: str, images: list[str] | None = None, temperature: float | None = None, max_tokens: int | None = None, timeout: float | None = None) -> str
```

> Route a chat request to the correct backend and return full completion text.  \n>   \n>         Args:  \n>             model: Target model identifier.  \n>             system_prompt: System persona/instructions.  \n>             user_prompt: User instruction or input.  \n>             images: Optional base64 encoded images.  \n>             temperature: Sampling temperature override.  \n>             max_tokens: Maximum completion tokens override.  \n>             timeout: Timeout in seconds override.  \n>   \n>         Returns:  \n>             str: Generated completion response.

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `model` | `str` | `—` |
| `system_prompt` | `str` | `—` |
| `user_prompt` | `str` | `—` |
| `images` | `list[str] | None` | `None` |
| `temperature` | `float | None` | `None` |
| `max_tokens` | `int | None` | `None` |
| `timeout` | `float | None` | `None` |

###### `close`  (L885)

```python
async def close(self) -> None
```

> 安全釋放占用之 TCP 線路。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |


---


### 📄 `handlers/bot_brain/cog.py`


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L13 |


#### Class `BotBrain` ← `commands.Cog`  (L16)

> 大腦推斷單步佇列守衛：限制GPU顯存佔滿之生命線守衛兵。

##### Methods

###### `__init__`  (L19)

```python
def __init__(self, bot: commands.Bot) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `bot` | `commands.Bot` | `—` |

###### `sd_generating`  (L34)

```python
@property
def sd_generating(self) -> bool
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `sd_generating`  (L38)

```python
@sd_generating.setter
def sd_generating(self, value: bool) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `value` | `bool` | `—` |

###### `cog_load`  (L45)

```python
async def cog_load(self) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `cog_unload`  (L51)

```python
async def cog_unload(self) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `enqueue_task`  (L64)

```python
async def enqueue_task(self, task: InferenceTask) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `task` | `InferenceTask` | `—` |

###### `get_pending_tasks`  (L77)

```python
def get_pending_tasks(self) -> list[dict[str, Any]]
```

> 回傳目前在 PriorityQueue 內排隊等待大腦推理的任務列表。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `_inference_worker`  (L94)

```python
async def _inference_worker(self) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `_process_task`  (L131)

```python
async def _process_task(self, task: InferenceTask, max_retries: int, retry_delay: float) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `task` | `InferenceTask` | `—` |
| `max_retries` | `int` | `—` |
| `retry_delay` | `float` | `—` |


---


### 📄 `handlers/bot_brain/health_monitor.py`


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L11 |


#### Class `DiscordChannelHandler` ← `logging.Handler`  (L13)

##### Methods

###### `__init__`  (L14)

```python
def __init__(self, bot: commands.Bot, level: int = logging.WARNING) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `bot` | `commands.Bot` | `—` |
| `level` | `int` | `logging.WARNING` |

###### `emit`  (L19)

```python
def emit(self, record: logging.LogRecord) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `record` | `logging.LogRecord` | `—` |

###### `_send_log`  (L33)

```python
async def _send_log(self, channel_id: int, user_ids: list[int], msg: str) -> None
```

> 非同步發送日誌訊息至頻道與管理員私訊。  \n>   \n>         Args:  \n>             channel_id: 目標 Discord 頻道 ID（若為 0 則不發送）。  \n>             user_ids: 接收私訊的 Discord 使用者 ID 清單。  \n>             msg: 欲發送的日誌字串內容。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `channel_id` | `int` | `—` |
| `user_ids` | `list[int]` | `—` |
| `msg` | `str` | `—` |


#### Class `HealthMonitor` ← `commands.Cog`  (L63)

##### Methods

###### `__init__`  (L64)

```python
def __init__(self, bot: commands.Bot) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `bot` | `commands.Bot` | `—` |

###### `cog_load`  (L74)

```python
async def cog_load(self) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `cog_unload`  (L91)

```python
async def cog_unload(self) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `ping_ollama`  (L102)

```python
async def ping_ollama(self) -> bool
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `notify_admin`  (L111)

```python
async def notify_admin(self, message: str) -> None
```

> 發送系統警報至警報頻道與管理員/指定使用者私訊。  \n>   \n>         Args:  \n>             message: 警報內容文字。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `message` | `str` | `—` |

###### `record_tok_speed`  (L146)

```python
def record_tok_speed(self, tok_s: float) -> None
```

> 由 BotBrain 回報每則回應的真實生成速度，供效能看門狗評估。  \n>   \n>         使用滑動式觀察窗口：每次呼叫時若已跨越窗口時長，先結算上一窗口再開新窗。  \n>         累積 tok/s 樣本，足夠樣本且連續多窗平均都低於門檻時，發送重啟提醒。  \n>         本方法為同步計數，只更新記憶體狀態，實際發送由後續 _health_loop 週期處理，  \n>         避免在串流 hot-path 上做網路 I/O。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `tok_s` | `float` | `—` |

###### `_close_perf_window`  (L180)

```python
def _close_perf_window(self, min_samples: int, consecutive_needed: int, threshold: float, cooldown: float, now: float) -> None
```

> 結算一個觀察窗口：樣本足夠且平均過慢則累計失敗次數。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `min_samples` | `int` | `—` |
| `consecutive_needed` | `int` | `—` |
| `threshold` | `float` | `—` |
| `cooldown` | `float` | `—` |
| `now` | `float` | `—` |

###### `_health_loop`  (L212)

```python
@tasks.loop(seconds=settings.HEALTH_CHECK_INTERVAL_SECONDS)
async def _health_loop(self) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `before_loop`  (L219)

```python
@_health_loop.before_loop
async def before_loop(self) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |


---


### 📄 `handlers/bot_brain/models.py`


#### Class `InferenceTask` ← `object`  (L7)

> 提交到任務排程優先級對列內的單一資料模塊 (Task Schema)。
    包含對話邏輯要求，與目標指定要發到什麼模型處理。

##### Methods

###### `cancel`  (L25)

```python
def cancel(self) -> None
```

> 標記該推論任務已取消，通知佇列 Worker 與底層推論跳過或中斷執行。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |


---


<a id="social"></a>

## 💬 `handlers/social/`


### 📄 `handlers/social/__init__.py`


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L22 |


#### Class `SocialChat` ← `commands.Cog`  (L25)

> 社群日常對話處理模組，負責訊息計數、熱門話題討論串建立、主動插話與表情符號互動。

##### Methods

###### `__init__`  (L28)

```python
def __init__(self, bot: commands.Bot) -> None
```

> 初始化 SocialChat 模組。  \n>   \n>         Args:  \n>             bot: Discord Bot 實例。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `bot` | `commands.Bot` | `—` |

###### `_get_interject_state`  (L39)

```python
def _get_interject_state(self, channel_id: int) -> InterjectState
```

> 取得或建立指定頻道的插話評估狀態。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `channel_id` | `int` | `—` |

###### `on_message`  (L46)

```python
@commands.Cog.listener()
async def on_message(self, message: discord.Message) -> None
```

> 監聽文字頻道與討論串訊息，進行計數、氛圍更新與 Tag 意圖路由。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `message` | `discord.Message` | `—` |

###### `on_raw_reaction_add`  (L116)

```python
@commands.Cog.listener()
async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None
```

> 處理使用者的 Reaction 快捷操作（🗑️ 刪除、📌 釘選、🔄 重新生成、❓ 除錯回報）。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `payload` | `discord.RawReactionActionEvent` | `—` |


#### Functions

##### `setup`  (L248)

```python
async def setup(bot: commands.Bot) -> None
```

> 載入 SocialChat 套件架構。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `bot` | `commands.Bot` | `—` |


---


### 📄 `handlers/social/contracts.py`

> handlers/social/contracts.py


#### Class `ToolExecutionStatus` ← `str, Enum`  (L15)

> 工具執行狀態代碼。

##### 類別變數

| 名稱 | 值（摘要） |
|---|---|
| `SUCCESS` | `'SUCCESS'` |
| `ERROR` | `'ERROR'` |
| `NO_DATA` | `'NO_DATA'` |


#### Class `ToolCategory` ← `str, Enum`  (L23)

> 工具類別劃分。

    DIRECT_UI:
        自帶 Discord 渲染（Embed / 圖片 / 音樂語音）的互動服務工具。
        若使用者無額外聊天/心得提問，執行完成後可觸發對話抑制以避免重複發言。
    DATA_PROVIDER:
        純資料/API 查詢工具，本身不發送最終回覆，僅提供結構化摘要供 Agent 進行性格轉述。

##### 類別變數

| 名稱 | 值（摘要） |
|---|---|
| `DIRECT_UI` | `'DIRECT_UI'` |
| `DATA_PROVIDER` | `'DATA_PROVIDER'` |
| `DATA_ONLY` | `'DATA_ONLY'` |


#### Class `ToolResult` ← `object`  (L39)

> 核心工具標準執行成果合約。

    Attributes:
        status: 執行狀態 (SUCCESS, ERROR, NO_DATA)
        is_direct_rendered: True 代表工具已向 Discord 發送完整的 UI/圖片/訊息，Agent 應避免重複合成回覆
        summary_text: 供 Agent 對話合成或記憶記錄使用的文字摘要
        extra_data: 額外的結構化資料字典

##### Methods

###### `success`  (L55)

```python
@classmethod
def success(cls, summary_text: str = '', is_direct_rendered: bool = False, extra_data: dict[str, Any] | None = None) -> ToolResult
```

> 建立成功結果。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `cls` | `—` | `—` |
| `summary_text` | `str` | `''` |
| `is_direct_rendered` | `bool` | `False` |
| `extra_data` | `dict[str, Any] | None` | `None` |

###### `error`  (L71)

```python
@classmethod
def error(cls, error_msg: str, is_direct_rendered: bool = False, extra_data: dict[str, Any] | None = None) -> ToolResult
```

> 建立失敗結果。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `cls` | `—` | `—` |
| `error_msg` | `str` | `—` |
| `is_direct_rendered` | `bool` | `False` |
| `extra_data` | `dict[str, Any] | None` | `None` |

###### `no_data`  (L87)

```python
@classmethod
def no_data(cls, message: str = '未找到相關數據。', is_direct_rendered: bool = False, extra_data: dict[str, Any] | None = None) -> ToolResult
```

> 建立無數據結果。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `cls` | `—` | `—` |
| `message` | `str` | `'未找到相關數據。'` |
| `is_direct_rendered` | `bool` | `False` |
| `extra_data` | `dict[str, Any] | None` | `None` |


---


### 📄 `handlers/social/dispatch/__init__.py`

> handlers/social/dispatch/


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `__all__` | `—` | `['BaseDispatchStrategy', 'IntegratedStrategy', 'ParallelStrategy', 'ReactStrateg...` | L23 |


---


### 📄 `handlers/social/dispatch/base.py`

> handlers/social/dispatch/base.py


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L20 |


#### Class `BaseDispatchStrategy` ← `ABC`  (L23)

> 調度策略基底抽象類別。

    所有調度模式（INTEGRATED、PARALLEL、REACT）均繼承此類別，透過 self._agent 存取共用基礎設施。

##### Methods

###### `__init__`  (L29)

```python
def __init__(self, agent: Any) -> None
```

> 初始化策略並繫結 DispatchAgentTool (Coordinator)。  \n>   \n>         Args:  \n>             agent: DispatchAgentTool 實例。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `agent` | `Any` | `—` |

###### `_tools`  (L38)

```python
@property
def _tools(self) -> dict[str, BaseTool]
```

> 可用工具字典。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `mode_name`  (L44)

```python
@property
@abstractmethod
def mode_name(self) -> str
```

> 回傳當前策略模式名稱。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `_build_tool_descriptions`  (L48)

```python
def _build_tool_descriptions(self, include_chat: bool = False) -> str
```

> 委託 Coordinator 組裝工具描述清單。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `include_chat` | `bool` | `False` |

###### `execute`  (L53)

```python
@abstractmethod
async def execute(self, brain: Any, bot: commands.Bot, message: discord.Message, user_query: str, vibe_snippet: str, full_context: str, images: list[str] | None, start_time: float, visual_context: str = '') -> None
```

> 執行該調度策略的主流程。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `brain` | `Any` | `—` |
| `bot` | `commands.Bot` | `—` |
| `message` | `discord.Message` | `—` |
| `user_query` | `str` | `—` |
| `vibe_snippet` | `str` | `—` |
| `full_context` | `str` | `—` |
| `images` | `list[str] | None` | `—` |
| `start_time` | `float` | `—` |
| `visual_context` | `str` | `''` |


---


### 📄 `handlers/social/dispatch/integrated.py`

> handlers/social/dispatch/integrated.py


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L33 |


#### Class `IntegratedStrategy` ← `BaseDispatchStrategy`  (L36)

> 單階段整合式自主調度策略。

##### Methods

###### `mode_name`  (L40)

```python
@property
def mode_name(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `execute`  (L43)

```python
async def execute(self, brain: Any, bot: commands.Bot, message: discord.Message, user_query: str, vibe_snippet: str, full_context: str, images: list[str] | None, start_time: float, visual_context: str = '') -> None
```

> INTEGRATED 模式主流程（單階段內嵌 Action 標籤調度）。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `brain` | `Any` | `—` |
| `bot` | `commands.Bot` | `—` |
| `message` | `discord.Message` | `—` |
| `user_query` | `str` | `—` |
| `vibe_snippet` | `str` | `—` |
| `full_context` | `str` | `—` |
| `images` | `list[str] | None` | `—` |
| `start_time` | `float` | `—` |
| `visual_context` | `str` | `''` |


---


### 📄 `handlers/social/dispatch/models.py`

> handlers/social/dispatch/models.py


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L13 |


#### Class `PlannedTask` ← `object`  (L22)

> 規劃器輸出的單一工具任務。


#### Class `ExecutionPlan` ← `object`  (L30)

> 規劃器輸出的完整執行計畫。


#### Functions

##### `_parse_actions`  (L43)

```python
def _parse_actions(text: str) -> list[PlannedTask]
```

> 從 LLM 生成文字中解析 <action name="TOOL">input</action> 或相關變體。  \n>   \n>     Args:  \n>         text: LLM 原始生成文字。  \n>   \n>     Returns:  \n>         list[PlannedTask]: 解析出的工具任務列表。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `text` | `str` | `—` |

##### `_parse_plan_json`  (L93)

```python
def _parse_plan_json(raw: str, default_query: str) -> ExecutionPlan
```

> 從 LLM 原始輸出中解析 ExecutionPlan，具備多層容錯回退機制。  \n>   \n>     Args:  \n>         raw: LLM 原始文字輸出。  \n>         default_query: 解析失敗時的兜底查詢文字。  \n>   \n>     Returns:  \n>         ExecutionPlan 實例。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `raw` | `str` | `—` |
| `default_query` | `str` | `—` |

##### `_extract_xml_tag`  (L156)

```python
def _extract_xml_tag(text: str, tag: str) -> str | None
```

> 從文字中提取 <tag>content</tag>。  \n>   \n>     Args:  \n>         text: 要解析的原始文字。  \n>         tag: XML 標籤名稱。  \n>   \n>     Returns:  \n>         標籤內容字串，若未找到則回傳 None。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `text` | `str` | `—` |
| `tag` | `str` | `—` |

##### `_parse_react_output`  (L170)

```python
def _parse_react_output(raw: str) -> dict[str, str]
```

> 解析 LLM 的 ReAct 格式輸出（支援 XML 標籤與標準文字 fallback）。  \n>   \n>     Args:  \n>         raw: LLM 原始輸出文字。  \n>   \n>     Returns:  \n>         dict，含 type ('action'|'final')、thought、  \n>         及 action+action_input 或 final_answer。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `raw` | `str` | `—` |


---


### 📄 `handlers/social/dispatch/parallel.py`

> handlers/social/dispatch/parallel.py


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L31 |


#### Class `ParallelStrategy` ← `BaseDispatchStrategy`  (L34)

> 雙階段並行調度策略。

##### Methods

###### `mode_name`  (L38)

```python
@property
def mode_name(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `should_early_terminate`  (L41)

```python
def should_early_terminate(self, plan: ExecutionPlan, executed_results: list[tuple[str, ToolResponseMode, str]], user_query: str) -> bool
```

> 判定是否觸發 PARALLEL 模式的行為自適應對話抑制。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `plan` | `ExecutionPlan` | `—` |
| `executed_results` | `list[tuple[str, ToolResponseMode, str]]` | `—` |
| `user_query` | `str` | `—` |

###### `execute`  (L60)

```python
async def execute(self, brain: Any, bot: commands.Bot, message: discord.Message, user_query: str, vibe_snippet: str, full_context: str, images: list[str] | None, start_time: float, visual_context: str = '') -> None
```

> PARALLEL 模式主流程。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `brain` | `Any` | `—` |
| `bot` | `commands.Bot` | `—` |
| `message` | `discord.Message` | `—` |
| `user_query` | `str` | `—` |
| `vibe_snippet` | `str` | `—` |
| `full_context` | `str` | `—` |
| `images` | `list[str] | None` | `—` |
| `start_time` | `float` | `—` |
| `visual_context` | `str` | `''` |


---


### 📄 `handlers/social/dispatch/prompts.py`

> handlers/social/dispatch/prompts.py


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L14 |
| `_TOOL_DESC_TEMPLATE` | `—` | `'- **{name}**：{description}'` | L20 |
| `_USER_MESSAGE_TEMPLATE` | `—` | `'使用者（{author}）說：「{content}」'` | L21 |
| `VISION_AWARENESS_RULE` | `—` | `'【視覺感官與圖片認知原則】：\n- 提示詞中的【妳眼前的視覺畫面】是妳自己的眼睛（視覺神經）看到的客觀畫面與情報，絕不是使用者打的文字！\n- 使用者只是把圖...` | L23 |
| `_LEGACY_VISION_PATTERN` | `—` | `re.compile('【群友上傳了一張圖片(?:[，,]\\s*(?:系統視覺解析結果：(?P<desc>.*?)\|但(?P<err>.*?)))?】\\s*...` | L31 |
| `_TOOL_ALIASES` | `dict[str, str]` | `{'PIXIV_SEARCH': 'PIXIV', 'PIXIV_TOOL': 'PIXIV', 'FIND_IMAGE': 'PIXIV', 'SEARCH_...` | L114 |
| `_INTEGRATED_TOOL_TEMPLATE` | `—` | `'\n【外部工具自主調用特權】\n\n【⚠️ 輸出模式二選一鐵則（嚴禁 ACTION 與正文對白同時出現！）】：\n- 模式 A（調用工具）：若需即時情報、多媒...` | L156 |
| `_TOOL_RULES_PATH` | `Path` | `getattr(settings, 'TOOL_RULES_PATH', settings.SOUL_DIR / 'tool_rules.md')` | L176 |
| `_PARALLEL_PLANNER_SYSTEM_TEMPLATE` | `—` | `'[SYSTEM: PARALLEL TASK DISPATCH & CHAT SUPPRESSION ENGINE]\n你是一個高效率、極致冷靜的任務排程與對...` | L199 |
| `_REACT_SYSTEM_TEMPLATE` | `—` | `'[SYSTEM: TASK PLANNING & TOOL DISPATCH ENGINE]\n你是一個冷靜、精準的任務規劃與工具調度引擎。\n嚴格鐵則：\n...` | L259 |
| `_OBSERVATION_TEMPLATE` | `—` | `'<observation>\n工具 [{tool}] 執行完畢，結果如下：\n{result}\n</observation>'` | L313 |
| `_REACT_CONTINUATION_TEMPLATE` | `—` | `'以下是目前的對話歷史與工具執行記錄。請根據這些資訊繼續推理：\n\n{transcript}\n\n現在輪到你回覆：'` | L318 |


#### Functions

##### `extract_vision_context`  (L37)

```python
def extract_vision_context(user_query: str, visual_context: str = '') -> tuple[str, str]
```

> 從使用者查詢與傳入的視覺上下文提煉純淨的使用者發言與視覺感知內容。  \n>   \n>     支援自動解析舊格式（如包含『【群友上傳了一張圖片，系統視覺解析結果：...】』），  \n>     確保無論上游是否已分離，雪莉均能明確區分『自己的眼睛看到的畫面』與『使用者真正說的話』。  \n>   \n>     Args:  \n>         user_query: 原始使用者輸入文字（可能混雜舊版視覺標籤）。  \n>         visual_context: 上游明確傳遞的視覺感知描述（若有）。  \n>   \n>     Returns:  \n>         tuple[str, str]: (純淨的使用者發言, 視覺感知描述內容)。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `user_query` | `str` | `—` |
| `visual_context` | `str` | `''` |

##### `build_user_turn_prompt`  (L67)

```python
def build_user_turn_prompt(display_name: str, user_query: str, visual_context: str = '', context_header: str = '', observations_text: str = '') -> str
```

> 建構統一的使用者輪次 Prompt，徹底隔絕視覺感知（雪莉的眼睛）與使用者發言。  \n>   \n>     Args:  \n>         display_name: 使用者在伺服器中的顯示名稱。  \n>         user_query: 使用者輸入文字（可為空或未打字）。  \n>         visual_context: 視覺神經/視覺模型觀察到的畫面特徵情報。  \n>         context_header: 上下文歷史標頭（若有）。  \n>         observations_text: 工具執行結果回傳文字（若有）。  \n>   \n>     Returns:  \n>         str: 格式化後的 User Prompt 文本。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `display_name` | `str` | `—` |
| `user_query` | `str` | `—` |
| `visual_context` | `str` | `''` |
| `context_header` | `str` | `''` |
| `observations_text` | `str` | `''` |

##### `_load_tool_rules`  (L181)

```python
def _load_tool_rules() -> str
```

> 讀取外部工具規則 Markdown。讀取失敗時 fallback 至內建模板。


---


### 📄 `handlers/social/dispatch/react.py`

> handlers/social/dispatch/react.py


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L31 |


#### Class `ReactStrategy` ← `BaseDispatchStrategy`  (L34)

> 多步驟循環推理調度策略。

##### Methods

###### `mode_name`  (L38)

```python
@property
def mode_name(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `execute`  (L41)

```python
async def execute(self, brain: Any, bot: commands.Bot, message: discord.Message, user_query: str, vibe_snippet: str, full_context: str, images: list[str] | None, start_time: float, visual_context: str = '') -> None
```

> REACT 模式主流程（多步推理迴圈）。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `brain` | `Any` | `—` |
| `bot` | `commands.Bot` | `—` |
| `message` | `discord.Message` | `—` |
| `user_query` | `str` | `—` |
| `vibe_snippet` | `str` | `—` |
| `full_context` | `str` | `—` |
| `images` | `list[str] | None` | `—` |
| `start_time` | `float` | `—` |
| `visual_context` | `str` | `''` |


---


### 📄 `handlers/social/dispatch_agent.py`

> handlers/social/dispatch_agent.py


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L55 |
| `parse_plan_json` | `—` | `_parse_plan_json` | L57 |
| `__all__` | `—` | `['PlannedTask', 'ExecutionPlan', 'DispatchAgentTool', 'IntegratedAgentTool', 'Pa...` | L59 |


#### Class `DispatchAgentTool` ← `BaseTool`  (L84)

> 統一調度 Agent（Coordinator）。

    支援 INTEGRATED（單階段整合）、PARALLEL（雙階段並行）與 REACT（多步驟）三種執行模式。
    內部採用策略模式（Strategy Pattern）將實際調度細節委任至 dispatch/ 子模組。

##### Methods

###### `__init__`  (L91)

```python
def __init__(self, tool_registry: list[BaseTool], mode: Literal['INTEGRATED', 'PARALLEL', 'REACT'] = 'INTEGRATED') -> None
```

> 初始化調度器並掛載策略實例。  \n>   \n>         Args:  \n>             tool_registry: 所有可用工具的列表。  \n>             mode: 執行模式，'INTEGRATED'（預設）、'PARALLEL' 或 'REACT'。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `tool_registry` | `list[BaseTool]` | `—` |
| `mode` | `Literal['INTEGRATED', 'PARALLEL', 'REACT']` | `'INTEGRATED'` |

###### `intent_name`  (L116)

```python
@property
def intent_name(self) -> str
```

> Returns: 工具意圖名稱（依模式區分）。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `llm_description`  (L121)

```python
@property
def llm_description(self) -> str
```

> Returns: 工具說明。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `can_handle_by_regex`  (L125)

```python
def can_handle_by_regex(self, text: str) -> bool
```

> Returns: 永遠回傳 False，由 router 直接呼叫。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `text` | `str` | `—` |

###### `current_strategy`  (L130)

```python
@property
def current_strategy(self) -> BaseDispatchStrategy
```

> 取得當前模式對應之策略實作。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `_build_tool_descriptions`  (L138)

```python
def _build_tool_descriptions(self, include_chat: bool = False) -> str
```

> 組裝工具描述清單。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `include_chat` | `bool` | `False` |

###### `_call_llm`  (L150)

```python
async def _call_llm(self, brain: BotBrain, system_prompt: str, user_prompt: str, task_id: str, model: str, temperature: float = 0.1, timeout: float | None = None, images: list[str] | None = None, stream_callback: Any | None = None) -> str
```

> 向 BotBrain 發送推理任務並等待結果。可供測試 mock。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `brain` | `BotBrain` | `—` |
| `system_prompt` | `str` | `—` |
| `user_prompt` | `str` | `—` |
| `task_id` | `str` | `—` |
| `model` | `str` | `—` |
| `temperature` | `float` | `0.1` |
| `timeout` | `float | None` | `None` |
| `images` | `list[str] | None` | `None` |
| `stream_callback` | `Any | None` | `None` |

###### `_parallel_execute_single_task`  (L202)

```python
async def _parallel_execute_single_task(self, task: PlannedTask, bot: commands.Bot, message: discord.Message, vibe_snippet: str, full_context: str, images: list[str] | None = None) -> tuple[str, ToolResponseMode, str]
```

> 非同步執行單一規劃工具任務。可供測試 mock。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `task` | `PlannedTask` | `—` |
| `bot` | `commands.Bot` | `—` |
| `message` | `discord.Message` | `—` |
| `vibe_snippet` | `str` | `—` |
| `full_context` | `str` | `—` |
| `images` | `list[str] | None` | `None` |

###### `_parallel_should_early_terminate`  (L274)

```python
def _parallel_should_early_terminate(self, plan: ExecutionPlan, executed_results: list[tuple[str, ToolResponseMode, str]], user_query: str) -> bool
```

> 判定是否觸發 PARALLEL 模式的行為自適應對話抑制。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `plan` | `ExecutionPlan` | `—` |
| `executed_results` | `list[tuple[str, ToolResponseMode, str]]` | `—` |
| `user_query` | `str` | `—` |

###### `_should_early_terminate`  (L293)

```python
def _should_early_terminate(self, plan: ExecutionPlan, executed_results: list[tuple[str, ToolResponseMode, str]], user_query: str) -> bool
```

> 舊版相容早期終止方法。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `plan` | `ExecutionPlan` | `—` |
| `executed_results` | `list[tuple[str, ToolResponseMode, str]]` | `—` |
| `user_query` | `str` | `—` |

###### `_react_execute_tool`  (L302)

```python
async def _react_execute_tool(self, tool_name: str, action_input: str, bot: commands.Bot, message: discord.Message, vibe_snippet: str, full_context: str, images: list[str] | None = None) -> str
```

> ReAct 模式執行工具方法。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `tool_name` | `str` | `—` |
| `action_input` | `str` | `—` |
| `bot` | `commands.Bot` | `—` |
| `message` | `discord.Message` | `—` |
| `vibe_snippet` | `str` | `—` |
| `full_context` | `str` | `—` |
| `images` | `list[str] | None` | `None` |

###### `_generate_final_response`  (L362)

```python
async def _generate_final_response(self, brain: BotBrain, user_query: str, channel_id: int | None, bot: commands.Bot, user_id: int, display_name: str, observations: list[tuple[str, str]], vibe_snippet: str = '', full_context: str = '', draft_answer: str = '', images: list[str] | None = None, session: LiveThinkingSession | None = None, server_id: str = '', visual_context: str = '') -> tuple[str, str, str, str]
```

> 使用 PRIMARY_MODEL 合成最終給使用者的對話文本。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `brain` | `BotBrain` | `—` |
| `user_query` | `str` | `—` |
| `channel_id` | `int | None` | `—` |
| `bot` | `commands.Bot` | `—` |
| `user_id` | `int` | `—` |
| `display_name` | `str` | `—` |
| `observations` | `list[tuple[str, str]]` | `—` |
| `vibe_snippet` | `str` | `''` |
| `full_context` | `str` | `''` |
| `draft_answer` | `str` | `''` |
| `images` | `list[str] | None` | `None` |
| `session` | `LiveThinkingSession | None` | `None` |
| `server_id` | `str` | `''` |
| `visual_context` | `str` | `''` |

###### `_run_integrated`  (L514)

```python
async def _run_integrated(self, brain: Any, bot: commands.Bot, message: discord.Message, user_query: str, vibe_snippet: str, full_context: str, images: list[str] | None, start_time: float, visual_context: str = '') -> None
```

> INTEGRATED 模式委派。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `brain` | `Any` | `—` |
| `bot` | `commands.Bot` | `—` |
| `message` | `discord.Message` | `—` |
| `user_query` | `str` | `—` |
| `vibe_snippet` | `str` | `—` |
| `full_context` | `str` | `—` |
| `images` | `list[str] | None` | `—` |
| `start_time` | `float` | `—` |
| `visual_context` | `str` | `''` |

###### `execute`  (L535)

```python
async def execute(self, bot: commands.Bot, message: discord.Message, user_query: str, vibe_snippet: str, full_context: str, silent: bool = False, images: list[str] | None = None, visual_context: str = '', **kwargs: Any) -> ToolResult | str | None
```

> 根據 self._mode 委派對應策略執行。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `bot` | `commands.Bot` | `—` |
| `message` | `discord.Message` | `—` |
| `user_query` | `str` | `—` |
| `vibe_snippet` | `str` | `—` |
| `full_context` | `str` | `—` |
| `silent` | `bool` | `False` |
| `images` | `list[str] | None` | `None` |
| `visual_context` | `str` | `''` |
| `**kwargs` | `Any` | `—` |


#### Class `IntegratedAgentTool` ← `DispatchAgentTool`  (L576)

> 單階段整合式自主調度 Agent（固定 INTEGRATED 模式）。

##### Methods

###### `__init__`  (L579)

```python
def __init__(self, tool_registry: list[BaseTool]) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `tool_registry` | `list[BaseTool]` | `—` |


#### Class `ParallelAgentTool` ← `DispatchAgentTool`  (L583)

> 雙階段並行調度 Agent（固定 PARALLEL 模式）。

##### Methods

###### `__init__`  (L586)

```python
def __init__(self, tool_registry: list[BaseTool]) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `tool_registry` | `list[BaseTool]` | `—` |


#### Class `ReactAgentTool` ← `DispatchAgentTool`  (L590)

> 多步驟 ReAct 調度 Agent（固定 REACT 模式）。

##### Methods

###### `__init__`  (L593)

```python
def __init__(self, tool_registry: list[BaseTool]) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `tool_registry` | `list[BaseTool]` | `—` |


---


### 📄 `handlers/social/interject.py`


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L18 |


#### Class `InterjectState` ← `object`  (L20)

##### 類別變數

| 名稱 | 值（摘要） |
|---|---|
| `__slots__` | `('cooldown_until', 'consecutive_count', 'eval_counter', 'is_evaluating')` |

##### Methods

###### `__init__`  (L22)

```python
def __init__(self) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `is_cooling_down`  (L27)

```python
def is_cooling_down(self) -> bool
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `reset_cooldown`  (L29)

```python
def reset_cooldown(self) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `can_interject`  (L32)

```python
def can_interject(self) -> bool
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |


#### Functions

##### `maybe_interject`  (L38)

```python
async def maybe_interject(bot: commands.Bot, channel: discord.TextChannel | discord.Thread | Any, state: InterjectState) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `bot` | `commands.Bot` | `—` |
| `channel` | `discord.TextChannel | discord.Thread | Any` | `—` |
| `state` | `InterjectState` | `—` |


---


### 📄 `handlers/social/memory_builder.py`

> handlers/social/memory_builder.py


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L16 |


#### Functions

##### `build_user_memory_block`  (L19)

```python
def build_user_memory_block(bot: Any, user_id: int, display_name: str, mentioned_users: list[Any] | None = None, query_text: str = '') -> str
```

> 從 MemoryManager 取得特定使用者與提及對象的記憶摘要。  \n>     若 MemoryManager 尚未載入或該使用者無記錄，回傳空字串。  \n>   \n>     Args:  \n>         bot: discord.ext.commands.Bot 實例，用來取得 MemoryManager Cog。  \n>         user_id: Discord 使用者的 ID。  \n>         display_name: 使用者的目前顯示名稱，用於 fallback 稱呼。  \n>         mentioned_users: 訊息中顯式 @提及 之使用者列表（可選）。  \n>         query_text: 使用者訊息文字，用於模糊比對記憶庫中其他使用者的暱稱（可選）。  \n>   \n>     Returns:  \n>         組裝完成的記憶字串，可直接嵌入 Prompt。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `bot` | `Any` | `—` |
| `user_id` | `int` | `—` |
| `display_name` | `str` | `—` |
| `mentioned_users` | `list[Any] | None` | `None` |
| `query_text` | `str` | `''` |


---


### 📄 `handlers/social/observability.py`

> handlers/social/observability.py


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L31 |
| `_TZ_TW` | `—` | `timezone(timedelta(hours=8))` | L33 |
| `current_trace_id` | `contextvars.ContextVar[str]` | `contextvars.ContextVar('current_trace_id', default='')` | L38 |
| `_RUNTIME_LOG_PATH` | `Path` | `settings.RUNTIME_LOG_PATH` | L92 |
| `_ERROR_LOG_PATH` | `Path` | `settings.ERROR_LOG_PATH` | L93 |
| `_MAX_LOG_SIZE_BYTES` | `int` | `50 * 1024 * 1024` | L94 |
| `_WRITE_COUNTS` | `dict[str, int]` | `{}` | L97 |
| `_STAT_CHECK_INTERVAL` | `int` | `100` | L98 |
| `_CHAT_DB_PATH` | `Path` | `settings.DATA_DIR / 'chat_history.db'` | L100 |
| `_DB_INITIALIZED` | `—` | `False` | L138 |
| `_WRITE_QUEUE` | `'_pyqueue.Queue[tuple[Path, dict] | None]'` | `_pyqueue.Queue()` | L151 |
| `_WRITE_THREAD_STARTED` | `—` | `False` | L152 |


#### Class `TraceIdFilter` ← `logging.Filter`  (L76)

> 將當前 ContextVar 中的 trace_id 自動注入所有 LogRecord 的過濾器。

##### Methods

###### `filter`  (L79)

```python
def filter(self, record: logging.LogRecord) -> bool
```

> 過濾並補充 trace_id 屬性。  \n>   \n>         Args:  \n>             record: Python 日誌記錄物件。  \n>   \n>         Returns:  \n>             bool: 恆為 True（放行所有記錄）。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `record` | `logging.LogRecord` | `—` |


#### Functions

##### `get_trace_id`  (L41)

```python
def get_trace_id() -> str
```

> 取得當前協程/執行緒的 Trace ID。  \n>   \n>     Returns:  \n>         str: 當前的 Trace ID 字串，若未設定則回傳空字串。

##### `new_trace_id`  (L50)

```python
def new_trace_id(prefix: str = 'tr') -> str
```

> 生成一個新的唯一 Trace ID。  \n>   \n>     Args:  \n>         prefix: 前綴字串，預設為 'tr'。可為 'cmd', 'btn', 'chat', 'llm' 等。  \n>   \n>     Returns:  \n>         str: 組合後的唯一 Trace ID，如 'cmd-a1b2c3d4'。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `prefix` | `str` | `'tr'` |

##### `set_trace_id`  (L62)

```python
def set_trace_id(tid: str | None = None) -> str
```

> 設定當前協程的 Trace ID。  \n>   \n>     Args:  \n>         tid: 指定的 Trace ID。若為 None 或空字串，則自動生成一組新 ID。  \n>   \n>     Returns:  \n>         str: 設定成功的 Trace ID 字串。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `tid` | `str | None` | `None` |

##### `_init_events_db`  (L103)

```python
def _init_events_db() -> None
```

> 初始化 chat_history.db 中的 runtime_events 與 error_events 資料表。

##### `_flush_batch`  (L155)

```python
def _flush_batch(batch: list[tuple[Path, dict]]) -> None
```

> 實際批次落盤（SQLite + JSONL），於背景執行緒中單一交易執行。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `batch` | `list[tuple[Path, dict]]` | `—` |

##### `_writer_worker`  (L218)

```python
def _writer_worker() -> None
```

> 背景執行緒：持續取出事件並批次寫入 SQLite 與 JSONL。

##### `_ensure_writer_thread`  (L249)

```python
def _ensure_writer_thread() -> None
```

> 確保背景寫入執行緒已啟動（daemon，隨行程結束）。

##### `_write_line`  (L259)

```python
def _write_line(path: Path, entry: dict) -> None
```

> 將事件推入背景佇列，非阻塞。由 _writer_worker 執行緒實際批次落盤。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `path` | `Path` | `—` |
| `entry` | `dict` | `—` |

##### `flush_observability_logs`  (L272)

```python
def flush_observability_logs() -> None
```

> 等待背景寫入佇列清空（供測試與優雅關機使用）。

##### `log_event`  (L282)

```python
def log_event(event: str, message_id: int = 0, user: str = '', channel_id: int = 0, trace_id: str = '', raw_input: str = '', intent: str = '', intent_source: str = '', veto_fired: bool = False, self_aware_triggered: bool = False, vision_hit_count: int = 0, tags_resolved: list[str] | None = None, tools_invoked: list[str] | None = None, cot_thought: str = '', reply_preview: str = '', model_used: str = '', latency_ms: int = 0, queue_size: int = 0, prompt_tokens_cached: int = 0, prompt_tokens_uncached: int = 0, decode_tokens: int = 0, ttft_ms: int = 0, prefill_latency_ms: int = 0, decode_latency_ms: int = 0, cache_hit_ratio: float = 0.0, **extra: Any) -> None
```

> 記錄一筆互動事件到 runtime_log.jsonl。  \n>   \n>     event 類型清單：  \n>       chat_reply           — CHAT / Agent 完成回覆  \n>       command_exec         — Discord Slash / Prefix 指令執行  \n>       component_interaction — Discord 按鈕 / 選單互動  \n>       modal_submit         — Discord 表單提交  \n>       tool_exec            — 工具獨立執行（輸入、輸出與耗時）  \n>       inference_done       — BotBrain LLM 推論任務完成  \n>       image_search         — PIXIV Tool 完成搜圖  \n>       draw                 — DRAW Tool 完成（或失敗）  \n>       search_rag           — SEARCH Tool 完成 RAG 回覆  \n>       music_action         — MUSIC Tool 執行動作  \n>       schedule_action      — SCHEDULE Tool 執行動作  \n>       stock_query          — STOCK Tool 查詢完成  \n>       weather_query        — WEATHER Tool 查詢完成  \n>       interject            — 插話引擎執行評分（不論是否插話）  \n>       vision_analysis      — Vision 圖片辨識完成  \n>       intent_classify      — LLM Classifier 完成 Intent 判斷  \n>       veto_fired           — Veto 機制觸發降級  \n>       self_aware           — 自我辨識信號被觸發  \n>       user_debug_report    — 使用者對回覆按 ❓ 表情符號  \n>       error                — 任何 exception 發生

| 參數 | 型別 | 預設值 |
|---|---|---|
| `event` | `str` | `—` |
| `message_id` | `int` | `0` |
| `user` | `str` | `''` |
| `channel_id` | `int` | `0` |
| `trace_id` | `str` | `''` |
| `raw_input` | `str` | `''` |
| `intent` | `str` | `''` |
| `intent_source` | `str` | `''` |
| `veto_fired` | `bool` | `False` |
| `self_aware_triggered` | `bool` | `False` |
| `vision_hit_count` | `int` | `0` |
| `tags_resolved` | `list[str] | None` | `None` |
| `tools_invoked` | `list[str] | None` | `None` |
| `cot_thought` | `str` | `''` |
| `reply_preview` | `str` | `''` |
| `model_used` | `str` | `''` |
| `latency_ms` | `int` | `0` |
| `queue_size` | `int` | `0` |
| `prompt_tokens_cached` | `int` | `0` |
| `prompt_tokens_uncached` | `int` | `0` |
| `decode_tokens` | `int` | `0` |
| `ttft_ms` | `int` | `0` |
| `prefill_latency_ms` | `int` | `0` |
| `decode_latency_ms` | `int` | `0` |
| `cache_hit_ratio` | `float` | `0.0` |
| `**extra` | `Any` | `—` |

##### `log_error`  (L457)

```python
def log_error(event: str = 'error', message_id: int = 0, task_id: str = '', trace_id: str = '', error_type: str = '', message: str = '', **extra: Any) -> None
```

> 記錄一筆錯誤/降級事件，同時寫入 runtime_log 與 error_log。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `event` | `str` | `'error'` |
| `message_id` | `int` | `0` |
| `task_id` | `str` | `''` |
| `trace_id` | `str` | `''` |
| `error_type` | `str` | `''` |
| `message` | `str` | `''` |
| `**extra` | `Any` | `—` |

##### `log_command`  (L519)

```python
def log_command(command_type: str = 'slash', command_name: str = '', user: str = '', user_id: int = 0, guild_name: str = '', guild_id: int = 0, channel_name: str = '', channel_id: int = 0, options: dict[str, Any] | None = None, latency_ms: int = 0, status: str = 'success', error_msg: str = '', trace_id: str = '', **extra: Any) -> None
```

> 記錄 Discord 指令與元件互動生命週期事件。  \n>   \n>     Args:  \n>         command_type: 指令類型 ('slash', 'prefix', 'button', 'select', 'modal', 'autocomplete')。  \n>         command_name: 指令名稱（如 'vibe', 'market', 'play' 或按鈕 custom_id）。  \n>         user: 使用者顯示名稱與帳號。  \n>         user_id: Discord 使用者 ID。  \n>         guild_name: 伺服器名稱。  \n>         guild_id: 伺服器 ID。  \n>         channel_name: 頻道名稱。  \n>         channel_id: 頻道 ID。  \n>         options: 傳入的參數字典。  \n>         latency_ms: 執行耗時（毫秒）。  \n>         status: 執行狀態 ('success', 'error', 'deferred')。  \n>         error_msg: 若出錯時的錯誤訊息。  \n>         trace_id: 指定的 Trace ID，預設自動自 ContextVar 取得。  \n>         **extra: 任意附加欄位。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `command_type` | `str` | `'slash'` |
| `command_name` | `str` | `''` |
| `user` | `str` | `''` |
| `user_id` | `int` | `0` |
| `guild_name` | `str` | `''` |
| `guild_id` | `int` | `0` |
| `channel_name` | `str` | `''` |
| `channel_id` | `int` | `0` |
| `options` | `dict[str, Any] | None` | `None` |
| `latency_ms` | `int` | `0` |
| `status` | `str` | `'success'` |
| `error_msg` | `str` | `''` |
| `trace_id` | `str` | `''` |
| `**extra` | `Any` | `—` |

##### `log_tool_exec`  (L595)

```python
def log_tool_exec(tool_name: str = '', tool_input: str = '', observation: str = '', latency_ms: int = 0, status: str = 'success', user: str = '', channel_id: int = 0, message_id: int = 0, error_msg: str = '', trace_id: str = '', **extra: Any) -> None
```

> 記錄 Agent 內部工具調用事件（Tool Execution）。  \n>   \n>     Args:  \n>         tool_name: 工具名稱（如 'SEARCH', 'PIXIV', 'DRAW', 'MUSIC', 'STOCK' 等）。  \n>         tool_input: 傳入工具之參數內容。  \n>         observation: 工具回傳之 Observation 觀測數據。  \n>         latency_ms: 工具執行耗時（毫秒）。  \n>         status: 執行狀態 ('success', 'error')。  \n>         user: 關聯使用者名稱。  \n>         channel_id: 頻道 ID。  \n>         message_id: 訊息 ID。  \n>         error_msg: 錯誤訊息（若有）。  \n>         trace_id: Trace ID。  \n>         **extra: 任意附加欄位。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `tool_name` | `str` | `''` |
| `tool_input` | `str` | `''` |
| `observation` | `str` | `''` |
| `latency_ms` | `int` | `0` |
| `status` | `str` | `'success'` |
| `user` | `str` | `''` |
| `channel_id` | `int` | `0` |
| `message_id` | `int` | `0` |
| `error_msg` | `str` | `''` |
| `trace_id` | `str` | `''` |
| `**extra` | `Any` | `—` |

##### `log_inference`  (L668)

```python
def log_inference(task_id: str = '', model: str = '', prompt_length: int = 0, completion_length: int = 0, queue_latency_ms: int = 0, inference_latency_ms: int = 0, status: str = 'success', prompt_preview: str = '', completion_preview: str = '', error_msg: str = '', trace_id: str = '', **extra: Any) -> None
```

> 記錄 BotBrain LLM 推論任務生命週期事件。  \n>   \n>     Args:  \n>         task_id: 任務 ID。  \n>         model: 使用之模型名稱。  \n>         prompt_length: 輸入 Prompt 字數。  \n>         completion_length: 生成回覆字數。  \n>         queue_latency_ms: 佇列排隊等候耗時（毫秒）。  \n>         inference_latency_ms: 實際推論耗時（毫秒）。  \n>         status: 狀態 ('success', 'retry', 'error')。  \n>         prompt_preview: Prompt 前段摘要。  \n>         completion_preview: 生成內容摘要。  \n>         error_msg: 錯誤訊息（若有）。  \n>         trace_id: Trace ID。  \n>         **extra: 任意附加欄位。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `task_id` | `str` | `''` |
| `model` | `str` | `''` |
| `prompt_length` | `int` | `0` |
| `completion_length` | `int` | `0` |
| `queue_latency_ms` | `int` | `0` |
| `inference_latency_ms` | `int` | `0` |
| `status` | `str` | `'success'` |
| `prompt_preview` | `str` | `''` |
| `completion_preview` | `str` | `''` |
| `error_msg` | `str` | `''` |
| `trace_id` | `str` | `''` |
| `**extra` | `Any` | `—` |

##### `make_timer`  (L738)

```python
def make_timer() -> Callable[[], int]
```

> 回傳一個 callable，呼叫時回傳距離建立時的毫秒數。  \n>   \n>     使用方式：  \n>         elapsed = make_timer()  \n>         # ... 執行一些工作 ...  \n>         latency_ms = elapsed()


---


### 📄 `handlers/social/persona.py`


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L8 |
| `_HIGH_PRIORITY_LORE_PATTERNS` | `—` | `frozenset(['(性暗示\|色色\|瑟瑟\|開車\|撩\|黃腔\|調情\|曖昧\|色\|做愛\|摸\|胸部?\|乳\|奶\|臀\|屁股\|下面\|裸\|脫\|舔\|插入?\|射精\|弄壞\|發情\|想...` | L12 |


#### Functions

##### `_load_soul_persona_by_name`  (L21)

```python
@functools.lru_cache(maxsize=16)
def _load_soul_persona_by_name(soul_name: str) -> str
```

> 載入指定名稱的 SOUL 靈魂 Markdown 檔案內容。  \n>   \n>     Args:  \n>         soul_name: 人設檔案名稱（例如 sherry_default.md）。  \n>   \n>     Returns:  \n>         str: 人設文本內容，若讀取失敗則 fallback 至 CORE_PERSONA。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `soul_name` | `str` | `—` |

##### `_load_soul_persona`  (L53)

```python
def _load_soul_persona(channel_id: int | None = None) -> str
```

> 載入當前有效之 SOUL 靈魂 Markdown 檔案，支援頻道映射與全域預設。  \n>   \n>     Args:  \n>         channel_id: 可選的頻道 ID。  \n>   \n>     Returns:  \n>         str: 角色人設文本。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `channel_id` | `int | None` | `None` |

##### `_load_common_rules`  (L70)

```python
@functools.lru_cache(maxsize=1)
def _load_common_rules() -> str
```

> 載入通用防幻覺與互動鐵則 Markdown 檔案。  \n>   \n>     Returns:  \n>         str: 通用互動規則文本。

##### `_compile_lorebook`  (L93)

```python
@functools.lru_cache(maxsize=16)
def _compile_lorebook(items_tuple: tuple[tuple[str, str], ...]) -> list[tuple[re.Pattern[str], bool, str]]
```

> 預編譯 Lorebook 正則表達式並快取編譯結果。  \n>   \n>     Args:  \n>         items_tuple: Lorebook 鍵值對元組。  \n>   \n>     Returns:  \n>         list[tuple[re.Pattern[str], bool, str]]: (compiled_regex, is_high_priority, lore_fact) 列表。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `items_tuple` | `tuple[tuple[str, str], ...]` | `—` |

##### `clear_soul_cache`  (L110)

```python
def clear_soul_cache() -> None
```

> 清除人設與通用規則快取，供動態熱切換時調用。

##### `build_dynamic_persona`  (L117)

```python
def build_dynamic_persona(current_text: str, intent_name: str, channel_id: int | None = None, server_id: str = '') -> tuple[str, str]
```

> 動態人格拼裝系統（ST 雙層注入與 SOUL 外部化版）。  \n>   \n>     將 SOUL/CORE_PERSONA 與 LOREBOOK 拆分成兩段回傳：  \n>       - head_persona: 放在 Prompt 最前面（背景設定、低優先度世界觀）  \n>       - tail_lore:    放在對話歷史最後面（高優先度鐵則，緊貼生成點，模仿 ST Author's Note）  \n>   \n>     Args:  \n>         current_text: 當前對話歷史或輸入文字（用於觸發世界書關鍵字匹配）。  \n>         intent_name: 意圖分類名稱（如 CHAT, STOCK, DRAW 等）。  \n>         channel_id: 當前頻道 ID（用於特定頻道 SOUL 設定載入）。  \n>         server_id: 當前伺服器識別碼（用於過濾跨伺服器世界記憶）。  \n>   \n>     Returns:  \n>         tuple[str, str]: (head_persona, tail_lore) 二元組。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `current_text` | `str` | `—` |
| `intent_name` | `str` | `—` |
| `channel_id` | `int | None` | `None` |
| `server_id` | `str` | `''` |

##### `get_current_time_block`  (L241)

```python
def get_current_time_block() -> str
```

> 取得當前台灣時間標籤字串。


---


### 📄 `handlers/social/rate_limiter.py`


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `rate_limiter` | `—` | `RateLimiter()` | L31 |


#### Class `RateLimiter` ← `object`  (L5)

##### Methods

###### `__init__`  (L6)

```python
def __init__(self)
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `is_allowed`  (L9)

```python
def is_allowed(self, uid: int) -> bool
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `uid` | `int` | `—` |

###### `get_remaining_cooldown`  (L17)

```python
def get_remaining_cooldown(self, uid: int) -> float
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `uid` | `int` | `—` |

###### `cleanup_expired`  (L24)

```python
def cleanup_expired(self, uid: int) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `uid` | `int` | `—` |


---


### 📄 `handlers/social/registry.py`

> handlers/social/registry.py


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `TOOL_LIST` | `list[BaseTool]` | `[URLSummaryTool(), MusicTool(), ScheduleTool(), DrawTool(), ImageSearchTool(), S...` | L15 |


#### Functions

##### `get_fallback_tool`  (L28)

```python
def get_fallback_tool() -> BaseTool
```


---


### 📄 `handlers/social/router.py`

> handlers/social/router.py


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L18 |


#### Functions

##### `route_and_execute_tag`  (L20)

```python
async def route_and_execute_tag(bot: commands.Bot, message: discord.Message, from_core: bool = False) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `bot` | `commands.Bot` | `—` |
| `message` | `discord.Message` | `—` |
| `from_core` | `bool` | `False` |


---


### 📄 `handlers/social/streaming_thinking.py`

> handlers/social/streaming_thinking.py


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L29 |


#### Class `LiveThinkingSession` ← `object`  (L32)

> 管理單次對話請求的即時思考推演動態編輯生命週期。

##### Methods

###### `__init__`  (L35)

```python
def __init__(self, target: Any) -> None
```

> 初始化即時思考會話。  \n>   \n>         Args:  \n>             target: discord.Message 或 TextChannel 目標。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `target` | `Any` | `—` |

###### `_prefill_ms`  (L69)

```python
@property
def _prefill_ms(self) -> int | None
```

> 向上相容屬性別名：首字前綴運算耗時。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `_prefill_ms`  (L74)

```python
@_prefill_ms.setter
def _prefill_ms(self, val: int | None) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `val` | `int | None` | `—` |

###### `_thought_ms`  (L78)

```python
@property
def _thought_ms(self) -> int | None
```

> 向上相容屬性別名：心智推演耗時。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `_thought_ms`  (L83)

```python
@_thought_ms.setter
def _thought_ms(self, val: int | None) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `val` | `int | None` | `—` |

###### `is_enabled`  (L87)

```python
@property
def is_enabled(self) -> bool
```

> 是否啟用動態編輯思考流程。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `start`  (L91)

```python
async def start(self, initial_hint: str = '📋 意圖解析中') -> None
```

> 發送初始佔位思考訊息（零延遲即時反饋）。  \n>   \n>         Args:  \n>             initial_hint: 當前正在進行的動作簡述。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `initial_hint` | `str` | `'📋 意圖解析中'` |

###### `mark_inference_start`  (L127)

```python
def mark_inference_start(self) -> None
```

> 在向推論引擎發出請求當下標記開始時間，排除排隊與前置處理延遲。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `prepare_synthesis_pass`  (L131)

```python
def prepare_synthesis_pass(self) -> None
```

> 為第二階段合成對話生成重置串流與計時狀態。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `push_step`  (L144)

```python
def push_step(self, step_type: str, text: str, duration_ms: int | None = None) -> None
```

> 以非同步背景任務 (Fire-and-Forget) 推送新思考/工具執行階段，主執行緒 0 等待。  \n>   \n>         Args:  \n>             step_type: 步驟類型 ('plan', 'action', 'obs', 'persona')。  \n>             text: 步驟描述內容。  \n>             duration_ms: 該步驟執行耗時（毫秒，可選）。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `step_type` | `str` | `—` |
| `text` | `str` | `—` |
| `duration_ms` | `int | None` | `None` |

###### `on_stream_chunk`  (L166)

```python
async def on_stream_chunk(self, chunk: str) -> None
```

> 接收 LLM 串流 Token，即時萃取思考鏈並以防抖方式背景更新 Discord 訊息。  \n>   \n>         Args:  \n>             chunk: LLM 生成的即時 Token 片段。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `chunk` | `str` | `—` |

###### `_debounced_edit_step`  (L248)

```python
async def _debounced_edit_step(self) -> None
```

> 非同步背景執行的節流編輯任務。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `_render_in_progress_block`  (L273)

```python
def _render_in_progress_block(self) -> str
```

> 組裝進行中的步驟 Markdown 區塊。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `finish`  (L338)

```python
async def finish(self, final_reply: str, thought_log: str = '', intent_name: str = 'CHAT', model: str = '', latency_ms: int = 0, prompt: str = '') -> list[discord.Message]
```

> 完成思考，將佔位訊息原地編輯為正式回覆。  \n>   \n>         Args:  \n>             final_reply: 最終回覆文字。  \n>             thought_log: 完整思維鏈。  \n>             intent_name: 意圖標籤。  \n>             model: 模型名稱。  \n>             latency_ms: 總耗時。  \n>             prompt: 使用者提問。  \n>   \n>         Returns:  \n>             list[discord.Message]: 發送或更新成功之 Discord 訊息列表。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `final_reply` | `str` | `—` |
| `thought_log` | `str` | `''` |
| `intent_name` | `str` | `'CHAT'` |
| `model` | `str` | `''` |
| `latency_ms` | `int` | `0` |
| `prompt` | `str` | `''` |


---


### 📄 `handlers/social/tool_base.py`


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `ToolResponseMode` | `—` | `ToolCategory` | L12 |


#### Class `BaseTool` ← `object`  (L15)

##### Methods

###### `intent_name`  (L17)

```python
@property
def intent_name(self) -> str
```

> Tool 唯一識別標籤 (如 'MUSIC', 'STOCK')

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `llm_description`  (L22)

```python
@property
def llm_description(self) -> str
```

> 交由分類模型判斷時，說明該在什麼時候喚起這個 Tool 的準則

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `response_mode`  (L27)

```python
@property
def response_mode(self) -> ToolResponseMode
```

> 工具的回應模式，預設為 DATA_PROVIDER。自帶 Discord 渲染/語音的工具可覆寫為 DIRECT_UI。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `can_handle_by_regex`  (L31)

```python
def can_handle_by_regex(self, text: str) -> bool
```

> 最高速匹配層：針對非常明顯的使用情境直接啟動不廢話

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `text` | `str` | `—` |

###### `execute`  (L35)

```python
async def execute(self, bot: commands.Bot, message: discord.Message, user_query: str, vibe_snippet: str, full_context: str, **kwargs: object) -> ToolResult | str | None
```

> 工具核心：此功能實際上的運作邏輯。  \n>   \n>         Args:  \n>             bot: Discord Bot 實例。  \n>             message: 觸發的 Discord 訊息。  \n>             user_query: 使用者的文字輸入。  \n>             vibe_snippet: 頻道近期話題氛圍摘要。  \n>             full_context: 含上下文的完整提示文本。  \n>             **kwargs: 擴充參數，例如 images: list[str]。  \n>   \n>         Returns:  \n>             標準 ToolResult 實例、字串摘要或 None。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `bot` | `commands.Bot` | `—` |
| `message` | `discord.Message` | `—` |
| `user_query` | `str` | `—` |
| `vibe_snippet` | `str` | `—` |
| `full_context` | `str` | `—` |
| `**kwargs` | `object` | `—` |


---


### 📄 `handlers/social/tools/__init__.py`


---


### 📄 `handlers/social/tools/chat_tools.py`

> handlers/social/tools/chat_tools.py


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L30 |


#### Class `ChatTool` ← `BaseTool`  (L56)

##### Methods

###### `intent_name`  (L59)

```python
@property
def intent_name(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `llm_description`  (L63)

```python
@property
def llm_description(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `can_handle_by_regex`  (L66)

```python
def can_handle_by_regex(self, text: str) -> bool
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `text` | `str` | `—` |

###### `execute`  (L76)

```python
async def execute(self, bot: commands.Bot, message: discord.Message, user_query: str, vibe_snippet: str, full_context: str, **kwargs: object) -> ToolResult | str | None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `bot` | `commands.Bot` | `—` |
| `message` | `discord.Message` | `—` |
| `user_query` | `str` | `—` |
| `vibe_snippet` | `str` | `—` |
| `full_context` | `str` | `—` |
| `**kwargs` | `object` | `—` |


#### Functions

##### `_should_trigger_search`  (L33)

```python
def _should_trigger_search(query: str) -> bool
```

> 判斷使用者輸入是否需要觸發即時網路/實體檢索。  \n>   \n>     ⚠️ 注意：排除一般「介紹」（避免誤觸自我介紹），聚焦真實時事、新聞與行情查詢。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `query` | `str` | `—` |


---


### 📄 `handlers/social/tools/draw_tool.py`


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L20 |


#### Class `DrawControlView` ← `discord.ui.View`  (L22)

##### Methods

###### `__init__`  (L23)

```python
def __init__(self, user_query: str, original_author: discord.Member | discord.User)
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `user_query` | `str` | `—` |
| `original_author` | `discord.Member | discord.User` | `—` |

###### `redraw_button`  (L29)

```python
@discord.ui.button(label='🔄 重畫一張', style=discord.ButtonStyle.primary)
async def redraw_button(self, interaction: discord.Interaction, button: discord.ui.Button)
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |
| `button` | `discord.ui.Button` | `—` |

###### `edit_prompt_button`  (L42)

```python
@discord.ui.button(label='📝 修改提示詞', style=discord.ButtonStyle.secondary)
async def edit_prompt_button(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |
| `button` | `discord.ui.Button[Any]` | `—` |


#### Class `DrawTool` ← `BaseTool`  (L66)

##### 類別變數

| 名稱 | 值（摘要） |
|---|---|
| `MANDATORY_POSITIVE` | `['masterpiece', 'best quality', 'ultra-detailed', 'highres', 'perfect anatomy', ` |
| `MANDATORY_NEGATIVE` | `['(worst quality, low quality:1.4)', 'bad anatomy', 'bad hands', 'missing finger` |

##### Methods

###### `__init__`  (L67)

```python
def __init__(self) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `_get_session`  (L81)

```python
async def _get_session(self) -> aiohttp.ClientSession
```

> 取得或惰性初始化共用的 aiohttp.ClientSession 連線池。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `intent_name`  (L121)

```python
@property
def intent_name(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `llm_description`  (L125)

```python
@property
def llm_description(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `response_mode`  (L129)

```python
@property
def response_mode(self) -> ToolResponseMode
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `can_handle_by_regex`  (L132)

```python
def can_handle_by_regex(self, text: str) -> bool
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `text` | `str` | `—` |

###### `_extract_danbooru_tags`  (L136)

```python
def _extract_danbooru_tags(self, user_query: str) -> list[str]
```

> 用 pixiv_tag.json 字典掃描使用者輸入，萃取精確的 Danbooru tag。  \n>         採「最長匹配優先」策略，避免短關鍵詞吃掉長關鍵詞。  \n>         例如：「蔚藍檔案星野的泳裝」→ ["hoshino_(blue_archive)", "swimsuit"]

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `user_query` | `str` | `—` |

###### `_translate_to_sd_prompt`  (L164)

```python
async def _translate_to_sd_prompt(self, user_query: str, brain: BotBrain) -> tuple[str, str]
```

> 將使用者中文描述轉換為二次元動漫風格專用的英文提示詞。  \n>         以畫美少女（2D 二次元）為主要目標，自動補上品質與風格標籤。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `user_query` | `str` | `—` |
| `brain` | `BotBrain` | `—` |

###### `_expand_prompt`  (L219)

```python
async def _expand_prompt(self, user_query: str, brain: BotBrain, seed_tags: list[str] | None = None) -> str
```

> 前置步驟：將使用者的簡短中文擴寫成豐富的英文繪圖提示詞。  \n>         若有 seed_tags（從 pixiv_tag.json 萃取的精確 Danbooru tag），  \n>         會將其作為 hint 一起送給 LLM，確保角色名與屬性標籤的精確性。  \n>         超時或失敗時回傳原始 user_query 作為備援。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `user_query` | `str` | `—` |
| `brain` | `BotBrain` | `—` |
| `seed_tags` | `list[str] | None` | `None` |

###### `_generate_gemini_image`  (L278)

```python
async def _generate_gemini_image(self, prompt: str) -> bytes | None
```

> 使用 Google Gemini Nano Banana (gemini-2.5-flash-image) 生成圖片。  \n>         支援自然語言 Prompt 描述。成功回傳圖片 bytes，超時或失敗回傳 None。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `prompt` | `str` | `—` |

###### `_generate_hf_animagine`  (L290)

```python
async def _generate_hf_animagine(self, positive_prompt: str, negative_prompt: str = '') -> bytes | None
```

> 使用 Hugging Face Inference API 呼叫 Animagine-XL-3.1 生成圖片。  \n>         Animagine-XL-3.1 是專為動漫訓練的 SDXL 模型，對动漫角色（包括蓋藍檔案、原神等）有良好識別。  \n>         需要 HF_TOKEN；https://huggingface.co/settings/tokens

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `positive_prompt` | `str` | `—` |
| `negative_prompt` | `str` | `''` |

###### `_build_animagine_prompt`  (L350)

```python
def _build_animagine_prompt(self, seed_tags: list[str], expanded_prompt: str) -> tuple[str, str]
```

> 针對 Animagine-XL-3.1 的最佳 prompt 格式組裝。  \n>         Animagine 最好的格式：  \n>           [character (game)], [game name], [appearance], [clothing/scene], [quality tags]  \n>         品質標籤的正確用詞是: masterpiece, high score, great score, absurdres

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `seed_tags` | `list[str]` | `—` |
| `expanded_prompt` | `str` | `—` |

###### `_generate_pollinations`  (L383)

```python
async def _generate_pollinations(self, positive_prompt: str, negative_prompt: str = '') -> bytes | None
```

> 使用 Pollinations.ai 雲端 API 生成圖片（二次元動漫風格）。  \n>         成功回傳圖片 bytes，超時或失敗回傳 None。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `positive_prompt` | `str` | `—` |
| `negative_prompt` | `str` | `''` |

###### `_generate_image_local`  (L426)

```python
async def _generate_image_local(self, positive_prompt: str, negative_prompt: str) -> bytes | None
```

> 使用本地 Stable Diffusion WebUI 生成圖片（備援方案）。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `positive_prompt` | `str` | `—` |
| `negative_prompt` | `str` | `—` |

###### `execute`  (L457)

```python
async def execute(self, bot: commands.Bot, message: discord.Message, user_query: str, vibe_snippet: str, full_context: str, **kwargs: object) -> ToolResult | str | None
```

> 畫圖工具核心。  \n>   \n>         Args:  \n>             bot: Discord Bot 實例。  \n>             message: 觸發訊息。  \n>             user_query: 使用者查詢。  \n>             vibe_snippet: 頻道氛圍。  \n>             full_context: 完整提示文本。  \n>             **kwargs: 包含 silent (bool) 等擴充參數。  \n>   \n>         Returns:  \n>             標準 ToolResult 成果合約、字串摘要或 None。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `bot` | `commands.Bot` | `—` |
| `message` | `discord.Message` | `—` |
| `user_query` | `str` | `—` |
| `vibe_snippet` | `str` | `—` |
| `full_context` | `str` | `—` |
| `**kwargs` | `object` | `—` |


---


### 📄 `handlers/social/tools/image_tool.py`

> handlers/social/tools/image_tool.py


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L25 |
| `_TAG_ALIASES` | `dict[str, str]` | `{'huge_boobs': 'large_breasts', 'big_boobs': 'large_breasts', 'medium_breasts': ...` | L30 |
| `_CONFLICT_ROOTS` | `list[list[str]]` | `[['large_breasts', 'small_breasts'], ['naked', 'swimsuit', 'underwear']]` | L47 |
| `_KEYWORD_MAPS` | `list[tuple[str, str, int]]` | `[('(巨乳\|大胸\|大奶\|huge boobs)', 'large_breasts', 2), ('(貧乳\|平胸\|微乳\|ちっぱい)', 'small_breas...` | L52 |


#### Class `ImageSearchTool` ← `BaseTool`  (L67)

> 二次元圖片搜尋工具，支援兩階段實體解析、分級動態標籤縮減與多圖庫檢索。

##### Methods

###### `intent_name`  (L71)

```python
@property
def intent_name(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `llm_description`  (L75)

```python
@property
def llm_description(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `response_mode`  (L79)

```python
@property
def response_mode(self) -> ToolResponseMode
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `can_handle_by_regex`  (L82)

```python
def can_handle_by_regex(self, text: str) -> bool
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `text` | `str` | `—` |

###### `__init__`  (L102)

```python
def __init__(self) -> None
```

> 初始化 ImageSearchTool，載入標籤字典、元資料與 BooruClient。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `_extract_entities_from_llm`  (L135)

```python
def _extract_entities_from_llm(self, raw_reply: str) -> dict[str, Any]
```

> 解析 LLM 結構化實體萃取回覆。  \n>   \n>         Args:  \n>             raw_reply: LLM 的原始回傳文字。  \n>   \n>         Returns:  \n>             實體萃取結果字典。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `raw_reply` | `str` | `—` |

###### `_resolve_single_tag`  (L163)

```python
def _resolve_single_tag(self, keyword: str) -> str
```

> 將單一中文/英文關鍵詞對齊為標準 Danbooru tag。  \n>   \n>         Args:  \n>             keyword: 輸入的詞彙。  \n>   \n>         Returns:  \n>             標準 tag 字串，若無對齊則回傳清理後的 snake_case。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `keyword` | `str` | `—` |

###### `_resolve_tag_tiers`  (L202)

```python
def _resolve_tag_tiers(self, user_query: str, entities: dict[str, Any] | None = None) -> tuple[list[str], list[str], list[str]]
```

> 將使用者輸入與實體萃取結果分流為三級權重標籤。  \n>   \n>         - Tier 1 (Core / 不可丟棄): 角色 (Category 4)、作品 (Category 3)  \n>         - Tier 2 (Style / 主要特徵): 服裝、姿勢、身材 (Category 0 - Clothing/Pose)  \n>         - Tier 3 (Context / 次要修飾): 場景、背景、物品、表情 (Category 0 - Scene/Item)  \n>   \n>         Args:  \n>             user_query: 使用者原始查詢。  \n>             entities: LLM 萃取的結構化實體。  \n>   \n>         Returns:  \n>             (tier1_core, tier2_style, tier3_context)

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `user_query` | `str` | `—` |
| `entities` | `dict[str, Any] | None` | `None` |

###### `_normalize_tier_tags`  (L332)

```python
def _normalize_tier_tags(self, tags: list[str]) -> list[str]
```

> 清理單一列表內的別名、衝突與重複項。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `tags` | `list[str]` | `—` |

###### `_tiered_booru_search`  (L356)

```python
async def _tiered_booru_search(self, tier1: list[str], tier2: list[str], tier3: list[str], is_nsfw: bool = False) -> tuple[str, list[str], bool, str]
```

> 智慧分級動態縮減搜尋（永遠優先保護 Tier 1 核心角色名）。  \n>   \n>         降級策略：  \n>           1. [Tier 1 + Tier 2 + Tier 3] ➔ Safebooru  \n>           2. [Tier 1 + Tier 2] (丟棄次要場景/道具) ➔ Safebooru  \n>           3. [Tier 1] (僅保留角色) + order:score ➔ Safebooru  \n>           4. [Tier 1] ➔ Danbooru (SFW 模式)  \n>           5. [Tier 1 去除 (series) 後綴] ➔ Danbooru (SFW 模式)  \n>           6. 保底策略 ➔ 1girl/solo  \n>   \n>         Returns:  \n>             (image_url, final_used_tags, is_fallback, source_site)

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `tier1` | `list[str]` | `—` |
| `tier2` | `list[str]` | `—` |
| `tier3` | `list[str]` | `—` |
| `is_nsfw` | `bool` | `False` |

###### `search`  (L445)

```python
async def search(self, user_query: str) -> tuple[str, list[str], bool]
```

> ReAct Agent 專用快速搜圖介面（本地解析 + 分級退避）。  \n>   \n>         Args:  \n>             user_query: 使用者的搜圖描述。  \n>   \n>         Returns:  \n>             (img_url, used_tags, is_fallback)

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `user_query` | `str` | `—` |

###### `execute`  (L469)

```python
async def execute(self, bot: commands.Bot, message: discord.Message, user_query: str, vibe_snippet: str, full_context: str, **kwargs: object) -> ToolResult
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `bot` | `commands.Bot` | `—` |
| `message` | `discord.Message` | `—` |
| `user_query` | `str` | `—` |
| `vibe_snippet` | `str` | `—` |
| `full_context` | `str` | `—` |
| `**kwargs` | `object` | `—` |


---


### 📄 `handlers/social/tools/music_tool.py`


#### Class `MusicTool` ← `BaseTool`  (L16)

##### Methods

###### `intent_name`  (L18)

```python
@property
def intent_name(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `llm_description`  (L22)

```python
@property
def llm_description(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `response_mode`  (L26)

```python
@property
def response_mode(self) -> ToolResponseMode
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `can_handle_by_regex`  (L29)

```python
def can_handle_by_regex(self, text: str) -> bool
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `text` | `str` | `—` |

###### `execute`  (L42)

```python
async def execute(self, bot: commands.Bot, message: discord.Message, user_query: str, vibe_snippet: str, full_context: str, **kwargs: object) -> ToolResult
```

> 音樂工具核心。  \n>   \n>         Returns:  \n>             標準 ToolResult 成果合約。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `bot` | `commands.Bot` | `—` |
| `message` | `discord.Message` | `—` |
| `user_query` | `str` | `—` |
| `vibe_snippet` | `str` | `—` |
| `full_context` | `str` | `—` |
| `**kwargs` | `object` | `—` |


---


### 📄 `handlers/social/tools/schedule_tool.py`

> handlers/social/tools/schedule_tool.py


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L24 |
| `_LIST_INTENT_REGEX` | `—` | `re.compile('(?:/schedule\\s+list)\|(?:排程列表)\|(?:目前的排程)\|(?:列出排程)\|(?:查看排程)\|(?:看.*排程)...` | L29 |
| `_DELETE_INTENT_REGEX` | `—` | `re.compile('(?:/schedule\\s+delete)\|(?:/schedule\\s+del)\|(?:刪除排程)\|(?:取消排程)\|(?:移除...` | L34 |
| `_DELETE_TASK_ID_REGEX` | `—` | `re.compile('[0-9a-fA-F]{8,32}')` | L39 |


#### Class `ScheduleTool` ← `BaseTool`  (L62)

##### Methods

###### `intent_name`  (L65)

```python
@property
def intent_name(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `llm_description`  (L69)

```python
@property
def llm_description(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `can_handle_by_regex`  (L77)

```python
def can_handle_by_regex(self, text: str) -> bool
```

> 嚴格禁止關鍵字硬比對，全交由 LLM Planner 語意規劃。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `text` | `str` | `—` |

###### `execute`  (L81)

```python
async def execute(self, bot: commands.Bot, message: discord.Message, user_query: str, vibe_snippet: str, full_context: str, **kwargs: object) -> ToolResult
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `bot` | `commands.Bot` | `—` |
| `message` | `discord.Message` | `—` |
| `user_query` | `str` | `—` |
| `vibe_snippet` | `str` | `—` |
| `full_context` | `str` | `—` |
| `**kwargs` | `object` | `—` |


#### Functions

##### `_detect_list_intent`  (L44)

```python
def _detect_list_intent(text: str) -> bool
```

> 偵測是否為列出排程的意圖。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `text` | `str` | `—` |

##### `_detect_delete_intent`  (L49)

```python
def _detect_delete_intent(text: str) -> str | None
```

> 偵測是否為刪除排程的意圖，若是則提取 task_id。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `text` | `str` | `—` |


---


### 📄 `handlers/social/tools/search_tools.py`

> handlers/social/tools/search_tools.py


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L38 |
| `DEFAULT_HTTP_TIMEOUT` | `int` | `getattr(settings, 'DEFAULT_HTTP_TIMEOUT', 10)` | L40 |
| `_search_session` | `aiohttp.ClientSession | None` | `None` | L41 |


#### Class `URLSummaryTool` ← `BaseTool`  (L420)

> 傳送網址 (HTTP/HTTPS) 時，自動抓取網頁全文並進行總結分析。

##### Methods

###### `intent_name`  (L424)

```python
@property
def intent_name(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `llm_description`  (L428)

```python
@property
def llm_description(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `can_handle_by_regex`  (L431)

```python
def can_handle_by_regex(self, text: str) -> bool
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `text` | `str` | `—` |

###### `execute`  (L437)

```python
async def execute(self, bot: commands.Bot, message: discord.Message, user_query: str, vibe_snippet: str, full_context: str, **kwargs: object) -> ToolResult
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `bot` | `commands.Bot` | `—` |
| `message` | `discord.Message` | `—` |
| `user_query` | `str` | `—` |
| `vibe_snippet` | `str` | `—` |
| `full_context` | `str` | `—` |
| `**kwargs` | `object` | `—` |


#### Class `AdvancedSearchTool` ← `BaseTool`  (L677)

> 相容性搜尋工具 (現已整合至 ChatTool 聯網對話系統中)。

    當 Intent 強制為 SEARCH 時，會調用檢索並交由 ChatTool 個性化回覆。

##### Methods

###### `intent_name`  (L684)

```python
@property
def intent_name(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `llm_description`  (L688)

```python
@property
def llm_description(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `can_handle_by_regex`  (L691)

```python
def can_handle_by_regex(self, text: str) -> bool
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `text` | `str` | `—` |

###### `execute`  (L694)

```python
async def execute(self, bot: commands.Bot, message: discord.Message, user_query: str, vibe_snippet: str, full_context: str, **kwargs: object) -> ToolResult
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `bot` | `commands.Bot` | `—` |
| `message` | `discord.Message` | `—` |
| `user_query` | `str` | `—` |
| `vibe_snippet` | `str` | `—` |
| `full_context` | `str` | `—` |
| `**kwargs` | `object` | `—` |


#### Functions

##### `_get_search_session`  (L44)

```python
async def _get_search_session() -> aiohttp.ClientSession
```

> 取得或延遲初始化共用之 aiohttp.ClientSession。

##### `extract_clean_url`  (L62)

```python
def extract_clean_url(text: str) -> str
```

> 從使用者訊息或字串中萃取乾淨且單一的有效 URL。  \n>   \n>     支援處理：  \n>     1. Discord Markdown 格式：[label](https://...) 或 [https://...](https://...)  \n>     2. 角括號格式：<https://...>  \n>     3. 一般文字夾帶 URL：過濾結尾標點符號與多餘括號。  \n>   \n>     Args:  \n>         text: 使用者輸入之原始文字。  \n>   \n>     Returns:  \n>         str: 萃取出的乾淨 URL 字串，若未找到則回傳空字串。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `text` | `str` | `—` |

##### `_extract_slug_keywords`  (L101)

```python
def _extract_slug_keywords(url: str) -> str
```

> 從 URL 路徑 Slug 提取有意義的主題關鍵字，用於 SPA 頁面備援搜尋。  \n>   \n>     Args:  \n>         url: 目標網頁 URL。  \n>   \n>     Returns:  \n>         str: 提取出之主題關鍵字字串。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `url` | `str` | `—` |

##### `is_safe_public_url`  (L137)

```python
def is_safe_public_url(url: str) -> bool
```

> 檢查目標網址是否為安全的公網 HTTP/HTTPS 位址，防禦 SSRF 攻擊。  \n>   \n>     阻擋私有 IP、迴路位址、Link-local、雲端 Metadata 服務 (如 169.254.169.254)、  \n>     Carrier-Grade NAT (RFC 6598) 與非 HTTP(S) 協定。  \n>   \n>     Args:  \n>         url: 待檢查的目標網址字串。  \n>   \n>     Returns:  \n>         bool: 若為安全公網位址則回傳 True，若存在安全風險或無法解析則回傳 False。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `url` | `str` | `—` |

##### `_check_redirect_safety`  (L207)

```python
async def _check_redirect_safety(url: str, timeout_sec: int = DEFAULT_HTTP_TIMEOUT, max_redirects: int = 5) -> str | None
```

> 檢查目標網址及其所有跳轉鏈 (Redirects) 是否皆為安全公網位址。  \n>   \n>     Args:  \n>         url: 待檢查的目標網址。  \n>         timeout_sec: 連線超時秒數。  \n>         max_redirects: 最大允許跳轉次數。  \n>   \n>     Returns:  \n>         str | None: 若所有跳轉目的地皆安全，回傳最終目標 URL；若偵測到非安全跳轉或異常則回傳 None。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `url` | `str` | `—` |
| `timeout_sec` | `int` | `DEFAULT_HTTP_TIMEOUT` |
| `max_redirects` | `int` | `5` |

##### `fetch_and_extract_url`  (L249)

```python
async def fetch_and_extract_url(url: str, timeout_sec: int = DEFAULT_HTTP_TIMEOUT) -> tuple[str, str, str]
```

> 多層次高品質網頁內文與情報萃取管線。  \n>   \n>     管線架構：  \n>     1. 第一層：trafilatura 深度清洗提取（針對傳統靜態文章）。  \n>     2. 第二層（Meta/DOM 備援）：BeautifulSoup 解析 OpenGraph、Meta Description、JSON-LD 與 <p> 標籤。  \n>     3. 第三層（SPA Slug 搜尋聯網回退）：若內文為空（如 MSN 等 SPA 架構），自動自 URL 路徑與標題提取關鍵字，並調用 DuckDuckGo 檢索即時新聞補足情資。  \n>   \n>     Args:  \n>         url: 目標網頁 URL。  \n>         timeout_sec: 請求逾時秒數。  \n>   \n>     Returns:  \n>         tuple[str, str, str]: (網頁標題, 萃取內文或情資摘要, 萃取方式標記)。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `url` | `str` | `—` |
| `timeout_sec` | `int` | `DEFAULT_HTTP_TIMEOUT` |

##### `fetch_web_and_entity_context`  (L568)

```python
async def fetch_web_and_entity_context(user_query: str) -> tuple[str, str]
```

> 通用即時聯網與實體檢索函式。  \n>   \n>     供 ChatTool 調用，進行新聞、時事、股票與實體資料檢索。  \n>     回傳 (intelligence_text, top_link)。  \n>   \n>     Args:  \n>         user_query: 使用者原始查詢語句。  \n>   \n>     Returns:  \n>         tuple[str, str]: (情報內文, 最佳來源連結)。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `user_query` | `str` | `—` |


---


### 📄 `handlers/social/tools/stock_tool.py`

> handlers/social/tools/stock_tool.py


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L46 |
| `_STOCK_DATA_CACHE` | `dict[str, tuple[float, bytes | None, dict[str, Any]]]` | `{}` | L49 |
| `_STOCK_CACHE_TTL_SEC` | `float` | `120.0` | L50 |
| `_KNOWN_CRYPTOS` | `set[str]` | `{'BTC', 'ETH', 'SOL', 'DOGE', 'XAUT', 'USDT', 'USDC', 'XRP', 'BNB', 'ADA', 'AVAX...` | L58 |
| `_CN_TO_TICKER` | `dict[str, str]` | `{'台積電': '2330.TW', '聯發科': '2454.TW', '鴻海': '2317.TW', '台塑': '1301.TW', '中鋼': '20...` | L68 |
| `_TICKER_REGEX` | `—` | `re.compile('(股票\|股價\|K線\|EMA\|RSI\|MACD\|技術指標\|技術分析\|走勢\|行情\|幣價\|趨勢\|分析\|\\b\\d{4}(?:\\.TW)?\...` | L90 |
| `_TICKER_EXTRACT_SYSTEM_PROMPT` | `—` | `'你是一個金融標的代號識別專家。請從使用者語句中萃取出所有的股票代號、加密貨幣 Symbol 或指數代碼。\n規則：\n- 若有多個標的，以逗號分隔（例如：23...` | L103 |
| `_STOCK_STOPWORDS` | `set[str]` | `{'TW', 'TWO', 'HK', 'SS', 'SZ', 'US', 'USD', 'USDT', 'BUSD', 'EUR', 'JPY', 'TWD'...` | L159 |


#### Class `StockTool` ← `BaseTool`  (L600)

> 股票 / 加密貨幣 / 指數查詢，附 K 線圖與技術指標報告。

##### Methods

###### `intent_name`  (L604)

```python
@property
def intent_name(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `response_mode`  (L608)

```python
@property
def response_mode(self) -> ToolResponseMode
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `llm_description`  (L612)

```python
@property
def llm_description(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `can_handle_by_regex`  (L615)

```python
def can_handle_by_regex(self, text: str) -> bool
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `text` | `str` | `—` |

###### `execute`  (L618)

```python
async def execute(self, bot: commands.Bot, message: discord.Message, user_query: str, vibe_snippet: str, full_context: str, **kwargs: object) -> ToolResult
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `bot` | `commands.Bot` | `—` |
| `message` | `discord.Message` | `—` |
| `user_query` | `str` | `—` |
| `vibe_snippet` | `str` | `—` |
| `full_context` | `str` | `—` |
| `**kwargs` | `object` | `—` |


#### Functions

##### `_parse_extracted_tickers`  (L113)

```python
def _parse_extracted_tickers(raw: str) -> list[str]
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `raw` | `str` | `—` |

##### `_resolve_ticker`  (L125)

```python
def _resolve_ticker(raw: str) -> str
```

> 將 LLM 萃取的字串轉為 yfinance ticker。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `raw` | `str` | `—` |

##### `_quick_extract_tickers`  (L173)

```python
def _quick_extract_tickers(text: str) -> list[str]
```

> 純用正則一步到位提取所有 tickers。  \n>     支援多標的同時提取（如：0050 00878 00981A / 台積電 鴻海 NVDA / BTC ETH）。  \n>     具備佔位遮罩機制，防止 0050.TW 中的 .TW 被重複識別為美股 TW。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `text` | `str` | `—` |

##### `_quick_extract_ticker`  (L229)

```python
def _quick_extract_ticker(text: str) -> str
```

> 單一標的相容介面。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `text` | `str` | `—` |

##### `_compute_rsi`  (L235)

```python
def _compute_rsi(series: pd.Series, period: int = 14) -> pd.Series
```

> 用 pandas ewm 計算 RSI，不依賴外部套件。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `series` | `pd.Series` | `—` |
| `period` | `int` | `14` |

##### `_compute_kd`  (L246)

```python
def _compute_kd(df: pd.DataFrame, n: int = 9, m1: int = 3, m2: int = 3) -> tuple[pd.Series, pd.Series]
```

> 計算 KD (隨機指標, Stochastic Oscillator)。  \n>   \n>     Args:  \n>         df: 包含 High, Low, Close 的歷史資料 DataFrame。  \n>         n: RSV 週期 (預設 9)。  \n>         m1: K 值平滑週期 (預設 3)。  \n>         m2: D 值平滑週期 (預設 3)。  \n>   \n>     Returns:  \n>         tuple[pd.Series, pd.Series]: (K 值序列, D 值序列)。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `df` | `pd.DataFrame` | `—` |
| `n` | `int` | `9` |
| `m1` | `int` | `3` |
| `m2` | `int` | `3` |

##### `_compute_bollinger_bands`  (L267)

```python
def _compute_bollinger_bands(series: pd.Series, period: int = 20, std_dev: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series, pd.Series]
```

> 計算布林通道 (Bollinger Bands)。  \n>   \n>     Returns:  \n>         tuple: (中軌 MA20, 上軌 Upper, 下軌 Lower, 帶寬 Bandwidth %, %B 位階)。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `series` | `pd.Series` | `—` |
| `period` | `int` | `20` |
| `std_dev` | `float` | `2.0` |

##### `_compute_atr`  (L282)

```python
def _compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series
```

> 計算真實波動區間 (Average True Range, ATR)。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `df` | `pd.DataFrame` | `—` |
| `period` | `int` | `14` |

##### `_fetch_ticker_news`  (L292)

```python
def _fetch_ticker_news(ticker: str, limit: int = 4) -> list[dict[str, str]]
```

> 抓取標的即時重大新聞與情報（在背景 executor 執行）。  \n>   \n>     具備智慧備援機制：當 ETF (如 0050, 00878) 或指數自身無專屬新聞 Feed 時，  \n>     自動備援抓取權值核心或大盤焦點新聞。  \n>   \n>     Args:  \n>         ticker: 股票或加密貨幣代號。  \n>         limit: 最大新聞則數。  \n>   \n>     Returns:  \n>         list[dict[str, str]]: 新聞清單 [{"title": ..., "publisher": ..., "time": ..., "url": ...}]。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `ticker` | `str` | `—` |
| `limit` | `int` | `4` |

##### `_fetch_and_plot`  (L370)

```python
def _fetch_and_plot(ticker: str) -> tuple[bytes | None, dict]
```

> 同步函式：抓資料、計算指標、抓取新聞、畫圖（在 executor 中執行，不阻塞 event loop）。  \n>     回傳 (png_bytes, indicators_dict)。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `ticker` | `str` | `—` |


---


### 📄 `handlers/social/tools/sys_inspect_tool.py`

> handlers.social.tools.sys_inspect_tool


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L19 |


#### Class `SystemInspectTool` ← `BaseTool`  (L22)

> 查詢主機硬體負載與雪莉內部運行狀態工具。

##### Methods

###### `intent_name`  (L26)

```python
@property
def intent_name(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `llm_description`  (L30)

```python
@property
def llm_description(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `execute`  (L33)

```python
async def execute(self, bot: commands.Bot, message: discord.Message, user_query: str, vibe_snippet: str, full_context: str, **kwargs: object) -> ToolResult
```

> 執行系統快照採樣並回傳高密度觀察文本。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `bot` | `commands.Bot` | `—` |
| `message` | `discord.Message` | `—` |
| `user_query` | `str` | `—` |
| `vibe_snippet` | `str` | `—` |
| `full_context` | `str` | `—` |
| `**kwargs` | `object` | `—` |


---


### 📄 `handlers/social/tools/weather_tool.py`

> handlers/social/tools/weather_tool.py


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L34 |
| `_WEATHER_REGEX` | `—` | `re.compile('(天氣\|氣溫\|溫度\|會下雨\|下雨嗎\|紫外線\|體感\|濕度\|幾度\|weather\|降雨\|颱風)', re.IGNORECASE)` | L36 |
| `_CITY_MAP` | `dict[str, str]` | `{'台北': 'Taipei', '新北': 'New Taipei City', '桃園': 'Taoyuan', '台中': 'Taichung', '台南...` | L42 |
| `_OWM_API_URL` | `—` | `settings.OPENWEATHERMAP_API_URL` | L54 |


#### Class `WeatherTool` ← `BaseTool`  (L74)

> 即時天氣查詢，雪莉用語氣播報。

##### Methods

###### `__init__`  (L77)

```python
def __init__(self) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `_get_session`  (L80)

```python
async def _get_session(self) -> aiohttp.ClientSession
```

> 取得或惰性初始化共用的 aiohttp.ClientSession 連線池。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `intent_name`  (L89)

```python
@property
def intent_name(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `llm_description`  (L93)

```python
@property
def llm_description(self) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `can_handle_by_regex`  (L96)

```python
def can_handle_by_regex(self, text: str) -> bool
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `text` | `str` | `—` |

###### `execute`  (L99)

```python
async def execute(self, bot: commands.Bot, message: discord.Message, user_query: str, vibe_snippet: str, full_context: str, **kwargs: object) -> ToolResult
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `bot` | `commands.Bot` | `—` |
| `message` | `discord.Message` | `—` |
| `user_query` | `str` | `—` |
| `vibe_snippet` | `str` | `—` |
| `full_context` | `str` | `—` |
| `**kwargs` | `object` | `—` |


#### Functions

##### `_weather_desc_to_emoji`  (L57)

```python
def _weather_desc_to_emoji(description: str) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `description` | `str` | `—` |


---


### 📄 `handlers/social/utils.py`


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L11 |
| `SAFETY_FOOTER` | `—` | `'\n\n【角色與安全提醒】\n永遠用妳自己鮮明活潑的方式作出任何不可預期的回應！如不明白便吐槽。\n嚴禁發送空言論死機。'` | L13 |
| `BOT_MESSAGE_INTENTS` | `dict[int, Any]` | `{}` | L19 |
| `_TOOL_REPLY_REGEX` | `—` | `re.compile('(正在播放\|播放清單\|已加入\|已跳過\|已暫停\|已停止\|開始播放\|YouTube\|時長:\|開始破曉魔播！\|我收到神秘的電波了\|已接收播放指...` | L21 |
| `_CONTEXT_COMPRESS_CACHE` | `—` | `ContextCompressionCache(capacity=256, default_ttl=60.0)` | L244 |


#### Class `ContextCompressionCache` ← `object`  (L164)

> 具備容量上限 (LRU)、時間 TTL 與最新訊息水位感知之對話快取容器。

##### Methods

###### `__init__`  (L167)

```python
def __init__(self, capacity: int = 256, default_ttl: float = 60.0) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `capacity` | `int` | `256` |
| `default_ttl` | `float` | `60.0` |

###### `__contains__`  (L172)

```python
def __contains__(self, key: Any) -> bool
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `key` | `Any` | `—` |

###### `__getitem__`  (L175)

```python
def __getitem__(self, key: Any) -> tuple[float, str]
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `key` | `Any` | `—` |

###### `__setitem__`  (L179)

```python
def __setitem__(self, key: Any, value: tuple[float, str] | tuple[float, str, int]) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `key` | `Any` | `—` |
| `value` | `tuple[float, str] | tuple[float, str, int]` | `—` |

###### `set`  (L189)

```python
def set(self, key: Any, last_msg_id: int, summary: str) -> None
```

> 提供易讀的 set 介面儲存摘要與水位。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `key` | `Any` | `—` |
| `last_msg_id` | `int` | `—` |
| `summary` | `str` | `—` |

###### `get`  (L193)

```python
def get(self, key: Any, default: Any = None) -> tuple[float, str] | Any
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `key` | `Any` | `—` |
| `default` | `Any` | `None` |

###### `get_valid_summary`  (L200)

```python
def get_valid_summary(self, key: Any, last_msg_id: int, ttl: float | None = None) -> str | None
```

> 驗證 TTL 與最新訊息 ID 水位，最後訊息 ID 吻合且未過期才回傳摘要文字。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `key` | `Any` | `—` |
| `last_msg_id` | `int` | `—` |
| `ttl` | `float | None` | `None` |

###### `get_raw_summary`  (L216)

```python
def get_raw_summary(self, key: Any, ttl: float | None = None) -> str | None
```

> 取得上一輪已快取的濃縮摘要文字（供水位節流疊加新訊息使用）。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `key` | `Any` | `—` |
| `ttl` | `float | None` | `None` |

###### `get_watermark`  (L226)

```python
def get_watermark(self, key: Any) -> int
```

> 回傳頻道已濃縮到的最新訊息水位（無紀錄則回傳 0）。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `key` | `Any` | `—` |

###### `clear`  (L236)

```python
def clear(self) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `__len__`  (L239)

```python
def __len__(self) -> int
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |


#### Functions

##### `is_admin_user`  (L30)

```python
def is_admin_user(target: discord.Member | discord.User | discord.Interaction | None) -> bool
```

> 判斷目標使用者或 Interaction 是否具備管理員權限。  \n>   \n>     相容 discord.Interaction、discord.Member 與 discord.User。  \n>     判定依序：  \n>     1. settings.ADMIN_USER_IDS 明確指定的使用者。  \n>     2. 伺服器擁有者 (Guild Owner)。  \n>     3. 具備 Discord 原生 Administrator（系統管理員）權限之成員。  \n>     4. settings.ADMIN_ROLE_IDS 明確指定之管理身分組成員。  \n>     遵循安全原則預設拒絕（Fail-Closed）：若皆不符合，判定為無權限。  \n>   \n>     Args:  \n>         target: Discord Interaction、Member 或 User 物件。  \n>   \n>     Returns:  \n>         bool: 是否具備管理員權限。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `target` | `discord.Member | discord.User | discord.Interaction | None` | `—` |

##### `append_thinking_log`  (L85)

```python
def append_thinking_log(entry: dict[str, Any]) -> None
```

> 寫入插話評估記錄至統一觀測系統。  \n>   \n>     Args:  \n>         entry: 評估日誌內容字典。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `entry` | `dict[str, Any]` | `—` |

##### `append_deep_thinking_log`  (L102)

```python
def append_deep_thinking_log(thought_text: str, prompt: str = '', intent: str = 'CHAT', full_reply: str = '', model: str = '', system_prompt: str = '', user_prompt: str = '', latency_ms: int = 0) -> None
```

> 寫入深度思考與對話回覆記錄至統一觀測系統。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `thought_text` | `str` | `—` |
| `prompt` | `str` | `''` |
| `intent` | `str` | `'CHAT'` |
| `full_reply` | `str` | `''` |
| `model` | `str` | `''` |
| `system_prompt` | `str` | `''` |
| `user_prompt` | `str` | `''` |
| `latency_ms` | `int` | `0` |

##### `append_draw_thinking_log`  (L124)

```python
def append_draw_thinking_log(user_query: str, seed_tags: list[str], expanded_prompt: str, final_prompt: str, negative_prompt: str, source: str, success: bool, model_used: str = '') -> None
```

> 寫入繪圖推論與標籤擴展記錄至統一觀測系統。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `user_query` | `str` | `—` |
| `seed_tags` | `list[str]` | `—` |
| `expanded_prompt` | `str` | `—` |
| `final_prompt` | `str` | `—` |
| `negative_prompt` | `str` | `—` |
| `source` | `str` | `—` |
| `success` | `bool` | `—` |
| `model_used` | `str` | `''` |

##### `append_debug_log`  (L148)

```python
def append_debug_log(text: str) -> None
```

> 寫入使用者除錯回報記錄至統一觀測系統。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `text` | `str` | `—` |

##### `_estimate_tokens`  (L157)

```python
def _estimate_tokens(text: str) -> int
```

> 中英混合與程式碼文字的 Token 數快速估算。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `text` | `str` | `—` |

##### `fetch_context_script`  (L247)

```python
async def fetch_context_script(channel: Any, before_message: discord.Message | None = None, bot: Any = None) -> str
```

> 擷取頻道歷史發言紀錄並格式化為上下文劇本。  \n>   \n>     對話歷史採嚴格 Append-Only 格式，保護 Prefix Caching / KV Cache 命中率。  \n>     密集長對話 (> CONTEXT_COMPRESS_THRESHOLD) 會以主力模型濃縮成摘要，  \n>     當水位節流觸發時，取出前輪快取摘要疊加最新訊息，避免回退全量 raw 歷史。  \n>     無論濃縮是否發生，歷史區塊都有硬性封頂（CONTEXT_HISTORY_MAX_TOKENS）兜底。  \n>   \n>     Args:  \n>         channel: Discord 頻道物件。  \n>         before_message: 基準訊息（可選）。  \n>         bot: BotBrain / Bot 實例，供濃縮呼叫模型（可選）。  \n>   \n>     Returns:  \n>         str: 格式化後的上下文對話紀錄（可能已濃縮）。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `channel` | `Any` | `—` |
| `before_message` | `discord.Message | None` | `None` |
| `bot` | `Any` | `None` |

##### `_now_ts`  (L441)

```python
def _now_ts() -> float
```

> 模組層級時間戳輔助。

##### `_summarize_context`  (L446)

```python
async def _summarize_context(bot: Any, raw_context: str) -> str
```

> 用主力模型把密集對話濃縮成話題摘要（不帶人設，乾淨事實擷取）。  \n>   \n>     Args:  \n>         bot: Discord Bot 或 Cog 實例。  \n>         raw_context: 原始對話上下文文字。  \n>   \n>     Returns:  \n>         str: 濃縮後的話題摘要字串，若失敗則回傳空字串。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `bot` | `Any` | `—` |
| `raw_context` | `str` | `—` |

##### `fetch_recent_script`  (L500)

```python
async def fetch_recent_script(channel: Any, limit: int) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `channel` | `Any` | `—` |
| `limit` | `int` | `—` |

##### `strip_markdown`  (L538)

```python
def strip_markdown(text: str) -> str
```

> 移除模型偷塞的 Markdown 格式化符號，確保在 Discord 中顯示為純文字。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `text` | `str` | `—` |

##### `_process_micro_cot_extraction`  (L555)

```python
def _process_micro_cot_extraction(reply: str, prompt: str = '', intent: str = 'CHAT', model: str = '', system_prompt: str = '', user_prompt: str = '', latency_ms: int = 0) -> tuple[str, str]
```

> Returns (clean_text, thought_log)

| 參數 | 型別 | 預設值 |
|---|---|---|
| `reply` | `str` | `—` |
| `prompt` | `str` | `''` |
| `intent` | `str` | `'CHAT'` |
| `model` | `str` | `''` |
| `system_prompt` | `str` | `''` |
| `user_prompt` | `str` | `''` |
| `latency_ms` | `int` | `0` |

##### `_chunk_message`  (L583)

```python
def _chunk_message(text: str, max_length: int = 1900) -> list[str]
```

> 將超長訊息安全拆分為不超過 max_length 的片段，避免 Discord 50035 Invalid Form Body

| 參數 | 型別 | 預設值 |
|---|---|---|
| `text` | `str` | `—` |
| `max_length` | `int` | `1900` |

##### `remove_hourglass_reaction`  (L605)

```python
async def remove_hourglass_reaction(target: discord.Message | Any, bot_user: discord.User | discord.Member | discord.ClientUser | None = None) -> None
```

> 安全移除訊息上的 ⏳ 沙漏反應。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `target` | `discord.Message | Any` | `—` |
| `bot_user` | `discord.User | discord.Member | discord.ClientUser | None` | `None` |

##### `_extract_pure_thought_summary`  (L633)

```python
def _extract_pure_thought_summary(raw_thought: str, max_len: int = 180) -> str
```

> 從複合思考日誌中萃取純思考心智摘要，濾除已在工具步驟列印之觀測數據。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `raw_thought` | `str` | `—` |
| `max_len` | `int` | `180` |

##### `format_thinking_steps_block`  (L658)

```python
def format_thinking_steps_block(thought_log: str = '', intent_name: str = 'CHAT', model: str = '', latency_ms: int = 0, prompt: str = '', prefill_ms: int | None = None, thought_ms: int | None = None, tool_steps: list[Any] | None = None, generation_ms: int | None = None) -> str
```

> 建構精美步驟化思考推演 Markdown 區塊 (方案 A 模式)。  \n>   \n>     Args:  \n>         thought_log: 原始思考日誌 (包含 Plan/Obs/Persona 或純 CoT)。  \n>         intent_name: 意圖標籤名稱。  \n>         model: 使用之模型名稱。  \n>         latency_ms: 總執行耗時（毫秒）。  \n>         prompt: 使用者原始輸入。  \n>         prefill_ms: 首字生成 / 前綴快取運算耗時（毫秒，可選）。  \n>         thought_ms: 心智思考推演耗時（毫秒，可選）。  \n>         tool_steps: 工具調用或執行步驟紀錄列表（可選）。  \n>         generation_ms: 最終對話生成耗時（毫秒，可選）。  \n>   \n>     Returns:  \n>         str: Markdown 引用格式之思考與步驟區塊。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `thought_log` | `str` | `''` |
| `intent_name` | `str` | `'CHAT'` |
| `model` | `str` | `''` |
| `latency_ms` | `int` | `0` |
| `prompt` | `str` | `''` |
| `prefill_ms` | `int | None` | `None` |
| `thought_ms` | `int | None` | `None` |
| `tool_steps` | `list[Any] | None` | `None` |
| `generation_ms` | `int | None` | `None` |

##### `simulate_typing_and_send`  (L792)

```python
async def simulate_typing_and_send(target: Any, reply: str, intent_name: str = 'CHAT', prompt: str = '', model: str = '', system_prompt: str = '', user_prompt: str = '', latency_ms: int = 0, thought_log: str = '', target_user_id: int | None = None) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `target` | `Any` | `—` |
| `reply` | `str` | `—` |
| `intent_name` | `str` | `'CHAT'` |
| `prompt` | `str` | `''` |
| `model` | `str` | `''` |
| `system_prompt` | `str` | `''` |
| `user_prompt` | `str` | `''` |
| `latency_ms` | `int` | `0` |
| `thought_log` | `str` | `''` |
| `target_user_id` | `int | None` | `None` |

##### `simulate_typing_and_send_to_channel`  (L885)

```python
async def simulate_typing_and_send_to_channel(channel: discord.TextChannel | Any, reply: str, intent_name: str = 'CHAT', prompt: str = '', model: str = '', system_prompt: str = '', user_prompt: str = '', latency_ms: int = 0, thought_log: str = '', target_user_id: int | None = None) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `channel` | `discord.TextChannel | Any` | `—` |
| `reply` | `str` | `—` |
| `intent_name` | `str` | `'CHAT'` |
| `prompt` | `str` | `''` |
| `model` | `str` | `''` |
| `system_prompt` | `str` | `''` |
| `user_prompt` | `str` | `''` |
| `latency_ms` | `int` | `0` |
| `thought_log` | `str` | `''` |
| `target_user_id` | `int | None` | `None` |


---


<a id="memory"></a>

## 🧬 `handlers/memory/`


### 📄 `handlers/memory/__init__.py`


#### Functions

##### `setup`  (L5)

```python
async def setup(bot: commands.Bot) -> None
```

> 替神經接入具備個體反芻能力的『超級左腦』

| 參數 | 型別 | 預設值 |
|---|---|---|
| `bot` | `commands.Bot` | `—` |


---


### 📄 `handlers/memory/chat_logger.py`

> handlers/memory/chat_logger.py


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L22 |
| `_TZ_TW` | `—` | `timezone(timedelta(hours=8))` | L24 |
| `DB_PATH` | `—` | `settings.DATA_DIR / 'chat_history.db'` | L25 |
| `_BUFFER_LOCK` | `—` | `threading.Lock()` | L27 |
| `_LOG_BUFFER` | `list[tuple[str, dict[str, Any]]]` | `[]` | L28 |
| `_BUFFER_MAX_SIZE` | `int` | `10` | L29 |
| `_CHAT_DB_INITIALIZED` | `bool` | `False` | L32 |


#### Functions

##### `_init_chat_db`  (L35)

```python
def _init_chat_db() -> None
```

> 初始化 chat_history.db 資料表 (僅首次執行)。

##### `_today_str`  (L75)

```python
def _today_str() -> str
```

> 回傳台灣時間的今日日期字串 YYYY-MM-DD。

##### `flush_chat_logs`  (L80)

```python
def flush_chat_logs() -> None
```

> 將緩衝區內所有未落盤的聊天記錄批次寫入 SQLite 資料庫中 (標記 is_deleted = 0)。

##### `append_chat_log`  (L111)

```python
def append_chat_log(message: discord.Message) -> None
```

> 把一則訊息加入記憶體緩衝區；若滿 _BUFFER_MAX_SIZE 筆則批次非同步落盤。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `message` | `discord.Message` | `—` |

##### `async_append_chat_log`  (L147)

```python
async def async_append_chat_log(message: discord.Message) -> None
```

> 非同步版本的追加寫入，於需要 Flush 時在 ThreadPool 中執行，避免阻塞事件迴圈。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `message` | `discord.Message` | `—` |

##### `read_chat_log`  (L156)

```python
def read_chat_log(date_str: str, server_id: str = '') -> list[dict[str, Any]]
```

> 讀取指定日期的所有未刪除訊息記錄 (WHERE is_deleted = 0)，可依 server_id 過濾。  \n>   \n>     回傳 list[dict]，順序為寫入順序（時間正序）。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `date_str` | `str` | `—` |
| `server_id` | `str` | `''` |

##### `is_date_processed`  (L225)

```python
def is_date_processed(date_str: str) -> bool
```

> 確認該日期是否已整合過。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `date_str` | `str` | `—` |

##### `mark_date_processed`  (L246)

```python
def mark_date_processed(date_str: str, stats: Optional[dict[str, Any]] = None) -> None
```

> 標記該日期已完成整合。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `date_str` | `str` | `—` |
| `stats` | `Optional[dict[str, Any]]` | `None` |

##### `get_yesterday_str`  (L278)

```python
def get_yesterday_str() -> str
```

> 回傳台灣時間的昨日日期字串 YYYY-MM-DD。


---


### 📄 `handlers/memory/cog.py`


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L22 |


#### Class `MemoryManager` ← `commands.Cog`  (L25)

> 靈魂記錄者！追尋全局單一個體 UID，於日常背景中抓取反思屬性的深睡大腦層。

##### Methods

###### `__init__`  (L28)

```python
def __init__(self, bot: commands.Bot) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `bot` | `commands.Bot` | `—` |

###### `cog_load`  (L51)

```python
async def cog_load(self) -> None
```

> Cog 載入時確保定時 Flush 迴圈已啟動。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `cog_unload`  (L56)

```python
async def cog_unload(self) -> None
```

> Cog 卸載或關機時，強制落盤好感度與聊天記錄。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `_flush_loop`  (L65)

```python
@tasks.loop(seconds=60.0)
async def _flush_loop(self) -> None
```

> 週期性（每 60 秒）檢查並落盤好感度與聊天緩衝。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `reload_store`  (L78)

```python
def reload_store(self) -> None
```

> 從磁碟重新載入最新的 user_memory.json，確保 RAM 副本與磁碟一致。  \n>   \n>         NightlyDigest 完成深夜整合並寫回磁碟後應呼叫此方法，  \n>         防止白天的即時萃取使用開機時的舊副本覆蓋掉深夜整合的成果。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `get_user_memory_snippet`  (L93)

```python
def get_user_memory_snippet(self, user_id: int, max_facts: int | None = None, query_text: str = '') -> str
```

> 產生注入 LLM Prompt 的使用者記憶片段（結合類別多樣性與 Query 相關性選取）。  \n>   \n>         Args:  \n>             user_id: Discord 使用者 ID。  \n>             max_facts: 注入的事實數量上限（若為 None 則使用 MAX_FACTS_IN_CONTEXT）。  \n>             query_text: 當前使用者輸入文字，用於關鍵字相關性打分（可選）。  \n>   \n>         Returns:  \n>             格式化後的長期記憶字串。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `user_id` | `int` | `—` |
| `max_facts` | `int | None` | `None` |
| `query_text` | `str` | `''` |

###### `get_full_user_memory_snippet`  (L145)

```python
def get_full_user_memory_snippet(self, user_id: int) -> str
```

> 產生供管理者或使用者查看的完整使用者記憶清單（列出全部事實，不進行 Prompt 取樣截斷與相似度過濾）。  \n>   \n>         Args:  \n>             user_id: Discord 使用者 ID。  \n>   \n>         Returns:  \n>             格式化後的完整長期記憶字串。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `user_id` | `int` | `—` |

###### `cmd_forget`  (L186)

```python
@app_commands.command(name='forget', description='清除或檢視雪莉對你的個人長期記憶 (GDPR/隱私規範)')
@app_commands.describe(action='操作項目：view (檢視) 或 clear_all (全部抹除)')
async def cmd_forget(self, interaction: discord.Interaction, action: Literal['view', 'clear_all'] = 'view') -> None
```

> 供使用者自主檢視或刪除在雪莉大腦中的個人特徵事實。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |
| `action` | `Literal['view', 'clear_all']` | `'view'` |

###### `on_message`  (L214)

```python
@commands.Cog.listener()
async def on_message(self, message: discord.Message) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `message` | `discord.Message` | `—` |

###### `_run_reflection_extract`  (L253)

```python
async def _run_reflection_extract(self, author: discord.Member | discord.User, texts: str) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `author` | `discord.Member | discord.User` | `—` |
| `texts` | `str` | `—` |


---


### 📄 `handlers/memory/memory_archive.py`

> handlers/memory/memory_archive.py


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L29 |
| `_META_PATTERN` | `—` | `re.compile('<!-- world-week; servers=(\\d+); events=(\\d+); participants=(\\d+) ...` | L31 |
| `_WEEK_FILE_PATTERN` | `—` | `re.compile('^(\\d{4}-\\d{2}-\\d{2})_\\d{4}-W\\d{1,2}\\.md$')` | L32 |


#### Functions

##### `_archive_dir`  (L35)

```python
def _archive_dir() -> Path
```

##### `weekly_dir`  (L39)

```python
def weekly_dir() -> Path
```

> 取得週歸檔存放目錄路徑（若不存在則自動遞迴建立）。  \n>   \n>     Returns:  \n>         Path: 週歸檔目錄路徑。

##### `monthly_dir`  (L50)

```python
def monthly_dir() -> Path
```

> 取得月歸檔存放目錄路徑（若不存在則自動遞迴建立）。  \n>   \n>     Returns:  \n>         Path: 月歸檔目錄路徑。

##### `_week_start`  (L61)

```python
def _week_start(week_end: date) -> date
```

> 回傳該週的週一（ISO 週，week_end 為該週最後一天，預設週日）。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `week_end` | `date` | `—` |

##### `weekly_path`  (L66)

```python
def weekly_path(week_end: date) -> Path
```

> 計算指定週結束日對應的週歸檔檔案完整路徑。  \n>   \n>     Args:  \n>         week_end: 該週的最後一日（date 物件）。  \n>   \n>     Returns:  \n>         Path: 週歸檔 Markdown 檔案路徑（如：2026-08-31_2026-W36.md）。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `week_end` | `date` | `—` |

##### `monthly_path`  (L80)

```python
def monthly_path(anchor: date) -> Path
```

> 計算指定基準日期所屬月份之月歸檔檔案完整路徑。  \n>   \n>     Args:  \n>         anchor: 該月份內之任一基準日期（date 物件）。  \n>   \n>     Returns:  \n>         Path: 月歸檔 Markdown 檔案路徑（如：2026-08.md）。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `anchor` | `date` | `—` |

##### `_collect_week_events`  (L92)

```python
def _collect_week_events(week_start: date, week_end: date) -> list[dict[str, Any]]
```

> 從 world_events（原始表，30 天內必有資料）收集整個週的事件，依時間排序。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `week_start` | `date` | `—` |
| `week_end` | `date` | `—` |

##### `_build_weekly_md`  (L103)

```python
def _build_weekly_md(week_start: date, week_end: date, events: list[dict[str, Any]]) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `week_start` | `date` | `—` |
| `week_end` | `date` | `—` |
| `events` | `list[dict[str, Any]]` | `—` |

##### `_write_text`  (L145)

```python
def _write_text(path: Path, content: str) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `path` | `Path` | `—` |
| `content` | `str` | `—` |

##### `_merge_monthly`  (L150)

```python
def _merge_monthly(week_start: date) -> Path
```

> 掃描該月內所有週檔，合併為月歸檔（每次重算，保持單一權威來源）。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `week_start` | `date` | `—` |

##### `_parse_meta`  (L183)

```python
def _parse_meta(path: Path) -> dict[str, int]
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `path` | `Path` | `—` |

##### `_purge_old_weekly`  (L198)

```python
def _purge_old_weekly() -> int
```

> 清理超過 WORLD_ARCHIVE_KEEP_WEEKS 週的週檔（依檔名內嵌的週一日期判定）。

##### `archive_week`  (L221)

```python
def archive_week(week_end: date) -> Path | None
```

> 把 week_end 所在整個週的世界事件歸檔為 Markdown（可重複呼叫，已存在則跳過）。  \n>   \n>     Args:  \n>         week_end: 該週的最後一天（ISO 週日）。週一~週日對應同一週。  \n>   \n>     Returns:  \n>         Path | None: 新建立的週檔路徑，若該週已歸檔則回傳 None。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `week_end` | `date` | `—` |

##### `archive_finished_week`  (L248)

```python
def archive_finished_week(date_str: str) -> Path | None
```

> 由 NightlyDigest 呼叫：只在「週結束日（預設週日）」歸檔整週。  \n>   \n>     Args:  \n>         date_str: 昨日日期字串（YYYY-MM-DD）。  \n>   \n>     Returns:  \n>         Path | None: 落盤的週檔路徑，非歸檔日或已存在則回傳 None。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `date_str` | `str` | `—` |

##### `list_monthly_archives`  (L264)

```python
def list_monthly_archives() -> list[Path]
```

> 列出所有月歸檔（供日後注入/瀏覽使用）。

##### `main`  (L269)

```python
def main() -> None
```

> 手動觸發歸檔用：python -m handlers.memory.memory_archive 2026-08-30


---


### 📄 `handlers/memory/nightly_cog.py`

> handlers/memory/nightly_cog.py


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L41 |
| `_TZ_TW` | `—` | `timezone(timedelta(hours=8))` | L43 |
| `_MIN_MSGS_THRESHOLD` | `—` | `getattr(settings, 'NIGHTLY_MIN_MSGS_THRESHOLD', 5)` | L46 |
| `_NIGHTLY_SYSTEM_PROMPT` | `—` | `'【深夜靈魂整合引擎 (Nightly Reflection AI)】\n這是一份來自群友「{display_name}」今日完整的聊天記錄，內容遠比平時的片段...` | L49 |


#### Class `NightlyDigest` ← `commands.Cog`  (L89)

> 凌晨 3:00 深夜記憶整合工人。

##### Methods

###### `__init__`  (L92)

```python
def __init__(self, bot: commands.Bot) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `bot` | `commands.Bot` | `—` |

###### `cog_load`  (L96)

```python
async def cog_load(self) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `cog_unload`  (L100)

```python
async def cog_unload(self) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `_nightly_loop`  (L108)

```python
@tasks.loop(minutes=1)
async def _nightly_loop(self) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `_before_loop`  (L120)

```python
@_nightly_loop.before_loop
async def _before_loop(self) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `cmd_nightly_digest`  (L128)

```python
@commands.command(name='nightly_digest', hidden=True)
@commands.is_owner()
async def cmd_nightly_digest(self, ctx: commands.Context, date_str: str = '') -> None
```

> 手動觸發深夜整合（管理員用，供測試）。用法：!nightly_digest [YYYY-MM-DD]

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `ctx` | `commands.Context` | `—` |
| `date_str` | `str` | `''` |

###### `_run_digest`  (L146)

```python
async def _run_digest(self, date_str: str, force: bool = False) -> dict | None
```

> 讀取指定日期的 chat log，對每位使用者執行深度反射萃取，更新 user_memory.json。  \n>         force=True 時忽略 .processed 標記（手動觸發用）。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `date_str` | `str` | `—` |
| `force` | `bool` | `False` |


---


### 📄 `handlers/memory/relationship.py`


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L10 |
| `MEMORY_DB_PATH` | `—` | `settings.DATA_DIR / 'sherry_memory.db'` | L11 |
| `_RELATIONS_DB_INITIALIZED` | `bool` | `False` | L14 |
| `_INITIALIZED_RELATIONS_DBS` | `set[str]` | `set()` | L17 |


#### Class `RelationshipManager` ← `object`  (L20)

> 社交關係管理器（好感度、關係等級，支援 SQLite 持久化）。

    資料庫表：sherry_memory.db -> user_relations

##### Methods

###### `__init__`  (L38)

```python
def __init__(self, debounce_seconds: float = 30.0) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `debounce_seconds` | `float` | `30.0` |

###### `_init_db`  (L47)

```python
def _init_db(self) -> None
```

> 初始化 user_relations 資料表 (僅首次執行)。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `_load`  (L74)

```python
def _load(self) -> None
```

> 優先自 SQLite 載入好感度，若無資料則讀取 JSON 備援。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `_do_write_sync`  (L112)

```python
def _do_write_sync(self, store_copy: dict[str, dict[str, Any]]) -> None
```

> 同步寫入 SQLite 與備份 JSON (在 ThreadPool 中執行)。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `store_copy` | `dict[str, dict[str, Any]]` | `—` |

###### `flush`  (L143)

```python
def flush(self, force: bool = False) -> None
```

> 強制或髒標記落盤。  \n>   \n>         Args:  \n>             force: 若為 True，即使 _dirty 為 False 也強制寫入磁碟。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `force` | `bool` | `False` |

###### `_schedule_debounce`  (L162)

```python
def _schedule_debounce(self) -> None
```

> 排程延遲落盤任務（Debounce）。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `_save`  (L176)

```python
def _save(self) -> None
```

> 相容舊版直接存檔介面，觸發 flush。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `get_affinity`  (L183)

```python
def get_affinity(self, uid: int) -> float
```

> 回傳目前好感度（float，保留小數精度）。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `uid` | `int` | `—` |

###### `update_affinity`  (L188)

```python
def update_affinity(self, uid: int, delta: int | float) -> None
```

> 更新好感度（以 float 精度儲存，防止 0.5 被 int() 截斷）。  \n>   \n>         採用 Debounce 機制延遲落盤，並內建每日防刷上限與總量封頂保護。  \n>   \n>         Args:  \n>             uid: Discord 使用者 ID。  \n>             delta: 好感度變化量（可為小數，如 0.5）。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `uid` | `int` | `—` |
| `delta` | `int | float` | `—` |

###### `get_affinity_tier`  (L229)

```python
def get_affinity_tier(self, uid: int) -> str
```

> 根據當前好感度回傳關係等級（使用 float 比較）。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `uid` | `int` | `—` |

###### `decay_all`  (L237)

```python
def decay_all(self) -> int
```

> 對所有使用者進行每日好感度自然衰減（僅對 affinity > 0 者生效）。  \n>         每天至多呼叫一次。回傳受影響的使用者數量。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `get_relationship_snippet`  (L261)

```python
def get_relationship_snippet(self, uid: int) -> str
```

> 產生可注入 LLM Prompt 的關係狀態摘要。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `uid` | `int` | `—` |


---


### 📄 `handlers/memory/sanitize_memory.py`

> handlers/memory/sanitize_memory.py


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L45 |
| `_TZ_TW` | `—` | `timezone(timedelta(hours=8))` | L47 |
| `_BANTER_PATTERNS` | `list[re.Pattern[str]]` | `[re.compile('吃大便\|吃屎\|去死\|去吃屎\|神經病\|幹嘛啦\|智障')]` | L54 |
| `_REPLAY_SYSTEM_PROMPT` | `—` | `'【歷史記憶重建引擎 (Memory Rebuild AI)】\n這是一份來自群友「{display_name}」的過去完整聊天記錄，\n內容已通過清洗，請從中...` | L87 |
| `_REPLAY_MAX_MSGS_PER_USER` | `—` | `getattr(settings, 'NIGHTLY_MAX_MSGS_PER_USER', 80)` | L119 |
| `_REPLAY_MIN_MSGS_THRESHOLD` | `—` | `getattr(settings, 'NIGHTLY_MIN_MSGS_THRESHOLD', 5)` | L121 |


#### Functions

##### `sanitize_existing`  (L59)

```python
def sanitize_existing(path: Path = settings.USER_MEMORY_PATH) -> dict[str, int]
```

> 掃描並清洗現有 user_memory.json 與 SQLite 內的污染資料與重複事實。  \n>   \n>     清洗項目：  \n>     - Aliases：過濾超長/超短、含 @、含 [某群友]、命中黑名單或遊戲代號格式的別名。  \n>     - Facts：移除文字為空、超長句子（> 70 字）、含 [某群友] 或 @ 的污染 fact、  \n>              移除明顯的群聊互嘴幹話（如吃大便），並進行自體與全域語意模糊去重。  \n>   \n>     Args:  \n>         path: user_memory.json 的路徑。  \n>   \n>     Returns:  \n>         統計字典，含 users_cleaned、aliases_removed、facts_removed。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `path` | `Path` | `settings.USER_MEMORY_PATH` |

##### `replay_past_chat_logs`  (L124)

```python
async def replay_past_chat_logs(days: int = 30, memory_path: Path = settings.USER_MEMORY_PATH) -> dict[str, int]
```

> 從過去 N 天的 chat_logs 重新萃取記憶，產出乾淨的新記憶庫。  \n>   \n>     ⚠️ 此函式需要在已啟動的 Bot 環境中執行（依賴 BotBrain LLM 佇列），  \n>     或單獨透過 Ollama HTTP API 直接呼叫。  \n>     建議透過 Admin 指令 !rebuild_memory 觸發，而不是直接 CLI 呼叫。  \n>   \n>     Args:  \n>         days: 往回追溯幾天的 chat_logs。  \n>         memory_path: 記憶庫路徑（預設 settings.USER_MEMORY_PATH）。  \n>   \n>     Returns:  \n>         統計字典，含 users_processed、facts_added、dates_processed。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `days` | `int` | `30` |
| `memory_path` | `Path` | `settings.USER_MEMORY_PATH` |

##### `_call_llm_direct`  (L219)

```python
async def _call_llm_direct(system_prompt: str, user_prompt: str, uid: str) -> dict | None
```

> 直接透過 HTTP 呼叫 Ollama API（不依賴 BotBrain 佇列）。  \n>   \n>     Args:  \n>         system_prompt: 系統 Prompt。  \n>         user_prompt: 使用者 Prompt（聊天紀錄文字）。  \n>         uid: 使用者 ID（用於日誌）。  \n>   \n>     Returns:  \n>         解析後的 JSON dict，失敗時回傳 None。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `system_prompt` | `str` | `—` |
| `user_prompt` | `str` | `—` |
| `uid` | `str` | `—` |

##### `_setup_logging`  (L273)

```python
def _setup_logging() -> None
```

##### `main`  (L280)

```python
def main() -> None
```

> CLI 入口，支援 sanitize / replay / all 三種模式。


---


### 📄 `handlers/memory/storage.py`

> handlers/memory/storage.py


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L22 |
| `MEMORY_DB_PATH` | `—` | `settings.DATA_DIR / 'sherry_memory.db'` | L23 |
| `UserMemoryStore` | `—` | `dict[str, UserProfile]` | L49 |
| `RelationshipMemoryStore` | `—` | `dict[str, list[RelationshipFact]]` | L50 |
| `MAX_FACTS_IN_CONTEXT` | `int` | `getattr(settings, 'MEMORY_MAX_FACTS_IN_CONTEXT', 8)` | L56 |
| `MAX_FACTS_PER_USER` | `int` | `getattr(settings, 'MEMORY_MAX_FACTS_PER_USER', 25)` | L57 |
| `PERMANENT_FACT_THRESHOLD` | `int` | `getattr(settings, 'MEMORY_PERMANENT_FACT_THRESHOLD', 4)` | L58 |
| `_ALIAS_BLACKLIST` | `set[str]` | `{'靠邀', '未知', '無住址資訊', '哈哈', '喔喔', '幹', '靠北', '哎呀', '笑死', '問號', '群友', '群友們', '某群友...` | L60 |
| `_INVALID_ALIAS_PREFIXES` | `tuple[str, ...]` | `('@', '!', '/', '-', '.', '~', '$')` | L68 |
| `_CATEGORY_MIGRATION` | `dict[str, str]` | `{'interest': 'preference', 'relationship': 'social', 'opinion': 'rule'}` | L70 |
| `_VALID_CATEGORIES` | `set[str]` | `{'identity', 'preference', 'habit', 'social', 'rule', 'interest', 'relationship'...` | L76 |
| `_BANTER_PATTERNS` | `list[re.Pattern[str]]` | `[re.compile('吃大便\|吃屎\|去死\|去吃屎\|神經病\|幹嘛啦\|智障')]` | L82 |
| `_META_PREFIX_PATTERN` | `re.Pattern[str]` | `re.compile('^(他說\|他表示\|他提到\|他認為\|他覺得\|他常說\|他有提到\|他稱\|發話者說\|發話者表示)[：:，,]?\\s*')` | L86 |
| `_META_BRACKET_PATTERN` | `re.Pattern[str]` | `re.compile('[\\(（\\[【].*?[\\)）\\]】]')` | L89 |
| `_META_TAIL_PATTERNS` | `list[re.Pattern[str]]` | `[re.compile('[，,]?\\s*這是.*?(特質\|習慣\|特徵\|核心).*?$'), re.compile('[，,]?\\s*(重複\|強烈).*?$...` | L90 |
| `_TOPIC_FILLER_PATTERNS` | `list[re.Pattern[str]]` | `[re.compile('^(致力於\|正在進行\|正在\|進行\|習慣\|偏好\|熱愛\|擅長\|具備\|具有\|擁有\|會\|常\|經常\|對於\|關於\|主要\|針對\|在於)'), re....` | L194 |
| `_INITIALIZED_MEMORY_DBS` | `set[str]` | `set()` | L460 |


#### Class `UserFact` ← `TypedDict`  (L29)


#### Class `UserProfile` ← `TypedDict`  (L36)


#### Class `RelationshipFact` ← `TypedDict`  (L41)


#### Functions

##### `is_valid_alias`  (L100)

```python
def is_valid_alias(alias: str) -> bool
```

> 檢查別名是否合法（非空、非雪莉自身、長度適中、非指令且無特殊標記）。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `alias` | `str` | `—` |

##### `sanitize_aliases`  (L115)

```python
def sanitize_aliases(raw_aliases: list[str]) -> list[str]
```

> 統一別名消毒與去重（保留有效前 5 個）。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `raw_aliases` | `list[str]` | `—` |

##### `is_valid_fact_text`  (L129)

```python
def is_valid_fact_text(text: str) -> bool
```

> 檢查事實文字是否乾淨合規（非空、長度 <= 70、無 @mention、無嘴砲）。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `text` | `str` | `—` |

##### `normalize_category`  (L138)

```python
def normalize_category(category: str) -> str
```

> 標準化事實分類名稱。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `category` | `str` | `—` |

##### `make_fact`  (L145)

```python
def make_fact(fact_text: str, importance: int = 3, category: str = 'identity', updated_at: str | None = None) -> UserFact
```

> 建立標準化的 UserFact 物件。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `fact_text` | `str` | `—` |
| `importance` | `int` | `3` |
| `category` | `str` | `'identity'` |
| `updated_at` | `str | None` | `None` |

##### `parse_raw_fact`  (L160)

```python
def parse_raw_fact(raw: Any) -> UserFact | None
```

> 將任意格式（str / dict / UserFact）多型解析為標準 UserFact，無效則回傳 None。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `raw` | `Any` | `—` |

##### `_clean_fact_core`  (L184)

```python
@lru_cache(maxsize=4096)
def _clean_fact_core(text: str) -> str
```

> 萃取事實核心陳述（去除引號、元評論前綴、標點與後綴）。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `text` | `str` | `—` |

##### `_extract_semantic_core`  (L201)

```python
@lru_cache(maxsize=4096)
def _extract_semantic_core(text: str) -> str
```

> 進一步過濾虛詞與情態前綴，提取純淨的主題核心骨幹。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `text` | `str` | `—` |

##### `calculate_fact_similarity`  (L210)

```python
@lru_cache(maxsize=4096)
def calculate_fact_similarity(s1: str, s2: str) -> float
```

> 計算兩條事實文字的語意重疊度與字元相似度 (0.0 ~ 1.0)。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `s1` | `str` | `—` |
| `s2` | `str` | `—` |

##### `_find_best_match`  (L277)

```python
def _find_best_match(fact_text: str, candidates: list[UserFact]) -> tuple[float, UserFact | None]
```

> 尋找事實候選清單中相似度最高者。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `fact_text` | `str` | `—` |
| `candidates` | `list[UserFact]` | `—` |

##### `_merge_fact_attributes`  (L287)

```python
def _merge_fact_attributes(target: UserFact, incoming: UserFact) -> None
```

> 就地將 incoming 事實的重要屬性合併進 target 事實。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `target` | `UserFact` | `—` |
| `incoming` | `UserFact` | `—` |

##### `deduplicate_facts`  (L306)

```python
def deduplicate_facts(facts: list[Any], similarity_threshold: float = 0.65) -> list[UserFact]
```

> 對事實清單進行自體消毒與語意模糊去重（保留重要度最高者）。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `facts` | `list[Any]` | `—` |
| `similarity_threshold` | `float` | `0.65` |

##### `sanitize_and_merge_facts`  (L325)

```python
def sanitize_and_merge_facts(existing_facts: list[UserFact], new_raw_facts: list[Any], max_facts: int = MAX_FACTS_PER_USER, similarity_threshold: float = 0.65) -> tuple[list[UserFact], int]
```

> 統一 facts 合併邏輯：自體去重、語意模糊合併、更新重要度、排序與截斷。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `existing_facts` | `list[UserFact]` | `—` |
| `new_raw_facts` | `list[Any]` | `—` |
| `max_facts` | `int` | `MAX_FACTS_PER_USER` |
| `similarity_threshold` | `float` | `0.65` |

##### `consolidate_user_memory`  (L352)

```python
def consolidate_user_memory(profile: UserProfile, max_facts: int = MAX_FACTS_PER_USER, similarity_threshold: float = 0.65) -> tuple[UserProfile, int, int]
```

> 對單一使用者的長期記憶進行全面消毒與模糊去重整理。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `profile` | `UserProfile` | `—` |
| `max_facts` | `int` | `MAX_FACTS_PER_USER` |
| `similarity_threshold` | `float` | `0.65` |

##### `consolidate_all_user_memories`  (L370)

```python
def consolidate_all_user_memories(store: UserMemoryStore, max_facts: int = MAX_FACTS_PER_USER, similarity_threshold: float = 0.65) -> dict[str, int]
```

> 對全庫所有使用者的記憶特徵執行全域大掃除與去重（用於深夜整合與手動清洗）。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `store` | `UserMemoryStore` | `—` |
| `max_facts` | `int` | `MAX_FACTS_PER_USER` |
| `similarity_threshold` | `float` | `0.65` |

##### `_calculate_query_relevance`  (L392)

```python
def _calculate_query_relevance(query: str, text: str) -> float
```

> 計算使用者輸入與事實內容之輕量關鍵字相關度分數 (0.0 ~ 1.0)。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `query` | `str` | `—` |
| `text` | `str` | `—` |

##### `get_diverse_facts_for_context`  (L404)

```python
def get_diverse_facts_for_context(facts: list[UserFact], max_count: int = MAX_FACTS_IN_CONTEXT, query_text: str = '') -> list[UserFact]
```

> 從使用者的 facts 中挑選兼具重要性、相關性與類別多樣性的事實注入 Prompt。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `facts` | `list[UserFact]` | `—` |
| `max_count` | `int` | `MAX_FACTS_IN_CONTEXT` |
| `query_text` | `str` | `''` |

##### `_init_memory_db`  (L463)

```python
def _init_memory_db() -> None
```

> 初始化 sherry_memory.db 資料表與索引。

##### `load_user_memory`  (L504)

```python
def load_user_memory(path: Path) -> UserMemoryStore
```

> 載入既有記憶特徵，優先讀取 SQLite (WHERE is_deleted = 0)，並支援 JSON 備援。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `path` | `Path` | `—` |

##### `save_single_user_memory`  (L572)

```python
def save_single_user_memory(user_id: str, profile: UserProfile, full_store: UserMemoryStore | None = None, path: Path | None = None) -> None
```

> 增量局部更新：僅針對單一使用者的 Profile 與 Facts 進行交易更新，消除全庫寫放大。  \n>   \n>     Args:  \n>         user_id: 使用者 Discord ID。  \n>         profile: 該使用者的 UserProfile 實例。  \n>         full_store: 可選的全量 store 記憶體副本（若提供則非同步更新 JSON 備份）。  \n>         path: JSON 備援檔案路徑。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `user_id` | `str` | `—` |
| `profile` | `UserProfile` | `—` |
| `full_store` | `UserMemoryStore | None` | `None` |
| `path` | `Path | None` | `None` |

##### `delete_user_memory`  (L633)

```python
def delete_user_memory(user_id: str | int) -> None
```

> 軟刪除並清理指定使用者的所有長期記憶與別名特徵 (GDPR / 隱私規範)。  \n>   \n>     Args:  \n>         user_id: 使用者 Discord ID。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `user_id` | `str | int` | `—` |

##### `save_user_memory`  (L667)

```python
def save_user_memory(store: UserMemoryStore, path: Path | None = None) -> None
```

> 將記憶儲存落盤至 SQLite (WHERE is_deleted = 0)。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `store` | `UserMemoryStore` | `—` |
| `path` | `Path | None` | `None` |

##### `decay_memories`  (L717)

```python
def decay_memories(store: UserMemoryStore, reference_time: datetime | None = None) -> int
```

> 對所有 importance <= 2 的 facts 降級或移除。回傳移除的數量。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `store` | `UserMemoryStore` | `—` |
| `reference_time` | `datetime | None` | `None` |


---


### 📄 `handlers/memory/world_memory.py`

> handlers/memory/world_memory.py


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L33 |
| `_TZ_TW` | `—` | `timezone(timedelta(hours=8))` | L35 |
| `_WORLD_LOG_DIR` | `Path` | `settings.WORLD_LOG_DIR` | L36 |
| `_WORLD_MEMORY_PATH` | `Path` | `settings.WORLD_MEMORY_PATH` | L37 |
| `_WORLD_DB_PATH` | `Path` | `settings.DATA_DIR / 'world_memory.db'` | L38 |
| `_DB_INITIALIZED` | `bool` | `False` | L40 |
| `_WORLD_MEMORY_CACHE_TTL` | `float` | `60.0` | L318 |
| `_WORLD_MEMORY_SNIPPET_CACHE` | `dict[tuple[str, int, int, int], tuple[float, str]]` | `{}` | L319 |


#### Functions

##### `_today_str`  (L43)

```python
def _today_str() -> str
```

##### `_init_db`  (L47)

```python
def _init_db() -> None
```

> 初始化 world_memory.db 資料表（僅首次執行）。

##### `_seed_legacy_json`  (L105)

```python
def _seed_legacy_json() -> None
```

> 一次性：若 DB 空白且有舊檔 (world_history/*.jsonl)，唯讀種入 DB。

##### `_insert_event`  (L151)

```python
def _insert_event(event_id: str, ts: str, date_str: str, event_type: str, location: str, summary: str, participants: list[str], importance: int, server_id: str = '') -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `event_id` | `str` | `—` |
| `ts` | `str` | `—` |
| `date_str` | `str` | `—` |
| `event_type` | `str` | `—` |
| `location` | `str` | `—` |
| `summary` | `str` | `—` |
| `participants` | `list[str]` | `—` |
| `importance` | `int` | `—` |
| `server_id` | `str` | `''` |

##### `log_world_event`  (L187)

```python
def log_world_event(event_type: str, summary: str, location: str = '', participants: list[str] | None = None, importance: int = 3, server_id: str = '', date_str: str = '') -> str | None
```

> 記錄一筆世界事件到 SQLite。  \n>   \n>     Args:  \n>         event_type: 事件類型（如 DND, CHAT, DISCOVERY 等）。  \n>         summary: 事件內容摘要。  \n>         location: 發生地點（選填）。  \n>         participants: 參與成員名稱列表（選填）。  \n>         importance: 重要度評級 1(瑣碎)~5(改變世界格局)。  \n>         server_id: 伺服器唯一識別碼（TEXT snowflake），用於跨伺服器記憶隔離。  \n>         date_str: 事件所屬日期 YYYY-MM-DD，若為空則使用今日。  \n>   \n>     Returns:  \n>         str | None: 成功則回傳 event_id，失敗則回傳 None。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `event_type` | `str` | `—` |
| `summary` | `str` | `—` |
| `location` | `str` | `''` |
| `participants` | `list[str] | None` | `None` |
| `importance` | `int` | `3` |
| `server_id` | `str` | `''` |
| `date_str` | `str` | `''` |

##### `_row_to_event`  (L234)

```python
def _row_to_event(row: sqlite3.Row) -> dict[str, Any]
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `row` | `sqlite3.Row` | `—` |

##### `read_world_events`  (L251)

```python
def read_world_events(date_str: str, server_id: str = '') -> list[dict[str, Any]]
```

> 讀取指定日期的所有世界事件，可依 server_id 過濾。  \n>   \n>     Args:  \n>         date_str: 日期字串（格式 YYYY-MM-DD）。  \n>         server_id: 伺服器唯一識別碼（空字串代表不限伺服器）。  \n>   \n>     Returns:  \n>         list[dict[str, Any]]: 世界事件字典列表。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `date_str` | `str` | `—` |
| `server_id` | `str` | `''` |

##### `read_recent_events`  (L280)

```python
def read_recent_events(days: int = 1, server_id: str = '') -> list[dict[str, Any]]
```

> 讀取最近 N 天的所有世界事件，可依 server_id 過濾。  \n>   \n>     Args:  \n>         days: 往前回溯天數（預設 1 天）。  \n>         server_id: 伺服器唯一識別碼（空字串代表不限伺服器）。  \n>   \n>     Returns:  \n>         list[dict[str, Any]]: 最近 N 天的世界事件列表。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `days` | `int` | `1` |
| `server_id` | `str` | `''` |

##### `clear_world_memory_cache`  (L322)

```python
def clear_world_memory_cache() -> None
```

> 清除世界記憶的記憶體快取。

##### `get_world_memory_snippet`  (L327)

```python
def get_world_memory_snippet(days: int = 1, min_importance: int = 3, server_id: str = '', limit: int | None = None) -> str
```

> 取得可注入 LLM prompt 的世界近期事件摘要文字（具備 60 秒 TTL 記憶體快取）。  \n>   \n>     優先讀取壓縮摘要（world_summary），若無則從原始事件即時產生。  \n>   \n>     Args:  \n>         days: 往前回溯天數。  \n>         min_importance: 最低重要度門檻（1~5）。  \n>         server_id: 當前伺服器識別碼（缺乏 server_id 時回傳空字串，落實隔離）。  \n>         limit: 注入的摘要條數上限（預設取 settings.WORLD_MEMORY_SNIPPET_LIMIT）。  \n>   \n>     Returns:  \n>         str: 格式化後的世界事件摘要文字。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `days` | `int` | `1` |
| `min_importance` | `int` | `3` |
| `server_id` | `str` | `''` |
| `limit` | `int | None` | `None` |

##### `_load_compressed_memory`  (L380)

```python
def _load_compressed_memory(days: int, min_importance: int, server_id: str = '') -> list[dict[str, Any]]
```

> 讀取壓縮後的世界記憶（world_summary 表格），可依 server_id 過濾。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `days` | `int` | `—` |
| `min_importance` | `int` | `—` |
| `server_id` | `str` | `''` |

##### `compress_daily_events`  (L424)

```python
def compress_daily_events(date_str: str, server_id: str = '') -> int
```

> 將指定日期的原始事件壓縮為摘要並寫入 world_summary。  \n>   \n>     Args:  \n>         date_str: 日期字串（格式 YYYY-MM-DD）。  \n>         server_id: 伺服器唯一識別碼（非空時僅壓縮該伺服器的事件，並標記為該伺服器）。  \n>   \n>     Returns:  \n>         int: 壓縮後新增寫入的事件數量。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `date_str` | `str` | `—` |
| `server_id` | `str` | `''` |

##### `cleanup_old_logs`  (L488)

```python
def cleanup_old_logs(days: int = 30) -> int
```

> 清理超過指定天數的原始世界事件。  \n>   \n>     Args:  \n>         days: 欲保留的天數（預設 30 天，超過此天數的原始記錄將被刪除）。  \n>   \n>     Returns:  \n>         int: 實際刪除的記錄筆數。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `days` | `int` | `30` |


---


<a id="vibe_manager"></a>

## 🌊 `handlers/vibe_manager/`


### 📄 `handlers/vibe_manager/__init__.py`


#### Functions

##### `setup`  (L4)

```python
async def setup(bot: commands.Bot) -> None
```

> 代理載入氛圍控制元件的包裹橋段

| 參數 | 型別 | 預設值 |
|---|---|---|
| `bot` | `commands.Bot` | `—` |


---


### 📄 `handlers/vibe_manager/cog.py`


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L12 |


#### Class `VibeManager` ← `commands.Cog`  (L14)

> 跨區處理背景氛圍提取，自動隨著衰弱閥門遺忘非話題。

##### Methods

###### `__init__`  (L17)

```python
def __init__(self, bot: commands.Bot) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `bot` | `commands.Bot` | `—` |

###### `cog_load`  (L23)

```python
async def cog_load(self) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `cog_unload`  (L27)

```python
async def cog_unload(self) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `get_top_topics_snippet`  (L35)

```python
def get_top_topics_snippet(self, channel_id: int) -> str
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `channel_id` | `int` | `—` |

###### `request_vibe_update`  (L55)

```python
async def request_vibe_update(self, channel: discord.TextChannel) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `channel` | `discord.TextChannel` | `—` |

###### `_decay_loop`  (L117)

```python
@tasks.loop(seconds=3600)
async def _decay_loop(self) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `_before_decay_loop`  (L143)

```python
@_decay_loop.before_loop
async def _before_decay_loop(self) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |


---


### 📄 `handlers/vibe_manager/storage.py`

> handlers/vibe_manager/storage.py


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L18 |
| `MEMORY_DB_PATH` | `Path` | `settings.DATA_DIR / 'sherry_memory.db'` | L19 |
| `VibeStore` | `—` | `dict[str, dict[str, TopicEntry]]` | L27 |


#### Class `TopicEntry` ← `TypedDict`  (L22)


#### Functions

##### `migrate_entry`  (L30)

```python
def migrate_entry(value: Any) -> TopicEntry
```

> 若是舊系統遺留純粹記錄 int 值的舊設定，轉到具備內容描述與熱度整數的新設定版

| 參數 | 型別 | 預設值 |
|---|---|---|
| `value` | `Any` | `—` |

##### `_init_vibe_db`  (L42)

```python
def _init_vibe_db() -> None
```

> 初始化 channel_vibes 資料表。

##### `load_vibe_store`  (L57)

```python
def load_vibe_store(path: Path) -> VibeStore
```

> 優先自 SQLite (sherry_memory.db) 載入主題歷史檔，若無資料則讀取 JSON 備援。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `path` | `Path` | `—` |

##### `save_vibe_store`  (L108)

```python
def save_vibe_store(store: VibeStore, path: Path) -> None
```

> 將主題記憶儲存落盤至 SQLite (sherry_memory.db)。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `store` | `VibeStore` | `—` |
| `path` | `Path` | `—` |


---


<a id="music"></a>

## 🎵 `handlers/music/`


### 📄 `handlers/music/__init__.py`


#### Functions

##### `setup`  (L4)

```python
async def setup(bot: commands.Bot)
```

> 加載已拆分的重構音樂模塊

| 參數 | 型別 | 預設值 |
|---|---|---|
| `bot` | `commands.Bot` | `—` |


---


### 📄 `handlers/music/cog.py`


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L18 |


#### Class `MusicHandler` ← `commands.Cog`  (L21)

> 音樂處理器，負責語音頻道連接、佇列管理、播放與時間軸控制。

##### Methods

###### `__init__`  (L24)

```python
def __init__(self, bot: commands.Bot) -> None
```

> 初始化音樂處理器模組。  \n>   \n>         Args:  \n>             bot: Discord Bot 實例。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `bot` | `commands.Bot` | `—` |

###### `_cleanup_guild_source`  (L48)

```python
def _cleanup_guild_source(self, guild_id: int) -> None
```

> 安全釋放並強制終止指定伺服器現有的音訊來源與 FFmpeg 子行程。  \n>   \n>         Args:  \n>             guild_id: Discord 伺服器 ID。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `guild_id` | `int` | `—` |

###### `_recover_voice_and_play_next`  (L87)

```python
async def _recover_voice_and_play_next(self, guild: discord.Guild, text_channel: discord.TextChannel) -> None
```

> 當底層 Native 音訊/語音通道崩潰時，執行自動重置與重連自癒程序。  \n>   \n>         Args:  \n>             guild: Discord 伺服器物件。  \n>             text_channel: 文字回覆頻道。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `guild` | `discord.Guild` | `—` |
| `text_channel` | `discord.TextChannel` | `—` |

###### `_delete_song_message`  (L148)

```python
async def _delete_song_message(self, guild_id: int) -> None
```

> 刪除指定伺服器當前正在播放歌曲的留聲訊息。  \n>   \n>         Args:  \n>             guild_id: Discord 伺服器 ID。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `guild_id` | `int` | `—` |

###### `format_duration`  (L161)

```python
def format_duration(self, seconds: Any) -> str
```

> 將秒數格式化為 mm:ss 或 hh:mm:ss 字串。  \n>   \n>         Args:  \n>             seconds: 總秒數（可為 int、float 或數字字串）。  \n>   \n>         Returns:  \n>             str: 格式化後的時間字串。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `seconds` | `Any` | `—` |

###### `format_time_axis`  (L183)

```python
def format_time_axis(self, elapsed_seconds: Any, total_seconds: Any, bar_length: int = 15) -> str
```

> 產生聲音進度時間軸字串（含進度條圖示與時間）。  \n>   \n>         Args:  \n>             elapsed_seconds: 已播放秒數。  \n>             total_seconds: 歌曲總秒數。  \n>             bar_length: 進度條長度。  \n>   \n>         Returns:  \n>             str: 格式化後的時間軸。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `elapsed_seconds` | `Any` | `—` |
| `total_seconds` | `Any` | `—` |
| `bar_length` | `int` | `15` |

###### `get_elapsed_time`  (L210)

```python
def get_elapsed_time(self, guild_id: int) -> int
```

> 取得當前歌曲的已播放時間（秒）。  \n>   \n>         Args:  \n>             guild_id: Discord 伺服器 ID。  \n>   \n>         Returns:  \n>             int: 已播放秒數。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `guild_id` | `int` | `—` |

###### `pause_playback`  (L233)

```python
def pause_playback(self, guild_id: int) -> None
```

> 記錄暫停播放狀態與已播放累積時間。  \n>   \n>         Args:  \n>             guild_id: Discord 伺服器 ID。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `guild_id` | `int` | `—` |

###### `resume_playback`  (L246)

```python
def resume_playback(self, guild_id: int) -> None
```

> 記錄繼續播放狀態並重置段落計時。  \n>   \n>         Args:  \n>             guild_id: Discord 伺服器 ID。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `guild_id` | `int` | `—` |

###### `shorten_url`  (L256)

```python
def shorten_url(self, url: str) -> str
```

> 縮短 YouTube 等播放網址以避免 Embed 字元超過上限。  \n>   \n>         Args:  \n>             url: 原始影片網址。  \n>   \n>         Returns:  \n>             str: 縮短後的網址。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `url` | `str` | `—` |

###### `build_playing_embed`  (L274)

```python
def build_playing_embed(self, guild_id: int) -> discord.Embed
```

> 構建正在播放歌曲的 Embed 介面與時間軸。  \n>   \n>         Args:  \n>             guild_id: Discord 伺服器 ID。  \n>   \n>         Returns:  \n>             discord.Embed: 正在播放的音樂 Embed。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `guild_id` | `int` | `—` |

###### `build_queue_embed`  (L306)

```python
def build_queue_embed(self, guild_id: int, page: int = 0, per_page: int = 10) -> discord.Embed
```

> 構建音樂播放清單的分頁 Embed，並附帶當前播放歌曲與時間軸。  \n>   \n>         Args:  \n>             guild_id: Discord 伺服器 ID。  \n>             page: 欲顯示的頁碼 (0-indexed)。  \n>             per_page: 每頁顯示幾筆歌曲。  \n>   \n>         Returns:  \n>             discord.Embed: 音樂清單分頁 Embed。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `guild_id` | `int` | `—` |
| `page` | `int` | `0` |
| `per_page` | `int` | `10` |

###### `send_queue_embed`  (L361)

```python
async def send_queue_embed(self, channel: discord.abc.Messageable, guild_id: int) -> None
```

> 發送包含音樂清單與分頁按鈕的 Embed 訊息。  \n>   \n>         Args:  \n>             channel: 發送訊息的 Discord 頻道。  \n>             guild_id: Discord 伺服器 ID。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `channel` | `discord.abc.Messageable` | `—` |
| `guild_id` | `int` | `—` |

###### `_start_progress_updater`  (L379)

```python
def _start_progress_updater(self, guild_id: int) -> None
```

> 啟動定時更新音樂時間軸的後台工作。  \n>   \n>         Args:  \n>             guild_id: Discord 伺服器 ID。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `guild_id` | `int` | `—` |

###### `_stop_progress_updater`  (L389)

```python
def _stop_progress_updater(self, guild_id: int) -> None
```

> 停止並取消指定伺服器的時間軸更新 Task。  \n>   \n>         Args:  \n>             guild_id: Discord 伺服器 ID。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `guild_id` | `int` | `—` |

###### `_progress_updater_loop`  (L399)

```python
async def _progress_updater_loop(self, guild_id: int) -> None
```

> 定時更新當前播放歌曲的 Embed 時間軸。  \n>   \n>         Args:  \n>             guild_id: Discord 伺服器 ID。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `guild_id` | `int` | `—` |

###### `jump_to`  (L432)

```python
def jump_to(self, guild_id: int, index: int) -> dict[str, Any] | None
```

> 把第 index 首（1-based）移到佇列最前面，並回傳該歌曲資訊。  \n>   \n>         由呼叫方負責 vc.stop() 觸發 after_playback → _play_next。  \n>   \n>         Args:  \n>             guild_id: Discord 伺服器 ID。  \n>             index: 歌曲位置（從 1 開始）。  \n>   \n>         Returns:  \n>             dict[str, Any] | None: 指定之歌曲字典，若無則為 None。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `guild_id` | `int` | `—` |
| `index` | `int` | `—` |

###### `clear_queue`  (L452)

```python
def clear_queue(self, guild_id: int) -> int
```

> 清空伺服器的播放清單，並回傳清除的歌曲數量。  \n>   \n>         Args:  \n>             guild_id: Discord 伺服器 ID。  \n>   \n>         Returns:  \n>             int: 已被清除的歌曲數。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `guild_id` | `int` | `—` |

###### `skip_song`  (L466)

```python
def skip_song(self, guild_id: int) -> bool
```

> 跳過指定伺服器當前正在播放的歌曲。  \n>   \n>         Args:  \n>             guild_id: Discord 伺服器 ID。  \n>   \n>         Returns:  \n>             bool: 是否成功觸發跳過。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `guild_id` | `int` | `—` |

###### `stop_player`  (L485)

```python
async def stop_player(self, guild_id: int, disconnect: bool = True) -> None
```

> 停止指定伺服器的音樂播放、清空佇列、重置循環模式並釋放播放資源。  \n>   \n>         Args:  \n>             guild_id: Discord 伺服器 ID。  \n>             disconnect: 是否中斷語音頻道連線（預設 True）。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `guild_id` | `int` | `—` |
| `disconnect` | `bool` | `True` |

###### `headless_play`  (L526)

```python
async def headless_play(self, text_channel: discord.TextChannel, voice_channel: discord.VoiceChannel, query: str, insert_at_top: bool = False) -> None
```

> 無頭模式載入音樂至佇列，並於需要時觸發播放。  \n>   \n>         Args:  \n>             text_channel: 文字回覆頻道。  \n>             voice_channel: 機器人應加入的語音頻道。  \n>             query: YouTube 網址或關鍵字。  \n>             insert_at_top: 是否優先插播在佇列首位。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `text_channel` | `discord.TextChannel` | `—` |
| `voice_channel` | `discord.VoiceChannel` | `—` |
| `query` | `str` | `—` |
| `insert_at_top` | `bool` | `False` |

###### `_play_next`  (L639)

```python
def _play_next(self, guild: discord.Guild, text_channel: discord.TextChannel) -> None
```

> 背景播放下一首歌曲引擎。  \n>   \n>         Args:  \n>             guild: Discord 伺服器物件。  \n>             text_channel: 文字頻道。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `guild` | `discord.Guild` | `—` |
| `text_channel` | `discord.TextChannel` | `—` |

###### `play_command`  (L845)

```python
@app_commands.command(name='play', description='🎵 音樂播放系統 — 無參數開啟留聲機主控面板，或直接選擇操作')
@app_commands.describe(play_type='操作類型（可選，留空則開啟留聲機互動主控台）', context='補充內容（YouTube 網址 / 歌名 / 編號），部分操作不需填寫')
@app_commands.choices(play_type=[app_commands.Choice(name='▶️  add    — 新增 YouTube 網址到佇列', value='add'), app_commands.Choice(name='🔍  search — 搜尋歌名並播放', value='search'), app_commands.Choice(name='📌  insert — 優先插播（搜尋或網址排到最前）', value='insert'), app_commands.Choice(name='📋  list   — 查看目前播放清單', value='list'), app_commands.Choice(name='⏭️  skip   — 跳過當前歌曲', value='skip'), app_commands.Choice(name='🎯  jump   — 優先播放指定編號的歌', value='jump'), app_commands.Choice(name='🗑️  remove — 刪除指定編號的歌', value='remove'), app_commands.Choice(name='🧹  clear  — 清空整個播放清單', value='clear'), app_commands.Choice(name='⏹️  stop   — 停止播放並離開語音頻道', value='stop')])
async def play_command(self, interaction: discord.Interaction, play_type: Optional[str] = None, context: Optional[str] = '') -> None
```

> 處理 /play 斜線指令的分支操作與主控台發送。  \n>   \n>         Args:  \n>             interaction: Discord 互動事件。  \n>             play_type: 操作類型 (add, search, list, skip, jump, remove 等，留空開啟面板)。  \n>             context: 相關參數 (網址、關鍵字或歌曲編號)。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |
| `play_type` | `Optional[str]` | `None` |
| `context` | `Optional[str]` | `''` |

###### `on_voice_state_update`  (L1037)

```python
@commands.Cog.listener()
async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None
```

> 處理當機器人被踢出或離開語音頻道時的資源清理。  \n>   \n>         Args:  \n>             member: 變更語音狀態的成員。  \n>             before: 變更前的語音狀態。  \n>             after: 變更後的語音狀態。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `member` | `discord.Member` | `—` |
| `before` | `discord.VoiceState` | `—` |
| `after` | `discord.VoiceState` | `—` |


---


### 📄 `handlers/music/config.py`


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L6 |
| `_system_ffmpeg` | `—` | `shutil.which('ffmpeg')` | L11 |
| `FFMPEG_OPTIONS` | `—` | `{'before_options': getattr(settings, 'MUSIC_FFMPEG_BEFORE_OPTIONS', '-reconnect ...` | L30 |
| `_COOKIE_PATH` | `—` | `getattr(settings, 'YOUTUBE_COOKIES_PATH', os.path.join(settings.DATA_DIR, 'cooki...` | L37 |
| `YTDL_OPTIONS` | `—` | `{'format': 'bestaudio/best', 'noplaylist': False, 'playlistend': getattr(setting...` | L40 |
| `ytdl_client` | `—` | `yt_dlp.YoutubeDL(YTDL_OPTIONS)` | L65 |


---


### 📄 `handlers/music/ui.py`

> handlers/music/ui.py


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L14 |


#### Class `SongInputModal` ← `ui.Modal`  (L21)

> 彈出式點歌與 YouTube 網址輸入表單。

##### Methods

###### `on_submit`  (L40)

```python
async def on_submit(self, interaction: discord.Interaction) -> None
```

> 處理點歌提交事件。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |


#### Class `MusicControlView` ← `discord.ui.View`  (L57)

> 音樂播放時的控制按鈕面板。

    包含點歌、暫停/播放、跳過歌曲、隨機打亂、循環切換、停止清空以及查看播放清單分頁面板。

##### Methods

###### `__init__`  (L63)

```python
def __init__(self) -> None
```

> 初始化音樂播放控制介面。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `add_song_button`  (L68)

```python
@discord.ui.button(label='➕ 點歌/貼網址', style=discord.ButtonStyle.success, custom_id='add_song_btn', row=0)
async def add_song_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None
```

> 彈出點歌對話框。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |
| `button` | `discord.ui.Button` | `—` |

###### `pause_resume_button`  (L73)

```python
@discord.ui.button(label='⏯️ 暫停/播放', style=discord.ButtonStyle.secondary, custom_id='pause_resume_btn', row=0)
async def pause_resume_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None
```

> 切換音樂播放或暫停狀態。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |
| `button` | `discord.ui.Button` | `—` |

###### `skip_button`  (L108)

```python
@discord.ui.button(label='⏭️ 跳過此曲', style=discord.ButtonStyle.blurple, custom_id='skip_song_btn', row=0)
async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None
```

> 跳過當前播放歌曲。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |
| `button` | `discord.ui.Button` | `—` |

###### `stop_button`  (L122)

```python
@discord.ui.button(label='⏹️ 停止/清空', style=discord.ButtonStyle.danger, custom_id='stop_song_btn', row=0)
async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None
```

> 停止播放並清空佇列。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |
| `button` | `discord.ui.Button` | `—` |

###### `shuffle_button`  (L132)

```python
@discord.ui.button(label='🔀 隨機', style=discord.ButtonStyle.secondary, custom_id='shuffle_btn', row=1)
async def shuffle_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None
```

> 隨機打亂目前排隊中的播放清單。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |
| `button` | `discord.ui.Button` | `—` |

###### `loop_button`  (L149)

```python
@discord.ui.button(label='🔁 循環', style=discord.ButtonStyle.secondary, custom_id='loop_btn', row=1)
async def loop_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None
```

> 切換循環模式：關閉、清單循環或單曲循環。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |
| `button` | `discord.ui.Button` | `—` |

###### `view_queue_button`  (L172)

```python
@discord.ui.button(label='📋 播放清單', style=discord.ButtonStyle.secondary, custom_id='view_queue_btn', row=1)
async def view_queue_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None
```

> 顯示目前的音樂播放清單與分頁介面。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |
| `button` | `discord.ui.Button` | `—` |


#### Class `MusicQueueView` ← `discord.ui.View`  (L194)

> 音樂播放清單的分頁與管理介面。

##### Methods

###### `__init__`  (L197)

```python
def __init__(self, guild_id: int, page: int = 0, per_page: int = 10) -> None
```

> 初始化播放清單分頁檢視介面。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `guild_id` | `int` | `—` |
| `page` | `int` | `0` |
| `per_page` | `int` | `10` |

###### `update_button_states`  (L205)

```python
def update_button_states(self, total_pages: int) -> None
```

> 依據頁碼與總頁數更新翻頁按鈕的啟用狀態。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `total_pages` | `int` | `—` |

###### `prev_button`  (L215)

```python
@discord.ui.button(label='⬅️ 上一頁', style=discord.ButtonStyle.secondary, custom_id='queue_prev_btn')
async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None
```

> 切換至上一頁播放清單。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |
| `button` | `discord.ui.Button` | `—` |

###### `next_button`  (L229)

```python
@discord.ui.button(label='➡️ 下一頁', style=discord.ButtonStyle.secondary, custom_id='queue_next_btn')
async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None
```

> 切換至下一頁播放清單。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |
| `button` | `discord.ui.Button` | `—` |

###### `refresh_button`  (L243)

```python
@discord.ui.button(label='🔄 重新整理', style=discord.ButtonStyle.primary, custom_id='queue_refresh_btn')
async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None
```

> 重新整理播放清單與當前歌曲時間軸。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |
| `button` | `discord.ui.Button` | `—` |

###### `on_timeout`  (L256)

```python
async def on_timeout(self) -> None
```

> 當互動逾時過期，將所有按鈕設為停用。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |


---


<a id="voice"></a>

## 🎤 `handlers/voice/`


### 📄 `handlers/voice/__init__.py`


#### Functions

##### `setup`  (L5)

```python
async def setup(bot: commands.Bot) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `bot` | `commands.Bot` | `—` |


---


### 📄 `handlers/voice/cog.py`


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L20 |
| `_WAKE_WORD_PATTERN` | `—` | `re.compile('(雪莉\|雪麗\|雪力)[,，、。\\s]*(.*)', re.IGNORECASE)` | L22 |


#### Class `VoiceProxyMessage` ← `object`  (L25)

> 模擬 Discord Message 以供文字路由器 (route_and_execute_tag) 處理語音指令。

##### Methods

###### `__init__`  (L28)

```python
def __init__(self, author: discord.Member | discord.User, channel: discord.abc.Messageable, content: str, bot: commands.Bot, guild_id: int) -> None
```

> 初始化語音代理訊息。  \n>   \n>         Args:  \n>             author: 發話之 Discord 使用者。  \n>             channel: 目標訊息頻道。  \n>             content: 語音識別後之文字內容。  \n>             bot: Discord Bot 實例。  \n>             guild_id: Discord 伺服器 ID。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `author` | `discord.Member | discord.User` | `—` |
| `channel` | `discord.abc.Messageable` | `—` |
| `content` | `str` | `—` |
| `bot` | `commands.Bot` | `—` |
| `guild_id` | `int` | `—` |

###### `reply`  (L56)

```python
async def reply(self, content: str | None = None, **kwargs: Any) -> discord.Message | None
```

> 回覆訊息並自動發動語音合成 (TTS) 與播放。  \n>   \n>         Args:  \n>             content: 回覆文字內容。  \n>             **kwargs: 其他額外參數。  \n>   \n>         Returns:  \n>             discord.Message | None: 發送出的 Discord 訊息物件。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `content` | `str | None` | `None` |
| `**kwargs` | `Any` | `—` |

###### `add_reaction`  (L84)

```python
async def add_reaction(self, emoji: str) -> None
```

> 忽略反應。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `emoji` | `str` | `—` |


#### Class `VoiceCog` ← `commands.Cog`  (L89)

> 語音互動處理模組，負責加入語音頻道、VAD 語音活動檢測、STT 識別與 TTS 播報。

##### Methods

###### `__init__`  (L92)

```python
def __init__(self, bot: commands.Bot) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `bot` | `commands.Bot` | `—` |

###### `join_voice`  (L101)

```python
@app_commands.command(name='join_voice', description='加入語音頻道開始交談')
async def join_voice(self, interaction: discord.Interaction) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |

###### `leave_voice`  (L137)

```python
@app_commands.command(name='leave_voice', description='離開語音頻道並釋放 VRAM')
async def leave_voice(self, interaction: discord.Interaction) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |

###### `_listen_loop`  (L159)

```python
async def _listen_loop(self, guild_id: int) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `guild_id` | `int` | `—` |

###### `_play_audio`  (L203)

```python
async def _play_audio(self, guild_id: int, audio_bytes: bytes) -> None
```

> 在指定伺服器的語音頻道播放合成音訊。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `guild_id` | `int` | `—` |
| `audio_bytes` | `bytes` | `—` |

###### `on_voice_state_update`  (L228)

```python
@commands.Cog.listener()
async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None
```

> 處理若機器人被踢出語音頻道時釋放神經網路資源。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `member` | `discord.Member` | `—` |
| `before` | `discord.VoiceState` | `—` |
| `after` | `discord.VoiceState` | `—` |


---


### 📄 `handlers/voice/recorder.py`


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L4 |


#### Class `VoiceRecorder` ← `object`  (L6)

##### Methods

###### `__init__`  (L7)

```python
def __init__(self, vad: object = None) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `vad` | `object` | `None` |

###### `get_next_utterance`  (L13)

```python
async def get_next_utterance(self) -> tuple[int, bytes] | None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `write`  (L21)

```python
def write(self, user_id: int, data: bytes) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `user_id` | `int` | `—` |
| `data` | `bytes` | `—` |

###### `clear`  (L25)

```python
def clear(self) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |


---


### 📄 `handlers/voice/stt.py`


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L9 |


#### Class `SpeechToText` ← `object`  (L12)

> 語音轉文字 (STT) 處理器。

##### Methods

###### `__init__`  (L15)

```python
def __init__(self) -> None
```

> 初始化 STT 模型。具備套件缺失保護 (ImportError) 與惰性載入。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `transcribe`  (L52)

```python
async def transcribe(self, audio_bytes: bytes) -> str
```

> 將音訊位元組資料轉譯為文字。  \n>   \n>         Args:  \n>             audio_bytes: 音訊檔案的二進位位元組資料（WAV 格式）。  \n>   \n>         Returns:  \n>             str: 轉譯出的文字字串，若模型未載入或失敗則回傳空字串。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `audio_bytes` | `bytes` | `—` |

###### `unload`  (L93)

```python
def unload(self) -> None
```

> 卸載 STT 模型並釋放 GPU 快取顯存。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |


---


### 📄 `handlers/voice/tts.py`


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L5 |


#### Class `TextToSpeech` ← `object`  (L7)

##### Methods

###### `__init__`  (L8)

```python
def __init__(self) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `synthesize`  (L28)

```python
async def synthesize(self, text: str) -> bytes
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `text` | `str` | `—` |

###### `unload`  (L50)

```python
def unload(self) -> None
```

> 卸載 TTS 語音模型並釋放 GPU 快取顯存。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |


---


### 📄 `handlers/voice/vad.py`


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L3 |


#### Class `VoiceActivityDetector` ← `object`  (L5)

##### Methods

###### `__init__`  (L6)

```python
def __init__(self, threshold: float = 0.5, min_silence_ms: int = 500) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `threshold` | `float` | `0.5` |
| `min_silence_ms` | `int` | `500` |

###### `is_speech`  (L19)

```python
def is_speech(self, audio_chunk: bytes) -> bool
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `audio_chunk` | `bytes` | `—` |

###### `reset`  (L23)

```python
def reset(self) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |


---


<a id="admin"></a>

## 🛠️ `handlers/admin/`


### 📄 `handlers/admin/__init__.py`


#### Functions

##### `setup`  (L5)

```python
async def setup(bot: commands.Bot) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `bot` | `commands.Bot` | `—` |


---


### 📄 `handlers/admin/cog.py`


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L15 |


#### Class `ConfigModal` ← `discord.ui.Modal`  (L17)

##### Methods

###### `__init__`  (L25)

```python
def __init__(self, key: str, current_value: str)
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `key` | `str` | `—` |
| `current_value` | `str` | `—` |

###### `on_submit`  (L30)

```python
async def on_submit(self, interaction: discord.Interaction)
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |


#### Class `ConfigSelect` ← `discord.ui.Select`  (L42)

##### Methods

###### `__init__`  (L43)

```python
def __init__(self)
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `callback`  (L54)

```python
async def callback(self, interaction: discord.Interaction)
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |


#### Class `ConfigView` ← `discord.ui.View`  (L59)

##### Methods

###### `__init__`  (L60)

```python
def __init__(self, author_id: int | None = None)
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `author_id` | `int | None` | `None` |

###### `interaction_check`  (L66)

```python
async def interaction_check(self, interaction: discord.Interaction) -> bool
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |

###### `on_timeout`  (L72)

```python
async def on_timeout(self) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |


#### Class `MemoryControlView` ← `discord.ui.View`  (L82)

##### Methods

###### `__init__`  (L83)

```python
def __init__(self, target_user_id: int)
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `target_user_id` | `int` | `—` |

###### `on_timeout`  (L87)

```python
async def on_timeout(self) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `correct_button`  (L93)

```python
@discord.ui.button(label='✅ 沒錯，很準', style=discord.ButtonStyle.success)
async def correct_button(self, interaction: discord.Interaction, button: discord.ui.Button)
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |
| `button` | `discord.ui.Button` | `—` |

###### `incorrect_button`  (L100)

```python
@discord.ui.button(label='❌ 記錯了，清除記憶', style=discord.ButtonStyle.danger)
async def incorrect_button(self, interaction: discord.Interaction, button: discord.ui.Button)
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |
| `button` | `discord.ui.Button` | `—` |


#### Class `AdminCog` ← `commands.Cog`  (L116)

> 提供伺服器管理、記憶維護、氛圍操作與除錯指令。

##### Methods

###### `__init__`  (L119)

```python
def __init__(self, bot: commands.Bot) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `bot` | `commands.Bot` | `—` |

###### `_is_admin`  (L122)

```python
def _is_admin(self, interaction: discord.Interaction) -> bool
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |

###### `_is_super_admin`  (L125)

```python
def _is_super_admin(self, interaction: discord.Interaction) -> bool
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |

###### `vibe_command`  (L140)

```python
@app_commands.command(name='vibe', description='💬 檢視或清除頻道即時話題氛圍')
@app_commands.describe(action='選擇執行的動作', channel='目標文字頻道 (預設為當前頻道)')
@app_commands.choices(action=[app_commands.Choice(name='👀 檢視氛圍 (show) — 查看當前頻道話題氛圍 (公開)', value='show'), app_commands.Choice(name='🧹 清除氛圍 (clear) — 清除指定頻道氛圍資料 (管理員)', value='clear')])
async def vibe_command(self, interaction: discord.Interaction, action: app_commands.Choice[str], channel: discord.TextChannel | None = None) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |
| `action` | `app_commands.Choice[str]` | `—` |
| `channel` | `discord.TextChannel | None` | `None` |

###### `memory_command`  (L187)

```python
@app_commands.command(name='memory', description='🧠 檢視或清除成員長期記憶 / 查看世界記憶')
@app_commands.describe(action='選擇執行的動作', user='選擇目標成員（show/clear 適用；world 時可略）')
@app_commands.choices(action=[app_commands.Choice(name='👀 檢視記憶 (show) — 檢視成員長期記憶 (僅自己可見)', value='show'), app_commands.Choice(name='🧹 清除記憶 (clear) — 清除成員長期記憶 (管理員)', value='clear'), app_commands.Choice(name='🌍 世界記憶 (world) — 查看當前伺服器世界記憶', value='world')])
async def memory_command(self, interaction: discord.Interaction, action: app_commands.Choice[str], user: discord.Member | None = None) -> None
```

> 處理 /memory 斜線指令（檢視/清除成員長期記憶或查看伺服器世界記憶）。  \n>   \n>         Args:  \n>             interaction: Discord 互動事件物件。  \n>             action: 選擇執行的動作（show/clear/world）。  \n>             user: 目標成員（show/clear 適用，world 時可省略）。  \n>   \n>         Returns:  \n>             None

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |
| `action` | `app_commands.Choice[str]` | `—` |
| `user` | `discord.Member | None` | `None` |

###### `admin_command`  (L301)

```python
@app_commands.command(name='admin', description='⚙️ 系統維護、指令同步與熱重載管理')
@app_commands.describe(action='選擇執行的管理動作', target='目標 Cog 名稱或模組名 (reload 或 restart 時選填，預設為 all)')
@app_commands.choices(action=[app_commands.Choice(name='🔄 同步指令 (sync) — 即時同步所有 Slash 指令至 Discord API', value='sync'), app_commands.Choice(name='⚡ 模組熱重啟 (restart) — 重新加載指定模組或全部模組與 settings', value='restart'), app_commands.Choice(name='📦 重新加載 Cog (reload) — 重新加載特定 Cog 擴展模組', value='reload'), app_commands.Choice(name='🛠️ 設定面板 (config) — 開啟伺服器端動態參數設定面板', value='config')])
async def admin_command(self, interaction: discord.Interaction, action: app_commands.Choice[str], target: str | None = None) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |
| `action` | `app_commands.Choice[str]` | `—` |
| `target` | `str | None` | `None` |

###### `debug_command`  (L401)

```python
@app_commands.command(name='debug', description='🐞 系統除錯模式設定、狀態檢視與完整 Prompt 注入檢視')
@app_commands.describe(action='選擇欲調整或執行的除錯功能 (status: 狀態總覽 | detail: 步驟化思考欄 | live: 即時動態編輯思考 | tag: 模擬匯出 Prompt)', enable='True = 開啟功能；False = 關閉功能 (僅 action 為 detail 或 live 時有效)', context='輸入想模擬 @雪莉 的對話內容 (僅 action 為 tag 時有效)')
@app_commands.choices(action=[app_commands.Choice(name='📊 檢視當前除錯與推演狀態 (status) — 一覽所有除錯開關、模型與推演配置', value='status'), app_commands.Choice(name='💡 步驟化思考推演欄 (detail) — 顯示步驟化思考、執行耗時、模型與工具調用', value='detail'), app_commands.Choice(name='⚡ 即時動態編輯思考 (live) — 原地動態編輯更新思考進度，0 等待', value='live'), app_commands.Choice(name='🏷️ 完整 Prompt 注入檢視 (tag) — 模擬 @雪莉 並產出完整注入的 System/User Prompt', value='tag')])
async def debug_command(self, interaction: discord.Interaction, action: app_commands.Choice[str] | None = None, enable: bool | None = None, context: str | None = None) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |
| `action` | `app_commands.Choice[str] | None` | `None` |
| `enable` | `bool | None` | `None` |
| `context` | `str | None` | `None` |

###### `_handle_debug_status`  (L439)

```python
async def _handle_debug_status(self, interaction: discord.Interaction) -> None
```

> 顯示除錯模式、思考推演與模型調度之即時配置狀態總覽。  \n>   \n>         Args:  \n>             interaction: Discord 互動事件實例。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |

###### `_handle_debug_tag`  (L512)

```python
async def _handle_debug_tag(self, interaction: discord.Interaction, context: str | None) -> None
```

> 模擬 @雪莉 流程，執行並匯出完整 Prompt、中間排程與推演報告。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |
| `context` | `str | None` | `—` |

###### `stats_command`  (L722)

```python
@app_commands.command(name='stats', description='顯示系統詳細統計儀表板 (公開)')
async def stats_command(self, interaction: discord.Interaction) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |


---


<a id="executor"></a>

## 🔫 `handlers/executor/`


### 📄 `handlers/executor/__init__.py`


#### Functions

##### `setup`  (L5)

```python
async def setup(bot: commands.Bot) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `bot` | `commands.Bot` | `—` |


---


### 📄 `handlers/executor/cog.py`

> handlers/executor/cog.py


#### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `logger` | `—` | `logging.getLogger(__name__)` | L20 |
| `ADMIN_ONLY_INTENTS` | `set[str]` | `{'ADMIN', 'RELOAD', 'SYNC', 'SYSTEM_SYNC', 'MEMORY_CLEAR', 'VIBE_CLEAR', 'DEBUG'...` | L22 |
| `_INTENT_CHOICES` | `—` | `[app_commands.Choice(name=f'{t.intent_name} ({t.llm_description[:35]}{('…' if le...` | L34 |


#### Class `_FakeMessage` ← `object`  (L44)

> 模擬 discord.Message 的最小包裝，供 tool.execute() 使用。

##### Methods

###### `guild`  (L54)

```python
@property
def guild(self) -> discord.Guild
```

> 委派至 channel.guild。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `reply`  (L58)

```python
async def reply(self, content: str | None = None, embed: discord.Embed | None = None, mention_author: bool = False) -> Any
```

> 將 reply 轉發至 interaction.followup.send()。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `content` | `str | None` | `None` |
| `embed` | `discord.Embed | None` | `None` |
| `mention_author` | `bool` | `False` |

###### `add_reaction`  (L77)

```python
async def add_reaction(self, emoji: str) -> None
```

> 忽略反應。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `emoji` | `str` | `—` |


#### Class `PromptTestModal` ← `discord.ui.Modal`  (L86)

> 彈出式 Prompt 測試表單。

##### Methods

###### `__init__`  (L106)

```python
def __init__(self, bot: commands.Bot) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `bot` | `commands.Bot` | `—` |

###### `on_submit`  (L110)

```python
async def on_submit(self, interaction: discord.Interaction) -> None
```

> 執行指定的 Prompt。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |


#### Class `CommandHubCategorySelect` ← `discord.ui.Select`  (L163)

> 功能領域分類切換選單。

##### Methods

###### `__init__`  (L166)

```python
def __init__(self, parent_view: CommandHubView) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `parent_view` | `CommandHubView` | `—` |

###### `callback`  (L231)

```python
async def callback(self, interaction: discord.Interaction) -> None
```

> 選取分類後切換主面板顯示。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |


#### Class `CommandHubView` ← `discord.ui.View`  (L239)

> 雪莉全能互動測試中樞 View。

##### Methods

###### `__init__`  (L242)

```python
def __init__(self, bot: commands.Bot, author_id: int | None = None) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `bot` | `commands.Bot` | `—` |
| `author_id` | `int | None` | `None` |

###### `interaction_check`  (L250)

```python
async def interaction_check(self, interaction: discord.Interaction) -> bool
```

> 限制僅指令發起者可點擊操作。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |

###### `on_timeout`  (L257)

```python
async def on_timeout(self) -> None
```

> 超時時自動將元件設為 disabled，防止過期點擊。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |

###### `build_preview_embed`  (L268)

```python
def build_preview_embed(self, category: str) -> discord.Embed
```

> 根據選定分類產生預覽說明 Embed。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `category` | `str` | `—` |

###### `btn_open_module`  (L344)

```python
@discord.ui.button(label='🚀 開啟該模組主控台', style=discord.ButtonStyle.success, row=1)
async def btn_open_module(self, interaction: discord.Interaction, button: discord.ui.Button) -> None
```

> 根據當前分類動態開啟對應模組主控台。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |
| `button` | `discord.ui.Button` | `—` |

###### `btn_prompt_test`  (L415)

```python
@discord.ui.button(label='🧪 測試 Prompt 執行', style=discord.ButtonStyle.primary, row=1)
async def btn_prompt_test(self, interaction: discord.Interaction, button: discord.ui.Button) -> None
```

> 彈出 Prompt 直通測試表單。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |
| `button` | `discord.ui.Button` | `—` |


#### Class `ExecutorCog` ← `commands.Cog`  (L424)

> 提供 /execute 互動測試中樞與強制意圖測試指令。

##### Methods

###### `__init__`  (L427)

```python
def __init__(self, bot: commands.Bot) -> None
```

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `bot` | `commands.Bot` | `—` |

###### `execute_intent`  (L439)

```python
@app_commands.command(name='execute', description='🎛️ 開啟雪莉全能互動測試中樞 (Command Hub Preview) 或強制執行 Prompt')
@app_commands.describe(intent='可選：要直接強制執行的意圖類型 (若留空則開啟互動測試中樞)', content='可選：要傳入執行的 Prompt 內容')
@app_commands.choices(intent=_INTENT_CHOICES)
async def execute_intent(self, interaction: discord.Interaction, intent: Optional[str] = None, content: Optional[str] = None) -> None
```

> 若無傳入 intent 則開啟互動測試中樞；若有傳入則直接執行 Tool。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `self` | `—` | `—` |
| `interaction` | `discord.Interaction` | `—` |
| `intent` | `Optional[str]` | `None` |
| `content` | `Optional[str]` | `None` |


#### Functions

##### `setup`  (L502)

```python
async def setup(bot: commands.Bot) -> None
```

> 掛載 ExecutorCog。

| 參數 | 型別 | 預設值 |
|---|---|---|
| `bot` | `commands.Bot` | `—` |


---
