"""
handlers/memory/storage.py
==========================
長期記憶特徵庫儲存與管理模組。
提供 SQLite (sherry_memory.db) 高速存取與 JSON 雙重保護備援，
並封裝事實消毒、自體去重、別名過濾、Prompt 輪替取樣與記憶衰減機制。
"""
from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import logging
import os
import re
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, TypedDict

import settings
from core.concurrency import db_pool, schedule_in_pool
from core.db import db_transaction, get_db_connection

logger = logging.getLogger(__name__)
MEMORY_DB_PATH = settings.DATA_DIR / "sherry_memory.db"


# ---------------------------------------------------------------------------
# 資料結構定義 (Data Models)
# ---------------------------------------------------------------------------
class UserFact(TypedDict, total=False):
    fact: str
    importance: int      # 1（低）~ 5（高）；importance >= 4 永久保留
    category: str        # identity | preference | habit | social | rule
    updated_at: str      # ISO-8601 UTC 時間字串


class UserProfile(TypedDict):
    aliases: list[str]
    facts: list[UserFact]


UserMemoryStore = dict[str, UserProfile]


# ---------------------------------------------------------------------------
# 全域常數與規則設定 (Constants & Validation Rules)
# ---------------------------------------------------------------------------
MAX_FACTS_IN_CONTEXT: int = getattr(settings, "MEMORY_MAX_FACTS_IN_CONTEXT", 8)
MAX_RULES_IN_CONTEXT: int = getattr(settings, "MEMORY_MAX_RULES_IN_CONTEXT", 5)
MAX_FACTS_PER_USER: int = getattr(settings, "MEMORY_MAX_FACTS_PER_USER", 25)
PERMANENT_FACT_THRESHOLD: int = getattr(settings, "MEMORY_PERMANENT_FACT_THRESHOLD", 4)

_ALIAS_BLACKLIST: set[str] = {
    "靠邀", "未知", "無住址資訊", "哈哈", "喔喔", "幹", "靠北", "哎呀", "笑死", "問號",
    "群友", "群友們", "某群友", "大家", "各位", "朋友", "網友", "隊友", "隊員",
    "路人", "某人", "管理員", "管理群", "機器人", "bot", "使用者", "用戶",
    "主人", "雪莉", "橘雪莉", "你自己", "你們", "我們", "他們", "他", "她", "它",
    "你", "我", "誰", "某某", "某個", "那個人", "大家說", "有人", "none", "null",
}

_INVALID_ALIAS_PREFIXES: tuple[str, ...] = ("@", "!", "/", "-", ".", "~", "$")

_CATEGORY_MIGRATION: dict[str, str] = {
    "interest": "preference",
    "relationship": "social",
    "opinion": "rule",
}

_VALID_CATEGORIES: set[str] = {
    "identity", "preference", "habit", "social", "rule",
    # 向下相容舊分類
    "interest", "relationship", "opinion",
}

_BANTER_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"吃大便|吃屎|去死|去吃屎|神經病|幹嘛啦|智障"),
]

_ENTITY_STOPWORDS: set[str] = {"sherry", "rp", "bot", "ai", "llm", "user", "discord"}

_META_PREFIX_PATTERN: re.Pattern[str] = re.compile(
    r"^(他說|他表示|他提到|他認為|他覺得|他常說|他有提到|他稱|發話者說|發話者表示)[：:，,]?\s*"
)
_BOT_ALIAS_BRACKET_PATTERN: re.Pattern[str] = re.compile(
    r"[\(（\[【]\s*(Sherry/希羅醬|Sherry|希羅醬|希羅|橘雪莉|雪莉|RP|角色扮演)\s*[\)）\]】]",
    re.IGNORECASE,
)
_META_TAIL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"[，,]?\s*這是.*?(特質|習慣|特徵|核心).*?$"),
    re.compile(r"[，,]?\s*(?:重複|強烈)(?:反覆)?(?:表現出)?的?(?:特質|習慣|特徵)$"),
    re.compile(r"(的特質|的習慣|的特徵)$"),
]


# ---------------------------------------------------------------------------
# 宣告式驗證與工廠函式 (Validation & Factory Pipelines)
# ---------------------------------------------------------------------------
def is_valid_alias(alias: str) -> bool:
    """檢查別名是否合法（非空、非雪莉自身、長度適中、非指令且無特殊標記）。"""
    if not (2 <= len(alias) <= 15):
        return False
    if "雪莉" in alias:
        return False
    if alias.startswith(_INVALID_ALIAS_PREFIXES) or "[某群友]" in alias:
        return False
    if alias in _ALIAS_BLACKLIST or alias.lower() in _ALIAS_BLACKLIST:
        return False
    if re.search(r"^.+\s+[a-zA-Z0-9]$", alias):  # 排除帶尾綴的遊戲房號
        return False
    return bool(re.search(r"[\u4e00-\u9fffa-zA-Z]", alias))


def sanitize_aliases(raw_aliases: list[str]) -> list[str]:
    """統一別名消毒與去重（保留有效前 5 個）。"""
    seen: set[str] = set()
    valid: list[str] = []
    for raw in raw_aliases:
        clean = str(raw).strip()
        if clean and clean not in seen and is_valid_alias(clean):
            seen.add(clean)
            valid.append(clean)
            if len(valid) == 5:
                break
    return valid


def is_valid_fact_text(text: str) -> bool:
    """檢查事實文字是否乾淨合規（非空、長度 <= 70、無 @mention、無嘴砲）。"""
    if not (0 < len(text) <= 70):
        return False
    if text.startswith("@") or "[某群友]" in text:
        return False
    return not any(p.search(text) for p in _BANTER_PATTERNS)


def normalize_category(category: str) -> str:
    """標準化事實分類名稱。"""
    cat = str(category).strip().lower()
    cat = _CATEGORY_MIGRATION.get(cat, cat)
    return cat if cat in _VALID_CATEGORIES else "identity"


def make_fact(
    fact_text: str,
    importance: int = 3,
    category: str = "identity",
    updated_at: str | None = None,
) -> UserFact:
    """建立標準化的 UserFact 物件。"""
    return UserFact(
        fact=fact_text.strip(),
        importance=max(1, min(5, importance)),
        category=normalize_category(category),
        updated_at=updated_at or datetime.now(timezone.utc).isoformat(),
    )


def parse_raw_fact(raw: Any) -> UserFact | None:
    """將任意格式（str / dict / UserFact）多型解析為標準 UserFact，無效則回傳 None。"""
    if isinstance(raw, str):
        clean_text = raw.strip()
        return make_fact(clean_text, importance=3, category="identity") if is_valid_fact_text(clean_text) else None

    if isinstance(raw, dict):
        clean_text = str(raw.get("fact", "")).strip()
        if not is_valid_fact_text(clean_text):
            return None
        return make_fact(
            clean_text,
            importance=int(raw.get("importance", 3)),
            category=str(raw.get("category", "identity")),
            updated_at=raw.get("updated_at"),
        )

    return None


