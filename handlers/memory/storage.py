"""
handlers/memory/storage.py
==========================
長期記憶特徵庫儲存與管理模組。
提供 SQLite (sherry_memory.db) 高速存取與 JSON 雙重保護備援，
並封裝事實消毒、自體去重、別名過濾、Prompt 輪替取樣與記憶衰減機制。
"""
from __future__ import annotations

import difflib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, TypedDict

import settings
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


class RelationshipFact(TypedDict):
    user_a: str          # 發話者 Discord UID
    user_b: str          # 目標使用者 Discord UID 或別名
    fact: str            # 關係描述文字
    importance: int      # 1~5
    updated_at: str


UserMemoryStore = dict[str, UserProfile]
RelationshipMemoryStore = dict[str, list[RelationshipFact]]


# ---------------------------------------------------------------------------
# 全域常數與規則設定 (Constants & Validation Rules)
# ---------------------------------------------------------------------------
MAX_FACTS_IN_CONTEXT: int = getattr(settings, "MEMORY_MAX_FACTS_IN_CONTEXT", 8)
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

_META_PREFIX_PATTERN: re.Pattern[str] = re.compile(
    r"^(他說|他表示|他提到|他認為|他覺得|他常說|他有提到|他稱|發話者說|發話者表示)[：:，,]?\s*"
)
_META_BRACKET_PATTERN: re.Pattern[str] = re.compile(r"[\(（\[【].*?[\)）\]】]")
_META_TAIL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"[，,]?\s*這是.*?(特質|習慣|特徵|核心).*?$"),
    re.compile(r"[，,]?\s*(重複|強烈).*?$"),
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
    """萃取事實核心陳述（去除引號、元評論前綴、標點與後綴）。"""
    cleaned = text.strip()
    cleaned = _META_PREFIX_PATTERN.sub("", cleaned).strip()
    cleaned = _META_BRACKET_PATTERN.sub("", cleaned).strip()
    for tp in _META_TAIL_PATTERNS:
        cleaned = tp.sub("", cleaned).strip()
    return re.sub(r"[『』「」\"'。！!，,？?、\s]", "", cleaned)


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

    # 1. 相互包含檢查（例如「只能吃大便」與「他喜歡吃大便」）
    if (c1 in c2 or c2 in c1) and min_len >= 3 and (min_len / max_len) >= 0.35:
        return 0.85

    # 2. 2-gram 集合計算 (Jaccard 與 Overlap)
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

    # 3. 連續長詞組 (Common Chunk) 覆蓋率計算 (精準辨識長句中的主題核心詞交集)
    matching_blocks = matcher.get_matching_blocks()
    significant_chunk_chars = sum(n for _, _, n in matching_blocks if n >= 2)
    chunk_coverage_min = significant_chunk_chars / min_len if min_len > 0 else 0.0

    # 若短事實中超過 40% 的內容是由長事實詞組構成，或累積共有詞元達到 12 字以上
    if (chunk_coverage_min >= 0.40 and significant_chunk_chars >= 6) or significant_chunk_chars >= 12:
        chunk_sim = min(1.0, 0.65 + chunk_coverage_min * 0.35)
        return max(seq_ratio, jaccard, overlap, chunk_sim)

    # 4. 針對 LLM 詞序倒裝或擴充修飾子句的重疊率加權
    if overlap >= 0.55 and seq_ratio >= 0.30 and min_len >= 6:
        return max(seq_ratio, jaccard, overlap)

    # 5. 短事實核心詞加權
    if any(len(g) >= 2 and g in grams2 for g in grams1) and min_len <= 8 and seq_ratio >= 0.35:
        return max(seq_ratio, 0.68)

    # 6. 核心詞元 (去除虛詞/填補詞後的骨幹) 相似度補強
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

    # 類別優先權：rule (強制約束規則) > 具體分類 (preference/habit/social) > identity (預設身分)
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
    """對事實清單進行自體消毒與語意模糊去重（保留重要度最高者）。"""
    parsed = [f for raw in facts if (f := parse_raw_fact(raw))]
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
    """統一 facts 合併邏輯：自體去重、語意模糊合併、更新重要度、排序與截斷。"""
    merged_facts = deduplicate_facts(existing_facts, similarity_threshold)
    new_count = 0

    for raw in new_raw_facts:
        new_fact = parse_raw_fact(raw)
        if not new_fact:
            continue

        best_sim, best_match = _find_best_match(new_fact["fact"], merged_facts)

        if best_match and best_sim >= similarity_threshold:
            _merge_fact_attributes(best_match, new_fact)
        else:
            merged_facts.append(new_fact)
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

    # 1. rule 規則全數優先注入（去重保護，不計入 max_count）
    rule_facts = deduplicate_facts(by_category.pop("rule", []), similarity_threshold=0.65)

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


