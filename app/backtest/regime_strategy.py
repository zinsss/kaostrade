from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from app.collector.collect_once import CONFIG_PATH, DEFAULT_DB_PATH, load_config
from app.regime.classifiers import CLASSIFIER_NAMES, ClassifierNotApplicable, get_classifier

START_CASH_KRW = 1_000_000.0
DEFAULT_ENTRY_STREAK = 3
DEFAULT_EXIT_STREAK = 3
DEFAULT_TRADE_NOTIONAL_KRW = 10_000.0
DEFAULT_FEE_RATE = 0.0005
DEFAULT_MAX_PRICE_DISTANCE_SECONDS = 300
DEFAULT_SOURCE = "backfill"
DEFAULT_REGIME_CLASSIFIER = "basic"
MARKETS = ("KRW-BTC", "KRW-ETH")


@dataclass
class BacktestSettings:
    mode: str = "persistent"
    entry_streak: int = DEFAULT_ENTRY_STREAK
    exit_streak: int = DEFAULT_EXIT_STREAK
    trade_notional_krw: float = DEFAULT_TRADE_NOTIONAL_KRW
    fee_rate: float = DEFAULT_FEE_RATE
    max_price_distance_seconds: int = DEFAULT_MAX_PRICE_DISTANCE_SECONDS
    source: str = DEFAULT_SOURCE
    regime_classifier: str = DEFAULT_REGIME_CLASSIFIER

    def to_json(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "entry_streak": self.entry_streak,
            "exit_streak": self.exit_streak,
            "trade_notional_krw": self.trade_notional_krw,
            "fee_rate": self.fee_rate,
            "max_price_distance_seconds": self.max_price_distance_seconds,
            "source": self.source,
            "regime_classifier": self.regime_classifier,
        }


@dataclass
class Position:
    quantity: float
    average_entry_price: float
    entry_ts: str


def main() -> None:
    args = parse_args()
    config = load_config(CONFIG_PATH)
    db_path = config.get("database", {}).get("path", DEFAULT_DB_PATH)

    with connect_read_only(db_path) as conn:
        if args.compare_classifiers:
            print_classifier_comparison(conn, args)
            return
        if args.compare:
            print_comparison(conn, args)
            return
        summary = run_backtest(conn, settings_from_args(args))

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest the regime strategy.")
    parser.add_argument("--mode", choices=("simple", "persistent"), default="persistent")
    parser.add_argument("--entry-streak", type=positive_int, default=DEFAULT_ENTRY_STREAK)
    parser.add_argument("--exit-streak", type=positive_int, default=DEFAULT_EXIT_STREAK)
    parser.add_argument("--trade-notional-krw", type=positive_float, default=DEFAULT_TRADE_NOTIONAL_KRW)
    parser.add_argument("--fee-rate", type=non_negative_float, default=DEFAULT_FEE_RATE)
    parser.add_argument("--max-price-distance-seconds", type=positive_int, default=DEFAULT_MAX_PRICE_DISTANCE_SECONDS)
    parser.add_argument("--source", choices=("backfill", "live", "all"), default=DEFAULT_SOURCE)
    parser.add_argument("--regime-classifier", choices=CLASSIFIER_NAMES, default=DEFAULT_REGIME_CLASSIFIER)
    parser.add_argument("--compare", action="store_true")
    parser.add_argument("--compare-classifiers", action="store_true")
    return parser.parse_args()


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
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


def settings_from_args(args: argparse.Namespace) -> BacktestSettings:
    return BacktestSettings(
        mode=args.mode,
        entry_streak=args.entry_streak,
        exit_streak=args.exit_streak,
        trade_notional_krw=args.trade_notional_krw,
        fee_rate=args.fee_rate,
        max_price_distance_seconds=args.max_price_distance_seconds,
        source=args.source,
        regime_classifier=args.regime_classifier,
    )


