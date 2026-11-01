# 22 — 異常處理矩陣、熔斷降級與系統自愈 (Error Handling)

> **難度標記**：`Difficulty: Advanced`  
> **證據等級**：`[CONFIRMED]`

---

## 🎯 Purpose (核心目的)
深度剖析雪莉系統的高可用與彈性架構 (Resilience Architecture)。分析其分層異常處理矩陣、全域事件循環異常過濾器、`core/guard.py` 死循環熔斷器 (Circuit Breaker)、資料庫交易自動 Rollback、LLM 確定性錯誤即時分流以及核心模組 Fail-Fast vs. 外掛模組 Fault-Tolerant 的容錯設計。

---

## 🧭 Prerequisites (先備知識)
- 軟體系統穩定性模式：熔斷器模式 (Circuit Breaker Pattern)、艙壁模式 (Bulkhead Pattern)。
- Python 例外傳播 (Exception Propagation) 與 Context Manager 回滾機制。
- 分散式交易補償與指數退避策略。

---

## 💡 Concept (彈性工程與故障隔離理論)
在長效運行的陪伴系統中，墨菲定律必然發生：外部 API 必定會倒、資料庫必定會鎖、網路必定會斷。
健全的架構必須確保**局部故障絕不引發全系統雪崩 (Cascading Failure)**：
1. **Bulkhead (艙壁隔離)**：外掛的失敗不可蔓延至核心，單一使用者的崩潰不可影響其他群組。
2. **Circuit Breaker (熔斷器)**：偵測到持續錯誤時迅速熔斷，避免持續呼叫癱瘓的下游服務。
3. **Graceful Degradation (優雅降級)**：當高級功能（如即時畫圖、深度聯網搜尋）故障時，系統仍能維持基礎聊天陪伴能力。

---

## 🛠️ Sherry Implementation (雪莉異常處理矩陣)

### 一、全系統異常分類處置矩陣

| 異常類型 / 場景 | 攔截層級 | 處置策略 | 系統後續狀態 |
|---|---|---|---|
| **Windows WinError 10054 (TCP 中斷)** | [CONFIRMED] `main.py` (`loop.set_exception_handler`) | 靜默過濾，忽略無害中斷日誌 | 正常運行，不污染主控制台 |
| **CommandNotFound (無效前綴指令)** | [CONFIRMED] `main.py` (`on_command_error`) | 靜默忽略（防止群友打 `!xxx` 時洗版報錯） | 正常運行 |
| **SQLite 交易崩潰 (`Exception`)** | [CONFIRMED] `core/db.py` (`db_transaction`) | 自動執行 `conn.rollback()`，記錄 ERROR 日誌並重拋 | 維持資料庫 ACID 一致性 |
| **核心 Cog 載入失敗** | [CONFIRMED] `main.py` (`bot.load_extension`) | 記錄完整 Stack Trace 並執行 `sys.exit(1)` (Fail-Fast) | 進程重啟，防止半殘運行 |
| **可選外掛 Mod 載入失敗** | [CONFIRMED] `main.py` (`_discover_optional_extensions`) | 捕捉 `Exception`，輸出 WARNING，跳過該外掛 | 主核心正常上線 |
| **死循環與重複呼叫風暴** | [CONFIRMED] `core/guard.py` (`CoreGuard`) | 觸發 Circuit Breaker 熔斷阻斷後續執行 | 阻斷單一請求，釋放 CPU |
| **LLM 安全過濾 (Safety Block)** | [CONFIRMED] `handlers/bot_brain/cog.py` | 判定為確定性錯誤，跳過原模型重試，直切備援 | 快速切換或優雅提示 |
| **GPU 顯存鎖等待超時** | [CONFIRMED] `handlers/bot_brain/cog.py` | 捕捉 `TimeoutError`，限制重試次數，切換輕量模型 | 釋放 GPU 鎖 |

---

