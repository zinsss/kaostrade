from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import combinations
from pathlib import Path
from statistics import mean, median, pstdev
from typing import Any

from rich.console import Console
from rich.table import Table

from app.collector.collect_once import CONFIG_PATH, DEFAULT_DB_PATH, load_config
from app.backtest.strategy_profiles import get_strategy_profile, profile_names

START_CASH_KRW = 1_000_000.0
DEFAULT_DAYS = 30
DEFAULT_INTERVAL = "1m"
DEFAULT_TRADE_NOTIONAL_KRW = 10_000.0
DEFAULT_FEE_RATE = 0.0005
DEFAULT_MIN_SIGNAL_GAP_MINUTES = 0
DEFAULT_TAKE_PROFIT_PCT = 0.0
DEFAULT_STOP_LOSS_PCT = 0.0
DEFAULT_WALK_FORWARD_WINDOW_DAYS = 10
RISK_SWEEP_TAKE_PROFIT_PCTS = (0.0, 0.5, 1.0, 1.5)
RISK_SWEEP_STOP_LOSS_PCTS = (0.0, 0.5, 1.0)
FEE_SWEEP_RATES = (0.0, 0.0002, 0.0004, 0.0006, 0.0008, 0.0010)
HOLD_TP_MAX_HOLD_HOURS = (6, 12, 24, 48, 72)
HOLD_TP_TAKE_PROFIT_PCTS = (0.5, 1.0, 2.0, 3.0, 5.0)
HOLD_TP_MARKETS = ("KRW-BTC", "KRW-SOL", "KRW-DOGE")
HOLD_TP_STRATEGY = "bollinger_rsi_and_mtf"
HOLD_TP_BASELINE_LABEL = "candidate_v1_baseline"
DYNAMIC_UNIVERSE_MAX_HOLD_HOURS = (None, 6, 12, 24, 48, 72)
DYNAMIC_UNIVERSE_TAKE_PROFIT_PCTS = (0.5, 1.0, 2.0, 3.0)
DYNAMIC_UNIVERSE_LOOKBACK_DAYS = 7
DYNAMIC_UNIVERSE_MARKET_COUNT = 3
FIXED_UNIVERSE_MARKETS = ("KRW-BTC", "KRW-SOL", "KRW-DOGE", "KRW-ETH", "KRW-XRP")
FIXED_UNIVERSE_MIN_SIZE = 1
FIXED_UNIVERSE_MAX_SIZE = 3
STRATEGIES = (
    "ema",
    "ema_volume_spike_2x",
    "ema_volume_spike_3x",
    "bollinger",
    "rsi",
    "ema_rsi",
    "donchian",
    "ichimoku",
    "ichimoku_strict_15m",
    "macd_ema_filter_15m",
    "donchian_5m",
    "donchian_15m",
    "ema_trend_5m",
    "ema_trend_15m",
    "bollinger_rsi_and",
    "bollinger_rsi_or",
    "bollinger_rsi_and_mtf",
)
EMA_FAST = 20
EMA_SLOW = 50
DEFAULT_BOLLINGER_PERIOD = 20
DEFAULT_BOLLINGER_STDDEV = 2.0
BOLLINGER_SWEEP_PERIODS = (10, 20, 30)
BOLLINGER_SWEEP_STDDEVS = (1.5, 2.0, 2.5, 3.0)
BOLLINGER_RSI_SWEEP_PERIODS = (10, 20, 30)
BOLLINGER_RSI_SWEEP_STDDEVS = (2.0, 2.5, 3.0)
BOLLINGER_RSI_SWEEP_BUY_THRESHOLDS = (20.0, 25.0, 30.0)
BOLLINGER_RSI_SWEEP_SELL_THRESHOLDS = (55.0, 60.0, 65.0)
RSI_PERIOD = 14
DEFAULT_RSI_BUY_THRESHOLD = 30.0
DEFAULT_RSI_SELL_THRESHOLD = 60.0
RSI_SWEEP_BUY_THRESHOLDS = (20.0, 25.0, 30.0, 35.0)
RSI_SWEEP_SELL_THRESHOLDS = (55.0, 60.0, 65.0, 70.0)
EMA_RSI_BUY_THRESHOLD = 55.0
EMA_RSI_SELL_THRESHOLD = 45.0
DONCHIAN_ENTRY_CHANNEL = 20
DONCHIAN_EXIT_CHANNEL = 10
ICHIMOKU_TENKAN = 9
ICHIMOKU_KIJUN = 26
ICHIMOKU_SENKOU_B = 52
ICHIMOKU_MARKETS = ("KRW-BTC", "KRW-SOL", "KRW-DOGE")
ICHIMOKU_DAYS = 365
ICHIMOKU_WALK_FORWARD_WINDOW_DAYS = 30
ICHIMOKU_STRICT_TAKE_PROFIT_PCTS = (1.0, 2.0, 3.0)
ICHIMOKU_STRICT_STOP_LOSS_PCTS = (0.8, 1.2, 1.5)
ICHIMOKU_STRICT_MAX_HOLD_HOURS = (12, 24, 48)
ICHIMOKU_STRICT_CLOUD_THICKNESS_PCT = 0.2
ICHIMOKU_STRICT_MAX_DISTANCE_ABOVE_CLOUD_PCT = 2.0
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
MACD_TREND_EMA_FAST = 20
MACD_TREND_EMA_SLOW = 100
MACD_TREND_MARKETS = ("KRW-BTC", "KRW-SOL", "KRW-DOGE")
MACD_TREND_DAYS = 365
MACD_TREND_WALK_FORWARD_WINDOW_DAYS = 30
MACD_TREND_TAKE_PROFIT_PCTS = (1.0, 2.0, 3.0)
MACD_TREND_STOP_LOSS_PCTS = (0.8, 1.2, 1.5)
MACD_TREND_MAX_HOLD_HOURS = (24, 48, 72)
BOLLINGER_SQUEEZE_VOLUME_MARKETS = ("KRW-BTC", "KRW-SOL", "KRW-DOGE")
BOLLINGER_SQUEEZE_VOLUME_DAYS = 365
BOLLINGER_SQUEEZE_VOLUME_WALK_FORWARD_WINDOW_DAYS = 30
BOLLINGER_SQUEEZE_VOLUME_TIMEFRAMES = (5, 15)
BOLLINGER_SQUEEZE_VOLUME_MULTIPLIERS = (2.0, 2.5, 3.0)
BOLLINGER_SQUEEZE_VOLUME_MAX_RECENT_PUMP_PCTS = (3.0, 5.0, 8.0)
BOLLINGER_SQUEEZE_VOLUME_TAKE_PROFIT_PCTS = (1.0, 1.5, 2.0)
BOLLINGER_SQUEEZE_VOLUME_STOP_LOSS_PCTS = (0.8, 1.2)
BOLLINGER_SQUEEZE_VOLUME_MAX_HOLD_HOURS = (6, 12, 24)
BOLLINGER_SQUEEZE_PERIOD = 20
BOLLINGER_SQUEEZE_STDDEV = 2.0
BOLLINGER_SQUEEZE_BANDWIDTH_LOOKBACK = 100
BOLLINGER_SQUEEZE_BANDWIDTH_PERCENTILE = 25
BOLLINGER_SQUEEZE_EMA_PERIOD = 20
BOLLINGER_SQUEEZE_EMA_SLOPE_LOOKBACK = 3
BOLLINGER_SQUEEZE_VOLUME_MA_PERIOD = 20
BOLLINGER_SQUEEZE_RECENT_PUMP_LOOKBACK = 6
EMA_VOLUME_EXPANSION_MARKETS = ("KRW-BTC", "KRW-SOL", "KRW-DOGE")
EMA_VOLUME_EXPANSION_DAYS = 365
EMA_VOLUME_EXPANSION_WALK_FORWARD_WINDOW_DAYS = 30
EMA_VOLUME_EXPANSION_TIMEFRAMES = (5, 15)
EMA_VOLUME_EXPANSION_MULTIPLIERS = (2.0, 2.5, 3.0)
EMA_VOLUME_EXPANSION_BTC_FILTER_PCTS = (-0.5, -0.3, 0.0)
EMA_VOLUME_EXPANSION_TAKE_PROFIT_PCTS = (1.0, 1.5, 2.0)
EMA_VOLUME_EXPANSION_STOP_LOSS_PCTS = (0.8, 1.2)
EMA_VOLUME_EXPANSION_MAX_HOLD_HOURS = (6, 12, 24)
EMA_VOLUME_EXPANSION_FAST = 10
EMA_VOLUME_EXPANSION_SLOW = 50
EMA_VOLUME_EXPANSION_SLOPE_EMA = 20
EMA_VOLUME_EXPANSION_SLOPE_LOOKBACK = 3
EMA_VOLUME_EXPANSION_VOLUME_MA_PERIOD = 20
EMA_VOLUME_EXPANSION_CLOSE_LOCATION_MIN = 0.70
EMA_VOLUME_EXPANSION_BTC_LOOKBACK_HOURS = 1
TREND_STRATEGY_MARKETS = ("KRW-BTC", "KRW-SOL", "KRW-DOGE")
TREND_STRATEGY_DAYS = 365
TREND_STRATEGY_WALK_FORWARD_WINDOW_DAYS = 30
TREND_STRATEGY_ROWS = (
    ("donchian_5m", "5m"),
    ("donchian_15m", "15m"),
    ("ema_trend_5m", "5m"),
    ("ema_trend_15m", "15m"),
)


@dataclass
class Candle:
    market: str
    ts: str
    price: float
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: float | None = None

    @property
    def open_price(self) -> float:
        return self.price if self.open is None else self.open

    @property
    def high_price(self) -> float:
        return self.price if self.high is None else self.high

    @property
    def low_price(self) -> float:
        return self.price if self.low is None else self.low


VOLUME_EMA_MARKETS = ("KRW-BTC", "KRW-SOL", "KRW-DOGE")
VOLUME_EMA_DAYS = 365
VOLUME_EMA_WALK_FORWARD_WINDOW_DAYS = 30
VOLUME_SPIKE_LOOKBACK = 10
VOLUME_SPIKE_MULTIPLIERS = (2.0, 3.0)


@dataclass
class Position:
    quantity: float
    average_entry_price: float
    entry_ts: str
    entry_fee_krw: float = 0.0


def main() -> None:
    args = parse_args()
    explicit_days = args.days is not None
    explicit_walk_forward_window_days = args.walk_forward_window_days is not None
    apply_profile_defaults(args)
    args.explicit_days = explicit_days
    args.explicit_walk_forward_window_days = explicit_walk_forward_window_days
    if args.json_report and not (args.walk_forward or args.compare_all_strategies or args.compare_bollinger_rsi):
        raise SystemExit("--json-report requires --walk-forward, --compare-all-strategies, or --compare-bollinger-rsi")

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
        if args.compare_bollinger_rsi:
            print_bollinger_rsi_comparison(conn, args, markets)
            return
        if args.compare_market_subsets:
            print_market_subset_comparison(conn, args, configured_markets(config))
            return
        if args.compare_fees:
            print_fee_sensitivity_comparison(conn, args, markets)
            return
        if args.compare_hold_tp:
            print_hold_tp_comparison(conn)
            return
        if args.compare_dynamic_universe_hold:
            print_dynamic_universe_hold_comparison(conn, config)
            return
        if args.compare_fixed_universes:
            print_fixed_universe_comparison(conn)
            return
        if args.compare_ichimoku:
            print_ichimoku_comparison(conn)
            return
        if args.compare_ichimoku_strict:
            print_ichimoku_strict_comparison(conn)
            return
        if args.compare_macd_trend:
            print_macd_trend_comparison(conn)
            return
        if args.compare_volume_ema:
            print_volume_ema_comparison(conn)
            return
        if args.compare_bollinger_squeeze_volume:
            print_bollinger_squeeze_volume_comparison(conn)
            return
        if args.compare_ema_volume_expansion:
            print_ema_volume_expansion_comparison(conn)
            return
        if args.compare_trend_strategies:
            print_trend_strategy_comparison(conn)
            return
        if args.trade_attribution:
            print_trade_attribution(conn)
            return
        if args.long_validation:
            print_long_validation(conn, args)
            return
        if args.compare_rsi:
            print_rsi_comparison(conn, args, markets)
            return
        if args.walk_forward:
            print_walk_forward(conn, args, markets)
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
            max_hold_hours=getattr(args, "max_hold_hours", None),
        )

    summary.pop("all_trades", None)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest candle-based technical strategies.")
    parser.add_argument("--profile", choices=profile_names())
    parser.add_argument("--strategy", choices=STRATEGIES)
    market_group = parser.add_mutually_exclusive_group()
    market_group.add_argument("--market", action="append", dest="markets")
    market_group.add_argument("--all-markets", action="store_true")
    parser.add_argument("--days", type=positive_int)
    parser.add_argument("--interval", default=DEFAULT_INTERVAL)
    parser.add_argument("--trade-notional-krw", type=positive_float, default=DEFAULT_TRADE_NOTIONAL_KRW)
    parser.add_argument("--fee-rate", type=non_negative_float, default=DEFAULT_FEE_RATE)
    parser.add_argument("--min-signal-gap-minutes", type=non_negative_int)
    parser.add_argument("--bollinger-period", type=positive_int)
    parser.add_argument("--bollinger-stddev", type=positive_float)
    parser.add_argument("--take-profit-pct", type=non_negative_float)
    parser.add_argument("--stop-loss-pct", type=non_negative_float)
    parser.add_argument("--rsi-buy-threshold", type=non_negative_float)
    parser.add_argument("--rsi-sell-threshold", type=non_negative_float)
    parser.add_argument("--walk-forward-window-days", type=positive_int)
    parser.add_argument("--compare", action="store_true")
    parser.add_argument("--compare-bollinger", action="store_true")
    parser.add_argument("--compare-risk", action="store_true")
    parser.add_argument("--compare-all-strategies", action="store_true")
    parser.add_argument("--compare-bollinger-rsi", action="store_true")
    parser.add_argument("--compare-market-subsets", action="store_true")
    parser.add_argument("--compare-fees", action="store_true")
    parser.add_argument("--compare-hold-tp", action="store_true")
    parser.add_argument("--compare-dynamic-universe-hold", action="store_true")
    parser.add_argument("--compare-fixed-universes", action="store_true")
    parser.add_argument("--compare-ichimoku", action="store_true")
    parser.add_argument("--compare-ichimoku-strict", action="store_true")
    parser.add_argument("--compare-macd-trend", action="store_true")
    parser.add_argument("--compare-volume-ema", action="store_true")
    parser.add_argument("--compare-bollinger-squeeze-volume", action="store_true")
    parser.add_argument("--compare-ema-volume-expansion", action="store_true")
    parser.add_argument("--compare-trend-strategies", action="store_true")
    parser.add_argument("--trade-attribution", action="store_true")
    parser.add_argument("--long-validation", action="store_true")
    parser.add_argument("--compare-rsi", action="store_true")
    parser.add_argument("--walk-forward", action="store_true")
    parser.add_argument("--json-report", action="store_true")
    parser.add_argument("--breakdown-by-market", action="store_true")
    return parser.parse_args()


PROFILE_DEFAULTS: dict[str, Any] = {
    "strategy": "ema",
    "days": DEFAULT_DAYS,
    "min_signal_gap_minutes": DEFAULT_MIN_SIGNAL_GAP_MINUTES,
    "bollinger_period": DEFAULT_BOLLINGER_PERIOD,
    "bollinger_stddev": DEFAULT_BOLLINGER_STDDEV,
    "take_profit_pct": DEFAULT_TAKE_PROFIT_PCT,
    "stop_loss_pct": DEFAULT_STOP_LOSS_PCT,
    "rsi_buy_threshold": DEFAULT_RSI_BUY_THRESHOLD,
    "rsi_sell_threshold": DEFAULT_RSI_SELL_THRESHOLD,
    "walk_forward_window_days": DEFAULT_WALK_FORWARD_WINDOW_DAYS,
}


def apply_profile_defaults(args: argparse.Namespace) -> argparse.Namespace:
    if args.profile:
        profile = get_strategy_profile(args.profile)
        if profile.get("markets") and not args.markets and not args.all_markets:
            args.markets = list(profile["markets"])
        for key, value in profile.items():
            if key == "markets":
                continue
            if getattr(args, key) is None:
                setattr(args, key, value)

    for key, value in PROFILE_DEFAULTS.items():
        if getattr(args, key) is None:
            setattr(args, key, value)
    return args


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
    summaries = run_all_strategy_summaries(conn, args, markets)
    if args.json_report:
        report = build_compare_all_strategies_report(args, markets, summaries)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return

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


def run_all_strategy_summaries(
    conn: sqlite3.Connection,
    args: argparse.Namespace,
    markets: list[str],
) -> list[dict[str, Any]]:
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
    return summaries


