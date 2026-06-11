from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from app.backtest.candle_strategy import DEFAULT_INTERVAL, connect_read_only
from app.collector.collect_once import CONFIG_PATH, DEFAULT_DB_PATH, load_config
from app.paper.simulator import DEFAULT_STATE_PATH, load_state


def main() -> None:
    args = parse_args()
    config = load_config(CONFIG_PATH)
    db_path = config.get("database", {}).get("path", DEFAULT_DB_PATH)
    state = load_state(Path(args.state_path))
    with connect_read_only(db_path) as conn:
        latest_prices = latest_candle_prices(conn, list(state.get("positions", {})))
    print_status(state, latest_prices)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show local JSON paper simulator state.")
    parser.add_argument("--state-path", default=DEFAULT_STATE_PATH)
    return parser.parse_args()


def status_summary(state: dict[str, Any], latest_prices: dict[str, float]) -> dict[str, Any]:
    position_market_value = 0.0
    unrealized_pnl = 0.0
    for market, position in state.get("positions", {}).items():
        metrics = position_metrics(position, latest_prices.get(market))
        position_market_value += metrics["market_value_krw"]
        unrealized_pnl += metrics["unrealized_pnl_krw"]

    cash = float(state["cash_krw"])
    return {
        "cash": cash,
        "equity": cash + position_market_value,
        "realized_pnl": float(state.get("realized_pnl_krw", 0.0)),
        "unrealized_pnl": unrealized_pnl,
        "open_positions": len(state.get("positions", {})),
        "trade_count": len(state.get("trade_log", [])),
        "last_processed_timestamp_by_market": state.get("last_processed_timestamp_by_market", {}),
    }


def position_metrics(position: dict[str, Any], latest_price: float | None) -> dict[str, float | None]:
    quantity = float(position.get("quantity", 0.0))
    cost_basis = float(position.get("cost_basis_krw", 0.0))
    if latest_price is None:
        return {
            "latest_price": None,
            "market_value_krw": cost_basis,
            "unrealized_pnl_krw": 0.0,
        }
    market_value = quantity * latest_price
    return {
        "latest_price": latest_price,
        "market_value_krw": market_value,
        "unrealized_pnl_krw": market_value - cost_basis,
    }


def latest_candle_prices(
    conn: sqlite3.Connection,
    markets: list[str],
    interval: str = DEFAULT_INTERVAL,
) -> dict[str, float]:
    prices = {}
    for market in markets:
        row = conn.execute(
            """
            SELECT trade_price
            FROM candles
            WHERE market = ?
              AND interval = ?
              AND trade_price IS NOT NULL
            ORDER BY candle_date_time_utc DESC, id DESC
            LIMIT 1
            """,
            (market, interval),
        ).fetchone()
        if row is not None:
            prices[market] = float(row["trade_price"])
    return prices


def recent_trades(state: dict[str, Any], limit: int = 10) -> list[dict[str, Any]]:
    return list(state.get("trade_log", []))[-limit:]


def print_status(state: dict[str, Any], latest_prices: dict[str, float]) -> None:
    console = Console(width=160)
    console.print(summary_table(status_summary(state, latest_prices)))
    console.print(positions_table(state, latest_prices))
    console.print(recent_trades_table(recent_trades(state)))


def summary_table(summary: dict[str, Any]) -> Table:
    table = Table(title="Paper Status Summary")
    table.add_column("cash", justify="right")
    table.add_column("equity", justify="right")
    table.add_column("realized_pnl", justify="right")
    table.add_column("unrealized_pnl", justify="right")
    table.add_column("open_positions", justify="right")
    table.add_column("trade_count", justify="right")
    table.add_column("last_processed_timestamp_by_market")
    table.add_row(
        format_krw(summary["cash"]),
        format_krw(summary["equity"]),
        format_krw(summary["realized_pnl"]),
        format_krw(summary["unrealized_pnl"]),
        str(summary["open_positions"]),
        str(summary["trade_count"]),
        format_timestamp_map(summary["last_processed_timestamp_by_market"]),
    )
    return table


def positions_table(state: dict[str, Any], latest_prices: dict[str, float]) -> Table:
    table = Table(title="Open Positions")
    table.add_column("market")
    table.add_column("quantity", justify="right")
    table.add_column("average_entry_price", justify="right")
    table.add_column("latest_price", justify="right")
    table.add_column("market_value_krw", justify="right")
    table.add_column("unrealized_pnl_krw", justify="right")
    table.add_column("entry_ts")

    positions = state.get("positions", {})
    if not positions:
        table.add_row("-", "-", "-", "-", "-", "-", "-")
        return table

    for market, position in sorted(positions.items()):
        metrics = position_metrics(position, latest_prices.get(market))
        latest_price = metrics["latest_price"]
        table.add_row(
            market,
            format_float(float(position.get("quantity", 0.0))),
            format_float(float(position.get("average_entry_price", 0.0))),
            "unavailable" if latest_price is None else format_float(float(latest_price)),
            format_krw(float(metrics["market_value_krw"])),
            format_krw(float(metrics["unrealized_pnl_krw"])),
            str(position.get("entry_ts", "-")),
        )
    return table


def recent_trades_table(trades: list[dict[str, Any]]) -> Table:
    table = Table(title="Recent Trades")
    table.add_column("timestamp")
    table.add_column("market")
    table.add_column("side")
    table.add_column("price", justify="right")
    table.add_column("quantity", justify="right")
    table.add_column("notional_krw", justify="right")
    table.add_column("fee_krw", justify="right")
    table.add_column("reason")
    table.add_column("realized_pnl_krw", justify="right")

    if not trades:
        table.add_row("-", "-", "-", "-", "-", "-", "-", "-", "-")
        return table

    for trade in trades:
        table.add_row(
            str(trade.get("timestamp", "-")),
            str(trade.get("market", "-")),
            str(trade.get("side", "-")),
            format_float(float(trade.get("price", 0.0))),
            format_float(float(trade.get("quantity", 0.0))),
            format_krw(float(trade.get("notional_krw", 0.0))),
            format_krw(float(trade.get("fee_krw", 0.0))),
            str(trade.get("reason", "-")),
            format_krw(float(trade.get("realized_pnl_krw", 0.0))) if "realized_pnl_krw" in trade else "-",
        )
    return table


def format_timestamp_map(values: dict[str, str]) -> str:
    if not values:
        return "-"
    return ", ".join(f"{market}={timestamp}" for market, timestamp in sorted(values.items()))


def format_krw(value: float) -> str:
    return f"{float(value):,.2f}"


def format_float(value: float) -> str:
    return f"{float(value):,.8f}"


if __name__ == "__main__":
    main()
