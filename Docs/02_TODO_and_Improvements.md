# 📋 TODO & 改進事項 (Unified Task Board)

> 最後更新：2026-08-26 20:13  |  整合自：`待改進事項.txt` + `預計事項.txt`

---

## ✅ 已完成功能

- [x] 插件化 Tool Registry（URLSummaryTool / MusicTool / DrawTool / ImageSearchTool / AdvancedSearchTool / ChatTool）
- [x] 複合指令解析器（Compound Parser）：自動拆解多動作指令並依序執行
- [x] NSFW 話題分流（`ChatTool.can_handle_by_regex()` 直接短路）
- [x] Rate Limiter 防刷 與 Ollama 健康監控
- [x] SD 圖片生成（DrawTool）與 VRAM 狀態互斥鎖
- [x] 語音交談系統（STT + TTS + VAD + Recorder，已可加入/離開並載入模型）
- [x] 管理員介面（AdminCog）：6 個 Slash 指令
- [x] 長期記憶（MemoryManager）：UID 綁定屬性萃取 + Python 端物理消毒
- [x] 動態人格系統（persona.py）：三層 Prompt + LOREBOOK 觸發
- [x] **人設定義獨立化**：`CORE_PERSONA` / `LOREBOOK` 已拆分至 `file/AI_personality.py`
- [x] 排程提醒系統（Scheduler）：支援 once / cron，動態提醒文案生成
- [x] 強制意圖執行指令（ExecutorCog）：`/execute` 繞過分類器

---

## 🔴 高優先 — 已知 Bug / 待完成項目

- [ ] **VoiceCog `_listen_loop` TODO**：STT 辨識結果尚未串接 `social.router`，語音交談完整閉環尚未完成
- [ ] **WebExplorer 停用**：目前在 `main.py` 中被注解（`# "handlers.web_explorer"`），尚未重啟

---

## ⚠️ 困難事項

- [ ] **使雪莉能讀取影像**：可能需要使用 Claude API，VRAM 可能導致 BUG

---

## 🚀 第一優先：核心互動體驗補完

- [ ] **語音交談閉環完成**：`_listen_loop` STT 結果串接 `social.router` → TTS 回話
- [ ] **`/help` 全功能指令面板**：Discord Select Menu 分類式幫助（聊天/音樂/畫圖/搜圖/搜尋/語音/排程）
- [ ] **互動按鈕元件化（Discord UI Components）**：
  - 音樂播放器：⏯️ 暫停、⏭️ 下一首、🔀 隨機、🔁 循環
  - 搜圖結果：🔄「再來一張」、⛔「不喜歡」
  - 畫圖結果：🔄「重畫」、📝「修改提示詞」
  - 長期記憶詢問：✅「沒錯」、❌「記錯了」反饋修正

## 🟡 第二優先：社交互動深化

- [ ] **Reaction 快速互動**：🔄 重新生成、🗑️ 刪除（僅提問者）、📌 釘選、❤️ 提升好感度
- [ ] **引用回覆上下文感知**：Reply 引用某則訊息並 @雪莉 時，擷取被引用訊息作為額外上下文
- [ ] **伺服器加入歡迎系統**：新成員加入時自動發送含人設語氣的歡迎訊息

## 🟢 第三優先：質感與便利性

- [ ] **Discord Presence 動態狀態**：根據當前活動切換顯示（🎵 聽音樂 / 🎨 畫圖 / 🎤 語音 / 💤 閒置）
- [ ] **Ephemeral 隱藏訊息**：管理員指令改用 `ephemeral=True`；長操作先 defer
- [ ] **Thread 討論串支援**：高熱度話題自動建立 Thread；Thread 中 @雪莉 能正常回應
- [ ] **圖片辨識（多模態）**：傳圖 + @雪莉 → LLaVA 量化版辨識後回覆

## ⚙️ 第四優先：運維與開發體驗

- [ ] **除錯日誌頻道**：`LOG_CHANNEL_ID` 設定，ThinkingLog 分 INFO / WARNING / ERROR；ERROR 自動推播至管理員
- [ ] **Per-Guild 伺服器端設定面板**：自訂指令前綴、功能開關、插話頻率、NSFW 白名單、暱稱
- [ ] **統計儀表板 `/stats`**：今日互動次數、各 Tool 使用分布、平均回應延遲、佇列深度、VRAM 使用率

---

## 📌 穩定版本 Git Commit

```
80121356a6c1b8e85ff092767d4397090a7b2ac6
```
