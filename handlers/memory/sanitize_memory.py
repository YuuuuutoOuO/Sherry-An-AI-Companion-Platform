"""
handlers/memory/sanitize_memory.py
=====================================
舊記憶資料清洗與重建工具。

當記憶系統存在 Bug（如 Lost-Update、3B 小模型誤記）期間累積了污染資料，
可執行此腳本進行：

  1. 靜態清洗 (sanitize_existing)：
     清理現有 user_memory.json 內的無效 alias 與重複/格式異常的 fact，
     不依賴 LLM，可立即執行。

  2. 歷史重跑 (replay_past_chat_logs)：
     利用完全乾淨的 chat_logs（未受覆寫 Bug 影響），
     以修復後的寫入邏輯 + 高品質大模型重新萃取記憶。
     建議在確認程式碼 Bug 修復後再執行，產出乾淨的全新記憶庫。

執行方式（從專案根目錄）：
    python -m handlers.memory.sanitize_memory --mode sanitize
    python -m handlers.memory.sanitize_memory --mode replay --days 30
    python -m handlers.memory.sanitize_memory --mode all --days 30
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import settings
from handlers.memory.storage import (
    load_user_memory,
    save_user_memory,
    sanitize_aliases,
    sanitize_and_merge_facts,
    consolidate_all_user_memories,
    MAX_FACTS_PER_USER,
    UserMemoryStore,
)
from handlers.memory.chat_logger import read_chat_log

logger = logging.getLogger(__name__)

_TZ_TW = timezone(timedelta(hours=8))

# ──────────────────────────────────────────────────────────────
# 1. 靜態清洗：不依賴 LLM，直接過濾壞資料與模糊去重
# ──────────────────────────────────────────────────────────────

# 明顯屬於群聊互相打鬧/嘴砲句或主客體顛倒的幹話模式
_BANTER_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"吃大便|吃屎|去死|去吃屎|神經病|幹嘛啦|智障"),
]


def sanitize_existing(path: Path = settings.USER_MEMORY_PATH) -> dict[str, int]:
    """掃描並清洗現有 user_memory.json 與 SQLite 內的污染資料與重複事實。

    清洗項目：
    - Aliases：過濾超長/超短、含 @、含 [某群友]、命中黑名單或遊戲代號格式的別名。
    - Facts：移除文字為空、超長句子（> 70 字）、含 [某群友] 或 @ 的污染 fact、
             移除明顯的群聊互嘴幹話（如吃大便），並進行自體與全域語意模糊去重。

    Args:
        path: user_memory.json 的路徑。

    Returns:
        統計字典，含 users_cleaned、aliases_removed、facts_removed。
    """
    store: UserMemoryStore = load_user_memory(path)
    stats = consolidate_all_user_memories(store)
    save_user_memory(store, path)
    logger.info(
        "[清洗完成] 共 %d 位使用者受影響，移除 %d alias / %d facts",
        stats["users_cleaned"], stats["aliases_removed"], stats["facts_removed"],
    )
    return stats


# ──────────────────────────────────────────────────────────────
# 2. 歷史重跑：用大模型重新萃取 chat_logs 記憶
# ──────────────────────────────────────────────────────────────

_REPLAY_SYSTEM_PROMPT = (
    "【歷史記憶重建引擎 (Memory Rebuild AI)】\n"
    "這是一份來自群友「{display_name}」的過去完整聊天記錄，\n"
    "內容已通過清洗，請從中萃取關於「這位群友本人」的長期記憶。\n\n"
    "---任務指令---\n"
    "請從這份記錄中找出有意義且真實的人格特質、興趣愛好、生活習慣，\n"
    "以及他與雪莉（橘雪莉）的長期互動模式。\n\n"
    "⚠️【核心禁止事項（違規將導致嚴重人格錯亂）】：\n"
    "1. 【嚴格禁止主客體顛倒】：發話者對其他人說的話（例如叫別人吃大便、罵別人、開玩笑說『你只能...』、『你去死』），\n"
    "   是發話者對『其他人』的祈使或嘴砲句，絕對不可記為發話者本人的喜好或特質！\n"
    "2. 【嚴格過濾嘴砲與幹話】：群聊中的打鬧、互相嘲諷、無厘頭幹話或情緒發洩，請一律忽略，留下空陣列，寧缺勿濫！\n"
    "3. 【嚴格別名判定】：只有發話者明確自我介紹（例如：『我叫XX』、『可以叫我XX』）才記入 aliases。\n"
    "   絕對禁止將遊戲名稱（如賽爾號）、伺服器房號、動漫名、代號、泛稱（如群友、大家）或整句話記為 aliases！\n"
    "4. 發言紀錄中出現的「[某群友]」是發話者在訊息中 TAG 的其他人，絕對不是發話者本人！\n"
    "5. 分類規範（category）：\n"
    "   - identity: 真實身份/職業/居住地/個人自稱\n"
    "   - interest: 明確熱愛的遊戲/動漫/技術/興趣\n"
    "   - habit: 穩固生活作息/口頭禪\n"
    "   - relationship: 與雪莉（橘雪莉）的長期互動模式\n"
    "   - opinion: 對重要事物的穩定看法\n"
    "6. 每條 fact 必須附上 importance（1~5整數）與 category。\n"
    "7. 每條 fact 文字不超過 50 字，禁止記入完整句子。\n\n"
    "目前已知長期屬性（請勿重複，但可升級 importance）：\n"
    "{current_facts}\n\n"
    "請只返回 JSON 格式，不可有任何其他文字：\n"
    '```json\n'
    '{{"aliases": ["真實的綽號或自稱(若無則空陣列)"],\n'
    ' "facts": [{{"fact": "他平時喜歡寫Python", "importance": 3, "category": "interest"}}]}}\n'
    '```'
)

# 每位使用者最多送入的訊息數（避免 Prompt 過長）
_REPLAY_MAX_MSGS_PER_USER = getattr(settings, "NIGHTLY_MAX_MSGS_PER_USER", 80)
# 使用者至少要有多少條發言才值得重建
_REPLAY_MIN_MSGS_THRESHOLD = getattr(settings, "NIGHTLY_MIN_MSGS_THRESHOLD", 5)


async def replay_past_chat_logs(
    days: int = 30,
    memory_path: Path = settings.USER_MEMORY_PATH,
) -> dict[str, int]:
    """從過去 N 天的 chat_logs 重新萃取記憶，產出乾淨的新記憶庫。

    ⚠️ 此函式需要在已啟動的 Bot 環境中執行（依賴 BotBrain LLM 佇列），
    或單獨透過 Ollama HTTP API 直接呼叫。
    建議透過 Admin 指令 !rebuild_memory 觸發，而不是直接 CLI 呼叫。

    Args:
        days: 往回追溯幾天的 chat_logs。
        memory_path: 記憶庫路徑（預設 settings.USER_MEMORY_PATH）。

    Returns:
        統計字典，含 users_processed、facts_added、dates_processed。
    """
    from collections import defaultdict

    store = load_user_memory(memory_path)
    stats: dict[str, int] = {"users_processed": 0, "facts_added": 0, "dates_processed": 0}

    now_tw = datetime.now(_TZ_TW)
    by_user: dict[str, list[dict]] = defaultdict(list)
    display_names: dict[str, str] = {}

    # 收集過去 N 天的所有聊天紀錄
    for delta_days in range(1, days + 1):
        date_str = (now_tw - timedelta(days=delta_days)).strftime("%Y-%m-%d")
        records = read_chat_log(date_str)
        if not records:
            continue
        for rec in records:
            uid = rec.get("user_id", "")
            if not uid:
                continue
            by_user[uid].append(rec)
            display_names[uid] = rec.get("display_name", uid)
        stats["dates_processed"] += 1
        logger.info("[重建] 已讀取 %s 的聊天記錄（%d 條）", date_str, len(records))

    logger.info("[重建] 共 %d 個日期，%d 位使用者待處理", stats["dates_processed"], len(by_user))

    for uid, msgs in by_user.items():
        if len(msgs) < _REPLAY_MIN_MSGS_THRESHOLD:
            logger.debug("[重建] uid=%s 發言過少 (%d)，跳過", uid, len(msgs))
            continue

        display_name = display_names.get(uid, uid)
        capped = msgs[-_REPLAY_MAX_MSGS_PER_USER:]
        texts = "\n".join(
            f"[{r['ts'][11:16]}] {r['content']}"
            for r in capped
            if r.get("content")
        )

        current_profile = store.get(uid, {"aliases": [display_name], "facts": []})
        current_facts_text = "\n".join(
            f"- {f['fact']}" for f in current_profile.get("facts", [])
        ) or "（目前無已知記憶）"

        system_prompt = _REPLAY_SYSTEM_PROMPT.format(
            display_name=display_name,
            current_facts=current_facts_text,
        )

        # 呼叫 Ollama 直接 HTTP 請求（不依賴 BotBrain 佇列）
        parsed = await _call_llm_direct(system_prompt, texts, uid)
        if not parsed:
            continue

        raw_aliases = list(current_profile.get("aliases", [])) + list(parsed.get("aliases", []))
        clean_aliases = sanitize_aliases(raw_aliases)

        existing_facts = list(current_profile.get("facts", []))
        final_facts, new_count = sanitize_and_merge_facts(
            existing_facts, parsed.get("facts", []), MAX_FACTS_PER_USER,
        )

        store[uid] = {"aliases": clean_aliases, "facts": final_facts}
        stats["users_processed"] += 1
        stats["facts_added"] += new_count
        logger.info(
            "[重建] uid=%s (%s) 完成，新增 %d 條記憶",
            uid, display_name, new_count,
        )

    save_user_memory(store, memory_path)
    logger.info(
        "[重建完成] 共 %d 位使用者，新增 %d 條記憶",
        stats["users_processed"], stats["facts_added"],
    )
    return stats


async def _call_llm_direct(system_prompt: str, user_prompt: str, uid: str) -> dict | None:
    """直接透過 HTTP 呼叫 Ollama API（不依賴 BotBrain 佇列）。

    Args:
        system_prompt: 系統 Prompt。
        user_prompt: 使用者 Prompt（聊天紀錄文字）。
        uid: 使用者 ID（用於日誌）。

    Returns:
        解析後的 JSON dict，失敗時回傳 None。
    """
    import httpx

    # 從 PRIMARY_MODEL 設定解析出模型名稱
    raw_model = getattr(settings, "NIGHTLY_DIGEST_MODEL", settings.PRIMARY_MODEL)
    # lms:gemma-4-12b → gemma-4-12b；ollama:xxx → xxx
    if ":" in raw_model:
        model_name = raw_model.split(":", 1)[1]
        base = settings.OLLAMA_BASE_URL if raw_model.startswith("ollama") else settings.LMS_BASE_URL
    else:
        model_name = raw_model
        base = settings.OLLAMA_BASE_URL

    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
    }

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{base}/v1/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
            reply = data["choices"][0]["message"]["content"]

        raw = reply.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            raw = m.group(0)
        res = json.loads(raw)
        return res if isinstance(res, dict) else None
    except Exception as e:
        logger.warning("[重建] uid=%s LLM 呼叫失敗: %s", uid, e)
        return None


# ──────────────────────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────────────────────

def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def main() -> None:
    """CLI 入口，支援 sanitize / replay / all 三種模式。"""
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    _setup_logging()

    parser = argparse.ArgumentParser(description="雪莉記憶清洗與重建工具")
    parser.add_argument(
        "--mode",
        choices=["sanitize", "replay", "all"],
        default="sanitize",
        help="sanitize=靜態清洗，replay=歷史重跑，all=兩者都做",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="replay 模式往回追溯的天數（預設 30）",
    )
    args = parser.parse_args()

    logger.info("=" * 50)
    logger.info("橘雪莉記憶系統 - 舊資料清洗與重建工具")
    logger.info("=" * 50)

    if args.mode in ("sanitize", "all"):
        logger.info("[模式 1: 靜態記憶清洗]")
        if not settings.USER_MEMORY_PATH.exists():
            logger.info("尚未找到記憶庫檔案 %s (可能為首次部署)。無需進行靜態清洗。", settings.USER_MEMORY_PATH)
        else:
            logger.info("正在掃描並清洗 %s ...", settings.USER_MEMORY_PATH)
            sanitize_stats = sanitize_existing()
            logger.info("靜態清洗完成！統計數據: %s", sanitize_stats)

    if args.mode in ("replay", "all"):
        logger.info("[模式 2: 歷史對話記憶重跑 (最近 %d 天)]", args.days)
        chat_log_dir = settings.CHAT_LOG_DIR
        if not chat_log_dir.exists() or not list(chat_log_dir.glob("*.jsonl")):
            logger.info("尚未找到歷史對話日誌目錄 %s 或無 .jsonl 檔案。無需歷史重跑。", chat_log_dir)
        else:
            logger.info("正在讀取 %s 並透過大模型重新萃取...", chat_log_dir)
            replay_stats = asyncio.run(replay_past_chat_logs(days=args.days))
            logger.info("歷史對話重跑完成！統計數據: %s", replay_stats)

    logger.info("=" * 50)


if __name__ == "__main__":
    main()


