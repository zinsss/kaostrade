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
            paper_state = load_paper_state(conn)
            ticker_rows = latest_tickers(conn)
            sparkline_by_market = latest_sparklines(conn)
            orderbook_rows = latest_orderbooks(conn)
            candle_rows = candle_counts(conn)
    except sqlite3.Error as exc:
        return Group(header(db_path), Panel(str(exc), title="Database error"))

    return Group(
        header(db_path),
        regime_panel(regime_row),
        paper_trading_panel(paper_state),
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
        WHERE source = 'live'
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


def load_paper_state(conn: sqlite3.Connection) -> dict[str, Any] | None:
    try:
        account = conn.execute(
            """
            SELECT id, name, cash_krw, created_at, updated_at
            FROM paper_accounts
            WHERE name = ?
            LIMIT 1
            """,
            ("default",),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return None
        raise
    if account is None:
        return None

    positions = conn.execute(
        """
        SELECT market, quantity, average_entry_price, updated_at
        FROM paper_positions
        WHERE account_id = ?
        ORDER BY market
        """,
        (account["id"],),
    ).fetchall()
    trades = conn.execute(
        """
        SELECT ts, side, market, price, quantity, notional_krw, fee_krw
        FROM paper_trades
        WHERE account_id = ?
        ORDER BY ts DESC, id DESC
        LIMIT 5
        """,
        (account["id"],),
    ).fetchall()
    latest_prices = latest_ticker_prices(conn)

    position_rows = []
    total_position_value = 0.0
    total_unrealized_pnl = 0.0
    for position in positions:
        quantity = float(position["quantity"])
        average_entry_price = float(position["average_entry_price"])
        latest_price = latest_prices.get(position["market"])
        market_value = None
        unrealized_pnl = None
        if latest_price is not None:
            market_value = quantity * latest_price
            unrealized_pnl = (latest_price - average_entry_price) * quantity
            total_position_value += market_value
            total_unrealized_pnl += unrealized_pnl
        position_rows.append(
            {
                "market": position["market"],
                "quantity": quantity,
                "average_entry_price": average_entry_price,
                "latest_price": latest_price,
                "market_value": market_value,
                "unrealized_pnl": unrealized_pnl,
            }
        )

    cash_krw = float(account["cash_krw"])
    return {
        "account": dict(account),
        "cash_krw": cash_krw,
        "total_position_value": total_position_value,
        "total_unrealized_pnl": total_unrealized_pnl,
        "total_equity_estimate": cash_krw + total_position_value,
        "positions": position_rows,
        "trades": [dict(trade) for trade in trades],
    }


def latest_ticker_prices(conn: sqlite3.Connection) -> dict[str, float]:
    rows = conn.execute(
        """
        SELECT market, trade_price
        FROM ticker_snapshots
        WHERE id IN (
            SELECT MAX(id)
            FROM ticker_snapshots
            WHERE trade_price IS NOT NULL
            GROUP BY market
        )
        """
    ).fetchall()
    return {row["market"]: float(row["trade_price"]) for row in rows}


def paper_trading_panel(state: dict[str, Any] | None) -> Panel:
    if state is None:
        return Panel("No paper account yet", title="Paper Trading")

    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="bold")
    summary.add_column(justify="right")
    summary.add_row("Cash KRW", format_number(state["cash_krw"]))
    summary.add_row("Position Value", format_number(state["total_position_value"]))
    summary.add_row("Equity Estimate", format_number(state["total_equity_estimate"]))
    summary.add_row("Unrealized PnL", format_number(state["total_unrealized_pnl"]))

    positions = Table(title="Positions")
    positions.add_column("Market")
    positions.add_column("Quantity", justify="right")
    positions.add_column("Avg Entry", justify="right")
    positions.add_column("Latest Price", justify="right")
    positions.add_column("Value", justify="right")
    positions.add_column("Unrealized PnL", justify="right")
    for position in state["positions"]:
        positions.add_row(
            position["market"],
            format_number(position["quantity"]),
            format_number(position["average_entry_price"]),
            format_number(position["latest_price"]),
            format_number(position["market_value"]),
            format_number(position["unrealized_pnl"]),
        )
    if not state["positions"]:
        positions.add_row("-", "-", "-", "-", "-", "-")

    trades = Table(title="Latest Trades")
    trades.add_column("Timestamp")
    trades.add_column("Side")
    trades.add_column("Market")
    trades.add_column("Price", justify="right")
    trades.add_column("Quantity", justify="right")
    trades.add_column("Notional", justify="right")
    trades.add_column("Fee", justify="right")
    for trade in state["trades"]:
        trades.add_row(
            str(trade["ts"]),
            str(trade["side"]),
            str(trade["market"]),
            format_number(trade["price"]),
            format_number(trade["quantity"]),
            format_number(trade["notional_krw"]),
            format_number(trade["fee_krw"]),
        )
    if not state["trades"]:
        trades.add_row("-", "-", "-", "-", "-", "-", "-")

    return Panel(Group(summary, positions, trades), title="Paper Trading")


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
