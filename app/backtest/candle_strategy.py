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
DEFAULT_TAKE_PROFIT_PCT = 0.0
DEFAULT_STOP_LOSS_PCT = 0.0
RISK_SWEEP_TAKE_PROFIT_PCTS = (0.0, 0.5, 1.0, 1.5)
RISK_SWEEP_STOP_LOSS_PCTS = (0.0, 0.5, 1.0)
STRATEGIES = (
    "ema",
    "bollinger",
    "rsi",
    "ema_rsi",
    "donchian",
    "bollinger_rsi_and",
    "bollinger_rsi_or",
)
EMA_FAST = 20
EMA_SLOW = 50
DEFAULT_BOLLINGER_PERIOD = 20
DEFAULT_BOLLINGER_STDDEV = 2.0
BOLLINGER_SWEEP_PERIODS = (10, 20, 30)
BOLLINGER_SWEEP_STDDEVS = (1.5, 2.0, 2.5, 3.0)
RSI_PERIOD = 14
DEFAULT_RSI_BUY_THRESHOLD = 30.0
DEFAULT_RSI_SELL_THRESHOLD = 60.0
RSI_SWEEP_BUY_THRESHOLDS = (20.0, 25.0, 30.0, 35.0)
RSI_SWEEP_SELL_THRESHOLDS = (55.0, 60.0, 65.0, 70.0)
EMA_RSI_BUY_THRESHOLD = 55.0
EMA_RSI_SELL_THRESHOLD = 45.0
DONCHIAN_ENTRY_CHANNEL = 20
DONCHIAN_EXIT_CHANNEL = 10


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
        if args.compare_bollinger:
            print_bollinger_comparison(conn, args, markets)
            return
        if args.compare_risk:
            print_risk_comparison(conn, args, markets)
            return
        if args.compare_all_strategies:
            print_all_strategies_comparison(conn, args, markets)
            return
        if args.compare_rsi:
            print_rsi_comparison(conn, args, markets)
            return
        if args.breakdown_by_market:
            print_market_breakdown(conn, args, markets)
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
            bollinger_period=args.bollinger_period,
            bollinger_stddev=args.bollinger_stddev,
            take_profit_pct=args.take_profit_pct,
            stop_loss_pct=args.stop_loss_pct,
            rsi_buy_threshold=args.rsi_buy_threshold,
            rsi_sell_threshold=args.rsi_sell_threshold,
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
    parser.add_argument("--bollinger-period", type=positive_int, default=DEFAULT_BOLLINGER_PERIOD)
    parser.add_argument("--bollinger-stddev", type=positive_float, default=DEFAULT_BOLLINGER_STDDEV)
    parser.add_argument("--take-profit-pct", type=non_negative_float, default=DEFAULT_TAKE_PROFIT_PCT)
    parser.add_argument("--stop-loss-pct", type=non_negative_float, default=DEFAULT_STOP_LOSS_PCT)
    parser.add_argument("--rsi-buy-threshold", type=non_negative_float, default=DEFAULT_RSI_BUY_THRESHOLD)
    parser.add_argument("--rsi-sell-threshold", type=non_negative_float, default=DEFAULT_RSI_SELL_THRESHOLD)
    parser.add_argument("--compare", action="store_true")
    parser.add_argument("--compare-bollinger", action="store_true")
    parser.add_argument("--compare-risk", action="store_true")
    parser.add_argument("--compare-all-strategies", action="store_true")
    parser.add_argument("--compare-rsi", action="store_true")
    parser.add_argument("--breakdown-by-market", action="store_true")
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
            bollinger_period=args.bollinger_period,
            bollinger_stddev=args.bollinger_stddev,
            take_profit_pct=args.take_profit_pct,
            stop_loss_pct=args.stop_loss_pct,
            rsi_buy_threshold=args.rsi_buy_threshold,
            rsi_sell_threshold=args.rsi_sell_threshold,
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


