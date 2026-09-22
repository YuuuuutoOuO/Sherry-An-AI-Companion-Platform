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
from typing import Any

from discord.ext import commands, tasks

import settings
from handlers.memory.chat_logger import (
    get_yesterday_str, is_date_processed,
    mark_date_processed, read_chat_log,
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
    "   - rule: 【嚴格限定】他對雪莉（橘雪莉）的明確溝通硬規則（如『叫我OO』『不要用模糊詞』『說話要直接』『禁止某稱呼』）。注意：個人性癖、情境愛好或角色扮演偏好一律歸入 preference，絕對不是 rule！\n"
    "7. 每條 fact 必須附上 importance（1~5整數）：\n"
    "   1=短暫狀態  2=一般偏好  3=穩固習慣  4=重要特質  5=核心身份或最高規則\n"
    "8. 【記憶合併同類項原則】：請仔細參照底下『目前已知長期屬性』，若今日對話涉及相同情境（如同一款遊戲或按摩/角色設定），請將細節整合成 1 條語意完整且精練的事實並提高 importance，嚴禁生成重複破碎的多條同類事實！\n\n"
    "目前已知長期屬性（請勿重複，但可合併升級 importance）：\n"
    "{current_facts}\n\n"
    "請只返回 JSON 格式，不可有任何其他文字：\n"
    '```json\n'
    '{{"aliases": ["真實的綽號或自稱(若無則空陣列)"],\n'
    ' "facts": [\n'
    '   {{"fact": "在角色扮演情境中偏好兄妹設定與沉浸式互動", "importance": 4, "category": "preference"}},\n'
    '   {{"fact": "要求雪莉對話時必須直接稱呼他為主人", "importance": 5, "category": "rule"}},\n'
    '   {{"fact": "偏好純愛互動並強烈排斥 NTR 題材", "importance": 4, "category": "preference"}},\n'
    '   {{"fact": "正在深入玩韓版楓之谷並專注於裝備洗鍊", "importance": 3, "category": "preference"}}\n'
    ' ]}}\n'
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
        # 凌晨 4:30 ~ 4:39 的 10 分鐘視窗
        target_minute = getattr(settings, "NIGHTLY_DIGEST_MINUTE", 30)
        if now.hour != settings.NIGHTLY_DIGEST_HOUR or not (target_minute <= now.minute < target_minute + 10):
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

            brain_cog = self.bot.get_cog("BotBrain")
            if not brain_cog:
                logger.error("NightlyDigest: BotBrain 不可用，中止整合")
                return None
            brain: Any = brain_cog

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
                "users_processed": users_processed,
                "facts_added": facts_added,
                "total_messages": len(records),
                "decay_removed": decay_removed,
                "duplicates_removed": clean_stats["facts_removed"],
                "aliases_cleaned": clean_stats["aliases_removed"],
            }
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
            try:
                from handlers.memory.world_memory import (
                    log_world_event, compress_daily_events, cleanup_old_logs
                )
                # 按伺服器分組聊天記錄
                by_server: dict[str, list[dict]] = defaultdict(list)
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
                    summary_text = "\n".join(
                        f"[{r.get('display_name', '群友')}]: {r.get('content', '')}" for r in sampled
                    )
                    world_sys_prompt = (
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
                        '  {"summary": "雪莉大小姐與雪莉（希羅）進行兄妹情境的角色扮演互動，沉浸於禁忌浪漫與細膩的情節展開", "importance": 4, "participants": ["雪莉大小姐", "橘雪莉"]},\n'
                        '  {"summary": "歐潤吉與喵喵針對韓版楓之谷新轉移卷軸機制與裝備洗鍊收益深入探討攻略細節", "importance": 3, "participants": ["歐潤吉", "喵喵"]},\n'
                        '  {"summary": "群友們針對《絕區零》新角色養成機制與抽卡規劃展開熱烈交流與經驗分享", "importance": 3, "participants": ["喵喵", "咕咕嘎嘎"]}\n'
                        ']\n'
                        '```'
                    )
                    world_user_prompt = f"以下是伺服器在 {date_str} 的對話記錄（共 {len(sampled)} 則）：\n\n{summary_text}"

                    world_done_event = asyncio.Event()
                    world_res_holder: list[list[dict] | None] = [None]

                    async def _on_world_result(reply: str, _sid=sid) -> None:
                        try:
                            raw = reply.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
                            m = re.search(r"\[.*\]", raw, re.DOTALL)
                            if m:
                                raw = m.group(0)
                            parsed = json.loads(raw)
                            if isinstance(parsed, list):
                                world_res_holder[0] = parsed
                        except Exception as e:
                            logger.warning("NightlyDigest: 世界記憶 JSON 解析失敗 sid=%s: %s", _sid, e)
                        finally:
                            world_done_event.set()

                    await brain.enqueue_task(InferenceTask(
                        priority=settings.PRIORITY_VIBE_UPDATE,
                        system_prompt=world_sys_prompt,
                        user_prompt=world_user_prompt,
                        callback=_on_world_result,
                        model=settings.NIGHTLY_DIGEST_MODEL,
                        task_id=f"world-{date_str}-{sid}",
                    ))

                    try:
                        await asyncio.wait_for(world_done_event.wait(), timeout=settings.NIGHTLY_DIGEST_TIMEOUT)
                    except asyncio.TimeoutError:
                        logger.warning("NightlyDigest: 世界記憶提煉超時 sid=%s", sid)
                        continue

                    extracted_events = world_res_holder[0]
                    if extracted_events:
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

            return stats

        except Exception as exc:
            logger.exception("NightlyDigest: 整合過程發生未知錯誤: %s", exc)
            return None
        finally:
            self._is_running = False
