import json
import logging
from pathlib import Path
from typing import Any, TypedDict

logger = logging.getLogger(__name__)

# 定義我們字典記憶中存在的格式型態
class TopicEntry(TypedDict):
    summary: str
    weight: int

VibeStore = dict[str, dict[str, TopicEntry]]

def migrate_entry(value: Any) -> TopicEntry:
    """若是舊系統遺留純粹記錄int值的舊設定，轉到具備內容描述與熱度整數的新設定版"""
    if isinstance(value, int):
        return TopicEntry(summary="", weight=value)
    if isinstance(value, dict) and "weight" in value:
        return TopicEntry(
            summary=value.get("summary", ""),
            weight=int(value["weight"]),
        )
    return TopicEntry(summary="", weight=1)

def load_vibe_store(path: Path) -> VibeStore:
    """初始化並自硬碟喚起先前關閉保存的主題歷史檔。具備自動資料翻修遷移處理。"""
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data: Any = json.load(fh)
        if not isinstance(data, dict):
            logger.warning("VibeStorage: Unexpected JSON structure; 發現格式不一致，清空 Store。")
            return {}
            
        migrated: VibeStore = {}
        for ch_id, topics in data.items():
            if not isinstance(topics, dict): continue
            migrated[ch_id] = {
                topic: migrate_entry(entry)
                for topic, entry in topics.items()
                if isinstance(topic, str)
            }
        return migrated
        
    except json.JSONDecodeError as exc:
        logger.error("VibeStorage: 存取損壞載入失敗，報錯日誌 %s: %s", path, exc)
        return {}

def save_vibe_store(store: VibeStore, path: Path) -> None:
    """對記憶主題做強制原子性落地存檔（不會寫一半關機爆破資料檔）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(store, fh, ensure_ascii=False, indent=2)
        tmp.replace(path)
    except OSError as exc:
        logger.error("VibeStorage: 磁碟寫入存擋執行遭遇 I/O 例外阻饒！ %s", exc)