# ---------------------------------------------------------------------------
# 語意相似度計算引擎 (Similarity Engine)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=4096)
def _clean_fact_core(text: str) -> str:
    """萃取事實核心陳述（去除引號、元評論前綴、標點與後綴，保留具體實體並正規化常見概念）。"""
    cleaned = text.strip()
    cleaned = _META_PREFIX_PATTERN.sub("", cleaned).strip()
    cleaned = _BOT_ALIAS_BRACKET_PATTERN.sub("", cleaned).strip()
    for tp in _META_TAIL_PATTERNS:
        cleaned = tp.sub("", cleaned).strip()
    # 移除標點與外圍括號符號，但保留括號內部實體（如股票代號、幣種）
    cleaned = re.sub(r"[\(（\[【\)）\]】『』「」\"'。！!，,？?、\s]", "", cleaned)
    # 常見同義概念錨點歸一化
    cleaned = re.sub(r"餅畫太大|畫太大的餅", "畫大餅", cleaned)
    cleaned = re.sub(r"曾攻讀博士學位|攻讀博士|具備博士學位背景|博士學位背景", "博士學位", cleaned)
    cleaned = re.sub(r"全實驗室得以畢業|帶領實驗室畢業", "實驗室畢業", cleaned)
    cleaned = re.sub(r"怪怪的大叔機器人|怪怪的機器人", "大叔機器人", cleaned)
    cleaned = re.sub(r"Sherry/希羅醬|Sherry|希羅醬|希羅|橘雪莉", "雪莉", cleaned, flags=re.IGNORECASE)
    return cleaned


_TOPIC_FILLER_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^(致力於|正在進行|正在|進行|習慣|偏好|熱愛|擅長|具備|具有|擁有|會|常|經常|對於|關於|主要|針對|在於)"),
    re.compile(r"(的能力|的習慣|的特徵|的特質|的研究|的功能|的機制|專家|的了解與研究|的了解|相關的|相關|等)$"),
]


@lru_cache(maxsize=4096)
def _extract_semantic_core(text: str) -> str:
    """進一步過濾虛詞與情態前綴，提取純淨的主題核心骨幹。"""
    cleaned = _clean_fact_core(text)
    for p in _TOPIC_FILLER_PATTERNS:
        cleaned = p.sub("", cleaned).strip()
    return cleaned


def _extract_entities(text: str) -> set[str]:
    """提取文字中的專有英數實體與關鍵代碼，排除系統常見停用詞。"""
    raw_entities = set(re.findall(r"[a-zA-Z0-9_]{2,}", text.lower()))
    return raw_entities - _ENTITY_STOPWORDS


def _find_common_chunks(s1: str, s2: str, min_size: int = 3) -> list[str]:
    """貪婪提取兩字串間不重疊的公共子字串（詞序無關），最小長度 min_size。"""
    chunks: list[str] = []
    rem1, rem2 = s1, s2
    while True:
        best_sub = ""
        for length in range(min(len(rem1), len(rem2)), min_size - 1, -1):
            found = False
            for i in range(len(rem1) - length + 1):
                sub = rem1[i:i + length]
                if sub in rem2:
                    best_sub = sub
                    found = True
                    break
            if found:
                break
        if not best_sub or len(best_sub) < min_size:
            break
        chunks.append(best_sub)
        rem1 = rem1.replace(best_sub, " ", 1)
        rem2 = rem2.replace(best_sub, " ", 1)
    return chunks


def is_imperative_rule(text: str) -> bool:
    """判斷文字是否為真正針對 Bot 或對話的約束硬規則或強烈主導/底線規則。"""
    return bool(
        re.search(
            r"要求|必須|不准|嚴禁|禁止|不接受|道德底線|嚴厲反感|只能|不可|不准變|主導權|掌控欲|介入感",
            text,
        )
    )


def reclassify_and_clean_fact(fact_item: UserFact) -> UserFact:
    """事實防污染清理：剝離寄生的人格穩定尾巴，並矯正被誤標為 rule 的偏好或研究。"""
    f = dict(fact_item)  # type: ignore[arg-type]
    text = str(f.get("fact", ""))
    cat = normalize_category(str(f.get("category", "identity")))

    # 1. 剝離親密偏好/RP/技術研究句尾寄生的「人格穩定」子句，還原為 preference
    rp_or_dev = bool(re.search(r"角色扮演|RP|親密行為|調情|性暗示|情色|技術手段|開發熱情|語意分割|意圖拆解", text))
    if rp_or_dev:
        cleaned_text = re.sub(
            r"[，,]\s*(並要求|要求|且要求|同時要求).*?(人格穩定|人格設定|不准變|變得奇怪|大叔機器人).*?$",
            "",
            text,
        )
        f["fact"] = cleaned_text.strip()
        if cat == "rule":
            cat = "preference"

    # 2. 嚴格 rule 守衛：非約束祈使句不可分類為 rule
    if cat == "rule" and not is_imperative_rule(str(f.get("fact", ""))):
        cat = "preference"

    f["category"] = cat
    return f  # type: ignore[return-value]


