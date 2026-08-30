"""
config.py  (mod/sys_monitor)
============================
系統資源監控模組專屬設定檔。

所有採樣間隔、批次寫入頻率、輪播與資料路徑皆在此定義，
不干涉雪莉主程式 settings.py。可透過 .env 環境變數進行個別覆寫。
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Module Root & Project Paths
# ---------------------------------------------------------------------------
MOD_DIR: Path = Path(__file__).resolve().parent
PROJECT_ROOT: Path = MOD_DIR.parent.parent
DATA_DIR: Path = PROJECT_ROOT / "data" / "sys_monitor"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# 模組功能開關
# ---------------------------------------------------------------------------
ENABLED: bool = os.getenv("SYS_MONITOR_ENABLED", "True").lower() in ("true", "1", "yes")

# ---------------------------------------------------------------------------
# 採樣與落盤頻率控制
# ---------------------------------------------------------------------------
# 主採樣迴圈間隔 (秒)：每次採樣 CPU / DRAM / Storage / GPU
INTERVAL_SECONDS: float = float(os.getenv("SYS_MONITOR_INTERVAL_SECONDS", "5"))

# 記憶體緩衝批次 flush 間隔 (秒)：每隔多久把累積緩衝寫入一次 JSONL
FLUSH_SECONDS: float = float(os.getenv("SYS_MONITOR_FLUSH_SECONDS", "30"))

# 僅在數值變化超過此門檻時才推送「事件式」紀錄 (0 = 全部記錄)
# 用於過濾近乎恆定的採樣，進一步降低寫入量。
SAMPLE_DELTA_THRESHOLD: float = float(os.getenv("SYS_MONITOR_DELTA_THRESHOLD", "0.0"))

# 批次寫入採用的編碼 (預設 utf-8)
ENCODING: str = "utf-8"

# ---------------------------------------------------------------------------
# GPU 監測設定
# ---------------------------------------------------------------------------
# 是否啟用 nvidia-smi 子行程採樣 GPU 利用率 / VRAM / 溫度
# 依賴系統 PATH 中存在 nvidia-smi；若失敗會自動停用 GPU 監測。
GPU_ENABLED: bool = os.getenv("SYS_MONITOR_GPU_ENABLED", "True").lower() in (
    "true",
    "1",
    "yes",
)
# nvidia-smi 呼叫逾時 (秒)，避免子行程卡死主迴圈
GPU_SMI_TIMEOUT: float = float(os.getenv("SYS_MONITOR_GPU_SMI_TIMEOUT", "2.5"))

# ---------------------------------------------------------------------------
# 持久化紀錄路徑 (單行 JSONL 時間序列)
# ---------------------------------------------------------------------------
SERIES_PATH: Path = DATA_DIR / "series.jsonl"

# 保留的最大歷史採樣筆數 (寫入時修剪，避免無限成長)
MAX_SERIES_ROWS: int = int(os.getenv("SYS_MONITOR_MAX_ROWS", "8640"))
