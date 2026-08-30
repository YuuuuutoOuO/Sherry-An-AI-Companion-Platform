"""mod/spark/repository.py - Spark 資料持久化倉儲層。

負責 Trigger 觸發器設定資料的讀取、儲存（原子寫入）、查詢與刪除。
符合 mod規範 §6（資料存放於 data/spark/，受版本控制保護）。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from .config import SPARK_DATA_DIR, TRIGGERS_FILE_PATH
from .models import Trigger

logger = logging.getLogger(__name__)


class SparkRepository:
    """Spark 觸發器資料倉儲。"""

    def __init__(self, file_path: Path = TRIGGERS_FILE_PATH) -> None:
        """初始化資料倉儲並載入現有資料。

        Args:
            file_path: 觸發器 JSON 儲存路徑。
        """
        self._file_path = file_path
        self._cache: dict[str, Trigger] = {}
        self._ensure_storage_ready()
        self.reload()

    def _ensure_storage_ready(self) -> None:
        """確保儲存資料夾與初始檔案存在。"""
        try:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            if not self._file_path.exists():
                self._write_file_data({})
        except Exception as exc:
            logger.error("[SparkRepository] 初始化儲存目錄失敗: %s", exc)

    def reload(self) -> None:
        """重新從磁碟載入所有 Trigger 資料至記憶體快取。"""
        if not self._file_path.exists():
            self._cache = {}
            return

        try:
            content = self._file_path.read_text(encoding="utf-8")
            if not content.strip():
                self._cache = {}
                return
            data_dict: dict[str, Any] = json.loads(content)
            loaded: dict[str, Trigger] = {}
            for tid, tdata in data_dict.items():
                try:
                    loaded[tid] = Trigger.from_dict(tdata)
                except Exception as parse_err:
                    logger.warning(
                        "[SparkRepository] 解析 Trigger (%s) 失敗: %s", tid, parse_err
                    )
            self._cache = loaded
            logger.debug("[SparkRepository] 成功載入 %d 個觸發器", len(self._cache))
        except Exception as exc:
            logger.error("[SparkRepository] 讀取觸發器檔案失敗: %s", exc)
            self._cache = {}

    def _write_file_data(self, data: dict[str, Any]) -> None:
        """原子寫入資料至 JSON 檔案，防止斷電或崩潰導致檔案損壞。"""
        tmp_path = self._file_path.with_suffix(".tmp")
        try:
            tmp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            # 使用 os.replace 實現跨平台原子檔案替換
            os.replace(tmp_path, self._file_path)
        except Exception as exc:
            logger.error("[SparkRepository] 寫入觸發器檔案異常: %s", exc)
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            raise

    def get(self, trigger_id: str) -> Trigger | None:
        """根據 ID 取得單一觸發器。

        Args:
            trigger_id: 觸發器 UUID。

        Returns:
            Trigger | None: 觸發器實例，若不存在則為 None。
        """
        return self._cache.get(trigger_id)

    def list_all(self) -> list[Trigger]:
        """取得所有已註冊的觸發器清單。

        Returns:
            list[Trigger]: 全部觸發器列表。
        """
        return list(self._cache.values())

    def list_active(self) -> list[Trigger]:
        """取得目前處於啟用狀態 (is_active=True) 的觸發器清單。

        Returns:
            list[Trigger]: 活躍觸發器列表。
        """
        return [t for t in self._cache.values() if t.is_active]

    def list_by_user(self, user_id: int) -> list[Trigger]:
        """取得特定使用者建立的觸發器清單。

        Args:
            user_id: Discord 使用者 ID。

        Returns:
            list[Trigger]: 使用者專屬觸發器清單。
        """
        return [t for t in self._cache.values() if t.user_id == user_id]

    def save(self, trigger: Trigger) -> Trigger:
        """儲存或更新單一觸發器（寫入記憶體與磁碟）。

        Args:
            trigger: 欲保存的 Trigger 實例。

        Returns:
            Trigger: 已儲存的實例。
        """
        self._cache[trigger.trigger_id] = trigger
        data_to_write = {tid: t.to_dict() for tid, t in self._cache.items()}
        self._write_file_data(data_to_write)
        return trigger

    def delete(self, trigger_id: str) -> bool:
        """刪除指定 ID 的觸發器。

        Args:
            trigger_id: 觸發器 UUID。

        Returns:
            bool: 是否成功刪除（若 ID 原本就不存在則回傳 False）。
        """
        if trigger_id not in self._cache:
            return False
        del self._cache[trigger_id]
        data_to_write = {tid: t.to_dict() for tid, t in self._cache.items()}
        self._write_file_data(data_to_write)
        return True

    def get_stats(self) -> dict[str, Any]:
        """計算觸發器統計摘要。

        Returns:
            dict[str, Any]: 包含總數、啟用數、類型分佈等統計資訊。
        """
        total = len(self._cache)
        active = sum(1 for t in self._cache.values() if t.is_active)
        paused = total - active

        type_counts: dict[str, int] = {}
        for t in self._cache.values():
            type_counts[t.trigger_type] = type_counts.get(t.trigger_type, 0) + 1

        return {
            "total": total,
            "active": active,
            "paused": paused,
            "by_type": type_counts,
        }