def build_compare_all_strategies_report(
    args: argparse.Namespace,
    markets: list[str],
    summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    strategy_reports = [compare_strategy_report(summary) for summary in summaries]
    best_by_return = best_strategy_by_return(strategy_reports)
    research_candidates = [
        summary for summary in strategy_reports
        if summary["verdict"] == "RESEARCH_CANDIDATE"
    ]
    return {
        "mode": "compare_all_strategies",
        "markets": markets,
        "days": args.days,
        "interval": args.interval,
        "parameters": {
            "trade_notional_krw": args.trade_notional_krw,
            "fee_rate": args.fee_rate,
            "min_signal_gap_minutes": args.min_signal_gap_minutes,
            "bollinger_period": args.bollinger_period,
            "bollinger_stddev": args.bollinger_stddev,
            "rsi_buy_threshold": args.rsi_buy_threshold,
            "rsi_sell_threshold": args.rsi_sell_threshold,
            "take_profit_pct": args.take_profit_pct,
            "stop_loss_pct": args.stop_loss_pct,
        },
        "strategies": strategy_reports,
        "best_by_return": best_by_return,
        "best_research_candidate": best_strategy_by_return(research_candidates),
        "research_candidate_count": len(research_candidates),
    }


def compare_strategy_report(summary: dict[str, Any]) -> dict[str, Any]:
    report = {
        "strategy": summary["strategy"],
        "return_pct": summary["return_pct"],
        "trade_count": summary["trade_count"],
        "buy_count": summary["buy_count"],
        "sell_count": summary["sell_count"],
        "total_fees_krw": summary["total_fees_krw"],
        "max_drawdown_pct": summary["max_drawdown_pct"],
        "average_hold_minutes": summary["average_hold_minutes"],
        "take_profit_count": summary["take_profit_count"],
        "stop_loss_count": summary["stop_loss_count"],
        "signal_exit_count": summary["signal_exit_count"],
    }
    report["verdict"] = classify_single_backtest_verdict(report)
    return report


def classify_single_backtest_verdict(summary: dict[str, Any]) -> str:
    trade_count = int(summary["trade_count"])
    if trade_count == 0:
        return "NO_TRADES"
    if trade_count < 10:
        return "TOO_FEW_TRADES"
    if float(summary["return_pct"]) <= 0:
        return "WEAK_EDGE"
    if float(summary["max_drawdown_pct"]) > 2.0:
        return "HIGH_DRAWDOWN"
    return "RESEARCH_CANDIDATE"


def best_strategy_by_return(summaries: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not summaries:
        return None
    return max(summaries, key=lambda summary: float(summary["return_pct"]))


def print_bollinger_rsi_comparison(conn: sqlite3.Connection, args: argparse.Namespace, markets: list[str]) -> None:
    results = run_bollinger_rsi_sweep(conn, args, markets)
    if args.json_report:
        report = build_bollinger_rsi_sweep_report(args, markets, results)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return

    table = Table(title="Bollinger/RSI Parameter Sweep")
    table.add_column("strategy")
    table.add_column("period", justify="right")
    table.add_column("stddev", justify="right")
    table.add_column("rsi_buy", justify="right")
    table.add_column("rsi_sell", justify="right")
    table.add_column("trade_count", justify="right")
    table.add_column("return_pct", justify="right")
    table.add_column("total_fees_krw", justify="right")
    table.add_column("max_drawdown_pct", justify="right")
    table.add_column("verdict")

    for result in sorted(results, key=lambda item: item["return_pct"], reverse=True):
        table.add_row(
            result["strategy"],
            str(result["bollinger_period"]),
            format_float(result["bollinger_stddev"]),
            format_float(result["rsi_buy_threshold"]),
            format_float(result["rsi_sell_threshold"]),
            str(result["trade_count"]),
            format_float(result["return_pct"]),
            format_float(result["total_fees_krw"]),
            format_float(result["max_drawdown_pct"]),
            result["verdict"],
        )

    Console(width=160).print(table)


def run_bollinger_rsi_sweep(
    conn: sqlite3.Connection,
    args: argparse.Namespace,
    markets: list[str],
) -> list[dict[str, Any]]:
    grid = bollinger_rsi_parameter_grid()
    results = []
    total_runs = len(grid)
    for index, params in enumerate(grid, start=1):
        print(
            f"[{index}/{total_runs}] "
            f"bollinger_period={params['bollinger_period']} "
            f"stddev={params['bollinger_stddev']} "
            f"rsi_buy={params['rsi_buy_threshold']} "
            f"rsi_sell={params['rsi_sell_threshold']}",
            file=sys.stderr,
            flush=True,
        )
        results.append(run_bollinger_rsi_sweep_item(conn, args, markets, params))
    return results


def bollinger_rsi_parameter_grid() -> list[dict[str, Any]]:
    return [
        {
            "bollinger_period": period,
            "bollinger_stddev": stddev,
            "rsi_buy_threshold": buy_threshold,
            "rsi_sell_threshold": sell_threshold,
        }
        for period in BOLLINGER_RSI_SWEEP_PERIODS
        for stddev in BOLLINGER_RSI_SWEEP_STDDEVS
        for buy_threshold in BOLLINGER_RSI_SWEEP_BUY_THRESHOLDS
        for sell_threshold in BOLLINGER_RSI_SWEEP_SELL_THRESHOLDS
    ]


def run_bollinger_rsi_sweep_item(
    conn: sqlite3.Connection,
    args: argparse.Namespace,
    markets: list[str],
    params: dict[str, Any],
) -> dict[str, Any]:
    summary = run_backtest(
        conn=conn,
        strategy=args.strategy,
        markets=markets,
        days=args.days,
        interval=args.interval,
        trade_notional_krw=args.trade_notional_krw,
        fee_rate=args.fee_rate,
        min_signal_gap_minutes=args.min_signal_gap_minutes,
        bollinger_period=params["bollinger_period"],
        bollinger_stddev=params["bollinger_stddev"],
        take_profit_pct=args.take_profit_pct,
        stop_loss_pct=args.stop_loss_pct,
        rsi_buy_threshold=params["rsi_buy_threshold"],
        rsi_sell_threshold=params["rsi_sell_threshold"],
    )
    return bollinger_rsi_sweep_result(summary, args, params)


def bollinger_rsi_sweep_result(
    summary: dict[str, Any],
    args: argparse.Namespace,
    params: dict[str, Any],
) -> dict[str, Any]:
    result = {
        "strategy": summary["strategy"],
        "bollinger_period": params["bollinger_period"],
        "bollinger_stddev": params["bollinger_stddev"],
        "rsi_buy_threshold": params["rsi_buy_threshold"],
        "rsi_sell_threshold": params["rsi_sell_threshold"],
        "take_profit_pct": args.take_profit_pct,
        "stop_loss_pct": args.stop_loss_pct,
        "min_signal_gap_minutes": args.min_signal_gap_minutes,
        "trade_notional_krw": args.trade_notional_krw,
        "fee_rate": args.fee_rate,
        "return_pct": summary["return_pct"],
        "trade_count": summary["trade_count"],
        "buy_count": summary["buy_count"],
        "sell_count": summary["sell_count"],
        "total_fees_krw": summary["total_fees_krw"],
        "max_drawdown_pct": summary["max_drawdown_pct"],
        "average_hold_minutes": summary["average_hold_minutes"],
        "take_profit_count": summary["take_profit_count"],
        "stop_loss_count": summary["stop_loss_count"],
        "signal_exit_count": summary["signal_exit_count"],
    }
    result["verdict"] = classify_single_backtest_verdict(result)
    return result


def build_bollinger_rsi_sweep_report(
    args: argparse.Namespace,
    markets: list[str],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    sorted_results = sorted(results, key=lambda result: result["return_pct"], reverse=True)
    research_candidates = [
        result for result in sorted_results
        if result["verdict"] == "RESEARCH_CANDIDATE"
    ]
    return {
        "mode": "bollinger_rsi_parameter_sweep",
        "markets": markets,
        "days": args.days,
        "interval": args.interval,
        "strategy": args.strategy,
        "parameter_grid": bollinger_rsi_parameter_grid_summary(),
        "result_count": len(sorted_results),
        "research_candidate_count": len(research_candidates),
        "best_by_return": best_strategy_by_return(sorted_results),
        "best_research_candidate": best_strategy_by_return(research_candidates),
        "results": sorted_results,
    }


def bollinger_rsi_parameter_grid_summary() -> dict[str, Any]:
    return {
        "bollinger_periods": list(BOLLINGER_RSI_SWEEP_PERIODS),
        "bollinger_stddevs": list(BOLLINGER_RSI_SWEEP_STDDEVS),
        "rsi_buy_thresholds": list(BOLLINGER_RSI_SWEEP_BUY_THRESHOLDS),
        "rsi_sell_thresholds": list(BOLLINGER_RSI_SWEEP_SELL_THRESHOLDS),
        "total_runs": len(bollinger_rsi_parameter_grid()),
    }


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


def print_walk_forward(conn: sqlite3.Connection, args: argparse.Namespace, markets: list[str]) -> None:
    summaries = run_walk_forward_summaries(conn, args, markets)
    if args.json_report:
        report = build_walk_forward_report(args, markets, summaries)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return

    table = Table(title="Walk-Forward Backtest")
    table.add_column("window_start")
    table.add_column("window_end")
    table.add_column("trade_count", justify="right")
    table.add_column("return_pct", justify="right")
    table.add_column("total_fees_krw", justify="right")
    table.add_column("max_drawdown_pct", justify="right")

    for summary in summaries:
        table.add_row(
            summary["window_start"],
            summary["window_end"],
            str(summary["trade_count"]),
            format_float(summary["return_pct"]),
            format_float(summary["total_fees_krw"]),
            format_float(summary["max_drawdown_pct"]),
        )

    report_summary = summarize_walk_forward(summaries)
    summary_table = Table(title="Walk-Forward Summary")
    summary_table.add_column("average_return_pct", justify="right")
    summary_table.add_column("median_return_pct", justify="right")
    summary_table.add_column("positive_window_count", justify="right")
    summary_table.add_column("negative_window_count", justify="right")
    summary_table.add_column("worst_window_return_pct", justify="right")
    summary_table.add_column("best_window_return_pct", justify="right")
    summary_table.add_row(
        format_optional_float(report_summary["average_return_pct"]),
        format_optional_float(report_summary["median_return_pct"]),
        str(report_summary["positive_window_count"]),
        str(report_summary["negative_window_count"]),
        format_optional_float(report_summary["worst_window_return_pct"]),
        format_optional_float(report_summary["best_window_return_pct"]),
    )

    console = Console(width=140)
    console.print(table)
    console.print(summary_table)


def run_walk_forward_summaries(
    conn: sqlite3.Connection,
    args: argparse.Namespace,
    markets: list[str],
) -> list[dict[str, Any]]:
    windows = walk_forward_windows(args.days, args.walk_forward_window_days)
    summaries = []
    for window_start, window_end in windows:
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
            max_hold_hours=getattr(args, "max_hold_hours", None),
            window_start=window_start,
            window_end=window_end,
        )
        summary["window_start"] = format_utc(window_start)
        summary["window_end"] = format_utc(window_end)
        summaries.append(summary)
    return summaries


def build_walk_forward_report(
    args: argparse.Namespace,
    markets: list[str],
    summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    report_summary = summarize_walk_forward(summaries)
    return {
        "strategy": args.strategy,
        "markets": markets,
        "days": args.days,
        "interval": args.interval,
        "walk_forward_window_days": args.walk_forward_window_days,
        "parameters": {
            "bollinger_period": args.bollinger_period,
            "bollinger_stddev": args.bollinger_stddev,
            "rsi_buy_threshold": args.rsi_buy_threshold,
            "rsi_sell_threshold": args.rsi_sell_threshold,
            "take_profit_pct": args.take_profit_pct,
            "stop_loss_pct": args.stop_loss_pct,
            "min_signal_gap_minutes": args.min_signal_gap_minutes,
        },
        "windows": [walk_forward_window_report(summary) for summary in summaries],
        "summary": report_summary,
        "verdict": classify_walk_forward_verdict(report_summary),
    }


def walk_forward_window_report(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "window_start": summary["window_start"],
        "window_end": summary["window_end"],
        "return_pct": summary["return_pct"],
        "trade_count": summary["trade_count"],
        "buy_count": summary["buy_count"],
        "sell_count": summary["sell_count"],
        "total_fees_krw": summary["total_fees_krw"],
        "max_drawdown_pct": summary["max_drawdown_pct"],
        "average_hold_minutes": summary["average_hold_minutes"],
    }


def summarize_walk_forward(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    returns = [float(summary["return_pct"]) for summary in summaries]
    trade_counts = [int(summary["trade_count"]) for summary in summaries]
    drawdowns = [float(summary["max_drawdown_pct"]) for summary in summaries]
    return {
        "average_return_pct": mean(returns) if returns else None,
        "median_return_pct": median(returns) if returns else None,
        "positive_window_count": sum(1 for value in returns if value > 0),
        "negative_window_count": sum(1 for value in returns if value < 0),
        "worst_window_return_pct": min(returns) if returns else None,
        "best_window_return_pct": max(returns) if returns else None,
        "total_trade_count": sum(trade_counts),
        "average_trade_count_per_window": mean(trade_counts) if trade_counts else None,
        "average_max_drawdown_pct": mean(drawdowns) if drawdowns else None,
        "worst_max_drawdown_pct": max(drawdowns) if drawdowns else None,
    }


def classify_walk_forward_verdict(summary: dict[str, Any]) -> str:
    total_trade_count = int(summary["total_trade_count"])
    if total_trade_count == 0:
        return "NO_TRADES"
    if total_trade_count < 10:
        return "TOO_FEW_TRADES"
    if summary["negative_window_count"] > summary["positive_window_count"]:
        return "UNSTABLE"
    average_return_pct = summary["average_return_pct"]
    if average_return_pct is None or average_return_pct <= 0:
        return "WEAK_EDGE"
    return "RESEARCH_CANDIDATE"


def print_fee_sensitivity_comparison(conn: sqlite3.Connection, args: argparse.Namespace, markets: list[str]) -> None:
    rows = [run_fee_sensitivity_summary(conn, args, markets, fee_rate) for fee_rate in FEE_SWEEP_RATES]

    table = Table(title="Fee Sensitivity Walk-Forward Comparison")
    table.add_column("fee_rate", justify="right")
    table.add_column("average_return_pct", justify="right")
    table.add_column("median_return_pct", justify="right")
    table.add_column("positive_window_count", justify="right")
    table.add_column("negative_window_count", justify="right")
    table.add_column("max_drawdown_pct", justify="right")

    for row in rows:
        table.add_row(
            format_float(row["fee_rate"]),
            format_optional_float(row["average_return_pct"]),
            format_optional_float(row["median_return_pct"]),
            str(row["positive_window_count"]),
            str(row["negative_window_count"]),
            format_optional_float(row["max_drawdown_pct"]),
        )

    Console(width=140).print(table)


def run_fee_sensitivity_summary(
    conn: sqlite3.Connection,
    args: argparse.Namespace,
    markets: list[str],
    fee_rate: float,
) -> dict[str, Any]:
    fee_args = argparse.Namespace(**{**vars(args), "fee_rate": fee_rate})
    return summarize_fee_sensitivity(fee_rate, run_walk_forward_summaries(conn, fee_args, markets))


def summarize_fee_sensitivity(fee_rate: float, summaries: list[dict[str, Any]]) -> dict[str, Any]:
    walk_forward_summary = summarize_walk_forward(summaries)
    return {
        "fee_rate": fee_rate,
        "average_return_pct": walk_forward_summary["average_return_pct"],
        "median_return_pct": walk_forward_summary["median_return_pct"],
        "positive_window_count": walk_forward_summary["positive_window_count"],
        "negative_window_count": walk_forward_summary["negative_window_count"],
        "max_drawdown_pct": walk_forward_summary["worst_max_drawdown_pct"],
    }

def print_hold_tp_comparison(conn: sqlite3.Connection) -> None:
    window_inputs = hold_tp_window_inputs(conn)
    rows = sort_hold_tp_rows([
        run_hold_tp_summary(window_inputs, max_hold_hours, take_profit_pct)
        for max_hold_hours in HOLD_TP_MAX_HOLD_HOURS
        for take_profit_pct in HOLD_TP_TAKE_PROFIT_PCTS
    ])
    rows.insert(0, run_hold_tp_baseline_summary(window_inputs))

    table = Table(title="Candidate v2 Hold/Take-Profit Walk-Forward Sweep")
    table.add_column("label", no_wrap=True)
    table.add_column("max_hold_hours", no_wrap=True, justify="right")
    table.add_column("take_profit_pct", no_wrap=True, justify="right")
    table.add_column("average_return_pct", no_wrap=True, justify="right")
    table.add_column("median_return_pct", no_wrap=True, justify="right")
    table.add_column("positive_window_count", no_wrap=True, justify="right")
    table.add_column("negative_window_count", no_wrap=True, justify="right")
    table.add_column("worst_window_return_pct", no_wrap=True, justify="right")
    table.add_column("best_window_return_pct", no_wrap=True, justify="right")
    table.add_column("max_drawdown_pct", no_wrap=True, justify="right")
    table.add_column("trade_count", no_wrap=True, justify="right")
    table.add_column("forced_exit_count", no_wrap=True, justify="right")
    table.add_column("signal_exit_count", no_wrap=True, justify="right")
    table.add_column("take_profit_count", no_wrap=True, justify="right")
    table.add_column("average_hold_minutes", no_wrap=True, justify="right")

    for row in rows:
        table.add_row(
            row["label"],
            format_max_hold_hours(row["max_hold_hours"]),
            format_float(row["take_profit_pct"]),
            format_optional_float(row["average_return_pct"]),
            format_optional_float(row["median_return_pct"]),
            str(row["positive_window_count"]),
            str(row["negative_window_count"]),
            format_optional_float(row["worst_window_return_pct"]),
            format_optional_float(row["best_window_return_pct"]),
            format_optional_float(row["max_drawdown_pct"]),
            str(row["trade_count"]),
            str(row["forced_exit_count"]),
            str(row["signal_exit_count"]),
            str(row["take_profit_count"]),
            format_optional_float(row["average_hold_minutes"]),
        )

    Console(width=320).print(table)


def run_hold_tp_summary(
    window_inputs: list[dict[str, Any]],
    max_hold_hours: int,
    take_profit_pct: float,
) -> dict[str, Any]:
    args = hold_tp_sweep_args(max_hold_hours, take_profit_pct)
    summaries = [run_hold_tp_window_summary(window_input, args) for window_input in window_inputs]
    return summarize_hold_tp_result(
        f"hold_{max_hold_hours}h_tp_{take_profit_pct:g}",
        max_hold_hours,
        take_profit_pct,
        summaries,
    )


def run_hold_tp_baseline_summary(window_inputs: list[dict[str, Any]]) -> dict[str, Any]:
    args = hold_tp_baseline_args()
    summaries = [run_hold_tp_window_summary(window_input, args) for window_input in window_inputs]
    return summarize_hold_tp_result(
        HOLD_TP_BASELINE_LABEL,
        None,
        args.take_profit_pct,
        summaries,
    )


def hold_tp_window_inputs(
    conn: sqlite3.Connection,
    windows: list[tuple[datetime, datetime]] | None = None,
) -> list[dict[str, Any]]:
    args = hold_tp_baseline_args()
    selected_windows = windows or walk_forward_windows(args.days, args.walk_forward_window_days)
    return [
        build_hold_tp_window_input(conn, args, list(HOLD_TP_MARKETS), window_start, window_end)
        for window_start, window_end in selected_windows
    ]


def build_hold_tp_window_input(
    conn: sqlite3.Connection,
    args: argparse.Namespace,
    markets: list[str],
    window_start: datetime,
    window_end: datetime,
) -> dict[str, Any]:
    candles_by_market = {
        market: load_candles(conn, market, args.interval, args.days, window_start=window_start, window_end=window_end)
        for market in markets
    }
    raw_signals_by_market = {
        market: strategy_signals(
            args.strategy,
            candles,
            bollinger_period=args.bollinger_period,
            bollinger_stddev=args.bollinger_stddev,
            rsi_buy_threshold=args.rsi_buy_threshold,
            rsi_sell_threshold=args.rsi_sell_threshold,
        )
        for market, candles in candles_by_market.items()
    }
    signals_by_market = {
        market: filter_signals_by_gap(signals, args.min_signal_gap_minutes)
        for market, signals in raw_signals_by_market.items()
    }
    return {
        "window_start": window_start,
        "window_end": window_end,
        "markets": markets,
        "events": build_price_events(candles_by_market, signals_by_market),
        "final_prices": {
            market: candles[-1].price
            for market, candles in candles_by_market.items()
            if candles
        },
        "raw_signal_count": sum(len(signals) for signals in raw_signals_by_market.values()),
        "accepted_signal_count": sum(len(signals) for signals in signals_by_market.values()),
    }


def run_hold_tp_window_summary(window_input: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    cash = START_CASH_KRW
    positions: dict[str, Position] = {}
    buy_count = 0
    sell_count = 0
    take_profit_count = 0
    stop_loss_count = 0
    signal_exit_count = 0
    forced_exit_count = 0
    total_fees_krw = 0.0
    realized_pnl_krw = 0.0
    trades: list[dict[str, Any]] = []
    hold_minutes: list[float] = []
    equity_curve: list[dict[str, Any]] = []
    latest_prices: dict[str, float] = {}

    for event in window_input["events"]:
        market = event["market"]
        ts = event["ts"]
        price = float(event["price"])
        latest_prices[market] = price
        signal = event.get("signal")

        risk_exit_reason = risk_exit_for_position(
            positions.get(market),
            price,
            ts=ts,
            take_profit_pct=args.take_profit_pct,
            stop_loss_pct=args.stop_loss_pct,
            max_hold_hours=args.max_hold_hours,
        )
        if risk_exit_reason is not None:
            position = positions.pop(market)
            cash, realized_delta, fee_krw, trade = close_position(
                cash, position, ts, market, price, args.fee_rate, risk_exit_reason
            )
            realized_pnl_krw += realized_delta
            total_fees_krw += fee_krw
            sell_count += 1
            if risk_exit_reason == "TAKE_PROFIT":
                take_profit_count += 1
            elif risk_exit_reason == "STOP_LOSS":
                stop_loss_count += 1
            elif risk_exit_reason == "MAX_HOLD":
                forced_exit_count += 1
            hold_minutes.append(position_hold_minutes(position, ts))
            trades.append(trade)
        elif signal == "BUY" and market not in positions:
            total_cost = args.trade_notional_krw * (1 + args.fee_rate)
            if cash >= total_cost:
                quantity = args.trade_notional_krw / price
                fee_krw = args.trade_notional_krw * args.fee_rate
                cash -= total_cost
                positions[market] = Position(
                    quantity=quantity,
                    average_entry_price=price,
                    entry_ts=ts,
                    entry_fee_krw=fee_krw,
                )
                total_fees_krw += fee_krw
                buy_count += 1
                trades.append(simulated_trade(ts, "BUY", market, price, quantity, args.trade_notional_krw, fee_krw, "SIGNAL"))
        elif signal == "SELL" and market in positions:
            position = positions.pop(market)
            cash, realized_delta, fee_krw, trade = close_position(
                cash, position, ts, market, price, args.fee_rate, "SIGNAL"
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

    latest_prices.update(window_input["final_prices"])
    final_equity = estimate_equity(cash, positions, latest_prices)
    window_start = window_input["window_start"]
    window_end = window_input["window_end"]
    accepted_signal_count = window_input["accepted_signal_count"]
    raw_signal_count = window_input["raw_signal_count"]
    return {
        "strategy": args.strategy,
        "markets": window_input.get("markets", list(HOLD_TP_MARKETS)),
        "min_signal_gap_minutes": args.min_signal_gap_minutes,
        "bollinger_period": args.bollinger_period,
        "bollinger_stddev": args.bollinger_stddev,
        "take_profit_pct": args.take_profit_pct,
        "stop_loss_pct": args.stop_loss_pct,
        "max_hold_hours": args.max_hold_hours,
        "rsi_buy_threshold": args.rsi_buy_threshold,
        "rsi_sell_threshold": args.rsi_sell_threshold,
        "window_start": format_utc(window_start),
        "window_end": format_utc(window_end),
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
        "forced_exit_count": forced_exit_count,
        "total_fees_krw": total_fees_krw,
        "realized_pnl_krw": realized_pnl_krw,
        "max_drawdown_pct": max_drawdown_pct(equity_curve),
        "average_hold_minutes": average_hold_minutes(hold_minutes),
        "trades": trades[-20:],
        "all_trades": trades,
    }


def hold_tp_baseline_args() -> argparse.Namespace:
    profile = get_strategy_profile("candidate_v1")
    return argparse.Namespace(
        strategy=profile["strategy"],
        days=profile["days"],
        interval="1m",
        trade_notional_krw=DEFAULT_TRADE_NOTIONAL_KRW,
        fee_rate=DEFAULT_FEE_RATE,
        min_signal_gap_minutes=profile["min_signal_gap_minutes"],
        bollinger_period=profile["bollinger_period"],
        bollinger_stddev=profile["bollinger_stddev"],
        take_profit_pct=profile["take_profit_pct"],
        stop_loss_pct=profile["stop_loss_pct"],
        rsi_buy_threshold=profile["rsi_buy_threshold"],
        rsi_sell_threshold=profile["rsi_sell_threshold"],
        walk_forward_window_days=profile["walk_forward_window_days"],
        max_hold_hours=None,
    )


def hold_tp_sweep_args(max_hold_hours: int, take_profit_pct: float) -> argparse.Namespace:
    args = hold_tp_baseline_args()
    args.take_profit_pct = take_profit_pct
    args.max_hold_hours = max_hold_hours
    return args


def summarize_hold_tp_result(
    label: str,
    max_hold_hours: int | None,
    take_profit_pct: float,
    summaries: list[dict[str, Any]],
    universe_mode: str = "fixed",
    selected_markets_by_window: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    walk_forward_summary = summarize_walk_forward(summaries)
    average_hold_values = [
        float(summary["average_hold_minutes"])
        for summary in summaries
        if summary["average_hold_minutes"] is not None
    ]
    return {
        "label": label,
        "universe_mode": universe_mode,
        "max_hold_hours": max_hold_hours,
        "take_profit_pct": take_profit_pct,
        "average_return_pct": walk_forward_summary["average_return_pct"],
        "median_return_pct": walk_forward_summary["median_return_pct"],
        "positive_window_count": walk_forward_summary["positive_window_count"],
        "negative_window_count": walk_forward_summary["negative_window_count"],
        "worst_window_return_pct": walk_forward_summary["worst_window_return_pct"],
        "best_window_return_pct": walk_forward_summary["best_window_return_pct"],
        "max_drawdown_pct": walk_forward_summary["worst_max_drawdown_pct"],
        "trade_count": walk_forward_summary["total_trade_count"],
        "forced_exit_count": sum(int(summary["forced_exit_count"]) for summary in summaries),
        "signal_exit_count": sum(int(summary["signal_exit_count"]) for summary in summaries),
        "take_profit_count": sum(int(summary["take_profit_count"]) for summary in summaries),
        "average_hold_minutes": mean(average_hold_values) if average_hold_values else None,
        "selected_markets_by_window": selected_markets_by_window or fixed_selected_markets_by_window(summaries),
    }


def sort_hold_tp_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=hold_tp_sort_key)


def format_max_hold_hours(value: int | None) -> str:
    return "none" if value is None else str(value)


def hold_tp_sort_key(row: dict[str, Any]) -> tuple[float, int, float]:
    average_return_pct = row["average_return_pct"]
    max_drawdown_pct = row["max_drawdown_pct"]
    return (
        -(float(average_return_pct) if average_return_pct is not None else float("-inf")),
        -int(row["positive_window_count"]),
        float(max_drawdown_pct) if max_drawdown_pct is not None else float("inf"),
    )


def print_dynamic_universe_hold_comparison(conn: sqlite3.Connection, config: dict[str, Any]) -> None:
    markets = configured_markets_with_candles(conn, configured_markets(config))
    if not markets:
        raise ValueError("No configured markets have candle data")

    rows = dynamic_universe_hold_rows(conn, markets)

    table = Table(title="Dynamic Universe Hold/Take-Profit Walk-Forward Sweep")
    table.add_column("label", no_wrap=True)
    table.add_column("universe_mode", no_wrap=True)
    table.add_column("max_hold_hours", no_wrap=True, justify="right")
    table.add_column("take_profit_pct", no_wrap=True, justify="right")
    table.add_column("average_return_pct", no_wrap=True, justify="right")
    table.add_column("median_return_pct", no_wrap=True, justify="right")
    table.add_column("positive_window_count", no_wrap=True, justify="right")
    table.add_column("negative_window_count", no_wrap=True, justify="right")
    table.add_column("worst_window_return_pct", no_wrap=True, justify="right")
    table.add_column("best_window_return_pct", no_wrap=True, justify="right")
    table.add_column("max_drawdown_pct", no_wrap=True, justify="right")
    table.add_column("trade_count", no_wrap=True, justify="right")
    table.add_column("selected_markets_by_window")

    for row in rows:
        table.add_row(
            row["label"],
            row["universe_mode"],
            format_max_hold_hours(row["max_hold_hours"]),
            format_float(row["take_profit_pct"]),
            format_optional_float(row["average_return_pct"]),
            format_optional_float(row["median_return_pct"]),
            str(row["positive_window_count"]),
            str(row["negative_window_count"]),
            format_optional_float(row["worst_window_return_pct"]),
            format_optional_float(row["best_window_return_pct"]),
            format_optional_float(row["max_drawdown_pct"]),
            str(row["trade_count"]),
            format_selected_markets_by_window(row["selected_markets_by_window"]),
        )

    Console(width=320).print(table)


def dynamic_universe_hold_rows(conn: sqlite3.Connection, markets: list[str]) -> list[dict[str, Any]]:
    args = hold_tp_baseline_args()
    windows = walk_forward_windows(args.days, args.walk_forward_window_days)
    fixed_window_inputs = hold_tp_window_inputs(conn, windows)
    dynamic_window_inputs = dynamic_universe_window_inputs(conn, markets, windows)
    rows = [
        with_universe_mode(run_hold_tp_baseline_summary(fixed_window_inputs), "fixed", "fixed_candidate_v1"),
        with_universe_mode(run_hold_tp_summary(fixed_window_inputs, 6, 2.0), "fixed", "fixed_best_hold_6h_tp_2"),
        run_dynamic_universe_summary(dynamic_window_inputs, None, 0.5, "dynamic_candidate_v1"),
    ]
    sweep_rows = [
        run_dynamic_universe_summary(dynamic_window_inputs, max_hold_hours, take_profit_pct)
        for max_hold_hours in DYNAMIC_UNIVERSE_MAX_HOLD_HOURS
        for take_profit_pct in DYNAMIC_UNIVERSE_TAKE_PROFIT_PCTS
    ]
    return rows + sort_dynamic_universe_rows(sweep_rows)


def run_dynamic_universe_summary(
    window_inputs: list[dict[str, Any]],
    max_hold_hours: int | None,
    take_profit_pct: float,
    label: str | None = None,
) -> dict[str, Any]:
    args = hold_tp_baseline_args()
    args.max_hold_hours = max_hold_hours
    args.take_profit_pct = take_profit_pct
    summaries = [run_hold_tp_window_summary(window_input, args) for window_input in window_inputs]
    return summarize_hold_tp_result(
        label or f"dynamic_hold_{format_max_hold_hours(max_hold_hours)}_tp_{take_profit_pct:g}",
        max_hold_hours,
        take_profit_pct,
        summaries,
        universe_mode="dynamic_top3_prior_7d",
        selected_markets_by_window=selected_markets_by_window(window_inputs),
    )


def with_universe_mode(row: dict[str, Any], universe_mode: str, label: str) -> dict[str, Any]:
    updated = dict(row)
    updated["label"] = label
    updated["universe_mode"] = universe_mode
    return updated


def dynamic_universe_window_inputs(
    conn: sqlite3.Connection,
    markets: list[str],
    windows: list[tuple[datetime, datetime]] | None = None,
) -> list[dict[str, Any]]:
    args = hold_tp_baseline_args()
    selected_windows = windows or walk_forward_windows(args.days, args.walk_forward_window_days)
    window_inputs = []
    for window_start, window_end in selected_windows:
        selected_markets = rank_dynamic_universe_markets(conn, markets, window_start)[:DYNAMIC_UNIVERSE_MARKET_COUNT]
        window_inputs.append(build_hold_tp_window_input(conn, args, selected_markets, window_start, window_end))
    return window_inputs


def rank_dynamic_universe_markets(
    conn: sqlite3.Connection,
    markets: list[str],
    window_start: datetime,
) -> list[str]:
    returns = []
    for market in markets:
        prior_return = prior_market_return(conn, market, window_start, DYNAMIC_UNIVERSE_LOOKBACK_DAYS)
        if prior_return is not None:
            returns.append((market, prior_return))
    returns.sort(key=lambda item: item[1], reverse=True)
    return [market for market, _ in returns]


def prior_market_return(
    conn: sqlite3.Connection,
    market: str,
    window_start: datetime,
    lookback_days: int,
) -> float | None:
    current_price = candle_price_at_or_before(conn, market, window_start)
    reference_price = candle_price_at_or_before(conn, market, window_start - timedelta(days=lookback_days))
    if current_price is None or reference_price is None or reference_price <= 0:
        return None
    return (current_price - reference_price) / reference_price


def candle_price_at_or_before(conn: sqlite3.Connection, market: str, ts: datetime) -> float | None:
    row = conn.execute(
        """
        SELECT trade_price
        FROM candles
        WHERE market = ?
          AND interval = '1m'
          AND candle_date_time_utc <= ?
          AND trade_price IS NOT NULL
        ORDER BY candle_date_time_utc DESC, id DESC
        LIMIT 1
        """,
        (market, format_utc(ts)),
    ).fetchone()
    return float(row["trade_price"]) if row else None


def configured_markets_with_candles(conn: sqlite3.Connection, markets: list[str]) -> list[str]:
    available = []
    for market in markets:
        row = conn.execute(
            """
            SELECT 1
            FROM candles
            WHERE market = ?
              AND interval = '1m'
              AND trade_price IS NOT NULL
            LIMIT 1
            """,
            (market,),
        ).fetchone()
        if row:
            available.append(market)
    return available


def selected_markets_by_window(window_inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "window_start": format_utc(window_input["window_start"]),
            "markets": list(window_input.get("markets", [])),
        }
        for window_input in window_inputs
    ]


def fixed_selected_markets_by_window(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "window_start": summary["window_start"],
            "markets": list(summary.get("markets", HOLD_TP_MARKETS)),
        }
        for summary in summaries
    ]


def format_selected_markets_by_window(values: list[dict[str, Any]]) -> str:
    return "; ".join(
        f"{item['window_start']}={','.join(item['markets'])}"
        for item in values
    )


def sort_dynamic_universe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=hold_tp_sort_key)


def print_fixed_universe_comparison(conn: sqlite3.Connection) -> None:
    rows = fixed_universe_rows(conn)
    Console(width=220).print(fixed_universe_table("Fixed Universe Walk-Forward Comparison", rows))
    Console(width=220).print(fixed_universe_table("Top 10 Fixed Universes", rows[:10]))
    Console(width=220).print(fixed_universe_table("Bottom 10 Fixed Universes", rows[-10:]))


def print_ichimoku_comparison(conn: sqlite3.Connection) -> None:
    rows = ichimoku_comparison_rows(conn)
    table = Table(title="Ichimoku Strategy Family Comparison")
    table.add_column("strategy")
    table.add_column("trade_count", justify="right")
    table.add_column("average_return_pct", justify="right")
    table.add_column("median_return_pct", justify="right")
    table.add_column("positive_window_count", justify="right")
    table.add_column("negative_window_count", justify="right")
    table.add_column("worst_window_return_pct", justify="right")
    table.add_column("best_window_return_pct", justify="right")
    table.add_column("max_drawdown_pct", justify="right")
    table.add_column("average_hold_minutes", justify="right")
    for row in rows:
        table.add_row(
            row["strategy"],
            str(row["trade_count"]),
            format_optional_float(row["average_return_pct"]),
            format_optional_float(row["median_return_pct"]),
            str(row["positive_window_count"]),
            str(row["negative_window_count"]),
            format_optional_float(row["worst_window_return_pct"]),
            format_optional_float(row["best_window_return_pct"]),
            format_optional_float(row["max_drawdown_pct"]),
            format_optional_float(row["average_hold_minutes"]),
        )
    Console(width=160).print(table)


def ichimoku_comparison_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        summarize_strategy_family("candidate_v1", run_walk_forward_summaries(conn, ichimoku_candidate_v1_args(), list(ICHIMOKU_MARKETS))),
        summarize_strategy_family("ichimoku", run_walk_forward_summaries(conn, ichimoku_args(), list(ICHIMOKU_MARKETS))),
    ]


def print_ichimoku_strict_comparison(conn: sqlite3.Connection) -> None:
    rows = sort_ichimoku_strict_rows(ichimoku_strict_comparison_rows(conn))
    table = Table(title="Strict Ichimoku 15m Comparison")
    table.add_column("label")
    table.add_column("trade_count", justify="right")
    table.add_column("average_return_pct", justify="right")
    table.add_column("median_return_pct", justify="right")
    table.add_column("positive_window_count", justify="right")
    table.add_column("negative_window_count", justify="right")
    table.add_column("worst_window_return_pct", justify="right")
    table.add_column("best_window_return_pct", justify="right")
    table.add_column("max_drawdown_pct", justify="right")
    table.add_column("average_hold_minutes", justify="right")
    for row in rows:
        table.add_row(
            row["label"],
            str(row["trade_count"]),
            format_optional_float(row["average_return_pct"]),
            format_optional_float(row["median_return_pct"]),
            str(row["positive_window_count"]),
            str(row["negative_window_count"]),
            format_optional_float(row["worst_window_return_pct"]),
            format_optional_float(row["best_window_return_pct"]),
            format_optional_float(row["max_drawdown_pct"]),
            format_optional_float(row["average_hold_minutes"]),
        )
    Console(width=180).print(table)


def ichimoku_strict_comparison_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = [
        summarize_ichimoku_strict_result(
            "candidate_v1",
            run_walk_forward_summaries(conn, ichimoku_candidate_v1_args(), list(ICHIMOKU_MARKETS)),
        )
    ]
    for take_profit_pct in ICHIMOKU_STRICT_TAKE_PROFIT_PCTS:
        for stop_loss_pct in ICHIMOKU_STRICT_STOP_LOSS_PCTS:
            for max_hold_hours in ICHIMOKU_STRICT_MAX_HOLD_HOURS:
                label = f"ichimoku_strict_15m_tp_{take_profit_pct:g}_sl_{stop_loss_pct:g}_hold_{max_hold_hours}h"
                rows.append(
                    summarize_ichimoku_strict_result(
                        label,
                        run_walk_forward_summaries(
                            conn,
                            ichimoku_strict_args(take_profit_pct, stop_loss_pct, max_hold_hours),
                            list(ICHIMOKU_MARKETS),
                        ),
                    )
                )
    return rows


def ichimoku_strict_args(
    take_profit_pct: float,
    stop_loss_pct: float,
    max_hold_hours: int,
) -> argparse.Namespace:
    args = hold_tp_baseline_args()
    args.strategy = "ichimoku_strict_15m"
    args.days = ICHIMOKU_DAYS
    args.walk_forward_window_days = ICHIMOKU_WALK_FORWARD_WINDOW_DAYS
    args.take_profit_pct = take_profit_pct
    args.stop_loss_pct = stop_loss_pct
    args.max_hold_hours = max_hold_hours
    args.min_signal_gap_minutes = 0
    return args


def summarize_ichimoku_strict_result(label: str, summaries: list[dict[str, Any]]) -> dict[str, Any]:
    row = summarize_strategy_family(label, summaries)
    row["label"] = label
    return row


def sort_ichimoku_strict_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=ichimoku_strict_sort_key)


def ichimoku_strict_sort_key(row: dict[str, Any]) -> tuple[float, int, float]:
    average_return_pct = row["average_return_pct"]
    max_drawdown_pct = row["max_drawdown_pct"]
    return (
        -(float(average_return_pct) if average_return_pct is not None else float("-inf")),
        -int(row["positive_window_count"]),
        float(max_drawdown_pct) if max_drawdown_pct is not None else float("inf"),
    )


def print_macd_trend_comparison(conn: sqlite3.Connection) -> None:
    rows = sort_macd_trend_rows(macd_trend_comparison_rows(conn))
    table = Table(title="MACD + Long Trend Filter Comparison")
    table.add_column("strategy", no_wrap=True)
    table.add_column("trade_count", no_wrap=True, justify="right")
    table.add_column("average_return_pct", no_wrap=True, justify="right")
    table.add_column("median_return_pct", no_wrap=True, justify="right")
    table.add_column("positive_window_count", no_wrap=True, justify="right")
    table.add_column("negative_window_count", no_wrap=True, justify="right")
    table.add_column("worst_window_return_pct", no_wrap=True, justify="right")
    table.add_column("best_window_return_pct", no_wrap=True, justify="right")
    table.add_column("max_drawdown_pct", no_wrap=True, justify="right")
    table.add_column("average_hold_minutes", no_wrap=True, justify="right")
    for row in rows:
        table.add_row(
            row["strategy"],
            str(row["trade_count"]),
            format_optional_float(row["average_return_pct"]),
            format_optional_float(row["median_return_pct"]),
            str(row["positive_window_count"]),
            str(row["negative_window_count"]),
            format_optional_float(row["worst_window_return_pct"]),
            format_optional_float(row["best_window_return_pct"]),
            format_optional_float(row["max_drawdown_pct"]),
            format_optional_float(row["average_hold_minutes"]),
        )
    Console(width=260).print(table)


def macd_trend_comparison_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = [
        summarize_strategy_family(
            "candidate_v1",
            run_walk_forward_summaries(conn, macd_trend_candidate_v1_args(), list(MACD_TREND_MARKETS)),
        )
    ]
    window_inputs = macd_trend_window_inputs(conn)
    for take_profit_pct in MACD_TREND_TAKE_PROFIT_PCTS:
        for stop_loss_pct in MACD_TREND_STOP_LOSS_PCTS:
            for max_hold_hours in MACD_TREND_MAX_HOLD_HOURS:
                args = macd_trend_args(take_profit_pct, stop_loss_pct, max_hold_hours)
                summaries = [run_hold_tp_window_summary(window_input, args) for window_input in window_inputs]
                rows.append(summarize_strategy_family(
                    macd_trend_label(take_profit_pct, stop_loss_pct, max_hold_hours),
                    summaries,
                ))
    return rows


def macd_trend_window_inputs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    args = macd_trend_args(1.0, 0.8, 24)
    windows = walk_forward_windows(args.days, args.walk_forward_window_days)
    return [
        build_hold_tp_window_input(conn, args, list(MACD_TREND_MARKETS), window_start, window_end)
        for window_start, window_end in windows
    ]


def macd_trend_label(take_profit_pct: float, stop_loss_pct: float, max_hold_hours: int) -> str:
    return f"macd_ema_filter_15m_tp_{take_profit_pct:g}_sl_{stop_loss_pct:g}_hold_{max_hold_hours}h"


def macd_trend_candidate_v1_args() -> argparse.Namespace:
    args = hold_tp_baseline_args()
    args.days = MACD_TREND_DAYS
    args.walk_forward_window_days = MACD_TREND_WALK_FORWARD_WINDOW_DAYS
    return args


def macd_trend_args(
    take_profit_pct: float,
    stop_loss_pct: float,
    max_hold_hours: int,
) -> argparse.Namespace:
    args = hold_tp_baseline_args()
    args.strategy = "macd_ema_filter_15m"
    args.days = MACD_TREND_DAYS
    args.walk_forward_window_days = MACD_TREND_WALK_FORWARD_WINDOW_DAYS
    args.take_profit_pct = take_profit_pct
    args.stop_loss_pct = stop_loss_pct
    args.max_hold_hours = max_hold_hours
    args.min_signal_gap_minutes = 0
    return args


def sort_macd_trend_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=trend_strategy_sort_key)


