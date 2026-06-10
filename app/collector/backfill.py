from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from app.collector.collect_once import CONFIG_PATH, DEFAULT_DB_PATH, load_config
from app.data.db import connect, init_schema, insert_candles, upsert_markets
from app.exchange.bithumb_public import BithumbPublicClient

SUPPORTED_INTERVALS = ("1m", "5m", "15m", "1h")
REQUEST_COUNT = 200
REQUEST_SLEEP_SECONDS = 0.15


def main() -> None:
    args = parse_args()
    config = load_config(CONFIG_PATH)
    db_path = config.get("database", {}).get("path", DEFAULT_DB_PATH)
    started_at = time.monotonic()

    with connect(db_path) as conn:
        init_schema(conn)
        with BithumbPublicClient() as bithumb:
            markets = resolve_markets(bithumb, args)
            collected_at = datetime.now(timezone.utc).isoformat()
            upsert_markets(conn, markets, collected_at)
            conn.commit()

            total_inserted = 0
            oldest_seen = None
            newest_seen = None
            for market in markets:
                result = backfill_market(
                    conn=conn,
                    bithumb=bithumb,
                    market=market["market"],
                    interval=args.interval,
                    days=args.days,
                    sleep_seconds=args.sleep_seconds,
                )
                total_inserted += result["inserted"]
                oldest_seen = min_timestamp(oldest_seen, result["oldest"])
                newest_seen = max_timestamp(newest_seen, result["newest"])
                print(
                    f"market={market['market']} interval={args.interval} "
                    f"inserted={result['inserted']} oldest={result['oldest'] or '-'}",
                    flush=True,
                )

    elapsed_seconds = time.monotonic() - started_at
    print(
        "summary "
        f"markets_processed={len(markets)} "
        f"candles_inserted={total_inserted} "
        f"oldest_timestamp={oldest_seen or '-'} "
        f"newest_timestamp={newest_seen or '-'} "
        f"elapsed_seconds={elapsed_seconds:.2f}",
        flush=True,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill Bithumb public candles into SQLite.")
    market_group = parser.add_mutually_exclusive_group(required=True)
    market_group.add_argument("--market", action="append", dest="markets")
    market_group.add_argument("--all-markets", action="store_true")
    parser.add_argument("--interval", choices=SUPPORTED_INTERVALS, required=True)
    parser.add_argument("--days", type=positive_int, required=True)
    parser.add_argument("--sleep-seconds", type=non_negative_float, default=REQUEST_SLEEP_SECONDS)
    return parser.parse_args()


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def non_negative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return parsed


def resolve_markets(bithumb: BithumbPublicClient, args: argparse.Namespace) -> list[dict[str, Any]]:
    krw_markets = [market for market in bithumb.get_markets() if market.get("market", "").startswith("KRW-")]
    by_symbol = {market["market"]: market for market in krw_markets}
    if args.all_markets:
        return sorted(krw_markets, key=lambda market: market["market"])

    selected = []
    missing = []
    for symbol in args.markets or []:
        market = by_symbol.get(symbol)
        if market is None:
            missing.append(symbol)
        else:
            selected.append(market)
    if missing:
        raise ValueError("Unknown or non-KRW market(s): " + ", ".join(missing))
    return selected


def backfill_market(
    conn,
    bithumb: BithumbPublicClient,
    market: str,
    interval: str,
    days: int,
    sleep_seconds: float,
) -> dict[str, Any]:
    target_oldest = datetime.now(timezone.utc) - timedelta(days=days)
    page_to = None
    inserted = 0
    oldest = None
    newest = None
    seen_oldest = None

    while True:
        candles = bithumb.get_candles(market=market, interval=interval, count=REQUEST_COUNT, to=page_to)
        if not candles:
            break

        inserted += insert_candles(conn, candles, interval)
        conn.commit()

        timestamps = [candle["candle_date_time_utc"] for candle in candles if candle.get("candle_date_time_utc")]
        if not timestamps:
            break
        page_oldest = min(timestamps)
        page_newest = max(timestamps)
        oldest = min_timestamp(oldest, page_oldest)
        newest = max_timestamp(newest, page_newest)

        oldest_dt = parse_utc_datetime(page_oldest)
        if oldest_dt <= target_oldest:
            break
        if seen_oldest == page_oldest:
            break
        seen_oldest = page_oldest
        page_to = page_oldest
        time.sleep(sleep_seconds)

    return {"inserted": inserted, "oldest": oldest, "newest": newest}


def min_timestamp(left: str | None, right: str | None) -> str | None:
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right)


def max_timestamp(left: str | None, right: str | None) -> str | None:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)


def parse_utc_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


if __name__ == "__main__":
    main()
