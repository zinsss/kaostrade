from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from app.data.db import (
    connect,
    init_schema,
    insert_candles,
    insert_orderbook_snapshots,
    insert_ticker_snapshots,
    upsert_markets,
)
from app.exchange.bithumb_public import BithumbPublicClient

CONFIG_PATH = Path("/app/config.yaml")
DEFAULT_DB_PATH = "/app/data/kaostrade.sqlite"
DEFAULT_CANDLE_INTERVALS = ["1m", "5m", "15m"]


def main() -> None:
    config = load_config(CONFIG_PATH)
    db_path = config.get("database", {}).get("path", DEFAULT_DB_PATH)
    collected_at = datetime.now(timezone.utc).isoformat()

    with connect(db_path) as conn:
        init_schema(conn)
        with BithumbPublicClient() as bithumb:
            counts = collect_snapshots(conn, bithumb, config, collected_at)

    print(format_summary(counts, db_path))


def collect_snapshots(
    conn,
    bithumb: BithumbPublicClient,
    config: dict[str, Any],
    collected_at: str,
) -> dict[str, int]:
    krw_markets = [market for market in bithumb.get_markets() if market.get("market", "").startswith("KRW-")]
    selected_markets = filter_static_whitelist(krw_markets, config)
    market_symbols = [market["market"] for market in selected_markets]
    tickers = bithumb.get_tickers(market_symbols)
    orderbooks = bithumb.get_orderbooks(market_symbols)

    markets_count = upsert_markets(conn, selected_markets, collected_at)
    ticker_count = insert_ticker_snapshots(conn, tickers, collected_at)
    orderbook_count = insert_orderbook_snapshots(conn, orderbooks, collected_at)
    candles_count = collect_candles(conn, bithumb, market_symbols, get_candle_intervals(config))
    conn.commit()

    return {
        "markets": markets_count,
        "ticker": ticker_count,
        "orderbook": orderbook_count,
        "candles": candles_count,
    }


def collect_candles(
    conn,
    bithumb: BithumbPublicClient,
    markets: list[str],
    intervals: list[str],
) -> int:
    candles_count = 0
    for market in markets:
        for interval in intervals:
            candles = bithumb.get_candles(market=market, interval=interval)
            candles_count += insert_candles(conn, candles, interval)
    return candles_count


def format_summary(counts: dict[str, int], db_path: str) -> str:
    return (
        "markets={markets} ticker={ticker} orderbook={orderbook} candles={candles} db={db}".format(
            markets=counts.get("markets", 0),
            ticker=counts.get("ticker", 0),
            orderbook=counts.get("orderbook", 0),
            candles=counts.get("candles", 0),
            db=db_path,
        )
    )


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file) or {}
    if not isinstance(config, dict):
        raise ValueError(f"Config must be a mapping: {path}")
    return config


def filter_static_whitelist(markets: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    whitelist = config.get("collector", {}).get("static_whitelist", [])
    if not whitelist:
        return markets

    available_by_symbol = {market["market"]: market for market in markets}
    selected = [available_by_symbol[symbol] for symbol in whitelist if symbol in available_by_symbol]
    missing = [symbol for symbol in whitelist if symbol not in available_by_symbol]
    if missing:
        print("Skipping unavailable whitelist markets: " + ", ".join(missing))
    return selected


def get_candle_intervals(config: dict[str, Any]) -> list[str]:
    intervals = config.get("collector", {}).get("candle_intervals", DEFAULT_CANDLE_INTERVALS)
    if not isinstance(intervals, list):
        raise ValueError("collector.candle_intervals must be a list")
    return [str(interval) for interval in intervals]


if __name__ == "__main__":
    main()