def print_bollinger_comparison(conn: sqlite3.Connection, args: argparse.Namespace, markets: list[str]) -> None:
    summaries = []
    for period in BOLLINGER_SWEEP_PERIODS:
        for stddev in BOLLINGER_SWEEP_STDDEVS:
            summary = run_backtest(
                conn=conn,
                strategy="bollinger",
                markets=markets,
                days=args.days,
                interval=args.interval,
                trade_notional_krw=args.trade_notional_krw,
                fee_rate=args.fee_rate,
                min_signal_gap_minutes=args.min_signal_gap_minutes,
                bollinger_period=period,
                bollinger_stddev=stddev,
                take_profit_pct=args.take_profit_pct,
                stop_loss_pct=args.stop_loss_pct,
                rsi_buy_threshold=args.rsi_buy_threshold,
                rsi_sell_threshold=args.rsi_sell_threshold,
            )
            summaries.append(summary)

    summaries.sort(key=lambda summary: summary["return_pct"], reverse=True)

    table = Table(title="Bollinger Parameter Sweep")
    table.add_column("period", justify="right")
    table.add_column("stddev", justify="right")
    table.add_column("trade_count", justify="right")
    table.add_column("return_pct", justify="right")
    table.add_column("total_fees_krw", justify="right")
    table.add_column("average_hold_minutes", justify="right")
    table.add_column("max_drawdown_pct", justify="right")

    for summary in summaries:
        table.add_row(
            str(summary["bollinger_period"]),
            format_float(summary["bollinger_stddev"]),
            str(summary["trade_count"]),
            format_float(summary["return_pct"]),
            format_float(summary["total_fees_krw"]),
            format_optional_float(summary["average_hold_minutes"]),
            format_float(summary["max_drawdown_pct"]),
        )

    Console(width=120).print(table)


def print_risk_comparison(conn: sqlite3.Connection, args: argparse.Namespace, markets: list[str]) -> None:
    summaries = []
    for take_profit_pct in RISK_SWEEP_TAKE_PROFIT_PCTS:
        for stop_loss_pct in RISK_SWEEP_STOP_LOSS_PCTS:
            summary = run_backtest(
                conn=conn,
                strategy="bollinger",
                markets=markets,
                days=args.days,
                interval=args.interval,
                trade_notional_krw=args.trade_notional_krw,
                fee_rate=args.fee_rate,
                min_signal_gap_minutes=args.min_signal_gap_minutes,
                bollinger_period=20,
                bollinger_stddev=3.0,
                take_profit_pct=take_profit_pct,
                stop_loss_pct=stop_loss_pct,
                rsi_buy_threshold=DEFAULT_RSI_BUY_THRESHOLD,
                rsi_sell_threshold=DEFAULT_RSI_SELL_THRESHOLD,
            )
            summaries.append(summary)

    summaries.sort(key=lambda summary: summary["return_pct"], reverse=True)

    table = Table(title="Candle Risk Control Sweep")
    table.add_column("take_profit_pct", justify="right")
    table.add_column("stop_loss_pct", justify="right")
    table.add_column("trade_count", justify="right")
    table.add_column("return_pct", justify="right")
    table.add_column("total_fees_krw", justify="right")
    table.add_column("average_hold_minutes", justify="right")
    table.add_column("max_drawdown_pct", justify="right")
    table.add_column("take_profit_count", justify="right")
    table.add_column("stop_loss_count", justify="right")

    for summary in summaries:
        table.add_row(
            format_float(summary["take_profit_pct"]),
            format_float(summary["stop_loss_pct"]),
            str(summary["trade_count"]),
            format_float(summary["return_pct"]),
            format_float(summary["total_fees_krw"]),
            format_optional_float(summary["average_hold_minutes"]),
            format_float(summary["max_drawdown_pct"]),
            str(summary["take_profit_count"]),
            str(summary["stop_loss_count"]),
        )

    Console(width=140).print(table)


def print_all_strategies_comparison(conn: sqlite3.Connection, args: argparse.Namespace, markets: list[str]) -> None:
    summaries = []
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
            bollinger_period=args.bollinger_period,
            bollinger_stddev=args.bollinger_stddev,
            take_profit_pct=args.take_profit_pct,
            stop_loss_pct=args.stop_loss_pct,
            rsi_buy_threshold=args.rsi_buy_threshold,
            rsi_sell_threshold=args.rsi_sell_threshold,
        )
        summaries.append(summary)

    summaries.sort(key=lambda summary: summary["return_pct"], reverse=True)

    table = Table(title="Strategy Family Comparison")
    table.add_column("strategy")
    table.add_column("trade_count", justify="right")
    table.add_column("return_pct", justify="right")
    table.add_column("total_fees_krw", justify="right")
    table.add_column("average_hold_minutes", justify="right")
    table.add_column("max_drawdown_pct", justify="right")

    for summary in summaries:
        table.add_row(
            summary["strategy"],
            str(summary["trade_count"]),
            format_float(summary["return_pct"]),
            format_float(summary["total_fees_krw"]),
            format_optional_float(summary["average_hold_minutes"]),
            format_float(summary["max_drawdown_pct"]),
        )

    Console(width=120).print(table)


