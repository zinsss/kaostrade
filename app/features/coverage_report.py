from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import yaml

from app.collector.collect_once import CONFIG_PATH, DEFAULT_DB_PATH
from app.data.db import connect, init_schema

BACKFILL_SOURCE = "backfill"


def main() -> None:
    config = load_config(CONFIG_PATH)
    db_path = config.get("database", {}).get("path", DEFAULT_DB_PATH)
    markets = configured_markets(config)

    with connect(db_path) as conn:
        init_schema(conn)
        report = {
            "market_features": market_feature_coverage(conn, BACKFILL_SOURCE),
            "candles": candle_coverage(conn, markets),
        }

    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


def market_feature_coverage(conn: sqlite3.Connection, source: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total_rows,
            SUM(CASE WHEN btc_return_1h IS NOT NULL THEN 1 ELSE 0 END) AS rows_with_btc_return_1h,
            SUM(CASE WHEN btc_return_4h IS NOT NULL THEN 1 ELSE 0 END) AS rows_with_btc_return_4h,
            SUM(CASE WHEN median_return_1h IS NOT NULL THEN 1 ELSE 0 END) AS rows_with_median_return_1h,
            SUM(CASE WHEN median_return_4h IS NOT NULL THEN 1 ELSE 0 END) AS rows_with_median_return_4h
        FROM market_features
        WHERE source = ?
        """,
        (source,),
    ).fetchone()
    total_rows = int(row["total_rows"] or 0)
    counts = {
        "total_rows": total_rows,
        "rows_with_btc_return_1h": int(row["rows_with_btc_return_1h"] or 0),
        "rows_with_btc_return_4h": int(row["rows_with_btc_return_4h"] or 0),
        "rows_with_median_return_1h": int(row["rows_with_median_return_1h"] or 0),
        "rows_with_median_return_4h": int(row["rows_with_median_return_4h"] or 0),
    }
    counts["coverage_pct"] = {
        key.removeprefix("rows_with_"): pct(value, total_rows)
        for key, value in counts.items()
        if key.startswith("rows_with_")
    }
    return counts


def candle_coverage(conn: sqlite3.Connection, markets: list[str]) -> list[dict[str, Any]]:
    rows = []
    for market in markets:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total_1m_candles,
                MIN(candle_date_time_utc) AS oldest_candle,
                MAX(candle_date_time_utc) AS newest_candle
            FROM candles
            WHERE market = ? AND interval = '1m'
            """,
            (market,),
        ).fetchone()
        rows.append(
            {
                "market": market,
                "total_1m_candles": int(row["total_1m_candles"] or 0),
                "oldest_candle": row["oldest_candle"],
                "newest_candle": row["newest_candle"],
            }
        )
    return rows


def pct(value: int, total: int) -> float:
    if total == 0:
        return 0.0
    return round(value / total * 100, 2)


def configured_markets(config: dict[str, Any]) -> list[str]:
    symbols = config.get("collector", {}).get("static_whitelist", [])
    if not symbols:
        raise ValueError("collector.static_whitelist is empty")

    unique_symbols = []
    seen = set()
    for symbol in symbols:
        if symbol not in seen:
            unique_symbols.append(symbol)
            seen.add(symbol)
    return unique_symbols


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file) or {}
    if not isinstance(config, dict):
        raise ValueError(f"Config must be a mapping: {path}")
    return config


if __name__ == "__main__":
    main()