def print_classifier_comparison(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    table = Table(title="Regime Classifier Comparison")
    table.add_column("classifier")
    table.add_column("trade_count", justify="right")
    table.add_column("usable_feature_count", justify="right")
    table.add_column("skipped_feature_count", justify="right")
    table.add_column("return_pct", justify="right")
    table.add_column("total_fees_krw", justify="right")
    table.add_column("average_hold_minutes", justify="right")
    table.add_column("max_drawdown_pct", justify="right")

    for classifier_name in CLASSIFIER_NAMES:
        settings = BacktestSettings(
            mode=args.mode,
            entry_streak=args.entry_streak,
            exit_streak=args.exit_streak,
            trade_notional_krw=args.trade_notional_krw,
            fee_rate=args.fee_rate,
            max_price_distance_seconds=args.max_price_distance_seconds,
            source=args.source,
            regime_classifier=classifier_name,
        )
        summary = run_backtest(conn, settings)
        table.add_row(
            classifier_name,
            str(summary["trade_count"]),
            str(summary["usable_feature_count"]),
            str(summary["skipped_feature_count"]),
            format_float(summary["return_pct"]),
            format_float(summary["total_fees_krw"]),
            format_optional_float(summary["average_hold_minutes"]),
            format_float(summary["max_drawdown_pct"]),
        )

    Console(width=140).print(table)


def print_comparison(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    table = Table(title="Regime Backtest Comparison")
    table.add_column("mode")
    table.add_column("entry_streak", justify="right")
    table.add_column("exit_streak", justify="right")
    table.add_column("trade_count", justify="right")
    table.add_column("return_pct", justify="right")
    table.add_column("total_fees_krw", justify="right")
    table.add_column("average_hold_minutes", justify="right")
    table.add_column("max_drawdown_pct", justify="right")

    for streak in (3, 5, 7):
        settings = BacktestSettings(
            mode="persistent",
            entry_streak=streak,
            exit_streak=streak,
            trade_notional_krw=args.trade_notional_krw,
            fee_rate=args.fee_rate,
            max_price_distance_seconds=args.max_price_distance_seconds,
            source=args.source,
            regime_classifier=args.regime_classifier,
        )
        summary = run_backtest(conn, settings)
        table.add_row(
            settings.mode,
            str(settings.entry_streak),
            str(settings.exit_streak),
            str(summary["trade_count"]),
            format_float(summary["return_pct"]),
            format_float(summary["total_fees_krw"]),
            format_optional_float(summary["average_hold_minutes"]),
            format_float(summary["max_drawdown_pct"]),
        )

    Console(width=140).print(table)


def format_float(value: float) -> str:
    return f"{value:,.6f}"


def format_optional_float(value: float | None) -> str:
    if value is None:
        return "-"
    return format_float(value)


def connect_read_only(db_path: str) -> sqlite3.Connection:
    if not Path(db_path).exists():
        raise FileNotFoundError(f"SQLite database not found: {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def run_backtest(conn: sqlite3.Connection, settings: BacktestSettings | None = None, mode: str | None = None) -> dict[str, Any]:
    if settings is None:
        settings = BacktestSettings(mode=mode or "persistent")
    cash = START_CASH_KRW
    positions: dict[str, Position] = {}
    buy_count = 0
    sell_count = 0
    realized_pnl_krw = 0.0
    total_fees_krw = 0.0
    trades: list[dict[str, Any]] = []
    hold_minutes: list[float] = []
    equity_curve: list[dict[str, Any]] = []
    skipped: list[str] = []
    regime_counts = {"RISK_ON": 0, "NEUTRAL": 0, "RISK_OFF": 0}
    risk_on_streak = 0
    risk_off_streak = 0

    classification = load_classified_regimes(conn, settings)
    regimes = classification["regimes"]
    for regime_row in regimes:
        ts = str(regime_row["ts"])
        regime = str(regime_row["regime"])
        if regime in regime_counts:
            regime_counts[regime] += 1
        risk_on_streak, risk_off_streak = update_regime_streaks(regime, risk_on_streak, risk_off_streak)
        action_regime = action_regime_for_mode(settings, regime, risk_on_streak, risk_off_streak)
        price_result = prices_near_timestamp(conn, ts, MARKETS, settings)
        prices = price_result["prices"]
        skipped.extend(f"{ts}: {reason}" for reason in price_result["skipped"])
        missing_prices = [market for market in MARKETS if market not in prices]
        if missing_prices:
            skipped.append(f"{ts}: missing prices for {', '.join(missing_prices)}")

        if action_regime == "RISK_ON":
            for market in MARKETS:
                if market in positions:
                    continue
                price = prices.get(market)
                if price is None:
                    continue
                total_cost = settings.trade_notional_krw * (1 + settings.fee_rate)
                if cash < total_cost:
                    skipped.append(f"{ts}: insufficient cash to buy {market}")
                    continue
                quantity = settings.trade_notional_krw / price
                fee_krw = settings.trade_notional_krw * settings.fee_rate
                cash -= total_cost
                positions[market] = Position(quantity=quantity, average_entry_price=price, entry_ts=ts)
                total_fees_krw += fee_krw
                buy_count += 1
                trades.append(
                    simulated_trade(
                        ts=ts,
                        side="BUY",
                        market=market,
                        price=price,
                        quantity=quantity,
                        notional_krw=settings.trade_notional_krw,
                        fee_krw=fee_krw,
                    )
                )
        elif action_regime == "RISK_OFF":
            for market in list(positions):
                price = prices.get(market)
                if price is None:
                    continue
                position = positions.pop(market)
                notional = position.quantity * price
                fee_krw = notional * settings.fee_rate
                cash += notional - fee_krw
                realized_pnl_krw += notional - fee_krw - (position.quantity * position.average_entry_price)
                hold_minutes.append(position_hold_minutes(position, ts))
                total_fees_krw += fee_krw
                sell_count += 1
                trades.append(
                    simulated_trade(
                        ts=ts,
                        side="SELL",
                        market=market,
                        price=price,
                        quantity=position.quantity,
                        notional_krw=notional,
                        fee_krw=fee_krw,
                    )
                )
        elif action_regime == "NEUTRAL":
            pass
        else:
            skipped.append(f"{ts}: unsupported regime {regime}")

        equity_curve.append(
            {
                "ts": ts,
                "regime": regime,
                "equity": estimate_equity(cash, positions, prices),
                "cash": cash,
            }
        )

    latest_prices = latest_prices_for_positions(conn, positions, settings)
    final_equity = estimate_equity(cash, positions, latest_prices)
    unrealized_pnl_krw = unrealized_pnl(positions, latest_prices)
    first_ts = str(regimes[0]["ts"]) if regimes else None
    last_ts = str(regimes[-1]["ts"]) if regimes else None
    latest_equity_point = equity_curve[-1] if equity_curve else None
    if latest_equity_point is not None:
        latest_equity_point = dict(latest_equity_point)
        latest_equity_point["equity"] = final_equity
        latest_equity_point["cash"] = cash

    return {
        "parameters": settings.to_json(),
        "mode": settings.mode,
        "start_cash": START_CASH_KRW,
        "final_equity": final_equity,
        "return_pct": (final_equity - START_CASH_KRW) / START_CASH_KRW * 100,
        "realized_pnl_krw": realized_pnl_krw,
        "unrealized_pnl_krw": unrealized_pnl_krw,
        "total_fees_krw": total_fees_krw,
        "trade_count": buy_count + sell_count,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "duration_hours": duration_hours(first_ts, last_ts),
        "average_hold_minutes": average_hold_minutes(hold_minutes),
        "longest_hold_minutes": max(hold_minutes) if hold_minutes else None,
        "shortest_hold_minutes": min(hold_minutes) if hold_minutes else None,
        "regime_counts": regime_counts,
        "usable_feature_count": classification["usable_feature_count"],
        "skipped_feature_count": classification["skipped_feature_count"],
        "max_drawdown_pct": max_drawdown_pct(equity_curve),
        "positions": serialize_positions(positions, latest_prices),
        "latest_equity_point": latest_equity_point,
        "trades": trades[-20:],
        "skipped_count": len(skipped),
        "skipped": skipped,
    }


def update_regime_streaks(regime: str, risk_on_streak: int, risk_off_streak: int) -> tuple[int, int]:
    if regime == "RISK_ON":
        return risk_on_streak + 1, 0
    if regime == "RISK_OFF":
        return 0, risk_off_streak + 1
    return 0, 0


def action_regime_for_mode(settings: BacktestSettings, regime: str, risk_on_streak: int, risk_off_streak: int) -> str:
    if settings.mode == "simple":
        return regime
    if settings.mode != "persistent":
        raise ValueError(f"Unsupported backtest mode: {settings.mode}")
    if regime == "RISK_ON" and risk_on_streak >= settings.entry_streak:
        return "RISK_ON"
    if regime == "RISK_OFF" and risk_off_streak >= settings.exit_streak:
        return "RISK_OFF"
    return "NEUTRAL"


def position_hold_minutes(position: Position, exit_ts: str) -> float:
    return (parse_utc_datetime(exit_ts) - parse_utc_datetime(position.entry_ts)).total_seconds() / 60


def average_hold_minutes(hold_minutes: list[float]) -> float | None:
    if not hold_minutes:
        return None
    return sum(hold_minutes) / len(hold_minutes)


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


def unrealized_pnl(positions: dict[str, Position], latest_prices: dict[str, float]) -> float:
    pnl = 0.0
    for market, position in positions.items():
        latest_price = latest_prices.get(market)
        if latest_price is None:
            continue
        pnl += (latest_price - position.average_entry_price) * position.quantity
    return pnl


def duration_hours(first_ts: str | None, last_ts: str | None) -> float | None:
    if first_ts is None or last_ts is None:
        return None
    return (parse_utc_datetime(last_ts) - parse_utc_datetime(first_ts)).total_seconds() / 3600


def parse_utc_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_classified_regimes(conn: sqlite3.Connection, settings: BacktestSettings) -> dict[str, Any]:
    classifier = get_classifier(settings.regime_classifier)
    regimes = []
    skipped_feature_count = 0
    for feature_row in load_market_features(conn, settings.source):
        try:
            regime, reason = classifier(feature_row)
        except ClassifierNotApplicable:
            skipped_feature_count += 1
            continue
        regimes.append(
            {
                "id": feature_row["id"],
                "ts": feature_row["ts"],
                "regime": regime,
                "reason": reason,
            }
        )
    return {
        "regimes": regimes,
        "usable_feature_count": len(regimes),
        "skipped_feature_count": skipped_feature_count,
    }


def load_market_features(conn: sqlite3.Connection, source: str = DEFAULT_SOURCE) -> list[sqlite3.Row]:
    base_query = """
        SELECT
            id,
            ts,
            source,
            btc_return_1h,
            eth_return_1h,
            median_return_1h,
            btc_return_4h,
            eth_return_4h,
            median_return_4h,
            positive_ratio,
            average_spread_pct,
            average_imbalance_5,
            market_count
        FROM market_features
    """
    if source == "all":
        return conn.execute(base_query + " ORDER BY ts ASC, id ASC").fetchall()

    return conn.execute(base_query + " WHERE source = ? ORDER BY ts ASC, id ASC", (source,)).fetchall()


def prices_near_timestamp(
    conn: sqlite3.Connection,
    ts: str,
    markets: tuple[str, ...],
    settings: BacktestSettings,
) -> dict[str, Any]:
    prices = {}
    skipped = []
    for market in markets:
        candle = candle_price_near_timestamp(conn, market, ts, settings)
        if candle is not None:
            prices[market] = candle["price"]
            continue

        ticker = ticker_price_near_timestamp(conn, market, ts, settings)
        if ticker is not None:
            prices[market] = ticker["price"]
            continue

        nearest = nearest_price_distance(conn, market, ts)
        if nearest is not None and nearest > settings.max_price_distance_seconds:
            skipped.append(
                f"price too far from timestamp for {market}: distance_seconds={nearest} "
                f"max_distance_seconds={settings.max_price_distance_seconds}"
            )
    return {"prices": prices, "skipped": skipped}


def candle_price_near_timestamp(
    conn: sqlite3.Connection,
    market: str,
    ts: str,
    settings: BacktestSettings,
) -> dict[str, float] | None:
    row = conn.execute(
        """
        SELECT
            trade_price,
            ABS(strftime('%s', candle_date_time_utc) - strftime('%s', ?)) AS distance_seconds
        FROM candles
        WHERE market = ?
          AND interval = '1m'
          AND trade_price IS NOT NULL
        ORDER BY distance_seconds ASC
        LIMIT 1
        """,
        (ts, market),
    ).fetchone()
    return price_candidate(row, settings)


def ticker_price_near_timestamp(
    conn: sqlite3.Connection,
    market: str,
    ts: str,
    settings: BacktestSettings,
) -> dict[str, float] | None:
    row = conn.execute(
        """
        SELECT
            trade_price,
            ABS(strftime('%s', collected_at) - strftime('%s', ?)) AS distance_seconds
        FROM ticker_snapshots
        WHERE market = ?
          AND trade_price IS NOT NULL
        ORDER BY distance_seconds ASC
        LIMIT 1
        """,
        (ts, market),
    ).fetchone()
    return price_candidate(row, settings)


def price_candidate(row: sqlite3.Row | None, settings: BacktestSettings) -> dict[str, float] | None:
    if row is None:
        return None
    distance_seconds = int(row["distance_seconds"])
    if distance_seconds > settings.max_price_distance_seconds:
        return None
    return {"price": float(row["trade_price"]), "distance_seconds": float(distance_seconds)}


def nearest_price_distance(conn: sqlite3.Connection, market: str, ts: str) -> int | None:
    row = conn.execute(
        """
        SELECT MIN(distance_seconds) AS distance_seconds
        FROM (
            SELECT ABS(strftime('%s', candle_date_time_utc) - strftime('%s', ?)) AS distance_seconds
            FROM candles
            WHERE market = ? AND interval = '1m' AND trade_price IS NOT NULL
            UNION ALL
            SELECT ABS(strftime('%s', collected_at) - strftime('%s', ?)) AS distance_seconds
            FROM ticker_snapshots
            WHERE market = ? AND trade_price IS NOT NULL
        )
        """,
        (ts, market, ts, market),
    ).fetchone()
    if row is None or row["distance_seconds"] is None:
        return None
    return int(row["distance_seconds"])


def latest_prices_for_positions(
    conn: sqlite3.Connection,
    positions: dict[str, Position],
    settings: BacktestSettings,
) -> dict[str, float]:
    if not positions:
        return {}
    prices = {}
    now = datetime.now(timezone.utc).isoformat()
    for market in positions:
        candle = candle_price_near_timestamp(conn, market, now, settings)
        if candle is not None:
            prices[market] = candle["price"]
            continue
        ticker = ticker_price_near_timestamp(conn, market, now, settings)
        if ticker is not None:
            prices[market] = ticker["price"]
    return prices


def estimate_equity(cash: float, positions: dict[str, Position], prices: dict[str, float]) -> float:
    equity = cash
    for market, position in positions.items():
        price = prices.get(market)
        if price is not None:
            equity += position.quantity * price
    return equity


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


def serialize_positions(positions: dict[str, Position], latest_prices: dict[str, float]) -> dict[str, dict[str, float | None]]:
    output = {}
    for market, position in positions.items():
        latest_price = latest_prices.get(market)
        output[market] = {
            "quantity": position.quantity,
            "average_entry_price": position.average_entry_price,
            "latest_price": latest_price,
            "market_value": position.quantity * latest_price if latest_price is not None else None,
        }
    return output


if __name__ == "__main__":
    main()
