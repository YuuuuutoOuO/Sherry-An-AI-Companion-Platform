# 🎯 專案總覽 (Project Snapshot)

> 最後更新：2026-08-05 16:43

---

## 專案基本資訊

- **專案名稱**：橘雪莉 (Shiry) — 泛用型超擬真 Discord AI Agent
- **核心目標**：打造一個具備高度擴展性、獨立靈魂記憶與特規功能（社群記憶、音樂點唱、防封鎖搜尋、解限搜圖、語音交談、圖片生成、排程提醒）的二次元虛擬智慧實況夥伴。

## 技術棧 (Tech Stack)

| 類別 | 技術 |
|---|---|
| 語言 | Python 3.10+（`from __future__ import annotations` 後向兼容） |
| Discord 框架 | `discord.py` (v2.4+)，全採 Cog 模組化，Slash commands & Events |
| LLM 推理 | `Ollama` Local Inference API（`/api/chat` 非同步 POST） |
| STT | `faster-whisper` large-v3（CUDA float16, zh） |
| TTS | `TTS` XTTS-v2（speaker_wav 克隆音色） |
| 圖片生成 | Stable Diffusion WebUI API（`/sdapi/v1/txt2img`） |
| HTTP | `aiohttp`（非同步）、`trafilatura`（高效能網文解析） |
| 音樂 | `yt-dlp` + `FFmpeg`（LoudNorm -14 LUFS 串流） |

## 模型配置

| 用途 | 模型名稱 |
|---|---|
| `PRIMARY_MODEL` / `NOMORAL_MODEL` | `krith/mistral-nemo-instruct-2407-abliterated:IQ4_XS` |
| `QUICK_MODEL` / `CLASSIFIER_MODEL` / `VIBE_MODEL` | `mannix/llama3.1-8b-abliterated` |
| LLM 輸入上限 | `num_ctx = 16384` |
| LLM 輸出上限 | `num_predict = 512` |

## 設計模式

- **模組化 Agent 架構 (Tool-Router Architecture)**：所有功能封裝為 `BaseTool` 子類別
- **兩階漏斗路由 (Hybrid Regex + LLM Intent Routing)**：Phase 1 正則極速命中 → Phase 2 LLM 動態派任
- **狀態機互斥鎖 (State Mutex)**：保護 16GB VRAM（`_sd_generating` / `_voice_mode_active`）

## 核心資料流

```text
用戶文字 @Tag
  → RateLimiter（5次/30秒防洗頻）
  → fetch_context_script（最近30則 + 1800秒斷層截斷）
  → MemoryManager.get_user_memory_snippet（UID長期屬性注入）
  → VibeManager.get_top_topics_snippet（頻道熱門話題注入）
  → parse_compound_command（LLM拆解複合指令，priority=0）
  → 對每個子句：
      Phase 1: Tool.can_handle_by_regex() 正則極速命中
      Phase 2: CLASSIFIER_MODEL 動態派任
  → Tool.execute() → persona.py build_dynamic_persona()
  → BotBrain.enqueue_task()（asyncio.PriorityQueue）
  → _inference_worker → callback → Discord 回覆
```

## Tool Registry 優先順序

```text
URLSummaryTool → MusicTool → DrawTool → ImageSearchTool → AdvancedSearchTool → ChatTool
```

## VRAM 互斥法則

| 狀態旗標 | 影響 |
|---|---|
| `_voice_mode_active = True` | `DrawTool.execute()` 拒絕執行 |
| `_sd_generating = True` | `_inference_worker` 暫停所有 LLM 推理 |

## 三層 Prompt 架構

| 層次 | 來源 | 內容 |
|---|---|---|
| A（靈魂底核） | `file/AI_personality.py` → `settings.CORE_PERSONA` | 常駐角色人設 + 防幻覺鐵則 |
| B（情境帽） | `social/persona.py` | intent 對應的行為模式 + LOREBOOK Regex 觸發 |
| C（執行規定） | `tools/*.py` | 字數限制、回覆格式、RAG 防幻覺鐵則 |

---

> 🔗 詳細的各模組 function 與參數紀錄請參閱：
> - [module_root.md](./module_root.md)
> - [module_file.md](./module_file.md)
> - [module_handlers.md](./module_handlers.md)
