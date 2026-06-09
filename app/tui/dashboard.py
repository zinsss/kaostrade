from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

import yaml
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

CONFIG_PATH = Path("/app/config.yaml")
DEFAULT_DB_PATH = "/app/data/kaostrade.sqlite"
REFRESH_SECONDS = 2


def main() -> None:
    config = load_config(CONFIG_PATH)
    db_path = config.get("database", {}).get("path", DEFAULT_DB_PATH)

    with Live(render_dashboard(db_path), refresh_per_second=4, screen=True) as live:
        while True:
            live.update(render_dashboard(db_path))
            time.sleep(REFRESH_SECONDS)


def render_dashboard(db_path: str) -> Group:
    if not Path(db_path).exists():
        return Group(header(db_path), Panel("SQLite database not found.", title="Status"))

    try:
        with connect(db_path) as conn:
            ticker_rows = latest_tickers(conn)
            orderbook_rows = latest_orderbooks(conn)
            candle_rows = candle_counts(conn)
    except sqlite3.Error as exc:
        return Group(header(db_path), Panel(str(exc), title="Database error"))

    return Group(
        header(db_path),
        ticker_table(ticker_rows),
        orderbook_table(orderbook_rows),
        candle_table(candle_rows),
    )


def header(db_path: str) -> Panel:
    return Panel(f"DB: {db_path} | refresh: {REFRESH_SECONDS}s | read-only", title="KaosTrade")


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def latest_tickers(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT t.market, t.collected_at, t.trade_price, t.change, t.signed_change_rate, t.acc_trade_price_24h
        FROM ticker_snapshots t
        JOIN (
            SELECT market, MAX(id) AS id
            FROM ticker_snapshots
            GROUP BY market
        ) latest ON latest.id = t.id
        ORDER BY t.market
        """
    ).fetchall()


def latest_orderbooks(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT o.market, o.collected_at, o.total_ask_size, o.total_bid_size
        FROM orderbook_snapshots o
        JOIN (
            SELECT market, MAX(id) AS id
            FROM orderbook_snapshots
            GROUP BY market
        ) latest ON latest.id = o.id
        ORDER BY o.market
        """
    ).fetchall()


def candle_counts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT market, interval, COUNT(*) AS candle_count, MAX(candle_date_time_utc) AS latest_utc
        FROM candles
        GROUP BY market, interval
        ORDER BY market, interval
        """
    ).fetchall()


def ticker_table(rows: list[sqlite3.Row]) -> Table:
    table = Table(title="Latest Ticker Snapshots")
    table.add_column("Market")
    table.add_column("Collected")
    table.add_column("Trade Price", justify="right")
    table.add_column("Change")
    table.add_column("Rate", justify="right")
    table.add_column("24h Value", justify="right")

    for row in rows:
        table.add_row(
            row["market"],
            row["collected_at"],
            format_number(row["trade_price"]),
            str(row["change"] or ""),
            format_percent(row["signed_change_rate"]),
            format_number(row["acc_trade_price_24h"]),
        )
    if not rows:
        table.add_row("-", "-", "-", "-", "-", "-")
    return table


def orderbook_table(rows: list[sqlite3.Row]) -> Table:
    table = Table(title="Latest Orderbook Snapshots")
    table.add_column("Market")
    table.add_column("Collected")
    table.add_column("Total Ask", justify="right")
    table.add_column("Total Bid", justify="right")

    for row in rows:
        table.add_row(
            row["market"],
            row["collected_at"],
            format_number(row["total_ask_size"]),
            format_number(row["total_bid_size"]),
        )
    if not rows:
        table.add_row("-", "-", "-", "-")
    return table


def candle_table(rows: list[sqlite3.Row]) -> Table:
    table = Table(title="Candle Counts")
    table.add_column("Market")
    table.add_column("Interval")
    table.add_column("Candles", justify="right")
    table.add_column("Latest UTC")

    for row in rows:
        table.add_row(
            row["market"],
            row["interval"],
            str(row["candle_count"]),
            row["latest_utc"] or "-",
        )
    if not rows:
        table.add_row("-", "-", "0", "-")
    return table


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file) or {}
    if not isinstance(config, dict):
        raise ValueError(f"Config must be a mapping: {path}")
    return config


def format_number(value: Any) -> str:
    if value is None:
        return "-"
    return f"{float(value):,.8g}"


def format_percent(value: Any) -> str:
    if value is None:
        return "-"
    return f"{float(value) * 100:,.2f}%"


if __name__ == "__main__":
    main()
