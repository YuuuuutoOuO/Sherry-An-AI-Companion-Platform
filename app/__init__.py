"""
app
===
應用層協調者套件 (Application Layer / Composition Root)。

負責統籌請求生命週期並串接 handlers 與 mod，位於比 core 基礎層更高之位階，
確保依賴方向為 app -> handlers/mod -> core，杜絕循環依賴。
"""

from app.orchestrator import SherryCore

__all__ = ["SherryCore"]
