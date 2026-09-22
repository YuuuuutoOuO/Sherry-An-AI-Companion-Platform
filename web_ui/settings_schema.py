"""
web_ui/settings_schema.py
=========================
Web UI Settings Metadata and Schema Definition.

Provides user-friendly labels, descriptions, categories, input widget types,
and numerical constraints for system parameters editable via Web Console.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

SETTINGS_META: dict[str, dict[str, Any]] = {
    # === 模型設定 ===
    "PRIMARY_MODEL": {
        "label": "主力模型 (Primary Model)",
        "description": "平衡智商與生成速度，用於日常深度對話與邏輯推演。"
                        "格式：前綴:模型名（例：freetoken:mistralai/Mixtral-8x7B-Instruct-v0.1 / ollama:llama3.2）",
        "type": "model",
        "category": "模型設定",
    },
    "NOMORAL_MODEL": {
        "label": "無道德限制模型 (No-Moral Model)",
        "description": "專門處理橘雪莉缺乏倫理觀的瘋狂人設，通常使用 abliterated 版本模型。",
        "type": "model",
        "category": "模型設定",
    },
    "QUICK_MODEL": {
        "label": "快速回覆模型 (Quick Model)",
        "description": "要求極速回覆時使用，需要足夠基礎智商。適合簡短互動。",
        "type": "model",
        "category": "模型設定",
    },
    "CLASSIFIER_MODEL": {
        "label": "分類輕量模型 (Classifier Model)",
        "description": "用於插話評分與 YouTube 影片篩選，輕量優先（如 qwen2.5:3b）。",
        "type": "model",
        "category": "模型設定",
    },
    "VIBE_MODEL": {
        "label": "氛圍萃取模型 (Vibe Model)",
        "description": "從聊天記錄中萃取當前話題氛圍，輕量模型即可勝任。",
        "type": "model",
        "category": "模型設定",
    },
    "VISION_MODEL": {
        "label": "視覺辨識模型 (Vision Model)",
        "description": "多模態圖片對話模型（如 ollama:minicpm-v 或 lms:qwen2.5-vl）。當訊息夾帶圖片時，由該模型同時執行看圖理解與角色性格回覆（單階段直出），大幅減輕顯卡負擔並降低延遲。若留空（預設），將使用主力模型（PRIMARY_MODEL）原生看圖對話。",
        "type": "model",
        "category": "模型設定",
    },
    "CLOUD_SEARCH_MODEL": {
        "label": "雲端搜尋模型 (Cloud Search Model)",
        "description": "網路搜尋任務使用的雲端模型，需要良好的工具呼叫能力。",
        "type": "model",
        "category": "模型設定",
    },
    "AGENT_DISPATCH_MODE": {
        "label": "代理人調度模式 (Agent Dispatch Mode)",
        "description": "選擇訊息解析與工具調度核心：INTEGRATED（單階段整合自主調度，日常閒聊秒回，推薦）、PARALLEL（雙階段並行調度）、REACT（多步驟思考迭代迴圈）。",
        "type": "select",
        "options": [
            {"value": "INTEGRATED", "label": "⚡ 單階段整合自主調度 (Integrated) - 推薦"},
            {"value": "PARALLEL", "label": "🚀 雙階段高效能並行 (Parallel)"},
            {"value": "REACT", "label": "🔄 經典多步驟 ReAct (Sequential)"},
        ],
        "category": "模型設定",
    },
    "ACTION_EARLY_TERMINATION_ENABLED": {
        "label": "行為自適應對話抑制 (Action Early Termination)",
        "description": "開啟 (True) = 當發動的工具全為自帶 UI 渲染（如點歌、搜圖、畫圖）且無聊天需求時，動作完成即刻收工，不發送多餘文字；關閉 (False) = 動作完成後仍強制由主力模型生成文字閒聊回覆。",
        "type": "bool",
        "category": "模型設定",
    },
    "PARALLEL_PLANNER_MODEL": {
        "label": "並行調度模型 (Parallel Planner Model)",
        "description": "用於 Parallel Agent 單次任務拆解與對話抑制標記的輕量模型。",
        "type": "model",
        "category": "模型設定",
    },
    "REACT_MODEL": {
        "label": "ReAct 任務拆解模型 (ReAct Planner Model)",
        "description": "ReAct 多步驟 Agent 用於意圖分析與工具發動的規劃模型（負責「思考→行動→觀察」步驟調度）。最終對話生成會由 PRIMARY_MODEL (主力模型) 進行發言合成。",
        "type": "model",
        "category": "模型設定",
    },
    "OLLAMA_TIMEOUT": {
        "label": "Ollama 逾時秒數 (Timeout)",
        "description": "等待 Ollama 回應的最長秒數，較大模型需要更長時間。建議 60–180。",
        "type": "int",
        "min": 10,
        "max": 600,
        "category": "LLM 參數",
    },
    "LLM_MAX_CONTEXT_TOKENS": {
        "label": "最大 Context Token 數",
        "description": "送給 LLM 的最大上下文長度（包含 System Prompt + 歷史記錄）。越大越耗資源。",
        "type": "int",
        "min": 1024,
        "max": 131072,
        "category": "LLM 參數",
    },
    "LLM_MAX_OUTPUT_TOKENS": {
        "label": "最大輸出 Token 數",
        "description": "模型單次生成的最大 token 數量。太少會截斷回覆，太多會降低速度。",
        "type": "int",
        "min": 128,
        "max": 8192,
        "category": "LLM 參數",
    },
    "LLM_TEMPERATURE": {
        "label": "生成溫度 (Temperature)",
        "description": "控制輸出隨機性。0 = 完全確定性；1 = 高隨機創意。日常對話建議 0.7–0.9。",
        "type": "float",
        "min": 0.0,
        "max": 2.0,
        "step": 0.05,
        "category": "LLM 參數",
    },
    "LLM_PRESENCE_PENALTY": {
        "label": "Presence Penalty（話題多樣性）",
        "description": "懲罰已出現過的 token，迫使模型引入新話題。建議 0.4–0.8。",
        "type": "float",
        "min": -2.0,
        "max": 2.0,
        "step": 0.05,
        "category": "LLM 參數",
    },
    "LLM_FREQUENCY_PENALTY": {
        "label": "Frequency Penalty（降低重複）",
        "description": "懲罰重複出現的 token，降低跳針（重複生成相同句子）機率。建議 0.4–0.8。",
        "type": "float",
        "min": -2.0,
        "max": 2.0,
        "step": 0.05,
        "category": "LLM 參數",
    },
    "LLM_NUM_BATCH": {
        "label": "Prompt 批次處理量 (n_batch)",
        "description": "GPU 單次平行處理的 Prompt Token 批次大小（對應 llama.cpp n_batch）。數值越大首字延遲越低，但 VRAM 佔用略增。RTX A4000 (16GB) 建議 2048–4096。",
        "type": "int",
        "min": 512,
        "max": 8192,
        "category": "LLM 參數",
    },
    "OLLAMA_KEEP_ALIVE": {
        "label": "Ollama 模型駐留時間 (Keep-Alive)",
        "description": "推論完成後模型與 KV 快取在 VRAM 中保留多久。支援時間格式：\"60m\"（60 分鐘）、\"24h\"（24 小時）、\"0\"（立即卸載，停用快取）。建議設為 \"60m\" 以發揮 Prompt Caching 效益。",
        "type": "str",
        "category": "LLM 參數",
    },
    # === 後端端點設定 ===
    "LLM_BASE_URL": {
        "label": "FreeToken / LLM 伺服器 URL",
        "description": "本機 FreeToken Edge-Native MoE 服務或 OpenAI 相容端點，預設為 http://localhost:1919/v1。",
        "type": "url",
        "category": "後端連線設定",
    },
    "OLLAMA_BASE_URL": {
        "label": "Ollama 伺服器 URL",
        "description": "本機 Ollama 服務的基礎 URL，預設為 http://localhost:11434。",
        "type": "url",
        "category": "後端連線設定",
    },
    "LMS_BASE_URL": {
        "label": "LM Studio 伺服器 URL",
        "description": "本機 LM Studio 的 OpenAI 相容 API 位址，預設為 http://localhost:1234/v1。",
        "type": "url",
        "category": "後端連線設定",
    },
    "OPENAI_BASE_URL": {
        "label": "OpenAI API URL",
        "description": "OpenAI 相容 API 端點，可指向 OpenAI 或第三方代理。",
        "type": "url",
        "category": "後端連線設定",
    },
    "DEEPSEEK_BASE_URL": {
        "label": "DeepSeek API URL",
        "description": "DeepSeek API 端點 URL。",
        "type": "url",
        "category": "後端連線設定",
    },
    "GEMINI_BASE_URL": {
        "label": "Gemini API URL",
        "description": "Google Gemini OpenAI 相容端點 URL。",
        "type": "url",
        "category": "後端連線設定",
    },
    "CLAUDE_BASE_URL": {
        "label": "Claude API URL",
        "description": "Anthropic Claude Messages API 端點。",
        "type": "url",
        "category": "後端連線設定",
    },
    "OPENROUTER_BASE_URL": {
        "label": "OpenRouter API URL",
        "description": "OpenRouter OpenAI 相容端點 URL，預設為 https://openrouter.ai/api/v1。",
        "type": "url",
        "category": "後端連線設定",
    },
    # === 記憶體與上下文 ===
    "CONTEXT_HISTORY_LIMIT": {
        "label": "歷史記錄筆數限制",
        "description": "注入 LLM Prompt 的最大對話輪數（USER+BOT 各算一輪）。越高記憶越長但越耗 token。",
        "type": "int",
        "min": 2,
        "max": 100,
        "category": "記憶與上下文",
    },
    "CONTEXT_TIME_GAP_SECONDS": {
        "label": "上下文時間斷層門檻（秒）",
        "description": "兩則訊息相差超過此秒數，視為對話斷層並截斷歷史。預設 900 秒（15 分鐘）。",
        "type": "int",
        "min": 60,
        "max": 86400,
        "category": "記憶與上下文",
    },
    "CONTEXT_HISTORY_MAX_TOKENS": {
        "label": "歷史區塊硬性封頂（tokens）",
        "description": "歷史區塊的 token 估算上限。即使濃縮失敗，也絕不讓歷史超過此值，從根源封死「使用者每句 2-300 字」造成的快速膨脹。估算偏保守，實際 token 數通常更低。",
        "type": "int",
        "min": 500,
        "max": 20000,
        "category": "記憶與上下文",
    },
    "CONTEXT_COMPRESS_MIN_GAP_MESSAGES": {
        "label": "濃縮水位節流（累積新訊息數）",
        "description": "距離上次濃縮至少累積幾則新訊息才允許再次濃縮。越大則壓縮次數越少、體感越順，但歷史比較長；建議 6–12。",
        "type": "int",
        "min": 1,
        "max": 30,
        "category": "記憶與上下文",
    },
    "CONTEXT_COMPRESS_KEEP_RECENT": {
        "label": "濃縮後保留最近原文則數",
        "description": "濃縮後原樣保留的最近幾則對話，供雪莉當下精準接話。越多越連貫但越吃 token；建議 3–6。",
        "type": "int",
        "min": 1,
        "max": 12,
        "category": "記憶與上下文",
    },
    "CONTEXT_COMPRESS_THRESHOLD": {
        "label": "濃縮觸發字數門檻",
        "description": "歷史區塊超過此字數才考慮濃縮。太小會過度濃縮失去細節，太大則 prompt 偏大；預設 2000。",
        "type": "int",
        "min": 300,
        "max": 6000,
        "category": "記憶與上下文",
    },
    "MEMORY_MAX_FACTS_IN_CONTEXT": {
        "label": "記憶注入數量（每次 Prompt）",
        "description": "每次對話注入 LLM Prompt 的最大 facts 數（不含 rule 類，rule 類永遠全數注入）。數字越高雪莉記住的事越多，但會消耗更多 token。預設 15。",
        "type": "int",
        "min": 3,
        "max": 30,
        "category": "記憶與上下文",
    },
    "MEMORY_MAX_FACTS_PER_USER": {
        "label": "每人最大記憶條數",
        "description": "每位使用者最多保留的 facts 總數，超出時會移除 importance 最低的舊事實。預設 25。",
        "type": "int",
        "min": 10,
        "max": 50,
        "category": "記憶與上下文",
    },
    "MEM_DIGEST_THRESHOLD": {
        "label": "即時萃取觸發門檻（句數）",
        "description": "每位使用者累積幾句話後，觸發一次即時記憶萃取。數字越小越頻繁但每次 context 較短；越大則 context 豐富品質較高。預設 10。",
        "type": "int",
        "min": 3,
        "max": 30,
        "category": "記憶與上下文",
    },
    "MEMORY_EXTRACT_MODEL": {
        "label": "記憶萃取模型",
        "description": "即時萃取（每 N 句觸發）使用的模型。建議與主力模型一致以確保萃取品質。格式：前綴:模型名。",
        "type": "model",
        "category": "記憶與上下文",
    },
    "MEMORY_PERMANENT_FACT_THRESHOLD": {
        "label": "永久保留 importance 門檻",
        "description": "importance 達到或超過此值的 facts 永久保留，不受每日衰減與數量上限影響。預設 4。",
        "type": "int",
        "min": 3,
        "max": 5,
        "category": "記憶與上下文",
    },
    # === 氛圍系統 ===
    "VIBE_UPDATE_MESSAGE_THRESHOLD": {
        "label": "氛圍更新訊息門檻",
        "description": "累積幾則訊息後才觸發氛圍（話題）萃取更新。",
        "type": "int",
        "min": 5,
        "max": 500,
        "category": "氛圍系統",
    },
    "VIBE_TOP_N_TOPICS": {
        "label": "話題萃取數量 (Top N)",
        "description": "每次氛圍萃取保留前 N 個主要話題注入 Prompt。",
        "type": "int",
        "min": 1,
        "max": 10,
        "category": "氛圍系統",
    },
    "VIBE_DECAY_INTERVAL_SECONDS": {
        "label": "話題衰減間隔（秒）",
        "description": "每隔多少秒話題權重自動降低一次，模擬話題逐漸淡忘。",
        "type": "int",
        "min": 300,
        "max": 86400,
        "category": "氛圍系統",
    },
    "VIBE_DECAY_AMOUNT": {
        "label": "話題衰減量",
        "description": "每次衰減時話題權重減少的數值。",
        "type": "int",
        "min": 1,
        "max": 10,
        "category": "氛圍系統",
    },
    "VIBE_MIN_WEIGHT_TO_INJECT": {
        "label": "話題注入最低權重",
        "description": "低於此權重的話題不會注入 Prompt（避免注入過時或無關話題）。",
        "type": "int",
        "min": 1,
        "max": 20,
        "category": "氛圍系統",
    },
    "VIBE_REINFORCE_MAX_WEIGHT": {
        "label": "話題強化最高權重上限",
        "description": "話題再次出現時可補滿到的最高權重值。",
        "type": "int",
        "min": 5,
        "max": 50,
        "category": "氛圍系統",
    },
    # === 插話引擎 ===
    "INTERJECT_EVAL_THRESHOLD": {
        "label": "插話觸發門檻（訊息數）",
        "description": "每累積幾則真人訊息，觸發一次插話評分。",
        "type": "int",
        "min": 1,
        "max": 100,
        "category": "插話引擎",
    },
    "INTERJECT_COOLDOWN_SECONDS": {
        "label": "插話冷卻時間（秒）",
        "description": "插話後的冷卻秒數，冷卻期間不再觸發插話評估。",
        "type": "int",
        "min": 0,
        "max": 600,
        "category": "插話引擎",
    },
    "INTERJECT_MAX_CONSECUTIVE": {
        "label": "最大連續插話次數",
        "description": "同一冷卻週期內最多允許連續插話幾次，超過後需等下一週期。",
        "type": "int",
        "min": 1,
        "max": 20,
        "category": "插話引擎",
    },
    "INTERJECT_SCORE_THRESHOLD": {
        "label": "插話評分門檻 (1–10)",
        "description": "LLM 插話評分 >= 此值才實際插話。設為 10 = 幾乎不主動插話。",
        "type": "int",
        "min": 1,
        "max": 10,
        "category": "插話引擎",
    },
    # === 速率限制 ===
    "RATE_LIMIT_MAX_REQUESTS": {
        "label": "速率限制：最大請求數",
        "description": "在時間窗口內，同一用戶最多可以發送的請求數量。",
        "type": "int",
        "min": 1,
        "max": 100,
        "category": "速率限制",
    },
    "RATE_LIMIT_WINDOW_SECONDS": {
        "label": "速率限制：時間窗口（秒）",
        "description": "速率限制的統計時間窗口長度（秒）。",
        "type": "int",
        "min": 5,
        "max": 3600,
        "category": "速率限制",
    },
    # === 打字模擬 ===
    "TYPING_BASE_DELAY": {
        "label": "打字延遲基礎值（秒）",
        "description": "雪莉開始打字前的最小等待秒數，模擬人類思考時間。",
        "type": "float",
        "min": 0.0,
        "max": 10.0,
        "step": 0.1,
        "category": "打字模擬",
    },
    "TYPING_DELAY_PER_50_CHARS": {
        "label": "每 50 字打字追加延遲（秒）",
        "description": "回覆每 50 個字增加的打字延遲，模擬長文打字速度。",
        "type": "float",
        "min": 0.0,
        "max": 5.0,
        "step": 0.1,
        "category": "打字模擬",
    },
    # === Chain-of-Thought & Debug ===
    "MICRO_COT_ENABLED": {
        "label": "啟用 Micro Chain-of-Thought",
        "description": "開啟後，雪莉在每次回覆前先輸出內心推理 <thought>。"
                        "會增加推理品質但略微降低速度。",
        "type": "bool",
        "category": "Chain-of-Thought",
    },
    "DEBUG_DETAIL": {
        "label": "Discord 卡片式偵錯模式 (Debug Detail)",
        "description": "開啟 (True) = 雪莉在 Discord 的回覆將切換為卡片式展示（包含意圖標籤、CoT 思考過程、執行耗時 ms、模型名稱，與 WebUI 對齊）。",
        "type": "bool",
        "category": "Chain-of-Thought",
    },
    # === 圖片生成 ===
    "USE_ONLINE_IMAGE_MODEL": {
        "label": "使用線上圖片生成模型",
        "description": "True = 優先使用 Gemini/HF/Pollinations 線上生成；False = 使用本機 SD WebUI。",
        "type": "bool",
        "category": "圖片生成",
    },
    "POLLINATIONS_MODEL": {
        "label": "Pollinations 圖片模型",
        "description": "Pollinations.ai 使用的模型名稱。推薦：flux-anime（二次元）、z-image-turbo（寫實）。",
        "type": "str",
        "category": "圖片生成",
    },
    "GEMINI_IMAGE_MODEL": {
        "label": "Gemini 圖片生成模型",
        "description": "Google Gemini 圖片生成使用的模型 ID。",
        "type": "str",
        "category": "圖片生成",
    },
    "SD_WEBUI_URL": {
        "label": "Stable Diffusion WebUI URL",
        "description": "本機 SD WebUI（AUTOMATIC1111）的服務位址，作為圖片生成後備方案。",
        "type": "url",
        "category": "圖片生成",
    },
    "SD_DEFAULT_WIDTH": {
        "label": "SD 預設圖片寬度（px）",
        "description": "Stable Diffusion 生成圖片的預設寬度，建議為 64 的倍數。",
        "type": "int",
        "min": 256,
        "max": 2048,
        "category": "圖片生成",
    },
    "SD_DEFAULT_HEIGHT": {
        "label": "SD 預設圖片高度（px）",
        "description": "Stable Diffusion 生成圖片的預設高度，建議為 64 的倍數。",
        "type": "int",
        "min": 256,
        "max": 2048,
        "category": "圖片生成",
    },
    "SD_DEFAULT_STEPS": {
        "label": "SD 推理步數 (Steps)",
        "description": "步數越多品質越高但速度越慢，一般推薦 20–30 步。",
        "type": "int",
        "min": 5,
        "max": 150,
        "category": "圖片生成",
    },
    "SD_DEFAULT_CFG_SCALE": {
        "label": "SD CFG Scale（提示詞遵從度）",
        "description": "越高越嚴格遵從 Prompt，過高會過度飽和。建議 5–9。",
        "type": "float",
        "min": 1.0,
        "max": 30.0,
        "step": 0.5,
        "category": "圖片生成",
    },
    # === 健康監控 ===
    "HEALTH_CHECK_INTERVAL_SECONDS": {
        "label": "健康監控間隔（秒）",
        "description": "Bot 健康狀態檢查的頻率，單位為秒。",
        "type": "int",
        "min": 30,
        "max": 3600,
        "category": "健康監控",
    },
    "DISCORD_LOG_LEVEL": {
        "label": "Discord 日誌等級",
        "description": "discord.py 的日誌輸出等級。可選：DEBUG、INFO、WARNING、ERROR、CRITICAL。",
        "type": "str",
        "category": "健康監控",
    },
    # === Web 爬蟲 ===
    "EXPLORER_INTERVAL_SECONDS": {
        "label": "爬蟲執行間隔（秒）",
        "description": "閒置爬蟲（Web Explorer）的觸發間隔。",
        "type": "int",
        "min": 60,
        "max": 86400,
        "category": "Web 爬蟲",
    },
    "YOUTUBE_MAX_RESULTS": {
        "label": "YouTube 搜尋最大結果數",
        "description": "每次 YouTube 搜尋的最大候選影片數量。",
        "type": "int",
        "min": 1,
        "max": 50,
        "category": "Web 爬蟲",
    },
    # === 深夜整合 ===
    "NIGHTLY_DIGEST_HOUR": {
        "label": "深夜整合觸發時間（小時）",
        "description": "每天深夜幾點觸發記憶整合任務（台灣時間 UTC+8）。預設 3（凌晨 3 點）。",
        "type": "int",
        "min": 0,
        "max": 23,
        "category": "深夜記憶整合",
    },
    "NIGHTLY_MAX_MSGS_PER_USER": {
        "label": "深夜整合每用戶最大訊息數",
        "description": "每位使用者最多送入 LLM 進行整合的訊息數量，過多會超出 context。",
        "type": "int",
        "min": 10,
        "max": 2000,
        "category": "深夜記憶整合",
    },
    "NIGHTLY_DIGEST_MODEL": {
        "label": "深夜記憶整合模型",
        "description": "進行每日深夜對話記憶摘要與精煉的模型前綴與名稱。",
        "type": "model",
        "category": "深夜記憶整合",
    },
    "NIGHTLY_DIGEST_TIMEOUT": {
        "label": "深夜整合逾時秒數",
        "description": "深夜記憶整合任務的最長執行等待秒數。",
        "type": "float",
        "min": 30,
        "max": 600,
        "step": 0.5,
        "category": "深夜記憶整合",
    },
    "NIGHTLY_MIN_MSGS_THRESHOLD": {
        "label": "深夜整合最低訊息門檻",
        "description": "使用者當日發送超過此數量訊息時，才會觸發該用戶的深夜記憶總結。",
        "type": "int",
        "min": 1,
        "max": 50,
        "category": "深夜記憶整合",
    },
    "NIGHTLY_WORLD_SAMPLE_COUNT": {
        "label": "世界記憶每日取樣訊息數",
        "description": "深夜為每個伺服器提煉世界事件時，最多送入 LLM 的訊息數。深夜非同步執行，可放心調大。",
        "type": "int",
        "min": 50,
        "max": 2000,
        "category": "深夜記憶整合",
    },
    "WORLD_ARCHIVE_TRIGGER_WEEKDAY": {
        "label": "世界記憶週歸檔日（星期幾深夜）",
        "description": "0=週一 … 6=週日。到了指定星期幾的深夜，會把「整個週」的世界事件歸檔成 Markdown 永久存於 ./data/memory/world。",
        "type": "int",
        "min": 0,
        "max": 6,
        "category": "深夜記憶整合",
    },
    "WORLD_ARCHIVE_KEEP_WEEKS": {
        "label": "週歸檔保留週數",
        "description": "超過此週數的週檔會自動清理（月歸檔為永久庫不清理）。設 0 表示全都刪，僅保留月歸檔。",
        "type": "int",
        "min": 0,
        "max": 156,
        "category": "深夜記憶整合",
    },
    # === 雪莉人設與心跳 ===
    "CURRENT_SOUL_NAME": {
        "label": "當前活躍人設檔案 (Active Soul)",
        "description": "系統自動掃描 file/soul/*_default.md，選擇 Bot 的核心角色人設靈魂檔案。",
        "type": "select",
        "category": "雪莉人設與心跳",
    },
    "SOUL_CONTENT": {
        "label": "當前人設內容預覽與編輯 (Soul Content)",
        "description": "即時預覽與編輯當前選中之 *_default.md 檔案內容，保存時將直接更新該人設檔案。",
        "type": "str",
        "category": "雪莉人設與心跳",
    },
    "BOT_NAME": {
        "label": "雪莉稱呼名稱 (Bot Name)",
        "description": "橘雪莉對外顯示與對話中被稱呼的名字。",
        "type": "str",
        "category": "雪莉人設與心跳",
    },
    "DEFAULT_HEARTBEAT_CHANNEL_ID": {
        "label": "預設心跳主動發言頻道 ID",
        "description": "當雪莉主動觸發心跳聊天或日常閒聊時，預設發布的 Discord 頻道 ID。",
        "type": "int",
        "category": "雪莉人設與心跳",
    },
    "HEARTBEAT_CHECK_INTERVAL_MINUTES": {
        "label": "心跳巡邏檢查間隔（分鐘）",
        "description": "系統多久檢查一次是否該在頻道中主動發起問候或閒聊。",
        "type": "int",
        "min": 5,
        "max": 1440,
        "category": "雪莉人設與心跳",
    },
    "HEARTBEAT_CONDITION_START_HOUR": {
        "label": "心跳活躍起始時間（時）",
        "description": "每天幾點開始允許橘雪莉主動在頻道裡說話（0-23）。",
        "type": "int",
        "min": 0,
        "max": 23,
        "category": "雪莉人設與心跳",
    },
    "HEARTBEAT_CONDITION_END_HOUR": {
        "label": "心跳活躍結束時間（時）",
        "description": "每天幾點後停止主動在頻道發言，進入夜晚休息（0-23）。",
        "type": "int",
        "min": 0,
        "max": 23,
        "category": "雪莉人設與心跳",
    },
    "HEARTBEAT_CONDITION_MIN_GAP_SECONDS": {
        "label": "心跳兩次主動發言最短間隔（秒）",
        "description": "兩次主動閒聊之間的最短間隔，預設 14400 秒（4 小時），避免洗版。",
        "type": "int",
        "min": 600,
        "max": 86400,
        "category": "雪莉人設與心跳",
    },
    # === 語音辨識與合成 (TTS/STT) ===
    "VOICE_STT_MODEL": {
        "label": "語音辨識 STT 本地模型路徑",
        "description": "本機 Nemotron-3.5 語音轉文字模型的存放資料夾路徑。",
        "type": "str",
        "category": "語音辨識與合成",
    },
    "VOICE_STT_REPO_ID": {
        "label": "STT 於 HuggingFace Repo ID",
        "description": "Nemotron 語音辨識於 Hugging Face 的模型名稱。",
        "type": "str",
        "category": "語音辨識與合成",
    },
    "VOICE_STT_LANGUAGE": {
        "label": "語音辨識目標語言",
        "description": "STT 辨識預設語言代碼，例如 'zh' 代表中文。",
        "type": "str",
        "category": "語音辨識與合成",
    },
    "VOICE_STT_DEVICE": {
        "label": "STT 運算設備",
        "description": "執行語音辨識的設備（'cpu' 或 'cuda'）。",
        "type": "str",
        "category": "語音辨識與合成",
    },
    "VOICE_STT_COMPUTE_TYPE": {
        "label": "STT 運算精度",
        "description": "模型推論使用的浮點數精度（如 'float32', 'float16'）。",
        "type": "str",
        "category": "語音辨識與合成",
    },
    "VOICE_TTS_MODEL_NAME": {
        "label": "語音合成 TTS 模型名稱",
        "description": "發音合成模型，預設採用 XTTS v2 進行多語情感合成。",
        "type": "str",
        "category": "語音辨識與合成",
    },
    "VOICE_TTS_LANGUAGE": {
        "label": "TTS 語音發音語言",
        "description": "語音合成目標發音語言（例如 'zh-cn'）。",
        "type": "str",
        "category": "語音辨識與合成",
    },
    "VOICE_TTS_SPEAKER_WAV": {
        "label": "TTS 參考音訊樣本檔路徑",
        "description": "用於聲音音色選用之樣本檔案（如 shiry_voice_sample.wav）。",
        "type": "str",
        "category": "語音辨識與合成",
    },
    "VOICE_VAD_MODEL_NAME": {
        "label": "VAD 靜音檢測模型名稱",
        "description": "用於判斷使用者說話起止的 VAD 引擎名稱（如 silero_vad）。",
        "type": "str",
        "category": "語音辨識與合成",
    },
    "VOICE_VAD_THRESHOLD": {
        "label": "VAD 靜音檢測靈敏度",
        "description": "判定是否為人聲的聲音強度閥值（0.1–1.0），越高越不易誤判噪音。",
        "type": "float",
        "min": 0.1,
        "max": 1.0,
        "step": 0.05,
        "category": "語音辨識與合成",
    },
    "VOICE_VAD_MIN_SILENCE_MS": {
        "label": "判定說話結束之靜音毫秒數",
        "description": "持續靜音超過幾毫秒後，認定使用者講話完畢並開始回復。",
        "type": "int",
        "min": 100,
        "max": 3000,
        "category": "語音辨識與合成",
    },
    "VOICE_MAX_RECORD_SECONDS": {
        "label": "單次語音最長錄製秒數",
        "description": "每次錄製使用者語音的最長秒數限制，防呆與防濫用。",
        "type": "int",
        "min": 5,
        "max": 120,
        "category": "語音辨識與合成",
    },
    "VOICE_SILENCE_TIMEOUT_SECONDS": {
        "label": "語音對話靜音逾時秒數",
        "description": "當通話中持續沉靜多久（秒）判定為發話間隔結尾。",
        "type": "float",
        "min": 0.5,
        "max": 10.0,
        "step": 0.5,
        "category": "語音辨識與合成",
    },
    "FFMPEG": {
        "label": "FFmpeg 執行檔路徑",
        "description": "系統中的 FFmpeg 執行檔位址，用於音訊解碼與轉檔。",
        "type": "str",
        "category": "語音辨識與合成",
    },
    # === 音樂播放與隊列 ===
    "MUSIC_FFMPEG_OPTIONS": {
        "label": "音樂 FFmpeg 轉碼參數",
        "description": "播放 YouTube 或音訊串流時傳遞給 FFmpeg 的音量與標準化參數。",
        "type": "str",
        "category": "音樂播放與隊列",
    },
    "MUSIC_DEFAULT_VOLUME": {
        "label": "音樂預設播放音量倍率",
        "description": "傳送至 Discord 語音頻道時的 PCM 數位音量增益倍率（預設 1.2 倍）。",
        "type": "float",
        "min": 0.1,
        "max": 3.0,
        "step": 0.1,
        "category": "音樂播放與隊列",
    },
    "MUSIC_FFMPEG_BEFORE_OPTIONS": {
        "label": "音樂 FFmpeg 串流重連參數",
        "description": "確保網路波動時串流可自動重連的 FFmpeg 參數。",
        "type": "str",
        "category": "音樂播放與隊列",
    },
    "MUSIC_PLAYLIST_MAX_ITEMS": {
        "label": "單次匯入歌單最大首數",
        "description": "使用 YouTube 播放清單時一次最多允許載入的歌曲數量。",
        "type": "int",
        "min": 5,
        "max": 200,
        "category": "音樂播放與隊列",
    },
    "MUSIC_QUEUE_VIEW_TIMEOUT": {
        "label": "音樂介面按鈕逾時（秒）",
        "description": "音樂點歌介面按鈕（上一首/下一首/暫停）過多長秒數後自動停用。",
        "type": "int",
        "min": 30,
        "max": 600,
        "category": "音樂播放與隊列",
    },
    # === 圖片生成與圖庫 API ===
    "POLLINATIONS_API_URL": {
        "label": "Pollinations 圖片 API 網址",
        "description": "使用 Pollinations 進行免費二次元/寫實圖片生成時的請求端點。",
        "type": "str",
        "category": "圖片生成",
    },
    "POLLINATIONS_TIMEOUT_SECONDS": {
        "label": "Pollinations 生成逾時秒數",
        "description": "等待 Pollinations 返回圖片的最長等待時間。",
        "type": "int",
        "min": 10,
        "max": 120,
        "category": "圖片生成",
    },
    "GEMINI_IMAGE_TIMEOUT_SECONDS": {
        "label": "Gemini 生圖逾時秒數",
        "description": "呼叫 Google Gemini 圖片生成模型的最大逾時限制。",
        "type": "int",
        "min": 10,
        "max": 120,
        "category": "圖片生成",
    },
    "HF_IMAGE_MODEL": {
        "label": "HuggingFace 繪圖模型 ID",
        "description": "若啟用 HF API 繪圖，採用之模型名稱（如 animagine-xl-3.1）。",
        "type": "str",
        "category": "圖片生成",
    },
    "HF_IMAGE_TIMEOUT_SECONDS": {
        "label": "HuggingFace 生圖逾時秒數",
        "description": "調用 HF API 繪圖等待的最大限制時間。",
        "type": "int",
        "min": 10,
        "max": 300,
        "category": "圖片生成",
    },
    "SD_DEFAULT_SAMPLER": {
        "label": "SD 預設採樣器 (Sampler)",
        "description": "Stable Diffusion WebUI 預設採樣器名稱（例如 DPM++ 2M Karras）。",
        "type": "str",
        "category": "圖片生成",
    },
    "SD_NEGATIVE_PROMPT_BASE": {
        "label": "SD 預設負面提示詞",
        "description": "生成圖片時自動帶入的負面過濾提示，防崩壞與低畫質。",
        "type": "str",
        "category": "圖片生成",
    },
    "SD_TIMEOUT_SECONDS": {
        "label": "SD WebUI 生圖逾時（秒）",
        "description": "本機 Stable Diffusion WebUI 最多允許生成的秒數。",
        "type": "int",
        "min": 30,
        "max": 300,
        "category": "圖片生成",
    },
    "DANBOORU_API_URL": {
        "label": "Danbooru 圖庫 API 網址",
        "description": "找圖功能使用的 Danbooru 標籤檢索端點。",
        "type": "str",
        "category": "二次元圖庫 API",
    },
    "SAFEBOORU_API_URL": {
        "label": "Safebooru API 檢索網址",
        "description": "安全全齡向動漫圖庫 Safebooru 的 XML/JSON 查詢 API 網址。",
        "type": "str",
        "category": "二次元圖庫 API",
    },
    "SAFEBOORU_IMAGE_BASE_URL": {
        "label": "Safebooru 圖片來源 URL",
        "description": "Safebooru 下載與展示圖片的基底 URL。",
        "type": "str",
        "category": "二次元圖庫 API",
    },
    "SAFEBOORU_HTTP_TIMEOUT": {
        "label": "Safebooru 請求逾時（秒）",
        "description": "抓取 Safebooru 圖片時的 HTTP 連線逾時限制。",
        "type": "int",
        "min": 5,
        "max": 60,
        "category": "二次元圖庫 API",
    },
    "PIXIV_TAG_MIN_POST_COUNT": {
        "label": "Pixiv 標籤最低作品門檻",
        "description": "在 Pixiv 找圖時，標籤必須具有的基本作品數，確保圖片熱度品質。",
        "type": "int",
        "min": 10,
        "max": 10000,
        "category": "二次元圖庫 API",
    },
    "PIXIV_TAG_MAX_PAGES": {
        "label": "Pixiv 搜尋最大翻頁數",
        "description": "在 Pixiv 搜尋標籤作品時最多允許翻看的頁數。",
        "type": "int",
        "min": 1,
        "max": 100,
        "category": "二次元圖庫 API",
    },
    "PIXIV_TAG_REQUEST_DELAY": {
        "label": "Pixiv API 請求間隔（秒）",
        "description": "連續抓取 Pixiv 時的防護等待秒數，避免被伺服器限流。",
        "type": "float",
        "min": 0.1,
        "max": 5.0,
        "step": 0.1,
        "category": "二次元圖庫 API",
    },
    # === 外部資訊檢索 (Web/天氣/股市) ===
    "DUCKDUCKGO_SEARCH_URL": {
        "label": "DuckDuckGo 搜尋網址端點",
        "description": "雪莉進行即時時事與情報檢索所呼叫的搜尋網址。",
        "type": "str",
        "category": "外部資訊檢索",
    },
    "OPENWEATHERMAP_API_URL": {
        "label": "OpenWeatherMap 天氣 API",
        "description": "詢問各地區氣象預報所呼叫之 OpenWeatherMap 網址。",
        "type": "str",
        "category": "外部資訊檢索",
    },
    "WEATHER_EXTRACT_TIMEOUT": {
        "label": "天氣資料分析逾時（秒）",
        "description": "處理天氣 API 回傳資訊的最大逾時時間。",
        "type": "int",
        "min": 5,
        "max": 60,
        "category": "外部資訊檢索",
    },
    "WEATHER_REPLY_TIMEOUT": {
        "label": "天氣對話生成逾時（秒）",
        "description": "雪莉根據氣象預報產生對話的推論逾時秒數。",
        "type": "int",
        "min": 10,
        "max": 120,
        "category": "外部資訊檢索",
    },
    "STOCK_EXTRACT_TIMEOUT": {
        "label": "股市報價解析逾時（秒）",
        "description": "抓取與解析台股、美股行情資訊的逾時限制。",
        "type": "int",
        "min": 5,
        "max": 60,
        "category": "外部資訊檢索",
    },
    "STOCK_REPLY_TIMEOUT": {
        "label": "股市分析回覆逾時（秒）",
        "description": "雪莉產生股價走勢與名偵探評語對話的推論限制。",
        "type": "int",
        "min": 10,
        "max": 120,
        "category": "外部資訊檢索",
    },
    "YOUTUBE_SEARCH_API_URL": {
        "label": "YouTube 搜尋 API URL",
        "description": "Google YouTube v3 搜尋 API 端點。",
        "type": "str",
        "category": "YouTube 爬蟲",
    },
    "YOUTUBE_RSS_FEED_URL": {
        "label": "YouTube RSS 訂閱端點 URL",
        "description": "透過 RSS 快訊檢查特定頻道最新發片的 XML 基底網址。",
        "type": "str",
        "category": "YouTube 爬蟲",
    },
    "YOUTUBE_RSS_NS": {
        "label": "YouTube RSS Atom 命名空間",
        "description": "解析 YouTube Feed 用的 XML Atom Namespace。",
        "type": "str",
        "category": "YouTube 爬蟲",
    },
    "YOUTUBE_EXPLORER_TIMEOUT": {
        "label": "YouTube 爬蟲執行逾時（秒）",
        "description": "執行單次 YouTube 最新上傳檢查的最大限制秒數。",
        "type": "int",
        "min": 5,
        "max": 120,
        "category": "YouTube 爬蟲",
    },
    # === 任務佇列優先級 ===
    "PRIORITY_TAG_RESPONSE": {
        "label": "對話回覆優先權 (Priority)",
        "description": "真人點名與主動聊天的佇列優先序（數值越小越優先）。預設 1（最高）。",
        "type": "int",
        "min": 1,
        "max": 100,
        "category": "任務佇列優先級",
    },
    "PRIORITY_INTERJECT": {
        "label": "主動插話優先權 (Priority)",
        "description": "雪莉插話推論在 BotBrain 中的優先等級。預設 10。",
        "type": "int",
        "min": 1,
        "max": 100,
        "category": "任務佇列優先級",
    },
    "PRIORITY_HEARTBEAT": {
        "label": "日常心跳聊天優先權",
        "description": "主動巡邏聊天推論任務優先等第。預設 20。",
        "type": "int",
        "min": 1,
        "max": 100,
        "category": "任務佇列優先級",
    },
    "PRIORITY_VIBE_UPDATE": {
        "label": "話題氛圍萃取優先權",
        "description": "背景萃取聊天室話題與氣氛推論優先等第。預設 50。",
        "type": "int",
        "min": 1,
        "max": 100,
        "category": "任務佇列優先級",
    },
    "PRIORITY_DRAW": {
        "label": "二次元生圖與修圖優先權",
        "description": "繪圖與圖片描述推論優先級別。預設 80。",
        "type": "int",
        "min": 1,
        "max": 100,
        "category": "任務佇列優先級",
    },
    "PRIORITY_WEB_EXPLORER": {
        "label": "閒置網路爬蟲優先權",
        "description": "後台網路時事爬蟲推論任務優先等第。預設 99（最低）。",
        "type": "int",
        "min": 1,
        "max": 100,
        "category": "任務佇列優先級",
    },
    # === 系統與網路進階參數 ===
    "BOT_BRAIN_MAX_RETRIES": {
        "label": "BotBrain LLM 重試次數",
        "description": "如果模型推論發生異常或空訊，自動重試的最多次數。",
        "type": "int",
        "min": 1,
        "max": 10,
        "category": "系統與網路",
    },
    "BOT_BRAIN_RETRY_DELAY": {
        "label": "BotBrain 重試等候秒數",
        "description": "每次失敗後等待幾秒鐘再重新發起推論。",
        "type": "float",
        "min": 1,
        "max": 30,
        "step": 0.5,
        "category": "系統與網路",
    },
    "DEFAULT_HTTP_TIMEOUT": {
        "label": "預設對外 HTTP 請求逾時",
        "description": "發起對外網路 API 呼叫（如搜尋、抓圖）的預設秒數門檻。",
        "type": "int",
        "min": 3,
        "max": 60,
        "category": "系統與網路",
    },
    "DEFAULT_USER_AGENT": {
        "label": "對外爬蟲請求 User-Agent",
        "description": "爬取網頁與 API 時所宣告之瀏覽器/機器人識別字串。",
        "type": "str",
        "category": "系統與網路",
    },
    "DISCORD_UI_VIEW_TIMEOUT": {
        "label": "Discord 互動選單按鈕逾時",
        "description": "發布至 Discord 的按鈕與選單最長可維持可點擊狀態的時間（秒）。",
        "type": "int",
        "min": 30,
        "max": 1800,
        "category": "系統與網路",
    },
    "DRAW_RAW_TIMEOUT": {
        "label": "原圖描述生成逾時（秒）",
        "description": "將自然對話文字轉換為 SD 或繪圖提示詞推論的時間上限。",
        "type": "int",
        "min": 10,
        "max": 120,
        "category": "系統與網路",
    },
    "DRAW_EXPAND_TIMEOUT": {
        "label": "繪圖提示詞擴充逾時（秒）",
        "description": "擴充美術畫風細節提示詞的推理最長等候時間。",
        "type": "int",
        "min": 5,
        "max": 60,
        "category": "系統與網路",
    },
    "INTERJECT_SCORE_TIMEOUT": {
        "label": "插話評分推論逾時（秒）",
        "description": "判斷當前話題是否適合雪莉插話的推論逾時限制。",
        "type": "int",
        "min": 5,
        "max": 60,
        "category": "系統與網路",
    },
    "INTERJECT_REPLY_TIMEOUT": {
        "label": "插話發文生成逾時（秒）",
        "description": "判定為適合插話後，實際產生對話的最高逾時秒數。",
        "type": "int",
        "min": 10,
        "max": 120,
        "category": "系統與網路",
    },
    "SCHEDULE_PARSE_TIMEOUT": {
        "label": "自然語言排程解析逾時",
        "description": "把對話語句解讀為定時提醒或 Cron 任務的推理時間上限。",
        "type": "int",
        "min": 5,
        "max": 60,
        "category": "系統與網路",
    },
    "VISION_ANALYSIS_TIMEOUT": {
        "label": "圖片視覺識別推論逾時",
        "description": "把使用者上傳的圖片傳給 Vision 模型分析看懂的時間上限（建議 60~180 秒）。",
        "type": "int",
        "min": 10,
        "max": 600,
        "category": "系統與網路",
    },
    "PARALLEL_STEP_TIMEOUT": {
        "label": "Parallel 規劃器推論逾時",
        "description": "Parallel Agent 規劃模型單次任務拆解與意圖推論的最長等待秒數（建議 30~120 秒）。",
        "type": "int",
        "min": 10,
        "max": 300,
        "category": "系統與網路",
    },
    "HEALTH_CHECK_TIMEOUT": {
        "label": "健康檢查心跳逾時（秒）",
        "description": "監控後端 Ollama 與 Discord 狀態的回應逾時判定秒數。",
        "type": "int",
        "min": 2,
        "max": 30,
        "category": "健康監控",
    },
    "HEALTH_ALERT_CHANNEL_ID": {
        "label": "系統警報 Discord 頻道 ID",
        "description": "當系統狀態異常或模型離線時，推播警報通知的頻道 ID（設為 0 代表停用頻道推播）。",
        "type": "int",
        "category": "健康監控",
    },
    "HEALTH_ALERT_USER_IDS": {
        "label": "系統警報 Discord 私訊 User ID 清單",
        "description": "當系統發生 ERROR 錯誤日誌或模型離線時，額外以 Discord 私訊推播警報的使用者 ID 列表（ADMIN_USER_IDS 管理員會自動包含在內）。",
        "type": "list[int]",
        "category": "健康監控",
    },
    "RATE_LIMIT_REPLY_MESSAGE": {
        "label": "高頻速率限制提示訊息",
        "description": "對手速太快或短時間頻繁送請求的使用者推播的溫馨冷卻文字。",
        "type": "str",
        "category": "速率限制",
    },
    "WEB_UI_HOST": {
        "label": "Web UI 監聽 Host IP",
        "description": "網頁後端監聽之網路介面地址，預設 0.0.0.0 代表允許所有 IP 訪問。",
        "type": "str",
        "category": "系統與網路",
    },
    "WEB_UI_PORT": {
        "label": "Web UI 伺服器連接埠",
        "description": "網頁控制台開啟的連接埠（Port），預設為 8763。",
        "type": "int",
        "min": 1024,
        "max": 65535,
        "category": "系統與網路",
    },
    "WEB_UI_HTTPS": {
        "label": "Web UI 啟用 HTTPS 加密",
        "description": "是否啟用 SSL/TLS 證書提供加密 https:// 連線，強烈建議 ON。",
        "type": "bool",
        "category": "系統與網路",
    },
    "WEB_UI_HTTP_TIMEOUT": {
        "label": "Web UI 內部呼叫 API 逾時",
        "description": "網頁伺服器請求 Ollama 與 LM Studio 模型清單的最大等待秒數。",
        "type": "int",
        "min": 2,
        "max": 30,
        "category": "系統與網路",
    },
    "WEB_UI_RADMIN_IP": {
        "label": "Radmin VPN 主機推薦 IP",
        "description": "供介面上提示或快速連線參考的 Radmin VPN IP 位址。",
        "type": "str",
        "category": "系統與網路",
    },
    "WEB_UI_MAX_LOGIN_ATTEMPTS": {
        "label": "Web UI 登入失敗次數上限",
        "description": "同一 IP 於監控區間內允許的登入失敗次數，超過即暫時封鎖。",
        "type": "int",
        "min": 1,
        "max": 50,
        "category": "系統與網路",
    },
    "WEB_UI_LOCKOUT_MINUTES": {
        "label": "Web UI 封鎖持續分鐘數",
        "description": "觸發登入失敗上限後，禁止該 IP 嘗試登入的分鐘數。",
        "type": "int",
        "min": 1,
        "max": 1440,
        "category": "系統與網路",
    },
    # === ReAct 推論與思考 ===
    "REACT_MAX_STEPS": {
        "label": "ReAct 最大推理步數",
        "description": "單次 ReAct 推理允許的最大「思考 → 行動」循環次數，越高越深入但越耗時。",
        "type": "int",
        "min": 1,
        "max": 30,
        "category": "Chain-of-Thought",
    },
    "REACT_MAX_OBSERVATION_LENGTH": {
        "label": "ReAct 單筆觀察最大長度",
        "description": "單一工具觀察結果寫入推理上下文前的最大字元數，避免上下文爆量。",
        "type": "int",
        "min": 100,
        "max": 10000,
        "step": 100,
        "category": "Chain-of-Thought",
    },
    "REACT_MAX_TRANSCRIPT_CHARS": {
        "label": "ReAct 推理紀錄最大字元數",
        "description": "整段 ReAct 推理歷程允許保留的最大字元數，超過將被截斷。",
        "type": "int",
        "min": 500,
        "max": 50000,
        "step": 500,
        "category": "Chain-of-Thought",
    },
    "LIVE_THINKING_EDIT": {
        "label": "即時思考串流編輯",
        "description": "啟用後以 message.edit 即時更新思考過程，提供更流暢的推理可視化。",
        "type": "bool",
        "category": "Chain-of-Thought",
    },
    # === 記憶與上下文 ===
    "CONTEXT_COMPRESS_CACHE_TTL": {
        "label": "上下文壓縮快取存活秒數",
        "description": "已壓縮摘要的快取有效時間（秒），避免同一對話重複觸發壓縮。",
        "type": "float",
        "min": 0,
        "max": 3600,
        "step": 10,
        "category": "記憶與上下文",
    },
    "WORLD_MEMORY_SNIPPET_LIMIT": {
        "label": "世界記憶注入片段數上限",
        "description": "每次生成時最多注入的世界觀記憶片段數量。",
        "type": "int",
        "min": 0,
        "max": 50,
        "category": "記憶與上下文",
    },
    "NIGHTLY_DIGEST_MINUTE": {
        "label": "深夜記憶整合觸發分鐘",
        "description": "每日深夜記憶整理排程於每小時的第幾分鐘執行（0-59）。",
        "type": "int",
        "min": 0,
        "max": 59,
        "category": "深夜記憶整合",
    },
    # === 清單型設定 ===
    "ADMIN_ROLE_IDS": {
        "label": "管理員身分組 ID",
        "description": "具備管理員權限的 Discord 身分組 ID 清單，可逐項新增／移除。",
        "type": "list",
        "item_type": "int",
        "category": "系統與網路",
    },
    "ADMIN_USER_IDS": {
        "label": "管理員使用者 ID",
        "description": "具備管理員權限的 Discord 使用者 ID 清單，可逐項新增／移除。",
        "type": "list",
        "item_type": "int",
        "category": "系統與網路",
    },
    "NSFW_ALLOWED_CHANNEL_IDS": {
        "label": "NSFW 允許頻道 ID",
        "description": "允許觸發 NSFW 內容的頻道 ID 清單。",
        "type": "list",
        "item_type": "int",
        "category": "系統與網路",
    },
    "EXPLORER_TARGET_CHANNEL_IDS": {
        "label": "外部探索目標頻道 ID",
        "description": "允許主動探索與推送資訊的頻道 ID 清單。",
        "type": "list",
        "item_type": "int",
        "category": "外部資訊檢索",
    },
    "WEB_UI_TRUSTED_PROXIES": {
        "label": "Web UI 信任代理清單",
        "description": "信任的 Reverse Proxy 來源位址清單，用於正確解析 X-Forwarded-For。",
        "type": "list",
        "item_type": "str",
        "category": "系統與網路",
    },
    "SPARK_DEFAULT_COOLDOWN_SECONDS": {
        "label": "Spark 推播觸發冷卻 (秒)",
        "description": "同一個 Spark 觸發器的預設冷卻時間，避免短時間內重複推播。",
        "type": "int",
        "category": "模組與排程",
        "restart_required": True,
        "min": 0,
    },
    "SPARK_CHECK_INTERVAL_MINUTES": {
        "label": "Spark 巡檢間隔 (分鐘)",
        "description": "Spark 背景巡檢迴圈輪詢觸發器的頻率。",
        "type": "int",
        "category": "模組與排程",
        "restart_required": True,
        "min": 1,
    },
    "DEEP_SEARCH_DEFAULT_ROUNDS": {
        "label": "深度研究預設輪數",
        "description": "深度研究一次預設執行的多輪鑽研輪數。",
        "type": "int",
        "category": "外部資訊檢索",
        "restart_required": True,
        "min": 1,
    },
    "DEEP_SEARCH_MIN_ROUNDS": {
        "label": "深度研究最少輪數",
        "description": "深度研究允許的最少研究輪數下限。",
        "type": "int",
        "category": "外部資訊檢索",
        "restart_required": True,
        "min": 1,
    },
    "DEEP_SEARCH_MAX_ROUNDS": {
        "label": "深度研究最多輪數",
        "description": "深度研究可執行的最大輪數上限，保護 Token 與時間成本。",
        "type": "int",
        "category": "外部資訊檢索",
        "restart_required": True,
        "min": 1,
    },
    "DEEP_SEARCH_QUERIES_PER_ROUND": {
        "label": "每輪檢索關鍵字數",
        "description": "深度研究每一輪產生的檢索關鍵字數量。",
        "type": "int",
        "category": "外部資訊檢索",
        "restart_required": True,
        "min": 1,
    },
    "DEEP_SEARCH_MAX_RESULTS_PER_QUERY": {
        "label": "每關鍵字候選結果數",
        "description": "深度研究每個關鍵字取得的候選結果數。",
        "type": "int",
        "category": "外部資訊檢索",
        "restart_required": True,
        "min": 1,
    },
    "DEEP_SEARCH_MAX_PAGES_PER_ROUND": {
        "label": "每輪深入抓取頁數上限",
        "description": "深度研究每一輪實際深入抓取內文的網頁上限。",
        "type": "int",
        "category": "外部資訊檢索",
        "restart_required": True,
        "min": 1,
    },
    "DEEP_SEARCH_MAX_CHARS_PER_PAGE": {
        "label": "單頁擷取字元上限",
        "description": "深度研究單一網頁擷取的最大字元數，適配地端 SLM context。",
        "type": "int",
        "category": "外部資訊檢索",
        "restart_required": True,
        "min": 100,
    },
    "DND_MAX_HISTORY_ROUNDS": {
        "label": "D&D 歷史回合數",
        "description": "D&D 世界引擎每個頻道保留的對話歷史回合數 (玩家行動 + DM 回覆)。",
        "type": "int",
        "category": "模組與排程",
        "restart_required": True,
        "min": 1,
    },
    "DRAW_TOOL_MAX_PROMPT_LENGTH": {
        "label": "畫圖工具 Prompt 長度上限",
        "description": "Pollinations 畫圖工具會截斷的 prompt 字數上限。",
        "type": "int",
        "category": "圖片生成",
        "min": 50,
    },
    "LOREBOOK_MAX_GENERAL_ENTRIES": {
        "label": "世界書一般條目上限",
        "description": "每次組裝上下文時納入的一般世界書條目數量上限。",
        "type": "int",
        "category": "記憶與上下文",
        "min": 0,
    },
    "PAINTER_MAX_PAINTING_TIMEOUT_SEC": {
        "label": "繪圖整體逾時 (秒)",
        "description": "Painter 單次繪圖的總時間上限 (硬性 2 分鐘保護)。",
        "type": "float",
        "category": "圖片生成",
        "restart_required": True,
        "min": 30,
        "step": 5,
    },
    "STOCK_TRADER_INITIAL_CAPITAL_USD": {
        "label": "虛擬交易初始資金 (美元)",
        "description": "美股虛擬量化交易模組的初始帳戶資金。",
        "type": "float",
        "category": "模組與排程",
        "restart_required": True,
        "min": 0,
        "step": 100,
    },
    "STOCK_TRADER_FEE_RATE": {
        "label": "虛擬交易手續費率",
        "description": "美股虛擬量化交易模組的每筆買賣手續費率。",
        "type": "float",
        "category": "模組與排程",
        "restart_required": True,
        "min": 0,
        "step": 0.0001,
    },
    "STOCK_TRADER_MIN_SHARES": {
        "label": "虛擬交易最少股數",
        "description": "美股虛擬量化交易模組允許的最少交易股數。",
        "type": "float",
        "category": "模組與排程",
        "restart_required": True,
        "min": 0,
        "step": 0.01,
    },
}


RESTART_REQUIRED_KEYS: frozenset[str] = frozenset({
    "WEB_UI_HOST",
    "WEB_UI_PORT",
    "WEB_UI_HTTPS",
    "WEB_UI_TRUSTED_PROXIES",
})


def _infer_list_item_type(values: list[Any], key: str, meta: dict[str, Any]) -> str:
    """推斷清單型設定的元素型別，供前端編輯器正確轉型。"""
    declared = meta.get("item_type")
    if declared:
        return str(declared)
    if values:
        sample = values[0]
        if isinstance(sample, bool):
            return "bool"
        if isinstance(sample, int):
            return "int"
        if isinstance(sample, float):
            return "float"
        return "str"
    if "_IDS" in key:
        return "int"
    return "str"


def get_editable_settings(source_dict: dict[str, Any] | None = None) -> dict[str, Any]:
    """回傳所有可編輯設定，含 meta 資訊（label、description、type、category 等）。

    Args:
        source_dict: 可選的設定變數來源字典，預設從 settings 模組讀取。

    Returns:
        dict: key -> {value, label, description, type, category, ...constraints}
    """
    if source_dict is None:
        import settings
        source_dict = vars(settings)

    editable: dict[str, Any] = {}
    _SENSITIVE_KEYWORDS = ("PASSWORD", "PATH", "DIR", "KEY", "TOKEN", "SECRET")
    _NON_SENSITIVE_WHITELIST = {
        "LLM_MAX_OUTPUT_TOKENS",
        "LLM_MAX_CONTEXT_TOKENS",
        "CONTEXT_COMPRESS_TOKEN_THRESHOLD",
    }
    _EXCLUDED_KEYS = ("LLM_MODEL_NAME", "CORE_PERSONA")
    _SCALAR_TYPES = (int, float, str, bool)

    for k, v in source_dict.items():
        if not k.isupper() or k.startswith("_") or k in _EXCLUDED_KEYS:
            continue
        if k not in _NON_SENSITIVE_WHITELIST and any(kw in k for kw in _SENSITIVE_KEYWORDS):
            continue

        meta = SETTINGS_META.get(k, {})
        item_type: str | None = None

        if type(v) in _SCALAR_TYPES:
            entry_type = meta.get("type", type(v).__name__)
            entry_value: Any = v
        elif isinstance(v, list) and all(type(e) in _SCALAR_TYPES for e in v):
            entry_type = meta.get("type", "list")
            entry_value = list(v)
            item_type = _infer_list_item_type(v, k, meta)
        else:
            continue

        entry: dict[str, Any] = {
            "value": entry_value,
            "label": meta.get("label", k),
            "description": meta.get("description", ""),
            "type": entry_type,
            "category": meta.get("category", "其他"),
            "restart_required": bool(meta.get("restart_required", k in RESTART_REQUIRED_KEYS)),
        }
        if item_type is not None:
            entry["item_type"] = item_type
        for constraint in ("min", "max", "step", "options"):
            if constraint in meta:
                entry[constraint] = meta[constraint]

        if k == "CURRENT_SOUL_NAME":
            import settings as _s
            if hasattr(_s, "get_available_soul_names"):
                entry["options"] = _s.get_available_soul_names()
            else:
                entry["options"] = [str(v)] if v else ["manosaba_sherry_default.md"]

        if k == "SOUL_CONTENT":
            import settings as _s
            target_file = getattr(
                _s, "DEFAULT_SOUL_PATH", getattr(_s, "SOUL_DIR", Path("file/soul")) / "manosaba_sherry_default.md"
            )
            if not target_file.exists() and not target_file.name.startswith("manosaba_"):
                fallback_target = getattr(_s, "SOUL_DIR", Path("file/soul")) / f"manosaba_{target_file.name}"
                if fallback_target.exists():
                    target_file = fallback_target
            if target_file.exists():
                try:
                    entry["value"] = target_file.read_text(encoding="utf-8")
                except Exception:
                    entry["value"] = ""
            else:
                entry["value"] = ""

        editable[k] = entry
    return editable
