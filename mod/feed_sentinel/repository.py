"""
repository.py  (mod/feed_sentinel)
==================================
AI 社群情報哨兵之資料倉儲與去重索引層。

負責：
1. 封存已推播至 Discord 的情報文章與雪莉銳評至 ./data/feed_sentinel/posts.json
2. 維護多維特徵指紋索引 (Canonical Keys Index) 進行精準去重防禦
3. 自動無縫遷移舊版 data/sentinel_seen.json 快取
4. 提供 Web UI 分頁、篩選、搜尋與統計指標計算
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from . import config
from .models import FeedItem, SharedPost, extract_canonical_keys

logger = logging.getLogger(__name__)


class SentinelRepository:
    """AI 社群情報哨兵持久化倉儲。"""

    def __init__(
        self,
        posts_path: Path | None = None,
        seen_path: Path | None = None,
        legacy_seen_path: Path | None = None,
    ) -> None:
        """初始化資料倉儲並載入快取與去重索引。

        Args:
            posts_path: 文章存檔 JSON 路徑 (預設為 config.POSTS_PATH)。
            seen_path: 去重指紋鍵值 JSON 路徑 (預設為 config.SEEN_PATH)。
            legacy_seen_path: 舊版 ID 快取 JSON 路徑 (預設為 config.LEGACY_SEEN_PATH)。
        """
        self._posts_path: Path = posts_path or config.POSTS_PATH
        self._seen_path: Path = seen_path or config.SEEN_PATH
        self._legacy_seen_path: Path = legacy_seen_path or config.LEGACY_SEEN_PATH

        self._posts_path.parent.mkdir(parents=True, exist_ok=True)
        self._seen_path.parent.mkdir(parents=True, exist_ok=True)

        self._posts: list[SharedPost] = []
        self._seen_keys: set[str] = set()

        self._load_all()

    def _load_all(self) -> None:
        """從硬碟載入所有文章與去重指紋。"""
        # 1. 遷移舊版 seen.json (若存在且新版尚未建立)
        self._migrate_legacy_seen_if_needed()

        # 2. 讀取新版 seen.json
        if self._seen_path.exists():
            try:
                raw_text = self._seen_path.read_text(encoding="utf-8")
                data = json.loads(raw_text)
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, str):
                            self._seen_keys.add(item.strip())
            except Exception as exc:
                logger.warning("[FeedSentinel] 讀取 %s 失敗: %s", self._seen_path, exc)

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
                            # 將文章的所有指紋鍵加入去重集合
                            keys = post.canonical_keys or extract_canonical_keys(post)
                            self._seen_keys.update(keys)
                    self._posts = sorted(
                        posts,
                        key=lambda p: p.posted_at or p.published_at or "",
                        reverse=True,
                    )
            except Exception as exc:
                logger.warning("[FeedSentinel] 讀取 %s 失敗: %s", self._posts_path, exc)

    def load_posts(self) -> list[SharedPost]:
        """取得目前記憶體中已載入的所有文章清單副本。

        Returns:
            以發布時間降冪排序的 SharedPost 清單。
        """
        return list(self._posts)

    def get_post(self, post_id: str) -> SharedPost | None:
        """依據識別碼取得單篇文章。

        Args:
            post_id: 文章唯一識別碼。

        Returns:
            若找到則回傳 SharedPost，否則回傳 None。
        """
        for post in self._posts:
            if post.post_id == post_id:
                return post
        return None

    def _migrate_legacy_seen_if_needed(self) -> None:
        """若存在舊版 data/sentinel_seen.json，自動將歷史 ID 遷移納入索引。"""
        if self._legacy_seen_path.exists() and self._legacy_seen_path != self._seen_path:
            try:
                raw_text = self._legacy_seen_path.read_text(encoding="utf-8")
                data = json.loads(raw_text)
                if isinstance(data, list):
                    count = 0
                    for item in data:
                        if isinstance(item, str):
                            raw_id = item.strip()
                            self._seen_keys.add(f"raw:{raw_id}")
                            self._seen_keys.add(raw_id)
                            count += 1
                    logger.info(
                        "[FeedSentinel] 成功從舊版 %s 遷移 %d 筆去重 ID 快取至新倉儲",
                        self._legacy_seen_path,
                        count,
                    )
            except Exception as exc:
                logger.warning("[FeedSentinel] 舊版 ID 遷移異常: %s", exc)

    def _atomic_write_json(self, target_path: Path, data: Any) -> None:
        """安全原子性寫入 JSON 檔案，防止斷電或中斷導致資料損毀。"""
        target_path.parent.mkdir(parents=True, exist_ok=True)
        dir_path = target_path.parent
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=dir_path,
            delete=False,
            suffix=".tmp",
        ) as tf:
            json.dump(data, tf, ensure_ascii=False, indent=2)
            temp_name = tf.name

        os.replace(temp_name, target_path)

    def is_duplicate(self, item: FeedItem | SharedPost | dict[str, Any]) -> bool:
        """檢查給定情報項目是否已在歷史推播或指紋快取中存在。

        Args:
            item: 待檢查的 FeedItem, SharedPost 或字典。

        Returns:
            若任一多維指紋鍵值已存在則回傳 True，否則回傳 False。
        """
        keys = extract_canonical_keys(item)
        if not keys:
            return False
        for key in keys:
            if key in self._seen_keys:
                return True
        return False

    def record_seen(self, item: FeedItem | SharedPost | dict[str, Any]) -> None:
        """手動將項目指紋加入去重集合並持久化。

        Args:
            item: FeedItem, SharedPost 或字典。
        """
        keys = extract_canonical_keys(item)
        if not keys:
            return
        self._seen_keys.update(keys)
        self._save_seen_keys()

    def _save_seen_keys(self) -> None:
        """持久化去重指紋集合至硬碟。"""
        try:
            # 保留最新 5000 筆指紋
            sorted_keys = sorted(self._seen_keys)[-5000:]
            self._atomic_write_json(self._seen_path, sorted_keys)
        except Exception as exc:
            logger.error("[FeedSentinel] 儲存指紋快取失敗: %s", exc)

    def save_post(self, post: SharedPost) -> None:
        """儲存新推播的文章並同步更新指紋索引。

        Args:
            post: 已推播之 SharedPost 物件。
        """
        # 提取指紋並注入 post
        keys = extract_canonical_keys(post)
        if not post.canonical_keys:
            post_dict = post.to_dict()
            post_dict["canonical_keys"] = keys
            post = SharedPost.from_dict(dict(post_dict))

        # 加入記憶體文章清單頂端 (若已存在相同 post_id 則更新)
        self._posts = [p for p in self._posts if p.post_id != post.post_id]
        self._posts.insert(0, post)

        # 更新指紋
        self._seen_keys.update(keys)

        # 持久化
        try:
            posts_data = [p.to_dict() for p in self._posts]
            self._atomic_write_json(self._posts_path, posts_data)
            self._save_seen_keys()
            logger.info("[FeedSentinel] 成功儲存文章紀錄 [%s]: %s", post.source, post.title)
        except Exception as exc:
            logger.error("[FeedSentinel] 儲存文章紀錄失敗: %s", exc)

    def list_posts(
        self,
        limit: int = 50,
        offset: int = 0,
        source: str = "",
        query: str = "",
    ) -> tuple[list[SharedPost], int]:
        """依據來源與關鍵字搜尋文章紀錄。

        Args:
            limit: 每頁筆數。
            offset: 位移筆數。
            source: 來源篩選 (例如 'HuggingFace', 'arXiv', 'GitHub')。
            query: 搜尋關鍵字 (支援標題、摘要、銳評、標籤、作者)。

        Returns:
            (篩選後該頁文章清單, 總筆數)
        """
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
        """計算文章庫統計指標。

        Returns:
            包含總文章數、各來源筆數、最新推播時間與指紋總數的統計字典。
        """
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
        """刪除指定的文章封存紀錄 (保留指紋防止意外重複抓取)。

        Args:
            post_id: 欲刪除之文章識別碼。

        Returns:
            若刪除成功回傳 True，找不到則回傳 False。
        """
        before_len = len(self._posts)
        self._posts = [p for p in self._posts if p.post_id != post_id]
        if len(self._posts) < before_len:
            try:
                posts_data = [p.to_dict() for p in self._posts]
                self._atomic_write_json(self._posts_path, posts_data)
                logger.info("[FeedSentinel] 已刪除文章紀錄: %s", post_id)
                return True
            except Exception as exc:
                logger.error("[FeedSentinel] 刪除文章紀錄寫入失敗: %s", exc)
                return False
        return False
