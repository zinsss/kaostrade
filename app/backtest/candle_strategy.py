from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

from rich.console import Console
from rich.table import Table

from app.collector.collect_once import CONFIG_PATH, DEFAULT_DB_PATH, load_config

START_CASH_KRW = 1_000_000.0
DEFAULT_DAYS = 30
DEFAULT_INTERVAL = "1m"
DEFAULT_TRADE_NOTIONAL_KRW = 10_000.0
DEFAULT_FEE_RATE = 0.0005
DEFAULT_MIN_SIGNAL_GAP_MINUTES = 0
STRATEGIES = ("ema", "bollinger")
EMA_FAST = 20
EMA_SLOW = 50
BOLLINGER_PERIOD = 20
BOLLINGER_STDDEV = 2.0


@dataclass
class Candle:
    market: str
    ts: str
    price: float


@dataclass
class Position:
    quantity: float
    average_entry_price: float
    entry_ts: str


def main() -> None:
    args = parse_args()
    config = load_config(CONFIG_PATH)
    db_path = config.get("database", {}).get("path", DEFAULT_DB_PATH)
    markets = resolve_markets(args, config)

    with connect_read_only(db_path) as conn:
        if args.compare:
            print_comparison(conn, args, markets)
            return
        summary = run_backtest(
            conn=conn,
            strategy=args.strategy,
            markets=markets,
            days=args.days,
            interval=args.interval,
            trade_notional_krw=args.trade_notional_krw,
            fee_rate=args.fee_rate,
            min_signal_gap_minutes=args.min_signal_gap_minutes,
        )

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest candle-based technical strategies.")
    parser.add_argument("--strategy", choices=STRATEGIES, default="ema")
    market_group = parser.add_mutually_exclusive_group()
    market_group.add_argument("--market", action="append", dest="markets")
    market_group.add_argument("--all-markets", action="store_true")
    parser.add_argument("--days", type=positive_int, default=DEFAULT_DAYS)
    parser.add_argument("--interval", default=DEFAULT_INTERVAL)
    parser.add_argument("--trade-notional-krw", type=positive_float, default=DEFAULT_TRADE_NOTIONAL_KRW)
    parser.add_argument("--fee-rate", type=non_negative_float, default=DEFAULT_FEE_RATE)
    parser.add_argument("--min-signal-gap-minutes", type=non_negative_int, default=DEFAULT_MIN_SIGNAL_GAP_MINUTES)
    parser.add_argument("--compare", action="store_true")
    return parser.parse_args()


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return parsed


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def non_negative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return parsed


def resolve_markets(args: argparse.Namespace, config: dict[str, Any]) -> list[str]:
    if args.all_markets:
        return configured_markets(config)
    if args.markets:
        return dedupe(args.markets)
    return ["KRW-BTC"]


def configured_markets(config: dict[str, Any]) -> list[str]:
    symbols = config.get("collector", {}).get("static_whitelist", [])
    if not symbols:
        raise ValueError("collector.static_whitelist is empty")
    return dedupe(symbols)


def dedupe(values: list[str]) -> list[str]:
    unique_values = []
    seen = set()
    for value in values:
        if value not in seen:
            unique_values.append(value)
            seen.add(value)
    return unique_values