def print_volume_ema_comparison(conn: sqlite3.Connection) -> None:
    rows = sort_volume_ema_rows(volume_ema_comparison_rows(conn))
    table = Table(title="EMA Crossover + Volume Spike Comparison")
    table.add_column("strategy", no_wrap=True)
    table.add_column("trade_count", no_wrap=True, justify="right")
    table.add_column("average_return_pct", no_wrap=True, justify="right")
    table.add_column("median_return_pct", no_wrap=True, justify="right")
    table.add_column("positive_window_count", no_wrap=True, justify="right")
    table.add_column("negative_window_count", no_wrap=True, justify="right")
    table.add_column("worst_window_return_pct", no_wrap=True, justify="right")
    table.add_column("best_window_return_pct", no_wrap=True, justify="right")
    table.add_column("max_drawdown_pct", no_wrap=True, justify="right")
    table.add_column("average_hold_minutes", no_wrap=True, justify="right")
    for row in rows:
        table.add_row(
            row["strategy"],
            str(row["trade_count"]),
            format_optional_float(row["average_return_pct"]),
            format_optional_float(row["median_return_pct"]),
            str(row["positive_window_count"]),
            str(row["negative_window_count"]),
            format_optional_float(row["worst_window_return_pct"]),
            format_optional_float(row["best_window_return_pct"]),
            format_optional_float(row["max_drawdown_pct"]),
            format_optional_float(row["average_hold_minutes"]),
        )
    Console(width=220).print(table)