def print_rsi_comparison(conn: sqlite3.Connection, args: argparse.Namespace, markets: list[str]) -> None:
    summaries = []
    for buy_threshold in RSI_SWEEP_BUY_THRESHOLDS:
        for sell_threshold in RSI_SWEEP_SELL_THRESHOLDS:
            if buy_threshold >= sell_threshold:
                continue
            summary = run_backtest(
                conn=conn,
                strategy="rsi",
                markets=markets,
                days=args.days,
                interval=args.interval,
                trade_notional_krw=args.trade_notional_krw,
                fee_rate=args.fee_rate,
                min_signal_gap_minutes=args.min_signal_gap_minutes,
                bollinger_period=args.bollinger_period,
                bollinger_stddev=args.bollinger_stddev,
                take_profit_pct=args.take_profit_pct,
                stop_loss_pct=args.stop_loss_pct,
                rsi_buy_threshold=buy_threshold,
                rsi_sell_threshold=sell_threshold,
            )
            summaries.append(summary)

    summaries.sort(key=lambda summary: summary["return_pct"], reverse=True)

    table = Table(title="RSI Parameter Sweep")
    table.add_column("buy_threshold", justify="right")
    table.add_column("sell_threshold", justify="right")
    table.add_column("trade_count", justify="right")
    table.add_column("return_pct", justify="right")
    table.add_column("total_fees_krw", justify="right")
    table.add_column("average_hold_minutes", justify="right")
    table.add_column("max_drawdown_pct", justify="right")

    for summary in summaries:
        table.add_row(
            format_float(summary["rsi_buy_threshold"]),
            format_float(summary["rsi_sell_threshold"]),
            str(summary["trade_count"]),
            format_float(summary["return_pct"]),
            format_float(summary["total_fees_krw"]),
            format_optional_float(summary["average_hold_minutes"]),
            format_float(summary["max_drawdown_pct"]),
        )

    Console(width=120).print(table)


def print_market_breakdown(conn: sqlite3.Connection, args: argparse.Namespace, markets: list[str]) -> None:
    summaries = []
    for market in markets:
        summary = run_backtest(
            conn=conn,
            strategy=args.strategy,
            markets=[market],
            days=args.days,
            interval=args.interval,
            trade_notional_krw=args.trade_notional_krw,
            fee_rate=args.fee_rate,
            min_signal_gap_minutes=args.min_signal_gap_minutes,
            bollinger_period=args.bollinger_period,
            bollinger_stddev=args.bollinger_stddev,
            take_profit_pct=args.take_profit_pct,
            stop_loss_pct=args.stop_loss_pct,
            rsi_buy_threshold=args.rsi_buy_threshold,
            rsi_sell_threshold=args.rsi_sell_threshold,
        )
        summaries.append(summary)

    summaries.sort(key=lambda summary: summary["return_pct"], reverse=True)

    table = Table(title="Per-Market Backtest Breakdown")
    table.add_column("market")
    table.add_column("trade_count", justify="right")
    table.add_column("return_pct", justify="right")
    table.add_column("total_fees_krw", justify="right")
    table.add_column("average_hold_minutes", justify="right")
    table.add_column("max_drawdown_pct", justify="right")
    table.add_column("take_profit_count", justify="right")
    table.add_column("stop_loss_count", justify="right")

    for summary in summaries:
        market = summary["markets"][0] if summary["markets"] else "-"
        table.add_row(
            market,
            str(summary["trade_count"]),
            format_float(summary["return_pct"]),
            format_float(summary["total_fees_krw"]),
            format_optional_float(summary["average_hold_minutes"]),
            format_float(summary["max_drawdown_pct"]),
            str(summary["take_profit_count"]),
            str(summary["stop_loss_count"]),
        )

    Console(width=140).print(table)