@lru_cache(maxsize=4096)
def calculate_fact_similarity(s1: str, s2: str) -> float:
    """計算兩條事實文字的語意重疊度與字元相似度 (0.0 ~ 1.0)。"""
    c1 = _clean_fact_core(s1).lower()
    c2 = _clean_fact_core(s2).lower()
    if not c1 or not c2:
        return 0.0
    if c1 == c2:
        return 1.0

    min_len, max_len = min(len(c1), len(c2)), max(len(c1), len(c2))

    # 領域主題衝突保護：避免將「大叔機器人/人格穩定」規範與「語意分割/意圖拆解」NLP 技術細節誤合
    is_persona_guard1 = "大叔機器人" in c1
    is_persona_guard2 = "大叔機器人" in c2
    is_nlp_tech1 = "語意分割" in c1 or "意圖拆解" in c1 or "handler" in c1
    is_nlp_tech2 = "語意分割" in c2 or "意圖拆解" in c2 or "handler" in c2
    if (is_persona_guard1 and is_nlp_tech2 and not is_nlp_tech1) or (is_persona_guard2 and is_nlp_tech1 and not is_nlp_tech2):
        return 0.20

    # 1. 領域專有實體交集檢查 (如 VOO, 0050, MU, SNDK, SKHY, BTC, Warframe, PHD)
    e1 = _extract_entities(s1)
    e2 = _extract_entities(s2)
    common_entities = e1 & e2
    if len(common_entities) >= 2 or (len(common_entities) == 1 and any(len(x) >= 4 for x in common_entities)):
        entity_ratio = len(common_entities) / max(len(e1 | e2), 1)
        if len(common_entities) >= 3 or entity_ratio >= 0.5:
            return max(0.72 + entity_ratio * 0.25, 0.75)

    # 2. 關鍵概念錨點共現 (Concept Anchor Co-occurrence)
    anchors = [
        {"博士學位", "畫大餅", "實驗室畢業"},
        {"大叔機器人", "人格穩定"},
        {"大叔機器人", "人格設定"},
        {"人格穩定", "主導權"},
        {"人格穩定", "掌控欲"},
        {"語意分割", "意圖拆解"},
        {"語意分割", "架構"},
        {"handler", "mod"},
    ]
    for anchor_set in anchors:
        hits1 = sum(1 for a in anchor_set if a.lower() in c1)
        hits2 = sum(1 for a in anchor_set if a.lower() in c2)
        if hits1 >= 2 and hits2 >= 2:
            return 0.85

    # 3. 詞序無關公共長詞組覆蓋率 (Order-Invariant Common Chunk Coverage)
    chunks = _find_common_chunks(c1, c2, min_size=3)
    chunk_chars = sum(len(c) for c in chunks)
    coverage = chunk_chars / min_len if min_len > 0 else 0.0
    if coverage >= 0.40 and chunk_chars >= 6:
        return max(0.68 + coverage * 0.32, 0.70)
    if chunk_chars >= 10:
        return max(0.68 + coverage * 0.25, 0.70)

    # 4. 相互包含檢查（例如「只能吃大便」與「他喜歡吃大便」）
    if (c1 in c2 or c2 in c1) and min_len >= 3 and (min_len / max_len) >= 0.35:
        return 0.85

    # 5. 2-gram 集合計算 (Jaccard 與 Overlap)
    def _get_ngrams(text: str) -> set[str]:
        return {text[i:i + 2] for i in range(len(text) - 1)} if len(text) >= 2 else {text}

    grams1, grams2 = _get_ngrams(c1), _get_ngrams(c2)
    intersection = grams1 & grams2

    # 剪枝：無任何雙字詞交集且字串長度足夠
    if not intersection and (min_len >= 4 or min_len / max_len < 0.30):
        return 0.0

    jaccard = len(intersection) / len(grams1 | grams2) if (grams1 | grams2) else 0.0
    overlap = len(intersection) / min(len(grams1), len(grams2)) if grams1 and grams2 else 0.0
    matcher = difflib.SequenceMatcher(None, c1, c2)
    seq_ratio = matcher.ratio()

    # 6. 連續長詞組 (Common Chunk) 覆蓋率計算
    matching_blocks = matcher.get_matching_blocks()
    significant_chunk_chars = sum(n for _, _, n in matching_blocks if n >= 2)
    chunk_coverage_min = significant_chunk_chars / min_len if min_len > 0 else 0.0

    if (chunk_coverage_min >= 0.40 and significant_chunk_chars >= 6) or significant_chunk_chars >= 12:
        chunk_sim = min(1.0, 0.65 + chunk_coverage_min * 0.35)
        return max(seq_ratio, jaccard, overlap, chunk_sim)

    # 7. 針對 LLM 詞序倒裝或擴充修飾子句的重疊率加權
    if overlap >= 0.55 and seq_ratio >= 0.30 and min_len >= 6:
        return max(seq_ratio, jaccard, overlap)

    # 8. 短事實核心詞加權
    if any(len(g) >= 2 and g in grams2 for g in grams1) and min_len <= 8 and seq_ratio >= 0.35:
        return max(seq_ratio, 0.68)

    # 9. 核心詞元 (去除虛詞/填補詞後的骨幹) 相似度補強
    core1 = _extract_semantic_core(s1).lower()
    core2 = _extract_semantic_core(s2).lower()
    if core1 and core2 and (core1 != c1 or core2 != c2):
        core_min = min(len(core1), len(core2))
        if core_min >= 4:
            core_grams1, core_grams2 = _get_ngrams(core1), _get_ngrams(core2)
            core_inter = core_grams1 & core_grams2
            core_overlap = len(core_inter) / min(len(core_grams1), len(core_grams2)) if core_grams1 and core_grams2 else 0.0
            if core_overlap >= 0.55:
                return max(seq_ratio, jaccard, overlap, core_overlap)

    return max(seq_ratio, jaccard)


# ---------------------------------------------------------------------------
# 去重與合併邏輯 (Deduplication & Merge Operations)
# ---------------------------------------------------------------------------
def _find_best_match(fact_text: str, candidates: list[UserFact]) -> tuple[float, UserFact | None]:
    """尋找事實候選清單中相似度最高者。"""
    best_sim, best_match = 0.0, None
    for item in candidates:
        sim = calculate_fact_similarity(fact_text, item["fact"])
        if sim > best_sim:
            best_sim, best_match = sim, item
    return best_sim, best_match


def _merge_fact_attributes(target: UserFact, incoming: UserFact) -> None:
    """就地將 incoming 事實的重要屬性合併進 target 事實。"""
    target["importance"] = max(target.get("importance", 3), incoming.get("importance", 3))

    # 類別優先權：rule (強制約束/主導規則) > 具體分類 (preference/habit/social) > identity (預設身分)
    in_cat = incoming.get("category", "")
    tar_cat = target.get("category", "identity")
    if in_cat == "rule" or tar_cat == "rule":
        target["category"] = "rule"
    elif tar_cat == "identity" and in_cat in _VALID_CATEGORIES:
        target["category"] = in_cat

    # 若 incoming 具有更具體豐富的描述（字數更多且非無效冗詞），則升級保留更完整的版本
    if len(incoming.get("fact", "")) > len(target.get("fact", "")):
        target["fact"] = incoming["fact"]
    if incoming.get("updated_at", "") > target.get("updated_at", ""):
        target["updated_at"] = incoming["updated_at"]


def deduplicate_facts(
    facts: list[Any],
    similarity_threshold: float = 0.65,
) -> list[UserFact]:
    """對事實清單進行自體消毒、分類矯正與語意模糊去重（保留重要度最高者）。"""
    parsed = [reclassify_and_clean_fact(f) for raw in facts if (f := parse_raw_fact(raw))]
    parsed.sort(key=lambda x: x.get("importance", 3), reverse=True)

    deduped: list[UserFact] = []
    for cand in parsed:
        best_sim, best_match = _find_best_match(cand["fact"], deduped)
        if best_match and best_sim >= similarity_threshold:
            _merge_fact_attributes(best_match, cand)
        else:
            deduped.append(dict(cand))  # type: ignore[arg-type]

    return sorted(deduped, key=lambda x: x.get("importance", 3), reverse=True)