def volume_ema_comparison_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = [
        summarize_strategy_family(
            "candidate_v1",
            run_walk_forward_summaries(conn, volume_ema_candidate_v1_args(), list(VOLUME_EMA_MARKETS)),
        ),
        summarize_strategy_family(
            "ema",
            run_walk_forward_summaries(conn, volume_ema_args("ema"), list(VOLUME_EMA_MARKETS)),
        ),
    ]
    for multiplier in VOLUME_SPIKE_MULTIPLIERS:
        strategy = f"ema_volume_spike_{multiplier:g}x"
        rows.append(summarize_strategy_family(
            strategy,
            run_walk_forward_summaries(conn, volume_ema_args(strategy), list(VOLUME_EMA_MARKETS)),
        ))
    return rows


def volume_ema_candidate_v1_args() -> argparse.Namespace:
    args = hold_tp_baseline_args()
    args.days = VOLUME_EMA_DAYS
    args.walk_forward_window_days = VOLUME_EMA_WALK_FORWARD_WINDOW_DAYS
    return args


def volume_ema_args(strategy: str) -> argparse.Namespace:
    args = hold_tp_baseline_args()
    args.strategy = strategy
    args.days = VOLUME_EMA_DAYS
    args.walk_forward_window_days = VOLUME_EMA_WALK_FORWARD_WINDOW_DAYS
    args.take_profit_pct = 0.0
    args.stop_loss_pct = 0.0
    args.max_hold_hours = None
    args.min_signal_gap_minutes = 0
    return args


def sort_volume_ema_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=trend_strategy_sort_key)


def print_bollinger_squeeze_volume_comparison(conn: sqlite3.Connection) -> None:
    baseline = bollinger_squeeze_volume_candidate_v1_row(conn)
    squeeze_rows = sort_bollinger_squeeze_volume_rows(bollinger_squeeze_volume_rows(conn))
    best_squeeze = squeeze_rows[0] if squeeze_rows else None

    baseline_table = Table(title="Candidate v1 Baseline")
    add_bollinger_squeeze_volume_columns(baseline_table)
    add_bollinger_squeeze_volume_row(baseline_table, baseline)

    top_table = Table(title="Top 10 Bollinger Squeeze + Volume Rows")
    add_bollinger_squeeze_volume_columns(top_table)
    for row in squeeze_rows[:10]:
        add_bollinger_squeeze_volume_row(top_table, row)

    bottom_table = Table(title="Bottom 10 Bollinger Squeeze + Volume Rows")
    add_bollinger_squeeze_volume_columns(bottom_table)
    for row in squeeze_rows[-10:]:
        add_bollinger_squeeze_volume_row(bottom_table, row)

    console = Console(width=420)
    console.print(baseline_table)
    console.print(top_table)
    console.print(bottom_table)
    console.print(bollinger_squeeze_volume_verdict(baseline, best_squeeze))


def add_bollinger_squeeze_volume_columns(table: Table) -> None:
    table.add_column("label", no_wrap=True)
    table.add_column("timeframe", no_wrap=True, justify="right")
    table.add_column("trade_count", no_wrap=True, justify="right")
    table.add_column("average_return_pct", no_wrap=True, justify="right")
    table.add_column("median_return_pct", no_wrap=True, justify="right")
    table.add_column("positive_window_count", no_wrap=True, justify="right")
    table.add_column("negative_window_count", no_wrap=True, justify="right")
    table.add_column("worst_window_return_pct", no_wrap=True, justify="right")
    table.add_column("best_window_return_pct", no_wrap=True, justify="right")
    table.add_column("max_drawdown_pct", no_wrap=True, justify="right")
    table.add_column("average_hold_minutes", no_wrap=True, justify="right")


def add_bollinger_squeeze_volume_row(table: Table, row: dict[str, Any]) -> None:
    table.add_row(
        row["label"],
        row["timeframe"],
        str(row["trade_count"]),
        format_optional_float(row["average_return_pct"]),
        format_optional_float(row["median_return_pct"]),
        str(row["positive_window_count"]),
        str(row["negative_window_count"]),
        format_optional_float(row["worst_window_return_pct"]),
        format_optional_float(row["best_window_return_pct"]),
        format_optional_float(row["max_drawdown_pct"]),
        format_optional_float(row["average_hold_minutes"]),
    )


def bollinger_squeeze_volume_verdict(
    baseline: dict[str, Any],
    best_squeeze: dict[str, Any] | None,
) -> str:
    if best_squeeze is None:
        return "No squeeze-volume rows were generated."
    beats_baseline = trend_strategy_sort_key(best_squeeze) < trend_strategy_sort_key(baseline)
    if beats_baseline:
        return f"Best squeeze-volume row beats candidate_v1: {best_squeeze['label']}"
    return f"No squeeze-volume row beats candidate_v1. Best squeeze-volume row: {best_squeeze['label']}"


