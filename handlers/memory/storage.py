import json
import logging
import re
import difflib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, TypedDict
import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# UserFact：帶有重要性、分類與時間戳記的記憶事實單元
# ---------------------------------------------------------------------------
class UserFact(TypedDict, total=False):
    fact: str
    importance: int      # 1（低）~ 5（高）；importance >= 4 永久保留
    category: str        # identity | interest | habit | relationship | opinion
    updated_at: str      # ISO-8601 UTC 時間字串


class UserProfile(TypedDict):
    aliases: list[str]
    facts: list[UserFact]   # list[UserFact]


class RelationshipFact(TypedDict):
    user_a: str          # Discord UID of the speaker
    user_b: str          # Discord UID (or display name for fuzzy match)
    fact: str            # e.g. "他們是高中同學"
    importance: int      # 1-5
    updated_at: str


# 資料群庫字典架構 (使用 Discord_UID 強力當金鑰認證區別多重假身)
UserMemoryStore = dict[str, UserProfile]

# 跨使用者關係事實存儲
RelationshipMemoryStore = dict[str, list[RelationshipFact]]

# Prompt 注入：每次只取最重要的事實數
MAX_FACTS_IN_CONTEXT: int = getattr(settings, "MEMORY_MAX_FACTS_IN_CONTEXT", 15)
# 每位使用者最多保留的事實數（即時萃取）
MAX_FACTS_PER_USER: int = getattr(settings, "MEMORY_MAX_FACTS_PER_USER", 25)
# importance >= 此值的事實永久保留（不受上限與衰減影響）
PERMANENT_FACT_THRESHOLD: int = getattr(settings, "MEMORY_PERMANENT_FACT_THRESHOLD", 4)

# Aliases 黑名單詞彙（常見發語詞、代稱、泛稱、機器人稱謂）
_ALIAS_BLACKLIST: set[str] = {
    "靠邀", "未知", "無住址資訊", "哈哈", "喔喔", "幹", "靠北", "哎呀", "笑死", "問號",
    "群友", "群友們", "某群友", "大家", "各位", "朋友", "網友", "隊友", "隊員",
    "路人", "某人", "管理員", "管理群", "機器人", "bot", "BOT", "使用者", "用戶",
    "主人", "雪莉", "橘雪莉", "你自己", "你們", "我們", "他們", "他", "她", "它",
    "你", "我", "誰", "某某", "某個", "那個人", "大家說", "有人", "None", "null",
}

# 舊分類 → 新分類向下相容對映字典
_CATEGORY_MIGRATION: dict[str, str] = {
    "interest": "preference",
    "relationship": "social",
    "opinion": "rule",
}

_VALID_CATEGORIES: set[str] = {
    "identity", "preference", "habit", "social", "rule",
    # 向下相容舊分類（遷移期保留）
    "interest", "relationship", "opinion",
}