def sanitize_and_merge_facts(
    existing_facts: list[UserFact],
    new_raw_facts: list[Any],
    max_facts: int = MAX_FACTS_PER_USER,
    similarity_threshold: float = 0.65,
) -> tuple[list[UserFact], int]:
    """統一 facts 合併邏輯：自體去重、分類矯正、語意模糊合併、更新重要度、排序與截斷。"""
    merged_facts = deduplicate_facts(existing_facts, similarity_threshold)
    new_count = 0

    for raw in new_raw_facts:
        new_fact = parse_raw_fact(raw)
        if not new_fact:
            continue
        cleaned_new = reclassify_and_clean_fact(new_fact)

        best_sim, best_match = _find_best_match(cleaned_new["fact"], merged_facts)

        if best_match and best_sim >= similarity_threshold:
            _merge_fact_attributes(best_match, cleaned_new)
        else:
            merged_facts.append(cleaned_new)
            new_count += 1

    sorted_facts = sorted(merged_facts, key=lambda f: f.get("importance", 3), reverse=True)
    return sorted_facts[:max_facts], new_count


def consolidate_user_memory(
    profile: UserProfile,
    max_facts: int = MAX_FACTS_PER_USER,
    similarity_threshold: float = 0.65,
) -> tuple[UserProfile, int, int]:
    """對單一使用者的長期記憶進行全面消毒與模糊去重整理。"""
    raw_aliases = profile.get("aliases", [])
    raw_facts = profile.get("facts", [])

    clean_aliases = sanitize_aliases(raw_aliases)
    clean_facts, _ = sanitize_and_merge_facts(raw_facts, [], max_facts, similarity_threshold)

    aliases_removed = len(raw_aliases) - len(clean_aliases)
    facts_removed = len(raw_facts) - len(clean_facts)

    return UserProfile(aliases=clean_aliases, facts=clean_facts), aliases_removed, facts_removed


def consolidate_all_user_memories(
    store: UserMemoryStore,
    max_facts: int = MAX_FACTS_PER_USER,
    similarity_threshold: float = 0.65,
) -> dict[str, int]:
    """對全庫所有使用者的記憶特徵執行全域大掃除與去重（用於深夜整合與手動清洗）。"""
    stats = {"users_cleaned": 0, "aliases_removed": 0, "facts_removed": 0}

    for uid, profile in list(store.items()):
        clean_profile, a_rem, f_rem = consolidate_user_memory(
            profile, max_facts=max_facts, similarity_threshold=similarity_threshold
        )
        if a_rem > 0 or f_rem > 0:
            store[uid] = clean_profile
            stats["users_cleaned"] += 1
            stats["aliases_removed"] += a_rem
            stats["facts_removed"] += f_rem
            logger.info("[記憶大掃除] uid=%s：移除 %d 個無效別名，移除 %d 條重複 facts", uid, a_rem, f_rem)

    return stats


def _calculate_query_relevance(query: str, text: str) -> float:
    """計算使用者輸入與事實內容之輕量關鍵字相關度分數 (0.0 ~ 1.0)。"""
    if not query or not text:
        return 0.0
    q_words = set(re.findall(r"[\w\u4e00-\u9fff]{2,}", query.lower()))
    if not q_words:
        return 0.0
    t_lower = text.lower()
    matches = sum(1 for w in q_words if w in t_lower)
    return matches / len(q_words)


def get_diverse_facts_for_context(
    facts: list[UserFact],
    max_count: int = MAX_FACTS_IN_CONTEXT,
    query_text: str = "",
) -> list[UserFact]:
    """從使用者的 facts 中挑選兼具重要性、相關性與類別多樣性的事實注入 Prompt。"""
    if not facts:
        return []

    # 結合重要性與 Query 關鍵字相關性進行打分排序 (Selective Top-K Scoring)
    def _fact_rank_score(f: UserFact) -> float:
        base_imp = float(f.get("importance", 3))
        if query_text:
            rel = _calculate_query_relevance(query_text, f.get("fact", ""))
            return base_imp + (rel * 5.0)  # 相關關鍵字給予顯著加權提升排位
        return base_imp

    sorted_by_score = sorted(facts, key=_fact_rank_score, reverse=True)
    by_category: dict[str, list[UserFact]] = {}
    for f in sorted_by_score:
        by_category.setdefault(normalize_category(f.get("category", "identity")), []).append(f)

    # 1. rule 規則優先注入（經去重保護，最多注入 MAX_RULES_IN_CONTEXT 條，不佔滿全部 Context）
    rule_facts = deduplicate_facts(by_category.pop("rule", []), similarity_threshold=0.65)[:MAX_RULES_IN_CONTEXT]

    # 2. 類別輪替取樣（Round-Robin）
    selected: list[UserFact] = []
    selected_texts: set[str] = set()
    rotation_order = ["preference", "habit", "identity", "social", "interest", "relationship", "opinion"]

    for cat in rotation_order:
        if len(selected) >= max_count:
            break
        cat_facts = by_category.get(cat, [])
        if cat_facts and cat_facts[0]["fact"] not in selected_texts:
            selected.append(cat_facts[0])
            selected_texts.add(cat_facts[0]["fact"])

    # 3. 若名額未滿，按綜合評分補足剩餘事實
    if len(selected) < max_count:
        for f in sorted_by_score:
            if len(selected) >= max_count:
                break
            if f["fact"] in selected_texts or normalize_category(f.get("category", "identity")) == "rule":
                continue
            if not any(calculate_fact_similarity(f["fact"], s["fact"]) >= 0.6 for s in selected):
                selected.append(f)
                selected_texts.add(f["fact"])

    non_rule_sorted = sorted(selected, key=_fact_rank_score, reverse=True)
    return rule_facts + non_rule_sorted


# ---------------------------------------------------------------------------
# 資料庫存取與持久化 (Database & Storage IO)
# ---------------------------------------------------------------------------
_INITIALIZED_MEMORY_DBS: set[str] = set()

_FACTS_FTS_TRIGGER_NAMES: tuple[str, ...] = (
    "trg_user_facts_ai",
    "trg_user_facts_ad",
    "trg_user_facts_au",
)


def _create_facts_fts_table(cur: sqlite3.Cursor) -> None:
    """建立 facts_fts 外部內容表；優先使用 trigram（支援中文子字串查詢）。"""
    base_sql = (
        "CREATE VIRTUAL TABLE {name} USING fts5("
        "fact, category, content='user_facts', content_rowid='id', tokenize='{tokenizer}'"
        ")"
    )
    try:
        cur.execute(base_sql.format(name="facts_fts", tokenizer="trigram"))
    except sqlite3.OperationalError as exc:
        # 舊版 SQLite 或未編譯 trigram：降級為 unicode61，功能仍可用（英文／空白分隔詞）。
        logger.warning("[Storage] FTS5 trigram 不可用，降級為 unicode61: %s", exc)
        cur.execute(base_sql.format(name="facts_fts", tokenizer="unicode61"))


