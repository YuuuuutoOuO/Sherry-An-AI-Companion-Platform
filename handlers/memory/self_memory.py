"""handlers/memory/self_memory.py
===============================
雪莉專屬自我記憶系統（Self-Memory）— SQLite 持久化層。

補足現有架構僅有「人設（SOUL）」、「使用者記憶（user_memory）」與「世界記憶（world_memory）」之缺口：
  1. self_episodes: 儲存雪莉的第一人稱每日日記、個人生活小劇場、近期衍生興趣與個人想法。
  2. self_reading_log: 儲存雪莉在網路衝浪時看過、分享過的文章、自身獨到觀點與吐槽。

提供去重判斷（防止重複分享同一篇新聞）、近期興趣提取（驅動衝浪引擎搜尋）、
以及供 persona.py 於 Prompt 尾部動態注入的記憶片段（確保靜態 KV Cache 命中率）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import settings
from core.db import get_db_connection, db_transaction

logger = logging.getLogger(__name__)

_TZ_TW = timezone(timedelta(hours=8))
_DEFAULT_DB_PATH: Path = getattr(settings, "SELF_MEMORY_DB_PATH", settings.DATA_DIR / "self_memory.db")
_INITIALIZED_DBS: set[str] = set()

_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "fbclid", "gclid", "igshid", "spm",
}


def normalize_url(url: str) -> str:
    """標準化 URL，移除追蹤參數與結尾斜線，確保跨來源去重一致。"""
    clean = (url or "").strip()
    if not clean:
        return ""
    try:
        parts = urlsplit(clean)
        q_pairs = [
            pair for pair in parse_qsl(parts.query, keep_blank_values=True)
            if pair[0].lower() not in _TRACKING_PARAMS
        ]
        new_query = urlencode(sorted(q_pairs))
        path = parts.path.rstrip("/")
        normalized = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, new_query, ""))
        return normalized or clean.lower().rstrip("/")
    except Exception:
        return clean.rstrip("/").lower()


def _get_db_path(db_path: Path | str | None = None) -> Path:
    if db_path is not None:
        return Path(db_path)
    return getattr(settings, "SELF_MEMORY_DB_PATH", settings.DATA_DIR / "self_memory.db")


def init_self_memory_db(db_path: Path | str | None = None) -> None:
    """初始化 self_memory.db 資料表。"""
    path = _get_db_path(db_path)
    path_key = str(path.resolve())
    if path_key in _INITIALIZED_DBS:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with db_transaction(path) as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS self_episodes (
                episode_id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                tags_json TEXT NOT NULL DEFAULT '[]',
                importance INTEGER NOT NULL DEFAULT 3,
                is_deleted INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_self_episodes_cat ON self_episodes(category, created_at DESC);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_self_episodes_del ON self_episodes(is_deleted);")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS self_reading_log (
                reading_id TEXT PRIMARY KEY,
                url_hash TEXT UNIQUE NOT NULL,
                url TEXT NOT NULL,
                title TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'rss',
                summary TEXT NOT NULL DEFAULT '',
                sherry_opinion TEXT NOT NULL DEFAULT '',
                channel_id TEXT NOT NULL DEFAULT '',
                tagged_user_id TEXT NOT NULL DEFAULT '',
                tagged_user_name TEXT NOT NULL DEFAULT '',
                post_text TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL
            );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_self_reading_hash ON self_reading_log(url_hash);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_self_reading_title ON self_reading_log(title COLLATE NOCASE);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_self_reading_created ON self_reading_log(created_at DESC);")

        # 行為記憶：雪莉「自己做過什麼」的短期情節記憶（播放、搜尋、繪圖…）。
        # 只在使用者主動詢問「你剛剛／你記得」時才檢索注入，平常不佔用 Token。
        cur.execute("""
            CREATE TABLE IF NOT EXISTS self_actions (
                action_id TEXT PRIMARY KEY,
                guild_id TEXT NOT NULL DEFAULT '',
                channel_id TEXT NOT NULL DEFAULT '',
                user_id TEXT NOT NULL DEFAULT '',
                user_name TEXT NOT NULL DEFAULT '',
                tool TEXT NOT NULL,
                verb TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL,
                detail_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'success',
                importance INTEGER NOT NULL DEFAULT 3,
                created_at REAL NOT NULL
            );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_self_actions_channel ON self_actions(channel_id, created_at DESC);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_self_actions_created ON self_actions(created_at DESC);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_self_actions_tool ON self_actions(tool, created_at DESC);")
    _INITIALIZED_DBS.add(path_key)