### 二、死循環熔斷器 (`core/guard.py`)
- 當對話機器人遇到惡意對話陷阱、自身與其他 Bot 互相觸發、或 Tool 重複發動相同指令時，容易產生死循環。
- `[CONFIRMED]` [`core/guard.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/core/guard.py)：
  `CoreGuard` 維護近期調用簽名計數器。若同一 Session 在短時間內重複進入相同意圖超過閥值，立即熔斷阻斷，並向使用者發送「偵測到重複迴圈，已主動暫停處理」防護訊息。

---

### 三、資料庫交易 Rollback 保護
- `[CONFIRMED]` [`core/db.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/core/db.py#L65-L99)：
  ```python
  @contextlib.contextmanager
  def db_transaction(db_path, ...):
      conn = get_db_connection(db_path, ...)
      cursor = conn.cursor()
      try:
          yield cursor
          conn.commit()
      except Exception as e:
          conn.rollback()
          logger.error("[CoreDB] 交易失敗，已執行 Rollback: %s", e)
          raise
      finally:
          conn.close()
  ```
  嚴格保證任何未預期例外均能觸發交易回滾與連線安全關閉。

---

## 🔍 Source Evidence & Ground Truth (原始碼佐證)

1. `[CONFIRMED]` **WinError 10054 例外過濾實證**：
   - 見 [`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L190-L198)。
2. `[CONFIRMED]` **CommandNotFound 忽略邏輯**：
   - 見 [`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L494-L496)。
3. `[CONFIRMED]` **交易 Rollback 上下文管理器**：
   - 見 [`core/db.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/core/db.py#L90-L98)。

---

## 📊 Flow Diagram (分層容錯防禦拓撲)

```text
[Unhandled Exception in Tool / Handler]
                  │
                  ▼
   /─────────────────────────────\
  <     是否發生在 db_transaction?   >
   \─────────────────────────────/
            /             \
      (是) /               \ (否)
          ▼                 ▼
   [conn.rollback()]  /─────────────────────────────\
   [連線安全關閉]      <   是否為 Core Cog 載入失敗?   >
          │            \─────────────────────────────/
          │                     /             \
          │               (是) /               \ (否)
          │                   ▼                 ▼
          │             [sys.exit(1)]    /─────────────────────────\
          │             (Fail-Fast)     <   是否為 Mod 插件失敗?   >
          │                              \─────────────────────────/
          │                                        /        \
          │                                  (是) /          \ (否)
          │                                      ▼            ▼
          │                             [logger.warning] [上報 log_error()]
          │                             [優雅跳過該外掛]  [發送 Discord 友善提示]
          ▼                                      │            │
  +──────────────────────────────────────────────┴────────────┴───+
  |                   系統維持高可用穩態 (Zero Crash)                |
  +---------------------------------------------------------------+
```

---

## ⚠️ Failure Cases & Edge Cases (異常邊界案例)

1. **連線重拋掩蓋真實原因 (Exception Chaining Loss)**：
   - 在部分早期程式碼中，`except Exception as e: raise RuntimeError("失敗")` 缺少 `from e`，導致遺失原始底層 Stack Trace。目前規範第 3 節已強制要求保持完整追蹤鏈路。
2. **資料庫連線洩漏導致鎖定無法釋放**：
   - 如前述 `mod/market_plugin/repository.py` 的直連連線，若在 `with` 區塊外部發生異常，Cursor 未關閉可能造成短暫鎖佔用。

---

## ⚖️ Trade-offs & Engineering Decisions (架構權衡)

| 設計選擇 | 優點 | 缺點 / 潛在代價 | 替代方案 |
|---|---|---|---|
| **核心 Fail-Fast，外掛 Fault-Tolerant** | 確保核心功能絕對可靠，同時最大化周邊外掛的容錯生存能力。 | 外掛載入失敗時使用者可能在 Discord 指令列表中看不到特定指令，產生疑惑。 | 載入失敗時自動發送管理員警報通知與 Web UI 標紅。 |

---

## 💼 Interview Relevance (面試考點)

- **Q: 在微服務或模組化單體中，如何定義「可容忍錯誤」與「致命錯誤 (Fatal Error)」的邊界？**
  - **Ans**:
    - **致命錯誤 (Fatal)**：影響系統基礎設施完整性（如設定檔損毀、資料庫連線池無法初始化、事件循環無法啟動、核心身分驗證失效）。此類錯誤應立刻實施 Fail-Fast，讓進程重啟或觸發健康探針失敗。
    - **可容忍錯誤 (Tolerable / Recoverable)**：單一消費者的請求異常、第三方非核心插件故障、網路暫態超時。此類錯誤應實施艙壁隔離與退避降級，保證主體服務不受波及。

---

## 🔗 What to Study Next (後續章節銜接)
- 進入效能評估維度，前往 [Performance/27-bottleneck-analysis.md](../Performance/27-bottleneck-analysis.md) 研讀系統效能瓶頸與資源爭用剖析。
