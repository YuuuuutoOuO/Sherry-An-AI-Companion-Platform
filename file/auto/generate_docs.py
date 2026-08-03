"""
generate_docs.py
================
從 ast_result.json 生成所有 Docs/ 下的 Markdown 文件

執行方式（由 update_docs.bat 呼叫，或手動執行）：
    python file/auto/generate_docs.py
"""
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# ─── 路徑設定 ─────────────────────────────────────────────────────────────────
SCRIPT_DIR   = Path(__file__).parent          # .../file/auto/
PROJECT_ROOT = SCRIPT_DIR.parent.parent       # .../專案根目錄/
JSON_PATH    = SCRIPT_DIR / "ast_result.json"
OUTPUT_DIR   = PROJECT_ROOT / "Docs"

NOW = datetime.now().strftime("%Y-%m-%d %H:%M")

# 子模組的排列順序與對應 emoji
SUBMODULE_ORDER = [
    "bot_brain", "social", "memory", "vibe_manager",
    "music", "voice", "admin", "scheduler", "executor",
    "web_explorer",
]
EMOJI_MAP = {
    "bot_brain":   "🧠", "social":      "💬", "memory":     "🧬",
    "vibe_manager":"🌊", "music":        "🎵", "voice":      "🎤",
    "admin":       "🛠️", "scheduler":   "⏰", "executor":  "🔫",
    "web_explorer":"🌐",
}


# ─── 輔助函式 ─────────────────────────────────────────────────────────────────

def sig_str(fn: dict) -> str:
    """組裝函式簽名字串"""
    prefix = "async " if fn.get("is_async") else ""
    params = []
    for p in fn.get("params", []):
        s = p["name"]
        if "type" in p:
            s += f": {p['type']}"
        if "default" in p:
            s += f" = {p['default']}"
        params.append(s)
    ret = f" -> {fn['return_type']}" if fn.get("return_type") else ""
    return f"{prefix}def {fn['name']}({', '.join(params)}){ret}"


def decorators_str(fn: dict) -> str:
    decos = fn.get("decorators", [])
    if not decos:
        return ""
    return "\n".join(f"@{d}" for d in decos) + "\n"


def write_fn_section(lines: list, fn: dict, indent: int = 4):
    """將一個 function/method 寫成 Markdown 段落"""
    sig  = sig_str(fn)
    deco = decorators_str(fn)
    h    = "#" * indent
    lines.append(f"{h} `{fn['name']}`  (L{fn['line']})\n")
    lines.append(f"```python\n{deco}{sig}\n```\n")
    if fn.get("docstring"):
        lines.append(f"> {fn['docstring'].replace(chr(10), '  \\n> ')}\n")
    if fn.get("params"):
        lines.append("| 參數 | 型別 | 預設值 |\n|---|---|---|")
        for p in fn["params"]:
            t = p.get("type", "—")
            d = p.get("default", "—")
            lines.append(f"| `{p['name']}` | `{t}` | `{d}` |")
        lines.append("")


def write_file_section(lines: list, file_data: dict, level: int = 2):
    """將一個 .py 檔案的資料寫成 Markdown 區塊"""
    path = file_data["path"]
    h    = "#" * level
    lines.append(f"\n{h} 📄 `{path}`\n")

    if file_data.get("error"):
        lines.append(f"> ⚠️ **語法錯誤**：{file_data['error']}\n")
        return

    if file_data.get("module_docstring"):
        lines.append(f"> {file_data['module_docstring'].splitlines()[0]}\n")

    # 全域變數
    gvars = file_data.get("global_vars", [])
    if gvars:
        lines.append(f"\n{'#' * (level+1)} 全域變數 / 模組常數\n")
        lines.append("| 名稱 | 型別 | 值（摘要） | 行號 |")
        lines.append("|---|---|---|---|")
        for v in gvars:
            t   = v.get("type", "—")
            val = str(v.get("value_repr") or "—")
            if len(val) > 80:
                val = val[:80] + "..."
            val = val.replace("|", "\\|").replace("\n", " ")
            lines.append(f"| `{v['name']}` | `{t}` | `{val}` | L{v['line']} |")
        lines.append("")

    # Classes
    for cls in file_data.get("classes", []):
        bases = ", ".join(cls.get("bases", [])) or "object"
        lines.append(f"\n{'#' * (level+1)} Class `{cls['name']}` ← `{bases}`  (L{cls['line']})\n")
        if cls.get("docstring"):
            lines.append(f"> {cls['docstring']}\n")
        if cls.get("class_vars"):
            lines.append(f"{'#' * (level+2)} 類別變數\n")
            lines.append("| 名稱 | 值（摘要） |")
            lines.append("|---|---|")
            for v in cls["class_vars"]:
                val = str(v.get("value_repr") or "—")[:80].replace("|", "\\|")
                lines.append(f"| `{v['name']}` | `{val}` |")
            lines.append("")
        if cls.get("methods"):
            lines.append(f"{'#' * (level+2)} Methods\n")
            for m in cls["methods"]:
                write_fn_section(lines, m, indent=level+3)

    # Module-level functions
    if file_data.get("functions"):
        lines.append(f"\n{'#' * (level+1)} Functions\n")
        for fn in file_data["functions"]:
            write_fn_section(lines, fn, indent=level+2)

    lines.append("\n---\n")