def hash_url(url: str) -> str:
    """計算 URL 的標準化雜湊值（SHA-256）。"""
    norm = normalize_url(url)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 自我經歷與日記 (self_episodes)
# ---------------------------------------------------------------------------

def add_self_episode(
    category: str,
    content: str,
    tags: list[str] | None = None,
    importance: int = 3,
    episode_id: str | None = None,
    now: float | None = None,
    db_path: Path | str | None = None,
) -> str:
    """新增一筆雪莉自我經歷／日記／興趣／個人想法。

    Args:
        category: 分類 ('diary' | 'interest' | 'reflection' | 'activity')
        content: 第一人稱內容
        tags: 相關關鍵字標籤
        importance: 重要度評級 (1~5)
        episode_id: 自訂 ID（若無則自動生成 UUID）
        now: 時間戳記
        db_path: 資料庫路徑

    Returns:
        str: episode_id
    """
    path = _get_db_path(db_path)
    init_self_memory_db(path)

    eid = episode_id or f"ep-{uuid.uuid4().hex[:12]}"
    ts = float(now if now is not None else time.time())
    tags_json = json.dumps(tags or [], ensure_ascii=False)
    imp = max(1, min(5, int(importance)))

    with db_transaction(path) as cur:
        cur.execute(
            """
            INSERT INTO self_episodes (episode_id, category, content, tags_json, importance, is_deleted, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (eid, category.strip().lower(), content.strip(), tags_json, imp, ts, ts),
        )
    return eid


def list_self_episodes(
    category: str | None = None,
    limit: int = 20,
    offset: int = 0,
    include_deleted: bool = False,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """列出雪莉自我經歷／日記列表。"""
    path = _get_db_path(db_path)
    init_self_memory_db(path)

    conn = get_db_connection(path)
    try:
        query = "SELECT * FROM self_episodes WHERE 1=1"
        params: list[Any] = []
        if not include_deleted:
            query += " AND is_deleted = 0"
        if category:
            query += " AND category = ?"
            params.append(category.strip().lower())
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([max(1, int(limit)), max(0, int(offset))])

        rows = conn.execute(query, params).fetchall()
        result: list[dict[str, Any]] = []
        for r in rows:
            try:
                tags = json.loads(r["tags_json"])
            except Exception:
                tags = []
            result.append({
                "episode_id": str(r["episode_id"]),
                "category": str(r["category"]),
                "content": str(r["content"]),
                "tags": tags if isinstance(tags, list) else [],
                "importance": int(r["importance"]),
                "is_deleted": bool(r["is_deleted"]),
                "created_at": float(r["created_at"]),
                "updated_at": float(r["updated_at"]),
            })
        return result
    finally:
        conn.close()


def delete_self_episode(
    episode_id: str,
    hard_delete: bool = False,
    db_path: Path | str | None = None,
) -> bool:
    """刪除自我經歷（預設軟刪除）。"""
    path = _get_db_path(db_path)
    init_self_memory_db(path)

    with db_transaction(path) as cur:
        if hard_delete:
            cur.execute("DELETE FROM self_episodes WHERE episode_id = ?", (episode_id,))
        else:
            cur.execute(
                "UPDATE self_episodes SET is_deleted = 1, updated_at = ? WHERE episode_id = ?",
                (time.time(), episode_id),
            )
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# 衝浪閱讀紀錄 (self_reading_log)
# ---------------------------------------------------------------------------

def record_reading_item(
    url: str,
    title: str,
    source: str = "rss",
    summary: str = "",
    sherry_opinion: str = "",
    channel_id: str | int = "",
    tagged_user_id: str | int = "",
    tagged_user_name: str = "",
    post_text: str = "",
    reading_id: str | None = None,
    now: float | None = None,
    db_path: Path | str | None = None,
) -> str | None:
    """記錄雪莉讀過或分享的文章與觀點。若 URL 雜湊已存在則略過不重複插入。

    Returns:
        str | None: 成功建立傳回 reading_id，已存在則傳回 None。
    """
    path = _get_db_path(db_path)
    init_self_memory_db(path)

    clean_url = (url or "").strip()
    clean_title = (title or "").strip()
    if not clean_url:
        return None

    # 同時比對 URL 雜湊與標題防重複
    if is_article_seen(clean_url, title=clean_title, db_path=path):
        return None

    u_hash = hash_url(clean_url)
    rid = reading_id or f"read-{uuid.uuid4().hex[:12]}"
    ts = float(now if now is not None else time.time())

    with db_transaction(path) as cur:
        cur.execute(
            """
            INSERT INTO self_reading_log (
                reading_id, url_hash, url, title, source, summary,
                sherry_opinion, channel_id, tagged_user_id, tagged_user_name,
                post_text, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rid,
                u_hash,
                clean_url,
                clean_title,
                (source or "rss").strip().lower(),
                (summary or "").strip(),
                (sherry_opinion or "").strip(),
                str(channel_id or ""),
                str(tagged_user_id or ""),
                str(tagged_user_name or ""),
                (post_text or "").strip(),
                ts,
            ),
        )
    return rid