def run_backtest(
    conn: sqlite3.Connection,
    strategy: str,
    markets: list[str],
    days: int,
    interval: str,
    trade_notional_krw: float,
    fee_rate: float,
    min_signal_gap_minutes: int,
    bollinger_period: int,
    bollinger_stddev: float,
    take_profit_pct: float,
    stop_loss_pct: float,
    rsi_buy_threshold: float,
    rsi_sell_threshold: float,
) -> dict[str, Any]:
    cash = START_CASH_KRW
    positions: dict[str, Position] = {}
    buy_count = 0
    sell_count = 0
    take_profit_count = 0
    stop_loss_count = 0
    signal_exit_count = 0
    total_fees_krw = 0.0
    realized_pnl_krw = 0.0
    trades: list[dict[str, Any]] = []
    hold_minutes: list[float] = []
    equity_curve: list[dict[str, Any]] = []
    latest_prices: dict[str, float] = {}

    candles_by_market = {
        market: load_candles(conn, market, interval, days)
        for market in markets
    }
    signals_by_market = {
        market: strategy_signals(
            strategy,
            candles,
            bollinger_period=bollinger_period,
            bollinger_stddev=bollinger_stddev,
            rsi_buy_threshold=rsi_buy_threshold,
            rsi_sell_threshold=rsi_sell_threshold,
        )
        for market, candles in candles_by_market.items()
    }
    raw_signal_count = sum(len(signals) for signals in signals_by_market.values())
    signals_by_market = {
        market: filter_signals_by_gap(signals, min_signal_gap_minutes)
        for market, signals in signals_by_market.items()
    }
    accepted_signal_count = sum(len(signals) for signals in signals_by_market.values())
    risk_controls_enabled = take_profit_pct > 0 or stop_loss_pct > 0
    events = build_price_events(candles_by_market, signals_by_market) if risk_controls_enabled else sorted(
        (event for signals in signals_by_market.values() for event in signals),
        key=lambda event: (event["ts"], event["market"]),
    )

    for event in events:
        market = event["market"]
        ts = event["ts"]
        price = float(event["price"])
        latest_prices[market] = price
        signal = event.get("signal")

        risk_exit_reason = risk_exit_for_position(
            positions.get(market),
            price,
            take_profit_pct=take_profit_pct,
            stop_loss_pct=stop_loss_pct,
        )
        if risk_exit_reason is not None:
            position = positions.pop(market)
            cash, realized_delta, fee_krw, trade = close_position(
                cash, position, ts, market, price, fee_rate, risk_exit_reason
            )
            realized_pnl_krw += realized_delta
            total_fees_krw += fee_krw
            sell_count += 1
            if risk_exit_reason == "TAKE_PROFIT":
                take_profit_count += 1
            elif risk_exit_reason == "STOP_LOSS":
                stop_loss_count += 1
            hold_minutes.append(position_hold_minutes(position, ts))
            trades.append(trade)
        elif signal == "BUY" and market not in positions:
            total_cost = trade_notional_krw * (1 + fee_rate)
            if cash >= total_cost:
                quantity = trade_notional_krw / price
                fee_krw = trade_notional_krw * fee_rate
                cash -= total_cost
                positions[market] = Position(quantity=quantity, average_entry_price=price, entry_ts=ts)
                total_fees_krw += fee_krw
                buy_count += 1
                trades.append(simulated_trade(ts, "BUY", market, price, quantity, trade_notional_krw, fee_krw, "SIGNAL"))
        elif signal == "SELL" and market in positions:
            position = positions.pop(market)
            cash, realized_delta, fee_krw, trade = close_position(
                cash, position, ts, market, price, fee_rate, "SIGNAL"
            )
            realized_pnl_krw += realized_delta
            total_fees_krw += fee_krw
            sell_count += 1
            signal_exit_count += 1
            hold_minutes.append(position_hold_minutes(position, ts))
            trades.append(trade)

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
        "bollinger_period": bollinger_period if strategy.startswith("bollinger") else None,
        "bollinger_stddev": bollinger_stddev if strategy.startswith("bollinger") else None,
        "take_profit_pct": take_profit_pct,
        "stop_loss_pct": stop_loss_pct,
        "rsi_buy_threshold": rsi_buy_threshold,
        "rsi_sell_threshold": rsi_sell_threshold,
        "raw_signal_count": raw_signal_count,
        "accepted_signal_count": accepted_signal_count,
        "skipped_signal_count": raw_signal_count - accepted_signal_count,
        "start_cash": START_CASH_KRW,
        "final_equity": final_equity,
        "return_pct": (final_equity - START_CASH_KRW) / START_CASH_KRW * 100,
        "trade_count": buy_count + sell_count,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "take_profit_count": take_profit_count,
        "stop_loss_count": stop_loss_count,
        "signal_exit_count": signal_exit_count,
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


