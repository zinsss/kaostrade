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
SPARKLINE_BARS = "▁▂▃▄▅▆▇█"


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
            regime_row = latest_regime(conn)
            ticker_rows = latest_tickers(conn)
            sparkline_by_market = latest_sparklines(conn)
            orderbook_rows = latest_orderbooks(conn)
            candle_rows = candle_counts(conn)
    except sqlite3.Error as exc:
        return Group(header(db_path), Panel(str(exc), title="Database error"))

    return Group(
        header(db_path),
        regime_panel(regime_row),
        ticker_table(ticker_rows, sparkline_by_market),
        orderbook_table(orderbook_rows),
        candle_table(candle_rows),
    )


def header(db_path: str) -> Panel:
    return Panel(f"DB: {db_path} | refresh: {REFRESH_SECONDS}s | read-only", title="KaosTrade")


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def latest_regime(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT
            ts,
            regime,
            reason,
            btc_return_1h,
            eth_return_1h,
            median_return_1h,
            positive_ratio,
            average_spread_pct,
            average_imbalance_5,
            market_count
        FROM market_regimes
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()


def regime_panel(row: sqlite3.Row | None) -> Panel:
    if row is None:
        return Panel("No regime yet", title="Market Regime")

    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Regime", str(row["regime"]))
    table.add_row("Reason", str(row["reason"]))
    table.add_row("Timestamp", str(row["ts"]))
    table.add_row("BTC 1h", format_percent(row["btc_return_1h"]))
    table.add_row("ETH 1h", format_percent(row["eth_return_1h"]))
    table.add_row("Median 1h", format_percent(row["median_return_1h"]))
    table.add_row("Positive Ratio", format_percent(row["positive_ratio"]))
    table.add_row("Avg Spread", format_percent(row["average_spread_pct"], scale=1))
    table.add_row("Avg Imbalance 5", format_number(row["average_imbalance_5"]))
    table.add_row("Market Count", str(row["market_count"] if row["market_count"] is not None else "-"))
    return Panel(table, title="Market Regime")


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


def latest_sparklines(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute(
        """
        SELECT market, trade_price
        FROM (
            SELECT
                market,
                trade_price,
                candle_date_time_utc,
                ROW_NUMBER() OVER (PARTITION BY market ORDER BY candle_date_time_utc DESC) AS row_num
            FROM candles
            WHERE interval = ? AND trade_price IS NOT NULL
        ) latest
        WHERE row_num <= 30
        ORDER BY market, candle_date_time_utc ASC
        """,
        ("1m",),
    ).fetchall()

    prices_by_market: dict[str, list[float]] = {}
    for row in rows:
        prices_by_market.setdefault(row["market"], []).append(float(row["trade_price"]))

    return {market: make_sparkline(values) for market, values in prices_by_market.items()}


def make_sparkline(values: list[float]) -> str:
    if not values:
        return "-"

    low = min(values)
    high = max(values)
    if low == high:
        return SPARKLINE_BARS[len(SPARKLINE_BARS) // 2] * len(values)

    span = high - low
    max_index = len(SPARKLINE_BARS) - 1
    return "".join(SPARKLINE_BARS[round((value - low) / span * max_index)] for value in values)


def latest_orderbooks(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            o.market,
            o.collected_at,
            o.best_bid_price,
            o.best_ask_price,
            o.spread_pct,
            o.bid_depth_5,
            o.ask_depth_5,
            o.imbalance_5
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


def ticker_table(rows: list[sqlite3.Row], sparkline_by_market: dict[str, str]) -> Table:
    table = Table(title="Latest Ticker Snapshots")
    table.add_column("Market")
    table.add_column("Sparkline")
    table.add_column("Collected")
    table.add_column("Trade Price", justify="right")
    table.add_column("Change")
    table.add_column("Rate", justify="right")
    table.add_column("24h Value", justify="right")

    for row in rows:
        table.add_row(
            row["market"],
            sparkline_by_market.get(row["market"], "-"),
            row["collected_at"],
            format_number(row["trade_price"]),
            str(row["change"] or ""),
            format_percent(row["signed_change_rate"]),
            format_number(row["acc_trade_price_24h"]),
        )
    if not rows:
        table.add_row("-", "-", "-", "-", "-", "-", "-")
    return table


def orderbook_table(rows: list[sqlite3.Row]) -> Table:
    table = Table(title="Latest Orderbook Metrics")
    table.add_column("Market")
    table.add_column("Collected")
    table.add_column("Best Bid", justify="right")
    table.add_column("Best Ask", justify="right")
    table.add_column("Spread %", justify="right")
    table.add_column("Bid Depth 5 KRW", justify="right")
    table.add_column("Ask Depth 5 KRW", justify="right")
    table.add_column("Imbalance 5", justify="right")

    for row in rows:
        table.add_row(
            row["market"],
            row["collected_at"],
            format_number(row["best_bid_price"]),
            format_number(row["best_ask_price"]),
            format_number(row["spread_pct"]),
            format_number(row["bid_depth_5"]),
            format_number(row["ask_depth_5"]),
            format_number(row["imbalance_5"]),
        )
    if not rows:
        table.add_row("-", "-", "-", "-", "-", "-", "-", "-")
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


def format_percent(value: Any, scale: float = 100) -> str:
    if value is None:
        return "-"
    return f"{float(value) * scale:,.2f}%"


if __name__ == "__main__":
    main()