def is_article_seen(
    url: str,
    title: str = "",
    db_path: Path | str | None = None,
) -> bool:
    """檢查該 URL 或標題是否已被雪莉閱讀或分享過（防重複發文防呆）。"""
    path = _get_db_path(db_path)
    init_self_memory_db(path)

    clean_url = (url or "").strip()
    clean_title = (title or "").strip()
    if not clean_url and not clean_title:
        return False

    conn = get_db_connection(path)
    try:
        if clean_url:
            u_hash = hash_url(clean_url)
            row = conn.execute(
                "SELECT reading_id FROM self_reading_log WHERE url_hash = ?", (u_hash,)
            ).fetchone()
            if row is not None:
                return True

        if clean_title:
            row_title = conn.execute(
                "SELECT reading_id FROM self_reading_log WHERE title = ? COLLATE NOCASE LIMIT 1",
                (clean_title,),
            ).fetchone()
            if row_title is not None:
                return True
        return False
    finally:
        conn.close()


def list_reading_logs(
    limit: int = 20,
    offset: int = 0,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """列出雪莉衝浪閱讀紀錄。"""
    path = _get_db_path(db_path)
    init_self_memory_db(path)

    conn = get_db_connection(path)
    try:
        rows = conn.execute(
            "SELECT * FROM self_reading_log ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (max(1, int(limit)), max(0, int(offset))),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for r in rows:
            result.append({
                "reading_id": str(r["reading_id"]),
                "url": str(r["url"]),
                "title": str(r["title"]),
                "source": str(r["source"]),
                "summary": str(r["summary"]),
                "sherry_opinion": str(r["sherry_opinion"]),
                "channel_id": str(r["channel_id"]),
                "tagged_user_id": str(r["tagged_user_id"]),
                "tagged_user_name": str(r["tagged_user_name"]),
                "post_text": str(r["post_text"]),
                "created_at": float(r["created_at"]),
            })
        return result
    finally:
        conn.close()


def delete_reading_log(
    reading_id: str,
    db_path: Path | str | None = None,
) -> bool:
    """刪除閱讀紀錄。"""
    path = _get_db_path(db_path)
    init_self_memory_db(path)

    with db_transaction(path) as cur:
        cur.execute("DELETE FROM self_reading_log WHERE reading_id = ?", (reading_id,))
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# 行為記憶 (self_actions)：雪莉「自己做過什麼」的短期情節記憶
# ---------------------------------------------------------------------------

# 只有使用者「主動問起雪莉自己做過什麼」時才觸發檢索，避免每輪都吃 Token。
_SELF_ACTION_QUERY_RE = re.compile(
    r"(你(剛剛|剛才|剛|昨天|前天|今天|之前|上次|先前)|"
    r"(剛剛|剛才|昨天|前天).{0,8}(歌|首|畫|圖|查|搜|放|播|點|做|用)|"
    r"還記得|記得嗎|記得我|記不記得|"
    r"你(播|放|點|唱|畫|查|搜|找|傳|給|開|關|做|弄|用|執行)(了|過|的|什麼|哪|這|那)|"
    r"(什麼|哪)(歌|首|張|部|個)|"
    r"(播|放|畫|查|搜|找)了什麼|"
    r"上次那|之前那)",
    re.IGNORECASE,
)


def _humanize_ago(ts: float, now: float | None = None) -> str:
    """將時間戳轉成人類可讀的相對時間（剛剛 / N 分鐘前 / N 小時前 / N 天前）。"""
    delta = max(0.0, float(now if now is not None else time.time()) - float(ts))
    if delta < 60:
        return "剛剛"
    if delta < 3600:
        return f"{int(delta // 60)} 分鐘前"
    if delta < 86400:
        return f"{int(delta // 3600)} 小時前"
    return f"{int(delta // 86400)} 天前"


def record_self_action(
    tool: str,
    summary: str,
    *,
    verb: str = "",
    guild_id: str | int = "",
    channel_id: str | int = "",
    user_id: str | int = "",
    user_name: str = "",
    detail: dict[str, Any] | None = None,
    status: str = "success",
    importance: int = 3,
    action_id: str | None = None,
    now: float | None = None,
    db_path: Path | str | None = None,
) -> str | None:
    """記錄一筆雪莉自己執行過的行為（播放、搜尋、繪圖、排程…）。

    Args:
        tool: 工具名稱（MUSIC / SEARCH / DRAW …）。
        summary: 第一人稱可讀摘要，例如「播放了《千本櫻》」。
        verb: 動作動詞（可選，供 UI 分類）。
        guild_id: Discord 伺服器 ID。
        channel_id: Discord 頻道 ID。
        user_id: 觸發者 ID。
        user_name: 觸發者顯示名稱。
        detail: 結構化細節（歌名、URL、查詢字串…）。
        status: success / error。
        importance: 重要度 1~5。
        action_id: 自訂 ID（若無則自動生成）。
        now: 時間戳。
        db_path: 資料庫路徑。

    Returns:
        str | None: 成功回傳 action_id，失敗或未啟用回傳 None。
    """
    if not getattr(settings, "SELF_ACTION_MEMORY_ENABLED", True):
        return None

    clean_summary = re.sub(r"\s+", " ", str(summary or "")).strip()
    if not clean_summary:
        return None

    path = _get_db_path(db_path)
    init_self_memory_db(path)

    aid = action_id or f"act-{uuid.uuid4().hex[:12]}"
    ts = float(now if now is not None else time.time())
    imp = max(1, min(5, int(importance)))
    detail_json = json.dumps(detail or {}, ensure_ascii=False)
    tool_key = str(tool or "").strip().upper()[:32]

    # 去重：20 秒內同工具、同摘要不重複寫入（工具層與音樂層可能同時記錄同一首歌）
    if action_id is None:
        try:
            conn = get_db_connection(path)
            try:
                row = conn.execute(
                    "SELECT action_id FROM self_actions "
                    "WHERE tool = ? AND summary = ? AND created_at >= ? LIMIT 1",
                    (tool_key, clean_summary[:300], ts - 20.0),
                ).fetchone()
            finally:
                conn.close()
            if row is not None:
                return str(row["action_id"])
        except sqlite3.Error:
            pass

    try:
        with db_transaction(path) as cur:
            cur.execute(
                """
                INSERT INTO self_actions (
                    action_id, guild_id, channel_id, user_id, user_name,
                    tool, verb, summary, detail_json, status, importance, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    aid,
                    str(guild_id or ""),
                    str(channel_id or ""),
                    str(user_id or ""),
                    str(user_name or "")[:80],
                    tool_key,
                    str(verb or "")[:32],
                    clean_summary[:300],
                    detail_json,
                    str(status or "success")[:16],
                    imp,
                    ts,
                ),
            )
    except sqlite3.Error as exc:
        logger.warning("self_memory: 寫入行為記憶失敗: %s", exc)
        return None
    return aid


def list_self_actions(
    limit: int = 20,
    *,
    channel_id: str | int | None = None,
    tool: str | None = None,
    since: float | None = None,
    include_errors: bool = True,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """列出雪莉近期的行為記憶（預設依時間新到舊）。"""
    path = _get_db_path(db_path)
    init_self_memory_db(path)

    query = "SELECT * FROM self_actions WHERE 1=1"
    params: list[Any] = []
    if channel_id is not None and str(channel_id) != "":
        query += " AND channel_id = ?"
        params.append(str(channel_id))
    if tool:
        query += " AND tool = ?"
        params.append(str(tool).strip().upper())
    if since is not None:
        query += " AND created_at >= ?"
        params.append(float(since))
    if not include_errors:
        query += " AND status = 'success'"
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, int(limit)))

    conn = get_db_connection(path)
    try:
        rows = conn.execute(query, params).fetchall()
        result: list[dict[str, Any]] = []
        for r in rows:
            try:
                detail = json.loads(r["detail_json"])
            except Exception:
                detail = {}
            result.append({
                "action_id": str(r["action_id"]),
                "guild_id": str(r["guild_id"]),
                "channel_id": str(r["channel_id"]),
                "user_id": str(r["user_id"]),
                "user_name": str(r["user_name"]),
                "tool": str(r["tool"]),
                "verb": str(r["verb"]),
                "summary": str(r["summary"]),
                "detail": detail if isinstance(detail, dict) else {},
                "status": str(r["status"]),
                "importance": int(r["importance"]),
                "created_at": float(r["created_at"]),
            })
        return result
    finally:
        conn.close()


def purge_old_self_actions(
    days: int | None = None,
    db_path: Path | str | None = None,
) -> int:
    """清除超過保留期限的行為記憶，避免資料庫無限增長。"""
    retention = int(days if days is not None else getattr(settings, "SELF_ACTION_RETENTION_DAYS", 14))
    cutoff = time.time() - max(1, retention) * 86400
    path = _get_db_path(db_path)
    init_self_memory_db(path)
    try:
        with db_transaction(path) as cur:
            cur.execute("DELETE FROM self_actions WHERE created_at < ?", (cutoff,))
            return int(cur.rowcount or 0)
    except sqlite3.Error as exc:
        logger.warning("self_memory: 清理行為記憶失敗: %s", exc)
        return 0


def get_action_memory_snippet(
    query_text: str = "",
    *,
    channel_id: str | int | None = None,
    limit: int | None = None,
    db_path: Path | str | None = None,
) -> str:
    """取得可注入 Prompt 的「雪莉行為記憶」片段。

    設計原則（Token 預算）：
      - 平常閒聊完全不觸發，回傳空字串（0 Token）。
      - 只有使用者問「你剛剛播什麼歌／你記得／你昨天查了什麼」等自我指涉問題時，
        才檢索最近 3~5 筆行為並注入，預算上限由 settings 控制。
    """
    if not getattr(settings, "SELF_ACTION_MEMORY_ENABLED", True):
        return ""
    if not query_text or not _SELF_ACTION_QUERY_RE.search(str(query_text)):
        return ""

    max_items = int(limit if limit is not None else getattr(settings, "SELF_ACTION_SNIPPET_LIMIT", 5))
    max_items = max(1, min(8, max_items))
    retention_days = float(getattr(settings, "SELF_ACTION_RETENTION_DAYS", 14))
    since = time.time() - retention_days * 86400

    # 先找同頻道，找不到再退回全域（例如重啟後或跨頻道詢問）
    actions = list_self_actions(
        limit=max_items * 4, channel_id=channel_id, since=since, db_path=db_path
    )
    if not actions:
        actions = list_self_actions(limit=max_items * 4, since=since, db_path=db_path)
    if not actions:
        return ""

    # 以關鍵字重疊度排序；同分時保留較新的行為
    clean_query = str(query_text).strip()[:120].lower()
    q_tokens: set[str] = set(re.findall(r"[A-Za-z0-9_]{2,}", clean_query))
    cjk_chars = "".join(re.findall(r"[\u4e00-\u9fff]", clean_query))
    for n in (4, 3, 2):
        for i in range(len(cjk_chars) - n + 1):
            q_tokens.add(cjk_chars[i:i + n])

    def _score(action: dict[str, Any]) -> int:
        text = f"{action.get('summary', '')} {action.get('detail', {})}".lower()
        return sum(1 for tok in q_tokens if tok and tok in text)

    actions.sort(key=lambda a: (_score(a), a.get("created_at", 0.0)), reverse=True)

    lines: list[str] = []
    seen: set[str] = set()
    now = time.time()
    for action in actions:
        summary = str(action.get("summary", "")).strip()
        if not summary or summary in seen:
            continue
        seen.add(summary)
        lines.append(f"- {_humanize_ago(action.get('created_at', now), now)}：{summary}")
        if len(lines) >= max_items:
            break

    if not lines:
        return ""

    text = (
        "🎬【妳自己的行為記憶】（這些都是妳真的做過的事，請自然地承認與回應，別說不知道）\n"
        + "\n".join(lines)
    )
    max_chars = int(getattr(settings, "SELF_ACTION_SNIPPET_MAX_CHARS", 400))
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text


# ---------------------------------------------------------------------------
# 衝浪檢索與 Persona 注入支援
# ---------------------------------------------------------------------------

def get_active_interests(
    limit: int = 5,
    db_path: Path | str | None = None,
) -> list[str]:
    """取出雪莉最近的個人興趣或關注話題關鍵字，供衝浪引擎搜尋使用。"""
    path = _get_db_path(db_path)
    init_self_memory_db(path)

    conn = get_db_connection(path)
    try:
        rows = conn.execute(
            """
            SELECT content, tags_json FROM self_episodes
            WHERE category = 'interest' AND is_deleted = 0
            ORDER BY created_at DESC LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        interests: list[str] = []
        for r in rows:
            c = str(r["content"]).strip()
            if c and c not in interests:
                interests.append(c)
            try:
                tags = json.loads(r["tags_json"])
                if isinstance(tags, list):
                    for t in tags:
                        t_str = str(t).strip()
                        if t_str and t_str not in interests:
                            interests.append(t_str)
            except Exception:
                pass
        return interests[:limit]
    finally:
        conn.close()


def get_self_memory_snippet(
    query_text: str = "",
    limit: int = 5,
    db_path: Path | str | None = None,
) -> str:
    """格式化雪莉自我記憶片段，供 persona.py 於 tail_lore 尾部注入。

    支援話題相關性排序：若 query_text 有傳入當前對話關鍵字，將優先召回相關的看過文章與日記！
    完全置於動態尾部，不破壞 head_persona 靜態前綴快取 (Prefix Caching)。
    """
    path = _get_db_path(db_path)
    init_self_memory_db(path)

    # 若有 query_text，擴大載入候選池進行關鍵字召回排序
    fetch_limit = max(limit * 5, 30) if query_text and query_text.strip() else limit
    episodes = list_self_episodes(limit=fetch_limit, db_path=path)
    readings = list_reading_logs(limit=fetch_limit, db_path=path)

    if not episodes and not readings:
        return ""

    # 話題相關性評分排序（只擷取前 120 字關鍵字，避免長對話全文計算佔用 CPU）
    clean_query = (query_text or "").strip()[:120].lower()
    if clean_query:
        import re
        q_tokens = set(re.findall(r"[A-Za-z0-9_]{2,}", clean_query))
        cjk_chars = "".join(re.findall(r"[\u4e00-\u9fff]", clean_query))
        for n in (4, 3, 2):
            for i in range(len(cjk_chars) - n + 1):
                q_tokens.add(cjk_chars[i:i+n])

        def _score_text(text: str) -> int:
            if not text or not q_tokens:
                return 0
            t_low = text.lower()
            return sum(1 for tok in q_tokens if tok in t_low)

        episodes.sort(key=lambda ep: _score_text(f"{ep['content']} {' '.join(ep.get('tags', []))}"), reverse=True)
        readings.sort(key=lambda rd: _score_text(f"{rd['title']} {rd['summary']} {rd['sherry_opinion']}"), reverse=True)

    parts: list[str] = []

    if episodes:
        ep_lines = []
        for ep in episodes[:max(1, min(limit, 4))]:
            dt = datetime.fromtimestamp(ep["created_at"], _TZ_TW).strftime("%m/%d")
            cat_name = {
                "diary": "日記",
                "interest": "近期興趣",
                "reflection": "心境想法",
                "activity": "近況",
            }.get(ep["category"], "生活")
            ep_lines.append(f"- [{dt} {cat_name}] {ep['content']}")
        parts.append(
            "💭【雪莉的自我心境與生活小劇場】（妳個人的生活記憶與想法，可在聊天中自然提及）：\n"
            + "\n".join(ep_lines)
        )

    if readings:
        rd_lines = []
        for rd in readings[:max(1, min(limit, 4))]:
            title = rd["title"]
            opinion = rd["sherry_opinion"] or rd["post_text"]
            if opinion:
                rd_lines.append(f"- 讀過《{title}》：妳的想法「{opinion}」")
            else:
                rd_lines.append(f"- 讀過《{title}》")
        parts.append(
            "📰【雪莉最近看過的新聞/文章與自身觀點】（群友提到相關話題時，妳記得自己看過並持有這些看法）：\n"
            + "\n".join(rd_lines)
        )

    return "\n\n".join(parts)