# ─── 各文件生成 ───────────────────────────────────────────────────────────────

def gen_00_document_structure():
    content = "\n".join([
        "# 📚 Docs — 技術文件目錄",
        f"\n> 最後更新：{NOW}\n",
        "本資料夾為專案技術文件的統一存放位置，所有文件均由 `file/auto/` 中的腳本自動生成，並可手動補充說明。\n",
        "> **命名規範**：小寫資料夾 = 程式碼；大寫開頭資料夾 = 技術文件\n",
        "## 📁 文件清單\n",
        "| 檔案名稱 | 說明 |",
        "|---|---|",
        "| [00_Document_Structure.md](./00_Document_Structure.md) | **本文件**。說明 Docs/ 的架構與各文件用途。 |",
        "| [01_Project_Snapshot.md](./01_Project_Snapshot.md) | 專案高層次總覽：目標、技術棧、架構圖、資料流、核心設計原則、插件系統說明。 |",
        "| [02_TODO_and_Improvements.md](./02_TODO_and_Improvements.md) | 整合後的待辦事項、改進方向與已完成功能紀錄。 |",
        "| [module_root.md](./module_root.md) | 根目錄 `.py`（`main.py`, `settings.py`, `build_tag_dict.py`）的詳細 function 與變數紀錄。 |",
        "| [module_file.md](./module_file.md) | `file/` 目錄（`AI_personality.py` 等）的詳細變數紀錄。 |",
        "| [module_handlers.md](./module_handlers.md) | `handlers/` 所有子模組的詳細 class、function、參數紀錄。 |",
        "",
        "## 🔄 更新方式\n",
        "程式碼有變動時，在專案根目錄執行以下 .bat 即可自動重新生成所有文件：\n",
        "```",
        "update_docs.bat",
        "```\n",
        "或手動分步執行：",
        "```bash",
        "python file/auto/scan_ast.py",
        "python file/auto/generate_docs.py",
        "```\n",
    ])
    (OUTPUT_DIR / "00_Document_Structure.md").write_text(content, encoding="utf-8")
    print("[OK] 00_Document_Structure.md")


