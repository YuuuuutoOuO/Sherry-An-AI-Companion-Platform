"""
mod.stock_trader.web
====================
Quart Blueprint and Web routes for Sherry Stock Trader Mod.
Provides endpoints for account overview, return chart, holdings,
trade history, CoT thinking logs, and manual evaluation triggers.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from quart import Blueprint, Response, jsonify

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_STATIC_DIR = Path(__file__).resolve().parent / "static"

stock_trader_bp = Blueprint(
    "stock_trader",
    __name__,
    template_folder=str(_TEMPLATE_DIR),
    static_folder=str(_STATIC_DIR),
)

_bot_instance: Any = None


def set_bot_instance(bot: Any) -> None:
    """Set the Discord Bot instance for live Cog access."""
    global _bot_instance
    _bot_instance = bot


@stock_trader_bp.route("/view", methods=["GET"])
async def get_view() -> Response | tuple[Response, int] | Any:
    """Return the HTML snippet for Stock Trader view."""
    html_file = _TEMPLATE_DIR / "stock_trader.html"
    if html_file.exists():
        content = html_file.read_text(encoding="utf-8")
        return Response(content, mimetype="text/html")
    return Response("<div class='view hidden' id='view-stock-trader'><h3>虛擬炒股視圖遺失</h3></div>", mimetype="text/html")


@stock_trader_bp.route("/api/stock_trader/overview", methods=["GET"])
@stock_trader_bp.route("/overview", methods=["GET"])
async def get_stock_trader_overview() -> Response | tuple[Response, int] | Any:
    """Return Stock Trader account balance and summary metrics."""
    try:
        from mod.stock_trader.repository import StockRepository
        repo = StockRepository()
        account = repo.load_portfolio()

        history = repo.load_daily_history()
        day_pnl = history[-1].day_pnl if history else 0.0

        return jsonify({
            "status": "success",
            "data": {
                "initial_capital": account.initial_capital,
                "cash": account.cash,
                "total_asset_value": account.get_total_asset_value(),
                "total_pnl": account.get_total_pnl(),
                "total_pnl_pct": account.get_total_pnl_pct(),
                "day_pnl": day_pnl,
                "realized_pnl": account.realized_pnl,
                "win_rate_pct": account.get_win_rate_pct(),
                "total_trades_count": account.total_trades_count,
                "winning_trades_count": account.winning_trades_count,
                "positions_count": len(account.positions),
                "updated_at": account.updated_at,
            }
        })
    except Exception as exc:
        logger.error("[stock_trader] overview error: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@stock_trader_bp.route("/api/stock_trader/chart", methods=["GET"])
@stock_trader_bp.route("/chart", methods=["GET"])
async def get_stock_trader_chart() -> Response | tuple[Response, int] | Any:
    """Return last 30 days daily asset snapshots for return rate line chart."""
    try:
        from mod.stock_trader.repository import StockRepository
        repo = StockRepository()
        history = repo.load_daily_history()

        history_30d = history[-30:] if len(history) > 30 else history

        chart_data = [
            {
                "date": s.date,
                "total_asset_value": s.total_asset_value,
                "cash": s.cash,
                "total_pnl": s.total_pnl,
                "return_rate_pct": s.return_rate_pct,
                "day_pnl": s.day_pnl,
            }
            for s in history_30d
        ]

        return jsonify({
            "status": "success",
            "count": len(chart_data),
            "data": chart_data,
        })
    except Exception as exc:
        logger.error("[stock_trader] chart error: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@stock_trader_bp.route("/api/stock_trader/holdings", methods=["GET"])
@stock_trader_bp.route("/holdings", methods=["GET"])
async def get_stock_trader_holdings() -> Response | tuple[Response, int] | Any:
    """Return current portfolio stock positions."""
    try:
        from mod.stock_trader.repository import StockRepository
        repo = StockRepository()
        account = repo.load_portfolio()

        holdings_list = []
        for ticker, pos in account.positions.items():
            holdings_list.append({
                "ticker": pos.ticker,
                "shares": pos.shares,
                "avg_cost": pos.avg_cost,
                "current_price": pos.current_price,
                "total_cost": pos.total_cost,
                "market_value": pos.market_value,
                "unrealized_pnl": pos.unrealized_pnl,
                "unrealized_pnl_pct": pos.unrealized_pnl_pct,
            })

        return jsonify({
            "status": "success",
            "data": holdings_list,
        })
    except Exception as exc:
        logger.error("[stock_trader] holdings error: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@stock_trader_bp.route("/api/stock_trader/trades", methods=["GET"])
@stock_trader_bp.route("/trades", methods=["GET"])
async def get_stock_trader_trades() -> Response | tuple[Response, int] | Any:
    """Return trade history records."""
    try:
        from mod.stock_trader.repository import StockRepository
        repo = StockRepository()
        trades = repo.load_trades(limit=100)

        trade_list = [
            {
                "trade_id": t.trade_id,
                "timestamp": t.timestamp,
                "ticker": t.ticker,
                "action": t.action,
                "shares": t.shares,
                "execution_price": t.execution_price,
                "fee": t.fee,
                "total_amount": t.total_amount,
                "realized_pnl": t.realized_pnl,
                "reason": t.reason,
            }
            for t in trades
        ]

        return jsonify({
            "status": "success",
            "data": trade_list,
        })
    except Exception as exc:
        logger.error("[stock_trader] trades error: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@stock_trader_bp.route("/api/stock_trader/thoughts", methods=["GET"])
@stock_trader_bp.route("/thoughts", methods=["GET"])
async def get_stock_trader_thoughts() -> Response | tuple[Response, int] | Any:
    """Return Sherry CoT thinking process logs."""
    try:
        from mod.stock_trader.repository import StockRepository
        repo = StockRepository()
        thoughts = repo.load_thoughts(limit=30)

        thought_list = [
            {
                "timestamp": th.timestamp,
                "trigger_type": th.trigger_type,
                "thought_process": th.thought_process,
                "decision_summary": th.decision_summary,
                "recommended_actions": th.recommended_actions,
            }
            for th in thoughts
        ]

        return jsonify({
            "status": "success",
            "data": thought_list,
        })
    except Exception as exc:
        logger.error("[stock_trader] thoughts error: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@stock_trader_bp.route("/api/stock_trader/targets", methods=["GET"])
@stock_trader_bp.route("/targets", methods=["GET"])
async def get_stock_trader_targets() -> Response | tuple[Response, int] | Any:
    """Return target stocks with latest technical indicators."""
    try:
        from mod.market_plugin.data_fetcher import DataFetcher
        fetcher = DataFetcher()
        tickers = ["NVDA", "AAPL", "MSFT", "GOOG", "AMZN", "META", "TSLA", "TSM", "AVGO", "AMD"]

        if _bot_instance and hasattr(_bot_instance, "_stock_trader_cog"):
            cog = _bot_instance._stock_trader_cog
            if hasattr(cog, "get_watch_tickers"):
                tickers = cog.get_watch_tickers()

        indicators = await asyncio.to_thread(fetcher.get_stock_info_raw, tickers)

        return jsonify({
            "status": "success",
            "data": indicators,
        })
    except Exception as exc:
        logger.error("[stock_trader] targets error: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@stock_trader_bp.route("/api/stock_trader/trigger", methods=["POST"])
@stock_trader_bp.route("/trigger", methods=["POST"])
async def trigger_stock_trader_eval() -> Response | tuple[Response, int] | Any:
    """Manually trigger a Stock Trader evaluation & decision cycle."""
    cog = getattr(_bot_instance, "_stock_trader_cog", None) if _bot_instance else None

    try:
        if cog and hasattr(cog, "run_evaluation_cycle"):
            res = await cog.run_evaluation_cycle(trigger_type="MANUAL")
        else:
            from mod.stock_trader.cog import StockTraderCog
            temp_cog = StockTraderCog(bot=_bot_instance)
            res = await temp_cog.run_evaluation_cycle(trigger_type="MANUAL")
        return jsonify({"status": "success", "result": res})
    except Exception as exc:
        logger.exception("[stock_trader] 手動觸發看盤發生異常: %s", exc)
        return jsonify({"status": "error", "message": f"觸發異常: {exc}"}), 500


@stock_trader_bp.route("/api/stock_trader/refresh_prices", methods=["POST"])
@stock_trader_bp.route("/refresh_prices", methods=["POST"])
async def refresh_stock_trader_prices() -> Response | tuple[Response, int] | Any:
    """Manually refresh real-time stock prices & portfolio unrealized PnL via yfinance."""
    cog = getattr(_bot_instance, "_stock_trader_cog", None) if _bot_instance else None

    try:
        if cog and hasattr(cog, "fetcher"):
            tickers = cog.get_watch_tickers()
            indicators = await asyncio.to_thread(cog.fetcher.get_stock_info_raw, tickers)
            price_map = {item["ticker"]: item["close"] for item in indicators} if indicators else {}
            account = cog.algo.update_prices(price_map)
            return jsonify({
                "status": "success",
                "message": f"已成功連線 yfinance 刷新 {len(indicators)} 檔標的之最新行情與持倉損益！",
                "total_asset_value": account.get_total_asset_value(),
                "total_pnl": account.get_total_pnl(),
            })
        else:
            from mod.market_plugin.data_fetcher import DataFetcher
            from mod.stock_trader.engine.algo_trader import AlgoTrader
            from mod.stock_trader.repository import StockRepository
            repo = StockRepository()
            algo = AlgoTrader(repository=repo)
            fetcher = DataFetcher()
            tickers = ["NVDA", "AAPL", "MSFT", "GOOG", "AMZN", "META", "TSLA", "TSM", "AVGO", "AMD"]
            indicators = await asyncio.to_thread(fetcher.get_stock_info_raw, tickers)
            price_map = {item["ticker"]: item["close"] for item in indicators} if indicators else {}
            account = algo.update_prices(price_map)
            return jsonify({
                "status": "success",
                "message": f"已成功連線 yfinance 刷新 {len(indicators)} 檔標的之最新行情與持倉損益！",
                "total_asset_value": account.get_total_asset_value(),
                "total_pnl": account.get_total_pnl(),
            })
    except Exception as exc:
        logger.exception("[stock_trader] 刷新標的行情異常: %s", exc)
        return jsonify({"status": "error", "message": f"刷新失敗: {exc}"}), 500
