"""
visualizer.py  (mod/market_plugin)
====================================
股票 AI 預測準確率圖表生成。
"""

from __future__ import annotations

import io

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _MPL_AVAILABLE = True
except ImportError:
    _MPL_AVAILABLE = False


class Visualizer:
    def create_accuracy_chart(self, ticker: str, history_data: list):
        """
        history_data: [(date_str, predicted_val, actual_val), ...]
        回傳: BytesIO 物件，可直接包為 discord.File
        """
        if not _MPL_AVAILABLE or not history_data:
            return None

        dates = [row[0] for row in history_data]
        preds = [row[1] for row in history_data]
        actuals = [row[2] for row in history_data]

        fig, ax = plt.subplots(figsize=(10, 6))
        ax.plot(dates, actuals, color="red", marker="o", label="Actual Price", linewidth=2)
        ax.plot(dates, preds, color="blue", linestyle="--", marker="x", label="AI Predicted", linewidth=2)
        ax.set_title(f"AI Prediction Accuracy: {ticker}", fontsize=16)
        ax.set_xlabel("Date")
        ax.set_ylabel("Price ($)")
        ax.legend()
        ax.grid(True, alpha=0.3)
        plt.xticks(rotation=45)
        plt.tight_layout()

        buf = io.BytesIO()
        plt.savefig(buf, format="png")
        buf.seek(0)
        plt.close(fig)
        return buf
