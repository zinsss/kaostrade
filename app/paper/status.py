from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from app.paper.simulator import DEFAULT_STATE_PATH, load_state


def main() -> None:
    args = parse_args()
    state = load_state(Path(args.state_path))
    print_status(state)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show local JSON paper simulator state.")
    parser.add_argument("--state-path", default=DEFAULT_STATE_PATH)
    return parser.parse_args()


def status_summary(state: dict[str, Any]) -> dict[str, Any]:
    position_value = position_cost_basis_total(state)
    cash = float(state["cash_krw"])
    return {
        "cash": cash,
        "equity": cash + position_value,
        "realized_pnl": float(state.get("realized_pnl_krw", 0.0)),
        "unrealized_pnl": 0.0,
        "open_positions": len(state.get("positions", {})),
        "trade_count": len(state.get("trade_log", [])),
        "last_processed_timestamp_by_market": state.get("last_processed_timestamp_by_market", {}),
    }


def position_cost_basis_total(state: dict[str, Any]) -> float:
    total = 0.0
    for position in state.get("positions", {}).values():
        total += float(position.get("cost_basis_krw", 0.0))
    return total


def recent_trades(state: dict[str, Any], limit: int = 10) -> list[dict[str, Any]]:
    return list(state.get("trade_log", []))[-limit:]


def print_status(state: dict[str, Any]) -> None:
    console = Console(width=160)
    console.print(summary_table(status_summary(state)))
    console.print(positions_table(state))
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


def positions_table(state: dict[str, Any]) -> Table:
    table = Table(title="Open Positions")
    table.add_column("market")
    table.add_column("quantity", justify="right")
    table.add_column("average_entry_price", justify="right")
    table.add_column("entry_ts")
    table.add_column("cost_basis_krw", justify="right")

    positions = state.get("positions", {})
    if not positions:
        table.add_row("-", "-", "-", "-", "-")
        return table

    for market, position in sorted(positions.items()):
        table.add_row(
            market,
            format_float(float(position.get("quantity", 0.0))),
            format_float(float(position.get("average_entry_price", 0.0))),
            str(position.get("entry_ts", "-")),
            format_krw(float(position.get("cost_basis_krw", 0.0))),
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