def bollinger_squeeze_volume_candidate_v1_row(conn: sqlite3.Connection) -> dict[str, Any]:
    return summarize_bollinger_squeeze_volume_result(
        label="candidate_v1",
        timeframe="1m",
        summaries=run_walk_forward_summaries(
            conn,
            bollinger_squeeze_volume_candidate_v1_args(),
            list(BOLLINGER_SQUEEZE_VOLUME_MARKETS),
        ),
    )


def bollinger_squeeze_volume_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    windows = walk_forward_windows(
        BOLLINGER_SQUEEZE_VOLUME_DAYS,
        BOLLINGER_SQUEEZE_VOLUME_WALK_FORWARD_WINDOW_DAYS,
    )
    window_inputs_by_timeframe = {
        timeframe: [
            build_bollinger_squeeze_volume_window_input(conn, timeframe, window_start, window_end)
            for window_start, window_end in windows
        ]
        for timeframe in BOLLINGER_SQUEEZE_VOLUME_TIMEFRAMES
    }
    rows = []
    for timeframe in BOLLINGER_SQUEEZE_VOLUME_TIMEFRAMES:
        for volume_multiplier in BOLLINGER_SQUEEZE_VOLUME_MULTIPLIERS:
            for max_recent_pump_pct in BOLLINGER_SQUEEZE_VOLUME_MAX_RECENT_PUMP_PCTS:
                signal_window_inputs = [
                    build_bollinger_squeeze_volume_signal_window_input(
                        window_input,
                        volume_multiplier=volume_multiplier,
                        max_recent_pump_pct=max_recent_pump_pct,
                    )
                    for window_input in window_inputs_by_timeframe[timeframe]
                ]
                for take_profit_pct in BOLLINGER_SQUEEZE_VOLUME_TAKE_PROFIT_PCTS:
                    for stop_loss_pct in BOLLINGER_SQUEEZE_VOLUME_STOP_LOSS_PCTS:
                        for max_hold_hours in BOLLINGER_SQUEEZE_VOLUME_MAX_HOLD_HOURS:
                            summaries = [
                                run_bollinger_squeeze_volume_window_summary(
                                    window_input,
                                    take_profit_pct=take_profit_pct,
                                    stop_loss_pct=stop_loss_pct,
                                    max_hold_hours=max_hold_hours,
                                )
                                for window_input in signal_window_inputs
                            ]
                            rows.append(summarize_bollinger_squeeze_volume_result(
                                label=bollinger_squeeze_volume_label(
                                    timeframe,
                                    volume_multiplier,
                                    max_recent_pump_pct,
                                    take_profit_pct,
                                    stop_loss_pct,
                                    max_hold_hours,
                                ),
                                timeframe=f"{timeframe}m",
                                summaries=summaries,
                            ))
    return rows


def build_bollinger_squeeze_volume_window_input(
    conn: sqlite3.Connection,
    timeframe: int,
    window_start: datetime,
    window_end: datetime,
) -> dict[str, Any]:
    candles_by_market = {}
    for market in BOLLINGER_SQUEEZE_VOLUME_MARKETS:
        one_minute_candles = load_candles(
            conn,
            market,
            "1m",
            BOLLINGER_SQUEEZE_VOLUME_DAYS,
            window_start=window_start,
            window_end=window_end,
        )
        candles_by_market[market] = derive_timeframe_candles(one_minute_candles, timeframe)
    return {
        "window_start": window_start,
        "window_end": window_end,
        "markets": list(BOLLINGER_SQUEEZE_VOLUME_MARKETS),
        "timeframe": timeframe,
        "candles_by_market": candles_by_market,
        "final_prices": {
            market: candles[-1].price
            for market, candles in candles_by_market.items()
            if candles
        },
    }


def build_bollinger_squeeze_volume_signal_window_input(
    window_input: dict[str, Any],
    volume_multiplier: float,
    max_recent_pump_pct: float,
) -> dict[str, Any]:
    signals_by_market = {
        market: bollinger_squeeze_volume_signals(
            candles,
            volume_spike_multiplier=volume_multiplier,
            max_recent_pump_pct=max_recent_pump_pct,
        )
        for market, candles in window_input["candles_by_market"].items()
    }
    return {
        "window_start": window_input["window_start"],
        "window_end": window_input["window_end"],
        "markets": window_input["markets"],
        "events": build_price_events(window_input["candles_by_market"], signals_by_market),
        "final_prices": window_input["final_prices"],
        "raw_signal_count": sum(len(signals) for signals in signals_by_market.values()),
        "accepted_signal_count": sum(len(signals) for signals in signals_by_market.values()),
    }


def run_bollinger_squeeze_volume_window_summary(
    signal_window_input: dict[str, Any],
    take_profit_pct: float,
    stop_loss_pct: float,
    max_hold_hours: int,
) -> dict[str, Any]:
    summary_args = bollinger_squeeze_volume_args(take_profit_pct, stop_loss_pct, max_hold_hours)
    return run_hold_tp_window_summary(signal_window_input, summary_args)


def bollinger_squeeze_volume_candidate_v1_args() -> argparse.Namespace:
    args = hold_tp_baseline_args()
    args.days = BOLLINGER_SQUEEZE_VOLUME_DAYS
    args.walk_forward_window_days = BOLLINGER_SQUEEZE_VOLUME_WALK_FORWARD_WINDOW_DAYS
    return args


def bollinger_squeeze_volume_args(
    take_profit_pct: float,
    stop_loss_pct: float,
    max_hold_hours: int,
) -> argparse.Namespace:
    args = hold_tp_baseline_args()
    args.strategy = "bollinger_squeeze_volume"
    args.days = BOLLINGER_SQUEEZE_VOLUME_DAYS
    args.walk_forward_window_days = BOLLINGER_SQUEEZE_VOLUME_WALK_FORWARD_WINDOW_DAYS
    args.take_profit_pct = take_profit_pct
    args.stop_loss_pct = stop_loss_pct
    args.max_hold_hours = max_hold_hours
    args.min_signal_gap_minutes = 0
    return args


def summarize_bollinger_squeeze_volume_result(
    label: str,
    timeframe: str,
    summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    walk_forward_summary = summarize_walk_forward(summaries)
    average_hold_values = [
        float(summary["average_hold_minutes"])
        for summary in summaries
        if summary["average_hold_minutes"] is not None
    ]
    return {
        "label": label,
        "timeframe": timeframe,
        "trade_count": walk_forward_summary["total_trade_count"],
        "average_return_pct": walk_forward_summary["average_return_pct"],
        "median_return_pct": walk_forward_summary["median_return_pct"],
        "positive_window_count": walk_forward_summary["positive_window_count"],
        "negative_window_count": walk_forward_summary["negative_window_count"],
        "worst_window_return_pct": walk_forward_summary["worst_window_return_pct"],
        "best_window_return_pct": walk_forward_summary["best_window_return_pct"],
        "max_drawdown_pct": walk_forward_summary["worst_max_drawdown_pct"],
        "average_hold_minutes": mean(average_hold_values) if average_hold_values else None,
    }


def bollinger_squeeze_volume_label(
    timeframe: int,
    volume_multiplier: float,
    max_recent_pump_pct: float,
    take_profit_pct: float,
    stop_loss_pct: float,
    max_hold_hours: int,
) -> str:
    return (
        f"squeeze_volume_{timeframe}m_vol_{volume_multiplier:g}x"
        f"_pump_{max_recent_pump_pct:g}_tp_{take_profit_pct:g}"
        f"_sl_{stop_loss_pct:g}_hold_{max_hold_hours}h"
    )


def sort_bollinger_squeeze_volume_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=trend_strategy_sort_key)


def print_ema_volume_expansion_comparison(conn: sqlite3.Connection) -> None:
    baseline = ema_volume_expansion_candidate_v1_row(conn)
    expansion_rows = sort_ema_volume_expansion_rows(ema_volume_expansion_rows(conn))
    best_expansion = expansion_rows[0] if expansion_rows else None

    baseline_table = Table(title="Candidate v1 Baseline")
    add_ema_volume_expansion_columns(baseline_table)
    add_ema_volume_expansion_row(baseline_table, baseline)

    top_table = Table(title="Top 10 EMA Crossover + Bullish Volume Expansion Rows")
    add_ema_volume_expansion_columns(top_table)
    for row in expansion_rows[:10]:
        add_ema_volume_expansion_row(top_table, row)

    bottom_table = Table(title="Bottom 10 EMA Crossover + Bullish Volume Expansion Rows")
    add_ema_volume_expansion_columns(bottom_table)
    for row in expansion_rows[-10:]:
        add_ema_volume_expansion_row(bottom_table, row)

    console = Console(width=420)
    console.print(baseline_table)
    console.print(top_table)
    console.print(bottom_table)
    console.print(ema_volume_expansion_verdict(baseline, best_expansion))


def add_ema_volume_expansion_columns(table: Table) -> None:
    table.add_column("label", no_wrap=True)
    table.add_column("timeframe", no_wrap=True, justify="right")
    table.add_column("trade_count", no_wrap=True, justify="right")
    table.add_column("average_return_pct", no_wrap=True, justify="right")
    table.add_column("median_return_pct", no_wrap=True, justify="right")
    table.add_column("positive_window_count", no_wrap=True, justify="right")
    table.add_column("negative_window_count", no_wrap=True, justify="right")
    table.add_column("worst_window_return_pct", no_wrap=True, justify="right")
    table.add_column("best_window_return_pct", no_wrap=True, justify="right")
    table.add_column("max_drawdown_pct", no_wrap=True, justify="right")
    table.add_column("average_hold_minutes", no_wrap=True, justify="right")


def add_ema_volume_expansion_row(table: Table, row: dict[str, Any]) -> None:
    table.add_row(
        row["label"],
        row["timeframe"],
        str(row["trade_count"]),
        format_optional_float(row["average_return_pct"]),
        format_optional_float(row["median_return_pct"]),
        str(row["positive_window_count"]),
        str(row["negative_window_count"]),
        format_optional_float(row["worst_window_return_pct"]),
        format_optional_float(row["best_window_return_pct"]),
        format_optional_float(row["max_drawdown_pct"]),
        format_optional_float(row["average_hold_minutes"]),
    )


def ema_volume_expansion_verdict(
    baseline: dict[str, Any],
    best_expansion: dict[str, Any] | None,
) -> str:
    if best_expansion is None:
        return "No EMA-volume expansion rows were generated."
    beats_baseline = trend_strategy_sort_key(best_expansion) < trend_strategy_sort_key(baseline)
    if beats_baseline:
        return f"Best EMA-volume row beats candidate_v1: {best_expansion['label']}"
    return f"No EMA-volume row beats candidate_v1. Best EMA-volume row: {best_expansion['label']}"


def ema_volume_expansion_candidate_v1_row(conn: sqlite3.Connection) -> dict[str, Any]:
    return summarize_ema_volume_expansion_result(
        label="candidate_v1",
        timeframe="1m",
        summaries=run_walk_forward_summaries(
            conn,
            ema_volume_expansion_candidate_v1_args(),
            list(EMA_VOLUME_EXPANSION_MARKETS),
        ),
    )


def ema_volume_expansion_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    windows = walk_forward_windows(
        EMA_VOLUME_EXPANSION_DAYS,
        EMA_VOLUME_EXPANSION_WALK_FORWARD_WINDOW_DAYS,
    )
    window_inputs_by_timeframe = {
        timeframe: [
            build_ema_volume_expansion_window_input(conn, timeframe, window_start, window_end)
            for window_start, window_end in windows
        ]
        for timeframe in EMA_VOLUME_EXPANSION_TIMEFRAMES
    }
    rows = []
    for timeframe in EMA_VOLUME_EXPANSION_TIMEFRAMES:
        for volume_multiplier in EMA_VOLUME_EXPANSION_MULTIPLIERS:
            for btc_filter_pct in EMA_VOLUME_EXPANSION_BTC_FILTER_PCTS:
                signal_window_inputs = [
                    build_ema_volume_expansion_signal_window_input(
                        window_input,
                        volume_multiplier=volume_multiplier,
                        btc_filter_pct=btc_filter_pct,
                    )
                    for window_input in window_inputs_by_timeframe[timeframe]
                ]
                for take_profit_pct in EMA_VOLUME_EXPANSION_TAKE_PROFIT_PCTS:
                    for stop_loss_pct in EMA_VOLUME_EXPANSION_STOP_LOSS_PCTS:
                        for max_hold_hours in EMA_VOLUME_EXPANSION_MAX_HOLD_HOURS:
                            summaries = [
                                run_ema_volume_expansion_window_summary(
                                    window_input,
                                    take_profit_pct=take_profit_pct,
                                    stop_loss_pct=stop_loss_pct,
                                    max_hold_hours=max_hold_hours,
                                )
                                for window_input in signal_window_inputs
                            ]
                            rows.append(summarize_ema_volume_expansion_result(
                                label=ema_volume_expansion_label(
                                    timeframe,
                                    volume_multiplier,
                                    btc_filter_pct,
                                    take_profit_pct,
                                    stop_loss_pct,
                                    max_hold_hours,
                                ),
                                timeframe=f"{timeframe}m",
                                summaries=summaries,
                            ))
    return rows


def build_ema_volume_expansion_window_input(
    conn: sqlite3.Connection,
    timeframe: int,
    window_start: datetime,
    window_end: datetime,
) -> dict[str, Any]:
    candles_by_market = {}
    for market in EMA_VOLUME_EXPANSION_MARKETS:
        one_minute_candles = load_candles(
            conn,
            market,
            "1m",
            EMA_VOLUME_EXPANSION_DAYS,
            window_start=window_start,
            window_end=window_end,
        )
        candles_by_market[market] = derive_timeframe_candles(one_minute_candles, timeframe)
    return {
        "window_start": window_start,
        "window_end": window_end,
        "markets": list(EMA_VOLUME_EXPANSION_MARKETS),
        "timeframe": timeframe,
        "candles_by_market": candles_by_market,
        "final_prices": {
            market: candles[-1].price
            for market, candles in candles_by_market.items()
            if candles
        },
    }


def build_ema_volume_expansion_signal_window_input(
    window_input: dict[str, Any],
    volume_multiplier: float,
    btc_filter_pct: float,
) -> dict[str, Any]:
    btc_candles = window_input["candles_by_market"].get("KRW-BTC", [])
    signals_by_market = {
        market: ema_volume_expansion_signals(
            candles,
            btc_candles=btc_candles,
            volume_spike_multiplier=volume_multiplier,
            btc_filter_pct=btc_filter_pct,
        )
        for market, candles in window_input["candles_by_market"].items()
    }
    return {
        "window_start": window_input["window_start"],
        "window_end": window_input["window_end"],
        "markets": window_input["markets"],
        "events": build_price_events(window_input["candles_by_market"], signals_by_market),
        "final_prices": window_input["final_prices"],
        "raw_signal_count": sum(len(signals) for signals in signals_by_market.values()),
        "accepted_signal_count": sum(len(signals) for signals in signals_by_market.values()),
    }


def run_ema_volume_expansion_window_summary(
    signal_window_input: dict[str, Any],
    take_profit_pct: float,
    stop_loss_pct: float,
    max_hold_hours: int,
) -> dict[str, Any]:
    summary_args = ema_volume_expansion_args(take_profit_pct, stop_loss_pct, max_hold_hours)
    return run_hold_tp_window_summary(signal_window_input, summary_args)


def ema_volume_expansion_candidate_v1_args() -> argparse.Namespace:
    args = hold_tp_baseline_args()
    args.days = EMA_VOLUME_EXPANSION_DAYS
    args.walk_forward_window_days = EMA_VOLUME_EXPANSION_WALK_FORWARD_WINDOW_DAYS
    return args


def ema_volume_expansion_args(
    take_profit_pct: float,
    stop_loss_pct: float,
    max_hold_hours: int,
) -> argparse.Namespace:
    args = hold_tp_baseline_args()
    args.strategy = "ema_volume_expansion"
    args.days = EMA_VOLUME_EXPANSION_DAYS
    args.walk_forward_window_days = EMA_VOLUME_EXPANSION_WALK_FORWARD_WINDOW_DAYS
    args.take_profit_pct = take_profit_pct
    args.stop_loss_pct = stop_loss_pct
    args.max_hold_hours = max_hold_hours
    args.min_signal_gap_minutes = 0
    return args


