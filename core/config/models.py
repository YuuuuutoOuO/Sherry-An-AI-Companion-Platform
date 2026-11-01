"""
core/config/models.py
=====================
LLM 推理引擎後端、模型名稱、API 端點與超參數配置。
"""

from __future__ import annotations

import os
from core.config.env import parse_bool_env, parse_float_env, parse_int_env

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

# --- 雲端 Base URLs ---
OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
GEMINI_BASE_URL: str = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
CLAUDE_BASE_URL: str = os.getenv("CLAUDE_BASE_URL", "https://api.anthropic.com/v1")
OPENROUTER_BASE_URL: str = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

# --- 預設模型定義 ---
# 全專案僅保留 5 個語言模型槽位，其餘用途一律沿用 PRIMARY_MODEL 或 LIGHT_MODEL。
# 定義需與 settings.py 保持一致（此模組供離線／CLI 情境使用）。
PRIMARY_MODEL: str = os.getenv("PRIMARY_MODEL", "freetoken:mistralai/Mixtral-8x7B-Instruct-v0.1")
LIGHT_MODEL: str = os.getenv("LIGHT_MODEL", "ollama:qwen2.5:3b")

_raw_vision_model: str = os.getenv("VISION_MODEL", "").strip()
VISION_MODEL: str = "" if (_raw_vision_model.endswith(":") or _raw_vision_model in ("freetoken", "ollama", "lms", "vllm", "openai", "deepseek", "gemini", "openrouter", "claude")) else _raw_vision_model

GEMINI_MIN_OUTPUT_TOKENS: int = parse_int_env("GEMINI_MIN_OUTPUT_TOKENS", 4096)

CLOUD_SEARCH_MODEL: str = os.getenv("CLOUD_SEARCH_MODEL", "deepseek:deepseek-v4-flash")
SELF_MONITORING_MODEL: str = os.getenv("SELF_MONITORING_MODEL", "gemini:gemini-3.5-flash-lite")
CONTEXT_MENU_EPHEMERAL: bool = parse_bool_env("CONTEXT_MENU_EPHEMERAL", False)

# --- 逾時與推論參數 ---
LLM_TIMEOUT: int = parse_int_env("LLM_TIMEOUT", 180)
LLM_STREAM_CHUNK_TIMEOUT: float = parse_float_env("LLM_STREAM_CHUNK_TIMEOUT", 120.0)
OLLAMA_TIMEOUT: int = parse_int_env("OLLAMA_TIMEOUT", LLM_TIMEOUT)
LLM_STREAM_INCLUDE_USAGE: bool = parse_bool_env("LLM_STREAM_INCLUDE_USAGE", True)

# --- 調度模式 ---
AGENT_DISPATCH_MODE: str = os.getenv("AGENT_DISPATCH_MODE", "INTEGRATED").upper()
ACTION_EARLY_TERMINATION_ENABLED: bool = parse_bool_env("ACTION_EARLY_TERMINATION_ENABLED", True)
PARALLEL_MAX_TASKS: int = parse_int_env("PARALLEL_MAX_TASKS", 8)
PARALLEL_STEP_TIMEOUT: float = parse_float_env("PARALLEL_STEP_TIMEOUT", 120.0)

REACT_MAX_STEPS: int = parse_int_env("REACT_MAX_STEPS", 5)
REACT_STEP_TIMEOUT: float = parse_float_env("REACT_STEP_TIMEOUT", 120.0)
REACT_MAX_OBSERVATION_LENGTH: int = parse_int_env("REACT_MAX_OBSERVATION_LENGTH", 1200)
REACT_MAX_TRANSCRIPT_CHARS: int = parse_int_env("REACT_MAX_TRANSCRIPT_CHARS", 6000)

LLM_MAX_CONTEXT_TOKENS: int = parse_int_env("LLM_MAX_CONTEXT_TOKENS", 8192)
LLM_MAX_OUTPUT_TOKENS: int = parse_int_env("LLM_MAX_OUTPUT_TOKENS", 2048)
LLM_NUM_BATCH: int = parse_int_env("LLM_NUM_BATCH", 2048)
OLLAMA_KEEP_ALIVE: str = os.getenv("OLLAMA_KEEP_ALIVE", "60m")

LLM_TEMPERATURE: float = parse_float_env("LLM_TEMPERATURE", 0.85)
LLM_PRESENCE_PENALTY: float = parse_float_env("LLM_PRESENCE_PENALTY", 0.6)
LLM_FREQUENCY_PENALTY: float = parse_float_env("LLM_FREQUENCY_PENALTY", 0.6)
