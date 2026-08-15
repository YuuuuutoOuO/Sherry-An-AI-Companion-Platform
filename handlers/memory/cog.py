import json
import logging
import re
import discord
from discord.ext import commands

import settings
from handlers.memory.storage import (
    load_user_memory, save_user_memory,
    UserProfile, UserMemoryStore, UserFact, make_fact,
    sanitize_aliases, sanitize_and_merge_facts,
    get_diverse_facts_for_context,
    MAX_FACTS_IN_CONTEXT, MAX_FACTS_PER_USER,
)
from handlers.bot_brain import BotBrain, InferenceTask
from handlers.memory.relationship import RelationshipManager

logger = logging.getLogger(__name__)


class MemoryManager(commands.Cog):
    """靈魂記錄者！追尋全局單一個體 UID，於日常背景中抓取反思屬性的深睡大腦層。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._store: UserMemoryStore = load_user_memory(settings.USER_MEMORY_PATH)
        self._working_memory_buffers: dict[int, list[str]] = {}
        self.relations: RelationshipManager = RelationshipManager()

        # 積滿幾句話它才會覺得對方有資訊量並背景運轉抽出知識精華入庫。
        self.MEM_DIGEST_THRESHOLD = 5
        # 追蹤每日首次對話（用於好感度加權）
        self._daily_first_conversation: dict[int, str] = {}

        logger.info(
            "MemoryManager: [專有特質紀錄大殿已上線] - 共裝載 %d 個被刻上靈魂印記的人物。",
            len(self._store),
        )

    # ==========================
    # 🔄 記憶庫重載（供 NightlyDigest 完成後同步 RAM）
    # ==========================
    def reload_store(self) -> None:
        """從磁碟重新載入最新的 user_memory.json，確保 RAM 副本與磁碟一致。

        NightlyDigest 完成深夜整合並寫回磁碟後應呼叫此方法，
        防止白天的即時萃取使用開機時的舊副本覆蓋掉深夜整合的成果。
        """
        self._store = load_user_memory(settings.USER_MEMORY_PATH)
        logger.info(
            "MemoryManager: [記憶庫重載完畢] - 現共有 %d 個靈魂印記已同步。",
            len(self._store),
        )

    # ==========================
    # 給其他單位/子系統 公開查尋介接區的檢索系統
    # ==========================
    def get_user_memory_snippet(self, user_id: int) -> str:
        """產生注入 LLM Prompt 的使用者記憶片段（結合類別多樣性選取）。

        Args:
            user_id: Discord 使用者 ID。

        Returns:
            格式化後的長期記憶字串。
        """
        profile = self._store.get(str(user_id))
        if not profile or not profile.get("facts"):
            return ""

        clean_aliases = sanitize_aliases(profile.get("aliases", []))
        known_aliases = "、".join(clean_aliases) if clean_aliases else "查無特定曾用別稱"

        # 採用類別多樣性輪替取樣，防止單一話題霸佔 Context
        diverse_facts: list[UserFact] = get_diverse_facts_for_context(
            profile["facts"],
            max_count=MAX_FACTS_IN_CONTEXT,
        )
        if not diverse_facts:
            return ""

        facts_text = "\n".join([f"- {f['fact']}" for f in diverse_facts])

        snippet = (
            f"📌 這個人類曾登錄使用過的暱稱或代稱含有：{known_aliases}\n"
            f"📖 這個靈魂長存於你記憶區最重要的知識點歸納 (深沉過往：可結合你目前發生的話題引用吐槽):\n{facts_text}"
        )
        return snippet

    # ==========================
    # 不斷聆聽頻道廢水（背景吸收者 Listener）
    # ==========================
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # ✅ [修復] 同時支援 TextChannel 與 Thread，讓雪莉在討論串也能累積記憶
        if message.author.bot or not isinstance(message.channel, (discord.TextChannel, discord.Thread)):
            return

        uid = message.author.id
        msg_str = message.clean_content.strip()
        if not msg_str or len(msg_str) < 3:
            return

        # 🔑 將訊息中 @提到的其他人名稱替換為佔位符，防止 LLM 把被 TAG 的人的暱稱
        # 誤記為「發話者本人」的 alias 或 facts。
        # clean_content 已把 <@ID> 展開為 @顯示名稱，用 regex 整批替換。
        sanitized_msg = re.sub(r"@\S+", "[某群友]", msg_str)

        # 📝 寫入每日聊天記錄（供凌晨深夜整合使用）
        from handlers.memory.chat_logger import async_append_chat_log
        await async_append_chat_log(message)

        # ❤️ 好感度微量追蹤：認真說話 (>50字) 或每日首次 +1
        from datetime import datetime, timezone, timedelta
        today_str = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
        if self._daily_first_conversation.get(uid, "") != today_str:
            self._daily_first_conversation[uid] = today_str
            self.relations.update_affinity(uid, 1)
        if len(msg_str) > 50:
            self.relations.update_affinity(uid, 0.5)

        user_buffer = self._working_memory_buffers.setdefault(uid, [])
        user_buffer.append(f"他說過：「{sanitized_msg}」")

        if len(user_buffer) >= self.MEM_DIGEST_THRESHOLD:
            context_to_digest = "\n".join(user_buffer)
            self._working_memory_buffers[uid] = []
            await self._run_reflection_extract(message.author, context_to_digest)

    # ==========================
    # Vector萃取封包 - 強打碎長篇話術取出人物標記
    # ==========================
    async def _run_reflection_extract(self, author: discord.Member | discord.User, texts: str) -> None:
        brain: BotBrain | None = self.bot.get_cog("BotBrain")
        if not brain:
            return

        uid_str = str(author.id)

        # 📝 只讀取當前 facts 文字供 LLM Prompt 參考（不在閉包中快照 profile，避免 Lost-Update 競態）
        current_facts_text = "\n".join(
            f"- {f['fact']}" for f in self._store.get(uid_str, {}).get("facts", [])
        ) or "（目前無已知長期記憶）"

        # 請 LLM 返回帶有 category 與 importance 的新格式
        prompt = (
            "【全網段唯一靈魂資訊整理引擎(Reflection AI)】\n"
            f"這是一份來自群友「{author.display_name}」的過往發言紀錄：\n\n{texts}\n\n"
            "---神經指引手冊規定要求---\n"
            f"該群友目前已知的長期屬性：\n{current_facts_text}\n\n"
            "任務指令：從上述發言中，萃取關於「這位群友本人」的長期記憶。\n\n"
            "⚠️【核心禁止事項（違規將導致嚴重人格錯亂）】：\n"
            "1. 【嚴格禁止主客體顛倒】：發話者對其他人說的話（例如：叫別人吃大便、罵別人、開玩笑說『你只能...』、『你去死』），"
            "是發話者對『其他人』的祈使或嘴砲句，絕對不可記為發話者本人的喜好或特質！\n"
            "2. 【嚴格過濾嘴砲與幹話】：群聊中的打鬧、互相嘲諷、無厘頭幹話或情緒發洩，請一律忽略，留下空陣列，寧缺勿濫！\n"
            "3. 【嚴格別名判定】：只有發話者明確自我介紹（例如：『我叫XX』、『可以叫我XX』）才記入 aliases。"
            "絕對禁止將遊戲名稱（如賽爾號）、伺服器房號、動漫名、代號、泛稱（如群友、大家）或整句話記為 aliases！\n"
            "4. 發言紀錄中出現的「[某群友]」是發話者在訊息中 TAG 的其他人，絕對不是發話者本人！\n"
            "5. 分類規範（category）：\n"
            "   - identity: 真實身份/職業/居住地/個人自稱\n"
            "   - interest: 明確熱愛的遊戲/動漫/技術/興趣\n"
            "   - habit: 穩固生活作息/口頭禪\n"
            "   - relationship: 與雪莉（橘雪莉）的長期互動模式\n"
            "   - opinion: 對重要事物的穩定看法\n"
            "6. 每條 fact 必須附上 importance（1~5整數）：\n"
            "   1=短暫狀態  2=一般偏好  3=穩固習慣  4=重要特質  5=核心身份\n\n"
            "請只返回 JSON 格式，不可有任何其他文字：\n"
            '```json\n'
            '{"aliases": ["真實的綽號或自稱(若無則空陣列)"],\n'
            ' "facts": [{"fact": "他平時喜歡寫Python", "importance": 3, "category": "interest"}]}\n'
            '```'
        )

        async def _save_extracted(reply: str) -> None:
            try:
                raw = reply.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
                match = re.search(r"\{.*\}", raw, re.DOTALL)
                if match:
                    raw = match.group(0)
                parsed = json.loads(raw)

                # ✅ [修復 Lost-Update 競態] 在 callback 執行時才讀取最新狀態，
                # 而不是使用任務排入時的快照，防止多次並行任務互相覆蓋。
                latest_profile = self._store.get(
                    uid_str, {"aliases": [author.display_name], "facts": []}
                )

                # ── Aliases 消毒（共用函式）─────────────────────────
                raw_aliases = latest_profile.get("aliases", []) + parsed.get("aliases", [])
                clean_aliases = sanitize_aliases(raw_aliases)

                # ── Facts 合併（共用函式：語意模糊去重）────────────
                existing_facts: list[UserFact] = list(latest_profile.get("facts", []))
                final_facts, _new_count = sanitize_and_merge_facts(
                    existing_facts, parsed.get("facts", []), MAX_FACTS_PER_USER,
                )

                new_profile = UserProfile(
                    aliases=clean_aliases,
                    facts=final_facts,
                )
                self._store[uid_str] = new_profile
                save_user_memory(self._store, settings.USER_MEMORY_PATH)
                logger.debug(
                    "[靈魂知識拓源] 使用者 [%s] 記憶更新完畢，共 %d 條 facts。",
                    author.display_name, len(final_facts),
                )

            except Exception as e:
                logger.warning("[靈魂知識拓源] 解析 JSON 失敗或被放棄: %s", e)

        await brain.enqueue_task(InferenceTask(
            priority=settings.PRIORITY_VIBE_UPDATE,
            system_prompt=prompt,
            user_prompt=author.display_name,
            callback=_save_extracted,
            model=settings.CLASSIFIER_MODEL,
            task_id=f"memory-sync-{author.id}",
        ))