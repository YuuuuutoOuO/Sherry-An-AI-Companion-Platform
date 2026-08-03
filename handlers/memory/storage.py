import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, TypedDict
import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# UserFact：帶有重要性與時間戳記的記憶事實單元
# ---------------------------------------------------------------------------
class UserFact(TypedDict):
    fact: str
    importance: int      # 1（低）~ 5（高）；importance >= 4 永久保留
    updated_at: str      # ISO-8601 UTC 時間字串


class UserProfile(TypedDict):
    aliases: list[str]
    facts: list[UserFact]   # 從原本的 list[str] 升級為 list[UserFact]


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
MAX_FACTS_IN_CONTEXT = getattr(settings, "MEMORY_MAX_FACTS_IN_CONTEXT", 5)
# 每位使用者最多保留的事實數（即時萃取）
MAX_FACTS_PER_USER = getattr(settings, "MEMORY_MAX_FACTS_PER_USER", 15)
# importance >= 此值的事實永久保留（不受上限與衰減影響）
PERMANENT_FACT_THRESHOLD = getattr(settings, "MEMORY_PERMANENT_FACT_THRESHOLD", 4)

# Aliases 黑名單詞彙（常見發語詞、無意義詞）
_ALIAS_BLACKLIST = {"靠邀", "未知", "無住址資訊", "哈哈", "喔喔", "幹", "靠北"}


# ---------------------------------------------------------------------------
# 向下相容工具：把舊版純字串 fact 轉換為新版 UserFact 格式
# ---------------------------------------------------------------------------
def _migrate_fact(raw: Any) -> UserFact | None:
    """把舊格式 str 或新格式 dict 統一轉換為 UserFact。"""
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return None
        return UserFact(
            fact=raw,
            importance=3,                               # 舊資料預設中等重要性
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
    elif isinstance(raw, dict):
        fact_str = str(raw.get("fact", "")).strip()
        if not fact_str:
            return None
        return UserFact(
            fact=fact_str,
            importance=int(raw.get("importance", 3)),
            updated_at=str(raw.get("updated_at", datetime.now(timezone.utc).isoformat())),
        )
    return None


def make_fact(fact_text: str, importance: int = 3) -> UserFact:
    """建立一個新的 UserFact 物件（供其他模組快速建立）。"""
    return UserFact(
        fact=fact_text.strip(),
        importance=max(1, min(5, importance)),
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# 存取介面
# ---------------------------------------------------------------------------
def load_user_memory(path: Path) -> UserMemoryStore:
    """載入既有的記憶特徵，作為無伺服器 Document Store；自動遷移舊格式。"""
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
                aliases=list(doc.get("aliases", [])),
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
    """全安全硬覆寫之原子狀態防斷記憶儲存落盤程序。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(store, fh, ensure_ascii=False, indent=2)
        tmp.replace(path)
    except OSError as e:
        logger.error("[Storage] 落存記憶斷區故障: %s", e)


# ---------------------------------------------------------------------------
# 共用消毒與合併工具（cog.py 和 nightly_cog.py 共用，消除重複程式碼）
# ---------------------------------------------------------------------------

def sanitize_aliases(raw_aliases: list[str]) -> list[str]:
    """統一的別名消毒邏輯：排除雪莉、@開頭、佔位符、黑名單、過長字串。"""
    valid: list[str] = []
    for a in raw_aliases:
        a_clean = str(a).strip()
        if not a_clean:
            continue
        if "雪莉" in a_clean:
            continue
        if a_clean.startswith("@"):
            continue
        if "[某群友]" in a_clean or a_clean == "None":
            continue
        if a_clean in _ALIAS_BLACKLIST:
            continue
        if len(a_clean) > 15:
            continue
        valid.append(a_clean)
    return list(dict.fromkeys(valid))[:5]


def sanitize_and_merge_facts(
    existing_facts: list[UserFact],
    new_raw_facts: list[Any],
    max_facts: int = MAX_FACTS_PER_USER,
) -> tuple[list[UserFact], int]:
    """統一的 facts 合併邏輯：解析、消毒、去重、排序、截斷。
    回傳 (最終 facts 列表, 本次新增數量)。"""
    existing_texts = {f["fact"] for f in existing_facts}
    new_count = 0

    for rf in new_raw_facts:
        if isinstance(rf, str):
            fact_text, importance = rf.strip(), 3
        elif isinstance(rf, dict):
            fact_text = str(rf.get("fact", "")).strip()
            importance = int(rf.get("importance", 3))
        else:
            continue
        if not fact_text or fact_text in existing_texts:
            continue
        if fact_text.startswith("@") or "[某群友]" in fact_text:
            logger.debug("[記憶消毒] 丟棄含有 @mention 的 fact: %s", fact_text)
            continue
        existing_facts.append(make_fact(fact_text, importance))
        existing_texts.add(fact_text)
        new_count += 1

    sorted_facts = sorted(existing_facts, key=lambda f: f.get("importance", 3), reverse=True)
    return sorted_facts[:max_facts], new_count


# ---------------------------------------------------------------------------
# 記憶衰減機制 (Memory Decay)
# 由 NightlyDigest 每天凌晨呼叫，對 importance <= 2 的暫時性事實做降級或移除
# ---------------------------------------------------------------------------

def decay_memories(store: UserMemoryStore, reference_time: datetime | None = None) -> int:
    """對所有 importance <= 2 的 facts 降級或移除。回傳移除的數量。
    - imp=1 的暫時狀態直接移除
    - imp=2 降為 imp=1
    - 30 天未更新的 fact 自動降一級
    """
    if reference_time is None:
        reference_time = datetime.now(timezone.utc)
    removed = 0

    for uid, profile in store.items():
        survived: list[UserFact] = []
        for fact in profile.get("facts", []):
            imp = fact.get("importance", 3)

            # 時間衰減檢查：超過 30 天未更新 → 降級
            # 注意：舊資料的 updated_at 可能是 naive datetime（無時區），
            # 需補上 UTC 才能和 reference_time（aware）安全比較。
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
            # imp < 1 → 直接移除
            else:
                removed += 1

        profile["facts"] = survived

    return removed