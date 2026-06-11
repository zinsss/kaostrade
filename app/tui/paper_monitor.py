from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from app.backtest.candle_strategy import connect_read_only
from app.backtest.strategy_profiles import profile_names
from app.collector.collect_once import CONFIG_PATH, DEFAULT_DB_PATH, load_config
from app.paper.simulator import DEFAULT_STATE_PATH, load_state
from app.paper.status import (
    latest_candle_prices,
    positions_table,
    recent_trades,
    recent_trades_table,
    status_summary,
    summary_table,
)

DEFAULT_PROFILE = "candidate_v1"
DEFAULT_REFRESH_SECONDS = 5


def main() -> None:
    args = parse_args()
    config = load_config(CONFIG_PATH)
    db_path = config.get("database", {}).get("path", DEFAULT_DB_PATH)

    if args.once:
        Console(width=160).print(render_monitor(args.profile, Path(args.state_path), db_path, args.refresh_seconds))
        return

    with Live(
        render_monitor(args.profile, Path(args.state_path), db_path, args.refresh_seconds),
        refresh_per_second=4,
        screen=True,
    ) as live:
        while True:
            live.update(render_monitor(args.profile, Path(args.state_path), db_path, args.refresh_seconds))
            time.sleep(args.refresh_seconds)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only Rich monitor for local JSON paper state.")
    parser.add_argument("--profile", choices=profile_names(), default=DEFAULT_PROFILE)
    parser.add_argument("--state-path", default=DEFAULT_STATE_PATH)
    parser.add_argument("--refresh-seconds", type=positive_int, default=DEFAULT_REFRESH_SECONDS)
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def render_monitor(profile_name: str, state_path: Path, db_path: str, refresh_seconds: int) -> Group:
    snapshot = monitor_snapshot(profile_name, state_path, db_path, refresh_seconds)
    state = snapshot["state"]
    latest_prices = snapshot["latest_prices"]
    summary = status_summary(state, latest_prices)

    return Group(
        header_panel(snapshot),
        summary_table(summary),
        last_processed_table(summary["last_processed_timestamp_by_market"]),
        positions_table(state, latest_prices),
        recent_trades_table(recent_trades(state)),
    )


def monitor_snapshot(profile_name: str, state_path: Path, db_path: str, refresh_seconds: int) -> dict[str, Any]:
    state = load_state(state_path)
    markets = list(state.get("positions", {}))
    with connect_read_only(db_path) as conn:
        latest_prices = latest_candle_prices(conn, markets)
    return {
        "profile": profile_name,
        "refresh_timestamp": datetime.now(timezone.utc).isoformat(),
        "refresh_seconds": refresh_seconds,
        "state_path": str(state_path),
        "db_path": db_path,
        "state": state,
        "latest_prices": latest_prices,
    }


def header_panel(snapshot: dict[str, Any]) -> Panel:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Profile", snapshot["profile"])
    table.add_row("Refresh Timestamp", snapshot["refresh_timestamp"])
    table.add_row("Refresh Seconds", str(snapshot["refresh_seconds"]))
    table.add_row("State", snapshot["state_path"])
    table.add_row("DB", snapshot["db_path"])
    table.add_row("Mode", "read-only")
    return Panel(table, title="Paper Monitor")


def last_processed_table(values: dict[str, str]) -> Table:
    table = Table(title="Last Processed Timestamp By Market")
    table.add_column("market")
    table.add_column("timestamp")
    if not values:
        table.add_row("-", "-")
        return table
    for market, timestamp in sorted(values.items()):
        table.add_row(market, timestamp)
    return table


if __name__ == "__main__":
    main()
