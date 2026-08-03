import json
import logging
import re
import asyncio
from discord.ext import commands

import settings
from handlers.bot_brain.models import InferenceTask

logger = logging.getLogger(__name__)

async def parse_compound_command(bot: commands.Bot, user_query: str) -> list[str]:
    """
    攔截複合指令，若判斷為單一指令則直接回傳。
    若判斷為複合指令，交給 LLM 拆解為多個動作子句。
    """
    compound_keywords = ["然後", "接著", "並且", "還有", "再來", "順便", "and", "再幫我", "順便幫我"]
    has_compound_intent = any(kw in user_query for kw in compound_keywords)
    
    # 簡單防線：不夠長且不含複合詞彙，直接視為單一指令，秒回。
    if not has_compound_intent and len(user_query) < 15:
        return [user_query]
        
    brain = bot.get_cog("BotBrain")
    if not brain:
        return [user_query]

    prompt = (
        "[SYSTEM OPERATION OVERRIDE: PARSER MODE]\n"
        "你是一個自然語言解析器。使用者的輸入可能包含多個獨立的指令或動作要求。\n"
        "如果這句話是單一指令或純聊天，請將原句放在一個陣列中回傳。\n"
        "如果這句話包含多個獨立的動作（例如：『跳過這首歌，然後找一張貓咪圖片』），請將其拆解為多個子句，並保持使用者的原始請求意圖。\n"
        "注意：如果是純聊天中的連續語句（例如：『早安，你今天吃飽了嗎？然後你在幹嘛』），請視為單一指令，不要過度拆解。\n"
        "【強制輸出格式】：只輸出 JSON 字串陣列，例如：[\"跳過這首歌\", \"找一張貓咪圖片\"]\n"
    )
    
    future = bot.loop.create_future()

    async def _callback(reply: str) -> None:
        try:
            raw = reply.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            match = re.search(r"\[.*\]", raw, re.DOTALL)
            if match:
                raw = match.group(0)
            parsed = json.loads(raw)
            if isinstance(parsed, list) and len(parsed) > 0:
                future.set_result([str(p).strip() for p in parsed if str(p).strip()])
            else:
                future.set_result([user_query])
        except Exception as e:
            logger.warning(f"Compound parser JSON decode failed: {e}. Fallback to single query.")
            if not future.done():
                future.set_result([user_query])

    task = InferenceTask(
        priority=0, # 設定為 0 (最高優先)，比正常分類 PRIORITY_TAG_RESPONSE (1) 更快執行
        system_prompt=prompt,
        user_prompt=user_query,
        callback=_callback,
        model=settings.CLASSIFIER_MODEL,
        task_id=f"compound-parse"
    )
    
    await brain.enqueue_task(task)
    
    try:
        # 等待 LLM 回傳拆解結果
        return await asyncio.wait_for(future, timeout=settings.COMPOUND_PARSER_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning("Compound parser timeout. Fallback to single query.")
        return [user_query]
    except Exception as e:
        logger.warning(f"Compound parser error: {e}. Fallback to single query.")
        return [user_query]