def strategy_signals(
    strategy: str,
    candles: list[Candle],
    bollinger_period: int,
    bollinger_stddev: float,
    rsi_buy_threshold: float,
    rsi_sell_threshold: float,
) -> list[dict[str, Any]]:
    if strategy == "ema":
        return ema_signals(candles)
    if strategy == "bollinger":
        return bollinger_signals(candles, period=bollinger_period, stddev=bollinger_stddev)
    if strategy == "rsi":
        return rsi_signals(candles, buy_threshold=rsi_buy_threshold, sell_threshold=rsi_sell_threshold)
    if strategy == "ema_rsi":
        return ema_rsi_signals(candles)
    if strategy == "donchian":
        return donchian_signals(candles)
    if strategy == "bollinger_rsi_and":
        return bollinger_rsi_signals(
            candles,
            period=bollinger_period,
            stddev=bollinger_stddev,
            rsi_buy_threshold=rsi_buy_threshold,
            rsi_sell_threshold=rsi_sell_threshold,
            buy_mode="and",
        )
    if strategy == "bollinger_rsi_or":
        return bollinger_rsi_signals(
            candles,
            period=bollinger_period,
            stddev=bollinger_stddev,
            rsi_buy_threshold=rsi_buy_threshold,
            rsi_sell_threshold=rsi_sell_threshold,
            buy_mode="or",
        )
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


def rsi_signals(candles: list[Candle], buy_threshold: float, sell_threshold: float) -> list[dict[str, Any]]:
    prices = [candle.price for candle in candles]
    rsi = rsi_series(prices, RSI_PERIOD)
    signals = []
    for index, value in enumerate(rsi):
        if value is None:
            continue
        if value < buy_threshold:
            signals.append(signal_event(candles[index], "BUY"))
        elif value > sell_threshold:
            signals.append(signal_event(candles[index], "SELL"))
    return signals


def bollinger_rsi_signals(
    candles: list[Candle],
    period: int,
    stddev: float,
    rsi_buy_threshold: float,
    rsi_sell_threshold: float,
    buy_mode: str,
) -> list[dict[str, Any]]:
    prices = [candle.price for candle in candles]
    rsi = rsi_series(prices, RSI_PERIOD)
    signals = []
    for index in range(period, len(candles)):
        previous_window = prices[index - period:index]
        current_window = prices[index - period + 1:index + 1]
        previous_middle = mean(previous_window)
        previous_lower = previous_middle - stddev * pstdev(previous_window)
        current_middle = mean(current_window)
        current_lower = current_middle - stddev * pstdev(current_window)
        previous_price = prices[index - 1]
        current_price = prices[index]
        current_rsi = rsi[index]
        if current_rsi is None:
            continue

        bollinger_buy = previous_price <= previous_lower and current_price > current_lower
        bollinger_sell = previous_price >= previous_middle and current_price < current_middle
        rsi_buy = current_rsi < rsi_buy_threshold
        rsi_sell = current_rsi > rsi_sell_threshold
        buy_signal = (
            (buy_mode == "and" and bollinger_buy and rsi_buy)
            or (buy_mode == "or" and (bollinger_buy or rsi_buy))
        )
        if buy_signal:
            signals.append(signal_event(candles[index], "BUY"))
        elif bollinger_sell or rsi_sell:
            signals.append(signal_event(candles[index], "SELL"))
    return signals


def ema_rsi_signals(candles: list[Candle]) -> list[dict[str, Any]]:
    prices = [candle.price for candle in candles]
    ema_fast = ema_series(prices, EMA_FAST)
    ema_slow = ema_series(prices, EMA_SLOW)
    rsi = rsi_series(prices, RSI_PERIOD)
    signals = []
    for index in range(len(candles)):
        current_fast = ema_fast[index]
        current_slow = ema_slow[index]
        current_rsi = rsi[index]
        if None in (current_fast, current_slow, current_rsi):
            continue
        if current_fast > current_slow and current_rsi > EMA_RSI_BUY_THRESHOLD:
            signals.append(signal_event(candles[index], "BUY"))
        elif current_fast < current_slow or current_rsi < EMA_RSI_SELL_THRESHOLD:
            signals.append(signal_event(candles[index], "SELL"))
    return signals


