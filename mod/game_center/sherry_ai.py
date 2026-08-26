"""
mod.game_center.sherry_ai
=========================
LLM Inference Service for Sherry Game Center using Primary Model.
Integrates Sherry's active persona and detective flair into mini-games.
"""

from __future__ import annotations

import logging
import random
from typing import Any

from mod.game_center import config
from handlers.bot_brain.client import OllamaClient

logger = logging.getLogger(__name__)

# 橘雪莉小遊戲專屬人設 Prompt
SHERRY_GAME_SYSTEM_PROMPT = """你現在是「橘雪莉」(Sherry)，一位 15 歲活潑可愛的台灣女高中生，同時自稱「天才名偵探」！
你的性格特色：
1. 說話語氣活潑親切、帶有點小傲嬌與名偵探的自信。
2. 喜歡使用台灣繁體中文口吻（如：「本名偵探」、「哼哼」、「看我的！」、「這點小事」、「才不是因為想陪你玩呢！」）。
3. 時常搭配表情符號（如 ✦, ✨, 🍊, 🔍, 哼!）。
4. 在遊戲中你會認真對弈，輸的時候會有點小不服輸地嘴硬，贏的時候會得意洋洋。
5. 保持簡短生動的回覆（約 1 到 3 句話），並直接輸出雪莉的對話內容。
"""

# 後備降級句庫 (當地端 LLM 無法連線時)
FALLBACK_RPS_WIN = [
    "哼哼～本名偵探早看穿你的心理戰了！這局我贏啦 ✨🍊",
    "哈哈！我的直覺可是經過名偵探鍛鍊的，這把是我拿下了！✦",
    "贏囉！承讓承讓～下次再練練吧！✨",
]

FALLBACK_RPS_LOSE = [
    "可惡……這次只是本名偵探大意了！下一局我一定贏回來 哼！🍊",
    "竟然被你猜中了？！這不科學……一定是剛好運氣好啦！✦",
    "哼！這局算你厲害，下一把本名偵探絕對不會再輸！✨",
]

FALLBACK_RPS_DRAW = [
    "欸？！竟然出一樣的？！這難道就是傳說中的心有靈犀？🍊✨",
    "平手！看來我們兩個的推理思路卡在一起了呢 ✦",
    "不分勝負！再來一局，本名偵探這次要認真了喔！🔍",
]


