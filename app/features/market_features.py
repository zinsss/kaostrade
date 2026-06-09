from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any

import yaml

from app.data.db import connect, init_schema, insert_market_features

CONFIG_PATH = Path("/app/config.yaml")
DEFAULT_DB_PATH = "/app/data/kaostrade.sqlite"


def main() -> None:
    config = load_config(CONFIG_PATH)
    db_path = config.get("database", {}).get("path", DEFAULT_DB_PATH)

    with connect(db_path) as conn:
        init_schema(conn)
        features = generate_market_features(conn)

    print(json.dumps(features, ensure_ascii=False, sort_keys=True))


def generate_market_features(conn: sqlite3.Connection) -> dict[str, Any]:
    returns_by_market = latest_1h_returns(conn)
    returns = list(returns_by_market.values())
    market_count = len(returns)

    features = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "btc_return_1h": returns_by_market.get("KRW-BTC"),
        "eth_return_1h": returns_by_market.get("KRW-ETH"),
        "median_return_1h": median(returns) if returns else None,
        "positive_ratio": positive_ratio(returns),
        "average_spread_pct": latest_average(conn, "spread_pct"),
        "average_imbalance_5": latest_average(conn, "imbalance_5"),
        "market_count": market_count,
    }
    feature_id = insert_market_features(conn, features)
    conn.commit()
    features["id"] = feature_id
    return features


def latest_1h_returns(conn: sqlite3.Connection, tolerance_minutes: int = 5) -> dict[str, float]:
    latest_rows = conn.execute(
        """
        SELECT market, trade_price, candle_date_time_utc
        FROM (
            SELECT
                market,
                trade_price,
                candle_date_time_utc,
                ROW_NUMBER() OVER (PARTITION BY market ORDER BY candle_date_time_utc DESC) AS row_num
            FROM candles
            WHERE interval = ? AND trade_price IS NOT NULL AND candle_date_time_utc IS NOT NULL
        ) latest
        WHERE row_num = 1
        ORDER BY market
        """,
        ("1m",),
    ).fetchall()

    returns = {}
    tolerance = timedelta(minutes=tolerance_minutes)
    for row in latest_rows:
        market = row["market"]
        latest_price = float(row["trade_price"])
        latest_time = parse_utc_datetime(row["candle_date_time_utc"])
        target_time = latest_time - timedelta(hours=1)
        comparison = closest_comparison_candle(conn, market, target_time, tolerance_minutes)
        if comparison is None:
            continue

        comparison_time = parse_utc_datetime(comparison["candle_date_time_utc"])
        if abs(comparison_time - target_time) > tolerance:
            continue

        comparison_price = float(comparison["trade_price"])
        if comparison_price <= 0:
            continue
        returns[market] = (latest_price - comparison_price) / comparison_price
    return returns


def closest_comparison_candle(
    conn: sqlite3.Connection,
    market: str,
    target_time: datetime,
    tolerance_minutes: int,
) -> sqlite3.Row | None:
    tolerance = timedelta(minutes=tolerance_minutes)
    start_time = (target_time - tolerance).strftime("%Y-%m-%dT%H:%M:%S")
    end_time = (target_time + tolerance).strftime("%Y-%m-%dT%H:%M:%S")
    rows = conn.execute(
        """
        SELECT trade_price, candle_date_time_utc
        FROM candles
        WHERE market = ?
          AND interval = ?
          AND trade_price IS NOT NULL
          AND candle_date_time_utc IS NOT NULL
          AND candle_date_time_utc BETWEEN ? AND ?
        """,
        (market, "1m", start_time, end_time),
    ).fetchall()
    if not rows:
        return None
    return min(rows, key=lambda row: abs(parse_utc_datetime(row["candle_date_time_utc"]) - target_time))


def parse_utc_datetime(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def latest_average(conn: sqlite3.Connection, column: str) -> float | None:
    if column not in {"spread_pct", "imbalance_5"}:
        raise ValueError(f"Unsupported orderbook feature column: {column}")

    row = conn.execute(
        f"""
        SELECT AVG({column}) AS average_value
        FROM orderbook_snapshots o
        JOIN (
            SELECT market, MAX(id) AS id
            FROM orderbook_snapshots
            WHERE {column} IS NOT NULL
            GROUP BY market
        ) latest ON latest.id = o.id
        """
    ).fetchone()
    return float(row["average_value"]) if row and row["average_value"] is not None else None


def positive_ratio(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(1 for value in values if value > 0) / len(values)


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file) or {}
    if not isinstance(config, dict):
        raise ValueError(f"Config must be a mapping: {path}")
    return config


if __name__ == "__main__":
    main()