def donchian_signals(candles: list[Candle]) -> list[dict[str, Any]]:
    prices = [candle.price for candle in candles]
    signals = []
    start_index = max(DONCHIAN_ENTRY_CHANNEL, DONCHIAN_EXIT_CHANNEL)
    for index in range(start_index, len(candles)):
        entry_high = max(prices[index - DONCHIAN_ENTRY_CHANNEL:index])
        exit_low = min(prices[index - DONCHIAN_EXIT_CHANNEL:index])
        price = prices[index]
        if price > entry_high:
            signals.append(signal_event(candles[index], "BUY"))
        elif price < exit_low:
            signals.append(signal_event(candles[index], "SELL"))
    return signals


def rsi_series(values: list[float], period: int) -> list[float | None]:
    series: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return series

    gains = []
    losses = []
    for index in range(1, period + 1):
        change = values[index] - values[index - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    average_gain = sum(gains) / period
    average_loss = sum(losses) / period
    series[period] = rsi_from_averages(average_gain, average_loss)

    for index in range(period + 1, len(values)):
        change = values[index] - values[index - 1]
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        average_gain = ((average_gain * (period - 1)) + gain) / period
        average_loss = ((average_loss * (period - 1)) + loss) / period
        series[index] = rsi_from_averages(average_gain, average_loss)

    return series


def rsi_from_averages(average_gain: float, average_loss: float) -> float:
    if average_loss == 0:
        return 100.0
    relative_strength = average_gain / average_loss
    return 100 - (100 / (1 + relative_strength))


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


def bollinger_signals(candles: list[Candle], period: int, stddev: float) -> list[dict[str, Any]]:
    prices = [candle.price for candle in candles]
    signals = []
    for index in range(period, len(candles)):
        previous_window = prices[index - period:index]
        current_window = prices[index - period + 1:index + 1]
        previous_middle = mean(previous_window)
        previous_lower = previous_middle - stddev * pstdev(previous_window)
        current_middle = mean(current_window)
        current_lower = current_middle - stddev * pstdev(current_window)
        previous_price = prices[index - 1]
        current_price = prices[index]

        if previous_price <= previous_lower and current_price > current_lower:
            signals.append(signal_event(candles[index], "BUY"))
        elif previous_price >= previous_middle and current_price < current_middle:
            signals.append(signal_event(candles[index], "SELL"))
    return signals


def signal_event(candle: Candle, signal: str) -> dict[str, Any]:
    return {"market": candle.market, "ts": candle.ts, "price": candle.price, "signal": signal}


def build_price_events(
    candles_by_market: dict[str, list[Candle]],
    signals_by_market: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    signal_lookup = {
        (signal["market"], signal["ts"]): signal["signal"]
        for signals in signals_by_market.values()
        for signal in signals
    }
    events = []
    for market, candles in candles_by_market.items():
        for candle in candles:
            events.append(
                {
                    "market": market,
                    "ts": candle.ts,
                    "price": candle.price,
                    "signal": signal_lookup.get((market, candle.ts)),
                }
            )
    return sorted(events, key=lambda event: (event["ts"], event["market"]))


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


def risk_exit_for_position(
    position: Position | None,
    price: float,
    take_profit_pct: float,
    stop_loss_pct: float,
) -> str | None:
    if position is None:
        return None
    if take_profit_pct > 0 and price >= position.average_entry_price * (1 + take_profit_pct / 100):
        return "TAKE_PROFIT"
    if stop_loss_pct > 0 and price <= position.average_entry_price * (1 - stop_loss_pct / 100):
        return "STOP_LOSS"
    return None


def close_position(
    cash: float,
    position: Position,
    ts: str,
    market: str,
    price: float,
    fee_rate: float,
    reason: str,
) -> tuple[float, float, float, dict[str, Any]]:
    notional = position.quantity * price
    fee_krw = notional * fee_rate
    new_cash = cash + notional - fee_krw
    realized_delta = notional - fee_krw - (position.quantity * position.average_entry_price)
    trade = simulated_trade(ts, "SELL", market, price, position.quantity, notional, fee_krw, reason)
    return new_cash, realized_delta, fee_krw, trade


def simulated_trade(
    ts: str,
    side: str,
    market: str,
    price: float,
    quantity: float,
    notional_krw: float,
    fee_krw: float,
    reason: str,
) -> dict[str, Any]:
    return {
        "ts": ts,
        "side": side,
        "market": market,
        "price": price,
        "quantity": quantity,
        "notional_krw": notional_krw,
        "fee_krw": fee_krw,
        "reason": reason,
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
