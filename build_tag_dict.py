"""
build_tag_dict.py
=================
二次元標籤庫多源合併工具

來源優先順序（後蓋前，品質越來越高）：
  1. Danbooru /tags.json API   — 英文原始庫，約 6 萬筆高頻 tag
  2. e621 tags CSV             — 西方/獸人風格補充，約 5 萬筆
  3. byzod 中文翻譯包          — 社群維護的中英對照，約 1 萬筆

最終輸出格式（中文 → 英文）：
  {
    "初音未來": "hatsune_miku",
    "藍色頭髮": "blue_hair",
    ...
  }

執行方式：
  python build_tag_dict.py

依賴：Python 3.8+ 標準函式庫（無需額外安裝）
      若已安裝 opencc-python-reimplemented，會自動將簡體轉繁體。
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
import re
import time
import urllib.request
import urllib.error
from typing import Iterator

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------

import settings

OUTPUT_FILE = str(settings.PIXIV_TAG_PATH)

# Danbooru：只抓使用次數 >= 此值的 tag（過濾超冷門 tag）
DANBOORU_MIN_POST_COUNT = settings.PIXIV_TAG_MIN_POST_COUNT

# Danbooru：最多抓幾頁（每頁 1000 筆）；60 頁 ≈ 6 萬筆，遠低於 500 次/小時限制
DANBOORU_MAX_PAGES = settings.PIXIV_TAG_MAX_PAGES

# 每次 HTTP 請求之間的間隔（秒），避免對伺服器造成壓力
REQUEST_DELAY = settings.PIXIV_TAG_REQUEST_DELAY

# ---------------------------------------------------------------------------
# 來源設定
# ---------------------------------------------------------------------------

SOURCES: dict[str, str] = {
    "byzod_zh": (
        "https://raw.githubusercontent.com/byzod/"
        "a1111-sd-webui-tagcomplete-CN/main/tags/Tags-zh-full-pack.csv"
    ),
    "e621_csv": (
        "https://raw.githubusercontent.com/DominikDoom/"
        "a1111-sd-webui-tagcomplete/main/tags/e621.csv"
    ),
    # Danbooru 用 API 分頁抓，不走這裡
}

DANBOORU_API = settings.DANBOORU_API_URL

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OpenCC（選用）
# ---------------------------------------------------------------------------

def _make_converter():
    """嘗試載入 OpenCC 簡轉繁，失敗時回傳 None。"""
    try:
        from opencc import OpenCC  # type: ignore
        cc = OpenCC("s2t")
        logger.info("OpenCC 載入成功，將自動轉換簡體 → 繁體。")
        return cc
    except ImportError:
        logger.warning("未安裝 opencc-python-reimplemented，保留原始字元。")
        return None

_CC = _make_converter()

def to_traditional(text: str) -> str:
    return _CC.convert(text) if _CC else text

# ---------------------------------------------------------------------------
# HTTP 工具
# ---------------------------------------------------------------------------

def _get(url: str, timeout: int = 30) -> str:
    """同步 HTTP GET，回傳 response body 字串。"""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "build_tag_dict/2.0 (static JSON builder)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")

# ---------------------------------------------------------------------------
# 各來源解析器
# ---------------------------------------------------------------------------

def _clean_zh(raw: str) -> str:
    """
    清洗中文字串：
    - 簡轉繁
    - 移除尾端括號注釋：「初音未來 (Vocaloid)」→「初音未來」
    - 去除首尾空白
    """
    converted = to_traditional(raw)
    cleaned = re.split(r"[（(]", converted)[0].strip()
    return cleaned


def _parse_byzod_csv(text: str) -> Iterator[tuple[str, str]]:
    """
    解析 byzod 中文翻譯包 CSV。
    格式：英文tag, 中文名稱[, 其他欄位...]
    yield: (中文, 英文)
    """
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        if len(row) < 2:
            continue
        en = row[0].strip()
        zh_raw = row[1].strip()
        if not en or not zh_raw:
            continue
        # 跳過純數字或明顯非中文的欄位
        if not re.search(r"[\u4e00-\u9fff\u3040-\u30ff]", zh_raw):
            continue
        zh = _clean_zh(zh_raw)
        if zh and en:
            yield zh, en


def _parse_e621_csv(text: str) -> Iterator[tuple[str, str]]:
    """
    解析 e621 CSV（英文 tag 庫，無中文）。
    格式：英文tag, 類型ID[, 其他欄位...]
    這裡只建立「英文 → 英文」的備用條目，
    主要目的是讓 byzod 能用英文 key 補充覆蓋。
    實際上跳過純英文條目，只在被 byzod 引用時才有意義。

    ※ e621 本身沒有中文，此函數保留給未來接中文翻譯層使用。
       目前僅 log 統計用。
    """
    reader = csv.reader(io.StringIO(text))
    count = 0
    for row in reader:
        if len(row) >= 1 and row[0].strip():
            count += 1
    logger.info("e621 CSV 讀取完成，共 %d 筆英文 tag（備用庫）。", count)
    return iter([])  # 目前不直接產生輸出


def _fetch_danbooru_page(page: int) -> list[dict]:
    """
    抓取 Danbooru tag 列表的一頁（1000 筆）。
    回傳原始 JSON list。
    """
    params = (
        f"?limit=1000"
        f"&page={page}"
        f"&order=count"
        f"&search[post_count]=>={DANBOORU_MIN_POST_COUNT}"
    )
    url = DANBOORU_API + params
    raw = _get(url)
    return json.loads(raw)


def _danbooru_name_to_zh(name: str) -> str:
    """
    將 Danbooru 英文 tag name 轉成「可讀的中文候選詞」。
    這只是格式清洗，不是真正翻譯；真正的中文對照由 byzod 覆蓋。
    規則：底線換空格，方便之後若要接翻譯 API 使用。
    """
    return name.replace("_", " ")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def build() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 最終字典：{中文: 英文}
    tag_dict: dict[str, str] = {}

    # ── 第一層：Danbooru API（英文原始庫）────────────────────────────────
    logger.info("═" * 50)
    logger.info("【第一層】Danbooru API — 高頻英文 tag 庫")
    logger.info("最小使用次數門檻：%d，最多 %d 頁", DANBOORU_MIN_POST_COUNT, DANBOORU_MAX_PAGES)

    danbooru_en_pool: set[str] = set()  # 純英文備用池，供後續對照

    for page in range(1, DANBOORU_MAX_PAGES + 1):
        try:
            items = _fetch_danbooru_page(page)
        except urllib.error.HTTPError as exc:
            logger.error("Danbooru HTTP 錯誤 page=%d: %s", page, exc)
            break
        except Exception as exc:
            logger.error("Danbooru 例外 page=%d: %s", page, exc)
            break

        if not items:
            logger.info("Danbooru 第 %d 頁為空，停止抓取。", page)
            break

        for item in items:
            name: str = item.get("name", "").strip()
            if name:
                danbooru_en_pool.add(name)

        fetched = len(danbooru_en_pool)
        logger.info(
            "  page %3d／%d — 累積 %d 筆英文 tag",
            page, DANBOORU_MAX_PAGES, fetched,
        )
        time.sleep(REQUEST_DELAY)

    logger.info("Danbooru 完成，英文 tag 池共 %d 筆。", len(danbooru_en_pool))

    # ── 第二層：e621 CSV（補充英文池）────────────────────────────────────
    logger.info("═" * 50)
    logger.info("【第二層】e621 CSV — 西方風格補充庫")
    try:
        e621_text = _get(SOURCES["e621_csv"])
        list(_parse_e621_csv(e621_text))  # 只做統計 log
    except Exception as exc:
        logger.warning("e621 CSV 載入失敗（非致命）: %s", exc)

    # ── 第三層：byzod 中文翻譯包（主力中文對照）─────────────────────────
    logger.info("═" * 50)
    logger.info("【第三層】byzod 中文翻譯包 — 中英對照主力來源")
    try:
        byzod_text = _get(SOURCES["byzod_zh"])
        byzod_count = 0
        skipped = 0
        for zh, en in _parse_byzod_csv(byzod_text):
            # 只收錄英文 tag 在 Danbooru 池中有出現的（確保 tag 有效）
            # 若 Danbooru 拉取失敗（danbooru_en_pool 為空），則全收
            if danbooru_en_pool and en not in danbooru_en_pool:
                skipped += 1
                continue
            tag_dict[zh] = en
            byzod_count += 1

        logger.info(
            "byzod 完成：收錄 %d 筆，略過無效 tag %d 筆。",
            byzod_count, skipped,
        )
    except Exception as exc:
        logger.error("byzod 載入失敗: %s", exc)

    # ── 補充：將 Danbooru 英文池中未被 byzod 覆蓋的 tag 以「英文空格版」存入 ──
    # 讓使用者輸入英文也能查到（例如輸入 "blue hair" → "blue_hair"）
    logger.info("═" * 50)
    logger.info("【補充】將未被中文覆蓋的 Danbooru tag 以英文可讀格式存入")

    covered_en = set(tag_dict.values())
    extra_count = 0
    for en_tag in danbooru_en_pool:
        readable = _danbooru_name_to_zh(en_tag)  # "blue_hair" → "blue hair"
        if readable not in tag_dict and en_tag not in covered_en:
            tag_dict[readable] = en_tag
            extra_count += 1

    logger.info("補充英文可讀 tag：%d 筆。", extra_count)

    # ── 輸出 ──────────────────────────────────────────────────────────────
    logger.info("═" * 50)
    total = len(tag_dict)
    logger.info("總計：%d 筆 tag，開始寫入 %s ...", total, OUTPUT_FILE)

    # 按鍵排序，方便人工檢視
    sorted_dict = dict(sorted(tag_dict.items()))

    tmp_path = OUTPUT_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(sorted_dict, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, OUTPUT_FILE)

    logger.info("✅ 完成！共 【%d】 筆 tag 已儲存至 %s", total, OUTPUT_FILE)
    logger.info("═" * 50)

    # ── 統計摘要 ──────────────────────────────────────────────────────────
    zh_count = sum(
        1 for k in sorted_dict if re.search(r"[\u4e00-\u9fff\u3040-\u30ff]", k)
    )
    en_count = total - zh_count
    logger.info("中文 key：%d 筆 ／ 英文可讀 key：%d 筆", zh_count, en_count)


if __name__ == "__main__":
    build()