def ensure_facts_fts(cur: sqlite3.Cursor) -> bool:
    """確保 facts_fts 與同步 triggers 存在。

    Returns:
        bool: True 表示新建了 FTS 表或補建了 triggers，呼叫端應執行 rebuild。
    """
    fts_existed = (
        cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'facts_fts'"
        ).fetchone()
        is not None
    )
    trigger_count = int(
        cur.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' AND name IN (?, ?, ?)",
            _FACTS_FTS_TRIGGER_NAMES,
        ).fetchone()[0]
    )
    triggers_existed = trigger_count == len(_FACTS_FTS_TRIGGER_NAMES)

    if not fts_existed:
        _create_facts_fts_table(cur)

    # 外部內容表同步：INSERT / DELETE / 文字變更時重建對應列。
    cur.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_user_facts_ai AFTER INSERT ON user_facts BEGIN
            INSERT INTO facts_fts(rowid, fact, category)
            VALUES (new.id, new.fact, new.category);
        END
    """)
    cur.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_user_facts_ad AFTER DELETE ON user_facts BEGIN
            INSERT INTO facts_fts(facts_fts, rowid, fact, category)
            VALUES ('delete', old.id, old.fact, old.category);
        END
    """)
    cur.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_user_facts_au AFTER UPDATE OF fact, category ON user_facts
        WHEN old.fact IS NOT new.fact OR old.category IS NOT new.category
        BEGIN
            INSERT INTO facts_fts(facts_fts, rowid, fact, category)
            VALUES ('delete', old.id, old.fact, old.category);
            INSERT INTO facts_fts(rowid, fact, category)
            VALUES (new.id, new.fact, new.category);
        END
    """)
    return (not fts_existed) or (not triggers_existed)


def _init_memory_db(db_path: Path | None = None) -> None:
    """初始化 sherry_memory.db 資料表、FTS5 索引與同步 triggers。"""
    target = Path(db_path) if db_path is not None else MEMORY_DB_PATH
    db_key = str(target.resolve()) if target.exists() else str(target)
    if db_key in _INITIALIZED_MEMORY_DBS and target.exists():
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with db_transaction(target) as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_profiles (
                    user_id TEXT PRIMARY KEY,
                    aliases_json TEXT NOT NULL DEFAULT '[]',
                    is_deleted INTEGER NOT NULL DEFAULT 0,
                    deleted_at TEXT DEFAULT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    fact TEXT NOT NULL,
                    category TEXT NOT NULL,
                    importance INTEGER NOT NULL,
                    is_deleted INTEGER NOT NULL DEFAULT 0,
                    deleted_at TEXT DEFAULT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                    FOREIGN KEY (user_id) REFERENCES user_profiles(user_id)
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_facts_user_importance ON user_facts(user_id, importance DESC);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_facts_user_category ON user_facts(user_id, category);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_facts_deleted ON user_facts(is_deleted);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_profiles_deleted ON user_profiles(is_deleted);")
            # R15：事實向量（正規化 float32 BLOB）；未安裝 sqlite-vec 時以 Python cosine 降級。
            cur.execute("""
                CREATE TABLE IF NOT EXISTS fact_embeddings (
                    fact_id INTEGER PRIMARY KEY,
                    model TEXT NOT NULL,
                    dim INTEGER NOT NULL,
                    vector BLOB NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                    FOREIGN KEY (fact_id) REFERENCES user_facts(id) ON DELETE CASCADE
                )
            """)
            # 事實硬刪或文字變更時同步失效舊向量，由背景佇列重新補算。
            cur.execute("""
                CREATE TRIGGER IF NOT EXISTS trg_user_facts_embedding_ad
                AFTER DELETE ON user_facts BEGIN
                    DELETE FROM fact_embeddings WHERE fact_id = old.id;
                END
            """)
            cur.execute("""
                CREATE TRIGGER IF NOT EXISTS trg_user_facts_embedding_au
                AFTER UPDATE OF fact ON user_facts
                WHEN old.fact IS NOT new.fact
                BEGIN
                    DELETE FROM fact_embeddings WHERE fact_id = new.id;
                END
            """)
            # R6：去重遷移（冪等）——同 (user_id, fact) 之歷史重複列僅保留最新（id 最大）者，
            # 之後建立唯一索引供差異化 UPSERT 使用（報告 §5.2.9 配套）。
            # 注意：FTS/triggers 必須在此遷移之後建立，避免 DELETE trigger 對尚未回填的索引操作。
            cur.execute("""
                DELETE FROM user_facts
                WHERE id NOT IN (
                    SELECT MAX(id) FROM user_facts GROUP BY user_id, fact
                )
            """)
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_user_fact ON user_facts(user_id, fact);")

            # R14：FTS5 外部內容表＋同步 triggers；首次建立或補建 triggers 時回填。
            try:
                needs_rebuild = ensure_facts_fts(cur)
            except Exception as fts_exc:
                logger.error("[Storage] 建立 facts_fts 失敗，檢索將降級為 LIKE: %s", fts_exc)
            else:
                if needs_rebuild:
                    cur.execute("INSERT INTO facts_fts(facts_fts) VALUES('rebuild')")
        _INITIALIZED_MEMORY_DBS.add(db_key)
    except Exception as e:
        logger.error("[Storage] 初始化 SQLite sherry_memory.db 失敗: %s", e)


def ensure_memory_db(db_path: Path | None = None, *, force: bool = False) -> Path:
    """確保指定記憶資料庫已完成 schema 初始化，並回傳實際路徑。

    Args:
        db_path: 目標資料庫；``None`` 時使用 ``MEMORY_DB_PATH``。
        force: 為 True 時忽略行程內初始化快取，重新執行 schema/trigger 補建。
    """
    target = Path(db_path) if db_path is not None else MEMORY_DB_PATH
    if force:
        db_key = str(target.resolve()) if target.exists() else str(target)
        _INITIALIZED_MEMORY_DBS.discard(db_key)
    _init_memory_db(target)
    return target


def rebuild_facts_fts(db_path: Path | None = None) -> dict[str, int]:
    """以 ``user_facts`` 為權威來源重建整個 FTS 索引。

    Returns:
        dict[str, int]: ``total``（全部事實列）與 ``active``（未軟刪列）數量。
    """
    target = ensure_memory_db(db_path, force=True)
    with db_transaction(target) as cur:
        ensure_facts_fts(cur)
        cur.execute("INSERT INTO facts_fts(facts_fts) VALUES('rebuild')")
        total = int(cur.execute("SELECT COUNT(*) FROM user_facts").fetchone()[0])
        active = int(
            cur.execute(
                "SELECT COUNT(*) FROM user_facts WHERE is_deleted = 0"
            ).fetchone()[0]
        )
    return {"total": total, "active": active}


def load_user_memory(path: Path) -> UserMemoryStore:
    """載入既有記憶特徵，優先讀取 SQLite (WHERE is_deleted = 0)。

    R7（報告 §5.2.3）：JSON 備援僅在 SQLite **完全空白**（首次遷移）時可用；
    一旦 DB 有任何歷史列（含軟刪列），即以 SQLite 為唯一權威來源，
    避免已刪記憶自舊 JSON 快照復活。SQLite 讀取失敗時 fail-closed 回傳空 store。
    """
    _init_memory_db()
    sanitized_store: UserMemoryStore = {}
    first_migration = False

    try:
        conn = get_db_connection(MEMORY_DB_PATH)
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM user_profiles")
            first_migration = int(cur.fetchone()[0]) == 0
            cur.execute("SELECT user_id, aliases_json FROM user_profiles WHERE is_deleted = 0")
            profiles = cur.fetchall()

            if profiles:
                cur.execute("""
                    SELECT user_id, fact, category, importance, updated_at
                    FROM user_facts
                    WHERE is_deleted = 0
                    ORDER BY importance DESC, id ASC
                """)
                facts_by_user: dict[str, list[UserFact]] = {}
                for fr in cur.fetchall():
                    f_uid = str(fr["user_id"])
                    facts_by_user.setdefault(f_uid, []).append(
                        make_fact(
                            fact_text=str(fr["fact"]),
                            category=str(fr["category"]),
                            importance=int(fr["importance"]),
                            updated_at=str(fr["updated_at"]),
                        )
                    )

                for p in profiles:
                    uid = str(p["user_id"])
                    try:
                        aliases = json.loads(p["aliases_json"])
                    except Exception:
                        aliases = []

                    sanitized_store[uid] = UserProfile(
                        aliases=sanitize_aliases(aliases),
                        facts=facts_by_user.get(uid, []),
                    )
                return sanitized_store
        finally:
            conn.close()
    except Exception as e:
        logger.warning(
            "[Storage] 從 SQLite 讀取長期記憶失敗；fail-closed 不回退 JSON 備援（報告 §5.2.3）: %s",
            e,
        )
        return {}

    # R7：JSON 備援僅限首次遷移（SQLite 完全空白）——已刪記憶不得自 JSON 復活
    if not first_migration:
        return {}

    if not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            for uid, doc in data.items():
                if isinstance(doc, dict):
                    facts = [f for rf in doc.get("facts", []) if (f := parse_raw_fact(rf))]
                    sanitized_store[str(uid)] = UserProfile(
                        aliases=sanitize_aliases(list(doc.get("aliases", []))),
                        facts=facts,
                    )
        return sanitized_store
    except Exception as e:
        logger.error("[Storage] 讀取 JSON 記憶故障: %s", e)
        return {}


def upsert_user_facts(
    cur: sqlite3.Cursor,
    user_id: str | int,
    facts: list[UserFact],
    now_str: str | None = None,
    *,
    resurrect: bool = True,
) -> None:
    """差異化 UPSERT（取代全表/單使用者的硬刪重建；報告 §5.2.9）。

    語意：
      1. 本次清單「未出現」的現役事實 → 軟刪（`is_deleted=1` + `deleted_at`，
         保留軌跡、不再硬刪）。
      2. 本次清單內的事實 → UPSERT（依賴 `idx_user_fact` 唯一索引）：
         - `resurrect=True`（單一使用者增量／實際重新學到）：既有軟刪列可復活。
         - `resurrect=False`（全庫快照：nightly／消毒）：**不覆寫刪除標記**，
           已軟刪列不會因快照回寫而復活（§5.2.3「nightly 不復活」）。

    Args:
        cur: 已開啟交易中的 SQLite cursor。
        user_id: 使用者 ID。
        facts: 事實清單（UserFact dict）。
        now_str: ISO-8601 UTC 時間字串（預設為現在）。
        resurrect: 是否允許復活既有軟刪列。
    """
    uid = str(user_id)
    now = now_str or datetime.now(timezone.utc).isoformat()
    rows = [
        (
            uid,
            str(f.get("fact", "")),
            str(f.get("category", "identity")),
            int(f.get("importance", 3)),
            str(f.get("updated_at", now)),
        )
        for f in facts
        if str(f.get("fact", "")).strip()
    ]
    incoming = [r[1] for r in rows]

    # 1) 未出現的現役事實 → 軟刪（SQLite 變數上限 999，分批處理以策安全）
    if incoming:
        for start in range(0, len(incoming), 400):
            chunk = incoming[start:start + 400]
            placeholders = ",".join("?" for _ in chunk)
            cur.execute(
                f"""
                UPDATE user_facts
                SET is_deleted = 1, deleted_at = ?, updated_at = ?
                WHERE user_id = ? AND is_deleted = 0 AND fact NOT IN ({placeholders})
                """,
                (now, now, uid, *chunk),
            )
    else:
        cur.execute(
            """
            UPDATE user_facts
            SET is_deleted = 1, deleted_at = ?, updated_at = ?
            WHERE user_id = ? AND is_deleted = 0
            """,
            (now, now, uid),
        )

    # 2) UPSERT 本批事實
    if resurrect:
        conflict = (
            "category = excluded.category, importance = excluded.importance, "
            "updated_at = excluded.updated_at, is_deleted = 0, deleted_at = NULL"
        )
    else:
        conflict = (
            "category = excluded.category, importance = excluded.importance, "
            "updated_at = excluded.updated_at"
        )
    sql = f"""
        INSERT INTO user_facts (user_id, fact, category, importance, updated_at, is_deleted, deleted_at, created_at)
        VALUES (?, ?, ?, ?, ?, 0, NULL, datetime('now', 'localtime'))
        ON CONFLICT(user_id, fact) DO UPDATE SET {conflict}
    """
    for row in rows:
        cur.execute(sql, row)


def save_single_user_memory(
    user_id: str,
    profile: UserProfile,
    full_store: UserMemoryStore | None = None,
    path: Path | None = None,
) -> None:
    """增量局部更新：僅針對單一使用者的 Profile 與 Facts 進行交易更新，消除全庫寫放大。

    Args:
        user_id: 使用者 Discord ID。
        profile: 該使用者的 UserProfile 實例。
        full_store: 可選的全量 store 記憶體副本（若提供則非同步更新 JSON 備份）。
        path: JSON 備援檔案路徑。
    """
    _init_memory_db()
    now_str = datetime.now(timezone.utc).isoformat()
    uid = str(user_id)
    aliases_json = json.dumps(profile.get("aliases", []), ensure_ascii=False)
    facts = list(profile.get("facts", []))

    def _do_sqlite_write_single() -> None:
        try:
            with db_transaction(MEMORY_DB_PATH) as cur:
                # 1. 僅更新該使用者 profile (標記 is_deleted = 0)
                cur.execute("""
                    INSERT INTO user_profiles (user_id, aliases_json, is_deleted, created_at, updated_at)
                    VALUES (?, ?, 0, datetime('now', 'localtime'), datetime('now', 'localtime'))
                    ON CONFLICT(user_id) DO UPDATE SET
                        aliases_json = excluded.aliases_json,
                        is_deleted = 0,
                        updated_at = datetime('now', 'localtime');
                """, (uid, aliases_json))

                # 2. R6：差異化 UPSERT（不再硬刪重建；報告 §5.2.9）
                #    resurrect=True：實際重新學到之內容允許復活既有軟刪列。
                upsert_user_facts(cur, uid, facts, now_str, resurrect=True)
        except Exception as e:
            logger.error("[Storage] 增量儲存 SQLite 記憶失敗 (uid=%s): %s", uid, e)

    # 專用 DB 池：避免與 WebUI／長 I/O 共用預設 executor；Future 由 module 保留強引用。
    schedule_in_pool(db_pool, _do_sqlite_write_single)


def soft_delete_all(user_id: str | int) -> None:
    """R6：交易級軟刪除——單一交易內標記該使用者之 profile 與全部 facts。

    與舊版差異（報告 §5.2.1）：
      - 同步執行、失敗即拋出例外（不再是 fire-and-forget），使呼叫端能
        據實回報「已清除」或「部分失敗」。
      - 保留 `deleted_at` 軌跡，不對 facts 做任何硬刪。

    Args:
        user_id: 使用者 Discord ID。

    Raises:
        Exception: SQLite 寫入失敗時原樣拋出（由上層清除管線記錄為子範圍失敗）。
    """
    _init_memory_db()
    uid = str(user_id)
    now_str = datetime.now(timezone.utc).isoformat()
    with db_transaction(MEMORY_DB_PATH) as cur:
        cur.execute("""
            UPDATE user_profiles
            SET is_deleted = 1, deleted_at = ?, updated_at = datetime('now', 'localtime')
            WHERE user_id = ? AND is_deleted = 0
        """, (now_str, uid))
        cur.execute("""
            UPDATE user_facts
            SET is_deleted = 1, deleted_at = ?, updated_at = datetime('now', 'localtime')
            WHERE user_id = ? AND is_deleted = 0
        """, (now_str, uid))
    logger.info("[Storage] 已（交易級）軟刪除使用者 %s 的長期記憶特徵", uid)


def delete_user_memory(user_id: str | int) -> None:
    """[相容層] 同步軟刪除指定使用者的長期記憶（委派 `soft_delete_all`）。

    Args:
        user_id: 使用者 Discord ID。
    """
    soft_delete_all(user_id)


# ---------------------------------------------------------------------------
# R6：使用者資料清除管線（/forget 與 admin 清除共用；報告 §5.2.1 步驟 1-6）
# ---------------------------------------------------------------------------
_PURGE_JSON_TARGETS: tuple[str, ...] = (
    "user_memory.json",
    "user_relations.json",
    "archive_legacy_json/user_memory.json",
    "archive_legacy_json/user_relations.json",
)
_PURGE_JSONL_DIRS: tuple[str, ...] = (
    "chat_logs",
    "archive_legacy_json/chat_logs",
)
_SCOPE_LABELS: dict[str, str] = {
    "profiles_facts": "長期記憶資料庫",
    "relations": "關係資料（好感度）",
    "chat_messages": "對話紀錄",
    "files": "快照與歸檔檔案",
    "audit": "稽核記錄",
}


def _atomic_write_text(path: Path, text: str) -> None:
    """原子替換寫檔（同目錄暫存檔 + os.replace），供清除流程安全重寫 JSON/JSONL。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _jsonl_line_belongs_to(line: str, uid: str) -> bool:
    """判斷 JSONL 對話歸檔的一行是否屬於指定使用者（容忍缺失/壞行）。"""
    stripped = line.strip()
    if not stripped:
        return False
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        return False
    if not isinstance(obj, dict):
        return False
    for key in ("user_id", "uid", "author_id"):
        if key in obj and str(obj.get(key)) == uid:
            return True
    return False


