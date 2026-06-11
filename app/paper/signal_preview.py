from __future__ import annotations

import argparse
import sqlite3
from typing import Any

from rich.console import Console
from rich.table import Table

from app.backtest.candle_strategy import (
    Candle,
    DEFAULT_DAYS,
    DEFAULT_INTERVAL,
    connect_read_only,
    filter_signals_by_gap,
    load_candles,
    strategy_signals,
    validate_strategy_interval,
)
from app.backtest.strategy_profiles import get_strategy_profile, profile_names
from app.collector.collect_once import CONFIG_PATH, DEFAULT_DB_PATH, load_config

POSITION_ASSUMPTION = "FLAT"


def main() -> None:
    args = parse_args()
    config = load_config(CONFIG_PATH)
    db_path = config.get("database", {}).get("path", DEFAULT_DB_PATH)
    profile = get_strategy_profile(args.profile)

    with connect_read_only(db_path) as conn:
        rows = preview_profile_signals(conn, profile)

    print_signal_preview(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview current paper strategy signals without writing state.")
    parser.add_argument("--profile", choices=profile_names(), required=True)
    return parser.parse_args()


def preview_profile_signals(conn: sqlite3.Connection, profile: dict[str, Any]) -> list[dict[str, Any]]:
    strategy = str(profile["strategy"])
    interval = str(profile.get("interval", DEFAULT_INTERVAL))
    validate_strategy_interval(strategy, interval)

    markets = profile.get("markets") or []
    if not markets:
        raise ValueError("Strategy profile has no markets")

    return [preview_market_signal(conn, profile, market, strategy, interval) for market in markets]


def preview_market_signal(
    conn: sqlite3.Connection,
    profile: dict[str, Any],
    market: str,
    strategy: str,
    interval: str,
) -> dict[str, Any]:
    candles = load_candles(
        conn,
        market,
        interval,
        int(profile.get("days", DEFAULT_DAYS)),
    )
    if not candles:
        return signal_preview_row(market, None, None, "HOLD", "No candles available")

    accepted_signals = filter_signals_by_gap(
        strategy_signals(
            strategy,
            candles,
            bollinger_period=int(profile["bollinger_period"]),
            bollinger_stddev=float(profile["bollinger_stddev"]),
            rsi_buy_threshold=float(profile["rsi_buy_threshold"]),
            rsi_sell_threshold=float(profile["rsi_sell_threshold"]),
        ),
        int(profile.get("min_signal_gap_minutes", 0)),
    )
    return current_signal_preview_row(market, candles[-1], accepted_signals)


def current_signal_preview_row(
    market: str,
    latest_candle: Candle,
    accepted_signals: list[dict[str, Any]],
) -> dict[str, Any]:
    latest_signal = next(
        (signal for signal in reversed(accepted_signals) if signal["ts"] == latest_candle.ts),
        None,
    )
    if latest_signal is None:
        return signal_preview_row(
            market,
            latest_candle.ts,
            latest_candle.price,
            "HOLD",
            "No accepted signal on latest candle",
        )

    signal = str(latest_signal["signal"])
    return signal_preview_row(
        market,
        latest_candle.ts,
        latest_candle.price,
        signal,
        f"Latest candle generated accepted {signal} signal",
    )


def signal_preview_row(
    market: str,
    latest_timestamp: str | None,
    latest_price: float | None,
    signal: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "market": market,
        "latest_timestamp": latest_timestamp or "-",
        "latest_price": latest_price,
        "signal": signal,
        "reason": reason,
        "position_assumption": POSITION_ASSUMPTION,
    }


def print_signal_preview(rows: list[dict[str, Any]]) -> None:
    table = Table(title="Candidate Signal Preview")
    table.add_column("market")
    table.add_column("latest_timestamp")
    table.add_column("latest_price", justify="right")
    table.add_column("signal")
    table.add_column("reason")
    table.add_column("position_assumption")

    for row in rows:
        latest_price = row["latest_price"]
        table.add_row(
            row["market"],
            row["latest_timestamp"],
            "-" if latest_price is None else f"{float(latest_price):.8f}",
            row["signal"],
            row["reason"],
            row["position_assumption"],
        )

    Console(width=140).print(table)


if __name__ == "__main__":
    main()