def _init_memory_db() -> None:
    """初始化 sherry_memory.db 資料表與索引。"""
    db_key = str(MEMORY_DB_PATH.resolve()) if MEMORY_DB_PATH.exists() else str(MEMORY_DB_PATH)
    if db_key in _INITIALIZED_MEMORY_DBS and MEMORY_DB_PATH.exists():
        return
    try:
        MEMORY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with db_transaction(MEMORY_DB_PATH) as cur:
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
        _INITIALIZED_MEMORY_DBS.add(db_key)
    except Exception as e:
        logger.error("[Storage] 初始化 SQLite sherry_memory.db 失敗: %s", e)


def load_user_memory(path: Path) -> UserMemoryStore:
    """載入既有記憶特徵，優先讀取 SQLite (WHERE is_deleted = 0)，並支援 JSON 備援。"""
    _init_memory_db()
    sanitized_store: UserMemoryStore = {}

    try:
        conn = get_db_connection(MEMORY_DB_PATH)
        try:
            cur = conn.cursor()
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
        logger.warning("[Storage] 從 SQLite 讀取長期記憶失敗，切換 JSON 備援: %s", e)

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

                # 2. 僅清理與重寫該單一使用者的 facts (標記 is_deleted = 0)
                cur.execute("DELETE FROM user_facts WHERE user_id = ?", (uid,))
                if facts:
                    fact_rows = [
                        (
                            uid,
                            str(f.get("fact", "")),
                            str(f.get("category", "identity")),
                            int(f.get("importance", 3)),
                            str(f.get("updated_at", now_str)),
                        )
                        for f in facts
                    ]
                    cur.executemany("""
                        INSERT INTO user_facts (user_id, fact, category, importance, updated_at, is_deleted, created_at)
                        VALUES (?, ?, ?, ?, ?, 0, datetime('now', 'localtime'))
                    """, fact_rows)
        except Exception as e:
            logger.error("[Storage] 增量儲存 SQLite 記憶失敗 (uid=%s): %s", uid, e)

    try:
        import asyncio
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _do_sqlite_write_single)
    except RuntimeError:
        _do_sqlite_write_single()


def delete_user_memory(user_id: str | int) -> None:
    """軟刪除並清理指定使用者的所有長期記憶與別名特徵 (GDPR / 隱私規範)。

    Args:
        user_id: 使用者 Discord ID。
    """
    _init_memory_db()
    uid = str(user_id)

    def _do_sqlite_delete() -> None:
        try:
            with db_transaction(MEMORY_DB_PATH) as cur:
                cur.execute("""
                    UPDATE user_profiles
                    SET is_deleted = 1, updated_at = datetime('now', 'localtime')
                    WHERE user_id = ?
                """, (uid,))
                cur.execute("""
                    UPDATE user_facts
                    SET is_deleted = 1, updated_at = datetime('now', 'localtime')
                    WHERE user_id = ?
                """, (uid,))
            logger.info("[Storage] 成功標記刪除使用者 %s 的長期記憶特徵", uid)
        except Exception as e:
            logger.error("[Storage] 刪除 SQLite 使用者記憶失敗 (uid=%s): %s", uid, e)

    try:
        import asyncio
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _do_sqlite_delete)
    except RuntimeError:
        _do_sqlite_delete()


def save_user_memory(store: UserMemoryStore, path: Path | None = None) -> None:
    """將記憶儲存落盤至 SQLite (WHERE is_deleted = 0)。"""
    _init_memory_db()
    now_str = datetime.now(timezone.utc).isoformat()
    store_copy = dict(store)

    def _do_sqlite_write() -> None:
        try:
            with db_transaction(MEMORY_DB_PATH) as cur:
                profile_rows = []
                fact_rows = []
                for uid, profile in store_copy.items():
                    aliases_json = json.dumps(profile.get("aliases", []), ensure_ascii=False)
                    profile_rows.append((uid, aliases_json))
                    for f in profile.get("facts", []):
                        fact_rows.append((
                            uid,
                            str(f.get("fact", "")),
                            str(f.get("category", "identity")),
                            int(f.get("importance", 3)),
                            str(f.get("updated_at", now_str)),
                        ))

                for uid, a_json in profile_rows:
                    cur.execute("""
                        INSERT INTO user_profiles (user_id, aliases_json, is_deleted, created_at, updated_at)
                        VALUES (?, ?, 0, datetime('now', 'localtime'), datetime('now', 'localtime'))
                        ON CONFLICT(user_id) DO UPDATE SET
                            aliases_json = excluded.aliases_json,
                            is_deleted = 0,
                            updated_at = datetime('now', 'localtime');
                    """, (uid, a_json))

                cur.execute("DELETE FROM user_facts")
                if fact_rows:
                    cur.executemany("""
                        INSERT INTO user_facts (user_id, fact, category, importance, updated_at, is_deleted, created_at)
                        VALUES (?, ?, ?, ?, ?, 0, datetime('now', 'localtime'))
                    """, fact_rows)
        except Exception as e:
            logger.error("[Storage] 儲存 SQLite 記憶失敗: %s", e)

    try:
        import asyncio
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _do_sqlite_write)
    except RuntimeError:
        _do_sqlite_write()


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