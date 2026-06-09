from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


def upsert_markets(conn: sqlite3.Connection, markets: Iterable[dict[str, Any]], collected_at: str) -> int:
    rows = [
        (
            market["market"],
            market.get("korean_name"),
            market.get("english_name"),
            market.get("market_warning"),
            collected_at,
        )
        for market in markets
    ]
    conn.executemany(
        """
        INSERT INTO markets (market, korean_name, english_name, market_warning, collected_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(market) DO UPDATE SET
            korean_name = excluded.korean_name,
            english_name = excluded.english_name,
            market_warning = excluded.market_warning,
            collected_at = excluded.collected_at
        """,
        rows,
    )
    return len(rows)


def insert_ticker_snapshots(conn: sqlite3.Connection, tickers: Iterable[dict[str, Any]], collected_at: str) -> int:
    rows = [
        (
            ticker["market"],
            collected_at,
            _as_int(ticker.get("timestamp")),
            _as_int(ticker.get("trade_timestamp")),
            _as_float(ticker.get("opening_price")),
            _as_float(ticker.get("high_price")),
            _as_float(ticker.get("low_price")),
            _as_float(ticker.get("trade_price")),
            _as_float(ticker.get("prev_closing_price")),
            ticker.get("change"),
            _as_float(ticker.get("change_price")),
            _as_float(ticker.get("change_rate")),
            _as_float(ticker.get("signed_change_price")),
            _as_float(ticker.get("signed_change_rate")),
            _as_float(ticker.get("trade_volume")),
            _as_float(ticker.get("acc_trade_price")),
            _as_float(ticker.get("acc_trade_price_24h")),
            _as_float(ticker.get("acc_trade_volume")),
            _as_float(ticker.get("acc_trade_volume_24h")),
            json.dumps(ticker, ensure_ascii=False, sort_keys=True),
        )
        for ticker in tickers
    ]
    conn.executemany(
        """
        INSERT INTO ticker_snapshots (
            market, collected_at, bithumb_timestamp, trade_timestamp,
            opening_price, high_price, low_price, trade_price, prev_closing_price,
            change, change_price, change_rate, signed_change_price, signed_change_rate,
            trade_volume, acc_trade_price, acc_trade_price_24h, acc_trade_volume,
            acc_trade_volume_24h, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def insert_orderbook_snapshots(conn: sqlite3.Connection, orderbooks: Iterable[dict[str, Any]], collected_at: str) -> int:
    rows = [
        (
            orderbook["market"],
            collected_at,
            _as_int(orderbook.get("timestamp")),
            _as_float(orderbook.get("total_ask_size")),
            _as_float(orderbook.get("total_bid_size")),
            json.dumps(orderbook, ensure_ascii=False, sort_keys=True),
        )
        for orderbook in orderbooks
    ]
    conn.executemany(
        """
        INSERT INTO orderbook_snapshots (
            market, collected_at, bithumb_timestamp, total_ask_size, total_bid_size, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)
