"""
tools/monitor_brain.py
======================
雪莉大腦思維、TAG 與指令即時終端監控器 (分頁 3)。

即時監聽 data/runtime_log.jsonl，提取使用者指令、解析 TAG、
LLM 思維鏈思考過程 (CoT)、產出回覆與推論耗時，並以彩色卡片呈現。

日誌檔超過 50MB 時會被主程式輪替 (rename)，本監控器會自動偵測並
重新追蹤新檔，不會因此永久停留在舊檔（解決「分頁假死」問題）。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# 自動確保專案根目錄在 Python 搜尋路徑中
BASE_DIR: Path = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# 監控共用邏輯（FileTailer / tail helpers）
from tools.common import FileTailer, should_exit, tail_bytes  # noqa: E402


# 確保輸出支援 Windows UTF-8 與 ANSI 色彩
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

if sys.platform == "win32":
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass
    os.system("title 🧠 雪莉大腦思維與對話動態")


# ANSI 色彩定義
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAGENTA = "\033[35m"
WHITE = "\033[37m"

LOG_PATH = BASE_DIR / "data" / "runtime_log.jsonl"

# 啟動時讀取的歷史檔尾部上限（避免整份大檔解析）
_HISTORY_MAX_BYTES = 131072


def _render_event_card(entry: dict[str, Any]) -> None:
    """以美化終端卡片輸出單一互動事件。

    Args:
        entry: 包含事件資訊的字典。
    """
    event = entry.get("event", "event")
    ts = entry.get("ts", "")
    tid = entry.get("trace_id", "")
    user = entry.get("user") or "Unknown"
    channel_id = entry.get("channel_id") or ""
    raw_input = entry.get("raw_input", "").strip()
    intent = entry.get("intent", "")
    tags = entry.get("tags_resolved") or []
    cot_thought = entry.get("cot_thought", "").strip()
    reply_preview = entry.get("reply_preview", "").strip()
    model = entry.get("model_used", "")
    latency_ms = entry.get("latency_ms", 0)
    queue_size = entry.get("queue_size", 0)
    extra = entry.get("extra", {})

    width = 76

    # ── 1. Discord 指令與元件操作卡片 ─────────────────────────────
    if event in ("command_exec", "component_interaction", "modal_submit"):
        cmd_type = extra.get("command_type", event).upper()
        cmd_name = extra.get("command_name", "")
        status = extra.get("status", "success")
        options = extra.get("options")
        status_color = GREEN if status == "success" else (YELLOW if status == "invoked" else RED)

        print(f"\n{CYAN}╭{'─' * (width - 2)}╮{RESET}")
        tid_str = f" | {DIM}[{tid}]{RESET}" if tid else ""
        print(
            f"{CYAN}│{RESET} {BOLD}{YELLOW}⚡ [{cmd_type}]{RESET} {DIM}{ts}{RESET}{tid_str} "
            f"👤 {BOLD}{WHITE}{user}{RESET} {DIM}#{channel_id}{RESET}"
        )
        if raw_input:
            print(f"{CYAN}│{RESET} {BOLD}⌨️  指令內容:{RESET} {WHITE}{raw_input}{RESET}")
        if options:
            print(f"{CYAN}│{RESET} {BOLD}⚙️  參數選項:{RESET} {CYAN}{options}{RESET}")
        print(
            f"{CYAN}│{RESET} 狀態: {status_color}{status}{RESET} | ⏱️  {DIM}耗時: {BOLD}{WHITE}{latency_ms:,}{RESET}{DIM} ms{RESET}"
        )
        print(f"{CYAN}╰{'─' * (width - 2)}╯{RESET}")
        return

    # ── 2. 工具獨立執行卡片 (Tool Execution) ──────────────────────
    if event == "tool_exec":
        tool_name = extra.get("tool_name") or intent.replace("TOOL: ", "")
        status = extra.get("status", "success")
        obs = extra.get("observation") or reply_preview
        status_color = GREEN if status == "success" else RED

        print(f"\n{YELLOW}╭{'─' * (width - 2)}╮{RESET}")
        tid_str = f" | {DIM}[{tid}]{RESET}" if tid else ""
        print(
            f"{YELLOW}│{RESET} {BOLD}{GREEN}🛠️  [TOOL: {tool_name}]{RESET} {DIM}{ts}{RESET}{tid_str} "
            f"👤 {DIM}{user}{RESET}"
        )
        if raw_input:
            print(f"{YELLOW}│{RESET} {BOLD}📥 傳入參數:{RESET} {WHITE}{raw_input}{RESET}")
        if obs:
            print(f"{YELLOW}│{RESET} {BOLD}📤 回傳數據 (Obs):{RESET} {DIM}{obs[:150]}{RESET}")
        print(
            f"{YELLOW}│{RESET} 狀態: {status_color}{status}{RESET} | ⏱️  {DIM}工具耗時: {BOLD}{WHITE}{latency_ms:,}{RESET}{DIM} ms{RESET}"
        )
        print(f"{YELLOW}╰{'─' * (width - 2)}╯{RESET}")
        return

    # ── 3. 大腦 LLM 推論卡片 ────────────────────────────────────
    if event == "inference_done":
        p_len = extra.get("prompt_length", 0)
        c_len = extra.get("completion_length", 0)
        q_lat = extra.get("queue_latency_ms", 0)

        print(f"\n{BLUE if 'BLUE' in globals() else CYAN}╭{'─' * (width - 2)}╮{RESET}")
        print(
            f"{CYAN}│{RESET} {BOLD}{MAGENTA}🧠 [LLM INFERENCE]{RESET} {DIM}{ts}{RESET} "
            f"模型: {CYAN}{model}{RESET}"
        )
        if raw_input:
            print(f"{CYAN}│{RESET} 📝 Prompt 摘要: {DIM}{raw_input[:100]}{RESET}")
        if reply_preview:
            print(f"{CYAN}│{RESET} ✨ 生成摘要: {WHITE}{reply_preview[:100]}{RESET}")
        print(
            f"{CYAN}│{RESET} ⏱️  {DIM}推論耗時: {BOLD}{WHITE}{latency_ms:,}{RESET}{DIM} ms (排隊: {q_lat} ms) | 字數: {p_len} in / {c_len} out{RESET}"
        )
        print(f"{CYAN}╰{'─' * (width - 2)}╯{RESET}")
        return

    # ── 4. 標準對話與綜合回覆卡片 ─────────────────────────────────
    # 忽略無實質內容的空事件
    if not raw_input and not reply_preview and not cot_thought and not tags:
        return

    print(f"\n{MAGENTA}╭{'─' * (width - 2)}╮{RESET}")
    tid_str = f" | {DIM}[{tid}]{RESET}" if tid else ""
    print(
        f"{MAGENTA}│{RESET} {BOLD}{CYAN}📥 [{event.upper()}]{RESET} {DIM}{ts}{RESET}{tid_str} "
        f"👤 {BOLD}{WHITE}{user}{RESET} {DIM}#{channel_id}{RESET}"
    )

    if raw_input:
        print(f"{MAGENTA}│{RESET} {BOLD}💬 輸入內容:{RESET} {WHITE}{raw_input}{RESET}")

    if intent or model:
        intent_str = f"{BOLD}{YELLOW}{intent}{RESET}" if intent else "N/A"
        model_str = f"{CYAN}{model}{RESET}" if model else "N/A"
        print(f"{MAGENTA}│{RESET} 🧠 意圖: {intent_str} | 模型: {model_str}")

    if tags:
        tags_str = ", ".join(f"#{t}" for t in tags)
        print(f"{MAGENTA}│{RESET} {BOLD}🏷️  TAG 解析:{RESET} {GREEN}{tags_str}{RESET}")

    if cot_thought:
        print(f"{MAGENTA}│{RESET} {BOLD}💭 思維鏈 (CoT):{RESET}")
        for line in cot_thought.splitlines():
            print(f"{MAGENTA}│{RESET}    {DIM}{CYAN}▶ {line}{RESET}")

    if reply_preview:
        print(f"{MAGENTA}│{RESET} {BOLD}✨ 雪莉回覆:{RESET}")
        for line in reply_preview.splitlines():
            print(f"{MAGENTA}│{RESET}    {GREEN}{line}{RESET}")

    print(
        f"{MAGENTA}│{RESET} ⏱️  {DIM}推論耗時: {BOLD}{WHITE}{latency_ms:,}{RESET}{DIM} ms | 佇列: {queue_size}{RESET}"
    )
    print(f"{MAGENTA}╰{'─' * (width - 2)}╯{RESET}")



def _parse_json_blocks(text: str) -> tuple[list[dict[str, Any]], str]:
    """解析以空行或 JSON 區塊分隔的日誌文字。

    Args:
        text: 待解析的日誌文字流。

    Returns:
        (成功解析的字典列表, 剩餘未完整的殘餘字串)
    """
    entries: list[dict[str, Any]] = []
    blocks = text.split("\n\n")
    remainder = blocks[-1] if text and not text.endswith("\n\n") else ""
    parseable_blocks = blocks[:-1] if (text and not text.endswith("\n\n")) else blocks

    for block in parseable_blocks:
        b = block.strip()
        if not b:
            continue
        try:
            data = json.loads(b)
            if isinstance(data, dict):
                entries.append(data)
        except json.JSONDecodeError:
            for line in b.splitlines():
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        data = json.loads(line)
                        if isinstance(data, dict):
                            entries.append(data)
                    except Exception:
                        pass

    return entries, remainder


def main() -> None:
    """即時監聽並輸出雪莉大腦與對話動態。"""
    print(f"{BOLD}{MAGENTA}{'=' * 76}{RESET}")
    print(f"{BOLD}{MAGENTA}🧠 雪莉大腦思維、TAG 與指令即時串流監控 (Tab 3){RESET}")
    print(f"{BOLD}{MAGENTA}{'=' * 76}{RESET}")
    print(f"{DIM}• 監聽目標：{LOG_PATH}{RESET}")
    print(f"{DIM}• 狀態：🟢 正在監聽大腦推斷與對話事件... (按 Ctrl+C 可停止){RESET}")
    print(f"{DIM}{'-' * 76}{RESET}")

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not LOG_PATH.exists():
        LOG_PATH.touch()

    # 讀取現有歷史紀錄中的最後幾筆（只讀檔尾，避免啟動時解析整份大檔）
    history_text = tail_bytes(LOG_PATH, _HISTORY_MAX_BYTES)
    if history_text:
        initial_entries, _ = _parse_json_blocks(history_text)
        if initial_entries:
            print(f"{DIM}--- 📜 最近歷史事件 ({min(3, len(initial_entries))} 筆) ---{RESET}")
            for entry in initial_entries[-3:]:
                _render_event_card(entry)

    # 進入即時監聽循環
    start_time = time.time()
    tailer = FileTailer(LOG_PATH)
    buffer = ""
    try:
        while True:
            if should_exit(start_time):
                break
            chunk = tailer.read()
            if tailer.detected_rotation:
                buffer = ""  # 檔被輪替，丟棄未完成的殘塊
            if chunk:
                buffer += chunk
                if "\n\n" in buffer:
                    entries, buffer = _parse_json_blocks(buffer)
                    for entry in entries:
                        _render_event_card(entry)
                sys.stdout.flush()
            else:
                time.sleep(tailer.poll_seconds)
    except KeyboardInterrupt:
        print(f"\n{YELLOW}大腦思維監控已停止。{RESET}")


if __name__ == "__main__":
    main()