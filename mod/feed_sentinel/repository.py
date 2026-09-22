"""
repository.py  (mod/feed_sentinel)
==================================
AI 社群情報哨兵之 SQLite 資料倉儲與去重索引層。

負責：
1. 封存已推播至 Discord 的情報文章與雪莉銳評至 ./data/feed_sentinel/feed_sentinel.db
2. 維護多維特徵指紋索引 (Canonical Keys Index) 進行精準去重防禦
3. 提供 Web UI 分頁、篩選、搜尋與統計指標計算
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from core.db import get_db_connection, db_transaction
from . import config
from .models import FeedItem, SharedPost, extract_canonical_keys

logger = logging.getLogger(__name__)


class SentinelRepository:
    """AI 社群情報哨兵 SQLite 資料倉儲。"""

    def __init__(
        self,
        db_path: Path | None = None,
        posts_path: Path | None = None,
        seen_path: Path | None = None,
        legacy_seen_path: Path | None = None,
    ) -> None:
        self._db_path: Path = db_path or (config.DATA_DIR / "feed_sentinel.db")
        self._posts_path: Path = posts_path or config.POSTS_PATH
        self._seen_path: Path = seen_path or config.SEEN_PATH
        self._legacy_seen_path: Path = legacy_seen_path or config.LEGACY_SEEN_PATH

        self._posts: list[SharedPost] = []
        self._seen_keys: set[str] = set()

        self._init_db()
        self._load_all()

    def _init_db(self) -> None:
        """初始化 SQLite 資料庫與表格。"""
        try:
            with db_transaction(self._db_path) as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS shared_posts (
                        post_id TEXT PRIMARY KEY,
                        source TEXT NOT NULL,
                        title TEXT NOT NULL,
                        url TEXT NOT NULL,
                        original_summary TEXT NOT NULL,
                        sherry_analysis TEXT NOT NULL,
                        author_or_repo TEXT NOT NULL,
                        tags_json TEXT NOT NULL,
                        canonical_keys_json TEXT NOT NULL,
                        published_at TEXT NOT NULL,
                        posted_at TEXT NOT NULL,
                        raw_json TEXT NOT NULL
                    )
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_posts_posted_at ON shared_posts(posted_at DESC);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_posts_source ON shared_posts(source);")

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS seen_fingerprints (
                        fingerprint TEXT PRIMARY KEY
                    )
                """)
        except Exception as e:
            logger.error("[FeedSentinel] 初始化 SQLite 資料庫失敗: %s", e)

    def _load_all(self) -> None:
        """從 SQLite (或 JSON 備援) 載入所有文章與去重指紋。"""
        loaded_db = False
        try:
            conn = get_db_connection(self._db_path)
            cur = conn.cursor()

            # 1. 讀取指紋
            cur.execute("SELECT fingerprint FROM seen_fingerprints")
            fp_rows = cur.fetchall()
            for r in fp_rows:
                self._seen_keys.add(str(r["fingerprint"]))

            # 2. 讀取文章
            cur.execute("SELECT raw_json FROM shared_posts ORDER BY posted_at DESC")
            post_rows = cur.fetchall()
            posts: list[SharedPost] = []
            for r in post_rows:
                try:
                    p_dict = json.loads(r["raw_json"])
                    post = SharedPost.from_dict(p_dict)
                    posts.append(post)
                    keys = post.canonical_keys or extract_canonical_keys(post)
                    self._seen_keys.update(keys)
                except Exception:
                    pass

            conn.close()

            if posts or fp_rows:
                self._posts = posts
                loaded_db = True
        except Exception as exc:
            logger.warning("[FeedSentinel] 從 SQLite 讀取失敗，嘗試 JSON 備援: %s", exc)

        # JSON 備援遷移
        if not loaded_db:
            self._migrate_from_json_files()

    def _migrate_from_json_files(self) -> None:
        """從既有 JSON 檔案遷移歷史文章與指紋。"""
        # 1. 遷移 legacy seen
        if self._legacy_seen_path.exists():
            try:
                raw_text = self._legacy_seen_path.read_text(encoding="utf-8")
                data = json.loads(raw_text)
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, str):
                            raw_id = item.strip()
                            self._seen_keys.add(f"raw:{raw_id}")
                            self._seen_keys.add(raw_id)
            except Exception:
                pass

        # 2. 讀取 seen.json
        if self._seen_path.exists():
            try:
                raw_text = self._seen_path.read_text(encoding="utf-8")
                data = json.loads(raw_text)
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, str):
                            self._seen_keys.add(item.strip())
            except Exception:
                pass

        # 3. 讀取 posts.json
        if self._posts_path.exists():
            try:
                raw_text = self._posts_path.read_text(encoding="utf-8")
                data = json.loads(raw_text)
                if isinstance(data, list):
                    posts: list[SharedPost] = []
                    for item in data:
                        if isinstance(item, dict):
                            post = SharedPost.from_dict(item)
                            posts.append(post)
                            keys = post.canonical_keys or extract_canonical_keys(post)
                            self._seen_keys.update(keys)
                    self._posts = sorted(
                        posts,
                        key=lambda p: p.posted_at or p.published_at or "",
                        reverse=True,
                    )
            except Exception:
                pass

    def load_posts(self) -> list[SharedPost]:
        """取得目前記憶體中已載入的所有文章清單副本。"""
        return list(self._posts)

    def get_post(self, post_id: str) -> SharedPost | None:
        """依據識別碼取得單篇文章。"""
        for post in self._posts:
            if post.post_id == post_id:
                return post
        return None

    def is_duplicate(self, item: FeedItem | SharedPost | dict[str, Any]) -> bool:
        """檢查給定情報項目是否已在歷史推播或指紋快取中存在。"""
        keys = extract_canonical_keys(item)
        if not keys:
            return False
        for key in keys:
            if key in self._seen_keys:
                return True
        return False

    def record_seen(self, item: FeedItem | SharedPost | dict[str, Any]) -> None:
        """手動將項目指紋加入去重集合並持久化至 SQLite。"""
        keys = extract_canonical_keys(item)
        if not keys:
            return
        self._seen_keys.update(keys)
        try:
            with db_transaction(self._db_path) as cur:
                for k in keys:
                    cur.execute("INSERT OR IGNORE INTO seen_fingerprints (fingerprint) VALUES (?)", (k,))
        except Exception as exc:
            logger.error("[FeedSentinel] 寫入 SQLite 指紋失敗: %s", exc)

    def save_post(self, post: SharedPost) -> None:
        """儲存新推播的文章並同步更新 SQLite 指紋索引。"""
        keys = extract_canonical_keys(post)
        if not post.canonical_keys:
            post_dict = post.to_dict()
            post_dict["canonical_keys"] = keys
            post = SharedPost.from_dict(dict(post_dict))

        # 更新記憶體清單
        self._posts = [p for p in self._posts if p.post_id != post.post_id]
        self._posts.insert(0, post)
        self._seen_keys.update(keys)

        # 寫入 SQLite
        try:
            post_dict = post.to_dict()
            raw_json = json.dumps(post_dict, ensure_ascii=False)
            tags_json = json.dumps(post.tags, ensure_ascii=False)
            keys_json = json.dumps(keys, ensure_ascii=False)

            with db_transaction(self._db_path) as cur:
                cur.execute("""
                    INSERT OR REPLACE INTO shared_posts (
                        post_id, source, title, url, original_summary, sherry_analysis,
                        author_or_repo, tags_json, canonical_keys_json, published_at, posted_at, raw_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    post.post_id,
                    post.source,
                    post.title,
                    post.url,
                    post.original_summary,
                    post.sherry_analysis,
                    post.author_or_repo,
                    tags_json,
                    keys_json,
                    post.published_at,
                    post.posted_at,
                    raw_json,
                ))

                for k in keys:
                    cur.execute("INSERT OR IGNORE INTO seen_fingerprints (fingerprint) VALUES (?)", (k,))
            logger.info("[FeedSentinel] 成功寫入 SQLite 文章紀錄 [%s]: %s", post.source, post.title)
        except Exception as exc:
            logger.error("[FeedSentinel] 儲存 SQLite 文章紀錄失敗: %s", exc)

    def list_posts(
        self,
        limit: int = 50,
        offset: int = 0,
        source: str = "",
        query: str = "",
    ) -> tuple[list[SharedPost], int]:
        """依據來源與關鍵字搜尋文章紀錄。"""
        results = self._posts

        # 1. 來源過濾
        if source and source.lower() != "all":
            src_lower = source.lower()
            results = [p for p in results if src_lower in p.source.lower()]

        # 2. 關鍵字搜尋
        if query:
            q_lower = query.lower().strip()
            filtered: list[SharedPost] = []
            for p in results:
                target_str = (
                    f"{p.title} {p.original_summary} {p.sherry_analysis} "
                    f"{p.author_or_repo} {' '.join(p.tags)}"
                ).lower()
                if q_lower in target_str:
                    filtered.append(p)
            results = filtered

        total = len(results)
        page_items = results[offset : offset + limit]
        return page_items, total

    def get_stats(self) -> dict[str, Any]:
        """計算文章庫統計指標。"""
        source_counts: dict[str, int] = {
            "huggingface": 0,
            "arxiv": 0,
            "github": 0,
            "other": 0,
        }

        for p in self._posts:
            src = p.source.lower()
            if "huggingface" in src:
                source_counts["huggingface"] += 1
            elif "arxiv" in src:
                source_counts["arxiv"] += 1
            elif "github" in src:
                source_counts["github"] += 1
            else:
                source_counts["other"] += 1

        last_posted = self._posts[0].posted_at if self._posts else ""

        return {
            "total_posts": len(self._posts),
            "source_counts": source_counts,
            "last_posted_at": last_posted,
            "total_fingerprints": len(self._seen_keys),
        }

    def delete_post(self, post_id: str) -> bool:
        """刪除指定的文章封存紀錄。"""
        before_len = len(self._posts)
        self._posts = [p for p in self._posts if p.post_id != post_id]
        if len(self._posts) < before_len:
            try:
                with db_transaction(self._db_path) as cur:
                    cur.execute("DELETE FROM shared_posts WHERE post_id = ?", (post_id,))
                logger.info("[FeedSentinel] 已自 SQLite 刪除文章紀錄: %s", post_id)
                return True
            except Exception as exc:
                logger.error("[FeedSentinel] 刪除 SQLite 文章紀錄失敗: %s", exc)
                return False
        return False
