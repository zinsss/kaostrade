from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any

import yaml

from app.data.db import connect, init_schema, insert_market_features, insert_market_regime
from app.features.market_features import parse_utc_datetime, positive_ratio
from app.regime.rule_based import classify_features

CONFIG_PATH = Path("/app/config.yaml")
DEFAULT_DB_PATH = "/app/data/kaostrade.sqlite"
DEFAULT_STEP_MINUTES = 5
DEFAULT_TOLERANCE_MINUTES = 5


def main() -> None:
    args = parse_args()
    started_at = time.monotonic()
    config = load_config(CONFIG_PATH)
    db_path = config.get("database", {}).get("path", DEFAULT_DB_PATH)
    markets = configured_markets(config)

    with connect(db_path) as conn:
        init_schema(conn)
        summary = backfill_features_and_regimes(
            conn=conn,
            markets=markets,
            days=args.days,
            step_minutes=args.step_minutes,
            tolerance_minutes=args.tolerance_minutes,
        )

    summary["elapsed_seconds"] = round(time.monotonic() - started_at, 2)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill historical market features and regimes from candles.")
    parser.add_argument("--days", type=positive_int, required=True)
    parser.add_argument("--step-minutes", type=positive_int, default=DEFAULT_STEP_MINUTES)
    parser.add_argument("--tolerance-minutes", type=positive_int, default=DEFAULT_TOLERANCE_MINUTES)
    return parser.parse_args()


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def backfill_features_and_regimes(
    conn: sqlite3.Connection,
    markets: list[str],
    days: int,
    step_minutes: int = DEFAULT_STEP_MINUTES,
    tolerance_minutes: int = DEFAULT_TOLERANCE_MINUTES,
) -> dict[str, Any]:
    end_time = latest_candle_time(conn, markets)
    if end_time is None:
        return empty_summary(markets, days, step_minutes, "No 1m candles found for configured markets")

    start_time = end_time - timedelta(days=days)
    timestamps = candidate_timestamps(conn, markets, start_time, step_minutes)
    summary = {
        "days": days,
        "step_minutes": step_minutes,
        "markets": markets,
        "timestamps_seen": len(timestamps),
        "features_inserted": 0,
        "features_existing": 0,
        "regimes_inserted": 0,
        "regimes_existing": 0,
        "skipped_count": 0,
        "first_ts": timestamps[0] if timestamps else None,
        "last_ts": timestamps[-1] if timestamps else None,
    }

    for ts in timestamps:
        existing_feature = market_feature_by_ts(conn, ts)
        if existing_feature is None:
            features = historical_features(conn, markets, ts, tolerance_minutes)
            if features is None:
                summary["skipped_count"] += 1
                continue
            feature_id = insert_market_features(conn, features)
            conn.commit()
            features["id"] = feature_id
            summary["features_inserted"] += 1
        else:
            features = dict(existing_feature)
            summary["features_existing"] += 1

        if market_regime_by_ts(conn, ts) is not None:
            summary["regimes_existing"] += 1
            continue

        regime, reason = classify_features(features)
        regime_row = {
            "ts": ts,
            "regime": regime,
            "reason": reason,
            "market_features_id": features["id"],
            "btc_return_1h": features.get("btc_return_1h"),
            "eth_return_1h": features.get("eth_return_1h"),
            "median_return_1h": features.get("median_return_1h"),
            "positive_ratio": features.get("positive_ratio"),
            "average_spread_pct": features.get("average_spread_pct"),
            "average_imbalance_5": features.get("average_imbalance_5"),
            "market_count": features.get("market_count"),
        }
        insert_market_regime(conn, regime_row)
        conn.commit()
        summary["regimes_inserted"] += 1

    return summary


def latest_candle_time(conn: sqlite3.Connection, markets: list[str]) -> datetime | None:
    row = conn.execute(
        f"""
        SELECT MAX(candle_date_time_utc) AS max_ts
        FROM candles
        WHERE interval = '1m'
          AND market IN ({placeholders(markets)})
          AND candle_date_time_utc IS NOT NULL
        """,
        markets,
    ).fetchone()
    if row is None or row["max_ts"] is None:
        return None
    return parse_utc_datetime(row["max_ts"])


def candidate_timestamps(
    conn: sqlite3.Connection,
    markets: list[str],
    start_time: datetime,
    step_minutes: int,
) -> list[str]:
    rows = conn.execute(
        f"""
        SELECT DISTINCT candle_date_time_utc
        FROM candles
        WHERE interval = '1m'
          AND market IN ({placeholders(markets)})
          AND candle_date_time_utc >= ?
          AND candle_date_time_utc IS NOT NULL
        ORDER BY candle_date_time_utc
        """,
        [*markets, format_utc(start_time)],
    ).fetchall()
    timestamps = []
    for row in rows:
        ts = row["candle_date_time_utc"]
        parsed = parse_utc_datetime(ts)
        if parsed.minute % step_minutes == 0 and parsed.second == 0:
            timestamps.append(ts)
    return timestamps