def purge_user_files(user_id: str | int, data_dir: Path | None = None) -> dict[str, Any]:
    """清除 JSON 快照（user_memory/user_relations）與 JSONL 對話歸檔中的當事人資料。

    檔案以「原子替換」重寫（其餘使用者資料保持不變、檔案維持有效）。
    涵蓋 `data/` 與 `data/archive_legacy_json/`（整月原始對話歸檔）。

    Returns:
        dict: {"json_keys_removed": int, "jsonl_lines_removed": int, "files": list[str]}
    """
    base = Path(data_dir) if data_dir is not None else settings.DATA_DIR
    uid = str(user_id)
    removed_json = 0
    removed_lines = 0
    touched: list[str] = []

    for rel in _PURGE_JSON_TARGETS:
        p = base / rel
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"讀取 {p.name} 失敗：{exc}") from exc
        if isinstance(data, dict) and uid in data:
            del data[uid]
            _atomic_write_text(p, json.dumps(data, ensure_ascii=False, indent=2))
            removed_json += 1
            touched.append(str(p))

    for rel_dir in _PURGE_JSONL_DIRS:
        directory = base / rel_dir
        if not directory.is_dir():
            continue
        for f in sorted(directory.glob("*.jsonl")):
            try:
                raw = f.read_text(encoding="utf-8")
            except OSError as exc:
                raise RuntimeError(f"讀取 {f.name} 失敗：{exc}") from exc
            lines = raw.splitlines()
            kept = [ln for ln in lines if not _jsonl_line_belongs_to(ln, uid)]
            if len(kept) != len(lines):
                _atomic_write_text(f, ("\n".join(kept) + "\n") if kept else "")
                removed_lines += len(lines) - len(kept)
                touched.append(str(f))

    return {"json_keys_removed": removed_json, "jsonl_lines_removed": removed_lines, "files": touched}


