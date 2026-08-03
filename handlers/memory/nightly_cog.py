"""
handlers/memory/nightly_cog.py
================================
深夜記憶整合 Cog。

每分鐘檢查一次台灣時間，當時間進入凌晨 3:00~3:09 的 10 分鐘視窗，
且昨日的 chat log 尚未整合，就啟動深度記憶整合流程：

  1. 讀取昨日 data/chat_logs/YYYY-MM-DD.jsonl
  2. 按 user_id 分組，過濾發言 < MIN_MSGS_THRESHOLD 的使用者
  3. 對每位使用者送入 PRIMARY_MODEL 做深度反射萃取
  4. 更新 user_memory.json（與即時反射相同邏輯，但品質更高）
  5. 建立 .processed 標記，並記錄整合統計

Admin 指令 !nightly_digest 可立即觸發（跳過時間限制），方便測試。
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

import discord
from discord.ext import commands, tasks

import settings
from handlers.memory.chat_logger import (
    get_yesterday_str, is_date_processed,
    mark_date_processed, read_chat_log,
)
from handlers.memory.storage import (
    load_user_memory, save_user_memory, make_fact, UserFact,
    sanitize_aliases, sanitize_and_merge_facts,
    decay_memories, MAX_FACTS_PER_USER,
)
from handlers.bot_brain import BotBrain, InferenceTask
from handlers.memory.cog import MemoryManager

logger = logging.getLogger(__name__)

_TZ_TW = timezone(timedelta(hours=8))

# 每位使用者至少要有幾條發言才值得整合
_MIN_MSGS_THRESHOLD = getattr(settings, "NIGHTLY_MIN_MSGS_THRESHOLD", 5)

# 深度反射的 System Prompt（給大模型的提示）
_NIGHTLY_SYSTEM_PROMPT = (
    "【深夜靈魂整合引擎 (Nightly Reflection AI)】\n"
    "這是一份來自群友「{display_name}」今日完整的聊天記錄，"
    "內容遠比平時的片段更完整。\n\n"
    "---任務指令---\n"
    "請從這份完整記錄中，萃取關於「這位群友本人」的長期記憶。\n"
    "你有更多資訊可以推斷，請盡量找出有意義的人格特質、興趣、習慣、"
    "以及他與雪莉（橘雪莉）的互動模式。\n\n"
    "⚠️【絕對禁止事項】：\n"
    "1. 絕對不可將「橘雪莉」或 [某群友] 的名稱記入 aliases！\n"
    "   [某群友] 代表發話者在訊息中提及的「其他人」，不是發話者本人。\n"
    "2. aliases 只填入這個人真正的自稱、綽號（若無則空陣列）。\n"
    "3. 把完整句子或他說給別人的話記成 alias 或 fact 是嚴重錯誤。\n"
    "4. 若今日發言皆為普通幹話、無具體資訊，留下空陣列，寧缺勿濫。\n"
    "5. 每條 fact 必須附上 importance（1~5整數）：\n"
    "   1=短暫狀態  2=一般偏好  3=穩固習慣  4=重要特質  5=核心身份\n\n"
    "目前已知長期屬性（請勿重複，但可升級 importance）：\n"
    "{current_facts}\n\n"
    "請只返回 JSON 格式，不可有任何其他文字：\n"
    '```json\n'
    '{{"aliases": ["真實的綽號或自稱"],\n'
    ' "facts": [{{"fact": "他喜歡XX", "importance": 3}}]}}\n'
    '```'
)


class NightlyDigest(commands.Cog):
    """凌晨 3:00 深夜記憶整合工人。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._is_running = False  # 防止同時觸發多次

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
        now = datetime.now(_TZ_TW)
        # 凌晨 3:00 ~ 3:09 的 10 分鐘視窗
        if now.hour != settings.NIGHTLY_DIGEST_HOUR or now.minute >= 10:
            return
        yesterday = get_yesterday_str()
        if is_date_processed(yesterday):
            return
        await self._run_digest(yesterday)

    @_nightly_loop.before_loop
    async def _before_loop(self) -> None:
        await self.bot.wait_until_ready()

    # -----------------------------------------------------------------------
    # Admin 手動觸發指令
    # -----------------------------------------------------------------------
    @commands.command(name="nightly_digest", hidden=True)
    @commands.is_owner()
    async def cmd_nightly_digest(self, ctx: commands.Context, date_str: str = "") -> None:
        """手動觸發深夜整合（管理員用，供測試）。用法：!nightly_digest [YYYY-MM-DD]"""
        if not date_str:
            date_str = get_yesterday_str()
        await ctx.send(f"🌙 開始整合 `{date_str}` 的記憶，請稍候...")
        stats = await self._run_digest(date_str, force=True)
        if stats:
            await ctx.send(
                f"✅ 整合完成！\n"
                f"共處理 **{stats['users_processed']}** 位使用者，"
                f"新增 **{stats['facts_added']}** 條記憶。"
            )
        else:
            await ctx.send("⚠️ 整合失敗或已無資料可處理。")

    # -----------------------------------------------------------------------
    # 核心整合邏輯
    # -----------------------------------------------------------------------
    async def _run_digest(self, date_str: str, force: bool = False) -> dict | None:
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
            by_user: dict[str, list[dict]] = defaultdict(list)
            display_names: dict[str, str] = {}
            for rec in records:
                uid = rec.get("user_id", "")
                if not uid:
                    continue
                by_user[uid].append(rec)
                display_names[uid] = rec.get("display_name", uid)

            brain: BotBrain | None = self.bot.get_cog("BotBrain")
            if not brain:
                logger.error("NightlyDigest: BotBrain 不可用，中止整合")
                return None

            store = load_user_memory(settings.USER_MEMORY_PATH)
            users_processed = 0
            facts_added = 0

            for uid, msgs in by_user.items():
                if len(msgs) < _MIN_MSGS_THRESHOLD:
                    continue  # 發言太少，不值得整合

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
                result_holder: list[dict | None] = [None]

                async def _on_result(reply: str, _uid=uid, _profile=current_profile) -> None:
                    try:
                        raw = reply.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
                        m = re.search(r"\{.*\}", raw, re.DOTALL)
                        if m:
                            raw = m.group(0)
                        parsed = json.loads(raw)
                        result_holder[0] = parsed
                    except Exception as e:
                        logger.warning("NightlyDigest: JSON 解析失敗 uid=%s: %s", _uid, e)
                    finally:
                        done_event.set()

                await brain.enqueue_task(InferenceTask(
                    priority=settings.PRIORITY_VIBE_UPDATE,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    callback=_on_result,
                    model=settings.NIGHTLY_DIGEST_MODEL,
                    task_id=f"nightly-{date_str}-{uid}",
                ))

                try:
                    await asyncio.wait_for(done_event.wait(), timeout=settings.NIGHTLY_DIGEST_TIMEOUT)
                except asyncio.TimeoutError:
                    logger.error("NightlyDigest: uid=%s 整合超時，跳過", uid)
                    continue

                parsed = result_holder[0]
                if not parsed:
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
                logger.info(
                    "NightlyDigest: uid=%s (%s) 整合完成，新增 %d 條記憶",
                    uid, display_name, new_count,
                )

            # ── 記憶衰減 ────────────────────────────────────────
            decay_removed = decay_memories(store)
            if decay_removed > 0:
                logger.info("NightlyDigest: 記憶衰減移除了 %d 條暫時性 facts", decay_removed)

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
            mem_mgr_early: MemoryManager | None = self.bot.get_cog("MemoryManager")
            if mem_mgr_early:
                mem_mgr_early.reload_store()
                logger.info("NightlyDigest: MemoryManager RAM 已同步最新磁碟記憶。")

            stats = {
                "users_processed": users_processed,
                "facts_added": facts_added,
                "total_messages": len(records),
                "decay_removed": decay_removed,
            }
            mark_date_processed(date_str, stats)
            logger.info(
                "NightlyDigest: %s 整合完畢！共 %d 位使用者，新增 %d 條記憶",
                date_str, users_processed, facts_added,
            )

            # ── 好感度每日衰減 ──────────────────────────────────
            mem_mgr: MemoryManager | None = self.bot.get_cog("MemoryManager")
            if mem_mgr:
                mem_mgr.relations.decay_all()

            # ── 世界記憶壓縮與清理 ───────────────────────────────
            try:
                from handlers.memory.world_memory import compress_daily_events, cleanup_old_logs
                world_added = compress_daily_events(date_str)
                world_cleaned = cleanup_old_logs(days=30)
                if world_added > 0:
                    logger.info("NightlyDigest: 世界記憶壓縮完成，新增 %d 條事件", world_added)
                if world_cleaned > 0:
                    logger.info("NightlyDigest: 清理了 %d 個過期世界 log 檔案", world_cleaned)
            except Exception as e:
                logger.warning("NightlyDigest: 世界記憶處理失敗: %s", e)

            return stats

        except Exception as exc:
            logger.exception("NightlyDigest: 整合過程發生未知錯誤: %s", exc)
            return None
        finally:
            self._is_running = False