def historical_features(
    conn: sqlite3.Connection,
    markets: list[str],
    ts: str,
    tolerance_minutes: int,
) -> dict[str, Any] | None:
    returns_by_market = historical_1h_returns(conn, markets, ts, tolerance_minutes)
    returns = list(returns_by_market.values())
    if not returns:
        return None

    return {
        "ts": ts,
        "btc_return_1h": returns_by_market.get("KRW-BTC"),
        "eth_return_1h": returns_by_market.get("KRW-ETH"),
        "median_return_1h": median(returns),
        "positive_ratio": positive_ratio(returns),
        "average_spread_pct": None,
        "average_imbalance_5": None,
        "market_count": len(returns),
    }


def historical_1h_returns(
    conn: sqlite3.Connection,
    markets: list[str],
    ts: str,
    tolerance_minutes: int,
) -> dict[str, float]:
    current_time = parse_utc_datetime(ts)
    target_time = current_time - timedelta(hours=1)
    returns = {}

    for market in markets:
        current_price = exact_candle_price(conn, market, ts)
        if current_price is None or current_price <= 0:
            continue
        comparison = closest_candle_price(conn, market, target_time, tolerance_minutes)
        if comparison is None or comparison <= 0:
            continue
        returns[market] = (current_price - comparison) / comparison

    return returns


def exact_candle_price(conn: sqlite3.Connection, market: str, ts: str) -> float | None:
    row = conn.execute(
        """
        SELECT trade_price
        FROM candles
        WHERE market = ?
          AND interval = '1m'
          AND candle_date_time_utc = ?
          AND trade_price IS NOT NULL
        LIMIT 1
        """,
        (market, ts),
    ).fetchone()
    return float(row["trade_price"]) if row is not None else None


def closest_candle_price(
    conn: sqlite3.Connection,
    market: str,
    target_time: datetime,
    tolerance_minutes: int,
) -> float | None:
    tolerance = timedelta(minutes=tolerance_minutes)
    rows = conn.execute(
        """
        SELECT trade_price, candle_date_time_utc
        FROM candles
        WHERE market = ?
          AND interval = '1m'
          AND trade_price IS NOT NULL
          AND candle_date_time_utc BETWEEN ? AND ?
        """,
        (
            market,
            format_utc(target_time - tolerance),
            format_utc(target_time + tolerance),
        ),
    ).fetchall()
    if not rows:
        return None
    closest = min(rows, key=lambda row: abs(parse_utc_datetime(row["candle_date_time_utc"]) - target_time))
    return float(closest["trade_price"])


def market_feature_by_ts(conn: sqlite3.Connection, ts: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT
            id,
            ts,
            btc_return_1h,
            eth_return_1h,
            median_return_1h,
            positive_ratio,
            average_spread_pct,
            average_imbalance_5,
            market_count
        FROM market_features
        WHERE ts = ?
        ORDER BY id ASC
        LIMIT 1
        """,
        (ts,),
    ).fetchone()


def market_regime_by_ts(conn: sqlite3.Connection, ts: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT id
        FROM market_regimes
        WHERE ts = ?
        ORDER BY id ASC
        LIMIT 1
        """,
        (ts,),
    ).fetchone()


def empty_summary(markets: list[str], days: int, step_minutes: int, reason: str) -> dict[str, Any]:
    return {
        "days": days,
        "step_minutes": step_minutes,
        "markets": markets,
        "timestamps_seen": 0,
        "features_inserted": 0,
        "features_existing": 0,
        "regimes_inserted": 0,
        "regimes_existing": 0,
        "skipped_count": 0,
        "first_ts": None,
        "last_ts": None,
        "skipped_reason": reason,
    }


def configured_markets(config: dict[str, Any]) -> list[str]:
    symbols = config.get("collector", {}).get("static_whitelist", [])
    if not symbols:
        raise ValueError("collector.static_whitelist is empty; no configured markets to backfill")

    unique_symbols = []
    seen = set()
    for symbol in symbols:
        if symbol not in seen:
            unique_symbols.append(symbol)
            seen.add(symbol)
    return unique_symbols


def placeholders(values: list[Any]) -> str:
    if not values:
        raise ValueError("At least one value is required")
    return ", ".join("?" for _ in values)


def format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file) or {}
    if not isinstance(config, dict):
        raise ValueError(f"Config must be a mapping: {path}")
    return config


if __name__ == "__main__":
    main()