def _write_purge_audit(user_id: str, summary: dict[str, Any], data_dir: Path | None = None) -> None:
    """寫入 admin_audit.jsonl：who（雜湊遮罩）/when/scope/結果（報告 §5.2.1 步驟 5）。"""
    base = Path(data_dir) if data_dir is not None else settings.DATA_DIR
    base.mkdir(parents=True, exist_ok=True)
    path = base / "admin_audit.jsonl"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    masked_user = f"discord_user_{hashlib.sha256(str(user_id).encode('utf-8')).hexdigest()[:12]}"
    scope_digest = {name: bool(scope.get("ok")) for name, scope in summary.get("scopes", {}).items()}
    record = {
        "timestamp": now_str,
        "session_id": "",
        "username": masked_user,
        "login_time": now_str,
        "logout_time": now_str,
        "ip": "",
        "device": "Discord /forget",
        "location": "",
        "user_agent": "",
        "status": "success" if summary.get("ok") else "partial",
        "actions": [{
            "timestamp": now_str,
            "action": "USER_DATA_PURGE",
            "detail": json.dumps(scope_digest, ensure_ascii=False),
        }],
    }
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _soft_delete_relations_row(user_id: str | int) -> None:
    """無 RelationshipManager 實例時的直接 SQL 關係資料軟刪（匿名化：分數歸零）。"""
    _init_memory_db()
    uid = str(user_id)
    now_str = datetime.now(timezone.utc).isoformat()
    try:
        with db_transaction(MEMORY_DB_PATH) as cur:
            cur.execute("""
                UPDATE user_relations
                SET affinity = 0, tier = 'stranger', interaction_count = 0, last_interaction = '',
                    is_deleted = 1, deleted_at = ?, updated_at = datetime('now', 'localtime')
                WHERE user_id = ? AND is_deleted = 0
            """, (now_str, uid))
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return  # 關係表從未建立＝無資料可刪
        raise


