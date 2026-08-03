import json
import logging
import discord
from discord.ext import commands, tasks

import settings
# 分為內部同包裹載入儲存模型
from .storage import load_vibe_store, save_vibe_store, TopicEntry, VibeStore
from handlers.bot_brain import BotBrain, InferenceTask

logger = logging.getLogger(__name__)

class VibeManager(commands.Cog):
    """跨區處理背景氛圍提取，自動隨著衰弱閥門遺忘非話題。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # 從剛剛新建的 storage 去安全提取檔案!
        self._store: VibeStore = load_vibe_store(settings.CHANNEL_VIBE_PATH)
        logger.info("VibeManager: 群眾心態模擬體啟動. 裝載通道熱搜庫共: %d.", len(self._store))

    async def cog_load(self) -> None:
        self._decay_loop.change_interval(seconds=settings.VIBE_DECAY_INTERVAL_SECONDS)
        self._decay_loop.start()

    async def cog_unload(self) -> None:
        self._decay_loop.cancel()
        save_vibe_store(self._store, settings.CHANNEL_VIBE_PATH)
        logger.info("VibeManager: 重起中止-關閉自動卸除所有記憶落盤緩存。")

    # ==========================
    # 開放介面：供代理人、打斷機使用提取。
    # ==========================
    def get_top_topics_snippet(self, channel_id: int) -> str:
        channel_topics = self._store.get(str(channel_id), {})
        if not channel_topics:
            return ""

        qualifying = [(t, entry) for t, entry in channel_topics.items() 
                      if entry["weight"] >= settings.VIBE_MIN_WEIGHT_TO_INJECT]
        if not qualifying: return ""

        top = sorted(qualifying, key=lambda x: x[1]["weight"], reverse=True)[: settings.VIBE_TOP_N_TOPICS]
        return "\n".join(
            [f"- {topic}（熱度: {entry['weight']}）{('：'+entry['summary']) if entry['summary'] else ''}" 
             for topic, entry in top]
        )

    # ==========================
    # 推理事件：發配總腦更新最近這話題紀錄。
    # ==========================
    async def request_vibe_update(self, channel: discord.TextChannel) -> None:
        brain: BotBrain | None = self.bot.get_cog("BotBrain")
        if brain is None: return

        lines: list[str] = []
        async for msg in channel.history(limit=settings.VIBE_UPDATE_MESSAGE_THRESHOLD, oldest_first=False):
            if not msg.author.bot:
                lines.append(f"[{msg.author.display_name}]: {msg.clean_content}")
        if not lines: return
        
        conversation_block = "\n".join(reversed(lines))
        channel_id_str = str(channel.id)

        system_prompt = (
            "你是一個群組話題分析器。"
            "從以下 Discord 對話中找出 1 到 3 個最熱門的話題或內部梗，"
            "只輸出一個 JSON 陣列，每個元素包含兩個欄位：\n"
            '  "topic"：話題名稱（簡短，不超過15字）\n'
            '  "summary"：一句話描述這個話題的內容（不超過30字）\n'
            "禁止輸出任何其他文字或 Markdown。"
        )

        async def _on_vibe_result(result: str) -> None:
            try:
                cleaned = result.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
                items: list[dict[str, str]] = json.loads(cleaned)
                if not isinstance(items, list): raise ValueError("Expected list!")
            except (json.JSONDecodeError, ValueError) as exc:
                return

            channel_store = self._store.setdefault(channel_id_str, {})
            for item in items:
                if not isinstance(item, dict): continue
                topic = str(item.get("topic", "")).strip()
                summary = str(item.get("summary", "")).strip()
                if not topic: continue

                if topic in channel_store:
                    channel_store[topic]["weight"] = settings.VIBE_REINFORCE_MAX_WEIGHT
                    if summary: channel_store[topic]["summary"] = summary
                else:
                    channel_store[topic] = TopicEntry(summary=summary, weight=2)
            save_vibe_store(self._store, settings.CHANNEL_VIBE_PATH)

        task = InferenceTask(
            priority=settings.PRIORITY_VIBE_UPDATE, system_prompt=system_prompt,
            user_prompt=conversation_block, callback=_on_vibe_result,
            model=settings.VIBE_MODEL, task_id=f"vibe-update-{channel.id}",
        )
        await brain.enqueue_task(task)

    # ==========================
    # 計時循環事件：自動讓沒有提及的話題下降趨緩刪除 (Weight Decay)
    # ==========================
    @tasks.loop(seconds=3600) 
    async def _decay_loop(self) -> None:
        if not self._store: return
        for channel_id in list(self._store.keys()):
            channel_topics = self._store[channel_id]
            to_delete = [topic for topic, entry in channel_topics.items() if entry["weight"] - settings.VIBE_DECAY_AMOUNT <= 0]
            
            for topic in to_delete: del channel_topics[topic]
            for entry in channel_topics.values(): entry["weight"] -= settings.VIBE_DECAY_AMOUNT
            if not channel_topics: del self._store[channel_id]
            
        save_vibe_store(self._store, settings.CHANNEL_VIBE_PATH)

    @_decay_loop.before_loop
    async def _before_decay_loop(self) -> None:
        await self.bot.wait_until_ready()