def summarize_ema_volume_expansion_result(
    label: str,
    timeframe: str,
    summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    walk_forward_summary = summarize_walk_forward(summaries)
    average_hold_values = [
        float(summary["average_hold_minutes"])
        for summary in summaries
        if summary["average_hold_minutes"] is not None
    ]
    return {
        "label": label,
        "timeframe": timeframe,
        "trade_count": walk_forward_summary["total_trade_count"],
        "average_return_pct": walk_forward_summary["average_return_pct"],
        "median_return_pct": walk_forward_summary["median_return_pct"],
        "positive_window_count": walk_forward_summary["positive_window_count"],
        "negative_window_count": walk_forward_summary["negative_window_count"],
        "worst_window_return_pct": walk_forward_summary["worst_window_return_pct"],
        "best_window_return_pct": walk_forward_summary["best_window_return_pct"],
        "max_drawdown_pct": walk_forward_summary["worst_max_drawdown_pct"],
        "average_hold_minutes": mean(average_hold_values) if average_hold_values else None,
    }


def ema_volume_expansion_label(
    timeframe: int,
    volume_multiplier: float,
    btc_filter_pct: float,
    take_profit_pct: float,
    stop_loss_pct: float,
    max_hold_hours: int,
) -> str:
    return (
        f"ema_volume_{timeframe}m_vol_{volume_multiplier:g}x"
        f"_btc_{btc_filter_pct:g}_tp_{take_profit_pct:g}"
        f"_sl_{stop_loss_pct:g}_hold_{max_hold_hours}h"
    )


def sort_ema_volume_expansion_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=trend_strategy_sort_key)


def print_trend_strategy_comparison(conn: sqlite3.Connection) -> None:
    rows = sort_trend_strategy_rows(trend_strategy_comparison_rows(conn))
    table = Table(title="Multi-Timeframe Trend Strategy Comparison")
    table.add_column("strategy")
    table.add_column("timeframe")
    table.add_column("trade_count", justify="right")
    table.add_column("average_return_pct", justify="right")
    table.add_column("median_return_pct", justify="right")
    table.add_column("positive_window_count", justify="right")
    table.add_column("negative_window_count", justify="right")
    table.add_column("worst_window_return_pct", justify="right")
    table.add_column("best_window_return_pct", justify="right")
    table.add_column("max_drawdown_pct", justify="right")
    table.add_column("average_hold_minutes", justify="right")
    for row in rows:
        table.add_row(
            row["strategy"],
            row["timeframe"],
            str(row["trade_count"]),
            format_optional_float(row["average_return_pct"]),
            format_optional_float(row["median_return_pct"]),
            str(row["positive_window_count"]),
            str(row["negative_window_count"]),
            format_optional_float(row["worst_window_return_pct"]),
            format_optional_float(row["best_window_return_pct"]),
            format_optional_float(row["max_drawdown_pct"]),
            format_optional_float(row["average_hold_minutes"]),
        )
    Console(width=180).print(table)


def trend_strategy_comparison_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = [
        with_timeframe(
            summarize_strategy_family(
                "candidate_v1",
                run_walk_forward_summaries(conn, trend_candidate_v1_args(), list(TREND_STRATEGY_MARKETS)),
            ),
            "1m",
        )
    ]
    for strategy, timeframe in TREND_STRATEGY_ROWS:
        rows.append(with_timeframe(
            summarize_strategy_family(
                strategy,
                run_walk_forward_summaries(conn, trend_strategy_args(strategy), list(TREND_STRATEGY_MARKETS)),
            ),
            timeframe,
        ))
    return rows


def trend_candidate_v1_args() -> argparse.Namespace:
    args = hold_tp_baseline_args()
    args.days = TREND_STRATEGY_DAYS
    args.walk_forward_window_days = TREND_STRATEGY_WALK_FORWARD_WINDOW_DAYS
    return args


def trend_strategy_args(strategy: str) -> argparse.Namespace:
    args = hold_tp_baseline_args()
    args.strategy = strategy
    args.days = TREND_STRATEGY_DAYS
    args.walk_forward_window_days = TREND_STRATEGY_WALK_FORWARD_WINDOW_DAYS
    args.take_profit_pct = 0.0
    args.stop_loss_pct = 0.0
    args.min_signal_gap_minutes = 0
    return args


def with_timeframe(row: dict[str, Any], timeframe: str) -> dict[str, Any]:
    updated = dict(row)
    updated["timeframe"] = timeframe
    return updated


def sort_trend_strategy_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=trend_strategy_sort_key)


def trend_strategy_sort_key(row: dict[str, Any]) -> tuple[float, int, float]:
    average_return_pct = row["average_return_pct"]
    max_drawdown_pct = row["max_drawdown_pct"]
    return (
        -(float(average_return_pct) if average_return_pct is not None else float("-inf")),
        -int(row["positive_window_count"]),
        float(max_drawdown_pct) if max_drawdown_pct is not None else float("inf"),
    )


def ichimoku_candidate_v1_args() -> argparse.Namespace:
    args = hold_tp_baseline_args()
    args.days = ICHIMOKU_DAYS
    args.walk_forward_window_days = ICHIMOKU_WALK_FORWARD_WINDOW_DAYS
    return args


def ichimoku_args() -> argparse.Namespace:
    args = hold_tp_baseline_args()
    args.strategy = "ichimoku"
    args.days = ICHIMOKU_DAYS
    args.walk_forward_window_days = ICHIMOKU_WALK_FORWARD_WINDOW_DAYS
    return args


def summarize_strategy_family(label: str, summaries: list[dict[str, Any]]) -> dict[str, Any]:
    walk_forward_summary = summarize_walk_forward(summaries)
    hold_values = [
        float(summary["average_hold_minutes"])
        for summary in summaries
        if summary["average_hold_minutes"] is not None
    ]
    return {
        "strategy": label,
        "trade_count": walk_forward_summary["total_trade_count"],
        "average_return_pct": walk_forward_summary["average_return_pct"],
        "median_return_pct": walk_forward_summary["median_return_pct"],
        "positive_window_count": walk_forward_summary["positive_window_count"],
        "negative_window_count": walk_forward_summary["negative_window_count"],
        "worst_window_return_pct": walk_forward_summary["worst_window_return_pct"],
        "best_window_return_pct": walk_forward_summary["best_window_return_pct"],
        "max_drawdown_pct": walk_forward_summary["worst_max_drawdown_pct"],
        "average_hold_minutes": mean(hold_values) if hold_values else None,
    }


def print_trade_attribution(conn: sqlite3.Connection) -> None:
    summaries = candidate_v1_walk_forward_summaries(conn)
    closed_trades = closed_trades_from_summaries(summaries)
    console = Console(width=180)
    console.print(trade_attribution_table("Trade Attribution by Market", attribution_rows(closed_trades, "market"), "market"))
    console.print(trade_attribution_table("Trade Attribution by Month", attribution_rows(closed_trades, "month"), "month"))
    console.print(trade_attribution_table("Trade Attribution by Exit Reason", attribution_rows(closed_trades, "exit_reason"), "exit_reason", include_average_hold=True))
    console.print(trade_detail_table("Worst 10 Trades", worst_trades(closed_trades, 10)))
    console.print(trade_detail_table("Best 10 Trades", best_trades(closed_trades, 10)))


