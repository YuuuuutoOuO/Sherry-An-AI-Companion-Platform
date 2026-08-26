"""build_tag_dict.py
=================
二次元標籤庫多源合併與角色別名索引建立工具。

資料來源：
  1. danbooru.csv (DominikDoom / TagComplete)  — 10 萬+ 標籤（含 category 0:一般, 3:作品, 4:角色 與別名）
  2. byzod 中文翻譯包 (Tags-zh-full-pack.csv) — 社群維護的中英對照，約 1 萬筆
  3. 內建動漫熱門角色映射字典                 — 保底常備熱門角色中文/日文對照

輸出檔案：
  - settings.PIXIV_TAG_PATH (file/pixiv_tag.json)     : {搜尋關鍵詞(中/英/別名): 標準Danbooru Tag}
  - settings.TAG_METADATA_PATH (file/tag_metadata.json): {標準Tag: {"category": int, "post_count": int, "series": str}}

執行方式：
  python build_tag_dict.py
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import urllib.request
import urllib.error
from pathlib import Path
from typing import Iterator

import settings

OUTPUT_TAG_FILE = str(settings.PIXIV_TAG_PATH)
OUTPUT_META_FILE = str(settings.TAG_METADATA_PATH)

SOURCES: dict[str, str] = {
    "danbooru_csv": (
        "https://raw.githubusercontent.com/DominikDoom/"
        "a1111-sd-webui-tagcomplete/main/tags/danbooru.csv"
    ),
    "byzod_zh": (
        "https://raw.githubusercontent.com/byzod/"
        "a1111-sd-webui-tagcomplete-CN/main/tags/Tags-zh-full-pack.csv"
    ),
}

# 內建常備熱門角色/作品中英對照表（保證核心角色永遠精準命中）
BUILTIN_ANIME_MAPPING: dict[str, str] = {
    # 葬送的芙莉蓮
    "芙莉蓮": "frieren_(sousou_no_frieren)",
    "費倫": "fern_(sousou_no_frieren)",
    "修塔爾克": "stark_(sousou_no_frieren)",
    "欣梅爾": "himmel_(sousou_no_frieren)",
    "阿烏拉": "aura_(sousou_no_frieren)",
    "尤貝爾": "ubel_(sousou_no_frieren)",
    # 蔚藍檔案 (Blue Archive)
    "星野": "takanashi_hoshino_(blue_archive)",
    "小鳥遊星野": "takanashi_hoshino_(blue_archive)",
    "白子": "sunaookami_shiroko_(blue_archive)",
    "砂狼白子": "sunaookami_shiroko_(blue_archive)",
    "愛麗絲": "tendou_alice_(blue_archive)",
    "天童愛麗絲": "tendou_alice_(blue_archive)",
    "優香": "hayase_yuuka_(blue_archive)",
    "早瀨優香": "hayase_yuuka_(blue_archive)",
    "亞子": "amau_ako_(blue_archive)",
    "天雨亞子": "amau_ako_(blue_archive)",
    "伊織": "shiromi_iori_(blue_archive)",
    "銀鏡伊織": "shiromi_iori_(blue_archive)",
    "陽奈": "sorasaki_hina_(blue_archive)",
    "空崎陽奈": "sorasaki_hina_(blue_archive)",
    "未花": "misono_mika_(blue_archive)",
    "聖園未花": "misono_mika_(blue_archive)",
    # 原神 (Genshin Impact)
    "雷電將軍": "raiden_shogun_(genshin_impact)",
    "雷神": "raiden_shogun_(genshin_impact)",
    "納西妲": "nahida_(genshin_impact)",
    "胡桃": "hu_tao_(genshin_impact)",
    "芙寧娜": "furina_(genshin_impact)",
    "神里綾華": "kamisato_ayaka_(genshin_impact)",
    "甘雨": "ganyu_(genshin_impact)",
    "刻晴": "keqing_(genshin_impact)",
    "鐘離": "zhongli_(genshin_impact)",
    "溫迪": "venti_(genshin_impact)",
    # 崩壞：星穹鐵道 (Honkai: Star Rail)
    "流螢": "firefly_(honkai:_star_rail)",
    "薩姆": "sam_(honkai:_star_rail)",
    "黃泉": "acheron_(honkai:_star_rail)",
    "符玄": "fu_xuan_(honkai:_star_rail)",
    "卡芙卡": "kafka_(honkai:_star_rail)",
    "銀狼": "silver_wolf_(honkai:_star_rail)",
    "砂金": "aventurine_(honkai:_star_rail)",
    "黑天鵝": "black_swan_(honkai:_star_rail)",
    "阮梅": "ruan_mei_(honkai:_star_rail)",
    "三月七": "march_7th_(honkai:_star_rail)",
    # 孤獨搖滾
    "後藤一里": "gotou_hitori",
    "波奇醬": "gotou_hitori",
    "波奇": "gotou_hitori",
    "伊地知虹夏": "ijichi_nijika",
    "山田涼": "yamada_ryo",
    "喜多郁代": "kita_ikuyo",
    # 咒術迴戰
    "五條悟": "gojou_satoru",
    "虎杖悠仁": "itadori_yuuji",
    "伏黑惠": "fushiguro_megumi",
    "釘崎野薔薇": "kugisaki_nobara",
    # 鏈鋸人
    "淀治": "denji_(chainsaw_man)",
    "帕瓦": "power_(chainsaw_man)",
    "瑪奇瑪": "makima_(chainsaw_man)",
    "早川秋": "hayakawa_aki",
    # Re:從零開始的異世界生活
    "愛蜜莉雅": "emilia_(re:zero)",
    "艾米莉婭": "emilia_(re:zero)",
    "雷姆": "rem_(re:zero)",
    "拉姆": "ram_(re:zero)",
    # 尼爾：自動人形
    "2b": "2b_(nier:automata)",
    "9s": "9s_(nier:automata)",
    "a2": "a2_(nier:automata)",
    # 名偵探柯南 & 原創
    "橘雪莉": "tachibana_sherry",
    "雪莉": "tachibana_sherry",
    "sherry": "tachibana_sherry",
    # 常備服裝
    "泳裝": "swimsuit",
    "泳衣": "swimsuit",
    "水著": "swimsuit",
    "女僕": "maid",
    "女僕裝": "maid",
    "水手服": "sailor_suit",
    "兔女郎": "bunny_girl",
    "和服": "kimono",
    "禮服": "dress",
    "制服": "school_uniform",
    "校服": "school_uniform",
    "護士服": "nurse",
    "旗袍": "china_dress",
    "睡衣": "pajamas",
    # 常備動作與表情
    "吃東西": "eating",
    "吃": "eating",
    "喝水": "drinking",
    "喝": "drinking",
    "微笑": "smile",
    "笑": "smile",
    "大笑": "laughing",
    "哭": "crying",
    "生氣": "angry",
    "害羞": "blush",
    "眨眼": "wink",
    "坐著": "sitting",
    "坐": "sitting",
    "站著": "standing",
    "躺著": "lying",
    "睡覺": "sleeping",
    "跑": "running",
    # 常備場景與背景
    "雪地": "snow",
    "雪景": "snow",
    "下雪": "snow",
    "海邊": "beach",
    "海灘": "beach",
    "教室": "classroom",
    "學校": "school",
    "室內": "indoors",
    "房間": "room",
    "室外": "outdoors",
    "戶外": "outdoors",
    "櫻花": "cherry_blossoms",
    "夜晚": "night",
    "夜景": "night",
    "森林": "forest",
    "溫泉": "onsen",
    "下雨": "rain",
    "雨中": "rain",
    # 常備身材與外觀
    "巨乳": "large_breasts",
    "大胸": "large_breasts",
    "大奶": "large_breasts",
    "貧乳": "small_breasts",
    "平胸": "small_breasts",
    "雙馬尾": "twintails",
    "單馬尾": "ponytail",
    "白髮": "white_hair",
    "黑髮": "black_hair",
    "金髮": "blonde_hair",
    "銀髮": "silver_hair",
    "藍髮": "blue_hair",
    "粉髮": "pink_hair",
    "貓耳": "cat_ears",
    "狐耳": "fox_ears",
    "兔耳": "rabbit_ears",
    "獸耳": "animal_ears",
}


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


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
    """簡轉繁工具。"""
    return _CC.convert(text) if _CC else text


def _clean_zh(raw: str) -> str:
    """清洗中文字串。"""
    converted = to_traditional(raw)
    cleaned = re.split(r"[（(]", converted)[0].strip()
    return cleaned


def _get(url: str, timeout: int = 30) -> str:
    """同步 HTTP GET。"""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "build_tag_dict/3.0 (anime companion platform)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def _parse_danbooru_csv(
    text: str,
) -> tuple[dict[str, dict[str, object]], dict[str, str]]:
    """解析 DominikDoom danbooru.csv。

    格式：name, category, post_count, aliases
    category 定義：
      0 = general (一般特徵/姿勢/服裝)
      1 = artist (畫師)
      3 = copyright (作品/版權)
      4 = character (角色)
      5 = meta (元標籤)

    Returns:
        (metadata_dict, alias_to_canonical_dict)
    """
    metadata: dict[str, dict[str, object]] = {}
    alias_map: dict[str, str] = {}

    reader = csv.reader(io.StringIO(text))
    for row in reader:
        if len(row) < 3:
            continue
        name = row[0].strip().lower()
        if not name:
            continue

        try:
            category = int(row[1].strip())
        except ValueError:
            category = 0

        try:
            post_count = int(row[2].strip())
        except ValueError:
            post_count = 0

        # 擷取作品名稱（若為角色 tag 且含有 (series)）
        series = ""
        series_match = re.search(r"\((.*?)\)$", name)
        if series_match:
            series = series_match.group(1).strip()

        metadata[name] = {
            "category": category,
            "post_count": post_count,
            "series": series,
        }

        # 解析別名欄位
        if len(row) >= 4 and row[3].strip():
            aliases_raw = row[3].strip()
            # 別名可能用逗號隔開
            for raw_alias in aliases_raw.split(","):
                clean_alias = raw_alias.strip().lower()
                if clean_alias and clean_alias != name and not clean_alias.startswith("/"):
                    alias_map[clean_alias] = name

    return metadata, alias_map


def _parse_byzod_csv(text: str) -> Iterator[tuple[str, str]]:
    """解析 byzod 中文翻譯包 CSV。"""
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        if len(row) < 2:
            continue
        en = row[0].strip().lower()
        zh_raw = row[1].strip()
        if not en or not zh_raw:
            continue
        if not re.search(r"[\u4e00-\u9fff\u3040-\u30ff]", zh_raw):
            continue
        zh = _clean_zh(zh_raw)
        if zh and en:
            yield zh, en


def build() -> None:
    """執行全量標籤庫建置。"""
    os.makedirs(os.path.dirname(OUTPUT_TAG_FILE), exist_ok=True)

    tag_dict: dict[str, str] = {}
    tag_metadata: dict[str, dict[str, object]] = {}

    # 1. 載入 DominikDoom danbooru.csv
    logger.info("═" * 60)
    logger.info("【階段 1】下載並解析 Danbooru 全量標籤庫（含角色與作品分類）")
    try:
        danbooru_csv_text = _get(SOURCES["danbooru_csv"], timeout=45)
        metadata, alias_map = _parse_danbooru_csv(danbooru_csv_text)
        tag_metadata.update(metadata)
        logger.info(
            "Danbooru CSV 解析完成：共 %d 個標籤，%d 個別名映射。",
            len(metadata), len(alias_map),
        )

        # 建立英文標準詞與可讀詞索引
        for tag_name, meta in metadata.items():
            cat = meta.get("category", 0)
            # 僅收錄 general(0), copyright(3), character(4)
            if cat not in (0, 3, 4):
                continue

            tag_dict[tag_name] = tag_name
            readable = tag_name.replace("_", " ")
            if readable not in tag_dict:
                tag_dict[readable] = tag_name

            # 如果是角色標籤且帶有作品後綴 (如 frieren_(sousou_no_frieren))
            # 額外把純角色名 frieren 與 frieren (sousou no frieren) 作為 alias 索引
            if cat == 4 and "(" in tag_name:
                base_char = re.sub(r"_\(.*?\)$", "", tag_name).strip()
                if base_char and base_char not in tag_dict:
                    tag_dict[base_char] = tag_name
                base_char_readable = base_char.replace("_", " ")
                if base_char_readable and base_char_readable not in tag_dict:
                    tag_dict[base_char_readable] = tag_name

        # 加入別名映射
        for alias, canonical in alias_map.items():
            if canonical in metadata:
                if alias not in tag_dict:
                    tag_dict[alias] = canonical
                alias_readable = alias.replace("_", " ")
                if alias_readable not in tag_dict:
                    tag_dict[alias_readable] = canonical

    except Exception as exc:
        logger.error("Danbooru CSV 載入失敗: %s", exc)

    # 2. 載入 byzod 中文翻譯包
    logger.info("═" * 60)
    logger.info("【階段 2】下載並解析 byzod 中文翻譯包")
    try:
        byzod_text = _get(SOURCES["byzod_zh"], timeout=30)
        byzod_count = 0
        for zh, en in _parse_byzod_csv(byzod_text):
            tag_dict[zh] = en
            # 同時也保留簡體版以提高匹配容錯率
            tag_dict[zh.lower()] = en
            byzod_count += 1
        logger.info("byzod 中文翻譯包完成：共加入 %d 筆中文對照。", byzod_count)
    except Exception as exc:
        logger.error("byzod 載入失敗: %s", exc)

    # 3. 載入內建熱門動漫角色與作品映射（最高優先級，覆蓋前述項目）
    logger.info("═" * 60)
    logger.info("【階段 3】載入內建熱門動漫角色/作品精確對照表")
    builtin_count = 0
    for alias_key, standard_tag in BUILTIN_ANIME_MAPPING.items():
        tag_dict[alias_key] = standard_tag
        tag_dict[alias_key.lower()] = standard_tag
        # 自動補充角色元資料
        if standard_tag not in tag_metadata:
            series = ""
            sm = re.search(r"\((.*?)\)$", standard_tag)
            if sm:
                series = sm.group(1).strip()
            tag_metadata[standard_tag] = {
                "category": 4,  # character
                "post_count": 99999,
                "series": series,
            }
        builtin_count += 1
    logger.info("內建熱門動漫角色完成：共加入 %d 筆精確對照。", builtin_count)

    # 4. 寫入檔案
    logger.info("═" * 60)
    total_tags = len(tag_dict)
    total_meta = len(tag_metadata)
    logger.info("總計：%d 筆 Tag 索引，%d 筆元資料，開始寫入磁碟...", total_tags, total_meta)

    # 排序輸出
    sorted_tag_dict = dict(sorted(tag_dict.items()))
    sorted_metadata = dict(sorted(tag_metadata.items()))

    tmp_tag_file = OUTPUT_TAG_FILE + ".tmp"
    with open(tmp_tag_file, "w", encoding="utf-8") as f:
        json.dump(sorted_tag_dict, f, ensure_ascii=False, indent=2)
    os.replace(tmp_tag_file, OUTPUT_TAG_FILE)

    tmp_meta_file = OUTPUT_META_FILE + ".tmp"
    with open(tmp_meta_file, "w", encoding="utf-8") as f:
        json.dump(sorted_metadata, f, ensure_ascii=False, indent=2)
    os.replace(tmp_meta_file, OUTPUT_META_FILE)

    logger.info("✅ 成功儲存 Tag 字典至：%s", OUTPUT_TAG_FILE)
    logger.info("✅ 成功儲存 Metadata 至：%s", OUTPUT_META_FILE)
    logger.info("═" * 60)


if __name__ == "__main__":
    build()