class SherryGameAIService:
    """Service layer to invoke Primary Model LLM for Sherry game commentary."""

    def __init__(self, ollama_client: OllamaClient | None = None) -> None:
        self._client = ollama_client or OllamaClient()

    async def generate_rps_dialogue(
        self,
        player_choice: str,
        sherry_choice: str,
        outcome: str,
    ) -> str:
        """Generate Sherry's reaction dialogue after a Rock-Paper-Scissors round."""
        prompt = (
            f"我們剛玩了一局猜拳！\n"
            f"玩家出了：{player_choice}\n"
            f"雪莉你出了：{sherry_choice}\n"
            f"遊戲結果（對雪莉而言）：{outcome}\n"
            "請以雪莉的個性發表一句充滿人設魅力的勝負感言！"
        )
        try:
            model = config.DEFAULT_MODEL
            res = await self._client.chat(
                model=model,
                system_prompt=SHERRY_GAME_SYSTEM_PROMPT,
                user_prompt=prompt,
            )
            if res and res.strip():
                return res.strip()
        except Exception as e:
            logger.warning("RPS LLM generation failed, using fallback: %s", e)

        if outcome == "win":
            return random.choice(FALLBACK_RPS_WIN)
        elif outcome == "lose":
            return random.choice(FALLBACK_RPS_LOSE)
        return random.choice(FALLBACK_RPS_DRAW)

    async def generate_blackjack_thought_and_dialogue(
        self,
        player_score: int,
        dealer_cards_str: str,
        dealer_score: int,
        action: str,
        status: str,
    ) -> tuple[str, str]:
        """Generate Sherry's inner thought and public dialogue during Blackjack."""
        prompt = (
            f"你正在和玩家玩 21點 (Blackjack)！雪莉你是莊家。\n"
            f"當前狀態：\n"
            f"- 玩家點數：{player_score}\n"
            f"- 雪莉手牌：{dealer_cards_str}（總點數：{dealer_score}）\n"
            f"- 剛發生的動作：{action}\n"
            f"- 對局結果狀態：{status}\n\n"
            "請用繁體中文輸出以下格式：\n"
            "內心推導：(雪莉思考算牌或心理戰的微想法一簡短句)\n"
            "雪莉發言：(雪莉向玩家說的生動對白)"
        )
        try:
            model = config.DEFAULT_MODEL
            res = await self._client.chat(
                model=model,
                system_prompt=SHERRY_GAME_SYSTEM_PROMPT,
                user_prompt=prompt,
            )
            if res and res.strip():
                thought = "本名偵探正在仔細計算點數概率！"
                dialogue = res.strip()
                if "內心推導：" in res and "雪莉發言：" in res:
                    parts = res.split("雪莉發言：")
                    thought = parts[0].replace("內心推導：", "").strip()
                    dialogue = parts[1].strip()
                return thought, dialogue
        except Exception as e:
            logger.warning("Blackjack LLM generation failed: %s", e)

        thought = "目前的牌面有點微妙呢……本名偵探得好好計算一下概率才行！"
        if status == "win":
            dialogue = "哼哼！本名偵探的莊家點數正中目標，這局我贏啦 🍊✨"
        elif status == "lose":
            dialogue = "爆牌了/被你超過了？！可惡，這張牌算你運氣好 哼！✦"
        elif status == "draw":
            dialogue = "竟然平手！看來名偵探與你的運氣不相上下呢 🔍"
        else:
            dialogue = "換你選擇要叫牌 (Hit) 還是停牌 (Stand) 囉！本名偵探看著你呢 ✨"

        return thought, dialogue

    async def generate_minesweeper_hint(
        self,
        board_summary: str,
        revealed_count: int,
        mine_count: int,
    ) -> str:
        """Generate a detective hint for Minesweeper when requested by player."""
        prompt = (
            f"玩家在玩踩地雷，並按下了『求助雪莉 (名偵探推理)』按鈕！\n"
            f"地圖概況：已揭開 {revealed_count} 格，共有 {mine_count} 個地雷。\n"
            f"盤面狀態資訊：\n{board_summary}\n\n"
            "請以雪莉名偵探的身份，給予玩家一句鼓勵、直覺觀察或幽默地雷提示！"
        )
        try:
            model = config.DEFAULT_MODEL
            res = await self._client.chat(
                model=model,
                system_prompt=SHERRY_GAME_SYSTEM_PROMPT,
                user_prompt=prompt,
            )
            if res and res.strip():
                return res.strip()
        except Exception as e:
            logger.warning("Minesweeper LLM generation failed: %s", e)

        return "🔍 名偵探觀察紀錄：注意看那些數字 1 與 2 的交界處！經驗告訴我周圍必定隱藏著線索 🍊✨"

    async def generate_turtle_soup_reply(
        self,
        surface_story: str,
        truth: str,
        player_question: str,
    ) -> str:
        """Answer Turtle Soup question with 'Yes', 'No', or 'Irrelevant'."""
        prompt = (
            f"你正在作為海龜湯 (Situation Puzzle) 的湯主！\n"
            f"湯面故事：{surface_story}\n"
            f"湯底真實答案：{truth}\n"
            f"玩家提問：{player_question}\n\n"
            "請判斷玩家的問題，並用雪莉傲嬌名偵探的語氣回答（明確指出是/否/無關，並加上一句雪莉式點評）！"
        )
        try:
            model = config.DEFAULT_MODEL
            res = await self._client.chat(
                model=model,
                system_prompt=SHERRY_GAME_SYSTEM_PROMPT,
                user_prompt=prompt,
            )
            if res and res.strip():
                return res.strip()
        except Exception as e:
            logger.warning("Turtle Soup LLM failed: %s", e)

        return "🔍 [名偵探判定]：這個問題與湯底無關喔！再換個角度思考看看吧 🍊✨"

    async def generate_20q_next_question(
        self,
        history: list[dict[str, str]],
        q_count: int,
    ) -> str:
        """Generate Akinator 20-Questions question or final guess."""
        history_str = "\n".join(f"Q{item['q_num']}: {item['question']} -> 玩家回答: {item['answer']}" for item in history)
        prompt = (
            f"你正在玩雪莉讀心術 (20 Questions)！已經問了 {q_count - 1} 個問題。\n"
            f"歷史問答：\n{history_str}\n\n"
            f"若是第 {q_count} 題，請用雪莉名偵探口吻提出下一個能縮小範圍的好問題，或者若很有把握直接猜測答案！"
        )
        try:
            model = config.DEFAULT_MODEL
            res = await self._client.chat(
                model=model,
                system_prompt=SHERRY_GAME_SYSTEM_PROMPT,
                user_prompt=prompt,
            )
            if res and res.strip():
                return res.strip()
        except Exception as e:
            logger.warning("20Q LLM failed: %s", e)

        return f"第 {q_count} 題：請問這個角色/物品具備戰鬥能力或特殊超能力嗎？🔍"

    async def generate_escape_room_narration(
        self,
        action: str,
        inventory: list[str],
    ) -> str:
        """Generate Escape Room investigation narration."""
        prompt = (
            f"玩家與雪莉名偵探在密室逃脫！\n"
            f"玩家調查了：{action}\n"
            f"目前手邊道具：{inventory}\n"
            "請以雪莉夥伴的視角描繪調查發現與搞笑/緊張氛圍！"
        )
        try:
            model = config.DEFAULT_MODEL
            res = await self._client.chat(
                model=model,
                system_prompt=SHERRY_GAME_SYSTEM_PROMPT,
                user_prompt=prompt,
            )
            if res and res.strip():
                return res.strip()
        except Exception as e:
            logger.warning("Escape Room LLM failed: %s", e)

        return f"🔍 雪莉湊上前仔細觀察『{action}』……哇！這裡發現了一枚暗藏的銅質鑰匙！🍊✨"

    async def generate_ttt_dialogue(
        self,
        status: str,
    ) -> str:
        """Generate Tic-Tac-Toe reaction."""
        if status == "win":
            return "可惡……竟然被你成功連線了！本名偵探大意了啦 哼！🍊"
        elif status == "lose":
            return "哈哈！三星連線！本名偵探的棋路可是無法預測的！✨✦"
        elif status == "draw":
            dialogue = "和局！看來我們的思維撞在同一條線上呢 🔍"
        else:
            dialogue = "換你下棋囉！名偵探嚴陣以待！✨"
        return dialogue
