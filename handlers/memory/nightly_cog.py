"""
handlers/memory/nightly_cog.py
================================
深夜記憶整合 Cog。

每分鐘檢查一次台灣時間，當時間進入 NIGHTLY_DIGEST_HOUR:NIGHTLY_DIGEST_MINUTE 起的 10 分鐘視窗，
且昨日的 chat log 尚未整合，就啟動深度記憶整合流程：

  1. 讀取昨日 data/chat_logs/YYYY-MM-DD.jsonl
  2. 按 user_id 分組，過濾發言 < MIN_MSGS_THRESHOLD 的使用者
  3. 對每位使用者送入 PRIMARY_MODEL 做深度反射萃取
  4. 更新 user_memory.json（與即時反射相同邏輯，但品質更高）
  5. 建立 .processed 標記，並記錄整合統計

Admin 指令 !nightly_digest 可立即觸發（跳過時間限制），方便測試。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any

from discord.ext import commands, tasks

import settings
from handlers.memory.chat_logger import (
    get_yesterday_str, is_date_processed,
    mark_date_processed, read_chat_log,
)
from handlers.memory.chat_logger import (
    get_pending_world_retries,
    record_world_retry as _record_world_retry,
    resolve_world_retry,
)
from handlers.memory.storage import (
    load_user_memory, save_user_memory, UserFact,
    sanitize_aliases, sanitize_and_merge_facts,
    consolidate_all_user_memories,
    decay_memories, MAX_FACTS_PER_USER,
)
from handlers.bot_brain import InferenceTask

logger = logging.getLogger(__name__)

_TZ_TW = timezone(timedelta(hours=8))

# 每位使用者至少要有幾條發言才值得整合
_MIN_MSGS_THRESHOLD = getattr(settings, "NIGHTLY_MIN_MSGS_THRESHOLD", 5)

# 主視窗錯過後的補跑策略：最多嘗試幾次、每次間隔多久（秒）
_CATCHUP_MAX_ATTEMPTS = 3
_CATCHUP_RETRY_INTERVAL = 1800.0

# 世界記憶補跑：每輪最多處理幾個伺服器、單一伺服器最多重試幾次
_WORLD_RETRY_BATCH = 3
_WORLD_RETRY_MAX_ROUNDS = 3
_WORLD_RETRY_INTERVAL = 1800.0

_WORLD_SYSTEM_PROMPT = (
    "你是一個專業且洞察敏銳的社群世界記憶史官。請從這份某 Discord 伺服器一日聊天記錄中，"
    "提煉 3~6 條最具代表性、生動且值得被長期記住的世界記憶條目，涵蓋：\n"
    "① 全群重大集體事件、深度互動插曲或熱烈話題焦點；\n"
    "② 正在進行、未來可能延續的情境主軸（包含與橘雪莉/希羅的深度對話或特定角色扮演）；\n"
    "③ 群體社會關係與互動默契的演變。\n\n"
    "⚠️【撰寫規範（重要）】：\n"
    "1. 【嚴禁公文套話與抽象元描述】：絕對禁止使用『進行文字創作測試』、『進行日常對話』、『與特定角色互動』、『展開相關討論』等空洞公文套話！\n"
    "2. 【保留具體關鍵細節】：務必清楚寫出：誰與誰（包含 Bot 當時扮演的角色/人設）、具體情境與設定（如兄妹角色扮演、主僕互動、特定遊戲副本或裝備名稱）、事件的核心焦點或結論。\n"
    "3. 【篇幅與長度】：每條 40~90 字以內，文筆生動流暢，主謂分明，彼此主題不可重複。\n"
    "【格式要求】：繁體中文，請僅返回 JSON Array 格式，嚴禁任何其他說明文字：\n"
    '```json\n'
    '[\n'
    '  {"summary": "雪莉大小姐與雪莉（希羅）參與雙人劇本演出，細膩刻畫角色間的情感張力與默契", "importance": 4, "participants": ["雪莉大小姐", "橘雪莉"]},\n'
    '  {"summary": "歐潤吉與喵喵針對韓版楓之谷新轉移卷軸機制與裝備洗鍊收益深入探討攻略細節", "importance": 3, "participants": ["歐潤吉", "喵喵"]},\n'
    '  {"summary": "群友們針對《絕區零》新角色養成機制與抽卡規劃展開熱烈交流與經驗分享", "importance": 3, "participants": ["喵喵", "咕咕嘎嘎"]}\n'
    ']\n'
    '```'
)


def _build_world_user_prompt(date_str: str, sampled: list[dict[str, Any]]) -> str:
    """組裝世界記憶提煉的 user prompt（與補跑流程共用）。"""
    summary_text = "\n".join(
        f"[{r.get('display_name', '群友')}]: {r.get('content', '')}" for r in sampled
    )
    return f"以下是伺服器在 {date_str} 的對話記錄（共 {len(sampled)} 則）：\n\n{summary_text}"


def _parse_world_events(raw_reply: str) -> list[dict[str, Any]] | None:
    """解析世界記憶回應為 JSON Array；無法解析或非 Array 時回傳 None。"""
    raw = raw_reply.strip()
    if not raw:
        return None
    raw = raw.lstrip("```json").lstrip("```").rstrip("```").strip()
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if m:
        raw = m.group(0)
    parsed = json.loads(raw)
    if isinstance(parsed, list):
        return parsed
    return None


def _remember_care_topics(uid: str, date_str: str, parsed: dict[str, Any]) -> None:
    """把深夜整合產出的 care_topics 寫入關懷引擎佇列（失敗不影響整合）。"""
    topics = parsed.get("care_topics")
    if not isinstance(topics, list) or not topics:
        return
    try:
        from handlers.care import get_care_engine

        added = get_care_engine().remember_nightly_topics(
            uid, topics[:2], source_ref=date_str
        )
        if added:
            logger.info("NightlyDigest: uid=%s 新增 %d 條主動關懷線索", uid, added)
    except Exception as exc:
        logger.debug("NightlyDigest: 寫入 care_topics 失敗 uid=%s: %s", uid, exc)


# 深度反射的 System Prompt（給大模型的提示）
_NIGHTLY_SYSTEM_PROMPT = (
    "【深夜靈魂整合引擎 (Nightly Reflection AI)】\n"
    "這是一份來自群友「{display_name}」今日完整的聊天記錄，"
    "內容遠比平時的片段更完整。\n\n"
    "---任務指令---\n"
    "請從這份完整記錄中，萃取關於「這位群友本人」的長期記憶。\n"
    "請找出有意義且真實的人格特質、興趣愛好、生活習慣，"
    "以及他與雪莉（橘雪莉）的長期互動模式。\n\n"
    "⚠️【核心禁止事項（違規將導致嚴重人格錯亂）】：\n"
    "1. 【嚴格禁止主客體顛倒】：發話者對其他人說的話（例如叫別人吃大便、罵別人、開玩笑說『你只能...』、『你去死』），"
    "是發話者對『其他人』的祈使或嘴砲句，絕對不可記為發話者本人的喜好或特質！\n"
    "2. 【嚴格過濾嘴砲與幹話】：群聊中的打鬧、互相嘲諷、無厘頭幹話或情緒發洩，請一律忽略，留下空陣列，寧缺勿濫！\n"
    "3. 【禁止公文套話與抽離元描述】：嚴禁使用『進行文字創作測試』、『進行日常對話』、『與特定角色互動』等抽象公文詞彙，必須精確記錄具體角色、情境設定與話題焦點！\n"
    "4. 【嚴格別名判定】：只有發話者明確自我介紹（例如：『我叫XX』、『可以叫我XX』）才記入 aliases。"
    "絕對禁止將遊戲名稱（如賽爾號）、伺服器房號、動漫名、代號、泛稱（如群友、大家）或整句話記為 aliases！\n"
    "5. 發言紀錄中出現的「[某群友]」是發話者在訊息中 TAG 的其他人，絕對不是發話者本人！\n"
    "6. 分類規範（category）：\n"
    "   - identity: 真實身份/職業/居住地/個人自稱\n"
    "   - preference: 明確熱愛的遊戲/動漫/技術/食物，以及特定角色扮演情境偏好（如兄妹/主僕 RP）、性癖或情色互動喜好\n"
    "   - habit: 穩固生活作息/口頭禪/反覆出現的行為\n"
    "   - social: 與其他人（群友、家人、朋友）的關係，或他正在進行的長期項目\n"
    "   - rule: 【嚴格限定】他對雪莉（橘雪莉）的明確溝通硬規則（如『叫我OO』『說話要直接』『禁止某稱呼』『不准變大叔機器人』）。注意：個人性癖、情境愛好、技術開發熱情或角色扮演偏好一律歸入 preference，絕對不是 rule！\n"
    "7. 每條 fact 必須附上 importance（1~5整數）：\n"
    "   1=短暫狀態  2=一般偏好  3=穩固習慣  4=重要特質  5=核心身份或最高規則\n"
    "8. 【既有記憶重構與合併同類項原則（極度重要）】：\n"
    "   請對照底下『目前已知長期屬性』與今日對話：\n"
    "   ① 若既有屬性中已有多條描述相同主題（如多條提及博士學位、多條股票代碼清單、多條人格穩定/大叔機器人約束、多條語意分割/架構研究），務必主動融合成單一精練條目，完整保留所有具體專有名詞（如股票代號、遊戲名稱），嚴禁生成重複破碎的多條同類事實！\n"
    "   ② 禁止在 preference 事實句尾重複黏貼『並要求其人格穩定』等寄生贅句，保持各條特質主題單一、語意精確。\n\n"
    "9. 【主動關懷線索】：另請在 care_topics 提供 0~2 條「下次適合主動關心此人的具體話題」，"
    "必須來自今日對話的具體事件（如考試、加班、遊戲進度），不可杜撰或使用抽象套話；若無則空陣列。\n\n"
    "目前已知長期屬性（請檢視並與今日新知重組合併）：\n"
    "{current_facts}\n\n"
    "請只返回 JSON 格式，不可有任何其他文字：\n"
    '```json\n'
    '{{"aliases": ["真實的綽號或自稱(若無則空陣列)"],\n'
    ' "facts": [\n'
    '   {{"fact": "在角色扮演情境中偏好兄妹設定與沉浸式互動", "importance": 4, "category": "preference"}},\n'
    '   {{"fact": "要求雪莉對話時必須直接稱呼他為主人", "importance": 5, "category": "rule"}},\n'
    '   {{"fact": "偏好純愛互動並強烈排斥 NTR 題材", "importance": 4, "category": "preference"}},\n'
    '   {{"fact": "正在深入玩韓版楓之谷並專注於裝備洗鍊", "importance": 3, "category": "preference"}}\n'
    ' ],\n'
    ' "care_topics": ["明天要報告的專題進度", "最近加班到很晚，可以問問有沒有好好休息"]}}\n'
    '```'
)


class NightlyDigest(commands.Cog):
    """凌晨 3:00 深夜記憶整合工人。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._is_running = False  # 防止同時觸發多次
        self._catchup_attempts: dict[str, list[float]] = {}
        self._world_retry_attempts: dict[str, int] = {}
        self._world_retry_last_run: float = 0.0

    async def cog_load(self) -> None:
        self._nightly_loop.start()
        logger.info("NightlyDigest: 深夜整合工人已上線（每分鐘巡邏一次）")

    async def cog_unload(self) -> None:
        self._nightly_loop.cancel()
        logger.info("NightlyDigest: 深夜整合工人已下線")

    # -----------------------------------------------------------------------
    # 背景巡邏迴圈
    # -----------------------------------------------------------------------
    @tasks.loop(minutes=1)
    async def _nightly_loop(self) -> None:
        yesterday = get_yesterday_str()
        if is_date_processed(yesterday):
            self._catchup_attempts.pop(yesterday, None)
            # 日期已完成不代表世界記憶也完成，需獨立補跑失敗的伺服器。
            if not self._is_running:
                await self._retry_world_memory()
            return

        if not self._should_attempt(yesterday):
            return

        logger.info("NightlyDigest: 偵測到 %s 尚未整合，啟動整合流程。", yesterday)
        stats = await self._run_digest(yesterday)
        if stats and stats.get("status") == "completed":
            self._catchup_attempts.pop(yesterday, None)

    def _should_attempt(self, date_str: str) -> bool:
        """主視窗內直接嘗試；主視窗外則依補跑次數與間隔節流。"""
        now = datetime.now(_TZ_TW)
        target_hour = int(getattr(settings, "NIGHTLY_DIGEST_HOUR", 4))
        target_minute = getattr(settings, "NIGHTLY_DIGEST_MINUTE", 30)
        in_window = now.hour == target_hour and target_minute <= now.minute < target_minute + 10
        if in_window:
            return True

        # 主視窗尚未到達：跨日後的「昨日」資料仍在累積中，提前整合會漏掉整天的後半段。
        if now.hour < target_hour:
            return False

        attempts = self._catchup_attempts.setdefault(date_str, [])
        if len(attempts) >= _CATCHUP_MAX_ATTEMPTS:
            return False
        if attempts and (time.monotonic() - attempts[-1]) < _CATCHUP_RETRY_INTERVAL:
            return False
        attempts.append(time.monotonic())
        logger.warning(
            "NightlyDigest: 已錯過主視窗，啟動第 %d 次補跑嘗試（日期 %s）。",
            len(attempts), date_str,
        )
        return True

    @_nightly_loop.before_loop
    async def _before_loop(self) -> None:
        await self.bot.wait_until_ready()

    # -----------------------------------------------------------------------
    # 世界記憶補跑
    # -----------------------------------------------------------------------
    async def _retry_world_memory(self) -> None:
        """補跑 world_retry_queue 中未完成的世界記憶提煉。

        日期的 processed 標記只代表「使用者記憶」完成，世界階段失敗不會回滾，
        因此需獨立重試。此處重跑單一伺服器的提煉，不重跑整日流程（避免重複寫入使用者記憶）。
        """
        pending = get_pending_world_retries(limit=_WORLD_RETRY_BATCH)
        if not pending:
            return

        now = time.monotonic()
        if now - self._world_retry_last_run < _WORLD_RETRY_INTERVAL:
            return
        self._world_retry_last_run = now

        brain_cog = self.bot.get_cog("BotBrain")
        if not brain_cog:
            return
        brain: Any = brain_cog

        for date_str, sid in pending:
            key = f"{date_str}|{sid}"
            attempts = self._world_retry_attempts.get(key, 0)
            if attempts >= _WORLD_RETRY_MAX_ROUNDS:
                logger.error(
                    "NightlyDigest: 世界記憶補跑 %s sid=%s 已重試 %d 次仍失敗，放棄補跑（該日此伺服器世界記憶將缺失）。",
                    date_str, sid, attempts,
                )
                self._world_retry_attempts.pop(key, None)
                resolve_world_retry(date_str, sid)
                continue

            records = await asyncio.to_thread(read_chat_log, date_str)
            s_msgs = [
                r for r in records if str(r.get("server_id", "") or "").strip() == sid
            ]
            if len(s_msgs) < _MIN_MSGS_THRESHOLD:
                logger.info(
                    "NightlyDigest: 世界記憶補跑 %s sid=%s 記錄不足（%d 則），略過。",
                    date_str, sid, len(s_msgs),
                )
                resolve_world_retry(date_str, sid)
                continue

            sample_size = int(getattr(settings, "NIGHTLY_WORLD_SAMPLE_COUNT", 200))
            sampled = s_msgs[-sample_size:]
            logger.info(
                "NightlyDigest: 世界記憶補跑中 %s sid=%s（第 %d/%d 次，%d 則記錄）",
                date_str, sid, attempts + 1, _WORLD_RETRY_MAX_ROUNDS, len(sampled),
            )

            done = asyncio.Event()
            result: list[list[dict[str, Any]] | None] = [None]

            async def _on_done(reply: str) -> None:
                try:
                    if reply.strip():
                        result[0] = _parse_world_events(reply)
                except (json.JSONDecodeError, ValueError, TypeError) as e:
                    logger.warning("NightlyDigest: 世界記憶補跑 %s sid=%s 解析失敗: %s", date_str, sid, e)
                finally:
                    done.set()

            retry_task = InferenceTask(
                priority=settings.PRIORITY_VIBE_UPDATE,
                system_prompt=_WORLD_SYSTEM_PROMPT,
                user_prompt=_build_world_user_prompt(date_str, sampled),
                callback=_on_done,
                model=settings.PRIMARY_MODEL,
                task_id=f"world-retry-{date_str}-{sid}",
                max_tokens=4096,
            )
            await brain.enqueue_task(retry_task)

            try:
                await asyncio.wait_for(done.wait(), timeout=settings.NIGHTLY_DIGEST_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning("NightlyDigest: 世界記憶補跑 %s sid=%s 逾時", date_str, sid)
                result[0] = None

            events = result[0]
            if not events:
                if getattr(retry_task, "is_content_filtered", False):
                    logger.warning(
                        "NightlyDigest: 世界記憶補跑 %s sid=%s 遭遇內容安全過濾 (content_filter)，立即從重試佇列移除。",
                        date_str, sid,
                    )
                    self._world_retry_attempts.pop(key, None)
                    resolve_world_retry(date_str, sid)
                else:
                    self._world_retry_attempts[key] = attempts + 1
                await asyncio.sleep(3.0)
                continue

            try:
                from handlers.memory.world_memory import log_world_event
                written = 0
                for ev in events[:8]:
                    if not (isinstance(ev, dict) and ev.get("summary")):
                        continue
                    summary = str(ev["summary"]).strip()
                    if len(summary) < 12:
                        continue
                    log_world_event(
                        event_type="CHAT",
                        summary=summary,
                        participants=list(ev.get("participants", []) or []),
                        importance=int(ev.get("importance", 3)),
                        server_id=sid,
                        date_str=date_str,
                    )
                    written += 1
            except Exception as exc:
                logger.warning("NightlyDigest: 世界記憶補跑寫入失敗 %s sid=%s: %s", date_str, sid, exc)
                self._world_retry_attempts[key] = attempts + 1
                await asyncio.sleep(3.0)
                continue

            resolve_world_retry(date_str, sid)
            self._world_retry_attempts.pop(key, None)
            logger.info(
                "NightlyDigest: 世界記憶補跑完成 %s sid=%s，寫入 %d 條事件。", date_str, sid, written,
            )
            await asyncio.sleep(3.0)

    # -----------------------------------------------------------------------
    # Admin 手動觸發指令
    # -----------------------------------------------------------------------
    @commands.command(name="nightly_digest", hidden=True)
    @commands.is_owner()
    async def cmd_nightly_digest(self, ctx: commands.Context[Any], date_str: str = "") -> None:
        """手動觸發深夜整合（管理員用，供測試）。用法：!nightly_digest [YYYY-MM-DD]"""
        if not date_str:
            date_str = get_yesterday_str()
        await ctx.send(f"🌙 開始整合 `{date_str}` 的記憶，請稍候...")
        stats = await self._run_digest(date_str, force=True)
        if stats and stats.get("status") == "all_users_failed":
            await ctx.send(
                f"⚠️ `{date_str}` 整合失敗：{stats['users_eligible']} 位使用者的上游推論皆無回應。\n"
                f"本日**未**標記為已處理，系統會自動補跑；你也可稍後再執行一次本指令。"
            )
        elif stats and stats.get("status") == "completed":
            message = (
                f"✅ 整合完成！\n"
                f"共處理 **{stats['users_processed']}** 位使用者，"
                f"新增 **{stats['facts_added']}** 條記憶。"
            )
            failed_count = len(stats.get("failed_users", [])) + len(stats.get("timed_out_users", []))
            if failed_count:
                message += f"\n⚠️ 有 **{failed_count}** 位使用者失敗，本日不會自動重試。"
            await ctx.send(message)
        else:
            await ctx.send("⚠️ 整合失敗或已無資料可處理。")

    # -----------------------------------------------------------------------
    # 核心整合邏輯
    # -----------------------------------------------------------------------
    async def _run_digest(self, date_str: str, force: bool = False) -> dict[str, Any] | None:
        """
        讀取指定日期的 chat log，對每位使用者執行深度反射萃取，更新 user_memory.json。
        force=True 時忽略 .processed 標記（手動觸發用）。
        """
        if self._is_running:
            logger.warning("NightlyDigest: 整合已在執行中，跳過本次觸發")
            return None
        self._is_running = True

        try:
            logger.info("NightlyDigest: 開始整合 %s 的記憶...", date_str)
            records = read_chat_log(date_str)
            if not records:
                logger.info("NightlyDigest: %s 無聊天記錄，跳過", date_str)
                mark_date_processed(date_str, {"skipped": True, "reason": "no_records"})
                return None

            # 按 user_id 分組
            by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
            display_names: dict[str, str] = {}
            for rec in records:
                uid = rec.get("user_id", "")
                if not uid:
                    continue
                by_user[uid].append(rec)
                display_names[uid] = rec.get("display_name", uid)

            brain_cog = self.bot.get_cog("BotBrain")
            if not brain_cog:
                logger.error("NightlyDigest: BotBrain 不可用，中止整合")
                return None
            brain: Any = brain_cog

            store = load_user_memory(settings.USER_MEMORY_PATH)
            users_processed = 0
            users_eligible = 0
            facts_added = 0
            failed_users: list[str] = []
            timed_out_users: list[str] = []

            for uid, msgs in by_user.items():
                if len(msgs) < _MIN_MSGS_THRESHOLD:
                    continue  # 發言太少，不值得整合

                users_eligible += 1
                display_name = display_names.get(uid, uid)

                # 取最近 N 條（避免 prompt 過長）
                capped = msgs[-settings.NIGHTLY_MAX_MSGS_PER_USER:]
                texts = "\n".join(
                    f"[{r['ts'][11:16]}] {r['content']}"
                    for r in capped
                )

                current_profile = store.get(uid, {"aliases": [display_name], "facts": []})
                current_facts_text = "\n".join(
                    f"- {f['fact']}" for f in current_profile.get("facts", [])
                ) or "（目前無已知記憶）"

                system_prompt = _NIGHTLY_SYSTEM_PROMPT.format(
                    display_name=display_name,
                    current_facts=current_facts_text,
                )
                user_prompt = (
                    f"以下是「{display_name}」在 {date_str} 的完整發言記錄"
                    f"（共 {len(capped)} 條）：\n\n{texts}"
                )

                # 使用 asyncio.Event 等待 LLM 回覆
                import asyncio
                done_event = asyncio.Event()
                result_holder: list[dict[str, Any] | None] = [None]

                async def _on_result(reply: str, _uid: str = uid) -> None:
                    try:
                        raw = reply.strip()
                        if not raw:
                            logger.warning(
                                "NightlyDigest: uid=%s 收到空白回應（上游推論失敗），跳過本日整合。",
                                _uid,
                            )
                            return
                        raw = raw.lstrip("```json").lstrip("```").rstrip("```").strip()
                        m = re.search(r"\{.*\}", raw, re.DOTALL)
                        if m:
                            raw = m.group(0)
                        result_holder[0] = json.loads(raw)
                    except (json.JSONDecodeError, ValueError, TypeError) as e:
                        logger.warning("NightlyDigest: JSON 解析失敗 uid=%s: %s", _uid, e)
                    except Exception as e:
                        logger.warning("NightlyDigest: 整合回應處理異常 uid=%s: %s", _uid, e)
                    finally:
                        done_event.set()

                user_task = InferenceTask(
                    priority=settings.PRIORITY_VIBE_UPDATE,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    callback=_on_result,
                    model=settings.PRIMARY_MODEL,
                    task_id=f"nightly-{date_str}-{uid}",
                    max_tokens=4096,
                )
                await brain.enqueue_task(user_task)

                try:
                    await asyncio.wait_for(done_event.wait(), timeout=settings.NIGHTLY_DIGEST_TIMEOUT)
                except asyncio.TimeoutError:
                    logger.error("NightlyDigest: uid=%s 整合超時，跳過", uid)
                    timed_out_users.append(uid)
                    await asyncio.sleep(3.0)
                    continue

                parsed = result_holder[0]
                if not parsed:
                    if getattr(user_task, "is_content_filtered", False):
                        logger.warning(
                            "NightlyDigest: uid=%s (%s) 遭遇供應商內容安全過濾 (content_filter)，跳過本日整合。",
                            uid, display_name,
                        )
                    failed_users.append(uid)
                    await asyncio.sleep(3.0)
                    continue

                # ── Aliases 消毒（共用函式）──────────────────────
                raw_aliases = list(current_profile.get("aliases", [])) + list(parsed.get("aliases", []))
                clean_aliases = sanitize_aliases(raw_aliases)

                # ── Facts 合併（共用函式）─────────────────────────
                existing_facts: list[UserFact] = list(current_profile.get("facts", []))
                final_facts, new_count = sanitize_and_merge_facts(
                    existing_facts, parsed.get("facts", []), MAX_FACTS_PER_USER,
                )

                store[uid] = {"aliases": clean_aliases, "facts": final_facts}
                users_processed += 1
                facts_added += new_count
                _remember_care_topics(uid, date_str, parsed)
                logger.info(
                    "NightlyDigest: uid=%s (%s) 整合完成，新增 %d 條記憶",
                    uid, display_name, new_count,
                )
                await asyncio.sleep(3.0)  # Pacing 固定間隔：避免多用戶連續呼叫觸發雲端限流 (429)

            # ── 全數失敗防護：絕不可標記已處理 ──────────────────────
            # 若所有符合門檻的使用者都因上游推論失敗（空白回應／解析失敗／逾時）而未整合，
            # 卻仍寫入 processed 標記，該日期將被 _nightly_loop 永久略過，
            # 導致整天聊天記錄永遠不會再被整合（無聲資料遺失）。
            if users_eligible > 0 and users_processed == 0:
                logger.error(
                    "NightlyDigest: %s 的 %d 位使用者全部整合失敗（空白回應 %d 位、逾時 %d 位），"
                    "保留未標記狀態以便稍後重試。",
                    date_str, users_eligible, len(failed_users), len(timed_out_users),
                )
                return {
                    "date_str": date_str,
                    "status": "all_users_failed",
                    "users_processed": 0,
                    "users_eligible": users_eligible,
                    "failed_users": failed_users,
                    "timed_out_users": timed_out_users,
                    "facts_added": 0,
                    "total_messages": len(records),
                }

            if failed_users or timed_out_users:
                logger.warning(
                    "NightlyDigest: %s 有 %d 位使用者整合失敗（空白/解析 %d 位、逾時 %d 位），"
                    "將不會自動重試，可手動執行 !nightly_digest %s 補做。",
                    date_str, len(failed_users) + len(timed_out_users),
                    len(failed_users), len(timed_out_users), date_str,
                )

            # ── 記憶衰減 ────────────────────────────────────────
            decay_removed = decay_memories(store)
            if decay_removed > 0:
                logger.info("NightlyDigest: 記憶衰減移除了 %d 條暫時性 facts", decay_removed)

            # ── 全庫記憶大掃除與語意去重 ─────────────────────────
            clean_stats = consolidate_all_user_memories(store)
            if clean_stats["facts_removed"] > 0 or clean_stats["aliases_removed"] > 0:
                logger.info(
                    "NightlyDigest: 全庫記憶大掃除完成，清理 %d 位使用者，移除 %d 條重複 facts、%d 個無效別名",
                    clean_stats["users_cleaned"], clean_stats["facts_removed"], clean_stats["aliases_removed"],
                )

            # ── Chat Log 清理（保留最近 30 天）───────────────────
            cutoff = datetime.now(_TZ_TW) - timedelta(days=30)
            chat_log_dir = settings.CHAT_LOG_DIR
            if chat_log_dir.exists():
                for f in chat_log_dir.glob("*.jsonl"):
                    try:
                        date_str_f = f.stem
                        file_date = datetime.strptime(date_str_f, "%Y-%m-%d").replace(tzinfo=_TZ_TW)
                        if file_date < cutoff:
                            f.unlink()
                            processed = f.with_suffix(".jsonl.processed")
                            if processed.exists():
                                processed.unlink()
                    except ValueError:
                        pass

            # 存檔
            save_user_memory(store, settings.USER_MEMORY_PATH)

            # ✅ [修復] 深夜整合寫回磁碟後，立即通知 MemoryManager 重載 RAM，
            # 防止白天的即時萃取使用開機時的舊副本，將深夜整合的成果全部沖掉。
            mem_mgr_early: Any = self.bot.get_cog("MemoryManager")
            if mem_mgr_early:
                mem_mgr_early.reload_store()
                logger.info("NightlyDigest: MemoryManager RAM 已同步最新磁碟記憶。")

            stats = {
                "status": "completed",
                "users_processed": users_processed,
                "users_eligible": users_eligible,
                "failed_users": failed_users,
                "timed_out_users": timed_out_users,
                "facts_added": facts_added,
                "total_messages": len(records),
                "decay_removed": decay_removed,
                "duplicates_removed": clean_stats["facts_removed"],
                "aliases_cleaned": clean_stats["aliases_removed"],
            }
            # 世界記憶於本行之後才執行（mark_date_processed 早於世界階段），
            # 故此處僅記錄使用者層級結果；世界階段失敗會另行記錄到 _WORLD_RETRY_QUEUE 供下次補跑。
            mark_date_processed(date_str, stats)
            logger.info(
                "NightlyDigest: %s 整合完畢！共 %d 位使用者，新增 %d 條記憶",
                date_str, users_processed, facts_added,
            )

            # ── 好感度每日衰減 ──────────────────────────────────
            mem_mgr: Any = self.bot.get_cog("MemoryManager")
            if mem_mgr:
                mem_mgr.relations.decay_all()

            # ── 世界記憶提煉、壓縮與清理 ───────────────────────────────
            world_failed_servers: list[str] = []
            try:
                from handlers.memory.world_memory import (
                    log_world_event, compress_daily_events, cleanup_old_logs
                )
                # 按伺服器分組聊天記錄
                by_server: dict[str, list[dict[str, Any]]] = defaultdict(list)
                for rec in records:
                    sid = str(rec.get("server_id", "") or "").strip()
                    if sid:
                        by_server[sid].append(rec)

                # 對各伺服器昨日聊天提煉 3~6 條世界記憶條目（大事件 + 進行中話題 + 群體關係變化）
                for sid, s_msgs in by_server.items():
                    if len(s_msgs) < _MIN_MSGS_THRESHOLD:
                        continue

                    sample_size = int(getattr(settings, "NIGHTLY_WORLD_SAMPLE_COUNT", 200))
                    sampled = s_msgs[-sample_size:]  # 深夜非同步，取樣多一些供提煉
                    world_sys_prompt = _WORLD_SYSTEM_PROMPT
                    world_user_prompt = _build_world_user_prompt(date_str, sampled)

                    world_done_event = asyncio.Event()
                    world_res_holder: list[list[dict[str, Any]] | None] = [None]

                    async def _on_world_result(reply: str, _sid: str = sid) -> None:
                        try:
                            if not reply.strip():
                                logger.warning(
                                    "NightlyDigest: 世界記憶 sid=%s 收到空白回應（上游推論失敗）。",
                                    _sid,
                                )
                                return
                            events = _parse_world_events(reply)
                            if events is None:
                                logger.warning(
                                    "NightlyDigest: 世界記憶 sid=%s 回應非 JSON Array，已忽略。", _sid,
                                )
                                return
                            world_res_holder[0] = events
                        except (json.JSONDecodeError, ValueError, TypeError) as e:
                            logger.warning("NightlyDigest: 世界記憶 JSON 解析失敗 sid=%s: %s", _sid, e)
                        except Exception as e:
                            logger.warning("NightlyDigest: 世界記憶回應處理異常 sid=%s: %s", _sid, e)
                        finally:
                            world_done_event.set()

                    world_task = InferenceTask(
                        priority=settings.PRIORITY_VIBE_UPDATE,
                        system_prompt=world_sys_prompt,
                        user_prompt=world_user_prompt,
                        callback=_on_world_result,
                        model=settings.PRIMARY_MODEL,
                        task_id=f"world-{date_str}-{sid}",
                        max_tokens=4096,
                    )
                    await brain.enqueue_task(world_task)

                    try:
                        await asyncio.wait_for(world_done_event.wait(), timeout=settings.NIGHTLY_DIGEST_TIMEOUT)
                    except asyncio.TimeoutError:
                        logger.warning("NightlyDigest: 世界記憶提煉超時 sid=%s", sid)
                        world_failed_servers.append(sid)
                        await asyncio.sleep(3.0)
                        continue

                    extracted_events = world_res_holder[0]
                    if not extracted_events:
                        if getattr(world_task, "is_content_filtered", False):
                            logger.warning(
                                "NightlyDigest: 世界記憶 sid=%s 遭遇供應商內容安全過濾 (content_filter)，禁止排入重試佇列。",
                                sid,
                            )
                        else:
                            world_failed_servers.append(sid)
                    else:
                        for ev in extracted_events[:8]:  # 防 LLM 過度產出，封頂 8 條
                            if not (isinstance(ev, dict) and ev.get("summary")):
                                continue
                            summary_text = str(ev["summary"]).strip()
                            if len(summary_text) < 12:  # 過短的無意義條目直接略過
                                continue
                            log_world_event(
                                event_type="CHAT",
                                summary=summary_text,
                                participants=list(ev.get("participants", []) or []),
                                importance=int(ev.get("importance", 3)),
                                server_id=sid,
                                date_str=date_str,
                            )
                    await asyncio.sleep(3.0)  # Pacing 固定間隔：避免多伺服器連續呼叫觸發雲端限流 (429)

                # 依伺服器分組壓縮，確保各伺服器世界記憶彼此獨立
                server_ids: list[str] = list(by_server.keys())
                guilds = getattr(self.bot, "guilds", [])
                if guilds:
                    server_ids.extend([str(g.id) for g in guilds if getattr(g, "id", None) is not None])
                server_ids.append("")  # 涵蓋無 server 歸屬的舊事件
                unique_sids = list(set(server_ids))

                def _run_world_compress() -> tuple[int, int]:
                    added = sum(compress_daily_events(date_str, server_id=sid) for sid in unique_sids)
                    cleaned = cleanup_old_logs(days=30)
                    return added, cleaned

                world_added, world_cleaned = await asyncio.to_thread(_run_world_compress)
                if world_added > 0:
                    logger.info("NightlyDigest: 世界記憶壓縮完成，新增 %d 條事件（分組 %d 個伺服器）", world_added, len(unique_sids))
                if world_cleaned > 0:
                    logger.info("NightlyDigest: 清理了 %d 個過期世界 log 檔案", world_cleaned)

                # ── 世界記憶長期歸檔（每週/月 Markdown，只存不注入）────────────────
                try:
                    from handlers.memory import memory_archive
                    archived = await asyncio.to_thread(memory_archive.archive_finished_week, date_str)
                    if archived:
                        logger.info("NightlyDigest: 世界記憶週歸檔完成: %s", archived.name)
                except Exception as arc_err:
                    logger.warning("NightlyDigest: 世界記憶週歸檔失敗: %s", arc_err)
            except Exception as e:
                logger.warning("NightlyDigest: 世界記憶處理失敗: %s", e)

            # 世界記憶失敗不會回滾日期標記（使用者記憶已成功寫入），
            # 故需獨立記錄待補跑的伺服器，並由巡邏迴圈自動補跑。
            if world_failed_servers:
                logger.error(
                    "NightlyDigest: %s 有 %d 個伺服器世界記憶提煉失敗（%s），已排入 world_retry_queue 待補跑。",
                    date_str,
                    len(world_failed_servers),
                    ", ".join(world_failed_servers),
                )
                _record_world_retry(date_str, world_failed_servers)

            # ── 資料保留原則清理與自動日誌輪轉 (DataRetentionWorker) ────────────
            try:
                from handlers.memory.retention_worker import DataRetentionWorker

                retention_worker = DataRetentionWorker()
                report = await asyncio.to_thread(retention_worker.run_retention_cycle)
                stats["retention_cleaned_files"] = report.cleaned_files_count
                stats["retention_purged_logs"] = report.purged_log_entries
                stats["retention_reclaimed_bytes"] = report.reclaimed_bytes
                if report.cleaned_files_count > 0 or report.purged_log_entries > 0:
                    logger.info(
                        "NightlyDigest: 資料保留清理完成: 清理檔案 %d 個, 輪轉記錄 %d 條, 釋放 %.2f MB",
                        report.cleaned_files_count,
                        report.purged_log_entries,
                        report.reclaimed_bytes / (1024 * 1024),
                    )
            except Exception as ret_err:
                logger.warning("NightlyDigest: 資料保留清理執行失敗: %s", ret_err)

            # ── 雪莉自我日記與衍生興趣整合 (Self-Memory Diary Digest) ─────────────
            try:
                await self._run_self_diary_digest(date_str, records, brain)
            except Exception as diary_err:
                logger.warning("NightlyDigest: 自我日記整合執行失敗: %s", diary_err)

            return stats

        except Exception as exc:
            logger.exception("NightlyDigest: 整合過程發生未知錯誤: %s", exc)
            return None
        finally:
            self._is_running = False

    async def _run_self_diary_digest(
        self, date_str: str, records: list[dict[str, Any]], brain: Any
    ) -> None:
        """根據當日互動與衝浪閱讀紀錄，提煉 1~2 條雪莉第一人稱日記 (diary) 與延伸興趣 (interest)。"""
        try:
            from handlers.memory.self_memory import add_self_episode, list_reading_logs
            recent_readings = list_reading_logs(limit=5)
            reading_summary = "\n".join([
                f"- 《{r['title']}》：{r['sherry_opinion'] or r['summary']}"
                for r in recent_readings[:3]
            ])

            sample_size = min(len(records), 50)
            sample_records = records[-sample_size:] if sample_size > 0 else []
            chat_snippets = "\n".join([
                f"{r.get('display_name', '群友')}: {r.get('content', '')[:100]}"
                for r in sample_records[:30] if r.get('content')
            ])

            if not chat_snippets and not reading_summary:
                return

            system_prompt = (
                "你是雪莉（Sherry）的內心自省日記工人。請以雪莉本人的第一人稱口吻，"
                "回顧今天的群友互動與上網閱讀內容，生成 1 篇簡短的生活隨筆/日記，以及 1~2 個延伸感興趣的話題。\n"
                "必須輸出嚴格 JSON 格式：\n"
                "{\n"
                '  "diary": "（第一人稱，50~100字，生動自然帶點吐槽或感觸）",\n'
                '  "interests": ["興趣關鍵字1", "興趣關鍵字2"]\n'
                "}"
            )
            user_prompt = f"【日期】：{date_str}\n【今日部分群友對話】：\n{chat_snippets}\n【今日閱讀文章】：\n{reading_summary}"

            done = asyncio.Event()
            res_holder: list[dict[str, Any] | None] = [None]

            async def _on_diary_done(reply: str) -> None:
                try:
                    cleaned = reply.strip()
                    if cleaned.startswith("```"):
                        lines = cleaned.splitlines()
                        if lines[0].startswith("```"):
                            lines = lines[1:]
                        if lines and lines[-1].startswith("```"):
                            lines = lines[:-1]
                        cleaned = "\n".join(lines).strip()
                    res_holder[0] = json.loads(cleaned)
                except Exception as e:
                    logger.warning("NightlyDigest: 自我日記 JSON 解析失敗: %s", e)
                finally:
                    done.set()

            task = InferenceTask(
                priority=getattr(settings, "PRIORITY_VIBE_UPDATE", 3),
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                callback=_on_diary_done,
                model=getattr(settings, "LIGHT_MODEL", settings.PRIMARY_MODEL),
                task_id=f"self-diary-{date_str}",
                max_tokens=1024,
            )
            await brain.enqueue_task(task)
            try:
                await asyncio.wait_for(done.wait(), timeout=60.0)
            except asyncio.TimeoutError:
                logger.warning("NightlyDigest: 自我日記提煉逾時")

            result = res_holder[0]
            if result and isinstance(result, dict):
                diary_text = str(result.get("diary", "")).strip()
                if diary_text:
                    add_self_episode(
                        category="diary",
                        content=diary_text,
                        tags=["每日日記", date_str],
                        importance=3,
                    )
                for item in result.get("interests", []):
                    item_str = str(item).strip()
                    if item_str:
                        add_self_episode(
                            category="interest",
                            content=item_str,
                            tags=["延伸興趣"],
                            importance=3,
                        )
                logger.info("NightlyDigest: %s 雪莉自我日記與興趣提煉完成。", date_str)
        except Exception as exc:
            logger.warning("NightlyDigest: 自我日記整合發生例外: %s", exc)

