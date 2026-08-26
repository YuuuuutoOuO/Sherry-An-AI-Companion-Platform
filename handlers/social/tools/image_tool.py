"""handlers/social/tools/image_tool.py
=====================================
動漫二次元圖搜工具（Safebooru + Danbooru 聯合檢索與智慧動態縮減標籤）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands

import settings
from handlers.bot_brain import BotBrain, InferenceTask
from handlers.social.contracts import ToolCategory, ToolResult
from handlers.social.persona import build_dynamic_persona
from handlers.social.tool_base import BaseTool, ToolResponseMode
from handlers.social.utils import simulate_typing_and_send
from mod.clients import BooruClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 共用 Tag 別名與正則關鍵字常數
# ---------------------------------------------------------------------------
_TAG_ALIASES: dict[str, str] = {
    "huge_boobs": "large_breasts",
    "big_boobs": "large_breasts",
    "medium_breasts": "large_breasts",
    "huge_breasts": "large_breasts",
    "breasts": "large_breasts",
    "oppai": "large_breasts",
    "flat_chest": "small_breasts",
    "pettanko": "small_breasts",
    "loli": "small_breasts",
    "swimwear": "swimsuit",
    "bikini": "swimsuit",
    "nude": "naked",
    "bottomless": "naked",
    "undress": "naked",
}

_CONFLICT_ROOTS: list[list[str]] = [
    ["large_breasts", "small_breasts"],
    ["naked", "swimsuit", "underwear"],
]

_KEYWORD_MAPS: list[tuple[str, str, int]] = [
    (r"(巨乳|大胸|大奶|huge boobs)", "large_breasts", 2),
    (r"(貧乳|平胸|微乳|ちっぱい)", "small_breasts", 2),
    (r"(尻|臀|屁股|お尻)", "ass", 2),
    (r"(裸|沒穿|脫衣|全裸|naked|色)", "naked", 2),
    (r"(泳裝|水着|泳衣|swimsuit|swimwear)", "swimsuit", 2),
    (r"(女僕|女僕裝|maid)", "maid", 2),
    (r"(水手服|sailor uniform)", "sailor_suit", 2),
    (r"(兔女郎|bunny girl)", "bunny_girl", 2),
    (r"(吃東西|吃|eating)", "eating", 3),
    (r"(雪地|雪景|下雪|snow)", "snow", 3),
    (r"(海邊|海灘|beach)", "beach", 3),
]


class ImageSearchTool(BaseTool):
    """二次元圖片搜尋工具，支援兩階段實體解析、分級動態標籤縮減與多圖庫檢索。"""

    @property
    def intent_name(self) -> str:
        return "PIXIV"

    @property
    def llm_description(self) -> str:
        return "明確要求『看圖、給圖、找照片、搜動漫圖』時選擇。"

    @property
    def response_mode(self) -> ToolResponseMode:
        return ToolResponseMode.DIRECT_UI

    def can_handle_by_regex(self, text: str) -> bool:
        # 若包含對現有圖片的描述、指示或分析語境，不應誤判為向外搜圖
        if re.search(r"(這張|圖中|圖裡|圖片裡|看這|圖上|這幅|上傳了|這段話|說明.*圖|圖.*說明)", text):
            return False

        action_regex = (
            r"(搜圖|找圖|找點圖|來點圖|來張圖|找張圖|求圖|發張圖|發點圖|查圖|搜一下圖"
            r"|pixiv|danbooru|safebooru|找本子|來點色圖|來張色圖"
            r"|(給我|我要|來|找|搜)[一幾1-9]?張?[^，。\n]{0,8}(圖|美圖|色圖|桌布|插畫|壁紙)"
            r"|(show|find|get|search)\s+(me\s+)?(a\s+)?(pic|picture|image|wallpaper|illustration|danbooru|pixiv))"
        )
        has_tag_structure = (
            len(re.findall(r"[a-zA-Z0-9]+_[a-zA-Z0-9]+", text)) >= 2
            or (
                bool(re.search(r"[a-zA-Z0-9]+_[a-zA-Z0-9]+", text))
                and bool(re.search(r"\b(1girl|1boy|2girls|solo|cosplay|swimsuit|maid)\b", text, re.IGNORECASE))
            )
        )
        return bool(re.search(action_regex, text, re.IGNORECASE)) or has_tag_structure

    def __init__(self) -> None:
        """初始化 ImageSearchTool，載入標籤字典、元資料與 BooruClient。"""
        dict_path = settings.PIXIV_TAG_PATH
        meta_path = settings.TAG_METADATA_PATH
        self._pixiv_dict: dict[str, str] = {}
        self._tag_metadata: dict[str, dict[str, Any]] = {}

        if dict_path.exists():
            try:
                with open(dict_path, "r", encoding="utf-8") as f:
                    self._pixiv_dict = json.load(f)
                logger.info("ImageSearchTool: 已載入 pixiv_tag.json 共 %d 筆標籤。", len(self._pixiv_dict))
            except Exception as e:
                logger.error("Image 尋圖字庫讀取異常: %s", e)

        if meta_path.exists():
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    self._tag_metadata = json.load(f)
                logger.info("ImageSearchTool: 已載入 tag_metadata.json 共 %d 筆元資料。", len(self._tag_metadata))
            except Exception as e:
                logger.error("Image 標籤元資料讀取異常: %s", e)

        # 依鍵長由長到短排序，利於最長匹配優先
        self._sorted_pixiv_keys: list[str] = sorted(
            self._pixiv_dict.keys(), key=len, reverse=True
        )
        self._booru_client = BooruClient()

    # ------------------------------------------------------------------
    # 標籤萃取、標準化與分級管線
    # ------------------------------------------------------------------

    def _extract_entities_from_llm(self, raw_reply: str) -> dict[str, Any]:
        """解析 LLM 結構化實體萃取回覆。

        Args:
            raw_reply: LLM 的原始回傳文字。

        Returns:
            實體萃取結果字典。
        """
        cleaned = raw_reply.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
            cleaned = re.sub(r"```$", "", cleaned).strip()

        # 嘗試從字串中擷取 JSON 區塊
        json_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if json_match:
            cleaned = json_match.group(0)

        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            logger.debug("無法以 JSON 解析 LLM 實體萃取輸出: %s", raw_reply[:80])

        return {}

    def _resolve_single_tag(self, keyword: str) -> str:
        """將單一中文/英文關鍵詞對齊為標準 Danbooru tag。

        Args:
            keyword: 輸入的詞彙。

        Returns:
            標準 tag 字串，若無對齊則回傳清理後的 snake_case。
        """
        cln = keyword.strip().lower()
        if not cln:
            return ""

        # 1. 字典精確查找
        if cln in self._pixiv_dict:
            resolved = self._pixiv_dict[cln]
            return _TAG_ALIASES.get(resolved, resolved)

        # 2. 移除前後括號注釋後查找
        base_cln = re.split(r"[（(]", cln)[0].strip()
        if base_cln in self._pixiv_dict:
            resolved = self._pixiv_dict[base_cln]
            return _TAG_ALIASES.get(resolved, resolved)

        # 3. 正則關鍵字映射
        for pattern, tag, _ in _KEYWORD_MAPS:
            if re.search(pattern, cln):
                return tag

        # 4. 英文空格換底線
        snake_cln = re.sub(r"\s+", "_", cln)
        if snake_cln in self._pixiv_dict:
            resolved = self._pixiv_dict[snake_cln]
            return _TAG_ALIASES.get(resolved, resolved)

        # 5. 別名表正規化
        return _TAG_ALIASES.get(snake_cln, snake_cln)


    def _resolve_tag_tiers(
        self,
        user_query: str,
        entities: dict[str, Any] | None = None,
    ) -> tuple[list[str], list[str], list[str]]:
        """將使用者輸入與實體萃取結果分流為三級權重標籤。

        - Tier 1 (Core / 不可丟棄): 角色 (Category 4)、作品 (Category 3)
        - Tier 2 (Style / 主要特徵): 服裝、姿勢、身材 (Category 0 - Clothing/Pose)
        - Tier 3 (Context / 次要修飾): 場景、背景、物品、表情 (Category 0 - Scene/Item)

        Args:
            user_query: 使用者原始查詢。
            entities: LLM 萃取的結構化實體。

        Returns:
            (tier1_core, tier2_style, tier3_context)
        """
        tier1: list[str] = []
        tier2: list[str] = []
        tier3: list[str] = []

        scan_query = user_query.lower()

        # 1. 處理 LLM 實體萃取欄位
        if entities:
            char_field = str(entities.get("character", "")).strip()
            series_field = str(entities.get("series", "")).strip()
            clothing_field = str(entities.get("clothing", "")).strip()
            action_field = str(entities.get("action", "")).strip()
            scene_field = str(entities.get("scene", "")).strip()
            extra_field = entities.get("extra_tags", [])

            if char_field:
                # 可能包含多個候選詞或中英雙語 (如 "芙莉蓮 / Frieren")
                for cand in re.split(r"[/,、\s]+", char_field):
                    tag = self._resolve_single_tag(cand)
                    if tag and tag not in tier1 and len(tag) > 1:
                        tier1.append(tag)
                        break

            if series_field:
                for cand in re.split(r"[/,、\s]+", series_field):
                    tag = self._resolve_single_tag(cand)
                    if tag and tag not in tier1 and len(tag) > 1:
                        tier1.append(tag)
                        break

            if clothing_field:
                for cand in re.split(r"[/,、\s]+", clothing_field):
                    tag = self._resolve_single_tag(cand)
                    if tag and tag not in tier2 and len(tag) > 1:
                        tier2.append(tag)

            if action_field:
                for cand in re.split(r"[/,、\s]+", action_field):
                    tag = self._resolve_single_tag(cand)
                    if tag and tag not in tier2 and tag not in tier3 and len(tag) > 1:
                        tier2.append(tag)

            if scene_field:
                for cand in re.split(r"[/,、\s]+", scene_field):
                    tag = self._resolve_single_tag(cand)
                    if tag and tag not in tier3 and len(tag) > 1:
                        tier3.append(tag)

            if isinstance(extra_field, list):
                for item in extra_field:
                    tag = self._resolve_single_tag(str(item))
                    if tag and tag not in tier1 and tag not in tier2 and tag not in tier3:
                        tier3.append(tag)

        # 2. 本地字典最長匹配掃描（補充遺漏標籤）
        for dk in self._sorted_pixiv_keys:
            if len(dk) >= 2 and dk in scan_query:
                tag = self._pixiv_dict[dk]
                # 判定分類層級
                meta = self._tag_metadata.get(tag, {})
                cat = meta.get("category", 0)
                if cat in (3, 4) or "(" in tag:
                    if tag not in tier1:
                        tier1.append(tag)
                elif any(c in tag for c in ["swimsuit", "maid", "dress", "uniform", "breasts", "legs", "eyes", "hair"]):
                    if tag not in tier2:
                        tier2.append(tag)
                else:
                    if tag not in tier3 and tag not in tier2 and tag not in tier1:
                        tier3.append(tag)
                scan_query = scan_query.replace(dk, "")

        # 3. 內建關鍵字正則映射
        _KEYWORD_MAPS: list[tuple[str, str, int]] = [
            (r"(巨乳|大胸|大奶|huge boobs)", "large_breasts", 2),
            (r"(貧乳|平胸|微乳|ちっぱい)", "small_breasts", 2),
            (r"(尻|臀|屁股|お尻)", "ass", 2),
            (r"(裸|沒穿|脫衣|全裸|naked|色)", "naked", 2),
            (r"(泳裝|水着|泳衣|swimsuit)", "swimsuit", 2),
            (r"(女僕|女僕裝|maid)", "maid", 2),
            (r"(水手服|sailor uniform)", "sailor_suit", 2),
            (r"(兔女郎|bunny girl)", "bunny_girl", 2),
        ]
        lower_query = user_query.lower()
        for pattern, tag, tier_num in _KEYWORD_MAPS:
            if re.search(pattern, lower_query):
                if tier_num == 2 and tag not in tier2:
                    tier2.append(tag)
                elif tier_num == 3 and tag not in tier3:
                    tier3.append(tag)

        # 4. 英文 snake_case 萃取
        eng_tags = re.findall(r"[a-zA-Z]+_[a-zA-Z0-9]+", scan_query)
        for et in eng_tags:
            cln_et = _TAG_ALIASES.get(et, et)
            if cln_et not in tier1 and cln_et not in tier2 and cln_et not in tier3:
                tier3.append(cln_et)

        return tier1, tier2, tier3

    def _normalize_tier_tags(self, tags: list[str]) -> list[str]:
        """清理單一列表內的別名、衝突與重複項。"""
        result: list[str] = []
        for raw in tags:
            cln = raw.strip().lower()
            cln = _TAG_ALIASES.get(cln, cln)
            if cln in result:
                continue

            # 衝突檢查
            has_conflict = False
            for group in _CONFLICT_ROOTS:
                if cln in group and any(existing in group for existing in result):
                    has_conflict = True
                    break

            if not has_conflict:
                result.append(cln)
        return result

    # ------------------------------------------------------------------
    # 智慧動態縮減標籤與多圖庫檢索核心
    # ------------------------------------------------------------------

    async def _tiered_booru_search(
        self,
        tier1: list[str],
        tier2: list[str],
        tier3: list[str],
        is_nsfw: bool = False,
    ) -> tuple[str, list[str], bool, str]:
        """智慧分級動態縮減搜尋（永遠優先保護 Tier 1 核心角色名）。

        降級策略：
          1. [Tier 1 + Tier 2 + Tier 3] ➔ Safebooru
          2. [Tier 1 + Tier 2] (丟棄次要場景/道具) ➔ Safebooru
          3. [Tier 1] (僅保留角色) + order:score ➔ Safebooru
          4. [Tier 1] ➔ Danbooru (SFW 模式)
          5. [Tier 1 去除 (series) 後綴] ➔ Danbooru (SFW 模式)
          6. 保底策略 ➔ 1girl/solo

        Returns:
            (image_url, final_used_tags, is_fallback, source_site)
        """
        clean_t1 = self._normalize_tier_tags(tier1)[:2]  # 最多保留 2 個核心 tag
        clean_t2 = self._normalize_tier_tags(tier2)[:2]  # 最多保留 2 個主要特徵
        clean_t3 = self._normalize_tier_tags(tier3)[:2]  # 最多保留 2 個次要修飾

        # 組合全量標籤
        full_query = list(clean_t1 + clean_t2 + clean_t3)
        if not full_query:
            full_query = ["1girl", "naked"] if is_nsfw else ["1girl", "solo"]

        logger.info(
            "⚡ [Booru 標籤分級隊列] Tier1(核心)=%s, Tier2(特徵)=%s, Tier3(修飾)=%s",
            clean_t1, clean_t2, clean_t3,
        )

        # ── 輪次 1: 全標籤檢索 (Safebooru) ─────────────────────────────
        res = await self._booru_client.search_safebooru(full_query)
        if res and res.image_url:
            return res.image_url, full_query, False, "safebooru"

        # ── 輪次 2: 丟棄 Tier 3 (場景/道具/表情) ────────────────────────
        if clean_t3 and (clean_t1 or clean_t2):
            q2 = list(clean_t1 + clean_t2)
            logger.info("⚡ [動態縮減 1/4] 丟棄 Tier 3 修飾詞，嘗試: %s", q2)
            res = await self._booru_client.search_safebooru(q2)
            if res and res.image_url:
                return res.image_url, q2, True, "safebooru"

        # ── 輪次 3: 丟棄 Tier 2，僅保留 Tier 1 (角色) ───────────────────
        if clean_t1:
            q3 = list(clean_t1)
            logger.info("⚡ [動態縮減 2/4] 僅保留核心角色標籤，嘗試 Safebooru: %s", q3)
            res = await self._booru_client.search_safebooru(q3)
            if res and res.image_url:
                return res.image_url, q3, True, "safebooru"

            # ── 輪次 4: 切換至 Danbooru (SFW 模式) ──────────────────────
            logger.info("⚡ [多圖庫聯合 3/4] Safebooru 查無角色，轉向 Danbooru (SFW): %s", q3)
            res_dan = await self._booru_client.search_danbooru(q3, is_nsfw=is_nsfw)
            if res_dan and res_dan.image_url:
                return res_dan.image_url, q3, True, "danbooru"

            # ── 輪次 5: 移除作品後綴 (如 frieren_(sousou_no_frieren) -> frieren)
            stripped_t1 = [re.sub(r"_\(.*?\)$", "", t) for t in clean_t1 if "(" in t]
            if stripped_t1:
                logger.info("⚡ [動態縮減 4/4] 嘗試簡化版角色名: %s", stripped_t1)
                res_dan = await self._booru_client.search_danbooru(stripped_t1, is_nsfw=is_nsfw)
                if res_dan and res_dan.image_url:
                    return res_dan.image_url, stripped_t1, True, "danbooru"

        # ── 輪次 6: 無角色標籤的通用詞逐一退避 ──────────────────────────
        working = list(full_query)
        while len(working) > 1:
            working.pop()
            res = await self._booru_client.search_safebooru(working)
            if res and res.image_url:
                return res.image_url, working, True, "safebooru"

        # ── 輪次 7: 終極保底 ────────────────────────────────────────────
        fallback_tags = ["1girl", "naked"] if is_nsfw else ["1girl", "solo"]
        res = await self._booru_client.search_safebooru(fallback_tags)
        if res and res.image_url:
            return res.image_url, fallback_tags, True, "safebooru"

        return "", [], False, "none"

    # ------------------------------------------------------------------
    # 公開介面：ReAct Agent 快速呼叫
    # ------------------------------------------------------------------

    async def search(self, user_query: str) -> tuple[str, list[str], bool]:
        """ReAct Agent 專用快速搜圖介面（本地解析 + 分級退避）。

        Args:
            user_query: 使用者的搜圖描述。

        Returns:
            (img_url, used_tags, is_fallback)
        """
        is_nsfw = bool(re.search(
            r"(裸|大奶|巨乳|色圖|naked|large_breasts|small_breasts|大胸|貧乳|nude)",
            user_query, re.IGNORECASE,
        ))

        t1, t2, t3 = self._resolve_tag_tiers(user_query, entities=None)
        img_url, used_tags, is_fallback, _source = await self._tiered_booru_search(
            t1, t2, t3, is_nsfw=is_nsfw
        )
        return img_url, used_tags, is_fallback

    # ------------------------------------------------------------------
    # BaseTool.execute（主執行路徑，支援 LLM 實體萃取與 silent 模式）
    # ------------------------------------------------------------------

    async def execute(
        self,
        bot: commands.Bot,
        message: discord.Message,
        user_query: str,
        vibe_snippet: str,
        full_context: str,
        **kwargs: object,
    ) -> ToolResult:
        silent = bool(kwargs.get("silent", False))
        brain_cog = bot.get_cog("BotBrain")
        if not brain_cog:
            return ToolResult.error("BotBrain 未就緒。", is_direct_rendered=True)
        brain: Any = brain_cog

        is_nsfw = bool(re.search(
            r"(裸|大奶|巨乳|色圖|naked|large_breasts|small_breasts|大胸|貧乳|nude)",
            user_query, re.IGNORECASE,
        ))

        extract_prompt = (
            "你是一位專精於二次元動漫角色與 Safebooru/Danbooru 標籤的實體萃取專家。\n"
            "任務：從使用者的找圖話語中萃取出結構化實體。請直接回傳純 JSON 字串，禁止包含任何 Markdown 代碼框或說明文字。\n\n"
            "【輸出格式規格】：\n"
            "{\n"
            '  "character": "角色名稱（中文/日文/英文，若無則留空字串）",\n'
            '  "series": "作品名稱（例如 葬送的芙莉蓮、蔚藍檔案、原神，若無則留空）",\n'
            '  "clothing": "服裝/外觀（例如 泳裝、女僕裝、水手服，若無則留空）",\n'
            '  "action": "動作/姿勢（例如 吃東西、微笑、坐著，若無則留空）",\n'
            '  "scene": "場景/背景（例如 雪地、海邊、教室，若無則留空）",\n'
            '  "extra_tags": ["其他特徵如 白髮、雙馬尾"]\n'
            "}\n\n"
            f"待分析文字：「{user_query}」"
        )

        async def _on_extract_reply(raw_reply: str) -> None:
            entities = self._extract_entities_from_llm(raw_reply)
            t1, t2, t3 = self._resolve_tag_tiers(user_query, entities=entities)

            img_url, used_tags, is_fallback, source_site = await self._tiered_booru_search(
                t1, t2, t3, is_nsfw=is_nsfw
            )

            if not img_url:
                no_result_msg = (
                    f"名偵探在魔法界中用上最高嚴格度掃描了這個詞了(`{', '.join(used_tags[:3])}`)！"
                    f"結果居然完全零下落！！不然換掉不熱門的詞改個別的說法！？"
                )
                if not silent:
                    await simulate_typing_and_send(message, no_result_msg)
                return

            _is_self_search = bool(re.search(
                r"(橘雪莉|雪莉|sherry|tachibana)",
                user_query, re.IGNORECASE
            ))

            title_tags = ", ".join(used_tags).replace("_", " ")
            source_label = "Safebooru" if source_site == "safebooru" else "Danbooru (SFW)"
            embed_msg = discord.Embed(
                title=f"橘偵探捕捉標定收網：【 {title_tags} 】",
                description=f"來源圖庫：`{source_label}`" if is_fallback else None,
                color=discord.Color.brand_green() if not is_fallback else discord.Color.gold(),
            )
            embed_msg.set_image(url=img_url)

            if _is_self_search:
                self_aware_query = f"橘雪莉的圖 {user_query}"
                head_persona, _tail = build_dynamic_persona(
                    self_aware_query, "PIXIV", channel_id=message.channel.id
                )

                async def _on_self_reply(reply: str) -> None:
                    clean_reply = re.sub(r'\(.*?\)|（.*?）|\*.*?\*', '', reply).strip()
                    try:
                        await message.reply(clean_reply, embed=embed_msg, mention_author=False)
                    except discord.HTTPException:
                        pass

                await brain.enqueue_task(InferenceTask(
                    priority=settings.PRIORITY_TAG_RESPONSE,
                    system_prompt=(
                        f"{head_persona}\n\n"
                        "【任務】：有人找了一張妳自己（橘雪莉）的圖，現在把圖發給他。"
                        "請以名偵探的口吻說一句話（一句就好！），表達妳對『自己的圖被人翻出來』這件事的真實反應。"
                        "不要加動作描寫，直接說話。"
                    ),
                    user_prompt=f"找到了一張橘雪莉的圖片，tags: {', '.join(used_tags)}",
                    callback=_on_self_reply,
                    model=settings.PRIMARY_MODEL,
                    task_id=f"image-self-aware-{message.id}",
                ))
            else:
                funny_replies = [
                    "這是你要的...沒出錯吧！？",
                    "啊！找到了找到了！名偵探偷偷先截獲過來一張了，請查驗！！",
                ]
                fallback_replies = [
                    "嗯嘛...其實你指定的全部疊合在一塊無法收束畫面呢。但這是稍微妥協一點抓下的最近畫面囉！",
                ]

                selected_talk = (
                    random.choice(fallback_replies) if is_fallback
                    else random.choice(funny_replies)
                )
                try:
                    await message.reply(selected_talk, embed=embed_msg, mention_author=False)
                except discord.HTTPException:
                    pass

        await brain.enqueue_task(InferenceTask(
            priority=settings.PRIORITY_TAG_RESPONSE,
            system_prompt=extract_prompt,
            user_prompt=user_query,
            callback=_on_extract_reply,
            model=settings.QUICK_MODEL,
            task_id=f"booru-extract-{id(user_query)}",
        ))

        return ToolResult.success("搜圖任務已排程執行中。", is_direct_rendered=True)