# ---------------------------------------------------------------------------
# 向下相容工具：把舊版純字串 fact 轉換為新版 UserFact 格式
# ---------------------------------------------------------------------------
def _migrate_fact(raw: Any) -> UserFact | None:
    """把舊格式 str 或新格式 dict 統一轉換為 UserFact。

    Args:
        raw: 原始事實資料（str 或 dict）。

    Returns:
        標準化後的 UserFact 物件，若無效則回傳 None。
    """
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return None
        return UserFact(
            fact=raw,
            importance=3,                               # 舊資料預設中等重要性
            category="identity",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
    elif isinstance(raw, dict):
        fact_str = str(raw.get("fact", "")).strip()
        if not fact_str:
            return None
        cat = str(raw.get("category", "identity")).strip().lower()
        cat = _CATEGORY_MIGRATION.get(cat, cat)
        if cat not in _VALID_CATEGORIES:
            cat = "identity"
        return UserFact(
            fact=fact_str,
            importance=int(raw.get("importance", 3)),
            category=cat,
            updated_at=str(raw.get("updated_at", datetime.now(timezone.utc).isoformat())),
        )
    return None


def make_fact(fact_text: str, importance: int = 3, category: str = "identity") -> UserFact:
    """建立一個新的 UserFact 物件（供其他模組快速建立）。

    Args:
        fact_text: 事實內容文字。
        importance: 重要性數值（1~5）。
        category: 主題分類（identity/preference/habit/social/rule）。

    Returns:
        標準化後的 UserFact 物件。
    """
    cat = category.strip().lower() if isinstance(category, str) else "identity"
    cat = _CATEGORY_MIGRATION.get(cat, cat)
    if cat not in _VALID_CATEGORIES:
        cat = "identity"
    return UserFact(
        fact=fact_text.strip(),
        importance=max(1, min(5, importance)),
        category=cat,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# 存取介面
# ---------------------------------------------------------------------------
def load_user_memory(path: Path) -> UserMemoryStore:
    """載入既有的記憶特徵，作為無伺服器 Document Store；自動遷移舊格式。

    Args:
        path: user_memory.json 儲存路徑。

    Returns:
        UserMemoryStore 記憶字典。
    """
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data: Any = json.load(fh)
        if not isinstance(data, dict):
            return {}

        sanitized_store: UserMemoryStore = {}
        for uid, doc in data.items():
            if not isinstance(doc, dict):
                continue
            raw_facts: list[Any] = doc.get("facts", [])
            migrated_facts: list[UserFact] = []
            for rf in raw_facts:
                mf = _migrate_fact(rf)
                if mf:
                    migrated_facts.append(mf)

            sanitized_store[str(uid)] = UserProfile(
                aliases=sanitize_aliases(list(doc.get("aliases", []))),
                facts=migrated_facts,
            )
        return sanitized_store

    except json.JSONDecodeError:
        logger.debug("[Storage] 長期腦庫為空或不是合法的 JSON，將建立全新記憶。")
        return {}
    except Exception as e:
        logger.error("[Storage] 長期腦庫遭破壞異常: %s", e)
        return {}


def save_user_memory(store: UserMemoryStore, path: Path) -> None:
    """全安全硬覆寫之原子狀態防斷記憶儲存落盤程序。

    Args:
        store: 要儲存的 UserMemoryStore 物件。
        path: 目標寫入路徑。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    store_copy = dict(store)

    def _do_write() -> None:
        try:
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(store_copy, fh, ensure_ascii=False, indent=2)
            tmp.replace(path)
        except OSError as e:
            logger.error("[Storage] 落存記憶斷區故障: %s", e)

    try:
        import asyncio
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _do_write)
    except RuntimeError:
        _do_write()


# ---------------------------------------------------------------------------
# 語意相似度計算與文字預處理
# ---------------------------------------------------------------------------
_META_PREFIX_PATTERN: re.Pattern[str] = re.compile(
    r"^(他說|他表示|他提到|他認為|他覺得|他常說|他有提到|他稱|發話者說|發話者表示)[：:，,]?\s*"
)
_META_BRACKET_PATTERN: re.Pattern[str] = re.compile(r"[\(（\[【].*?[\)）\]】]")
_META_TAIL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"[，,]?\s*這是.*?(特質|習慣|特徵|核心).*?$"),
    re.compile(r"[，,]?\s*(重複|強烈).*?$"),
    re.compile(r"(的特質|的習慣|的特徵)$"),
]


def _clean_fact_core(text: str) -> str:
    """去除 LLM 常見的後綴元評論、引號、贅詞前綴，萃取核心陳述。

    Args:
        text: 原始 fact 字串。

    Returns:
        清洗後的純核心語意字串。
    """
    cleaned = text.strip()
    cleaned = _META_PREFIX_PATTERN.sub("", cleaned).strip()
    cleaned = _META_BRACKET_PATTERN.sub("", cleaned).strip()
    for tp in _META_TAIL_PATTERNS:
        cleaned = tp.sub("", cleaned).strip()
    cleaned = re.sub(r"[『』「」\"'。！!，,？?、\s]", "", cleaned)
    return cleaned


def calculate_fact_similarity(s1: str, s2: str) -> float:
    """計算兩條事實文字的語意重疊度與字元相似度。

    結合元評論去除、字串包含度、SequenceMatcher 與 2-gram Jaccard 比對。

    Args:
        s1: 第一條事實字串。
        s2: 第二條事實字串。

    Returns:
        0.0 ~ 1.0 的相似度分數。
    """
    c1 = _clean_fact_core(s1).lower()
    c2 = _clean_fact_core(s2).lower()
    if not c1 or not c2:
        return 0.0
    if c1 == c2:
        return 1.0

    # 1. 相互包含檢查（例如「只能吃大便」與「他喜歡吃大便」）
    min_len = min(len(c1), len(c2))
    max_len = max(len(c1), len(c2))
    if (c1 in c2 or c2 in c1) and min_len >= 3:
        ratio = min_len / max_len
        if ratio >= 0.4:
            return 0.85

    # 2. SequenceMatcher 序列比對
    seq_ratio = difflib.SequenceMatcher(None, c1, c2).ratio()

    # 3. 2-gram Jaccard 相似度（針對中文關鍵字重疊，如「吃大便」）
    def _get_ngrams(text: str, n: int = 2) -> set[str]:
        return {text[i:i + n] for i in range(len(text) - n + 1)} if len(text) >= n else {text}

    grams1 = _get_ngrams(c1, 2)
    grams2 = _get_ngrams(c2, 2)
    intersection = grams1 & grams2
    union = grams1 | grams2
    jaccard = len(intersection) / len(union) if union else 0.0

    # 若核心短語（>= 3 字元）完全共享，給予加權
    for g in grams1:
        if len(g) >= 2 and g in grams2 and min_len <= 8 and seq_ratio >= 0.4:
            return max(seq_ratio, 0.68)

    return max(seq_ratio, jaccard)


# ---------------------------------------------------------------------------
# 共用消毒與合併工具（cog.py 和 nightly_cog.py 共用，消除重複程式碼）
# ---------------------------------------------------------------------------
def sanitize_aliases(raw_aliases: list[str]) -> list[str]:
    """統一的別名消毒邏輯：排除雪莉、@開頭、佔位符、黑名單、過長/過短/格式異常別名。

    Args:
        raw_aliases: 待過濾的原始別名列表。

    Returns:
        消毒後的有效別名列表（最多 5 個，去重保持順序）。
    """
    valid: list[str] = []
    for a in raw_aliases:
        a_clean = str(a).strip()
        if not a_clean:
            continue
        # 排除雪莉自身
        if "雪莉" in a_clean:
            continue
        # 排除 Mention、佔位符
        if a_clean.startswith("@") or "[某群友]" in a_clean or a_clean.lower() in ("none", "null"):
            continue
        # 排除黑名單（精確比對與小寫比對）
        if a_clean in _ALIAS_BLACKLIST or a_clean.lower() in _ALIAS_BLACKLIST:
            continue
        # 排除過短（< 2 字元）或過長（> 15 字元）
        if len(a_clean) < 2 or len(a_clean) > 15:
            continue
        # 排除指令型字串（例如 !help, /search, -play）
        if a_clean[0] in ("!", "/", "-", ".", "~", "$"):
            continue
        # 排除帶有尾綴單字母/數字的遊戲房號格式（例如「賽爾號 m」、「賽爾號 n」、「lol 2」）
        if re.search(r"^.+\s+[a-zA-Z0-9]$", a_clean):
            continue
        # 排除純數字、純標點
        if not re.search(r"[\u4e00-\u9fffa-zA-Z]", a_clean):
            continue
        valid.append(a_clean)

    return list(dict.fromkeys(valid))[:5]


def sanitize_and_merge_facts(
    existing_facts: list[UserFact],
    new_raw_facts: list[Any],
    max_facts: int = MAX_FACTS_PER_USER,
    similarity_threshold: float = 0.65,
) -> tuple[list[UserFact], int]:
    """統一的 facts 合併邏輯：解析、消毒、語意模糊去重、更新重要度、排序、截斷。

    當新事實與已有事實高度相似（similarity >= threshold）時，更新舊事實的 importance
    與 updated_at，不新增重複事實。

    Args:
        existing_facts: 當前已有的事實清單。
        new_raw_facts: 待合併的原始事實資料（str 或 dict 列表）。
        max_facts: 每位使用者保留的事實上限。
        similarity_threshold: 判定為重複事實的相似度閾值（預設 0.65）。

    Returns:
        (最終 facts 列表, 本次實質新增數量)。
    """
    merged_facts: list[UserFact] = [dict(f) for f in existing_facts]  # type: ignore[misc]
    new_count = 0

    for rf in new_raw_facts:
        if isinstance(rf, str):
            fact_text, importance, category = rf.strip(), 3, "identity"
        elif isinstance(rf, dict):
            fact_text = str(rf.get("fact", "")).strip()
            importance = int(rf.get("importance", 3))
            category = str(rf.get("category", "identity")).strip().lower()
            category = _CATEGORY_MIGRATION.get(category, category)
        else:
            continue

        if not fact_text:
            continue
        # 消毒：過濾提及他人標記
        if fact_text.startswith("@") or "[某群友]" in fact_text:
            logger.debug("[記憶消毒] 丟棄含有 @mention 的 fact: %s", fact_text)
            continue
        # 消毒：過濾過長句子（> 70 字）
        if len(fact_text) > 70:
            logger.debug("[記憶消毒] 丟棄過長 fact: %s", fact_text[:40])
            continue

        # 語意相似度比對已存在的事實
        matched_existing: UserFact | None = None
        highest_sim = 0.0

        for ef in merged_facts:
            sim = calculate_fact_similarity(fact_text, ef["fact"])
            if sim > highest_sim:
                highest_sim = sim
                matched_existing = ef

        if matched_existing and highest_sim >= similarity_threshold:
            # 找到高度相似的既有事實：更新重要性與時間戳記，不重複新增
            matched_existing["importance"] = max(matched_existing.get("importance", 3), importance)
            matched_existing["updated_at"] = datetime.now(timezone.utc).isoformat()
            if category in _VALID_CATEGORIES and matched_existing.get("category") == "identity":
                matched_existing["category"] = category
            logger.debug(
                "[記憶去重] 相似度 %.2f: 新 fact '%s' 聚合至既有 fact '%s'",
                highest_sim, fact_text, matched_existing["fact"],
            )
        else:
            # 全新事實：加入列表
            new_fact = make_fact(fact_text, importance, category)
            merged_facts.append(new_fact)
            new_count += 1

    # 排序：importance 降序
    sorted_facts = sorted(merged_facts, key=lambda f: f.get("importance", 3), reverse=True)
    return sorted_facts[:max_facts], new_count


def get_diverse_facts_for_context(
    facts: list[UserFact],
    max_count: int = MAX_FACTS_IN_CONTEXT,
) -> list[UserFact]:
    """從使用者的 facts 中挑選兼具重要性與類別多樣性的事實注入 Prompt。

    rule 類別（使用者對雪莉的明確要求）全數優先注入，不佔 max_count 名額。
    其餘名額按 preference → habit → identity → social 輪替取樣。

    Args:
        facts: 該使用者的全部事實清單。
        max_count: 非 rule 類最多選取的事實數量。

    Returns:
        精選後的事實清單：rule 類在前，其餘按 importance 降序排列。
    """
    if not facts:
        return []

    # 1. 先按 importance 降序排列
    sorted_by_imp = sorted(facts, key=lambda f: f.get("importance", 3), reverse=True)

    # 2. 依 category 分組
    by_category: dict[str, list[UserFact]] = {}
    for f in sorted_by_imp:
        raw_cat = f.get("category", "identity")
        cat = _CATEGORY_MIGRATION.get(raw_cat, raw_cat)
        by_category.setdefault(cat, []).append(f)

    # 3. rule 類全數優先（不計入 max_count）
    rule_facts: list[UserFact] = by_category.pop("rule", [])

    # 4. 其餘類別輪替取樣（Round-Robin）
    selected: list[UserFact] = []
    selected_texts: set[str] = set()
    rotation_order = [
        "preference", "habit", "identity", "social",
        # 向下相容舊分類名稱（遷移期）
        "interest", "relationship", "opinion",
    ]

    for cat in rotation_order:
        if len(selected) >= max_count:
            break
        cat_facts = by_category.get(cat, [])
        if cat_facts:
            best = cat_facts[0]
            if best["fact"] not in selected_texts:
                selected.append(best)
                selected_texts.add(best["fact"])

    # 5. 若名額未滿，從剩餘 facts 中按 importance 依序補足，同時避免高相似度事實
    if len(selected) < max_count:
        for f in sorted_by_imp:
            if len(selected) >= max_count:
                break
            raw_cat = f.get("category", "identity")
            cat = _CATEGORY_MIGRATION.get(raw_cat, raw_cat)
            if f["fact"] in selected_texts or cat == "rule":
                continue
            # 檢查是否與已選入的事實過度相似
            if not any(calculate_fact_similarity(f["fact"], s["fact"]) >= 0.6 for s in selected):
                selected.append(f)
                selected_texts.add(f["fact"])

    # rule 置頂，其餘依 importance 降序
    non_rule_sorted = sorted(selected, key=lambda f: f.get("importance", 3), reverse=True)
    return rule_facts + non_rule_sorted


# ---------------------------------------------------------------------------
# 記憶衰減機制 (Memory Decay)
# 由 NightlyDigest 每天凌晨呼叫，對 importance <= 2 的暫時性事實做降級或移除
# ---------------------------------------------------------------------------
def decay_memories(store: UserMemoryStore, reference_time: datetime | None = None) -> int:
    """對所有 importance <= 2 的 facts 降級或移除。回傳移除的數量。

    Args:
        store: UserMemoryStore 物件。
        reference_time: 參考時間點（預設為 UTC 當前時間）。

    Returns:
        被移除的暫時性事實數量。
    """
    if reference_time is None:
        reference_time = datetime.now(timezone.utc)
    removed = 0

    for uid, profile in store.items():
        survived: list[UserFact] = []
        for fact in profile.get("facts", []):
            imp = fact.get("importance", 3)

            # 時間衰減檢查：超過 30 天未更新 → 降級
            try:
                updated = datetime.fromisoformat(fact.get("updated_at", ""))
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
                if (reference_time - updated) > timedelta(days=30):
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
                fact["updated_at"] = reference_time.isoformat()
                survived.append(fact)
            elif imp == 1:
                removed += 1
            else:
                removed += 1

        profile["facts"] = survived

    return removed