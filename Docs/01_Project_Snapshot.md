# 🎯 專案總覽 (Project Snapshot)

> 最後更新：2026-09-23 02:00 (V2.0+ 架構校準)

---

## 專案基本資訊

- **專案名稱**：橘雪莉 (Sherry) — 泛用型超擬真 Companion & AI Agent 平台
- **核心目標**：打造具備高度擴展性、獨立靈魂記憶、受控自主性（SPARK 條件觸發、讀空氣、主動插話）與全方位互動特能（社群記憶、音樂串流、防封鎖搜尋、解限搜圖、語音交談、圖片生成、排程提醒、股票追蹤）的二次元虛擬智慧實況夥伴。

## 技術棧 (Tech Stack)

| 類別 | 技術 |
|---|---|
| 語言 | Python 3.10+（`from __future__ import annotations` 後向兼容，支援 3.11/3.12） |
| 治理與架構 | Layered Architecture (Presentation `handlers/` / Domain `mod/` / Foundation `core/`)，依據 `AGENTS.md` |
| Discord 框架 | `discord.py` (v2.4+)，全採 Cog 模組化，Slash commands, UI Modals/Views & Events |
| LLM 推理引擎 | 多後端支援：`Ollama` (Local)、`Gemini` (Google AI Studio)、`Claude` (Anthropic)、`OpenAI` |
| STT | `faster-whisper` large-v3（CUDA float16, zh） |
| TTS | `TTS` XTTS-v2（speaker_wav 克隆音色） / Edge-TTS 備援 |
| 圖片生成 | Stable Diffusion WebUI API（`/sdapi/v1/txt2img`） |
| 網路 I/O | `aiohttp`（非同步）、`trafilatura`（高效能網文解析） |
| 音樂串流 | `yt-dlp` + `FFmpeg`（LoudNorm -14 LUFS 串流） |
| 資料持久化 | SQLite (`PRAGMA user_version` 自動遷移)、JSONL (日誌與審計軌跡)、JSON (快取與設定覆寫) |

## 模型配置與調度架構

| 用途 | 預設模型 | 說明 |
|---|---|---|
| `PRIMARY_MODEL` | `krith/mistral-nemo-instruct-2407-abliterated:IQ4_XS` / Gemini 1.5 Flash | 核心擬真對話與複雜工具規劃 |
| `QUICK_MODEL` / `CLASSIFIER` | `mannix/llama3.1-8b-abliterated` | 意圖路由判斷、快速分類與即時摘要 |
| `VIBE_MODEL` / `SUMMARY` | 輕量化模型 | 頻道氣氛推論與定期世界記憶歸檔 |
| Context 視窗 | `num_ctx = 16384` | 動態 Token 預算與 Prefix Caching 最優化 |

## 系統核心分層架構

```text
[Presentation / Gateway]
  handlers/ (discord_event_handlers.py, admin/, spark/, social/, memory/, music/, voice/)
  web_ui/ (獨立本地維運儀表板與安全日誌檢視)
      ↓
[Core Governance & Runtime Coordination]
  core/ (db.py, db_migration.py, guard.py, risk_engine.py, autonomy_guard.py, system_state.py)
      ↓
[Domain Logic & Business Services]
  mod/ (audit_manager.py, sys_monitor/, error_logger.py)
      ↓
[External Clients & Repositories]
  mod/clients/ & mod/repositories/ (唯一允許直接調用外部 API / DB 的邊界)
```

## AI 調度與資料流

```text
Discord 事件 (Message / Mention / Slash Command)
  → CoreGuard (頻率限制、工具迴圈熔斷、黑名單過濾)
  → Context Builder (最近歷史 + 物理隔離消毒 + 關聯長期記憶 + 頻道氛圍)
  → Dispatch Agent (Integrated / Parallel / ReAct 三模式調度)
  → Tool Execution (安全沙箱 + <untrusted_external_content> 隔離)
  → Persona Engine (三層靈魂帽子疊加 + 語氣規範)
  → Inference Client (多後端容錯與 Streaming Thinking)
  → Response Delivery & 審計日誌記錄
```

## 自主性與運維體系 (SPARK & Proactive Behaviors)

1. **SPARK 條件推播引擎**：4 大感測器（股價觸發、排程提醒、RSS 即時訂閱、天氣預報）。
2. **三級風險決策引擎 (Risk Engine)**：
   - `LOW`：自動清理、狀態採集（完全自主）。
   - `MEDIUM`：推播通知、頻寬微調（通知可設定自動化）。
   - `HIGH`：全域設定更動、高危指令（強制 Discord/Web UI 雙重審核確認）。
3. **全域安全熔斷**：`GLOBAL_AUTONOMY_KILL_SWITCH` 與時間窗預算上限防失控。
