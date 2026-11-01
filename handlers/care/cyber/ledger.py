from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCK = threading.RLock()
_RETENTION_SECONDS = 86400.0


class ContactLedger:
    """每頻道實際發送回數的跨重啟帳本。

    只記「真的送出去」的回合，不記候選、不記 silent。檔案格式刻意保持
    扁平、可讀，方便 WebUI 或維運直接檢查：

    ``{"<channel_id>": [<unix_ts>, ...]}``
    """

    def __init__(self, channel_id: str, path: Path | str | None = None) -> None:
        self.channel_id = str(channel_id)
        self.path = Path(path) if path is not None else None
        self.sends: deque[float] = deque()
        self._load()

    def _load(self) -> None:
        if self.path is None:
            return
        with _LOCK:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return
            except Exception as exc:
                logger.warning("[CyberLedger] 讀取帳本失敗: %s", exc)
                return
            raw = payload.get(self.channel_id) if isinstance(payload, dict) else None
            if not isinstance(raw, list):
                return
            cutoff = time.time() - _RETENTION_SECONDS
            self.sends = deque(
                float(ts)
                for ts in raw
                if isinstance(ts, (int, float)) and float(ts) >= cutoff
            )

    def save(self) -> None:
        if self.path is None:
            return
        with _LOCK:
            payload: dict[str, Any] = {}
            try:
                if self.path.exists():
                    loaded = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        payload = loaded
            except Exception as exc:
                logger.warning("[CyberLedger] 讀取既有帳本失敗，將重建: %s", exc)
                payload = {}
            payload[self.channel_id] = list(self.sends)
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(self.path.suffix + ".tmp")
                tmp.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                tmp.replace(self.path)
            except Exception as exc:
                logger.warning("[CyberLedger] 寫入帳本失敗: %s", exc)

    def prune(self, now: float | None = None) -> None:
        cutoff = (time.time() if now is None else float(now)) - _RETENTION_SECONDS
        while self.sends and self.sends[0] < cutoff:
            self.sends.popleft()

    def record(self, now: float | None = None) -> None:
        self.sends.append(time.time() if now is None else float(now))
        self.save()