def gen_01_project_snapshot():
    content = (
        "# 🎯 專案總覽 (Project Snapshot)\n\n"
        f"> 最後更新：{NOW}\n\n"
        "---\n\n"
        "## 專案基本資訊\n\n"
        "- **專案名稱**：橘雪莉 (Shiry) — 泛用型超擬真 Discord AI Agent\n"
        "- **核心目標**：打造一個具備高度擴展性、獨立靈魂記憶與特規功能（社群記憶、音樂點唱、防封鎖搜尋、解限搜圖、語音交談、圖片生成、排程提醒）的二次元虛擬智慧實況夥伴。\n\n"
        "## 技術棧 (Tech Stack)\n\n"
        "| 類別 | 技術 |\n|---|---|\n"
        "| 語言 | Python 3.10+（`from __future__ import annotations` 後向兼容） |\n"
        "| Discord 框架 | `discord.py` (v2.4+)，全採 Cog 模組化，Slash commands & Events |\n"
        "| LLM 推理 | `Ollama` Local Inference API（`/api/chat` 非同步 POST） |\n"
        "| STT | `faster-whisper` large-v3（CUDA float16, zh） |\n"
        "| TTS | `TTS` XTTS-v2（speaker_wav 克隆音色） |\n"
        "| 圖片生成 | Stable Diffusion WebUI API（`/sdapi/v1/txt2img`） |\n"
        "| HTTP | `aiohttp`（非同步）、`trafilatura`（高效能網文解析） |\n"
        "| 音樂 | `yt-dlp` + `FFmpeg`（LoudNorm -14 LUFS 串流） |\n\n"
        "## 模型配置\n\n"
        "| 用途 | 模型名稱 |\n|---|---|\n"
        "| `PRIMARY_MODEL` / `NOMORAL_MODEL` | `krith/mistral-nemo-instruct-2407-abliterated:IQ4_XS` |\n"
        "| `QUICK_MODEL` / `CLASSIFIER_MODEL` / `VIBE_MODEL` | `mannix/llama3.1-8b-abliterated` |\n"
        "| LLM 輸入上限 | `num_ctx = 16384` |\n"
        "| LLM 輸出上限 | `num_predict = 512` |\n\n"
        "## 設計模式\n\n"
        "- **模組化 Agent 架構 (Tool-Router Architecture)**：所有功能封裝為 `BaseTool` 子類別\n"
        "- **兩階漏斗路由 (Hybrid Regex + LLM Intent Routing)**：Phase 1 正則極速命中 → Phase 2 LLM 動態派任\n"
        "- **狀態機互斥鎖 (State Mutex)**：保護 16GB VRAM（`_sd_generating` / `_voice_mode_active`）\n\n"
        "## 核心資料流\n\n"
        "```text\n"
        "用戶文字 @Tag\n"
        "  → RateLimiter（5次/30秒防洗頻）\n"
        "  → fetch_context_script（最近30則 + 1800秒斷層截斷）\n"
        "  → MemoryManager.get_user_memory_snippet（UID長期屬性注入）\n"
        "  → VibeManager.get_top_topics_snippet（頻道熱門話題注入）\n"
        "  → parse_compound_command（LLM拆解複合指令，priority=0）\n"
        "  → 對每個子句：\n"
        "      Phase 1: Tool.can_handle_by_regex() 正則極速命中\n"
        "      Phase 2: CLASSIFIER_MODEL 動態派任\n"
        "  → Tool.execute() → persona.py build_dynamic_persona()\n"
        "  → BotBrain.enqueue_task()（asyncio.PriorityQueue）\n"
        "  → _inference_worker → callback → Discord 回覆\n"
        "```\n\n"
        "## Tool Registry 優先順序\n\n"
        "```text\n"
        "URLSummaryTool → MusicTool → DrawTool → ImageSearchTool → AdvancedSearchTool → ChatTool\n"
        "```\n\n"
        "## VRAM 互斥法則\n\n"
        "| 狀態旗標 | 影響 |\n|---|---|\n"
        "| `_voice_mode_active = True` | `DrawTool.execute()` 拒絕執行 |\n"
        "| `_sd_generating = True` | `_inference_worker` 暫停所有 LLM 推理 |\n\n"
        "## 三層 Prompt 架構\n\n"
        "| 層次 | 來源 | 內容 |\n|---|---|---|\n"
        "| A（靈魂底核） | `file/AI_personality.py` → `settings.CORE_PERSONA` | 常駐角色人設 + 防幻覺鐵則 |\n"
        "| B（情境帽） | `social/persona.py` | intent 對應的行為模式 + LOREBOOK Regex 觸發 |\n"
        "| C（執行規定） | `tools/*.py` | 字數限制、回覆格式、RAG 防幻覺鐵則 |\n\n"
        "---\n\n"
        "> 🔗 詳細的各模組 function 與參數紀錄請參閱：\n"
        "> - [module_root.md](./module_root.md)\n"
        "> - [module_file.md](./module_file.md)\n"
        "> - [module_handlers.md](./module_handlers.md)\n"
    )
    (OUTPUT_DIR / "01_Project_Snapshot.md").write_text(content, encoding="utf-8")
    print("[OK] 01_Project_Snapshot.md")