def print_long_validation(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    validation_args = long_validation_args(args)
    summaries = candidate_v1_walk_forward_summaries(
        conn,
        days=validation_args.days,
        walk_forward_window_days=validation_args.walk_forward_window_days,
    )
    console = Console(width=160)
    print_walk_forward_summary_tables(console, summaries)
    console.print(long_validation_verdict_table(validation_args, summarize_walk_forward(summaries)))


def candidate_v1_walk_forward_summaries(
    conn: sqlite3.Connection,
    days: int | None = None,
    walk_forward_window_days: int | None = None,
) -> list[dict[str, Any]]:
    args = hold_tp_baseline_args()
    if days is not None:
        args.days = days
    if walk_forward_window_days is not None:
        args.walk_forward_window_days = walk_forward_window_days
    return run_walk_forward_summaries(conn, args, list(HOLD_TP_MARKETS))


def long_validation_args(args: argparse.Namespace) -> argparse.Namespace:
    validation_args = hold_tp_baseline_args()
    validation_args.days = args.days if getattr(args, "explicit_days", False) else 365
    validation_args.walk_forward_window_days = (
        args.walk_forward_window_days if getattr(args, "explicit_walk_forward_window_days", False) else 30
    )
    return validation_args


def print_walk_forward_summary_tables(console: Console, summaries: list[dict[str, Any]]) -> None:
    table = Table(title="Walk-Forward Backtest")
    table.add_column("window_start")
    table.add_column("window_end")
    table.add_column("trade_count", justify="right")
    table.add_column("return_pct", justify="right")
    table.add_column("total_fees_krw", justify="right")
    table.add_column("max_drawdown_pct", justify="right")

    for summary in summaries:
        table.add_row(
            summary["window_start"],
            summary["window_end"],
            str(summary["trade_count"]),
            format_float(summary["return_pct"]),
            format_float(summary["total_fees_krw"]),
            format_float(summary["max_drawdown_pct"]),
        )

    report_summary = summarize_walk_forward(summaries)
    summary_table = Table(title="Walk-Forward Summary")
    summary_table.add_column("average_return_pct", justify="right")
    summary_table.add_column("median_return_pct", justify="right")
    summary_table.add_column("positive_window_count", justify="right")
    summary_table.add_column("negative_window_count", justify="right")
    summary_table.add_column("worst_window_return_pct", justify="right")
    summary_table.add_column("best_window_return_pct", justify="right")
    summary_table.add_row(
        format_optional_float(report_summary["average_return_pct"]),
        format_optional_float(report_summary["median_return_pct"]),
        str(report_summary["positive_window_count"]),
        str(report_summary["negative_window_count"]),
        format_optional_float(report_summary["worst_window_return_pct"]),
        format_optional_float(report_summary["best_window_return_pct"]),
    )
    console.print(table)
    console.print(summary_table)


def long_validation_verdict_table(args: argparse.Namespace, summary: dict[str, Any]) -> Table:
    table = Table(title="Long-History Validation Verdict")
    table.add_column("days", justify="right")
    table.add_column("window_days", justify="right")
    table.add_column("positive_window_count", justify="right")
    table.add_column("negative_window_count", justify="right")
    table.add_column("average_return_pct", justify="right")
    table.add_column("median_return_pct", justify="right")
    table.add_column("worst_window_return_pct", justify="right")
    table.add_column("best_window_return_pct", justify="right")
    table.add_column("max_drawdown_pct", justify="right")
    table.add_column("trade_count", justify="right")
    table.add_row(
        str(args.days),
        str(args.walk_forward_window_days),
        str(summary["positive_window_count"]),
        str(summary["negative_window_count"]),
        format_optional_float(summary["average_return_pct"]),
        format_optional_float(summary["median_return_pct"]),
        format_optional_float(summary["worst_window_return_pct"]),
        format_optional_float(summary["best_window_return_pct"]),
        format_optional_float(summary["worst_max_drawdown_pct"]),
        str(summary["total_trade_count"]),
    )
    return table


def closed_trades_from_summaries(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        trade
        for summary in summaries
        for trade in summary.get("all_trades", [])
        if trade.get("side") == "SELL"
    ]


def attribution_rows(trades: list[dict[str, Any]], group_by: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        key = trade_attribution_key(trade, group_by)
        grouped.setdefault(key, []).append(trade)
    rows = [summarize_attribution_group(key, values, group_by) for key, values in grouped.items()]
    return sorted(rows, key=lambda row: row[group_by])


def trade_attribution_key(trade: dict[str, Any], group_by: str) -> str:
    if group_by == "market":
        return str(trade["market"])
    if group_by == "month":
        return str(trade["ts"])[:7]
    if group_by == "exit_reason":
        return str(trade["reason"])
    raise ValueError(f"Unsupported attribution group: {group_by}")


def summarize_attribution_group(key: str, trades: list[dict[str, Any]], group_by: str) -> dict[str, Any]:
    net_values = [float(trade.get("net_pnl_krw", 0.0)) for trade in trades]
    hold_values = [float(trade["hold_minutes"]) for trade in trades if trade.get("hold_minutes") is not None]
    trade_count = len(trades)
    return {
        group_by: key,
        "trade_count": trade_count,
        "win_count": sum(1 for value in net_values if value > 0),
        "loss_count": sum(1 for value in net_values if value < 0),
        "win_rate_pct": (sum(1 for value in net_values if value > 0) / trade_count * 100) if trade_count else None,
        "gross_pnl_krw": sum(float(trade.get("gross_pnl_krw", 0.0)) for trade in trades),
        "fees_krw": sum(float(trade.get("total_fee_krw", trade.get("fee_krw", 0.0))) for trade in trades),
        "net_pnl_krw": sum(net_values),
        "average_net_pnl_krw": mean(net_values) if net_values else None,
        "average_hold_minutes": mean(hold_values) if hold_values else None,
    }


def trade_attribution_table(
    title: str,
    rows: list[dict[str, Any]],
    group_column: str,
    include_average_hold: bool = False,
) -> Table:
    table = Table(title=title)
    table.add_column(group_column)
    table.add_column("trade_count", justify="right")
    table.add_column("win_count", justify="right")
    table.add_column("loss_count", justify="right")
    table.add_column("win_rate_pct", justify="right")
    table.add_column("gross_pnl_krw", justify="right")
    table.add_column("fees_krw", justify="right")
    table.add_column("net_pnl_krw", justify="right")
    if group_column == "market":
        table.add_column("average_net_pnl_krw", justify="right")
    if group_column == "market" or include_average_hold:
        table.add_column("average_hold_minutes", justify="right")
    for row in rows:
        values = [
            str(row[group_column]),
            str(row["trade_count"]),
            str(row["win_count"]),
            str(row["loss_count"]),
            format_optional_float(row["win_rate_pct"]),
            format_float(row["gross_pnl_krw"]),
            format_float(row["fees_krw"]),
            format_float(row["net_pnl_krw"]),
        ]
        if group_column == "market":
            values.append(format_optional_float(row["average_net_pnl_krw"]))
        if group_column == "market" or include_average_hold:
            values.append(format_optional_float(row["average_hold_minutes"]))
        table.add_row(*values)
    return table


def worst_trades(trades: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    return sorted(trades, key=lambda trade: float(trade.get("net_pnl_krw", 0.0)))[:limit]


def best_trades(trades: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    return sorted(trades, key=lambda trade: float(trade.get("net_pnl_krw", 0.0)), reverse=True)[:limit]


def trade_detail_table(title: str, trades: list[dict[str, Any]]) -> Table:
    table = Table(title=title)
    table.add_column("market")
    table.add_column("entry_ts")
    table.add_column("exit_ts")
    table.add_column("exit_reason")
    table.add_column("entry_price", justify="right")
    table.add_column("exit_price", justify="right")
    table.add_column("gross_pnl_krw", justify="right")
    table.add_column("fee_krw", justify="right")
    table.add_column("net_pnl_krw", justify="right")
    table.add_column("hold_minutes", justify="right")
    for trade in trades:
        table.add_row(
            str(trade["market"]),
            str(trade.get("entry_ts", "-")),
            str(trade["ts"]),
            str(trade["reason"]),
            format_float(float(trade.get("entry_price", 0.0))),
            format_float(float(trade["price"])),
            format_float(float(trade.get("gross_pnl_krw", 0.0))),
            format_float(float(trade.get("total_fee_krw", trade.get("fee_krw", 0.0)))),
            format_float(float(trade.get("net_pnl_krw", 0.0))),
            format_optional_float(trade.get("hold_minutes")),
        )
    return table


def fixed_universe_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    args = fixed_universe_args()
    windows = walk_forward_windows(args.days, args.walk_forward_window_days)
    single_market_inputs = fixed_universe_single_market_inputs(conn, args, windows)
    return sort_fixed_universe_rows([
        run_fixed_universe_summary_from_inputs(markets, single_market_inputs, args)
        for markets in fixed_universes()
    ])


def fixed_universes() -> list[list[str]]:
    universes: list[list[str]] = []
    for universe_size in range(FIXED_UNIVERSE_MIN_SIZE, FIXED_UNIVERSE_MAX_SIZE + 1):
        universes.extend([list(markets) for markets in combinations(FIXED_UNIVERSE_MARKETS, universe_size)])
    return universes


def run_fixed_universe_summary(conn: sqlite3.Connection, markets: list[str]) -> dict[str, Any]:
    return summarize_fixed_universe(markets, run_walk_forward_summaries(conn, fixed_universe_args(), markets))


def run_fixed_universe_summary_from_inputs(
    markets: list[str],
    single_market_inputs: dict[str, list[dict[str, Any]]],
    args: argparse.Namespace,
) -> dict[str, Any]:
    window_count = len(next(iter(single_market_inputs.values()), []))
    summaries = [
        run_hold_tp_window_summary(merge_fixed_universe_window_input(markets, single_market_inputs, window_index), args)
        for window_index in range(window_count)
    ]
    return summarize_fixed_universe(markets, summaries)


def fixed_universe_single_market_inputs(
    conn: sqlite3.Connection,
    args: argparse.Namespace,
    windows: list[tuple[datetime, datetime]],
) -> dict[str, list[dict[str, Any]]]:
    return {
        market: [
            build_hold_tp_window_input(conn, args, [market], window_start, window_end)
            for window_start, window_end in windows
        ]
        for market in FIXED_UNIVERSE_MARKETS
    }


def merge_fixed_universe_window_input(
    markets: list[str],
    single_market_inputs: dict[str, list[dict[str, Any]]],
    window_index: int,
) -> dict[str, Any]:
    selected_inputs = [single_market_inputs[market][window_index] for market in markets]
    events = [event for window_input in selected_inputs for event in window_input["events"]]
    events.sort(key=lambda event: (event["ts"], event["market"]))
    final_prices = {
        market: price
        for window_input in selected_inputs
        for market, price in window_input["final_prices"].items()
    }
    return {
        "window_start": selected_inputs[0]["window_start"],
        "window_end": selected_inputs[0]["window_end"],
        "markets": list(markets),
        "events": events,
        "final_prices": final_prices,
        "raw_signal_count": sum(int(window_input["raw_signal_count"]) for window_input in selected_inputs),
        "accepted_signal_count": sum(int(window_input["accepted_signal_count"]) for window_input in selected_inputs),
    }


def fixed_universe_args() -> argparse.Namespace:
    return hold_tp_baseline_args()


def summarize_fixed_universe(markets: list[str], summaries: list[dict[str, Any]]) -> dict[str, Any]:
    walk_forward_summary = summarize_walk_forward(summaries)
    return {
        "markets": markets,
        "market_count": len(markets),
        "average_return_pct": walk_forward_summary["average_return_pct"],
        "median_return_pct": walk_forward_summary["median_return_pct"],
        "positive_window_count": walk_forward_summary["positive_window_count"],
        "negative_window_count": walk_forward_summary["negative_window_count"],
        "worst_window_return_pct": walk_forward_summary["worst_window_return_pct"],
        "best_window_return_pct": walk_forward_summary["best_window_return_pct"],
        "max_drawdown_pct": walk_forward_summary["worst_max_drawdown_pct"],
        "trade_count": walk_forward_summary["total_trade_count"],
    }


def sort_fixed_universe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=fixed_universe_sort_key)


def fixed_universe_sort_key(row: dict[str, Any]) -> tuple[float, int, float]:
    average_return_pct = row["average_return_pct"]
    max_drawdown_pct = row["max_drawdown_pct"]
    return (
        -(float(average_return_pct) if average_return_pct is not None else float("-inf")),
        -int(row["positive_window_count"]),
        float(max_drawdown_pct) if max_drawdown_pct is not None else float("inf"),
    )


def fixed_universe_table(title: str, rows: list[dict[str, Any]]) -> Table:
    table = Table(title=title)
    table.add_column("markets")
    table.add_column("market_count", justify="right")
    table.add_column("average_return_pct", justify="right")
    table.add_column("median_return_pct", justify="right")
    table.add_column("positive_window_count", justify="right")
    table.add_column("negative_window_count", justify="right")
    table.add_column("worst_window_return_pct", justify="right")
    table.add_column("best_window_return_pct", justify="right")
    table.add_column("max_drawdown_pct", justify="right")
    table.add_column("trade_count", justify="right")

    for row in rows:
        table.add_row(
            ",".join(row["markets"]),
            str(row["market_count"]),
            format_optional_float(row["average_return_pct"]),
            format_optional_float(row["median_return_pct"]),
            str(row["positive_window_count"]),
            str(row["negative_window_count"]),
            format_optional_float(row["worst_window_return_pct"]),
            format_optional_float(row["best_window_return_pct"]),
            format_optional_float(row["max_drawdown_pct"]),
            str(row["trade_count"]),
        )
    return table


def print_market_subset_comparison(conn: sqlite3.Connection, args: argparse.Namespace, markets: list[str]) -> None:
    rows = sort_market_subset_rows([
        run_market_subset_summary(conn, args, subset)
        for subset in market_subsets(markets)
    ])

    table = Table(title="Market Subset Walk-Forward Comparison")
    table.add_column("markets")
    table.add_column("market_count", justify="right")
    table.add_column("trade_count", justify="right")
    table.add_column("return_pct", justify="right")
    table.add_column("median_window_return_pct", justify="right")
    table.add_column("positive_window_count", justify="right")
    table.add_column("negative_window_count", justify="right")
    table.add_column("max_drawdown_pct", justify="right")

    for row in rows:
        table.add_row(
            ",".join(row["markets"]),
            str(row["market_count"]),
            str(row["trade_count"]),
            format_optional_float(row["return_pct"]),
            format_optional_float(row["median_window_return_pct"]),
            str(row["positive_window_count"]),
            str(row["negative_window_count"]),
            format_optional_float(row["max_drawdown_pct"]),
        )

    Console(width=180).print(table)


def market_subsets(markets: list[str]) -> list[list[str]]:
    deduped_markets = dedupe(markets)
    subsets: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()

    def append_subset(values: list[str]) -> None:
        key = tuple(values)
        if key in seen:
            return
        subsets.append(values)
        seen.add(key)

    append_subset(deduped_markets)
    max_subset_size = min(4, len(deduped_markets))
    for subset_size in range(1, max_subset_size + 1):
        for subset in combinations(deduped_markets, subset_size):
            append_subset(list(subset))
    return subsets


def run_market_subset_summary(
    conn: sqlite3.Connection,
    args: argparse.Namespace,
    markets: list[str],
) -> dict[str, Any]:
    return summarize_market_subset(markets, run_walk_forward_summaries(conn, args, markets))


def summarize_market_subset(markets: list[str], summaries: list[dict[str, Any]]) -> dict[str, Any]:
    walk_forward_summary = summarize_walk_forward(summaries)
    return {
        "markets": markets,
        "market_count": len(markets),
        "trade_count": walk_forward_summary["total_trade_count"],
        "return_pct": walk_forward_summary["average_return_pct"],
        "median_window_return_pct": walk_forward_summary["median_return_pct"],
        "positive_window_count": walk_forward_summary["positive_window_count"],
        "negative_window_count": walk_forward_summary["negative_window_count"],
        "max_drawdown_pct": walk_forward_summary["worst_max_drawdown_pct"],
    }


def sort_market_subset_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=market_subset_sort_key)


def market_subset_sort_key(row: dict[str, Any]) -> tuple[float, int, float]:
    average_return_pct = row["return_pct"]
    max_drawdown_pct = row["max_drawdown_pct"]
    return (
        -(float(average_return_pct) if average_return_pct is not None else float("-inf")),
        -int(row["positive_window_count"]),
        float(max_drawdown_pct) if max_drawdown_pct is not None else float("inf"),
    )


def walk_forward_windows(days: int, window_days: int) -> list[tuple[datetime, datetime]]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    window_delta = timedelta(days=window_days)
    windows = []
    current_start = start
    while current_start < end:
        current_end = min(current_start + window_delta, end)
        windows.append((current_start, current_end))
        current_start = current_end
    return windows


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
    max_hold_hours: int | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> dict[str, Any]:
    validate_strategy_interval(strategy, interval)

    cash = START_CASH_KRW
    positions: dict[str, Position] = {}
    buy_count = 0
    sell_count = 0
    take_profit_count = 0
    stop_loss_count = 0
    signal_exit_count = 0
    forced_exit_count = 0
    total_fees_krw = 0.0
    realized_pnl_krw = 0.0
    trades: list[dict[str, Any]] = []
    hold_minutes: list[float] = []
    equity_curve: list[dict[str, Any]] = []
    latest_prices: dict[str, float] = {}

    candles_by_market = {
        market: load_candles(conn, market, interval, days, window_start=window_start, window_end=window_end)
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
    risk_controls_enabled = take_profit_pct > 0 or stop_loss_pct > 0 or max_hold_hours is not None
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
            ts=ts,
            take_profit_pct=take_profit_pct,
            stop_loss_pct=stop_loss_pct,
            max_hold_hours=max_hold_hours,
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
            elif risk_exit_reason == "MAX_HOLD":
                forced_exit_count += 1
            hold_minutes.append(position_hold_minutes(position, ts))
            trades.append(trade)
        elif signal == "BUY" and market not in positions:
            total_cost = trade_notional_krw * (1 + fee_rate)
            if cash >= total_cost:
                quantity = trade_notional_krw / price
                fee_krw = trade_notional_krw * fee_rate
                cash -= total_cost
                positions[market] = Position(
                    quantity=quantity,
                    average_entry_price=price,
                    entry_ts=ts,
                    entry_fee_krw=fee_krw,
                )
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

    final_prices = final_market_prices(conn, markets, interval, days, window_start=window_start, window_end=window_end)
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
        "max_hold_hours": max_hold_hours,
        "rsi_buy_threshold": rsi_buy_threshold,
        "rsi_sell_threshold": rsi_sell_threshold,
        "window_start": format_utc(window_start) if window_start is not None else None,
        "window_end": format_utc(window_end) if window_end is not None else None,
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
        "forced_exit_count": forced_exit_count,
        "total_fees_krw": total_fees_krw,
        "realized_pnl_krw": realized_pnl_krw,
        "max_drawdown_pct": max_drawdown_pct(equity_curve),
        "average_hold_minutes": average_hold_minutes(hold_minutes),
        "trades": trades[-20:],
        "all_trades": trades,
    }


def load_candles(
    conn: sqlite3.Connection,
    market: str,
    interval: str,
    days: int,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> list[Candle]:
    cutoff = window_start or (datetime.now(timezone.utc) - timedelta(days=days))
    params: list[Any] = [market, interval, format_utc(cutoff)]
    upper_bound_sql = ""
    if window_end is not None:
        upper_bound_sql = "AND candle_date_time_utc < ?"
        params.append(format_utc(window_end))
    price_columns = candle_price_columns(conn)
    rows = conn.execute(
        f"""
        SELECT {price_columns}
        FROM candles
        WHERE market = ?
          AND interval = ?
          AND candle_date_time_utc >= ?
          {upper_bound_sql}
          AND trade_price IS NOT NULL
        ORDER BY candle_date_time_utc ASC, id ASC
        """,
        params,
    ).fetchall()

    candles = []
    seen_timestamps = set()
    for row in rows:
        ts = row["candle_date_time_utc"]
        if ts in seen_timestamps:
            continue
        seen_timestamps.add(ts)
        candles.append(Candle(
            market=market,
            ts=ts,
            price=float(row["trade_price"]),
            open=float(row["opening_price"] if row["opening_price"] is not None else row["trade_price"]),
            high=float(row["high_price"] if row["high_price"] is not None else row["trade_price"]),
            low=float(row["low_price"] if row["low_price"] is not None else row["trade_price"]),
            volume=float(row["candle_acc_trade_volume"]) if row["candle_acc_trade_volume"] is not None else None,
        ))
    return candles


def candle_price_columns(conn: sqlite3.Connection) -> str:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(candles)").fetchall()}
    open_sql = "opening_price" if "opening_price" in columns else "trade_price AS opening_price"
    volume_sql = "candle_acc_trade_volume" if "candle_acc_trade_volume" in columns else "NULL AS candle_acc_trade_volume"
    if {"high_price", "low_price"}.issubset(columns):
        return f"candle_date_time_utc, trade_price, {open_sql}, high_price, low_price, {volume_sql}"
    return f"candle_date_time_utc, trade_price, {open_sql}, trade_price AS high_price, trade_price AS low_price, {volume_sql}"


def validate_strategy_interval(strategy: str, interval: str) -> None:
    if strategy == "bollinger_rsi_and_mtf" and interval != "1m":
        raise ValueError("bollinger_rsi_and_mtf requires --interval 1m")
    if strategy == "macd_ema_filter_15m" and interval != "1m":
        raise ValueError("macd_ema_filter_15m requires --interval 1m")


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
    if strategy == "ema_volume_spike_2x":
        return ema_volume_spike_signals(candles, volume_multiplier=2.0)
    if strategy == "ema_volume_spike_3x":
        return ema_volume_spike_signals(candles, volume_multiplier=3.0)
    if strategy == "bollinger":
        return bollinger_signals(candles, period=bollinger_period, stddev=bollinger_stddev)
    if strategy == "rsi":
        return rsi_signals(candles, buy_threshold=rsi_buy_threshold, sell_threshold=rsi_sell_threshold)
    if strategy == "ema_rsi":
        return ema_rsi_signals(candles)
    if strategy == "donchian":
        return donchian_signals(candles)
    if strategy == "donchian_5m":
        return donchian_signals(derive_timeframe_candles(candles, 5))
    if strategy == "donchian_15m":
        return donchian_signals(derive_timeframe_candles(candles, 15))
    if strategy == "ema_trend_5m":
        return ema_trend_signals(derive_timeframe_candles(candles, 5))
    if strategy == "ema_trend_15m":
        return ema_trend_signals(derive_timeframe_candles(candles, 15))
    if strategy == "ichimoku":
        return ichimoku_signals(candles)
    if strategy == "ichimoku_strict_15m":
        return ichimoku_strict_signals(derive_timeframe_candles(candles, 15))
    if strategy == "macd_ema_filter_15m":
        return macd_ema_filter_signals(derive_timeframe_candles(candles, 15))
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
    if strategy == "bollinger_rsi_and_mtf":
        return bollinger_rsi_mtf_signals(
            candles,
            period=bollinger_period,
            stddev=bollinger_stddev,
            rsi_buy_threshold=rsi_buy_threshold,
            rsi_sell_threshold=rsi_sell_threshold,
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


def ema_volume_spike_signals(candles: list[Candle], volume_multiplier: float) -> list[dict[str, Any]]:
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
            if has_volume_spike(candles, index, VOLUME_SPIKE_LOOKBACK, volume_multiplier):
                signals.append(signal_event(candles[index], "BUY"))
        elif previous_fast >= previous_slow and current_fast < current_slow:
            signals.append(signal_event(candles[index], "SELL"))
    return signals


def has_volume_spike(
    candles: list[Candle],
    index: int,
    lookback: int,
    multiplier: float,
) -> bool:
    current_volume = candles[index].volume
    if current_volume is None or current_volume <= 0 or index < lookback:
        return False
    prior_volumes = [
        candle.volume
        for candle in candles[index - lookback:index]
        if candle.volume is not None and candle.volume > 0
    ]
    if len(prior_volumes) < lookback:
        return False
    return current_volume >= mean(prior_volumes) * multiplier


def bollinger_squeeze_volume_signals(
    candles: list[Candle],
    volume_spike_multiplier: float,
    max_recent_pump_pct: float,
) -> list[dict[str, Any]]:
    prices = [candle.price for candle in candles]
    middle, upper, lower = bollinger_band_series(
        prices,
        period=BOLLINGER_SQUEEZE_PERIOD,
        stddev=BOLLINGER_SQUEEZE_STDDEV,
    )
    bandwidth = bollinger_bandwidth_pct_series(middle, upper, lower)
    ema = ema_series(prices, BOLLINGER_SQUEEZE_EMA_PERIOD)
    signals = []

    for index, candle in enumerate(candles):
        current_middle = middle[index]
        current_upper = upper[index]
        current_bandwidth = bandwidth[index]
        current_ema = ema[index]
        if None in (current_middle, current_upper, current_bandwidth, current_ema):
            continue

        if candle.price < current_ema:
            signals.append(signal_event(candle, "SELL"))
            continue

        if not has_low_bandwidth_percentile(
            bandwidth,
            index,
            lookback=BOLLINGER_SQUEEZE_BANDWIDTH_LOOKBACK,
            percentile=BOLLINGER_SQUEEZE_BANDWIDTH_PERCENTILE,
        ):
            continue
        if not ema_slope_positive(ema, index, BOLLINGER_SQUEEZE_EMA_SLOPE_LOOKBACK):
            continue
        if not has_volume_ma_spike(candles, index, BOLLINGER_SQUEEZE_VOLUME_MA_PERIOD, volume_spike_multiplier):
            continue
        if recent_return_pct(prices, index, BOLLINGER_SQUEEZE_RECENT_PUMP_LOOKBACK) > max_recent_pump_pct:
            continue
        if candle.price > current_upper:
            signals.append(signal_event(candle, "BUY"))
    return signals


def bollinger_band_series(
    values: list[float],
    period: int,
    stddev: float,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    middle: list[float | None] = [None] * len(values)
    upper: list[float | None] = [None] * len(values)
    lower: list[float | None] = [None] * len(values)
    if len(values) < period:
        return middle, upper, lower
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        current_middle = mean(window)
        current_stddev = pstdev(window)
        middle[index] = current_middle
        upper[index] = current_middle + stddev * current_stddev
        lower[index] = current_middle - stddev * current_stddev
    return middle, upper, lower


def bollinger_bandwidth_pct_series(
    middle: list[float | None],
    upper: list[float | None],
    lower: list[float | None],
) -> list[float | None]:
    values: list[float | None] = []
    for current_middle, current_upper, current_lower in zip(middle, upper, lower):
        if current_middle is None or current_upper is None or current_lower is None or current_middle == 0:
            values.append(None)
        else:
            values.append((current_upper - current_lower) / current_middle * 100)
    return values


def has_low_bandwidth_percentile(
    bandwidth: list[float | None],
    index: int,
    lookback: int,
    percentile: float,
) -> bool:
    if index < lookback or bandwidth[index] is None:
        return False
    prior_values = [
        value
        for value in bandwidth[index - lookback:index]
        if value is not None
    ]
    if len(prior_values) < lookback:
        return False
    return bandwidth[index] <= percentile_value(prior_values, percentile)


def percentile_value(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("values cannot be empty")
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (percentile / 100) * (len(sorted_values) - 1)
    lower_index = int(rank)
    upper_index = min(lower_index + 1, len(sorted_values) - 1)
    fraction = rank - lower_index
    return sorted_values[lower_index] + (sorted_values[upper_index] - sorted_values[lower_index]) * fraction


def ema_slope_positive(ema: list[float | None], index: int, lookback: int) -> bool:
    if index < lookback or ema[index] is None or ema[index - lookback] is None:
        return False
    return ema[index] > ema[index - lookback]


def has_volume_ma_spike(
    candles: list[Candle],
    index: int,
    lookback: int,
    multiplier: float,
) -> bool:
    current_volume = candles[index].volume
    if current_volume is None or current_volume <= 0 or index < lookback:
        return False
    prior_volumes = [
        candle.volume
        for candle in candles[index - lookback:index]
        if candle.volume is not None and candle.volume > 0
    ]
    if len(prior_volumes) < lookback:
        return False
    return current_volume >= mean(prior_volumes) * multiplier


def recent_return_pct(values: list[float], index: int, lookback: int) -> float:
    if index < lookback:
        return float("inf")
    previous = values[index - lookback]
    if previous == 0:
        return float("inf")
    return (values[index] - previous) / previous * 100


def ema_volume_expansion_signals(
    candles: list[Candle],
    btc_candles: list[Candle],
    volume_spike_multiplier: float,
    btc_filter_pct: float,
) -> list[dict[str, Any]]:
    prices = [candle.price for candle in candles]
    ema_fast = ema_series(prices, EMA_VOLUME_EXPANSION_FAST)
    ema_slow = ema_series(prices, EMA_VOLUME_EXPANSION_SLOW)
    ema_slope = ema_series(prices, EMA_VOLUME_EXPANSION_SLOPE_EMA)
    btc_filter = aligned_btc_return_filter(candles, btc_candles, btc_filter_pct)
    signals = []
    for index, candle in enumerate(candles):
        if index == 0:
            continue
        previous_fast = ema_fast[index - 1]
        previous_slow = ema_slow[index - 1]
        current_fast = ema_fast[index]
        current_slow = ema_slow[index]
        if None in (previous_fast, previous_slow, current_fast, current_slow):
            continue
        if previous_fast >= previous_slow and current_fast < current_slow:
            signals.append(signal_event(candle, "SELL"))
            continue
        if not (previous_fast <= previous_slow and current_fast > current_slow):
            continue
        if not ema_slope_positive(ema_slope, index, EMA_VOLUME_EXPANSION_SLOPE_LOOKBACK):
            continue
        if not has_bullish_volume_expansion(candles, index, volume_spike_multiplier):
            continue
        if not btc_filter.get(candle.ts, False):
            continue
        signals.append(signal_event(candle, "BUY"))
    return signals


def has_bullish_volume_expansion(
    candles: list[Candle],
    index: int,
    volume_spike_multiplier: float,
) -> bool:
    candle = candles[index]
    if candle.price <= candle.open_price:
        return False
    if not closes_near_high(candle, EMA_VOLUME_EXPANSION_CLOSE_LOCATION_MIN):
        return False
    return has_volume_ma_spike(
        candles,
        index,
        EMA_VOLUME_EXPANSION_VOLUME_MA_PERIOD,
        volume_spike_multiplier,
    )


def closes_near_high(candle: Candle, minimum_location: float) -> bool:
    epsilon = 1e-12
    range_size = max(candle.high_price - candle.low_price, epsilon)
    close_location = (candle.price - candle.low_price) / range_size
    return close_location >= minimum_location


def aligned_btc_return_filter(
    target_candles: list[Candle],
    btc_candles: list[Candle],
    min_return_pct: float,
) -> dict[str, bool]:
    if not target_candles or not btc_candles:
        return {}
    btc_points = [
        (parse_utc_datetime(candle.ts), candle.price)
        for candle in btc_candles
    ]
    btc_points.sort(key=lambda point: point[0])
    filters: dict[str, bool] = {}
    latest_index = -1
    reference_index = -1
    for candle in target_candles:
        candle_ts = parse_utc_datetime(candle.ts)
        reference_cutoff = candle_ts - timedelta(hours=EMA_VOLUME_EXPANSION_BTC_LOOKBACK_HOURS)
        while latest_index + 1 < len(btc_points) and btc_points[latest_index + 1][0] <= candle_ts:
            latest_index += 1
        while reference_index + 1 < len(btc_points) and btc_points[reference_index + 1][0] <= reference_cutoff:
            reference_index += 1
        if latest_index < 0 or reference_index < 0:
            filters[candle.ts] = False
            continue
        latest_price = btc_points[latest_index][1]
        reference_price = btc_points[reference_index][1]
        if reference_price <= 0:
            filters[candle.ts] = False
            continue
        return_pct = (latest_price - reference_price) / reference_price * 100
        filters[candle.ts] = return_pct >= min_return_pct
    return filters


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


def bollinger_rsi_mtf_signals(
    candles: list[Candle],
    period: int,
    stddev: float,
    rsi_buy_threshold: float,
    rsi_sell_threshold: float,
) -> list[dict[str, Any]]:
    prices = [candle.price for candle in candles]
    rsi = rsi_series(prices, RSI_PERIOD)
    mtf_trend = aligned_mtf_trend(candles)
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
        buy_signal = bollinger_buy and rsi_buy and mtf_trend.get(candles[index].ts, False)
        if buy_signal:
            signals.append(signal_event(candles[index], "BUY"))
        elif bollinger_sell or rsi_sell:
            signals.append(signal_event(candles[index], "SELL"))
    return signals


def aligned_mtf_trend(candles: list[Candle]) -> dict[str, bool]:
    five_minute_candles = derive_five_minute_candles(candles)
    if not five_minute_candles:
        return {}

    prices = [candle.price for candle in five_minute_candles]
    ema_fast = ema_series(prices, EMA_FAST)
    ema_slow = ema_series(prices, EMA_SLOW)
    states = []
    for index, candle in enumerate(five_minute_candles):
        current_fast = ema_fast[index]
        current_slow = ema_slow[index]
        if None in (current_fast, current_slow):
            continue
        states.append((parse_utc_datetime(candle.ts), current_fast > current_slow))

    aligned = {}
    state_index = 0
    latest_state: bool | None = None
    for candle in candles:
        candle_ts = parse_utc_datetime(candle.ts)
        while state_index < len(states) and states[state_index][0] <= candle_ts:
            latest_state = states[state_index][1]
            state_index += 1
        if latest_state is not None:
            aligned[candle.ts] = latest_state
    return aligned


def derive_five_minute_candles(candles: list[Candle]) -> list[Candle]:
    return derive_timeframe_candles(candles, 5)


def derive_timeframe_candles(candles: list[Candle], timeframe_minutes: int) -> list[Candle]:
    grouped: dict[datetime, list[Candle]] = {}
    for candle in candles:
        bucket_ts = floor_to_timeframe(parse_utc_datetime(candle.ts), timeframe_minutes)
        grouped.setdefault(bucket_ts, []).append(candle)
    derived = []
    for bucket_ts in sorted(grouped):
        bucket = grouped[bucket_ts]
        last_candle = bucket[-1]
        first_candle = bucket[0]
        volumes = [candle.volume for candle in bucket if candle.volume is not None]
        derived.append(Candle(
            market=last_candle.market,
            ts=last_candle.ts,
            price=last_candle.price,
            open=first_candle.open_price,
            high=max(candle.high_price for candle in bucket),
            low=min(candle.low_price for candle in bucket),
            volume=sum(volumes) if volumes else None,
        ))
    return derived


def floor_to_timeframe(value: datetime, timeframe_minutes: int) -> datetime:
    value = value.astimezone(timezone.utc)
    floored_minute = value.minute - (value.minute % timeframe_minutes)
    return value.replace(minute=floored_minute, second=0, microsecond=0)


def floor_to_five_minutes(value: datetime) -> datetime:
    return floor_to_timeframe(value, 5)


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


def ema_trend_signals(candles: list[Candle]) -> list[dict[str, Any]]:
    prices = [candle.price for candle in candles]
    ema_fast = ema_series(prices, EMA_FAST)
    ema_slow = ema_series(prices, EMA_SLOW)
    signals = []
    in_position = False
    for index, candle in enumerate(candles):
        current_fast = ema_fast[index]
        current_slow = ema_slow[index]
        if None in (current_fast, current_slow):
            continue
        entry_signal = current_fast > current_slow and candle.price > current_fast
        exit_signal = current_fast < current_slow
        if not in_position and entry_signal:
            signals.append(signal_event(candle, "BUY"))
            in_position = True
        elif in_position and exit_signal:
            signals.append(signal_event(candle, "SELL"))
            in_position = False
    return signals


def ichimoku_signals(candles: list[Candle]) -> list[dict[str, Any]]:
    tenkan = ichimoku_midpoint_series(candles, ICHIMOKU_TENKAN)
    kijun = ichimoku_midpoint_series(candles, ICHIMOKU_KIJUN)
    senkou_b = ichimoku_midpoint_series(candles, ICHIMOKU_SENKOU_B)
    signals = []
    in_position = False
    for index, candle in enumerate(candles):
        current_tenkan = tenkan[index]
        current_kijun = kijun[index]
        current_senkou_b = senkou_b[index]
        if None in (current_tenkan, current_kijun, current_senkou_b):
            continue
        senkou_a = (current_tenkan + current_kijun) / 2
        cloud_top = max(senkou_a, current_senkou_b)
        cloud_bullish = senkou_a > current_senkou_b
        long_entry = candle.price > cloud_top and current_tenkan > current_kijun and cloud_bullish
        tenkan_cross_below = False
        if index > 0 and tenkan[index - 1] is not None and kijun[index - 1] is not None:
            tenkan_cross_below = tenkan[index - 1] >= kijun[index - 1] and current_tenkan < current_kijun
        exit_signal = tenkan_cross_below or candle.price < current_kijun
        if not in_position and long_entry:
            signals.append(signal_event(candle, "BUY"))
            in_position = True
        elif in_position and exit_signal:
            signals.append(signal_event(candle, "SELL"))
            in_position = False
    return signals


def ichimoku_strict_signals(candles: list[Candle]) -> list[dict[str, Any]]:
    tenkan = ichimoku_midpoint_series(candles, ICHIMOKU_TENKAN)
    kijun = ichimoku_midpoint_series(candles, ICHIMOKU_KIJUN)
    senkou_b = ichimoku_midpoint_series(candles, ICHIMOKU_SENKOU_B)
    signals = []
    in_position = False
    for index, candle in enumerate(candles):
        current_tenkan = tenkan[index]
        current_kijun = kijun[index]
        current_senkou_b = senkou_b[index]
        if None in (current_tenkan, current_kijun, current_senkou_b):
            continue
        senkou_a = (current_tenkan + current_kijun) / 2
        cloud_top = max(senkou_a, current_senkou_b)
        cloud_bottom = min(senkou_a, current_senkou_b)
        cloud_bullish = senkou_a > current_senkou_b
        cloud_thickness_pct = 0.0
        if cloud_bottom > 0:
            cloud_thickness_pct = (cloud_top - cloud_bottom) / cloud_bottom * 100
        distance_above_cloud_pct = 0.0
        if cloud_top > 0:
            distance_above_cloud_pct = (candle.price - cloud_top) / cloud_top * 100
        long_entry = (
            candle.price > cloud_top
            and current_tenkan > current_kijun
            and cloud_bullish
            and candle.price > current_kijun
            and cloud_thickness_pct >= ICHIMOKU_STRICT_CLOUD_THICKNESS_PCT
            and distance_above_cloud_pct <= ICHIMOKU_STRICT_MAX_DISTANCE_ABOVE_CLOUD_PCT
        )
        tenkan_cross_below = False
        if index > 0 and tenkan[index - 1] is not None and kijun[index - 1] is not None:
            tenkan_cross_below = tenkan[index - 1] >= kijun[index - 1] and current_tenkan < current_kijun
        exit_signal = tenkan_cross_below or candle.price < current_kijun
        if not in_position and long_entry:
            signals.append(signal_event(candle, "BUY"))
            in_position = True
        elif in_position and exit_signal:
            signals.append(signal_event(candle, "SELL"))
            in_position = False
    return signals


def macd_ema_filter_signals(candles: list[Candle]) -> list[dict[str, Any]]:
    prices = [candle.price for candle in candles]
    ema_fast = ema_series(prices, MACD_TREND_EMA_FAST)
    ema_slow = ema_series(prices, MACD_TREND_EMA_SLOW)
    macd, signal = macd_signal_series(prices)
    signals = []
    in_position = False
    for index, candle in enumerate(candles):
        current_fast = ema_fast[index]
        current_slow = ema_slow[index]
        current_macd = macd[index]
        current_signal = signal[index]
        if None in (current_fast, current_slow, current_macd, current_signal):
            continue
        previous_macd = macd[index - 1] if index > 0 else None
        previous_signal = signal[index - 1] if index > 0 else None
        if previous_macd is None or previous_signal is None:
            continue

        trend_filter = current_fast > current_slow
        macd_cross_above = previous_macd <= previous_signal and current_macd > current_signal
        macd_cross_below = previous_macd >= previous_signal and current_macd < current_signal
        if not in_position and trend_filter and macd_cross_above and current_macd > 0:
            signals.append(signal_event(candle, "BUY"))
            in_position = True
        elif in_position and macd_cross_below:
            signals.append(signal_event(candle, "SELL"))
            in_position = False
    return signals


def macd_signal_series(values: list[float]) -> tuple[list[float | None], list[float | None]]:
    ema_fast = ema_series(values, MACD_FAST)
    ema_slow = ema_series(values, MACD_SLOW)
    macd = [
        fast - slow if fast is not None and slow is not None else None
        for fast, slow in zip(ema_fast, ema_slow)
    ]
    return macd, optional_ema_series(macd, MACD_SIGNAL)


def ichimoku_midpoint_series(candles: list[Candle], period: int) -> list[float | None]:
    series: list[float | None] = [None] * len(candles)
    if len(candles) < period:
        return series
    for index in range(period - 1, len(candles)):
        window = candles[index - period + 1:index + 1]
        highest_high = max(candle.high_price for candle in window)
        lowest_low = min(candle.low_price for candle in window)
        series[index] = (highest_high + lowest_low) / 2
    return series


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


def optional_ema_series(values: list[float | None], period: int) -> list[float | None]:
    series: list[float | None] = [None] * len(values)
    multiplier = 2 / (period + 1)
    current: float | None = None
    seed_values: list[float] = []
    for index, value in enumerate(values):
        if value is None:
            continue
        if current is None:
            seed_values.append(value)
            if len(seed_values) == period:
                current = mean(seed_values)
                series[index] = current
            continue
        current = (value - current) * multiplier + current
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


def final_market_prices(
    conn: sqlite3.Connection,
    markets: list[str],
    interval: str,
    days: int,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> dict[str, float]:
    prices = {}
    for market in markets:
        candles = load_candles(conn, market, interval, days, window_start=window_start, window_end=window_end)
        if candles:
            prices[market] = candles[-1].price
    return prices


def risk_exit_for_position(
    position: Position | None,
    price: float,
    ts: str,
    take_profit_pct: float,
    stop_loss_pct: float,
    max_hold_hours: int | None = None,
) -> str | None:
    if position is None:
        return None
    if take_profit_pct > 0 and price >= position.average_entry_price * (1 + take_profit_pct / 100):
        return "TAKE_PROFIT"
    if stop_loss_pct > 0 and price <= position.average_entry_price * (1 - stop_loss_pct / 100):
        return "STOP_LOSS"
    if max_hold_hours is not None and position_hold_minutes(position, ts) / 60 > max_hold_hours:
        return "MAX_HOLD"
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
    cost_basis = position.quantity * position.average_entry_price
    gross_pnl_krw = notional - cost_basis
    total_fee_krw = position.entry_fee_krw + fee_krw
    net_pnl_krw = gross_pnl_krw - total_fee_krw
    realized_delta = notional - fee_krw - cost_basis
    trade = simulated_trade(ts, "SELL", market, price, position.quantity, notional, fee_krw, reason)
    trade.update({
        "entry_ts": position.entry_ts,
        "entry_price": position.average_entry_price,
        "entry_fee_krw": position.entry_fee_krw,
        "exit_fee_krw": fee_krw,
        "total_fee_krw": total_fee_krw,
        "gross_pnl_krw": gross_pnl_krw,
        "net_pnl_krw": net_pnl_krw,
        "hold_minutes": position_hold_minutes(position, ts),
    })
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