def connect_read_only(db_path: str) -> sqlite3.Connection:
    if not Path(db_path).exists():
        raise FileNotFoundError(f"SQLite database not found: {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def print_comparison(conn: sqlite3.Connection, args: argparse.Namespace, markets: list[str]) -> None:
    table = Table(title="Candle Strategy Comparison")
    table.add_column("strategy")
    table.add_column("trade_count", justify="right")
    table.add_column("raw_signal_count", justify="right")
    table.add_column("accepted_signal_count", justify="right")
    table.add_column("return_pct", justify="right")
    table.add_column("total_fees_krw", justify="right")
    table.add_column("average_hold_minutes", justify="right")
    table.add_column("max_drawdown_pct", justify="right")

    for strategy in STRATEGIES:
        summary = run_backtest(
            conn=conn,
            strategy=strategy,
            markets=markets,
            days=args.days,
            interval=args.interval,
            trade_notional_krw=args.trade_notional_krw,
            fee_rate=args.fee_rate,
            min_signal_gap_minutes=args.min_signal_gap_minutes,
        )
        table.add_row(
            strategy,
            str(summary["trade_count"]),
            str(summary["raw_signal_count"]),
            str(summary["accepted_signal_count"]),
            format_float(summary["return_pct"]),
            format_float(summary["total_fees_krw"]),
            format_optional_float(summary["average_hold_minutes"]),
            format_float(summary["max_drawdown_pct"]),
        )

    Console(width=120).print(table)


def run_backtest(
    conn: sqlite3.Connection,
    strategy: str,
    markets: list[str],
    days: int,
    interval: str,
    trade_notional_krw: float,
    fee_rate: float,
    min_signal_gap_minutes: int,
) -> dict[str, Any]:
    cash = START_CASH_KRW
    positions: dict[str, Position] = {}
    buy_count = 0
    sell_count = 0
    total_fees_krw = 0.0
    realized_pnl_krw = 0.0
    trades: list[dict[str, Any]] = []
    hold_minutes: list[float] = []
    equity_curve: list[dict[str, Any]] = []
    latest_prices: dict[str, float] = {}

    signals_by_market = {
        market: strategy_signals(strategy, load_candles(conn, market, interval, days))
        for market in markets
    }
    raw_signal_count = sum(len(signals) for signals in signals_by_market.values())
    signals_by_market = {
        market: filter_signals_by_gap(signals, min_signal_gap_minutes)
        for market, signals in signals_by_market.items()
    }
    accepted_signal_count = sum(len(signals) for signals in signals_by_market.values())
    events = sorted(
        (event for signals in signals_by_market.values() for event in signals),
        key=lambda event: (event["ts"], event["market"]),
    )

    for event in events:
        market = event["market"]
        ts = event["ts"]
        price = float(event["price"])
        latest_prices[market] = price
        signal = event["signal"]

        if signal == "BUY" and market not in positions:
            total_cost = trade_notional_krw * (1 + fee_rate)
            if cash >= total_cost:
                quantity = trade_notional_krw / price
                fee_krw = trade_notional_krw * fee_rate
                cash -= total_cost
                positions[market] = Position(quantity=quantity, average_entry_price=price, entry_ts=ts)
                total_fees_krw += fee_krw
                buy_count += 1
                trades.append(simulated_trade(ts, "BUY", market, price, quantity, trade_notional_krw, fee_krw))
        elif signal == "SELL" and market in positions:
            position = positions.pop(market)
            notional = position.quantity * price
            fee_krw = notional * fee_rate
            cash += notional - fee_krw
            realized_pnl_krw += notional - fee_krw - (position.quantity * position.average_entry_price)
            total_fees_krw += fee_krw
            sell_count += 1
            hold_minutes.append(position_hold_minutes(position, ts))
            trades.append(simulated_trade(ts, "SELL", market, price, position.quantity, notional, fee_krw))

        equity_curve.append(
            {
                "ts": ts,
                "equity": estimate_equity(cash, positions, latest_prices),
                "cash": cash,
            }
        )

    final_prices = final_market_prices(conn, markets, interval, days)
    latest_prices.update(final_prices)
    final_equity = estimate_equity(cash, positions, latest_prices)

    return {
        "strategy": strategy,
        "markets": markets,
        "min_signal_gap_minutes": min_signal_gap_minutes,
        "raw_signal_count": raw_signal_count,
        "accepted_signal_count": accepted_signal_count,
        "skipped_signal_count": raw_signal_count - accepted_signal_count,
        "start_cash": START_CASH_KRW,
        "final_equity": final_equity,
        "return_pct": (final_equity - START_CASH_KRW) / START_CASH_KRW * 100,
        "trade_count": buy_count + sell_count,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "total_fees_krw": total_fees_krw,
        "realized_pnl_krw": realized_pnl_krw,
        "max_drawdown_pct": max_drawdown_pct(equity_curve),
        "average_hold_minutes": average_hold_minutes(hold_minutes),
        "trades": trades[-20:],
    }


def load_candles(conn: sqlite3.Connection, market: str, interval: str, days: int) -> list[Candle]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = conn.execute(
        """
        SELECT candle_date_time_utc, trade_price
        FROM candles
        WHERE market = ?
          AND interval = ?
          AND candle_date_time_utc >= ?
          AND trade_price IS NOT NULL
        ORDER BY candle_date_time_utc ASC, id ASC
        """,
        (market, interval, format_utc(cutoff)),
    ).fetchall()

    candles = []
    seen_timestamps = set()
    for row in rows:
        ts = row["candle_date_time_utc"]
        if ts in seen_timestamps:
            continue
        seen_timestamps.add(ts)
        candles.append(Candle(market=market, ts=ts, price=float(row["trade_price"])))
    return candles


def strategy_signals(strategy: str, candles: list[Candle]) -> list[dict[str, Any]]:
    if strategy == "ema":
        return ema_signals(candles)
    if strategy == "bollinger":
        return bollinger_signals(candles)
    raise ValueError(f"Unsupported strategy: {strategy}")


def ema_signals(candles: list[Candle]) -> list[dict[str, Any]]:
    if not candles:
        return []

    prices = [candle.price for candle in candles]
    ema_fast = ema_series(prices, EMA_FAST)
    ema_slow = ema_series(prices, EMA_SLOW)
    signals = []
    for index in range(1, len(candles)):
        previous_fast = ema_fast[index - 1]
        previous_slow = ema_slow[index - 1]
        current_fast = ema_fast[index]
        current_slow = ema_slow[index]
        if None in (previous_fast, previous_slow, current_fast, current_slow):
            continue
        if previous_fast <= previous_slow and current_fast > current_slow:
            signals.append(signal_event(candles[index], "BUY"))
        elif previous_fast >= previous_slow and current_fast < current_slow:
            signals.append(signal_event(candles[index], "SELL"))
    return signals


def ema_series(values: list[float], period: int) -> list[float | None]:
    series: list[float | None] = [None] * len(values)
    if len(values) < period:
        return series
    multiplier = 2 / (period + 1)
    current = mean(values[:period])
    series[period - 1] = current
    for index in range(period, len(values)):
        current = (values[index] - current) * multiplier + current
        series[index] = current
    return series


def bollinger_signals(candles: list[Candle]) -> list[dict[str, Any]]:
    prices = [candle.price for candle in candles]
    signals = []
    for index in range(BOLLINGER_PERIOD, len(candles)):
        previous_window = prices[index - BOLLINGER_PERIOD:index]
        current_window = prices[index - BOLLINGER_PERIOD + 1:index + 1]
        previous_middle = mean(previous_window)
        previous_lower = previous_middle - BOLLINGER_STDDEV * pstdev(previous_window)
        current_middle = mean(current_window)
        current_lower = current_middle - BOLLINGER_STDDEV * pstdev(current_window)
        previous_price = prices[index - 1]
        current_price = prices[index]

        if previous_price <= previous_lower and current_price > current_lower:
            signals.append(signal_event(candles[index], "BUY"))
        elif previous_price >= previous_middle and current_price < current_middle:
            signals.append(signal_event(candles[index], "SELL"))
    return signals


def signal_event(candle: Candle, signal: str) -> dict[str, Any]:
    return {"market": candle.market, "ts": candle.ts, "price": candle.price, "signal": signal}


def filter_signals_by_gap(signals: list[dict[str, Any]], min_signal_gap_minutes: int) -> list[dict[str, Any]]:
    if min_signal_gap_minutes <= 0:
        return signals

    accepted = []
    previous_signal_ts: datetime | None = None
    min_gap_seconds = min_signal_gap_minutes * 60
    for signal in signals:
        signal_ts = parse_utc_datetime(signal["ts"])
        if previous_signal_ts is not None:
            elapsed_seconds = (signal_ts - previous_signal_ts).total_seconds()
            if elapsed_seconds < min_gap_seconds:
                continue
        accepted.append(signal)
        previous_signal_ts = signal_ts
    return accepted


def final_market_prices(conn: sqlite3.Connection, markets: list[str], interval: str, days: int) -> dict[str, float]:
    prices = {}
    for market in markets:
        candles = load_candles(conn, market, interval, days)
        if candles:
            prices[market] = candles[-1].price
    return prices


def simulated_trade(
    ts: str,
    side: str,
    market: str,
    price: float,
    quantity: float,
    notional_krw: float,
    fee_krw: float,
) -> dict[str, Any]:
    return {
        "ts": ts,
        "side": side,
        "market": market,
        "price": price,
        "quantity": quantity,
        "notional_krw": notional_krw,
        "fee_krw": fee_krw,
    }


def estimate_equity(cash: float, positions: dict[str, Position], prices: dict[str, float]) -> float:
    equity = cash
    for market, position in positions.items():
        price = prices.get(market)
        if price is not None:
            equity += position.quantity * price
    return equity


def position_hold_minutes(position: Position, exit_ts: str) -> float:
    return (parse_utc_datetime(exit_ts) - parse_utc_datetime(position.entry_ts)).total_seconds() / 60


def average_hold_minutes(hold_minutes: list[float]) -> float | None:
    if not hold_minutes:
        return None
    return sum(hold_minutes) / len(hold_minutes)


def max_drawdown_pct(equity_curve: list[dict[str, Any]]) -> float:
    peak = START_CASH_KRW
    max_drawdown = 0.0
    for point in equity_curve:
        equity = float(point["equity"])
        if equity > peak:
            peak = equity
        if peak <= 0:
            continue
        drawdown = (peak - equity) / peak * 100
        if drawdown > max_drawdown:
            max_drawdown = drawdown
    return max_drawdown


def parse_utc_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def format_float(value: float) -> str:
    return f"{value:,.6f}"


def format_optional_float(value: float | None) -> str:
    if value is None:
        return "-"
    return format_float(value)


if __name__ == "__main__":
    main()