def gen_02_todo():
    content = (
        "# 📋 TODO & 改進事項 (Unified Task Board)\n\n"
        f"> 最後更新：{NOW}  |  整合自：`待改進事項.txt` + `預計事項.txt`\n\n"
        "---\n\n"
        "## ✅ 已完成功能\n\n"
        "- [x] 插件化 Tool Registry（URLSummaryTool / MusicTool / DrawTool / ImageSearchTool / AdvancedSearchTool / ChatTool）\n"
        "- [x] 複合指令解析器（Compound Parser）：自動拆解多動作指令並依序執行\n"
        "- [x] NSFW 話題分流（`ChatTool.can_handle_by_regex()` 直接短路）\n"
        "- [x] Rate Limiter 防刷 與 Ollama 健康監控\n"
        "- [x] SD 圖片生成（DrawTool）與 VRAM 狀態互斥鎖\n"
        "- [x] 語音交談系統（STT + TTS + VAD + Recorder，已可加入/離開並載入模型）\n"
        "- [x] 管理員介面（AdminCog）：6 個 Slash 指令\n"
        "- [x] 長期記憶（MemoryManager）：UID 綁定屬性萃取 + Python 端物理消毒\n"
        "- [x] 動態人格系統（persona.py）：三層 Prompt + LOREBOOK 觸發\n"
        "- [x] **人設定義獨立化**：`CORE_PERSONA` / `LOREBOOK` 已拆分至 `file/AI_personality.py`\n"
        "- [x] 排程提醒系統（Scheduler）：支援 once / cron，動態提醒文案生成\n"
        "- [x] 強制意圖執行指令（ExecutorCog）：`/execute` 繞過分類器\n\n"
        "---\n\n"
        "## 🔴 高優先 — 已知 Bug / 待完成項目\n\n"
        "- [ ] **VoiceCog `_listen_loop` TODO**：STT 辨識結果尚未串接 `social.router`，語音交談完整閉環尚未完成\n"
        "- [ ] **WebExplorer 停用**：目前在 `main.py` 中被注解（`# \"handlers.web_explorer\"`），尚未重啟\n\n"
        "---\n\n"
        "## ⚠️ 困難事項\n\n"
        "- [ ] **使雪莉能讀取影像**：可能需要使用 Claude API，VRAM 可能導致 BUG\n\n"
        "---\n\n"
        "## 🚀 第一優先：核心互動體驗補完\n\n"
        "- [ ] **語音交談閉環完成**：`_listen_loop` STT 結果串接 `social.router` → TTS 回話\n"
        "- [ ] **`/help` 全功能指令面板**：Discord Select Menu 分類式幫助（聊天/音樂/畫圖/搜圖/搜尋/語音/排程）\n"
        "- [ ] **互動按鈕元件化（Discord UI Components）**：\n"
        "  - 音樂播放器：⏯️ 暫停、⏭️ 下一首、🔀 隨機、🔁 循環\n"
        "  - 搜圖結果：🔄「再來一張」、⛔「不喜歡」\n"
        "  - 畫圖結果：🔄「重畫」、📝「修改提示詞」\n"
        "  - 長期記憶詢問：✅「沒錯」、❌「記錯了」反饋修正\n\n"
        "## 🟡 第二優先：社交互動深化\n\n"
        "- [ ] **Reaction 快速互動**：🔄 重新生成、🗑️ 刪除（僅提問者）、📌 釘選、❤️ 提升好感度\n"
        "- [ ] **引用回覆上下文感知**：Reply 引用某則訊息並 @雪莉 時，擷取被引用訊息作為額外上下文\n"
        "- [ ] **伺服器加入歡迎系統**：新成員加入時自動發送含人設語氣的歡迎訊息\n\n"
        "## 🟢 第三優先：質感與便利性\n\n"
        "- [ ] **Discord Presence 動態狀態**：根據當前活動切換顯示（🎵 聽音樂 / 🎨 畫圖 / 🎤 語音 / 💤 閒置）\n"
        "- [ ] **Ephemeral 隱藏訊息**：管理員指令改用 `ephemeral=True`；長操作先 defer\n"
        "- [ ] **Thread 討論串支援**：高熱度話題自動建立 Thread；Thread 中 @雪莉 能正常回應\n"
        "- [ ] **圖片辨識（多模態）**：傳圖 + @雪莉 → LLaVA 量化版辨識後回覆\n\n"
        "## ⚙️ 第四優先：運維與開發體驗\n\n"
        "- [ ] **除錯日誌頻道**：`LOG_CHANNEL_ID` 設定，ThinkingLog 分 INFO / WARNING / ERROR；ERROR 自動推播至管理員\n"
        "- [ ] **Per-Guild 伺服器端設定面板**：自訂指令前綴、功能開關、插話頻率、NSFW 白名單、暱稱\n"
        "- [ ] **統計儀表板 `/stats`**：今日互動次數、各 Tool 使用分布、平均回應延遲、佇列深度、VRAM 使用率\n\n"
        "---\n\n"
        "## 📌 穩定版本 Git Commit\n\n"
        "```\n"
        "80121356a6c1b8e85ff092767d4397090a7b2ac6\n"
        "```\n"
    )
    (OUTPUT_DIR / "02_TODO_and_Improvements.md").write_text(content, encoding="utf-8")
    print("[OK] 02_TODO_and_Improvements.md")


