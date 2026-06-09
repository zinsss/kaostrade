from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
ORDERBOOK_METRIC_COLUMNS = {
    "best_ask_price": "REAL",
    "best_bid_price": "REAL",
    "spread_pct": "REAL",
    "ask_depth_5": "REAL",
    "bid_depth_5": "REAL",
    "imbalance_5": "REAL",
}


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    migrate_orderbook_metric_columns(conn)
    conn.commit()


def migrate_orderbook_metric_columns(conn: sqlite3.Connection) -> None:
    existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(orderbook_snapshots)")}
    for column, column_type in ORDERBOOK_METRIC_COLUMNS.items():
        if column not in existing_columns:
            conn.execute(f"ALTER TABLE orderbook_snapshots ADD COLUMN {column} {column_type}")


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
    rows = []
    for orderbook in orderbooks:
        metrics = derive_orderbook_metrics(orderbook.get("orderbook_units", []))
        rows.append(
            (
                orderbook["market"],
                collected_at,
                _as_int(orderbook.get("timestamp")),
                _as_float(orderbook.get("total_ask_size")),
                _as_float(orderbook.get("total_bid_size")),
                metrics["best_ask_price"],
                metrics["best_bid_price"],
                metrics["spread_pct"],
                metrics["ask_depth_5"],
                metrics["bid_depth_5"],
                metrics["imbalance_5"],
                json.dumps(orderbook, ensure_ascii=False, sort_keys=True),
            )
        )
    conn.executemany(
        """
        INSERT INTO orderbook_snapshots (
            market, collected_at, bithumb_timestamp, total_ask_size, total_bid_size,
            best_ask_price, best_bid_price, spread_pct, ask_depth_5, bid_depth_5, imbalance_5,
            raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def derive_orderbook_metrics(orderbook_units: Any) -> dict[str, float | None]:
    if not isinstance(orderbook_units, list) or not orderbook_units:
        return _empty_orderbook_metrics()

    ask_prices = _unit_values(orderbook_units, "ask_price")
    bid_prices = _unit_values(orderbook_units, "bid_price")
    best_ask_price = min(ask_prices) if ask_prices else None
    best_bid_price = max(bid_prices) if bid_prices else None

    if best_ask_price is not None and best_bid_price is not None and best_ask_price < best_bid_price:
        best_ask_price, best_bid_price = best_bid_price, best_ask_price

    spread_pct = None
    if best_ask_price is not None and best_bid_price:
        spread_pct = (best_ask_price - best_bid_price) / best_bid_price * 100

    ask_depth_5 = _depth_krw(orderbook_units[:5], price_key="ask_price", size_key="ask_size")
    bid_depth_5 = _depth_krw(orderbook_units[:5], price_key="bid_price", size_key="bid_size")
    imbalance_5 = None
    if ask_depth_5 is not None and bid_depth_5 is not None and ask_depth_5 + bid_depth_5 > 0:
        imbalance_5 = bid_depth_5 / (bid_depth_5 + ask_depth_5)

    return {
        "best_ask_price": best_ask_price,
        "best_bid_price": best_bid_price,
        "spread_pct": spread_pct,
        "ask_depth_5": ask_depth_5,
        "bid_depth_5": bid_depth_5,
        "imbalance_5": imbalance_5,
    }


def _empty_orderbook_metrics() -> dict[str, float | None]:
    return {
        "best_ask_price": None,
        "best_bid_price": None,
        "spread_pct": None,
        "ask_depth_5": None,
        "bid_depth_5": None,
        "imbalance_5": None,
    }


def _unit_values(units: list[Any], key: str) -> list[float]:
    values = []
    for unit in units:
        if not isinstance(unit, dict):
            continue
        value = _as_float(unit.get(key))
        if value is not None:
            values.append(value)
    return values


def _depth_krw(units: list[Any], price_key: str, size_key: str) -> float | None:
    depth = 0.0
    found = False
    for unit in units:
        if not isinstance(unit, dict):
            continue
        price = _as_float(unit.get(price_key))
        size = _as_float(unit.get(size_key))
        if price is None or size is None:
            continue
        depth += price * size
        found = True
    return depth if found else None


def insert_candles(conn: sqlite3.Connection, candles: Iterable[dict[str, Any]], interval: str) -> int:
    rows = [
        (
            candle["market"],
            interval,
            candle["candle_date_time_utc"],
            candle.get("candle_date_time_kst"),
            _as_float(candle.get("opening_price")),
            _as_float(candle.get("high_price")),
            _as_float(candle.get("low_price")),
            _as_float(candle.get("trade_price")),
            _as_float(candle.get("candle_acc_trade_price")),
            _as_float(candle.get("candle_acc_trade_volume")),
            _as_int(candle.get("timestamp")),
            json.dumps(candle, ensure_ascii=False, sort_keys=True),
        )
        for candle in candles
    ]
    cursor = conn.executemany(
        """
        INSERT OR IGNORE INTO candles (
            market, interval, candle_date_time_utc, candle_date_time_kst,
            opening_price, high_price, low_price, trade_price,
            candle_acc_trade_price, candle_acc_trade_volume, timestamp, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return cursor.rowcount if cursor.rowcount != -1 else len(rows)


def insert_market_features(conn: sqlite3.Connection, features: dict[str, Any]) -> int:
    conn.execute(
        """
        INSERT INTO market_features (
            ts, btc_return_1h, eth_return_1h, median_return_1h, positive_ratio,
            average_spread_pct, average_imbalance_5, market_count
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            features["ts"],
            _as_float(features.get("btc_return_1h")),
            _as_float(features.get("eth_return_1h")),
            _as_float(features.get("median_return_1h")),
            _as_float(features.get("positive_ratio")),
            _as_float(features.get("average_spread_pct")),
            _as_float(features.get("average_imbalance_5")),
            _as_int(features.get("market_count")),
        ),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def insert_market_regime(conn: sqlite3.Connection, regime: dict[str, Any]) -> int:
    conn.execute(
        """
        INSERT INTO market_regimes (
            ts, regime, reason, market_features_id, btc_return_1h, eth_return_1h,
            median_return_1h, positive_ratio, average_spread_pct, average_imbalance_5, market_count
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            regime["ts"],
            regime["regime"],
            regime["reason"],
            _as_int(regime.get("market_features_id")),
            _as_float(regime.get("btc_return_1h")),
            _as_float(regime.get("eth_return_1h")),
            _as_float(regime.get("median_return_1h")),
            _as_float(regime.get("positive_ratio")),
            _as_float(regime.get("average_spread_pct")),
            _as_float(regime.get("average_imbalance_5")),
            _as_int(regime.get("market_count")),
        ),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)