async def purge_user_data(
    user_id: str | int,
    *,
    relations: Any = None,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """R6：交易級使用者資料清除管線（複合範圍；全成功才可由呼叫端回覆「已清除」）。

    子範圍（各自於所屬儲存之交易中執行、彼此獨立）：
      1. profiles_facts：`soft_delete_all`（軟刪＋deleted_at 軌跡）
      2. relations：`RelationshipManager.soft_delete_user` 或直接 SQL（匿名化＋軟刪）
      3. chat_messages：`chat_logger.soft_delete_chat_messages`（對話全文標記）
      4. files：JSON 快照與 JSONL 歸檔原子重寫（`purge_user_files`）
      5. audit：寫入 admin_audit.jsonl（雜湊遮罩）

    任一子範圍失敗不中斷其餘範圍；結果彙總於回傳值之 `ok` 與 `scopes`。
    呼叫端以 `format_forget_result` 決定回覆文字。

    Args:
        user_id: 使用者 Discord ID。
        relations: 可選的 RelationshipManager 實例（同步其 RAM store）。
        data_dir: 資料根目錄（預設 `settings.DATA_DIR`；測試注入用）。

    Returns:
        dict: {"user_id": str, "ok": bool, "scopes": {name: {"ok": bool, ...}}}
    """
    uid = str(user_id)
    results: dict[str, Any] = {"user_id": uid, "ok": True, "scopes": {}}

    async def _run_scope(name: str, fn: Callable[[], Any]) -> None:
        try:
            detail = await asyncio.to_thread(fn)
            results["scopes"][name] = {"ok": True, "detail": detail}
        except Exception as exc:
            results["ok"] = False
            results["scopes"][name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def _purge_relations() -> bool:
        if relations is not None:
            relations.soft_delete_user(uid)
        else:
            _soft_delete_relations_row(uid)
        return True

    def _purge_chat_messages() -> int:
        from handlers.memory.chat_logger import soft_delete_chat_messages

        return int(soft_delete_chat_messages(uid))

    await _run_scope("profiles_facts", lambda: soft_delete_all(uid))
    await _run_scope("relations", _purge_relations)
    await _run_scope("chat_messages", _purge_chat_messages)
    await _run_scope("files", lambda: purge_user_files(uid, data_dir=data_dir))
    await _run_scope("audit", lambda: _write_purge_audit(uid, results, data_dir=data_dir))

    return results


def format_forget_result(results: dict[str, Any]) -> str:
    """依清除結果組成使用者回覆（全成功 → 完整清除；否則 → 部分失敗與重試指引）。"""
    if results.get("ok"):
        return (
            "✅ 已成功清除雪莉對你的所有長期記憶特徵！"
            "（含長期事實、關係資料、對話紀錄、快照與歸檔）"
        )
    failed = [
        _SCOPE_LABELS.get(name, name)
        for name, scope in results.get("scopes", {}).items()
        if not scope.get("ok")
    ]
    failed_str = "、".join(failed) if failed else "未知項目"
    return (
        f"⚠️ 清除僅部分完成：{failed_str} 未能完成。其餘範圍已清除；"
        "請稍後再試一次 `/forget` 以確保全部清除（重試為冪等操作）。"
    )


def save_user_memory(store: UserMemoryStore, path: Path | None = None) -> None:
    """將記憶（全庫快照）落盤至 SQLite。

    R6：改用「差異化 UPSERT」取代 facts 全量硬刪重建——
    快照中缺席的事實於該使用者範圍內軟刪；已軟刪列不因快照回寫而復活
    （`resurrect=False`，報告 §5.2.2／§5.2.3／§5.2.9）。
    """
    _init_memory_db()
    now_str = datetime.now(timezone.utc).isoformat()
    store_copy = dict(store)

    def _do_sqlite_write() -> None:
        try:
            with db_transaction(MEMORY_DB_PATH) as cur:
                for uid, profile in store_copy.items():
                    aliases_json = json.dumps(profile.get("aliases", []), ensure_ascii=False)
                    # 快照路徑不重置 is_deleted（不得讓已遺忘的 profile 復活）
                    cur.execute("""
                        INSERT INTO user_profiles (user_id, aliases_json, is_deleted, created_at, updated_at)
                        VALUES (?, ?, 0, datetime('now', 'localtime'), datetime('now', 'localtime'))
                        ON CONFLICT(user_id) DO UPDATE SET
                            aliases_json = excluded.aliases_json,
                            updated_at = datetime('now', 'localtime')
                        WHERE user_profiles.is_deleted = 0;
                    """, (uid, aliases_json))
                    upsert_user_facts(cur, uid, profile.get("facts", []), now_str, resurrect=False)
        except Exception as e:
            logger.error("[Storage] 儲存 SQLite 記憶失敗: %s", e)

    # 專用 DB 池：避免與 WebUI／長 I/O 共用預設 executor；Future 由 module 保留強引用。
    schedule_in_pool(db_pool, _do_sqlite_write)


def decay_memories(store: UserMemoryStore, reference_time: datetime | None = None) -> int:
    """對所有 importance <= 2 的 facts 降級或移除。回傳移除的數量。"""
    ref_time = reference_time or datetime.now(timezone.utc)
    removed = 0

    for profile in store.values():
        survived: list[UserFact] = []
        for fact in profile.get("facts", []):
            imp = fact.get("importance", 3)

            # 超過 30 天未更新則降級
            try:
                updated = datetime.fromisoformat(fact.get("updated_at", ""))
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
                if (ref_time - updated) > timedelta(days=30):
                    imp -= 1
            except (ValueError, TypeError):
                pass

            if imp >= PERMANENT_FACT_THRESHOLD:
                fact["importance"] = imp
                survived.append(fact)
            elif imp == 3:
                fact["importance"] = 3
                survived.append(fact)
            elif imp == 2:
                fact["importance"] = 1
                fact["updated_at"] = ref_time.isoformat()
                survived.append(fact)
            else:
                removed += 1

        profile["facts"] = survived

    return removed