def gen_module_docs(data: list):
    # 分類
    root_files    = [f for f in data if "/" not in f["path"] and not f["path"].startswith("file/")]
    file_files    = [f for f in data if f["path"].startswith("file/")]
    handler_files = [f for f in data if f["path"].startswith("handlers/")]

    def submodule(path: str) -> str:
        parts = path.split("/")
        return parts[1] if len(parts) >= 2 else "root"

    handler_groups = defaultdict(list)
    for f in handler_files:
        handler_groups[submodule(f["path"])].append(f)

    # module_root.md
    lines = [
        "# 🏠 module_root — 根目錄模組文件",
        f"\n> 最後更新：{NOW}  |  自動生成自 AST 掃描\n",
        "涵蓋根目錄的所有 `.py` 檔案：`main.py`, `settings.py`, `build_tag_dict.py`\n",
        "---\n",
    ]
    for f in root_files:
        write_file_section(lines, f, level=2)
    (OUTPUT_DIR / "module_root.md").write_text("\n".join(lines), encoding="utf-8")
    print("[OK] module_root.md")

    # module_file.md
    lines = [
        "# 📁 module_file — `file/` 目錄模組文件",
        f"\n> 最後更新：{NOW}  |  自動生成自 AST 掃描\n",
        "涵蓋 `file/` 目錄：`AI_personality.py`（雪莉人格定義）\n",
        "---\n",
    ]
    for f in file_files:
        write_file_section(lines, f, level=2)
    (OUTPUT_DIR / "module_file.md").write_text("\n".join(lines), encoding="utf-8")
    print("[OK] module_file.md")

    # module_handlers.md
    lines = [
        "# ⚙️ module_handlers — `handlers/` 所有子模組文件",
        f"\n> 最後更新：{NOW}  |  自動生成自 AST 掃描\n",
        "## 📑 子模組目錄\n",
    ]
    for sub in SUBMODULE_ORDER:
        if sub in handler_groups:
            lines.append(f"- [{sub}](#{sub})")
    lines.append("\n---\n")

    top_handlers = [f for f in handler_files if f["path"].count("/") == 1]
    if top_handlers:
        lines.append("## 📄 handlers/ 頂層檔案\n")
        for f in top_handlers:
            write_file_section(lines, f, level=3)

    for sub in SUBMODULE_ORDER:
        if sub not in handler_groups:
            continue
        emoji = EMOJI_MAP.get(sub, "📦")
        lines.append(f'\n<a id="{sub}"></a>\n')
        lines.append(f"## {emoji} `handlers/{sub}/`\n")
        for f in sorted(handler_groups[sub], key=lambda x: x["path"]):
            write_file_section(lines, f, level=3)

    (OUTPUT_DIR / "module_handlers.md").write_text("\n".join(lines), encoding="utf-8")
    print("[OK] module_handlers.md")


# ─── 主程式 ───────────────────────────────────────────────────────────────────

def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    if not JSON_PATH.exists():
        print(f"[ERROR] 找不到 {JSON_PATH}")
        print("        請先執行：python file/auto/scan_ast.py")
        raise SystemExit(1)

    data: list[dict] = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    print(f"[generate_docs] 載入 {len(data)} 個檔案的掃描結果")

    gen_00_document_structure()
    gen_01_project_snapshot()
    gen_02_todo()
    gen_module_docs(data)

    print(f"\n[generate_docs] 全部完成！輸出目錄：{OUTPUT_DIR}  (Docs/)")


if __name__ == "__main__":
    main()
